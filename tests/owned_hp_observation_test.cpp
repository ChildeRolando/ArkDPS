#include "owned_replay_js.h"
#include <cassert>
#include <iostream>
#include <sstream>
using namespace arkdps::capture;namespace owned=arkdps::owned_js;using Json=nlohmann::json;
static bool poisoned=false;
static std::string hex(std::uintptr_t value){std::ostringstream text;text<<"0x"<<std::hex<<value;return text.str();}
struct Memory {
 std::array<std::byte,512> entity{},attributes{},cached{};std::array<std::byte,512> string{};std::array<std::byte,12> clock{};
 template<class T>void set(std::array<std::byte,512>& object,unsigned offset,T value){std::memcpy(object.data()+offset,&value,sizeof value);}
 std::uintptr_t root()const{return reinterpret_cast<std::uintptr_t>(entity.data());}
 static bool read(void* user,std::uintptr_t address,void* output,std::size_t bytes)noexcept{
  auto& memory=*static_cast<Memory*>(user);
  const std::pair<const std::byte*,std::size_t> ranges[]{{memory.entity.data(),memory.entity.size()},{memory.attributes.data(),memory.attributes.size()},{memory.cached.data(),memory.cached.size()},{memory.string.data(),memory.string.size()},{memory.clock.data(),memory.clock.size()}};
  for(const auto& range:ranges){const auto first=reinterpret_cast<std::uintptr_t>(range.first);if(address>=first&&address-first<=range.second&&bytes<=range.second-(address-first)){std::memcpy(output,reinterpret_cast<const void*>(address),bytes);return true;}}
  return false;
 }
 void initialize(){
  entity={};attributes={};cached={};string={};clock={};
  set(entity,0,UINT64_MAX);set(entity,24,std::uint32_t{73});set(entity,37,std::uint8_t{1});set(entity,64,INT64_MIN);
  set(entity,128,reinterpret_cast<std::uintptr_t>(string.data()));set(entity,176,reinterpret_cast<std::uintptr_t>(attributes.data()));set(attributes,104,reinterpret_cast<std::uintptr_t>(cached.data()));
  set(cached,24,std::uint32_t{1});set(cached,32,UINT64_MAX);set(cached,40,std::uint64_t{7});set(cached,48,std::uint8_t{1});
  set(string,0,std::uintptr_t{0x5000});const std::uint16_t units[]{65,0,0x4e2d,0xd800,66,0xdfff,67,0xd83d,0xde00};set(string,16,std::int32_t{9});std::memcpy(string.data()+20,units,sizeof units);
  const std::uint32_t frame=30;const std::int64_t fixed=-4294967296LL;std::memcpy(clock.data(),&frame,4);std::memcpy(clock.data()+4,&fixed,8);
 }
};
static Json codec(const char* kind,unsigned offset=0){return {{"kind",kind},{"offset",offset}};}
static CompiledPlan plan(Memory& memory){
 Json fields=Json::array();unsigned next=1;
 const auto add=[&](const char* phase,const char* name,const char* root,unsigned offset,unsigned bytes,Json values,Json path=Json::array(),const char* kind="bytes",unsigned guard=0,const char* side="none"){
  Json field{{"field_id",next++},{"phase",phase},{"role",guard?name:"body"},{"name",name},{"root",root},{"root_index",0},{"pointer_offsets",path},{"offset",offset},{"bytes",bytes},{"kind",kind},{"guard_id",guard},{"guard_side",side},{"required",false},{"observation",{{"name",name},{"values",values}}}};
  if(std::string(kind)=="utf16"){field["max_units"]=200;field["expected_class"]="0x5000";}
  fields.push_back(field);
 };
 add("entry","hp.identity","arg",0,28,Json::array({codec("u64"),codec("u32",24)}));
 add("entry","hp.born","arg",37,1,Json::array({codec("u8")}));
 add("entry","hp.before","arg",64,8,Json::array({codec("i64")}));
 add("entry","hp.id","arg",0,0,Json::array({codec("utf16")}),Json::array({128}),"utf16");
 for(const auto* phase:{"entry","leave"}){
  add(phase,"clock_open","global_slot",0,12,Json::array({codec("u32"),codec("i64",4)}),Json::array(),"bytes",1,"before");
  add(phase,"clock_close","global_slot",0,12,Json::array({codec("u32")}),Json::array(),"bytes",1,"after");
 }
 add("leave","hp.closing","entry_ref",24,48,Json::array({codec("u32"),codec("i64",40)}));
 add("leave","attributes.count","entry_ref",24,4,Json::array({codec("u32")}),Json::array({176,104}));
 add("leave","attributes.slot0","entry_ref",32,20,Json::array({codec("u64"),codec("u64",8),codec("u8",16)}),Json::array({176,104}));
 Json source{{"schema_version",1},{"complete",true},{"address","0x9000"},{"name","owned.raw.hp"},{"profile","owned"},{"abi","win64-gp"},{"arg_count",1},{"arg_mask",1},{"max_bytes",8192},{"max_reads",128},{"globals",Json::array({hex(reinterpret_cast<std::uintptr_t>(memory.clock.data()))})},{"fields",fields},{"required_projections",Json::array()},
 {"hp_filter",{{"receiver_arg",0},{"attribute_index_arg",nullptr},{"max_hp_attribute",0},{"enemy_class",nullptr},{"normal_setter",false}}}};
 auto malformed=source;malformed["fields"][0]["observation"]["values"][1]["offset"]=27;bool rejected=false;try{auto ignored=compile_plan(malformed,0x9000);(void)ignored;}catch(const std::exception&){rejected=true;}assert(rejected);
 return compile_plan(source,0x9000);
}
static void capture_point(const CompiledPlan& code,Memory& memory,Snapshot& snapshot,Phase phase=Phase::entry,bool skip_born=false){
 Context context;context.args[0]=memory.root();context.args_valid=1;context.entry_refs=context.args;context.entry_valid=1;context.globals=code.globals;context.globals_valid=code.globals_valid;context.checkpoint=3;
 assert(capture_begin(snapshot,phase,context,8192,128)==Error::ok);
 for(unsigned i=0;i<code.plan->field_count;++i)if(!skip_born||i!=1)capture_append(*code.plan,phase,context,Memory::read,&memory,snapshot,i,i+1);
 capture_finalize(snapshot);
}
static void begin(const CompiledPlan& code,CaptureConfigurations& config,Memory& memory,Snapshot& snapshot,Phase phase=Phase::entry,bool concurrent=false,std::uint64_t mode=5){
 poisoned=false;assert(owned::begin(snapshot.segments.data(),snapshot.segment_count,snapshot.fields.data(),snapshot.field_count,snapshot.bytes.data(),snapshot.byte_count,config.roles,17,snapshot.error,concurrent)==Error::ok);
 owned::bind_hp_observation(&code,phase,memory.root(),3,mode);
}
static JSValue observe(JSContext* c,std::uintptr_t root,std::uintptr_t hook=0x9000){JSValue args[]{JS_NewBigUint64(c,hook),JS_NewBigUint64(c,root)};auto result=owned::hp_observation(c,JS_UNDEFINED,2,args);for(auto value:args)JS_FreeValue(c,value);return result;}
static unsigned index(const CompiledPlan& code,const std::string& name,Phase phase=Phase::entry){const auto& fields=code.hp_observations[phase==Phase::entry?0:1];for(unsigned i=0;i<fields.size();++i)if(fields[i].name==name)return i;assert(false);return 0;}
static unsigned at(const CompiledPlan& code,const std::string& name,Phase phase=Phase::entry){unsigned value=5;for(const auto& field:code.hp_observations[phase==Phase::entry?0:1]){if(field.name==name)return value;value+=1+field.values.size();}assert(false);return 0;}
static unsigned integer(JSContext* c,JSValueConst array,unsigned where){auto value=JS_GetPropertyUint32(c,array,where);std::uint32_t result=0;assert(JS_ToUint32(c,&result,value)==0);JS_FreeValue(c,value);return result;}
static void consume(JSContext* c,unsigned slot){JSValue arg=JS_NewUint32(c,slot);auto result=owned::consume_hp_failure(c,JS_UNDEFINED,1,&arg);JS_FreeValue(c,arg);JS_FreeValue(c,result);}
static void witness(Snapshot& snapshot,unsigned role,unsigned id,std::uintptr_t address,const void* bytes,unsigned size){
 const auto first=snapshot.segment_count;const auto offset=snapshot.byte_count;std::memcpy(snapshot.bytes.data()+offset,bytes,size);snapshot.byte_count+=size;
 snapshot.segments[snapshot.segment_count++]={address,static_cast<std::uint16_t>(id),static_cast<std::uint16_t>(role),offset,size,0,3};
 auto& state=snapshot.fields[snapshot.field_count++];state={};state.id=id;state.role=role;state.error=Error::ok;state.checkpoint=3;state.segment_begin=first;state.segment_end=first+1;state.final_segment=first;state.attempted_address=address;state.attempted_size=size;
}
int main(){
 auto* runtime=JS_NewRuntime();auto* c=JS_NewContext(runtime);owned::install(c,[]()noexcept{poisoned=true;});Memory memory;memory.initialize();auto code=plan(memory);CaptureConfigurations config;config.intern(code);assert(code.hp_observations_ready);
 auto snapshot=std::make_unique<Snapshot>();capture_point(code,memory,*snapshot);begin(code,config,memory,*snapshot);
 auto packet=observe(c,memory.root());assert(!JS_IsException(packet)&&!JS_IsUndefined(packet)&&integer(c,packet,4)==5&&!poisoned);
 auto global=JS_GetGlobalObject(c);JS_SetPropertyStr(c,global,"packet",JS_DupValue(c,packet));JS_FreeValue(c,global);
 const auto evaluate=[&](const std::string& text){auto value=JS_Eval(c,text.data(),text.size(),"raw-observation-test.js",JS_EVAL_TYPE_GLOBAL);if(JS_IsException(value)){auto e=JS_GetException(c);const auto* message=JS_ToCString(c,e);std::cerr<<(message?message:"exception")<<"\n";if(message)JS_FreeCString(c,message);JS_FreeValue(c,e);}assert(!JS_IsException(value));const bool result=JS_ToBool(c,value)==1;JS_FreeValue(c,value);assert(result);};
 evaluate("Object.isFrozen(packet)&&packet[6]===18446744073709551615n&&packet[7]===73");
 evaluate("packet["+std::to_string(at(code,"hp.before")+1)+"]===-9223372036854775808n");
 evaluate("(()=>{const s=packet["+std::to_string(at(code,"hp.id")+1)+"];return s.length===9&&[65,0,0x4e2d,0xd800,66,0xdfff,67,0xd83d,0xde00].every((v,i)=>s.charCodeAt(i)===v)})()");
 evaluate("(()=>{'use strict';try{packet[5]=9;return false}catch(e){return packet[5]===0}})()");JS_FreeValue(c,packet);
 // Empty UTF16 remains a real empty string, not a missing value.
 memory.set(memory.string,16,std::int32_t{0});capture_point(code,memory,*snapshot);begin(code,config,memory,*snapshot);packet=observe(c,memory.root());global=JS_GetGlobalObject(c);JS_SetPropertyStr(c,global,"packet",JS_DupValue(c,packet));JS_FreeValue(c,global);evaluate("packet["+std::to_string(at(code,"hp.id")+1)+"]==='' ");JS_FreeValue(c,packet);memory.initialize();capture_point(code,memory,*snapshot);
 // Every slot result, including structural defects, stays deferred until demanded.
 for(unsigned i=0;i<snapshot->segment_count;++i)if(snapshot->segments[i].field_id==code.plan->fields[0].id)snapshot->segments[i].address+=4096;
 for(const bool concurrent:{false,true}){begin(code,config,memory,*snapshot,Phase::entry,concurrent);packet=observe(c,memory.root());assert(integer(c,packet,at(code,"hp.identity"))==9&&!poisoned);consume(c,index(code,"hp.identity"));assert(poisoned&&owned::binding().error==Error::missing_plan_field);JS_FreeValue(c,packet);}
 capture_point(code,memory,*snapshot,Phase::entry,true);begin(code,config,memory,*snapshot);packet=observe(c,memory.root());assert(integer(c,packet,at(code,"hp.born"))==65535&&!poisoned);consume(c,index(code,"hp.born"));assert(poisoned);JS_FreeValue(c,packet);
 // An absent nominal field is not authority to reject independent auto evidence.
 capture_point(code,memory,*snapshot,Phase::entry,true);const std::uint8_t one=1;witness(*snapshot,code.plan->fields[1].role,103,memory.root()+37,&one,1);begin(code,config,memory,*snapshot);const auto old_auto=owned::binding().view.read(code.plan->fields[1].role,3,std::nullopt,memory.root()+37,1);assert(old_auto.error==Error::ok&&*old_auto.data==std::byte{1});packet=observe(c,memory.root());assert(integer(c,packet,at(code,"hp.born"))==0&&integer(c,packet,at(code,"hp.born")+1)==1&&!poisoned);JS_FreeValue(c,packet);
 // Independent successful auto evidence can supply a failed primary range.
 capture_point(code,memory,*snapshot);for(unsigned i=0;i<snapshot->field_count;++i)if(snapshot->fields[i].id==code.plan->fields[0].id)snapshot->fields[i].error=Error::field_unreadable;
 witness(*snapshot,code.plan->fields[0].role,100,memory.root(),memory.entity.data(),28);begin(code,config,memory,*snapshot);packet=observe(c,memory.root());assert(integer(c,packet,at(code,"hp.identity"))==0&&!poisoned);JS_FreeValue(c,packet);
 // The separate trap eight-byte read must still detect smaller overlap conflict.
 capture_point(code,memory,*snapshot);const std::uint64_t other=7;witness(*snapshot,code.plan->fields[0].role,101,memory.root(),&other,8);begin(code,config,memory,*snapshot);packet=observe(c,memory.root());assert(integer(c,packet,at(code,"hp.identity"))==0&&!poisoned);JS_FreeValue(c,packet);
 JSValue args[]{JS_NewString(c,"body"),JS_NewUint32(c,3),JS_NewInt32(c,-1),JS_NewBigUint64(c,memory.root()),JS_NewUint32(c,4)};auto conflicting=owned::scalar(c,JS_UNDEFINED,5,args);for(auto arg:args)JS_FreeValue(c,arg);assert(JS_IsUndefined(conflicting)&&poisoned);JS_FreeValue(c,conflicting);
 // Planned guard failure has original unique-point priority, even far from read.
 capture_point(code,memory,*snapshot);for(unsigned i=0;i<snapshot->field_count;++i)if(snapshot->fields[i].id==code.plan->fields[4].id){snapshot->fields[i].error=Error::changed_guard;snapshot->fields[i].attempted_address=1;snapshot->fields[i].attempted_size=1;}
 begin(code,config,memory,*snapshot);packet=observe(c,memory.root());assert(integer(c,packet,at(code,"clock_open"))==8&&!poisoned);consume(c,index(code,"clock_open"));assert(!poisoned&&owned::binding().error==Error::changed_guard);JS_FreeValue(c,packet);
 capture_point(code,memory,*snapshot);for(unsigned i=0;i<snapshot->field_count;++i)if(snapshot->fields[i].id==code.plan->fields[4].id){snapshot->fields[i].error=Error::field_unreadable;snapshot->fields[i].attempted_address=1;snapshot->fields[i].attempted_size=1;}
 begin(code,config,memory,*snapshot,Phase::entry,true);packet=observe(c,memory.root());assert(integer(c,packet,at(code,"clock_open"))==5&&!poisoned);consume(c,index(code,"clock_open"));assert(!poisoned&&owned::binding().error==Error::field_unreadable);JS_FreeValue(c,packet);
 // The installed contract is immutable across mode epochs; the header pin owns mode.
 capture_point(code,memory,*snapshot);begin(code,config,memory,*snapshot,Phase::entry,false,0);packet=observe(c,memory.root());assert(integer(c,packet,4)==0&&!poisoned);JS_FreeValue(c,packet);
 // Nullable attributes remain truthful missing_root, never invented zero/null.
 memory.set(memory.entity,176,std::uintptr_t{0});capture_point(code,memory,*snapshot,Phase::leave);begin(code,config,memory,*snapshot,Phase::leave);packet=observe(c,memory.root());assert(integer(c,packet,at(code,"attributes.count",Phase::leave))==4&&!poisoned);consume(c,index(code,"attributes.count",Phase::leave));assert(!poisoned&&owned::binding().error==Error::missing_root);JS_FreeValue(c,packet);memory.initialize();
 capture_point(code,memory,*snapshot);begin(code,config,memory,*snapshot);packet=observe(c,memory.root()+1);assert(JS_IsUndefined(packet)&&poisoned);JS_FreeValue(c,packet);
 begin(code,config,memory,*snapshot);packet=observe(c,memory.root(),0x9001);assert(JS_IsUndefined(packet)&&poisoned);JS_FreeValue(c,packet);
 begin(code,config,memory,*snapshot);owned::end();packet=observe(c,memory.root());assert(JS_IsUndefined(packet)&&poisoned);JS_FreeValue(c,packet);
 JS_FreeContext(c);JS_FreeRuntime(runtime);std::cout<<"PASS_RAW_HP_OBSERVATION actual compiled fields/capture/view/primitive UTF16/deferred failures/auto overlap/unique/lease/mode\n";
}
