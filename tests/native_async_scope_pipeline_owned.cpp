#include "engine_capture_pipeline.h"
#include <cassert>
#include <iostream>
using namespace arkdps::capture;
using J=nlohmann::json;
struct Reads { unsigned count=0; bool mutate=false; std::uint64_t word=42; std::uint8_t born=0; };
bool reader(void* p,std::uintptr_t address,void* out,std::size_t size) noexcept {
 auto& r=*static_cast<Reads*>(p); ++r.count;
 if (r.mutate && r.count==2) ++r.word;
 if(address==reinterpret_cast<std::uintptr_t>(&r.born)&&size==1){std::memcpy(out,&r.born,1);return true;}
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
 J cold{{"version",1},{"max_bytes",1024},{"max_reads",32},
 {"fields",J::array({field(1,"cold.root_open","checkpoint","before",1),field(2,"cold.body","checkpoint","none",0),field(3,"cold.root_close","checkpoint","after",1)})}};
 plan["cold_plan"]=cold; plan["force_full_binding"]={{"phase","entry"}};
 auto boundary=std::make_unique<CompiledPlan>(compile_plan(plan,1));configs->intern(*boundary);
 header={};reads.count=0;
 capture_scope_point(*boundary,Phase::entry,context,nullptr,reader,&reads,*snapshot,header);
 assert(snapshot->error==Error::ok && snapshot->field_count==6 && reads.count==6);
 assert(header.cold_revision==1 && header.projection_candidate && !header.publication_request);
 reads.count=0;reads.mutate=true;header={};
 capture_scope_point(*boundary,Phase::entry,context,nullptr,reader,&reads,*snapshot,header);
 assert(snapshot->error==Error::changed_guard);
 reads.mutate=false;reads.count=0;
 plan.erase("cold_plan");plan.erase("force_full_binding");plan["profile"]="modifier";
 plan["fields"].push_back(field(4,"parent.body","checkpoint","none",0));
 auto modifier=std::make_unique<CompiledPlan>(compile_plan(plan,1));configs->intern(*modifier);
 auto lane=std::make_unique<Lane>();lane->depth=3;
 lane->calls[0].id=UINT64_MAX-10;lane->calls[0].apply_modifier=true;
 lane->calls[1].id=UINT64_MAX-5;lane->calls[1].apply_modifier=true;
 lane->calls[2].id=123;lane->calls[2].apply_modifier=true;
 header={};header.invocation=123;
 capture_scope_point(*modifier,Phase::entry,context,nullptr,reader,&reads,*snapshot,header,lane.get());
 assert(snapshot->error==Error::ok && snapshot->field_count==5);
 assert(header.parent_checkpoint_count==2);
 assert(lane->parent_checkpoints[0].invocation==UINT64_MAX-10 && lane->parent_checkpoints[0].checkpoint==1);
 assert(lane->parent_checkpoints[1].invocation==UINT64_MAX-5 && lane->parent_checkpoints[1].checkpoint==2);
 assert(snapshot->fields[2].checkpoint==1 && snapshot->fields[3].checkpoint==2);
 plan["arg_count"]=2;plan["arg_mask"]=3;plan["profile"]="hp";
 plan["hp_filter"]={{"receiver_arg",0},{"attribute_index_arg",1},{"max_hp_attribute",0},{"enemy_class","42"},{"normal_setter",false}};
 auto hp=std::make_unique<CompiledPlan>(compile_plan(plan,1));configs->intern(*hp);
 lane->depth=1;lane->calls[0].hp_ignored=false;context.args_valid=3;context.args[1]=1;
 reads.count=0;header={};
 capture_scope_point(*hp,Phase::entry,context,nullptr,reader,&reads,*snapshot,header,lane.get());
 assert(reads.count==0 && snapshot->field_count==0 && lane->calls[0].hp_ignored);
 capture_scope_point(*hp,Phase::leave,context,nullptr,reader,&reads,*snapshot,header,lane.get());
 assert(reads.count==0 && snapshot->field_count==0);
 hp->hp_field_kind[1]=1;
 hp->plan->fields[hp->plan->field_count]=hp->plan->fields[1];
 auto& born_field=hp->plan->fields[hp->plan->field_count++];
 born_field.id=20;born_field.bytes=1;born_field.absolute_address=reinterpret_cast<std::uintptr_t>(&reads.born);
 hp->hp_field_kind[hp->plan->field_count-1]=2;
 context.args[1]=0;reads.count=0;reads.mutate=false;reads.word=42;reads.born=0;header={};
 capture_scope_point(*hp,Phase::entry,context,nullptr,reader,&reads,*snapshot,header,lane.get());
 assert(reads.count==2 && snapshot->field_count==2 && lane->calls[0].hp_ignored);
 reads.count=0;reads.born=1;header={};
 capture_scope_point(*hp,Phase::entry,context,nullptr,reader,&reads,*snapshot,header,lane.get());
 assert(reads.count>2 && !lane->calls[0].hp_ignored);
 reads.count=0;reads.born=0;reads.word=43;header={};
 capture_scope_point(*hp,Phase::entry,context,nullptr,reader,&reads,*snapshot,header,lane.get());
 assert(reads.count>2 && !lane->calls[0].hp_ignored);
 std::cout<<"scope bracket, clock spanning graph, pinned immutable revision passed\n";
}
