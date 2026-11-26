#include "owned_replay_js.h"
#include <cassert>
#include <iostream>
using namespace arkdps::capture;namespace owned=arkdps::owned_js;using Json=nlohmann::json;
static bool poisoned=false;static std::vector<std::string> roles{"body","clock"};
static std::array<std::byte,256> payload;static std::array<Segment,5> segments;static std::array<FieldState,2> fields;
static void fixture(Error body=Error::ok,Error clock=Error::ok,bool concurrent=false){
 payload={};std::uint32_t uid=73,frame=30;std::uint64_t string=0x2000,klass=0x5000,fixed=std::uint64_t{1}<<32;
 std::int32_t count=6;const std::uint16_t text[]{65,0,0x4e2d,0xd800,0xd83d,0xde00};
 std::memcpy(payload.data()+24,&uid,4);std::memcpy(payload.data()+128,&string,8);
 std::memcpy(payload.data()+136,&klass,8);std::memcpy(payload.data()+144,&count,4);std::memcpy(payload.data()+148,text,sizeof text);
 std::memcpy(payload.data()+160,&frame,4);std::memcpy(payload.data()+164,&fixed,8);
 segments={Segment{0x1000,7,1,0,136,0,3},Segment{0x2000,7,1,136,8,0,3},Segment{0x2010,7,1,144,4,0,3},Segment{0x2014,7,1,148,12,0,3},Segment{0x3000,8,2,160,12,1,3}};
 fields={};fields[0].id=7;fields[0].role=1;fields[0].checkpoint=3;fields[0].error=body;fields[0].segment_end=4;fields[0].final_segment=0;fields[0].attempted_address=0x1000;fields[0].attempted_size=136;
 fields[1].id=8;fields[1].role=2;fields[1].checkpoint=3;fields[1].error=clock;fields[1].segment_begin=4;fields[1].segment_end=5;fields[1].final_segment=4;fields[1].guard_id=1;fields[1].guard=Guard::member;
 fields[1].attempted_address=0x9000;fields[1].attempted_size=1;poisoned=false;
 assert(owned::begin(segments.data(),segments.size(),fields.data(),fields.size(),payload.data(),172,roles,17,Error::ok,concurrent)==Error::ok);
}
static unsigned reg(JSContext* c,const Json& spec,unsigned metadata=1,bool reject=false){
 const auto text=spec.dump();JSValue args[]{JS_NewUint32(c,metadata),JS_ParseJSON(c,text.data(),text.size(),"owned-plan.json")};
 auto value=owned::register_read_plan(c,JS_UNDEFINED,2,args);for(auto arg:args)JS_FreeValue(c,arg);
 if(reject){assert(JS_IsException(value));auto error=JS_GetException(c);JS_FreeValue(c,error);return 0;}
 assert(!JS_IsException(value));std::uint32_t id=0;assert(JS_ToUint32(c,&id,value)==0);JS_FreeValue(c,value);return id;
}
static JSValue batch(JSContext* c,unsigned id,unsigned mask,unsigned metadata=1,std::uintptr_t base=0x1000,unsigned cp=3){
 JSValue args[]{JS_NewUint32(c,id),JS_NewUint32(c,metadata),JS_NewBigUint64(c,base),JS_NewUint32(c,mask),JS_NewUint32(c,cp)};
 auto value=owned::read_batch(c,JS_UNDEFINED,5,args);for(auto arg:args)JS_FreeValue(c,arg);return value;
}
template<class T>static T number(const std::uint8_t* p,unsigned at){T value;std::memcpy(&value,p+at,sizeof value);return value;}
int main(){
 auto* rt=JS_NewRuntime();auto* c=JS_NewContext(rt);owned::install(c,[]() noexcept{poisoned=true;});
 const Json spec{{"version",1},{"fields",Json::array({
  {{"tier",1},{"role","body"},{"checkpoint","current"},{"field_id",-1},{"root","arg0"},{"bytes",28}},
  {{"tier",2},{"role","clock"},{"checkpoint","current"},{"field_id","unique"},{"root","absolute"},{"absolute_address","0x3000"},{"bytes",12}},
  {{"tier",4},{"role","body"},{"checkpoint","current"},{"field_id",-1},{"root","arg0"},{"pointer_offsets",Json::array({128})},{"kind","utf16"},{"max_units",200}},
  {{"tier",8},{"role","body"},{"checkpoint",4},{"field_id",-1},{"root","arg0"},{"bytes",8}}
 })}};
 auto invalid=spec;invalid["fields"][0]["bytes"]=129;reg(c,invalid,1,true);
 invalid=spec;invalid["fields"][0]["tier"]=3;reg(c,invalid,1,true);
 invalid=spec;invalid["fields"][0]["pointer_offsets"]=Json::array();for(int i=0;i<13;i++)invalid["fields"][0]["pointer_offsets"].push_back(0);reg(c,invalid,1,true);
 const auto id=reg(c,spec);auto wrong=spec;wrong["fields"][1]["absolute_address"]="0x7000";const auto distant=reg(c,wrong);
 owned::seal_read_plans();reg(c,spec,1,true); // Host journal cannot register after setup either.
 fixture();reg(c,spec,1,true);assert(poisoned);fixture();
 auto value=batch(c,id,7);std::size_t size=0;auto* bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint32_t>(bytes,0)==1&&number<std::uint32_t>(bytes,4)==4&&number<std::uint32_t>(bytes,8)==7);
 assert(number<std::uint16_t>(bytes,16)==0&&number<std::uint16_t>(bytes,32)==0&&number<std::uint16_t>(bytes,48)==0&&number<std::uint16_t>(bytes,64)==65535);
 const auto identity=number<std::uint32_t>(bytes,20),clock=number<std::uint32_t>(bytes,36),string=number<std::uint32_t>(bytes,52);
 assert(number<std::uint32_t>(bytes,identity+24)==73&&number<std::uint32_t>(bytes,clock)==30);
 assert(number<std::uint64_t>(bytes,string)==0x5000&&number<std::int32_t>(bytes,string+8)==6);
 const std::uint16_t expected[]{65,0,0x4e2d,0xd800,0xd83d,0xde00};assert(std::memcmp(bytes+string+12,expected,sizeof expected)==0);
 // Returned AB is independent of the native view and remains stable later.
 payload[24]=std::byte{0};assert(number<std::uint32_t>(bytes,identity+24)==73);JS_FreeValue(c,value);
 for(auto mask:{0u,16u}){fixture();value=batch(c,id,mask);assert(JS_IsUndefined(value)&&poisoned);JS_FreeValue(c,value);}
 fixture();value=batch(c,id,1,2);assert(JS_IsUndefined(value)&&poisoned);JS_FreeValue(c,value);
 fixture();value=batch(c,id,8);assert(JS_IsUndefined(value)&&owned::binding().error==Error::missing_plan_field&&poisoned);JS_FreeValue(c,value);
 fixture();value=batch(c,id,1,1,0x7000);assert(JS_IsUndefined(value)&&poisoned);JS_FreeValue(c,value);
 for(auto status:{Error::field_unreadable,Error::changed_guard}){
  fixture(Error::ok,status);value=batch(c,distant,2);bytes=JS_GetArrayBuffer(c,&size,value);
  assert(bytes&&number<std::uint16_t>(bytes,32)==static_cast<unsigned>(status)&&number<std::uint32_t>(bytes,36)==0&&number<std::uint32_t>(bytes,40)==0&&!poisoned);JS_FreeValue(c,value);
 }
 fixture(Error::ok,Error::ok,true);value=batch(c,distant,2);bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint16_t>(bytes,32)==8&&!poisoned);JS_FreeValue(c,value);
 fixture(Error::ok,Error::ok,true);value=batch(c,id,1,1,0x7000);assert(JS_IsUndefined(value)&&owned::binding().error==Error::missing_plan_field&&poisoned);JS_FreeValue(c,value);
 fixture(Error::field_unreadable);value=batch(c,id,1);bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint16_t>(bytes,16)==5&&!poisoned);JS_FreeValue(c,value);
 fixture();owned::end();value=batch(c,id,1);assert(JS_IsUndefined(value)&&poisoned);JS_FreeValue(c,value);
 JS_FreeContext(c);JS_FreeRuntime(rt);std::cout<<"PASS owned read plans: raw tiers/UTF16/immutable ABI/metadata/seal/guard priority/concurrent/range/lease\n";
}
