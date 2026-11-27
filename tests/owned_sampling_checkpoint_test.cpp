// Actual engine checkpoint/owned-view/journal over memory owned by this process.
#include "../native_pc/native_event_runtime/engine.cpp"
#include <cassert>
#include <fstream>
#include <iostream>
extern "C" void arkdps_hook_entry(){} extern "C" void arkdps_hook_leave(){}
extern "C" MH_STATUS WINAPI MH_Initialize(){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_Uninitialize(){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_CreateHook(LPVOID,LPVOID,LPVOID*){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_EnableHook(LPVOID){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_DisableHook(LPVOID){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_QueueEnableHook(LPVOID){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_QueueDisableHook(LPVOID){return MH_OK;}
extern "C" MH_STATUS WINAPI MH_ApplyQueued(){return MH_OK;}
extern "C" const char* WINAPI MH_StatusToString(MH_STATUS){return "owned test stub";}

using namespace arkdps::capture;using arkdps::sample_checkpoint;using Json=nlohmann::json;
static void script(JSContext* c,const std::string& text){auto value=JS_Eval(c,text.data(),text.size(),"owned-checkpoint.js",JS_EVAL_TYPE_GLOBAL);if(JS_IsException(value))throw std::runtime_error(exception(c));JS_FreeValue(c,value);}
static std::string file(const char* name){std::ifstream input(name);return std::string((std::istreambuf_iterator<char>(input)),{});}
static void set_frame(std::array<std::byte,12>& clock,std::uint32_t frame){std::memcpy(clock.data(),&frame,4);const std::int64_t fixed=std::int64_t(frame)<<32;std::memcpy(clock.data()+4,&fixed,8);}
struct ChangingRead{std::array<std::byte,12>* clock;unsigned reads=0;};
static bool change_clock(void* user,std::uintptr_t at,void* target,std::size_t bytes)noexcept{
 auto& change=*static_cast<ChangingRead*>(user);if(++change.reads==2)set_frame(*change.clock,41);return capture_read(nullptr,at,target,bytes);
}
int main(int argc,char** argv){
 auto& r=state();r.runtime=JS_NewRuntime();r.context=JS_NewContext(r.runtime);auto* c=r.context;
 CaptureLanes::Config limits;limits.lane_queue={16,65536};limits.spill={16,65536};r.lanes=std::make_unique<CaptureLanes>(limits);
 arkdps::owned_js::install(c,[]()noexcept{state().lanes->poison(LaneFault::replay_structure);});
 auto global=JS_GetGlobalObject(c);function(c,global,"__pid",js_pid,0);function(c,global,"__nativeActive",js_active,0);function(c,global,"__nativeReplayHealthy",js_replay_healthy,0);function(c,global,"__nativeSamplingCheckpoint",js_sampling_checkpoint,1);
 script(c,file("native_pc/native_event_runtime/bridge.js"));script(c,file("event_probe_store.js"));script(c,file("native_pc/native_event_runtime/publication.js"));
 script(c,"const ownedEventStore=new ArkdpsEventStore();__arkdpsPublicationJournal.bindStore(ownedEventStore.view());");
 std::array<std::byte,12> clock{};set_frame(clock,30);const auto address=std::to_string(reinterpret_cast<std::uintptr_t>(clock.data()));
 Json fields=Json::array();for(unsigned i=0;i<2;++i)fields.push_back({{"field_id",i+1},{"phase","checkpoint"},{"role",i?"clock_close":"clock_open"},{"root","global_slot"},{"root_index",0},{"pointer_offsets",Json::array()},{"offset",0},{"bytes",12},{"kind","bytes"},{"guard_id",1},{"guard_side",i?"after":"before"},{"required",true}});
 Json plan{{"schema_version",1},{"complete",true},{"address","0x0"},{"profile","owned_checkpoint_test"},{"abi","win64-gp"},{"arg_count",0},{"arg_mask",0},{"max_bytes",1024},{"max_reads",16},{"globals",Json::array({address})},{"fields",fields},{"required_projections",Json::array()}};
 auto compiled=compile_plan(plan,0);r.configurations.intern(compiled);r.sampling_plan=std::make_unique<CompiledPlan>(std::move(compiled));
 script(c,"const clockAddress=ptr(BigInt('"+address+"'));let seen=[],lastRecord,terminalTest=false;function checkpointProjection(record){lastRecord=record;if(!Object.isFrozen(record)||!Object.isFrozen(record.facts))throw Error('mutable record');if(!__arkdpsVerifiedSamplingCheckpoint(record))throw Error('missing native checkpoint proof');const frame=__arkdpsReplayCheckpoint('clock_open',0,()=>clockAddress.readU32());const end=__arkdpsReplayCheckpoint('clock_close',0,()=>clockAddress.readU32());if(frame!==end)throw Error('clock changed');seen.push(frame);return {mark:{sequence:0,epoch:0,frame,game_time:frame,active:0,active_hooks:[],error:null,stopped:false,native_capture_scope_mode_epoch:0},acknowledged:0,events_after:0,events_through:0,events:[],hp_context:[],read_fields:{frame},terminal_read:terminalTest?{}:null};}");
 r.sampling_dispatch=JS_GetPropertyStr(c,global,"__arkdpsSamplingDispatch");r.prefix_projection=JS_GetPropertyStr(c,global,"checkpointProjection");r.publication_publish=JS_GetPropertyStr(c,global,"__arkdpsPublishClosedRecord");r.publication_request=JS_GetPropertyStr(c,global,"__arkdpsPublicationRequest");r.initialized=true;
 if(argc==2) {
  const std::string arm=argv[1];
  if(arm=="--gap") {
   script(c,"function badProjection(){try{__arkdpsReplayGap('unverified cold metadata private-token:8',{role:'clock_open',checkpoint:0,field_id:1});}catch(error){}return null;}");
   JS_FreeValue(c,r.prefix_projection);r.prefix_projection=JS_GetPropertyStr(c,global,"badProjection");
  } else if(arm=="--receipt"||arm=="--receipt-type") {
   script(c,arm=="--receipt"?"function badPublication(){return {};}":"function badPublication(){return [];}");
   JS_FreeValue(c,r.publication_publish);r.publication_publish=JS_GetPropertyStr(c,global,"badPublication");
  } else return 2;
  bool rejected=false;
  try{sample_checkpoint(r,1);}catch(const std::exception& error){
   const std::string detail=error.what();
   if(arm=="--gap") {
    assert(detail.rfind("native_sampling_failure:v1,s=3,c=0,j=0; native_sampling_gap:v1,t=0,r=1,p=0,f=1,g=1,m=8,k=1,q=",0)==0);
    assert(detail.find("private-token")==std::string::npos);
   } else assert(detail==(arm=="--receipt"?"native_sampling_failure:v1,s=6,c=0,j=403":"native_sampling_failure:v1,s=5,c=0,j=306"));
   rejected=true;
  }
  assert(rejected&&!r.publication_ready&&r.published_ticket==0&&r.completed_sample_request==0&&r.lanes->reserved.load()==0);
  arkdps::owned_js::end();for(auto value:{r.sampling_dispatch,r.prefix_projection,r.publication_publish,r.publication_request,global})JS_FreeValue(c,value);JS_FreeContext(c);JS_FreeRuntime(r.runtime);
  std::cout<<"PASS_NATIVE_SAMPLING_CHECKPOINT "<<arm<<" rejects without advancing publication; bounded diagnostic\n";return 0;
 }
 assert(argc==1);
 // No hook traffic: two real samples retain raw=0, sequence=0 and read new bytes.
 r.sample_request=1;assert(sample_checkpoint(r,1));const auto first=r.completed_sample_token;
 assert(r.published_ticket==0&&r.processed_ticket==0&&r.closed_ticket==0&&r.lanes->reserved.load()==0);
 set_frame(clock,31);r.capture_paused.store(true);r.sample_request=2;assert(sample_checkpoint(r,2));assert(first!=r.completed_sample_token);
 script(c,"if(seen.join(',')!=='30,31')throw Error('stale snapshot reused');");
 // Native activity/open prefix prevents checkpoint publication.
 const auto serial=r.sampling_serial;r.active.store(1);assert(!sample_checkpoint(r,3));r.active.store(0);
 r.lanes->reserved.store(1);assert(!sample_checkpoint(r,3));r.lanes->reserved.store(0);assert(r.sampling_serial==serial);
 // Changed independent closing clock is discarded, preserving actual failure.
 set_frame(clock,40);ChangingRead changing{&clock};assert(!sample_checkpoint(r,3,change_clock,&changing));assert(r.sampling_serial==serial);assert(r.cold_publication_request.load()==3);
 set_frame(clock,42);r.cold_publication_request.store(0);
 // A retry reuses its request, then completes without a new hook.
 r.sample_request=0;r.completed_sample_request=0;r.active.store(1);
 const auto pending=Json::parse(arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"fresh\"}"));assert(pending.at("retry")==true);
 const auto request=pending.at("native_sample_request").get<std::string>();
 const auto retry=Json::parse(arkdps::request_on_worker(Json{{"operation","mark"},{"sampling","fresh"},{"sample_request",request}}.dump()));assert(retry.at("native_sample_request")==request);assert(r.lanes->request_serial.load()==1);
 r.active.store(0);const auto ready=Json::parse(arkdps::request_on_worker(Json{{"operation","mark"},{"sampling","fresh"},{"sample_request",request}}.dump()));assert(ready.at("frame")==42);assert(ready.at("native_sample_request")==request);
 // Confirm validates the retained opening token and captures CURRENT state.
 set_frame(clock,43);const auto closed=Json::parse(arkdps::request_on_worker(Json{{"operation","mark"},{"sampling","confirm"},{"publication_token",ready.at("native_publication").at("token")}}.dump()));
 assert(closed.at("frame")==43&&closed.at("native_publication").at("token")!=ready.at("native_publication").at("token"));
 assert(r.lanes->reserved.load()==0&&r.lanes->failure.load()==0);
 const auto ids=r.lanes->request_serial.load();
 const auto expired=Json::parse(arkdps::request_on_worker(Json{{"operation","mark"},{"sampling","confirm"},{"publication_token","0:99999"}}.dump()));assert(expired.at("retry")==true&&r.lanes->request_serial.load()==ids);
 bool wrong_binding=false;try{arkdps::request_on_worker(Json{{"operation","mark"},{"sampling","fresh"},{"sample_request",closed.at("native_sample_request")}}.dump());}catch(const std::exception& error){wrong_binding=std::string(error.what()).find("transaction binding")!=std::string::npos;}assert(wrong_binding&&r.lanes->failure.load()==0);

 // A different mode cannot resume an old request. Missing an explicit retry
 // identity starts a new transaction, even while an earlier one is pending.
 r.active.store(1);const auto old_mode=Json::parse(arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"fresh\"}"));
 const auto old_request=old_mode.at("native_sample_request").get<std::string>();r.capture_mode_epoch.store(1);
 bool wrong_mode=false;try{arkdps::request_on_worker(Json{{"operation","mark"},{"sampling","fresh"},{"sample_request",old_request}}.dump());}catch(const std::exception& error){wrong_mode=std::string(error.what()).find("transaction binding")!=std::string::npos;}assert(wrong_mode);
 const auto new_mode=Json::parse(arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"fresh\"}"));
 assert(new_mode.at("retry")==true&&new_mode.at("native_sample_request")!=old_request);
 r.capture_mode_epoch.store(0);r.active.store(0);
 const auto reset_mode=Json::parse(arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"fresh\"}"));
 assert(!reset_mode.value("retry",false)&&reset_mode.at("frame")==43&&r.lanes->failure.load()==0);

 // Even a real checkpoint cannot publish a copied terminal without live fence proof.
 const auto before_terminal_attempts=r.sampling_attempts;const auto before_terminal_published=r.completed_sample_token;
 const auto nonterminal=Json::parse(arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"terminal\"}"));
 assert(nonterminal.at("retry")==true&&r.sampling_attempts==before_terminal_attempts&&r.completed_sample_token==before_terminal_published);
 script(c,"terminalTest=true;");const auto before_terminal=r.completed_sample_token;assert(!sample_checkpoint(r,44));assert(r.completed_sample_token==before_terminal);
 // A genuine quiet terminal fence permits the complete terminal endpoint.
 std::uintptr_t singleton=0;const auto* singleton_bytes=reinterpret_cast<const std::byte*>(&singleton);
 assert(r.terminal_fence.certify_singleton(singleton_bytes,sizeof singleton));
 assert(r.terminal_fence.add(reinterpret_cast<std::uintptr_t>(&singleton),singleton_bytes,sizeof singleton));
 r.terminal_fence.ticket=0;r.terminal_fence.activity=r.activity_generation.load();r.terminal_fence.verified=true;
 script(c,"checkpointProjection=(original=>record=>{const p=original(record);if(terminalTest){const m=p.mark,b={sequence:m.sequence,epoch:m.epoch,frame:m.frame,game_time:m.game_time};m.terminal_boundary=b;p.terminal_read={...m,terminal_only:true,terminal_read_version:1,retry:false,after:0,through:0,events:[],hp_context:[],terminal_boundary:b};}return p;})(checkpointProjection);");
 JS_FreeValue(c,r.prefix_projection);r.prefix_projection=JS_GetPropertyStr(c,global,"checkpointProjection");
 assert(sample_checkpoint(r,44));assert(r.completed_sample_token!=before_terminal);
 script(c,"if(__arkdpsPublicationJournal.latest.mark.native_publication.terminal_verified!==true)throw Error('terminal endpoint not verified');");
 const auto before_fast_attempts=r.sampling_attempts,before_fast_requests=r.lanes->request_serial.load();
 const auto fast=Json::parse(arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"terminal\"}"));
 assert(fast.at("native_publication").at("terminal_verified")==true&&fast.at("native_publication").at("token")==r.completed_sample_token);
 assert(r.sampling_attempts==before_fast_attempts&&r.lanes->request_serial.load()==before_fast_requests);
 // A newly verified destroy fence cannot authorize an older retained terminal
 // point merely because its mode and witness bytes happen to be unchanged.
 const auto previous_terminal=r.published_token;const auto before_new_fence_attempts=r.sampling_attempts;
 r.lanes->reserved.store(2);r.processed_ticket=2;r.closed_ticket=2;
 r.activity_generation.store(r.terminal_fence.activity+2);
 r.terminal_fence.ticket=2;r.terminal_fence.activity=r.activity_generation.load();
 const auto next_terminal=Json::parse(arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"terminal\"}"));
 assert(next_terminal.at("native_publication").at("closed_ticket")=="2");
 assert(next_terminal.at("native_publication").at("token")!=previous_terminal);
 assert(r.sampling_attempts>before_new_fence_attempts);
 const auto terminal_token=r.completed_sample_token;singleton=1;
 assert(!sample_checkpoint(r,45));assert(r.completed_sample_token==terminal_token);
 const auto invalid_terminal=Json::parse(arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"terminal\"}"));
 assert(invalid_terminal.at("retry")==true&&r.completed_sample_token==terminal_token);
 r.terminal_fence.clear();script(c,"terminalTest=false;");
 const auto before_control=r.lanes->request_serial.load();r.desired_mode=0;
 const auto control=Json::parse(arkdps::request_on_worker("{\"operation\":\"configure_mode\",\"autochess_enabled\":false}"));
 assert(control.at("applied")==true&&control.at("desired_mode")=="local"&&r.lanes->request_serial.load()==before_control);
 bool legacy_rejected=false;try{arkdps::request_on_worker("{\"operation\":\"mark\"}");}catch(const std::exception& error){legacy_rejected=std::string(error.what())=="native mark requires a sampling transaction";}
 assert(legacy_rejected&&r.lanes->request_serial.load()==before_control);
 bool preference_rejected=false;try{arkdps::request_on_worker("{\"operation\":\"mark\",\"sampling\":\"fresh\",\"autochess_enabled\":false}");}catch(const std::exception& error){preference_rejected=std::string(error.what())=="native preference requires mode control";}
 assert(preference_rejected&&r.lanes->request_serial.load()==before_control);
 // Ordinary objects cannot mint checkpoint evidence or publish a forged kind.
 script(c,"if(__arkdpsVerifiedSamplingCheckpoint(lastRecord))throw Error('lease escaped');const reused=new ArkdpsPublicationJournal();if(!reused.publish(lastRecord,{}).native_publication_failure)throw Error('brand reused');globalThis.__arkdpsConsumeSamplingCheckpoint=()=>true;if(__arkdpsVerifiedSamplingCheckpoint({kind:'sampling_checkpoint'}))throw Error('forged brand');const bad=new ArkdpsPublicationJournal();const failure=bad.publish({version:1,kind:'sampling_checkpoint',edge:'checkpoint',ticket:'0',sampling_serial:'99',activity_begin:'0',activity_end:'0',closed_after:true,active_after:0},{});if(!failure.native_publication_failure)throw Error('forged publication accepted');");
 // Structural capture faults poison rather than become an endless busy retry.
 r.sampling_plan->plan->max_reads=0;bool structural=false;try{sample_checkpoint(r,46);}catch(const std::exception& error){structural=std::string(error.what())=="native_sampling_failure:v1,s=1,c="+std::to_string(static_cast<unsigned>(LaneFault::publication_failure))+",j=0";}assert(structural&&r.lanes->failure.load()==static_cast<unsigned>(LaneFault::publication_failure));
 arkdps::owned_js::end();for(auto value:{r.sampling_dispatch,r.prefix_projection,r.publication_publish,r.publication_request,global})JS_FreeValue(c,value);JS_FreeContext(c);JS_FreeRuntime(r.runtime);
 std::cout<<"PASS_NATIVE_SAMPLING_CHECKPOINT no hooks/paused/raw0/seq0/new clock/retry/confirm/active/open prefix/changed clock/forged brand\n";
 return 0;
}
