// Self-owned engine boundary test. No detours or process attachment are enabled.
#include "../native_pc/native_event_runtime/engine.cpp"
#include <cassert>
#include <fstream>
#include <iostream>
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wreturn-type"
#define main unused_hp_semantics_main
#include "owned_hp_observation_test.cpp"
#undef main
#pragma GCC diagnostic pop
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
static void script(JSContext* c,const std::string& text){auto result=JS_Eval(c,text.data(),text.size(),"owned-batch.js",JS_EVAL_TYPE_GLOBAL);if(JS_IsException(result))throw std::runtime_error(exception(c));JS_FreeValue(c,result);}
int main(int argc,char** argv){
 const std::string test_mode=argc>1?argv[1]:"normal";const bool fault=test_mode=="fault";
 const bool malformed=test_mode=="early"||test_mode=="pending"||test_mode=="string"||test_mode=="nan";
 auto& runtime=state();runtime.runtime=JS_NewRuntime();runtime.context=JS_NewContext(runtime.runtime);auto* c=runtime.context;
 arkdps::capture::CaptureLanes::Config limits;limits.lane_queue={16,65536};limits.spill={16,65536};runtime.lanes=std::make_unique<arkdps::capture::CaptureLanes>(limits);
 owned::install(c,[]()noexcept{state().lanes->poison(LaneFault::replay_structure);});
 auto global=JS_GetGlobalObject(c);function(c,global,"__nativeReplayHealthy",js_replay_healthy,0);function(c,global,"__pid",js_pid,0);function(c,global,"__nativeActive",js_active,0);function(c,global,"__nativeBoundedReplayAdvance",js_batch_advance,2);
 runtime.batch_advance=JS_GetPropertyStr(c,global,"__nativeBoundedReplayAdvance");
 std::ifstream stream("native_pc/native_event_runtime/bridge.js");std::string bridge((std::istreambuf_iterator<char>(stream)),{});assert(!bridge.empty());script(c,bridge);
 runtime.replay_dispatch=JS_GetPropertyStr(c,global,"__arkdpsReplayDispatch");runtime.replay_batch=JS_GetPropertyStr(c,global,"__arkdpsReplayBatch");
 if(malformed){
  const auto expression=test_mode=="early"?"(()=>0)":test_mode=="pending"?"((advance,controller)=>{advance(controller,undefined);return 1;})":test_mode=="string"?"((original)=>(...args)=>{original(...args);return '3';})(__arkdpsReplayBatch)":"((original)=>(...args)=>{original(...args);return NaN;})(__arkdpsReplayBatch)";
  auto replacement=JS_Eval(c,expression,std::strlen(expression),"owned-malformed-batch.js",JS_EVAL_TYPE_GLOBAL);assert(JS_IsFunction(c,replacement));JS_FreeValue(c,runtime.replay_batch);runtime.replay_batch=replacement;
 }
 Memory memory;memory.initialize();auto hook=std::make_unique<Hook>();hook->capture_plan=plan(memory);hook->capture_plan.hp_normal=true;runtime.configurations.intern(hook->capture_plan);
 Json descriptors=Json::array();for(unsigned edge=0;edge<2;++edge)for(const auto& field:hook->capture_plan.hp_observations[edge]){
  Json values=Json::array();for(const auto& codec:field.values)values.push_back({{"kind",codec.kind==0?"u8":codec.kind==1?"u32":codec.kind==2?"u64":codec.kind==3?"i64":"utf16"},{"offset",codec.offset}});
  descriptors.push_back({{"phase",edge?"leave":"entry"},{"name",field.name},{"role",runtime.configurations.roles[field.raw.role_id-1]},{"kind",field.raw.kind?"utf16":"bytes"},{"bytes",field.raw.bytes},{"max_units",field.raw.max_units},{"observation",{{"name",field.name},{"values",values}}}});
 }
 script(c,"globalThis.contract="+Json({{"address","0x9000"},{"fields",descriptors}}).dump()+";globalThis.fault="+(fault?"true":"false")+R"(;
 let steps=[],retained,retainedRecord,retainedMode;
 globalThis.callbacks={
  arkdpsDirectHpEntry(actor,io){if(!__arkdpsIsPreparedHpObservation(io,contract,actor))throw Error('brand');this.write={actor};if(io.identity(actor)[1]!==73)throw Error('uid');retained=io;retainedRecord=__arkdpsReplayRecord();retainedMode=__arkdpsReplayModeRecord();steps.push('e');},
  arkdpsDirectHpLeave(io){if(!__arkdpsIsPreparedHpObservation(io,contract))throw Error('brand');if(fault){try{retained.identity(this.write.actor);}catch(ignored){}}else if(io.closing(this.write.actor)[0]!==73)throw Error('closing');if(retainedRecord.edge!=='entry'||retainedMode.edge!=='entry'||retainedMode.facts.mode_epoch!==5)throw Error('retained header');steps.push('l');},
  onEnter(args){this.write={actor:args[0]};steps.push('generic-e');},onLeave(){if(!this.write)throw Error('lost write');steps.push('generic-l');}
 };
 )");
 hook->callbacks=JS_GetPropertyStr(c,global,"callbacks");hook->direct_entry=JS_GetPropertyStr(c,hook->callbacks,"arkdpsDirectHpEntry");hook->direct_leave=JS_GetPropertyStr(c,hook->callbacks,"arkdpsDirectHpLeave");hook->direct_contract=JS_GetPropertyStr(c,global,"contract");runtime.hooks.push_back(std::move(hook));
 auto& lane=*runtime.lanes->lanes[0];lane.owner.store(GetCurrentThreadId());lane.birth.store(1);runtime.initialized=true;
 const auto enqueue=[&](unsigned ticket,Phase phase,unsigned invocation,bool publication=false){capture_point(runtime.hooks[0]->capture_plan,memory,lane.snapshot,phase);RecordHeader h{};h.ticket=ticket;h.invocation=invocation;h.phase=phase;h.args[0]=memory.root();h.arg_mask=1;h.depth=1;h.thread_id=GetCurrentThreadId();h.thread_birth=1;h.mode_epoch=5;h.scope_mode_epoch=5;h.active=1;h.active_after=phase==Phase::leave?0:1;h.closed_after=phase==Phase::leave;h.publication_request=publication?1:0;assert(runtime.lanes->publish(lane,h));runtime.lanes->reserved.store(ticket);};
 enqueue(1,Phase::entry,1);enqueue(2,Phase::leave,1);if(fault)enqueue(3,Phase::entry,2);else{enqueue(3,Phase::entry,2);enqueue(4,Phase::leave,2,true);}
 std::size_t first=0;assert(pop_replay_record(first));bool failed=false;try{replay_bounded_batch(first,std::chrono::steady_clock::now()+std::chrono::seconds(1),32);}catch(const std::exception& error){failed=true;assert(std::string(error.what()).find(malformed?"incomplete bounded replay result":"native_replay_gap:")!=std::string::npos);}
 if(malformed){assert(failed&&runtime.lanes->failure.load()==static_cast<unsigned>(LaneFault::replay_structure));if(test_mode=="early"||test_mode=="pending")assert(runtime.processed_ticket==0);else assert(runtime.processed_ticket==3);}
 else if(fault){assert(failed&&runtime.processed_ticket==1&&runtime.closed_ticket==0);}
 else {assert(!failed&&runtime.processed_ticket==3&&runtime.deferred_record_size);const auto deferred=runtime.deferred_record_size;runtime.deferred_record_size=0;replay_record(runtime.consume_buffer.data(),deferred);assert(runtime.processed_ticket==4&&runtime.closed_ticket==4);script(c,"if(steps.join(',')!=='e,l,e,generic-l')throw Error('barrier ordering')");}
 assert(runtime.batch_calls==1&&runtime.batch_issued==replay_batch_state().issued&&runtime.batch_finalized==replay_batch_state().finalized);
 if(fault)assert(runtime.batch_issued==2&&runtime.batch_finalized==1);
 if(!fault&&!malformed)assert(runtime.batch_issued==3&&runtime.batch_finalized==3&&runtime.maximum_batch_edges==3&&runtime.batch_barriers==1);
 // Independent rejection of a public call without the native-issued controller.
 if(runtime.lanes->failure.load()==0){JSValue wrong[]{JS_NewObject(c),JS_UNDEFINED};auto rejected=js_batch_advance(c,JS_UNDEFINED,2,wrong);assert(JS_IsException(rejected)&&runtime.lanes->failure.load()==static_cast<unsigned>(LaneFault::replay_structure));auto ignored=JS_GetException(c);JS_FreeValue(c,ignored);JS_FreeValue(c,wrong[0]);}
 owned::end();for(auto& calls:runtime.receivers)for(auto& call:calls){JS_FreeValue(c,call.receiver);call={};}
 for(auto& h:runtime.hooks){JS_FreeValue(c,h->callbacks);JS_FreeValue(c,h->direct_entry);JS_FreeValue(c,h->direct_leave);JS_FreeValue(c,h->direct_contract);}runtime.hooks.clear();
 JS_FreeValue(c,runtime.replay_dispatch);JS_FreeValue(c,runtime.replay_batch);JS_FreeValue(c,runtime.batch_advance);JS_FreeValue(c,global);JS_FreeContext(c);JS_FreeRuntime(runtime.runtime);
 std::cout<<"PASS_OWNED_BATCH actual engine/controller/owned view/QJS per-item lease/barrier/retained immutable/fault prefix\n";
 return 0;
}
