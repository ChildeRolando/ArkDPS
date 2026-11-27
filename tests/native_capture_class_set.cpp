#include "engine_capture_plan.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <cstdlib>
#include <new>
static std::size_t allocations=0;
__attribute__((noinline)) void* operator new(std::size_t n){++allocations;if(auto p=std::malloc(n))return p;throw std::bad_alloc();}
__attribute__((noinline)) void operator delete(void* p)noexcept{std::free(p);}
__attribute__((noinline)) void operator delete(void* p,std::size_t)noexcept{std::free(p);}
__attribute__((noinline)) void* operator new[](std::size_t n){return ::operator new(n);}
__attribute__((noinline)) void operator delete[](void* p)noexcept{std::free(p);}
__attribute__((noinline)) void operator delete[](void* p,std::size_t)noexcept{std::free(p);}
using namespace arkdps::capture;
using J=nlohmann::json;
struct Memory {
 unsigned parents=0,reads=0;bool change_class=false;unsigned class_reads=0; std::uintptr_t klass=0x9000; std::uint32_t data=73;
 static bool read(void* opaque,std::uintptr_t at,void* out,std::size_t size) noexcept {
  auto& m=*static_cast<Memory*>(opaque);++m.reads;
  if(at==0x1000&&size==8){if(m.change_class&&++m.class_reads==2)m.klass=0xa000;std::memcpy(out,&m.klass,8);return true;}
  if(at==0x1008&&size==4){std::memcpy(out,&m.data,4);return true;}
  ++m.parents;return false;
 }
};
int main(){
 J source={{"schema_version",1},{"complete",true},{"abi","win64-gp"},{"address","0x2000"},
 {"arg_count",1},{"arg_mask",1},{"max_bytes",128},{"max_reads",32},{"globals",J::array()},
 {"profile","owned"},{"required_projections",J::array()},
 {"projections",J::array({{{"kind","object"},{"gate","class_set"},{"expected_classes",J::array({"0x9000","0x8000"})},{"child_begin",1},{"child_count",1}},{{"kind","object"},{"offset",8},{"bytes",4}}})},
 {"fields",J::array({{{"field_id",1},{"role","body"},{"phase","entry"},{"root","arg"},{"offset",0},{"bytes",0},{"kind","projection_tree"},{"projection_root",0},{"pointer_offsets",J::array()},{"guard_id",0},{"guard_side","none"},{"required",true}}})}};
 auto compiled=compile_plan(source,0x2000);auto& p=*compiled.plan;
 assert(p.class_set_entries[0]==0x8000&&p.class_set_entries[1]==0x9000);
 // 0x9000 represents a setup-verified descendant beyond ten parents. No
 // parent memory exists here: native must consume only the frozen relation.
 Context c;c.args[0]=0x1000;c.args_valid=1;static Snapshot snapshot;Memory memory;const auto before=allocations;
 assert(capture(p,Phase::entry,c,Memory::read,&memory,snapshot)==Error::ok);
 std::uint32_t output=0;assert(read_snapshot(snapshot,1,0,1,0x1008,&output,4)==Error::ok&&output==73);
 assert(memory.parents==0&&allocations==before);
 // A nonmember is NOT evidence of unrelated identity: klass stays owned
 // for the JS closed-lineage owner; only unsafe child layout is withheld.
 memory.klass=0xa000;memory.reads=0;
 assert(capture(p,Phase::entry,c,Memory::read,&memory,snapshot)==Error::ok);
 assert(read_snapshot(snapshot,1,0,1,0x1008,&output,4)!=Error::ok);assert(memory.parents==0&&allocations==before);
 std::uintptr_t observed=0;assert(read_snapshot(snapshot,1,0,1,0x1000,&observed,8)==Error::ok&&observed==0xa000);
 memory.klass=0x9000;memory.change_class=true;memory.class_reads=0;
 assert(capture(p,Phase::entry,c,Memory::read,&memory,snapshot)==Error::changed_guard);
 assert(read_snapshot(snapshot,1,0,1,0x1008,&output,4)!=Error::ok);memory.change_class=false;
 memory.klass=0;assert(capture(p,Phase::entry,c,Memory::read,&memory,snapshot)==Error::ok);
 auto reject=[&](J bad){bool failed=false;try{(void)compile_plan(bad,0x2000);}catch(...){failed=true;}assert(failed);};
 for(const auto& table:{J::array(),J::array({"0x0"}),J::array({"0x8000","0x8000"}),J::array({7}),J("wrong")}){auto bad=source;bad["projections"][0]["expected_classes"]=table;reject(bad);}
 for(const char* key:{"expected_class","ancestry_depth"}){auto bad=source;bad["projections"][0][key]=key==std::string("expected_class")?J("0x8000"):J(10);reject(bad);}
 {auto bad=source;bad["projections"][0]["gate"]="none";reject(bad);}
 {auto bad=source;bad["projections"][0]["expected_classes"]=J::array();for(unsigned i=1;i<=max_class_set_entries+1;++i)bad["projections"][0]["expected_classes"].push_back(std::to_string(i));reject(bad);}
 p.class_set_entries={0x9000,0x8000};assert(validate_plan(p)==Error::invalid_plan);
 p.class_set_entries={0x8000,0x8000};assert(validate_plan(p)==Error::invalid_plan);
 p.class_set_entries={0x8000,0x9000};p.projections[0].class_set_count=3;assert(validate_plan(p)==Error::invalid_plan);
 std::cout<<"sizes plan="<<sizeof(HookCapturePlan)<<" segment="<<sizeof(Segment)<<" field="<<sizeof(FieldState)<<" snapshot="<<sizeof(Snapshot)<<"\n";
 std::cout<<"PASS class_set: verified deep descendant, unknown/null skip, no parent reads, strict tables\n";
}

