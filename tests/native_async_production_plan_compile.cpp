#include "engine_capture_plan.h"
#include <fstream>
#include <iostream>
#include <stdexcept>
using namespace arkdps::capture;
using J=nlohmann::json;
void check(bool value){if(!value)throw std::runtime_error("production plan capability contract failed");}
int main(int argc,char** argv){
 if(argc!=2)return 2;
 std::ifstream stream(argv[1]);J fixture;stream>>fixture;
 check(capture_capabilities(false)["lowered_projections"].empty());check(capture_capabilities(true)["lowered_projections"].size()==6);
 unsigned accepted=0,rejected=0;
 for(const auto& entry:fixture.at("plans")) {
   const auto& plan=entry.contains("contract")?entry.at("contract"):entry;
   const auto address=plan_address(plan.at("address"));
   try {auto compiled=compile_plan(plan,address);check(compiled.cold&&compiled.cold->plan->projection_count>0);++accepted;}
   catch(const std::exception& e){std::cerr<<plan.at("name")<<": "<<e.what()<<'\n';return 3;}
   auto reject=[&](J bad){bool failed=false;try{(void)compile_plan(bad,address);}catch(const std::exception&){failed=true;}check(failed);++rejected;};
   auto bad=plan;bad.erase("cold_plan");reject(bad);
   bad=plan;bad["required_projections"].push_back("unknown_projection");reject(bad);
   bad=plan;bad["required_projections"].push_back(bad["required_projections"][0]);reject(bad);
   bad=plan;bad["required_projections"]=J::array();reject(bad);
   bad=plan;bad["raw_collection"]="local";reject(bad);
   bad=plan;bad["cold_plan"]["fields"][2]["role"]="cold.body";reject(bad);
   bad=plan;for(auto& field:bad["fields"])if(field["role"]=="clock_open")field["bytes"]=8;reject(bad);
   if(plan["profile"]=="modifier") {
      bad=plan;auto& fields=bad["fields"];
      fields.erase(std::remove_if(fields.begin(),fields.end(),[](const auto& f){return f.at("phase")=="checkpoint";}),fields.end());reject(bad);
      bad=plan;auto& form=bad["fields"];
      form.erase(std::remove_if(form.begin(),form.end(),[](const auto& f){return f.value("name",std::string())=="source.data.host";}),form.end());reject(bad);
   }
   if(plan["profile"]=="shield") {
      bad=plan;auto& fields=bad["fields"];
      fields.erase(std::remove_if(fields.begin(),fields.end(),[](const auto& f){return f.at("kind")=="utf16_array";}),fields.end());reject(bad);
   }
   if(plan["profile"]=="boundary") {bad=plan;bad.erase("entry_roots");reject(bad);}
   if(plan["profile"]=="hp") {
      bad=plan;bad["hp_filter"].erase("defer_entry_scope");check(!compile_plan(bad,address).hp_defer_scope);
      bad=plan;bad["hp_filter"]["defer_entry_scope"]="true";reject(bad);
   }
 }
 check(accepted==fixture.value("contracts",36u));std::cout<<"PASS production recipe compiler: "<<accepted<<" exact source plans, "<<rejected<<" strict negatives; layout oracle, runtime initialization not exercised\n";
}
