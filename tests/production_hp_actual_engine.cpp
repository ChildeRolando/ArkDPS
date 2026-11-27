// Actual engine consumer over the existing frozen 40-Chess production HP world.
// No detours, assembly entry, external process or IPC are used.
#include "../native_pc/native_event_runtime/engine.cpp"
#define main unused_legacy_hp_runner_main
#include "production_hp_quickjs.cpp"
#undef main
extern "C" void arkdps_hook_entry(){} extern "C" void arkdps_hook_leave(){}
extern "C" MH_STATUS WINAPI MH_Initialize(){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_Uninitialize(){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_CreateHook(LPVOID,LPVOID,LPVOID*){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_EnableHook(LPVOID){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_DisableHook(LPVOID){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_QueueEnableHook(LPVOID){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_QueueDisableHook(LPVOID){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_ApplyQueued(){return MH_OK;}
extern "C" const char* WINAPI MH_StatusToString(MH_STATUS){return "unused owned stub";}
static bool fixture_configured=false,legacy_boundary=false,legacy_hp_io=false;
static JSValue actual_configure(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=1)throw std::runtime_error("actual configure arity");
 const auto j=J::parse(string(c,args[0]));
 auto& r=state();
 auto hook=std::make_unique<Hook>();hook->capture_plan=compile_plan(j.at("plan"),0x9000);r.configurations.intern(hook->capture_plan);
 r.configurations.publish(j.at("scope"),false);r.configurations.publish(j.at("publication"),true);
 regions.clear();for(const auto& part:j.at("ranges")){Region value;value.address=plan_address(part.at("address"));for(const auto& b:part.at("bytes"))value.data.push_back(static_cast<std::byte>(b.get<unsigned>()));regions.push_back(std::move(value));}
 input={};input.args[0]=plan_address(j.at("enemy"));input.entry_refs[0]=input.args[0];input.args_valid=input.entry_valid=1;input.globals=hook->capture_plan.globals;input.globals_valid=hook->capture_plan.globals_valid;
 r.hooks.push_back(std::move(hook));fixture_configured=true;
 return JS_UNDEFINED;
});}
static JSValue actual_graph_publish(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=2||!JS_IsBool(args[0]))throw std::runtime_error("actual graph publish arity");
 if(fixture_configured)state().configurations.publish(json_value(c,args[1]),JS_ToBool(c,args[0])==1);
 return JS_UNDEFINED;
});}
static JSValue actual_callbacks(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=3||!fixture_configured||!JS_IsObject(args[0])||!JS_IsFunction(c,args[1])||!JS_IsFunction(c,args[2]))throw std::runtime_error("actual callback setup");
 auto& r=state();
 auto& h=*r.hooks.at(0);h.callbacks=JS_DupValue(c,args[0]);h.direct_entry=JS_GetPropertyStr(c,args[0],"arkdpsDirectHpEntry");h.direct_leave=JS_GetPropertyStr(c,args[0],"arkdpsDirectHpLeave");h.direct_contract=JS_GetPropertyStr(c,args[0],"arkdpsCaptureContract");
 if(!JS_IsFunction(c,h.direct_entry)||!JS_IsFunction(c,h.direct_leave))throw std::runtime_error("actual direct callbacks missing");
 auto g=JS_GetGlobalObject(c);r.replay_dispatch=JS_GetPropertyStr(c,g,"__arkdpsReplayDispatch");r.replay_batch=JS_GetPropertyStr(c,g,"__arkdpsReplayBatch");r.materialization_count=JS_GetPropertyStr(c,g,"__arkdpsReplayMaterializationCount");r.publication_publish=JS_GetPropertyStr(c,g,"__arkdpsPublishClosedRecord");JS_FreeValue(c,g);
 r.prefix_projection=JS_DupValue(c,args[1]);r.graph_retire=JS_DupValue(c,args[2]);r.initialized=true;
 return JS_UNDEFINED;
});}
#include "production_hp_paced_engine.h"
static JSValue actual_run(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=1||!fixture_configured)throw std::runtime_error("actual run setup");
 const auto options=J::parse(string(c,args[0]));
 if(options.contains("rate"))return actual_paced_run(c,options);
 const unsigned count=options.at("count").get<unsigned>(),pub_every=options.at("pub_every").get<unsigned>();
 const bool batch=options.at("batch").get<bool>();
 const auto branch=options.at("branch").get<std::string>();
 if(!count||count>2000||!pub_every)throw std::runtime_error("actual semantic run budget");
 auto& r=state();auto* lane=r.lanes->current();
 if(!lane)throw std::runtime_error("actual owned lane");
 const auto set=[&](std::uintptr_t at,auto value){auto* p=region(at,sizeof value);
 if(!p)throw std::runtime_error("actual owned write outside captured ranges");
 std::memcpy(p->data.data()+at-p->address,&value,sizeof value);};
 std::uint64_t reads=0,read_bytes=0,payload_bytes=0;std::array<std::size_t,2> payload_max{};
 const auto produce_start=std::chrono::steady_clock::now();
 for(unsigned i=0;i<count;++i){const auto frame=30u+i/1000;set(input.globals[0],frame);set(input.globals[0]+4,(std::uint64_t(frame)<<32)/30);lane->depth=1;lane->calls[0].id=2*i+1;
  for(unsigned edge=0;edge<2;++edge){const std::uint64_t hp=branch=="unchanged"?100000:branch=="changed_damage"?100000-i-edge:100000+i+edge;set(input.args[0]+0x40,hp<<32);
   RecordHeader h{};h.ticket=r.lanes->reserved.fetch_add(1)+1;h.invocation=2*i+1;h.phase=edge?Phase::leave:Phase::entry;h.depth=1;h.args[0]=input.args[0];h.arg_mask=1;h.thread_id=lane->owner.load();h.thread_birth=lane->birth.load();h.active=1;h.active_after=edge?0:1;h.closed_after=edge!=0;h.activity_begin=2*i+1;h.activity=edge?2*i+2:2*i+1;h.return_address=0x1234;
   if(edge&&((i+1)%pub_every==0||i+1==count))h.publication_request=h.ticket;
   capture_scope_point(r.hooks[0]->capture_plan,h.phase,input,r.configurations.pin(0),read_memory,nullptr,lane->snapshot,h,lane);
   if(lane->snapshot.error!=Error::ok)throw std::runtime_error("actual capture failed");
   reads+=lane->snapshot.read_count;read_bytes+=lane->snapshot.read_bytes;
   const auto bytes=sizeof h+lane->snapshot.segment_count*sizeof(Segment)+lane->snapshot.field_count*sizeof(FieldState)+lane->snapshot.byte_count;payload_bytes+=bytes;payload_max[edge]=(std::max)(payload_max[edge],bytes);
   if(!r.lanes->publish(*lane,h))throw std::runtime_error("actual queue publish failed");
  }lane->depth=0;
 }
 const auto produced=std::chrono::steady_clock::now();while(r.processed_ticket<2*count&&r.fatal.empty()){drain_captures_with_batch(batch);
 if(r.lanes->failure.load())break;}
 const auto ended=std::chrono::steady_clock::now();
 if(!r.fatal.empty())throw std::runtime_error(r.fatal);
 std::uint64_t hp=0;read_memory(nullptr,input.args[0]+0x40,&hp,8);std::uint64_t maxhp=0;auto* hpfield=region(input.args[0]+0xb0,8);
 if(hpfield){std::uintptr_t attrs=0,cache=0;read_memory(nullptr,input.args[0]+0xb0,&attrs,8);read_memory(nullptr,attrs+0x68,&cache,8);read_memory(nullptr,cache+0x28,&maxhp,8);}
 J result{{"legacy_boundary",legacy_boundary},{"legacy_hp_io",legacy_hp_io},{"native_hp_io_issued",arkdps::owned_js::binding().hp_opaque_issued},{"replay_lease_calls",arkdps::owned_js::binding().lease_calls},{"replay_status_calls",arkdps::owned_js::binding().status_calls},{"replay_health_calls",r.replay_health_calls},{"controlled_finish_checks",r.controlled_finish_checks},{"failure_code",r.lanes->failure.load()},{"processed_ticket",r.processed_ticket},{"closed_ticket",r.closed_ticket},{"published_ticket",r.published_ticket},{"published_sequence",r.published_sequence},{"raw_entry_count",count},{"raw_leave_count",count},{"batch_calls",r.batch_calls},{"batch_issued",r.batch_issued},{"batch_finalized",r.batch_finalized},{"batch_barriers",r.batch_barriers},{"single_replayed",r.single_replayed},{"maximum_batch_edges",r.maximum_batch_edges},{"capture_reads",reads},{"capture_read_bytes",read_bytes},{"payload_bytes",payload_bytes},{"payload_max",payload_max},{"last_hp_raw",std::to_string(hp)},{"max_hp_raw",std::to_string(maxhp)},{"produce_ms",std::chrono::duration<double,std::milli>(produced-produce_start).count()},{"consume_ms",std::chrono::duration<double,std::milli>(ended-produced).count()}};
 return JS_NewString(c,result.dump().c_str());
});}
int main(int argc,char** argv){
 (void)sealed_owner;
 if(argc<2||argc>4)return 2;
 for(int i=2;i<argc;++i){if(std::string(argv[i])=="--legacy-boundary")legacy_boundary=true;else if(std::string(argv[i])=="--legacy-hp-io")legacy_hp_io=true;else return 2;}
 auto& r=state();r.runtime=JS_NewRuntime();r.context=JS_NewContext(r.runtime);auto* c=r.context;JS_SetMemoryLimit(r.runtime,512*1024*1024);
 CaptureLanes::Config capacity;capacity.lane_queue={2048,2*1024*1024};capacity.spill={32768,32*1024*1024};r.lanes=std::make_unique<CaptureLanes>(capacity);
 arkdps::owned_js::install(c,[]()noexcept{state().lanes->poison(LaneFault::replay_structure);},true,!legacy_hp_io);auto g=JS_GetGlobalObject(c);
 for(auto pair:std::array<std::pair<const char*,JSCFunction*>,4>{{{"print",print},{"__monotonicMs",timer},{"__ownedPut",put},{"__sealReadonlyPlans",seal_plans}}})function(c,g,pair.first,pair.second,1);
 function(c,g,"__ownedConfigure",actual_configure,1);function(c,g,"__ownedGraphPublish",actual_graph_publish,2);function(c,g,"__ownedSetCallbacks",actual_callbacks,3);function(c,g,"__ownedEngineRun",actual_run,1);function(c,g,"__nativeReplayHealthy",js_replay_healthy,0);function(c,g,"__nativeActive",js_active,0);function(c,g,"__pid",js_pid,0);install_controlled_advance(c,g);r.batch_advance=JS_GetPropertyStr(c,g,"__nativeBoundedReplayAdvance");if(legacy_boundary)JS_SetPropertyStr(c,g,"__nativeBoundedReplayAdvance",JS_UNDEFINED);JS_FreeValue(c,g);
 std::ifstream stream(argv[1]);std::string source((std::istreambuf_iterator<char>(stream)),{});auto answer=JS_Eval(c,source.data(),source.size(),"actual-engine-production-hp.js",JS_EVAL_TYPE_GLOBAL);
 if(JS_IsException(answer)){std::cerr<<exception(c)<<'\n';
 return 1;}JS_FreeValue(c,answer);
 // Runtime is intentionally process-lifetime in engine.cpp; this standalone
 // exits after one arm, matching that lifetime without reclaiming live detours.
 return 0;
}
