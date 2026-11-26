#include "engine_capture_pipeline.h"
#include <cassert>
#include <iostream>
using namespace arkdps::capture;
using J=nlohmann::json;
struct Reads { unsigned count=0; bool mutate=false; std::uint64_t word=42; };
bool reader(void* p,std::uintptr_t address,void* out,std::size_t size) noexcept {
 auto& r=*static_cast<Reads*>(p); ++r.count;
 if (r.mutate && r.count==2) ++r.word;
 if(address!=reinterpret_cast<std::uintptr_t>(&r.word)||size!=8)return false;
 std::memcpy(out,&r.word,size);return true;
}
int main(){
 auto configs=std::make_unique<CaptureConfigurations>();
 Reads reads; const auto address=std::to_string(reinterpret_cast<std::uintptr_t>(&reads.word));
 auto field=[&](unsigned id,const char* role,const char* phase,const char* side,unsigned guard){
 return J{{"field_id",id},{"role",role},{"phase",phase},{"root","absolute"},{"absolute_address",address},
 {"root_index",0},{"pointer_offsets",J::array()},{"offset",0},{"bytes",8},{"kind","bytes"},{"guard_id",guard},{"guard_side",side},{"required",true}};};
 J plan{{"schema_version",1},{"complete",true},{"address","1"},{"abi","win64-gp"},{"arg_count",0},{"arg_mask",0},
 {"globals",J::array()},{"max_bytes",1024},{"max_reads",32},{"required_projections",J::array()},
 {"fields",J::array({field(1,"clock_open","entry","before",1),field(2,"body","entry","none",0),field(3,"clock_close","entry","after",1)})}};
 auto base=std::make_unique<CompiledPlan>(compile_plan(plan,1)); configs->intern(*base);
 J graph{{"version",1},{"revision",1},{"mode_epoch",0},{"raw_collection","all"},{"available",true},{"body_insertion_index",1},
 {"fields",J::array({field(1,"scope.open","checkpoint","before",1),field(2,"scope.close","checkpoint","after",1)})}};
 configs->publish(graph,false); auto* pinned=configs->pin(0); assert(pinned);
 auto snapshot=std::make_unique<Snapshot>();Context context{};RecordHeader header;
 capture_scope_point(*base,Phase::entry,context,pinned,reader,&reads,*snapshot,header);
 assert(snapshot->error==Error::ok && snapshot->field_count==5 && reads.count==5);
 assert(header.scope_revision==1 && !header.scope_unavailable);
 reads.count=0;reads.mutate=true;
 capture_scope_point(*base,Phase::entry,context,pinned,reader,&reads,*snapshot,header);
 assert(snapshot->error==Error::changed_guard);
 graph["revision"]=2; configs->publish(graph,false);
 assert(configs->pin(0)!=pinned && pinned->scope->revision==1);
 assert(configs->pin(0)->scope->revision==2);
 J publication{{"version",1},{"revision",1},{"mode_epoch",0},{"raw_collection","all"},{"available",true},
 {"chunks",J::array({{{"fields",J::array({field(1,"publication.open.0","checkpoint","before",1),field(2,"publication.close.0","checkpoint","after",1)})}}})}};
 configs->publish(publication,true);reads.mutate=false;reads.count=0;header={};header.publication_request=1;
 capture_scope_point(*base,Phase::entry,context,configs->pin(0),reader,&reads,*snapshot,header);
 assert(snapshot->error==Error::ok && snapshot->field_count==7 && reads.count==7);
 assert(header.publication_captured && header.publication_revision==1);
 reads.count=0;header={};
 capture_scope_point(*base,Phase::entry,context,configs->pin(0),reader,&reads,*snapshot,header);
 assert(snapshot->field_count==5 && !header.publication_captured);
 std::cout<<"scope bracket, clock spanning graph, pinned immutable revision passed\n";
}
