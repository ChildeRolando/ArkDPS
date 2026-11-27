// Includes a generated, hash-bound copy of the existing owned actual-engine
// harness with its main renamed. Production engine/native_capture are unmodified.
#include "capture-demand-engine-base.inc"
struct DemandWrite{std::uintptr_t at;std::uint64_t value;unsigned size;};
struct DemandStep{unsigned hook;bool leave;std::array<std::uint64_t,16> args{};std::uint64_t result=0;std::vector<DemandWrite> writes;};
struct DemandTiming{std::uint64_t samples=0,callback=0,capture=0,rpm=0,enqueuing=0,read_calls=0;};
struct DemandReadTiming{bool sampled=false;std::uint64_t ticks=0,reads=0;};
static bool demand_timed_read(void* user,std::uintptr_t at,void* out,std::size_t bytes)noexcept{
 auto& p=*static_cast<DemandReadTiming*>(user);if(!p.sampled)return capture_read(nullptr,at,out,bytes);
 const auto begin=ticks_now();const bool ok=capture_read(nullptr,at,out,bytes);p.ticks+=ticks_now()-begin;++p.reads;return ok;
}
static std::vector<std::vector<DemandStep>> demand_routes;
static std::string demand_input_path;
static JSValue demand_configure(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=4||!fixture_configured)throw std::runtime_error("damage setup arity");auto j=J::parse(string(c,args[0]));auto& r=state();
 if(!demand_input_path.empty())std::ofstream(demand_input_path)<<J{{"routes",j.at("routes")},{"ranges",j.at("ranges")}}.dump();
 regions.clear();for(const auto& v:j.at("ranges")){Region part;part.address=plan_address(v.at("address"));for(const auto& b:v.at("bytes"))part.data.push_back(std::byte(b.get<unsigned>()));regions.push_back(std::move(part));}
 const auto first=regions.front().address&~std::uintptr_t(65535),last=(regions.back().address+regions.back().data.size()+65535)&~std::uintptr_t(65535);
 if(first<65536||last<=first||last-first>64*1024*1024)throw std::runtime_error("damage owned mapping bounds");
 if(VirtualAlloc(reinterpret_cast<void*>(first),last-first,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE)!=reinterpret_cast<void*>(first))throw std::runtime_error("damage fixed owned mapping unavailable");
 for(const auto& part:regions)std::memcpy(reinterpret_cast<void*>(part.address),part.data.data(),part.data.size());
 for(unsigned i=0;i<j.at("plans").size();++i){const auto& p=j.at("plans")[i];auto h=std::make_unique<Hook>();h->capture_plan=compile_plan(p,plan_address(p.at("address")));r.configurations.intern(h->capture_plan);h->callbacks=JS_GetPropertyUint32(c,args[1],i);h->timing_name=p.at("name");h->timing_id=static_cast<unsigned>(r.hooks.size());r.hooks.push_back(std::move(h));}
 for(const auto& route:j.at("routes")){std::vector<DemandStep> steps;for(const auto& v:route){DemandStep s{};s.hook=v.at("hook").get<unsigned>()+1;s.leave=v.at("edge")=="leave";if(v.contains("args"))for(unsigned i=0;i<v.at("args").size();++i)s.args[i]=plan_address(v.at("args")[i]);if(v.contains("return_value"))s.result=plan_address(v.at("return_value"));for(const auto& w:v.value("writes",J::array()))s.writes.push_back({plan_address(w.at("address")),plan_address(w.at("value")),w.at("bytes").get<unsigned>()});steps.push_back(s);}demand_routes.push_back(std::move(steps));}
 auto g=JS_GetGlobalObject(c);r.replay_dispatch=JS_GetPropertyStr(c,g,"__arkdpsReplayDispatch");r.replay_batch=JS_GetPropertyStr(c,g,"__arkdpsReplayBatch");r.materialization_count=JS_GetPropertyStr(c,g,"__arkdpsReplayMaterializationCount");r.publication_publish=JS_GetPropertyStr(c,g,"__arkdpsPublishClosedRecord");JS_FreeValue(c,g);r.prefix_projection=JS_DupValue(c,args[2]);r.graph_retire=JS_DupValue(c,args[3]);r.initialized=true;
 return JS_UNDEFINED;
});}
static JSValue demand_run(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=1)throw std::runtime_error("damage run arity");const auto options=J::parse(string(c,args[0]));auto& r=state();
 const unsigned count=options.at("count").get<unsigned>(),rate=options.value("rate",0u),pause_at=options.value("pause_at_ms",1000u),pause_ms=options.value("pause_ms",0u),pub_every=options.value("pub_every",100u);
 const bool profile=options.value("profile",false);std::vector<std::array<DemandTiming,2>> timings(r.hooks.size());std::uint32_t random=0x9e3779b9;std::uint64_t drain_ticks=0,drain_calls=0,drain_busy_ticks=0,drain_idle_ticks=0;
 if(!count||count>1000000||rate>30000||pause_ms>1000||!pub_every)throw std::runtime_error("damage workload bounds");
 std::atomic<bool> done{false},release{false},abort{false};std::atomic<Lane*> used{nullptr};std::string producer_error;
 std::uint64_t reads=0,read_bytes=0,abi_reads=0,packed_bytes=0,cycles=0,edges=0,peak_lag=0;double produce_ms=0,catchup=-1;
 const auto first=r.lanes->reserved.load();const auto started=std::chrono::steady_clock::now();
 auto produce=[&]{auto* lane=r.lanes->current();used.store(lane);try{if(!lane)throw std::runtime_error("damage lane missing");
 for(unsigned cycle=0;cycle<count&&!abort.load()&&!r.lanes->failure.load();++cycle){
  if(rate)std::this_thread::sleep_until(started+std::chrono::nanoseconds(std::uint64_t(cycle)*1000000000/rate));
  const auto route_count=options.value("route_count",5u);if(!route_count||route_count>demand_routes.size())throw std::runtime_error("damage route count");
  const auto& route=demand_routes.at(options.value("route",-1)<0?cycle%route_count:options.at("route").get<unsigned>());
  for(unsigned index=0;index<route.size();++index){const auto& step=route[index];auto& hook=*r.hooks.at(step.hook);
   bool sampled=false;if(profile){random^=random<<13;random^=random>>17;random^=random<<5;sampled=(random&127u)==0;}const auto callback_begin=sampled?ticks_now():0;
   for(const auto& w:step.writes){if(!region(w.at,w.size)||!w.size||w.size>8)throw std::runtime_error("damage write outside owned range");std::memcpy(reinterpret_cast<void*>(w.at),&w.value,w.size);}
   InvocationState* call=nullptr;
   if(!step.leave){const auto mode=pin_mode_entry(r.active,r.activity_generation,r.capture_mode_epoch);if(lane->depth>=invocation_depth)throw std::runtime_error("damage nesting budget");call=&lane->calls[lane->depth];*call={};call->hook=&hook;call->mode_epoch=mode;call->configuration=r.configurations.pin(mode);call->apply_modifier=hook.capture_plan.apply_modifier;call->buff_damage=hook.capture_plan.buff_damage;call->id=r.lanes->reserved.fetch_add(1)+1;call->parent=lane->depth?lane->calls[lane->depth-1].id:0;call->context.args=step.args;call->context.args_valid=hook.capture_plan.plan->arg_mask;call->context.entry_refs=step.args;call->context.entry_valid=call->context.args_valid;call->context.globals=hook.capture_plan.globals;call->context.globals_valid=hook.capture_plan.globals_valid;
    const std::uintptr_t own_return=0x1234;if(!capture_read(nullptr,reinterpret_cast<std::uintptr_t>(&own_return),&call->return_address,8))throw std::runtime_error("owned ABI return read");++abi_reads;
    for(unsigned arg=4;arg<hook.capture_plan.plan->arg_count;++arg)if(hook.capture_plan.plan->arg_mask&(1u<<arg)){if(!capture_read(nullptr,reinterpret_cast<std::uintptr_t>(&step.args[arg]),&call->context.args[arg],8))throw std::runtime_error("owned ABI argument read");++abi_reads;}
    call->context.entry_refs=call->context.args;
    for(unsigned p=lane->depth;p>0;--p)if(lane->calls[p-1].buff_damage){call->context.parent_refs[0]=lane->calls[p-1].context.args[0];call->context.parent_valid|=1;break;}++lane->depth;
   }else{if(!lane->depth)throw std::runtime_error("damage leave without entry");call=&lane->calls[lane->depth-1];if(call->hook!=&hook)throw std::runtime_error("damage hook topology");call->context.return_value=step.result;call->context.return_valid=true;}
   RecordHeader h{};h.ticket=step.leave?r.lanes->reserved.fetch_add(1)+1:call->id;h.invocation=call->id;h.parent=call->parent;h.mode_epoch=call->mode_epoch;h.scope_mode_epoch=r.capture_mode_epoch.load();h.phase=step.leave?Phase::leave:Phase::entry;h.args=call->context.args;h.arg_mask=call->context.args_valid;h.return_value=step.result;h.return_address=0x1234;h.hook_id=step.hook;h.depth=lane->depth;h.thread_id=lane->owner.load();h.thread_birth=lane->birth.load();for(unsigned i=0;i<lane_count;++i)if(r.lanes->lanes[i].get()==lane)h.lane=i;
   h.active=r.active.load();h.activity_begin=r.activity_generation.load();h.activity=h.activity_begin;h.captured_qpc=profile?ticks_now():0;
   if(step.leave&&lane->depth==1&&index+1==route.size()&&((cycle+1)%pub_every==0||cycle+1==count))h.publication_request=h.ticket;
   DemandReadTiming read_profile{sampled,0,0};const auto capture_begin=sampled?ticks_now():0;
   capture_scope_point(hook.capture_plan,h.phase,call->context,call->configuration,profile?demand_timed_read:capture_read,profile?&read_profile:nullptr,lane->snapshot,h,lane);
   const auto capture_end=sampled?ticks_now():0;
   if(lane->snapshot.error!=Error::ok)throw std::runtime_error("damage capture error "+std::to_string(unsigned(lane->snapshot.error)));
   if(!step.leave)save_entry_roots(hook.capture_plan,lane->snapshot,call->context);
   reads+=lane->snapshot.read_count;read_bytes+=lane->snapshot.read_bytes;packed_bytes+=sizeof(h)+h.parent_checkpoint_count*sizeof(ParentCheckpoint)+lane->snapshot.segment_count*sizeof(Segment)+lane->snapshot.field_count*sizeof(FieldState)+lane->snapshot.byte_count;
   if(step.leave){h.activity=r.activity_generation.fetch_add(1)+1;h.active_after=r.active.fetch_sub(1)-1;h.closed_after=!h.active_after&&h.activity==h.activity_begin+1;h.capture_concurrent=leave_capture_concurrent(h.active,h.depth,h.activity_begin,h.activity,h.active_after);}else h.capture_concurrent=entry_capture_concurrent(h.active,h.depth,h.activity_begin,r.activity_generation.load(),r.active.load());
   const auto queue_begin=sampled?ticks_now():0;
   if(!r.lanes->publish(*lane,h))throw std::runtime_error("damage queue_full");++edges;if(step.leave)--lane->depth;
   if(sampled){const auto end=ticks_now();auto& t=timings.at(step.hook)[step.leave?1:0];++t.samples;t.callback+=end-callback_begin;t.capture+=capture_end-capture_begin;t.rpm+=read_profile.ticks;t.read_calls+=read_profile.reads;t.enqueuing+=end-queue_begin;}
  }++cycles;
 }
 }catch(const std::exception& e){producer_error=e.what();}produce_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();done.store(true);while(!release.load())std::this_thread::yield();};
 std::thread producer(produce);J samples=J::array();double next=0;bool paused_once=false;
 try{while(!done.load()||r.processed_ticket<r.lanes->reserved.load()){
  const auto ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();if(ms>90000){abort.store(true);break;}
  const bool paused=pause_ms&&ms>=pause_at&&ms<pause_at+pause_ms;if(paused){paused_once=true;std::this_thread::sleep_for(std::chrono::milliseconds(1));}else{const auto begin=profile?ticks_now():0,processed_before=r.processed_ticket;drain_captures();if(profile){const auto elapsed=ticks_now()-begin;drain_ticks+=elapsed;++drain_calls;if(r.processed_ticket>processed_before)drain_busy_ticks+=elapsed;else drain_idle_ticks+=elapsed;}}
  const auto raw=r.lanes->reserved.load();peak_lag=(std::max)(peak_lag,raw-r.processed_ticket);const auto after_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();if(paused_once&&after_ms>=pause_at+pause_ms&&catchup<0&&raw==r.processed_ticket)catchup=after_ms-pause_at-pause_ms;
  if(ms>=next){samples.push_back({{"ms",ms},{"raw",raw},{"processed",r.processed_ticket},{"closed",r.closed_ticket},{"published",r.published_ticket}});next=ms+50;}
  if(r.lanes->failure.load()||!r.fatal.empty()){abort.store(true);break;}if(!paused&&raw==r.processed_ticket)std::this_thread::yield();
 }}catch(...){abort.store(true);release.store(true);producer.join();throw;}
 abort.store(true);while(!done.load())std::this_thread::yield();const auto* lane=used.load();const auto peak_bytes=lane?lane->submitted.peak_bytes.load():0,peak_records=lane?lane->submitted.peak_records.load():0;release.store(true);producer.join();
 J report{{"cycles",cycles},{"edges",edges},{"first_ticket",first},{"raw",r.lanes->reserved.load()},{"processed",r.processed_ticket},{"closed",r.closed_ticket},{"published",r.published_ticket},{"sequence",r.published_sequence},{"failure",r.lanes->failure.load()},{"fatal",r.fatal},{"producer_error",producer_error},{"capture_reads",reads},{"capture_read_bytes",read_bytes},{"abi_reads",abi_reads},{"packed_bytes",packed_bytes},{"planned_hp_edges",r.planned_hp_edges},{"produce_ms",produce_ms},{"elapsed_ms",std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count()},{"peak_lag_edges",peak_lag},{"peak_records",peak_records},{"peak_bytes",peak_bytes},{"pause_observed",paused_once},{"catchup_ms",catchup},{"samples",samples}};
 if(profile){LARGE_INTEGER frequency;QueryPerformanceFrequency(&frequency);J rows=J::array();for(unsigned hook=0;hook<timings.size();++hook)for(unsigned edge=0;edge<2;++edge){const auto& t=timings[hook][edge];if(t.samples)rows.push_back({{"hook",r.hooks[hook]->timing_name},{"hook_id",hook},{"edge",edge?"leave":"enter"},{"samples",t.samples},{"callback_ticks",t.callback},{"capture_ticks",t.capture},{"rpm_ticks",t.rpm},{"queue_ticks",t.enqueuing},{"sampled_native_reads",t.read_calls}});}report["profile"]={{"qpc_frequency",frequency.QuadPart},{"sample_rate","xorshift 1/128 per boundary"},{"producer",rows},{"consumer_drain_ticks",drain_ticks},{"consumer_drain_busy_ticks",drain_busy_ticks},{"consumer_empty_poll_ticks",drain_idle_ticks},{"consumer_drain_calls",drain_calls}};}
 return JS_NewString(c,report.dump().c_str());
});}
int main(int argc,char** argv){if(argc<2||argc>3)return 2;if(argc==3)demand_input_path=argv[2];auto& r=state();r.runtime=JS_NewRuntime();r.context=JS_NewContext(r.runtime);auto* c=r.context;JS_SetMemoryLimit(r.runtime,512*1024*1024);
 CaptureLanes::Config capacity;capacity.lane_queue={8192,8*1024*1024};capacity.spill={32768,32*1024*1024};r.lanes=std::make_unique<CaptureLanes>(capacity);arkdps::owned_js::install(c,[]()noexcept{state().lanes->poison(LaneFault::replay_structure);},true,true);auto g=JS_GetGlobalObject(c);
 for(auto pair:std::array<std::pair<const char*,JSCFunction*>,4>{{{"print",print},{"__monotonicMs",timer},{"__ownedPut",put},{"__sealReadonlyPlans",seal_plans}}})function(c,g,pair.first,pair.second,1);
 function(c,g,"__ownedConfigure",actual_configure,1);function(c,g,"__ownedGraphPublish",actual_graph_publish,2);function(c,g,"__ownedDemandConfigure",demand_configure,4);function(c,g,"__ownedDemandRun",demand_run,1);function(c,g,"__nativeReplayHealthy",js_replay_healthy,0);function(c,g,"__nativeActive",js_active,0);function(c,g,"__pid",js_pid,0);install_controlled_advance(c,g);r.batch_advance=JS_GetPropertyStr(c,g,"__nativeBoundedReplayAdvance");JS_FreeValue(c,g);
 std::ifstream stream(argv[1]);std::string source((std::istreambuf_iterator<char>(stream)),{});auto result=JS_Eval(c,source.data(),source.size(),"actual-mixed-damage.js",JS_EVAL_TYPE_GLOBAL);if(JS_IsException(result)){std::cerr<<exception(c)<<'\n';return 1;}JS_FreeValue(c,result);return 0;}
