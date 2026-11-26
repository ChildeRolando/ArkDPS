#include "engine_capture_pipeline.h"
#include <cassert>
#include <iostream>
using namespace arkdps::capture;
using J=nlohmann::json;
struct Fixture {
 alignas(8) std::array<std::byte,0x180> object{};
 struct String {std::uint64_t klass=0x3000,monitor=0;std::int32_t length=0;char16_t text[64]{};} id;
 std::array<std::byte,12> clock{};
 unsigned reads=0;bool unreadable=false;
 Fixture(){std::uintptr_t text=reinterpret_cast<std::uintptr_t>(&id);std::memcpy(object.data()+0x148,&text,8);set_class(0x2000);std::uint32_t uid=7;std::memcpy(object.data()+0x18,&uid,4);object[0x25]=std::byte{1};set_hp(100ull<<32);set_id(u"char_test");}
 void set_class(std::uintptr_t value){std::memcpy(object.data(),&value,8);}
 void set_hp(std::uint64_t value){std::memcpy(object.data()+0x40,&value,8);}
 void set_id(std::u16string_view value){id.length=value.size();std::memcpy(id.text,value.data(),value.size()*2);}
 static bool read(void* user,std::uintptr_t address,void* out,std::size_t size) noexcept {
  auto& f=*static_cast<Fixture*>(user);++f.reads;
  const auto text=reinterpret_cast<std::uintptr_t>(f.id.text);
  if(f.unreadable && address>=text && address<text+sizeof f.id.text)return false;
  std::memcpy(out,reinterpret_cast<const void*>(address),size);return true;
 }
};
J recipe(Fixture& f){
 J fields=J::array();unsigned id=0;
 auto add=[&](const char* phase,const char* name,const char* root,unsigned offset,unsigned bytes,const char* role="body",const char* side="none",unsigned guard=0){
  fields.push_back({{"field_id",++id},{"phase",phase},{"role",role},{"name",name},{"root",root},{"root_index",0},{"pointer_offsets",J::array()},{"offset",offset},{"bytes",bytes},{"kind","bytes"},{"guard_id",guard},{"guard_side",side},{"required",true}});
 };
 for(const auto* phase:{"entry","leave"}){add(phase,"clock_open","global_slot",0,12,"clock_open","before",1);add(phase,"clock_close","global_slot",0,12,"clock_close","after",1);}
 add("entry","hp.identity","arg",0,0x1c);add("entry","hp.born","arg",0x25,1);add("entry","hp.before","arg",0x40,8);
 add("entry","hp.id","arg",0,0);auto& text=fields.back();text["pointer_offsets"]={0x148};text["kind"]="utf16";text["expected_class"]="0x3000";text["max_units"]=64;
 add("leave","hp.closing","entry_ref",0x18,0x30);add("leave","attributes.count","entry_ref",0x60,4);fields.back()["tier"]="changed";
 return {{"schema_version",1},{"complete",true},{"name","owned"},{"profile","owned"},{"address","1"},{"abi","win64-gp"},{"arg_count",3},{"arg_mask",7},{"globals",J::array({std::to_string(reinterpret_cast<std::uintptr_t>(f.clock.data()))})},{"fields",fields},{"max_bytes",65536},{"max_reads",4096},{"required_projections",J::array()},
 {"hp_filter",{{"receiver_arg",0},{"attribute_index_arg",nullptr},{"max_hp_attribute",0},{"enemy_class","0x1000"},{"normal_setter",true},{"defer_entry_scope",true},{"hp_non_enemy_classes",J::array({"0x2000"})}}}};
}
bool has(const Snapshot& s,std::uint16_t id){for(unsigned i=0;i<s.field_count;++i)if(s.fields[i].id==id)return true;return false;}
int main(){
 Fixture f;auto json=recipe(f);auto plan=std::make_unique<CompiledPlan>(compile_plan(json,1));
 auto config=std::make_unique<CaptureConfigurations>();config->intern(*plan);
 auto lane=std::make_unique<Lane>();lane->depth=1;Context ctx{};ctx.args_valid=7;ctx.args[0]=reinterpret_cast<std::uintptr_t>(f.object.data());ctx.entry_refs=ctx.args;ctx.entry_valid=7;ctx.globals=plan->globals;ctx.globals_valid=plan->globals_valid;RecordHeader header;
 const auto entry=[&]{header={};f.reads=0;capture_scope_point(*plan,Phase::entry,ctx,nullptr,Fixture::read,&f,lane->snapshot,header,lane.get());};
 entry();assert(lane->calls[0].hp_ignored && lane->snapshot.field_count==2 && has(lane->snapshot,5)&&has(lane->snapshot,8));
 assert(!has(lane->snapshot,6)&&!has(lane->snapshot,7)&&!has(lane->snapshot,1));const auto nontrap_reads=f.reads;
 capture_scope_point(*plan,Phase::leave,ctx,nullptr,Fixture::read,&f,lane->snapshot,header,lane.get());assert(lane->snapshot.field_count==0 && f.reads==nontrap_reads);
 auto default_json=json;default_json["hp_filter"].erase("hp_non_enemy_classes");
 auto default_plan=std::make_unique<CompiledPlan>(compile_plan(default_json,1));config->intern(*default_plan);header={};
 capture_scope_point(*default_plan,Phase::entry,ctx,nullptr,Fixture::read,&f,lane->snapshot,header,lane.get());
 assert(!lane->calls[0].hp_ignored&&has(lane->snapshot,1));
 entry();assert(lane->calls[0].hp_ignored);
 auto non_hp_json=json;non_hp_json.erase("hp_filter");
 auto non_hp=std::make_unique<CompiledPlan>(compile_plan(non_hp_json,1));config->intern(*non_hp);header={};
 capture_scope_point(*non_hp,Phase::entry,ctx,nullptr,Fixture::read,&f,lane->snapshot,header,lane.get());
 assert(!lane->calls[0].hp_ignored); // Same stack slot now belongs to a normal hook and can claim publication.
 f.set_id(u"trap_fire");entry();assert(!lane->calls[0].hp_ignored && has(lane->snapshot,1)&&has(lane->snapshot,7)&&lane->snapshot.error==Error::ok);
 f.set_id(u"char_test");f.set_class(0x2001);entry();assert(!lane->calls[0].hp_ignored&&has(lane->snapshot,1));
 f.set_class(0x2000);f.unreadable=true;entry();assert(!lane->calls[0].hp_ignored&&has(lane->snapshot,1)&&lane->snapshot.error!=Error::invalid_plan);f.unreadable=false;
 f.id.klass=0x3001;entry();assert(!lane->calls[0].hp_ignored&&has(lane->snapshot,1));f.id.klass=0x3000;
 f.set_class(0x1000);entry();assert(!lane->calls[0].hp_ignored&&lane->calls[0].hp_before_valid);f.set_hp(101ull<<32);
 capture_scope_point(*plan,Phase::leave,ctx,nullptr,Fixture::read,&f,lane->snapshot,header,lane.get());assert(has(lane->snapshot,10));
 auto dirty_json=json;dirty_json["hp_filter"]["attribute_index_arg"]=1;dirty_json["hp_filter"]["normal_setter"]=false;dirty_json["hp_filter"]["defer_entry_scope"]=false;
 auto dirty=std::make_unique<CompiledPlan>(compile_plan(dirty_json,1));config->intern(*dirty);f.set_class(0x2000);lane->depth=2;ctx.args[1]=0;lane->calls[0].hp_nested_dirty=false;header={};
 capture_scope_point(*dirty,Phase::entry,ctx,nullptr,Fixture::read,&f,lane->snapshot,header,lane.get());assert(lane->calls[0].hp_nested_dirty && lane->calls[1].hp_ignored);
 lane->depth=1;f.set_class(0x1000);header={};
 capture_scope_point(*dirty,Phase::entry,ctx,nullptr,Fixture::read,&f,lane->snapshot,header,lane.get());
 assert(!lane->calls[0].hp_ignored);
 capture_scope_point(*dirty,Phase::leave,ctx,nullptr,Fixture::read,&f,lane->snapshot,header,lane.get());assert(has(lane->snapshot,10));
 unsigned rejects=0;auto reject=[&](J bad){bool failed=false;try{(void)compile_plan(bad,1);}catch(...){failed=true;}assert(failed);++rejects;};
 for(const auto& classes:{J::array({"0"}),J::array({"0x1000"}),J::array({"0x2000","8192"}),J("wrong"),J(false)}){auto bad=json;bad["hp_filter"]["hp_non_enemy_classes"]=classes;reject(bad);}
 auto bad=json;bad["hp_filter"]["hp_non_enemy_classes"]=J::array();for(unsigned i=0;i<17;++i)bad["hp_filter"]["hp_non_enemy_classes"].push_back(std::to_string(0x4000+i));reject(bad);
 bad=json;bad["fields"][7]["kind"]="bytes";bad["fields"][7]["bytes"]=8;reject(bad);
 bad=json;bad["hp_filter"].erase("hp_non_enemy_classes");assert(compile_plan(bad,1).hp_non_enemy_count==0);
 std::cout<<"PASS exact nonEnemy HP: Character/nontrap minimal, trap/unknown/unreadable/type mismatch full, first HP change, nested MaxHP, "<<rejects<<" strict rejects\n";
}
