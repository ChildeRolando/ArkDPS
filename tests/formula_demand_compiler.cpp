#include "../native_pc/native_event_runtime/engine_capture_pipeline.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
using J=nlohmann::json;
using namespace arkdps::capture;
static void check(bool b){if(!b)throw std::runtime_error("consumer demand gate failed");}
static bool reader(void*,std::uintptr_t address,void* out,std::size_t bytes) noexcept {
 std::memset(out,0,bytes);if(address==0x4000&&bytes==8){const std::uint64_t klass=0x3000;std::memcpy(out,&klass,8);}return address==0x1000||address==0x4000||address==0x5000;
}
int main(int argc,char** argv){
 check(argc==2);std::ifstream in(argv[1]);J plans;in>>plans;unsigned accepted=0,rejected=0,unexpected=0;
 for(auto source:plans){
  std::cerr<<source["name"]<<"\n";
  const auto address=plan_address(source.at("address"));
  auto valid=compile_plan(source,address);check(bool(valid.plan));++accepted;
  auto reject=[&](J p){bool failed=false;try{(void)compile_plan(p,address);}catch(const std::exception&){failed=true;}if(!failed)std::cerr<<"rejection failed index "<<rejected<<"\n";if(!failed)++unexpected;++rejected;};
  unsigned clock_index=0;while(source["fields"][clock_index].at("role")!="clock_open")++clock_index;
  auto bad=source;auto& requirements=bad["required_projections"];for(auto i=requirements.begin();i!=requirements.end();){if(*i=="consumer_edge_evidence")i=requirements.erase(i);else ++i;}reject(bad);
  bad=source;bad["fields"][clock_index]["bytes"]=8;reject(bad);
  bad=source;bad["fields"][clock_index]["tier"]="changed";reject(bad);
  bad=source;bad["fields"][clock_index]["tier"]=source["fields"][clock_index].value("tier",std::string())=="publication"?"":"publication";reject(bad);
  bad=source;bad["fields"][clock_index]["root"]="arg";reject(bad);
  bad=source;bad["fields"][clock_index]["phase"]="checkpoint";reject(bad);
  bad=source;bad["cold_plan"]["fields"][2]["required"]=false;reject(bad);
  if(source["name"]=="BattleFormula.CalculateDamageInternal"||source["name"]=="Unit.OnTakeDamage"||source["name"]=="Entity.OnTakeDamage"||source["name"]=="BlockDamage._AssignDynamicVar") {
   const bool internal=source["name"]=="BattleFormula.CalculateDamageInternal";
   // Isolate the compiler-issued edge policy from unrelated actor/blackboard data.
   // Full actual body semantics are covered by damage_demand_owned_trace.js.
   auto probe=compile_plan(plans[3],plan_address(plans[3].at("address")));
   probe.ordinary_scope_edges=valid.ordinary_scope_edges;
   if(!internal)probe.publication_fields.fill(false);
   valid=std::move(probe);
   auto configs=std::make_unique<CaptureConfigurations>();configs->intern(valid);
   const auto field=[](unsigned id,const char* role,const char* side){return J{{"field_id",id},{"role",role},{"phase","checkpoint"},{"root","absolute"},{"absolute_address","0x5000"},{"root_index",0},{"pointer_offsets",J::array()},{"offset",0},{"bytes",8},{"kind","bytes"},{"guard_id",1},{"guard_side",side},{"required",true}};};
   J graph{{"version",1},{"revision",1},{"mode_epoch",0},{"raw_collection","all"},{"available",true},{"body_insertion_index",1},{"fields",J::array({field(1,"scope.open","before"),field(2,"scope.close","after")})}};
   configs->publish(graph,false);
   J pub{{"version",1},{"revision",1},{"mode_epoch",0},{"raw_collection","all"},{"available",true},{"chunks",J::array({{{"fields",J::array({field(1,"publication.open.0","before"),field(2,"publication.close.0","after")})}}})}};
   configs->publish(pub,true);auto snapshot=std::make_unique<Snapshot>();Context context{};context.globals=valid.globals;context.globals_valid=valid.globals_valid;RecordHeader h{};
   capture_scope_point(valid,Phase::leave,context,configs->pin(0),reader,nullptr,*snapshot,h);
   std::cerr<<"ordinary "<<int(snapshot->error)<<" "<<snapshot->field_count<<"\n";check(snapshot->error==Error::ok&&snapshot->field_count==(internal?0:4)&&!h.publication_captured);
   h={};capture_scope_point(valid,Phase::entry,context,configs->pin(0),reader,nullptr,*snapshot,h);
   check(snapshot->error==Error::ok&&snapshot->field_count==(internal?0:2)&&h.scope_revision==0);
   h={};h.publication_request=1;capture_scope_point(valid,Phase::leave,context,configs->pin(0),reader,nullptr,*snapshot,h);
   std::cerr<<"publication "<<int(snapshot->error)<<" "<<snapshot->field_count<<"\n";check(snapshot->error==Error::ok&&snapshot->field_count==6&&h.publication_captured&&h.scope_revision==1);
   h={};capture_scope_point(valid,Phase::leave,context,configs->pin(0),reader,nullptr,*snapshot,h,nullptr,true);
   std::cerr<<"cold "<<int(snapshot->error)<<" "<<snapshot->field_count<<"\n";check(snapshot->error==Error::ok&&snapshot->field_count==5&&h.cold_revision==1&&!h.publication_captured);
  }
 }
 check(unexpected==0);
 std::cout<<"PASS_FORMULA_CONSUMER_COMPILER "<<accepted<<" valid / "<<rejected<<" strict negatives\n";
}
