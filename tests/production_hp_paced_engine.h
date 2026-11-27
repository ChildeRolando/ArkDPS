// Self-owned producer only. No hooks, process memory APIs, detours or IPC.
// Reuses the production pin/capture/queue/drain path and shared JS callbacks.
static JSValue actual_paced_run(JSContext* c, const J& options) {
 const unsigned count=options.at("count").get<unsigned>(),rate=options.at("rate").get<unsigned>();
 const unsigned pause_at=options.value("pause_at_ms",1000u),pause_ms=options.value("pause_ms",0u);
 const unsigned pub_every=options.at("pub_every").get<unsigned>();
 if(!count||count>300000||!rate||rate>60000||!pub_every||pause_ms>1000||options.at("branch")!="heal_coalesced")
  throw std::runtime_error("paced owned fixture budget/branch");
 auto& r=state();
 std::atomic<bool> done{false},release_thread{false},abort{false};
 std::atomic<Lane*> producer_lane{nullptr};
 std::uint64_t reads=0,read_bytes=0,bytes=0,produced_setters=0,produced_edges=0,max_lateness_ns=0;
 std::string producer_error;double produce_ms=0;std::uint64_t pin_mismatches=0;
 const auto start=std::chrono::steady_clock::now();
 std::thread producer([&]{
  auto* lane=r.lanes->current();producer_lane.store(lane,std::memory_order_release);
  if(!lane){producer_error="lane unavailable";done.store(true,std::memory_order_release);return;}
  const auto set=[&](std::uintptr_t at,auto value){auto* target=region(at,sizeof value);if(!target)throw std::runtime_error("write outside owned ranges");std::memcpy(target->data.data()+at-target->address,&value,sizeof value);};
  bool entered=false;
  try{
   for(unsigned i=0;i<count&&!abort.load(std::memory_order_relaxed)&&!r.lanes->failure.load(std::memory_order_acquire);++i){
    const auto due=start+std::chrono::nanoseconds(std::uint64_t(i)*1000000000/rate);
    std::this_thread::sleep_until(due);
    const auto now=std::chrono::steady_clock::now();
    if(now>due)max_lateness_ns=(std::max)(max_lateness_ns,static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(now-due).count()));
    const auto mode=pin_mode_entry(r.active,r.activity_generation,r.capture_mode_epoch);entered=true;
    const auto* config=r.configurations.pin(mode);
    auto& invocation=lane->calls[0];invocation={};invocation.configuration=config;invocation.mode_epoch=mode;
    invocation.id=r.lanes->reserved.fetch_add(1,std::memory_order_acq_rel)+1;
    lane->depth=1;
    const auto frame=30u+i/1000;set(input.globals[0],frame);set(input.globals[0]+4,(std::uint64_t(frame)<<32)/30);
    for(unsigned edge=0;edge<2;++edge){
     set(input.args[0]+0x40,std::uint64_t(100000+i+edge)<<32);
     RecordHeader h{};h.ticket=edge?r.lanes->reserved.fetch_add(1,std::memory_order_acq_rel)+1:invocation.id;
     h.invocation=invocation.id;h.mode_epoch=mode;h.scope_mode_epoch=r.capture_mode_epoch.load(std::memory_order_acquire);
     h.phase=edge?Phase::leave:Phase::entry;h.depth=1;h.args[0]=input.args[0];h.arg_mask=1;
     h.thread_id=lane->owner.load();h.thread_birth=lane->birth.load();h.return_address=0x1234;
     for(unsigned j=0;j<lane_count;++j)if(r.lanes->lanes[j].get()==lane)h.lane=j;
     h.activity_begin=r.activity_generation.load(std::memory_order_acquire);h.activity=h.activity_begin;h.active=r.active.load(std::memory_order_acquire);
     if(edge&&((i+1)%pub_every==0||i+1==count))h.publication_request=h.ticket;
     h.captured_qpc=ticks_now();
     if(invocation.configuration!=config||invocation.mode_epoch!=mode)++pin_mismatches;
     capture_scope_point(r.hooks[0]->capture_plan,h.phase,input,invocation.configuration,read_memory,nullptr,lane->snapshot,h,lane);
     if(lane->snapshot.error!=Error::ok)throw std::runtime_error("capture failed");
     reads+=lane->snapshot.read_count;read_bytes+=lane->snapshot.read_bytes;
     bytes+=sizeof h+lane->snapshot.segment_count*sizeof(Segment)+lane->snapshot.field_count*sizeof(FieldState)+lane->snapshot.byte_count;
     if(edge){h.activity=r.activity_generation.fetch_add(1,std::memory_order_acq_rel)+1;h.active_after=r.active.fetch_sub(1,std::memory_order_acq_rel)-1;entered=false;h.closed_after=h.active_after==0&&h.activity==h.activity_begin+1;h.capture_concurrent=leave_capture_concurrent(h.active,h.depth,h.activity_begin,h.activity,h.active_after);}
     else{h.activity=r.activity_generation.load(std::memory_order_acquire);h.capture_concurrent=entry_capture_concurrent(h.active,h.depth,h.activity_begin,h.activity,r.active.load(std::memory_order_acquire));}
     if(!r.lanes->publish(*lane,h))throw std::runtime_error("queue publish failed");
     ++produced_edges;
    }
    lane->depth=0;++produced_setters;
   }
  }catch(const std::exception& error){producer_error=error.what();}
  if(entered){r.activity_generation.fetch_add(1,std::memory_order_acq_rel);r.active.fetch_sub(1,std::memory_order_acq_rel);}
  lane->depth=0;produce_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
  done.store(true,std::memory_order_release);
  // Fixture lifetime barrier after production ends, never on the hot path.
  // Keeps normal terminated-thread reclamation from resetting evidence before
  // the worker takes its final metrics snapshot.
  while(!release_thread.load(std::memory_order_acquire))std::this_thread::sleep_for(std::chrono::milliseconds(1));
 });
 J samples=J::array();double next_sample=0;bool saw_pause=false;std::uint64_t peak_lag=0;
 std::uint64_t last_published=0,publication_changes=0;double catchup_ms=-1;
 try{
  while(!done.load(std::memory_order_acquire)||r.processed_ticket<r.lanes->reserved.load(std::memory_order_acquire)){
   const double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
   if(ms>60000){abort.store(true);break;}
   const bool paused=pause_ms&&ms>=pause_at&&ms<pause_at+pause_ms;
   if(paused){saw_pause=true;std::this_thread::sleep_for(std::chrono::milliseconds(1));}
   else drain_captures_with_batch(true);
   const auto reserved=r.lanes->reserved.load(std::memory_order_acquire);
   peak_lag=(std::max)(peak_lag,reserved-r.processed_ticket);
   if(r.published_ticket!=last_published){last_published=r.published_ticket;++publication_changes;}
   if(saw_pause&&ms>=pause_at+pause_ms&&catchup_ms<0&&r.processed_ticket==reserved)catchup_ms=ms-pause_at-pause_ms;
   if(ms>=next_sample){samples.push_back({{"ms",ms},{"reserved",reserved},{"processed",r.processed_ticket},{"closed",r.closed_ticket},{"published",r.published_ticket}});next_sample=ms+50;}
   if(r.lanes->failure.load(std::memory_order_acquire))break;
   if(!paused&&r.processed_ticket==reserved)std::this_thread::yield();
  }
 }catch(...){abort.store(true);release_thread.store(true);producer.join();throw;}
 abort.store(true);while(!done.load(std::memory_order_acquire))std::this_thread::yield();
 const auto* lane=producer_lane.load(std::memory_order_acquire);
 const auto peak_records=lane?lane->submitted.peak_records.load():0,peak_bytes=lane?lane->submitted.peak_bytes.load():0;
 const auto max_age=lane?lane->consumed.maximum_age:0;
 release_thread.store(true,std::memory_order_release);producer.join();
 std::uint64_t hp=0;read_memory(nullptr,input.args[0]+0x40,&hp,8);
 LARGE_INTEGER frequency{};QueryPerformanceFrequency(&frequency);
 J result{{"paced",true},{"requested_setters",count},{"requested_rate",rate},{"produced_setters",produced_setters},{"raw_produced_edges",produced_edges},{"produce_ms",produce_ms},{"elapsed_ms",std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count()},{"max_pacing_lateness_ns",max_lateness_ns},{"pause_ms",pause_ms},{"pause_observed",saw_pause},{"catchup_ms",catchup_ms},{"pin_mismatches",pin_mismatches},{"active_after",r.active.load()},{"failure_code",r.lanes->failure.load()},{"fatal",r.fatal},{"producer_error",producer_error},{"raw_reserved",r.lanes->reserved.load()},{"processed_ticket",r.processed_ticket},{"closed_ticket",r.closed_ticket},{"published_ticket",r.published_ticket},{"publication_changes",publication_changes},{"published_sequence",r.published_sequence},{"peak_lag_edges",peak_lag},{"peak_records",peak_records},{"peak_bytes",peak_bytes},{"maximum_raw_age_ticks",max_age},{"qpc_frequency",frequency.QuadPart},{"capture_reads",reads},{"capture_read_bytes",read_bytes},{"payload_bytes",bytes},{"last_hp_raw",std::to_string(hp)},{"native_hp_io_issued",arkdps::owned_js::binding().hp_opaque_issued},{"batch_calls",r.batch_calls},{"batch_issued",r.batch_issued},{"batch_finalized",r.batch_finalized},{"single_replayed",r.single_replayed},{"samples",samples}};
 return JS_NewString(c,result.dump().c_str());
}
