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

static void script(JSContext* c,const std::string& s){auto v=JS_Eval(c,s.data(),s.size(),"light-hp-owned.js",JS_EVAL_TYPE_GLOBAL);if(JS_IsException(v))throw std::runtime_error(exception(c));JS_FreeValue(c,v);}
int main(){
 auto& r=state();r.runtime=JS_NewRuntime();r.context=JS_NewContext(r.runtime);auto* c=r.context;
 CaptureLanes::Config limits;limits.lane_queue={32,65536};limits.spill={32,65536};r.lanes=std::make_unique<CaptureLanes>(limits);
 Memory memory;memory.initialize();auto parent=std::make_unique<Hook>();parent->capture_plan=plan(memory);parent->capture_plan.snapshot_hp=true;
 auto& policy=parent->capture_plan;
 assert(omit_hp_telemetry(policy,1,1));assert(!omit_hp_telemetry(policy,2,1));assert(!omit_hp_telemetry(policy,2,2));
 policy.hp_dirty=true;assert(!omit_hp_telemetry(policy,1,1));policy.hp_dirty=false;
 policy.snapshot_hp=false;assert(!omit_hp_telemetry(policy,1,1));policy.snapshot_hp=true;
 r.configurations.intern(policy);r.hooks.push_back(std::move(parent));
 auto& lane=*r.lanes->lanes[0];lane.owner.store(GetCurrentThreadId());lane.birth.store(1);lane.depth=1;
 Context context;context.args[0]=memory.root();context.args_valid=1;context.entry_refs=context.args;context.entry_valid=1;context.globals=policy.globals;context.globals_valid=policy.globals_valid;
 const auto boundary=[&](std::uint64_t ticket,Phase phase){RecordHeader h{};h.ticket=ticket;h.invocation=1;h.phase=phase;h.depth=1;h.active=1;h.active_after=phase==Phase::leave?0:1;h.closed_after=phase==Phase::leave;h.args=context.args;h.arg_mask=1;h.thread_id=GetCurrentThreadId();h.thread_birth=1;return h;};
 const auto consume=[&](RecordHeader h){assert(r.lanes->publish(lane,h));r.lanes->reserved.store(h.ticket);std::size_t n=0;assert(pop_replay_record(n));return consume_planned_hp_boundary(r.consume_buffer.data(),n);};
 auto e=boundary(1,Phase::entry);capture_scope_point(policy,e.phase,context,nullptr,Memory::read,&memory,lane.snapshot,e,&lane);
 assert(e.planned_hp_omission&&lane.snapshot.read_count==0&&lane.snapshot.field_count==0);assert(consume(e));assert(r.processed_ticket==1&&r.closed_ticket==0&&r.replay_depth[0]==1);
 // No raw omission can invent a prefix or publish a snapshot.
 assert(r.published_ticket==0&&r.delivered_sequence==0);
 for(unsigned fault=0;fault<5;++fault){auto h=boundary(2,Phase::leave);h.planned_hp_omission=true;
  if(fault==0)h.invocation=99;
  if(fault==1)h.depth=2;
  if(fault==2)h.parent=7;
  if(fault==3)h.parent_checkpoint_count=1;
  if(fault==4)h.error=Error::field_unreadable;
  bool refused=false;try{consume_planned_hp_boundary(reinterpret_cast<const std::byte*>(&h),sizeof h);}catch(const std::exception&){refused=true;}
  assert(refused&&r.processed_ticket==1&&r.closed_ticket==0&&r.published_ticket==0);
 }
 // Nested and cross-thread acquisition retain their real fields and no skip bit.
 lane.depth=2;auto nested=boundary(2,Phase::entry);nested.depth=2;nested.active=2;
 capture_scope_point(policy,nested.phase,context,nullptr,Memory::read,&memory,lane.snapshot,nested,&lane);
 assert(!nested.planned_hp_omission&&lane.snapshot.read_count>0);
 lane.depth=1;auto concurrent=boundary(2,Phase::entry);concurrent.active=2;
 capture_scope_point(policy,concurrent.phase,context,nullptr,Memory::read,&memory,lane.snapshot,concurrent,&lane);
 assert(!concurrent.planned_hp_omission&&lane.snapshot.read_count>0);
 // Restore the still-open original mechanical parent. Its child would use the
 // unchanged native topology/replay path; retaining it never executes source HP.
 lane.calls[0].planned_hp_omission=true;
 auto l=boundary(2,Phase::leave);capture_scope_point(policy,l.phase,context,nullptr,Memory::read,&memory,lane.snapshot,l,&lane);
 assert(l.planned_hp_omission&&lane.snapshot.read_count==0);assert(consume(l));assert(r.processed_ticket==2&&r.closed_ticket==2&&r.replay_depth[0]==0&&r.published_ticket==0);
 // A pending publication cannot take the native no-business shortcut.
 auto pe=boundary(3,Phase::entry);pe.invocation=3;capture_scope_point(policy,pe.phase,context,nullptr,Memory::read,&memory,lane.snapshot,pe,&lane);assert(consume(pe));
 auto pl=boundary(4,Phase::leave);pl.invocation=3;pl.publication_request=1;capture_scope_point(policy,pl.phase,context,nullptr,Memory::read,&memory,lane.snapshot,pl,&lane);
 assert(pl.planned_hp_omission&&pl.projection_candidate&&lane.snapshot.read_count>0);
 assert(!consume(pl));assert(r.processed_ticket==3&&r.closed_ticket==2&&r.published_ticket==0);
 JS_FreeContext(c);JS_FreeRuntime(r.runtime);std::cout<<"PASS_LIGHT_HP_BOUNDARIES root/nested/concurrent/strict faults/closed prefix/publication barrier\n";
}
