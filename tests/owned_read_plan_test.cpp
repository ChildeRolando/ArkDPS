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
template<class T>static T number(const std::uint8_t* p,unsigned at){T value;std::memcpy(&value,p+at,sizeof value);return value;}
static void graph_contract(JSContext* c){
 CaptureConfigurations configurations;Json specs=Json::array();
 for(unsigned i=0;i<33;++i)specs.push_back({{"field_id",i+1},{"phase","checkpoint"},{"role","scope."+std::to_string(i)},
  {"root","absolute"},{"absolute_address","0x"+std::string(i?"1000":"2000")},{"pointer_offsets",Json::array()},
  {"offset",i?i*4:0},{"bytes",4},{"kind","bytes"},{"required",true},{"guard_id",0},{"guard_side","none"}});
 Json graph{{"version",1},{"revision",1},{"mode_epoch",1},{"available",true},{"raw_collection","all"},{"body_insertion_index",33},{"fields",specs}};
 for(unsigned which=0;which<3;++which){auto invalid=graph;
  if(which==0){invalid["fields"][0]["pointer_offsets"]=Json::array();for(unsigned i=0;i<=max_depth;++i)invalid["fields"][0]["pointer_offsets"].push_back(0);}
  if(which==1)invalid["fields"][0]["field_id"]=0;
  if(which==2)invalid["raw_collection"]="local";
  bool rejected=false;try{configurations.publish(invalid,false);}catch(const std::exception&){rejected=true;}assert(rejected);
 }
 configurations.publish(graph,false);const auto* config=configurations.pin(1);assert(config&&config->scope->read_chunks.size()==2);
 std::vector<Segment> ss;std::vector<FieldState> fs;std::array<std::byte,132> data{};
 for(unsigned i=0;i<33;++i){
  const auto& op=config->scope->code->plan->fields[i];const std::uint32_t value=100+i;std::memcpy(data.data()+i*4,&value,4);
  ss.push_back(Segment{op.absolute_address+op.offset,op.id,op.role,i*4,4,0,0});FieldState state{};
  state.error=Error::ok;state.id=op.id;state.role=op.role;state.segment_begin=i;state.segment_end=i+1;state.final_segment=i;
  state.attempted_address=op.absolute_address+op.offset;state.attempted_size=4;fs.push_back(state);
 }
 const auto start=[&](bool concurrent=false){poisoned=false;assert(owned::begin(ss.data(),ss.size(),fs.data(),fs.size(),data.data(),data.size(),configurations.roles,99,Error::ok,concurrent)==Error::ok);owned::bind_graphs(config,1,0,1);};
 const auto run=[&](unsigned chunk,unsigned revision=1,unsigned mode=1){JSValue args[]{JS_NewString(c,"scope"),JS_NewUint32(c,revision),JS_NewUint32(c,mode),JS_NewUint32(c,chunk)};auto result=owned::graph_read_batch(c,JS_UNDEFINED,4,args);for(auto arg:args)JS_FreeValue(c,arg);return result;};
 start();auto value=run(0);std::size_t size=0;auto* bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint32_t>(bytes,4)==32);assert(number<std::uint32_t>(bytes,number<std::uint32_t>(bytes,20))==100);JS_FreeValue(c,value);
 value=run(1);bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint32_t>(bytes,4)==1&&number<std::uint32_t>(bytes,number<std::uint32_t>(bytes,20))==132);JS_FreeValue(c,value);
 // Original unique Witness concurrency wins before range interpretation.
 start(true);value=run(0);bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint16_t>(bytes,16)==8&&!poisoned);JS_FreeValue(c,value);
 ss[0].field_id=9000;fs[0].id=9000;start();value=run(0);assert(JS_IsUndefined(value)&&poisoned&&owned::binding().error==Error::missing_plan_field);JS_FreeValue(c,value);ss[0].field_id=4096;fs[0].id=4096;
 // A cp3 record cannot borrow otherwise valid cp0 graph evidence.
 start();owned::bind_graphs(config,1,0,1,3);value=run(0);assert(JS_IsUndefined(value)&&owned::binding().error==Error::missing_plan_field&&poisoned);JS_FreeValue(c,value);
 for(auto& segment:ss)segment.checkpoint=3;
 for(auto& state:fs)state.checkpoint=3;
 start();owned::bind_graphs(config,1,0,1,3);value=run(0);bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint32_t>(bytes,number<std::uint32_t>(bytes,20))==100&&!poisoned);JS_FreeValue(c,value);
 for(auto& segment:ss)segment.checkpoint=0;
 for(auto& state:fs)state.checkpoint=0;
 for(auto bad:{2u,3u}){start();value=run(0,bad);assert(JS_IsUndefined(value)&&poisoned);JS_FreeValue(c,value);}
 start();value=run(0,1,2);assert(JS_IsUndefined(value)&&poisoned);JS_FreeValue(c,value);
 fs[0].attempted_address=0x9000;fs[0].attempted_size=1;
 fs[0].error=Error::changed_guard;start();value=run(0);bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint16_t>(bytes,16)==8&&!poisoned);JS_FreeValue(c,value);
 // Original Witness rejects multiple fields before any geometry lookup.
 auto extra=fs[0];extra.id=9000;extra.error=Error::field_unreadable;extra.segment_begin=33;extra.segment_end=33;extra.final_segment=0xffff;fs.push_back(extra);
 start();value=run(0);assert(JS_IsUndefined(value)&&owned::binding().error==Error::ambiguous_read&&poisoned);JS_FreeValue(c,value);fs.pop_back();
 // Eager batch cannot hide a later genuine structural missing range behind a planned guard failure.
 ss[1].address+=1024;start();value=run(0);assert(JS_IsUndefined(value)&&poisoned);JS_FreeValue(c,value);ss[1].address-=1024;fs[0].error=Error::ok;
 owned::end();value=run(0);assert(JS_IsUndefined(value)&&poisoned);JS_FreeValue(c,value);
 // Static predicates share the same graph and reader; only field zero is dynamic.
 auto checked=graph;checked["revision"]=2;
 for(unsigned i=0;i<33;++i){checked["fields"][i]["projection"]=i==0;if(i)checked["fields"][i]["comparison"]={{"version",1},{"words",Json::array({{{"offset",0},{"kind",i==1?"u8":"u32"},{"value",100+i}}})}};}
 checked["fields"][0]["observation"]={{"values",Json::array({{{"kind","u32"},{"offset",0}},{{"kind","i32"},{"offset",0}}})}};
 configurations.publish(checked,false);const auto* check_config=configurations.pin(1);assert(check_config->scope->check_ready);
 const auto check_run=[&](){start();owned::bind_graphs(check_config,2,0,1);JSValue args[]{JS_NewString(c,"scope"),JS_NewUint32(c,2),JS_NewUint32(c,1)};auto result=owned::graph_check(c,JS_UNDEFINED,3,args);for(auto arg:args)JS_FreeValue(c,arg);return result;};
 value=check_run();bytes=JS_GetArrayBuffer(c,&size,value);assert(bytes&&number<std::uint32_t>(bytes,4)==1&&number<std::uint32_t>(bytes,number<std::uint32_t>(bytes,20))==100);JS_FreeValue(c,value);
 // Same compiled graph/check executor, primitive mode preserves signed i32.
 assert(check_config->scope->primitive_ready);
 start();owned::bind_graphs(check_config,2,0,1);JSValue typedArgs[]{JS_NewString(c,"scope"),JS_NewUint32(c,2),JS_NewUint32(c,1),JS_NewUint32(c,1)};
 value=owned::graph_check(c,JS_UNDEFINED,4,typedArgs);assert(JS_IsArray(c,value)==1&&!poisoned);
 auto got=JS_GetPropertyUint32(c,value,5);std::uint32_t actual=0;assert(JS_ToUint32(c,&actual,got)==0&&actual==100);JS_FreeValue(c,got);JS_FreeValue(c,value);
 std::memset(data.data(),255,4);start();owned::bind_graphs(check_config,2,0,1);value=owned::graph_check(c,JS_UNDEFINED,4,typedArgs);got=JS_GetPropertyUint32(c,value,6);std::int32_t signed_value=0;assert(JS_ToInt32(c,&signed_value,got)==0&&signed_value==-1);JS_FreeValue(c,got);JS_FreeValue(c,value);std::uint32_t restored=100;std::memcpy(data.data(),&restored,4);
 for(auto arg:typedArgs)JS_FreeValue(c,arg);
 data[4]=std::byte{1};value=check_run();assert(JS_IsUndefined(value)&&owned::binding().error==Error::changed_guard&&owned::binding().graph_failure==1&&!poisoned);JS_FreeValue(c,value);data[4]=std::byte{101};
 fs[1].error=Error::field_unreadable;value=check_run();assert(JS_IsUndefined(value)&&owned::binding().error==Error::field_unreadable&&!poisoned);JS_FreeValue(c,value);fs[1].error=Error::ok;
 // Later structural failures override earlier planned comparison uncertainty.
 data[4]=std::byte{1};ss[2].address+=1000;value=check_run();assert(JS_IsUndefined(value)&&poisoned&&owned::binding().graph_failure==2);JS_FreeValue(c,value);ss[2].address-=1000;data[4]=std::byte{101};
 owned::end();
 // High-bit unsigned and signed projections remain exact BigInts, not doubles.
 auto wide=checked;wide["revision"]=3;wide["fields"][0]["bytes"]=8;
 wide["fields"][0]["observation"]={{"values",Json::array({{{"kind","u64"},{"offset",0}},{{"kind","i64"},{"offset",0}}})}};
 wide["fields"][1]["projection"]=true;wide["fields"][1].erase("comparison");wide["fields"][1]["observation"]={{"values",Json::array({{{"kind","i32"},{"offset",0}}})}};
 configurations.publish(wide,false);const auto* wide_config=configurations.pin(1);assert(wide_config->scope->primitive_ready);
 const std::uint64_t maximum=UINT64_MAX;std::memcpy(data.data(),&maximum,8);ss[0].size=8;
 start();owned::bind_graphs(wide_config,3,0,1);JSValue wideArgs[]{JS_NewString(c,"scope"),JS_NewUint32(c,3),JS_NewUint32(c,1),JS_NewUint32(c,1)};
 value=owned::graph_check(c,JS_UNDEFINED,4,wideArgs);assert(JS_IsArray(c,value)==1&&!poisoned);
 auto unsigned_big=JS_GetPropertyUint32(c,value,5),signed_big=JS_GetPropertyUint32(c,value,6);std::int64_t signed_bigint=0;
 assert(JS_ToBigInt64(c,&signed_bigint,signed_big)==0&&signed_bigint==-1);const auto* exact=JS_ToCString(c,unsigned_big);assert(exact&&std::string(exact)=="18446744073709551615");JS_FreeCString(c,exact);
 JS_FreeValue(c,unsigned_big);JS_FreeValue(c,signed_big);JS_FreeValue(c,value);for(auto arg:wideArgs)JS_FreeValue(c,arg);ss[0].size=4;
 const std::uint32_t original_data[]{100,101};std::memcpy(data.data(),original_data,8);owned::end();
 // Batch geometry cannot narrow the capture contract: a legal 129-byte field
 // still publishes, but its optimization program is unavailable.
 auto unsupported=checked;unsupported["revision"]=4;unsupported["fields"][0]["comparison"]=nullptr;unsupported["fields"][32]["comparison"]="unreached unsupported metadata";
 configurations.publish(unsupported,false);assert(!configurations.pin(1)->scope->check_ready);
 auto large=graph;large["revision"]=5;large["fields"][0]["bytes"]=129;
 configurations.publish(large,false);const auto* large_config=configurations.pin(1);
 assert(large_config&&large_config->scope->code&&large_config->scope->read_chunks.empty());
 assert(configurations.find(config->id)==config);
 const auto old_id=config->id;configurations.collect_quiescent(1);assert(configurations.find(old_id)==nullptr);
 assert(configurations.find(large_config->id)==large_config);

}
static void string_number_graph(JSContext* c){
 CaptureConfigurations configs;
 const Json word={{"offset",0},{"kind","u64_number"},{"value",0}};
 Json field{{"field_id",1},{"phase","checkpoint"},{"role","body"},{"root","absolute"},{"absolute_address","0x1000"},{"pointer_offsets",Json::array({128})},
  {"offset",0},{"bytes",0},{"kind","utf16"},{"max_units",200},{"expected_class","0x5000"},{"required",true},{"guard_id",0},{"guard_side","none"},
  {"projection",false},{"comparison",{{"version",1},{"words",Json::array()},{"utf16",Json::array({65,0,0x4e2d,0xd800,0xd83d,0xde00})}}}};
 Json graph{{"version",1},{"revision",1},{"mode_epoch",1},{"available",true},{"raw_collection","all"},{"body_insertion_index",1},{"fields",Json::array({field})}};
 configs.publish(graph,false);
 const auto run=[&](const CaptureConfiguration* config,bool alter=false,std::uint64_t integer=0,int length=-1){
  fixture();if(alter)payload[150]=std::byte{7};else if(config->scope->revision>1)std::memcpy(payload.data(),&integer,8);
  if(length>=0)std::memcpy(payload.data()+144,&length,4);
  const auto& op=config->scope->code->plan->fields[0];
  for(unsigned i=0;i<4;++i){segments[i].field_id=op.id;segments[i].role=op.role;}
  fields[0].id=op.id;fields[0].role=op.role;
  assert(owned::begin(segments.data(),4,fields.data(),1,payload.data(),172,configs.roles,17,Error::ok)==Error::ok);
  owned::bind_graphs(config,config->scope->revision,0,1,3);
  JSValue args[]{JS_NewString(c,"scope"),JS_NewFloat64(c,config->scope->revision),JS_NewUint32(c,1)};
  auto value=owned::graph_check(c,JS_UNDEFINED,3,args);for(auto arg:args)JS_FreeValue(c,arg);return value;
 };
 auto value=run(configs.pin(1));std::size_t size=0;assert(JS_GetArrayBuffer(c,&size,value)&&size==16);JS_FreeValue(c,value);
 value=run(configs.pin(1),true);assert(JS_IsUndefined(value)&&owned::binding().error==Error::changed_guard&&!poisoned);JS_FreeValue(c,value);
 graph["revision"]=2;graph["fields"][0]["comparison"]["utf16"]=Json::array();configs.publish(graph,false);
 value=run(configs.pin(1),false,0,0);assert(!JS_IsUndefined(value));JS_FreeValue(c,value);
 const std::uint64_t integers[]{0,std::uint64_t{1}<<53,(std::uint64_t{1}<<53)+1,(std::uint64_t{1}<<53)+3,(std::uint64_t{1}<<63)+1,UINT64_MAX};
 const double expected[]{0,9007199254740992.0,9007199254740992.0,9007199254740996.0,9223372036854775808.0,18446744073709551616.0};
 graph["fields"][0]["kind"]="bytes";graph["fields"][0]["bytes"]=8;graph["fields"][0]["absolute_address"]="0x1000";graph["fields"][0]["pointer_offsets"]=Json::array();graph["fields"][0].erase("max_units");graph["fields"][0].erase("expected_class");
 for(unsigned i=0;i<6;++i){
  const auto expression="Number("+std::to_string(integers[i])+"n)";auto result=JS_Eval(c,expression.data(),expression.size(),"number-parity.js",JS_EVAL_TYPE_GLOBAL);double js=0;assert(!JS_IsException(result)&&JS_ToFloat64(c,&js,result)==0&&js==expected[i]);JS_FreeValue(c,result);
  graph["revision"]=i+3;auto predicate=word;predicate["value"]=js;graph["fields"][0]["comparison"]={{"version",1},{"words",Json::array({predicate})}};configs.publish(graph,false);
  value=run(configs.pin(1),false,integers[i]);assert(!JS_IsUndefined(value)&&!poisoned);JS_FreeValue(c,value);
 }
 owned::end();
}
int main(){
 auto* rt=JS_NewRuntime();auto* c=JS_NewContext(rt);owned::install(c,[]() noexcept{poisoned=true;});
 graph_contract(c);string_number_graph(c);
 JS_FreeContext(c);JS_FreeRuntime(rt);std::cout<<"PASS owned graph programs: immutable ABI/guard priority/concurrent/range/lease/predicates\n";
}
