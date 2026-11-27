// Real capture/queue/replay/Destroy projection over the full self-owned world.
// The source callback wrapper and prefix are invoked separately by engine.cpp.
static JSValue production_terminal_run(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=2||!JS_IsObject(args[1]))throw std::runtime_error("owned Destroy setup arity");
 const auto options=J::parse(string(c,args[0]));auto& r=state();
 if(r.lanes->reserved.load())throw std::runtime_error("owned Destroy requires a fresh stream");
 auto hook=std::make_unique<Hook>();hook->capture_plan=compile_plan(options.at("plan"),0x9100);
 r.configurations.intern(hook->capture_plan);hook->callbacks=JS_DupValue(c,args[1]);hook->timing_id=r.hooks.size();
 const auto hook_id=hook->timing_id;r.hooks.push_back(std::move(hook));auto& installed=*r.hooks.back();
 auto* lane=r.lanes->current();if(!lane)throw std::runtime_error("owned Destroy lane unavailable");
 lane->depth=1;auto& call=lane->calls[0];call={};call.id=1;call.hook=&installed;call.configuration=r.configurations.pin(0);
 auto& context=call.context;context={};context.args[0]=plan_address(options.at("controller"));context.args_valid=1;
 context.entry_refs=context.args;context.entry_valid=1;context.globals=installed.capture_plan.globals;context.globals_valid=installed.capture_plan.globals_valid;
 const auto singleton=plan_address(options.at("singleton"));
 for(unsigned edge=0;edge<2;++edge){
  if(edge&&options.value("release",false)){std::uint64_t zero=0;auto* slot=region(singleton,8);if(!slot)throw std::runtime_error("owned singleton absent");std::memcpy(slot->data.data()+singleton-slot->address,&zero,8);}
  if(edge&&options.contains("stage_slot")){
   const auto at=plan_address(options.at("stage_slot")),value=plan_address(options.at("stage_value"));auto* slot=region(at,8);
   if(!slot){throw std::runtime_error("owned stage slot absent");}
   std::memcpy(slot->data.data()+at-slot->address,&value,8);
  }
  RecordHeader header{};header.ticket=r.lanes->reserved.fetch_add(1)+1;header.invocation=1;header.hook_id=hook_id;
  header.depth=1;header.phase=edge?Phase::leave:Phase::entry;header.args=context.args;header.arg_mask=1;
  header.thread_id=lane->owner.load();header.thread_birth=lane->birth.load();header.active=1;header.active_after=edge?0:1;
  header.activity_begin=edge?1:0;header.activity=edge?2:1;header.closed_after=edge!=0;header.return_address=0x1234;
  capture_scope_point(installed.capture_plan,header.phase,context,call.configuration,read_memory,nullptr,lane->snapshot,header,lane);
  if(!edge)save_entry_roots(installed.capture_plan,lane->snapshot,context);
  if(!r.lanes->publish(*lane,header))throw std::runtime_error("owned Destroy queue rejected");
 }
 lane->depth=0;r.activity_generation.store(2);
 while(r.processed_ticket<2&&r.fatal.empty())drain_captures();
 if(!r.fatal.empty())throw std::runtime_error(r.fatal);
 J report{{"raw",r.lanes->reserved.load()},{"processed",r.processed_ticket},{"closed",r.closed_ticket},
  {"published",r.published_ticket},{"failure",r.lanes->failure.load()},{"terminal_fence",r.terminal_fence.verified},{"token",r.published_token}};
 return JS_NewString(c,report.dump().c_str());
});}
// The frozen world uses fixed low addresses. Map those owned ranges in THIS
// process so the unchanged native scheduler's capture_read can recheck its
// real TerminalFence. Failure to reserve that range is a test failure.
static JSValue production_terminal_request(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=1||regions.empty())throw std::runtime_error("owned terminal request setup");
 static void* mapped=nullptr;
 if(!mapped){
  const auto first=regions.front().address&~std::uintptr_t(65535);
  const auto last=(regions.back().address+regions.back().data.size()+65535)&~std::uintptr_t(65535);
  if(first<65536||last<=first||last-first>64*1024*1024)throw std::runtime_error("owned terminal map bounds");
  mapped=VirtualAlloc(reinterpret_cast<void*>(first),last-first,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
  if(mapped!=reinterpret_cast<void*>(first))throw std::runtime_error("owned terminal fixed-address map unavailable");
 }
 for(const auto& part:regions)std::memcpy(reinterpret_cast<void*>(part.address),part.data.data(),part.data.size());
 auto& r=state();const auto requests=r.lanes->request_serial.load(),attempts=r.sampling_attempts;
 const auto reply=J::parse(arkdps::request_on_worker(string(c,args[0])));
 const J result{{"reply",reply},{"before_requests",requests},{"after_requests",r.lanes->request_serial.load()},
  {"before_attempts",attempts},{"after_attempts",r.sampling_attempts}};
 return JS_NewString(c,result.dump().c_str());
});}
