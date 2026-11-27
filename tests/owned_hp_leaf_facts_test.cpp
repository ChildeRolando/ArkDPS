#define main original_hp_transport_test_main
#include "owned_hp_observation_test.cpp"
#undef main
#include "experiments/owned_hp_leaf_facts.h"

static void take(const CompiledPlan& code,Memory& memory,Snapshot& snapshot,Phase phase,
                 const CaptureGraph* scope=nullptr,bool attributes=true,bool change_scope=false){
 Context input;input.args[0]=memory.root();input.args_valid=1;input.entry_refs=input.args;input.entry_valid=1;
 input.globals=code.globals;input.globals_valid=code.globals_valid;input.checkpoint=3;
 assert(capture_begin(snapshot,phase,input,8192,256)==Error::ok);
 for(unsigned i=0;i<code.plan->field_count;++i){
  bool skip=false;
  if(!attributes)for(const auto& spec:code.observation_specs)if(spec.source_index==i&&spec.name.rfind("attributes.",0)==0)skip=true;
  if(!skip)capture_append(*code.plan,phase,input,Memory::read,&memory,snapshot,i,i+1);
 }
 if(scope){
  capture_append(*scope->code->plan,Phase::checkpoint,input,Memory::read,&memory,snapshot,0,1);
  if(change_scope){std::uint32_t frame=31;std::memcpy(memory.clock.data(),&frame,4);}
  capture_append(*scope->code->plan,Phase::checkpoint,input,Memory::read,&memory,snapshot,1,2);
 }
 capture_finalize(snapshot);
}
static void view_of(OwnedReplayView& view,const Snapshot& snapshot,const CaptureConfigurations& configs){
 assert(view.initialize(snapshot.segments.data(),snapshot.segment_count,snapshot.fields.data(),snapshot.field_count,
    snapshot.bytes.data(),snapshot.byte_count,configs.roles.size(),snapshot.error)==Error::ok);
}
int main(){
 Memory memory;memory.initialize();auto code=plan(memory);CaptureConfigurations configs;configs.intern(code);
 auto a=std::make_unique<Snapshot>(),b=std::make_unique<Snapshot>();
 auto av=std::make_unique<OwnedReplayView>(),bv=std::make_unique<OwnedReplayView>();OwnedRoleIndex roles;
 const auto before=std::int64_t{100}<<32,after=std::int64_t{101}<<32;
 memory.set(memory.entity,64,before);take(code,memory,*a,Phase::entry);
 memory.set(memory.entity,64,after);take(code,memory,*b,Phase::leave);
 view_of(*av,*a,configs);view_of(*bv,*b,configs);
 auto first=std::make_unique<owned::HpLeafPairFacts>(),next=std::make_unique<owned::HpLeafPairFacts>();
 assert(owned::collect_hp_leaf_pair(code,{*av,configs.roles,roles,false},{*bv,configs.roles,roles,false},memory.root(),3,3,*first));
 assert(first->before==before&&first->after==after&&!first->unchanged);
 memory.set(memory.entity,64,after);take(code,memory,*a,Phase::entry);
 memory.set(memory.entity,64,after+(std::int64_t{1}<<32));take(code,memory,*b,Phase::leave);
 view_of(*av,*a,configs);view_of(*bv,*b,configs);
 assert(owned::collect_hp_leaf_pair(code,{*av,configs.roles,roles,false},{*bv,configs.roles,roles,false},memory.root(),3,3,*next));
 assert(owned::same_hp_leaf_window(code,*first,*next));
 memory.set(memory.entity,24,std::uint32_t{74});take(code,memory,*b,Phase::leave);view_of(*bv,*b,configs);
 assert(!owned::collect_hp_leaf_pair(code,{*av,configs.roles,roles,false},{*bv,configs.roles,roles,false},memory.root(),3,3,*next));
 memory.set(memory.entity,24,std::uint32_t{73});
 // Equal raw HP does not demand missing optional attributes or substitute 0.
 memory.set(memory.entity,64,before);take(code,memory,*a,Phase::entry);take(code,memory,*b,Phase::leave,nullptr,false);
 view_of(*av,*a,configs);view_of(*bv,*b,configs);
 assert(owned::collect_hp_leaf_pair(code,{*av,configs.roles,roles,false},{*bv,configs.roles,roles,false},memory.root(),3,3,*next)&&next->unchanged);
 assert(next->leave.fields[index(code,"attributes.count",Phase::leave)].status==UINT16_MAX);
 // A changed pair cannot borrow that same absent MaxHP evidence.
 memory.set(memory.entity,64,after);take(code,memory,*b,Phase::leave,nullptr,false);view_of(*bv,*b,configs);
 assert(!owned::collect_hp_leaf_pair(code,{*av,configs.roles,roles,false},{*bv,configs.roles,roles,false},memory.root(),3,3,*next));
 // Independent callback clocks are checked from held bytes, not a cached frame.
 std::uint32_t frame=31;std::memcpy(memory.clock.data(),&frame,4);take(code,memory,*b,Phase::leave);view_of(*bv,*b,configs);
 assert(!owned::collect_hp_leaf_pair(code,{*av,configs.roles,roles,false},{*bv,configs.roles,roles,false},memory.root(),3,3,*next));
 frame=30;std::memcpy(memory.clock.data(),&frame,4);
 Json fields=Json::array();
 for(unsigned i=0;i<2;++i)fields.push_back({{"field_id",i+1},{"phase","checkpoint"},{"role",i?"scope.clock_close":"scope.clock_open"},
  {"root","absolute"},{"absolute_address",hex(reinterpret_cast<std::uintptr_t>(memory.clock.data()))},{"root_index",0},{"pointer_offsets",Json::array()},
  {"offset",0},{"bytes",12},{"kind","bytes"},{"required",true},{"guard_id",1},{"guard_side",i?"after":"before"},{"projection",true},
  {"observation",{{"values",Json::array({codec("u32"),codec("i64",4)})}}}});
 configs.publish({{"version",1},{"revision",1},{"mode_epoch",0},{"raw_collection","all"},{"available",true},{"fields",fields},{"body_insertion_index",1}},false);
 const auto* config=configs.pin(0);assert(config&&config->scope->primitive_ready);
 auto left=std::make_unique<owned::HpLeafScopeFacts>(),right=std::make_unique<owned::HpLeafScopeFacts>();
 take(code,memory,*a,Phase::leave,config->scope.get());view_of(*av,*a,configs);
 assert(owned::collect_hp_leaf_scope({*av,configs.roles,roles,false},config->scope->check_program,3,*left));
 assert(left->count==2&&owned::same_hp_leaf_scope(*left,*left));
 take(code,memory,*b,Phase::leave,config->scope.get(),true,true);view_of(*bv,*b,configs);
 poisoned=false;assert(!owned::collect_hp_leaf_scope({*bv,configs.roles,roles,false},config->scope->check_program,3,*right)&&!poisoned);
 // Ordinary consumption still receives the exact original refusal status.
 auto* runtime=JS_NewRuntime();auto* c=JS_NewContext(runtime);owned::install(c,[]()noexcept{poisoned=true;});
 begin(code,configs,memory,*b,Phase::leave);owned::bind_graphs(config,1,0,0,3);
 JSValue args[]{JS_NewString(c,"scope"),JS_NewInt32(c,1),JS_NewInt32(c,0),JS_NewInt32(c,1)};
 auto result=owned::graph_check(c,JS_UNDEFINED,4,args);assert(JS_IsUndefined(result)&&owned::binding().error==Error::changed_guard);
 JS_FreeValue(c,result);for(auto arg:args)JS_FreeValue(c,arg);owned::end();JS_FreeContext(c);JS_FreeRuntime(runtime);
 std::cout<<"PASS_HP_LEAF_FACTS pure held-byte pair/scope/UID/clock/optional statuses/ordinary refusal retained; no business or throughput acceptance\n";
}
