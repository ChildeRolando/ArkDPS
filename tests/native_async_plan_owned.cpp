#include "../native_pc/native_event_runtime/engine_capture_plan.h"
#include <iostream>
#include <stdexcept>
using Json = nlohmann::json;
using namespace arkdps::capture;
void expect(bool condition) { if (!condition) throw std::runtime_error("capture compiler contract failed"); }
int main() {
    const auto base=Json::parse(R"({"schema_version":1,"complete":true,"name":"owned",
        "address":"0x1234","abi":"win64-gp","profile":"owned","arg_count":16,
        "arg_mask":65535,"globals":[],"fields":[],"max_bytes":65536,"max_reads":4096,
        "required_projections":[]})");
    expect(compile_plan(base,0x1234).plan.arg_mask==65535);
    unsigned rejections=0;
    auto reject=[&](Json plan) {
        bool rejected=false;
        try { (void)compile_plan(plan,0x1234); } catch (const std::exception&) { rejected=true; }
        expect(rejected); ++rejections;
    };
    auto plan=base; plan["complete"]=false; reject(plan);
    plan=base; plan.erase("complete"); reject(plan);
    plan=base; plan["complete"]=1; reject(plan);
    plan=base; plan["schema_version"]=true; reject(plan);
    plan=base; plan["required_projections"]={"unimplemented_scope_graph"}; reject(plan);
    plan=base; plan["address"]="0x1235"; reject(plan);
    plan=base; plan["address"]=" 4660"; reject(plan);
    plan=base; plan["arg_count"]=1; reject(plan);
    plan=base; plan["max_reads"]=4097; reject(plan);
    plan=base; plan["max_bytes"]=0; reject(plan);
    auto tree=base;
    tree["projections"]=Json::array({{{"kind","object"},{"bytes",8}}});
    tree["fields"]=Json::array({{{"field_id",1},{"phase","entry"},{"role","body"},
        {"root","absolute"},{"absolute_address","0x4567"},{"pointer_offsets",Json::array()},
        {"offset",0},{"bytes",0},{"kind","projection_tree"},{"projection_root",0},
        {"guard_id",0},{"guard_side","none"},{"required",true}}});
    const auto compiled=compile_plan(tree,0x1234);
    expect(compiled.plan.projection_count==1 && compiled.plan.projections[0].bytes==8);
    plan=tree; plan["projections"][0]["kind"]="callback"; reject(plan);
    plan=tree; plan["projections"][0]["max_visits"]=257; reject(plan);
    plan=tree; plan["projections"][0]["child_count"]=2; reject(plan);
    plan=tree; plan["projections"][0]["gate"]="fuzzy"; reject(plan);
    plan=tree; plan["projections"][0]["skip_negative_i32_offset"]=-2; reject(plan);
    plan=tree; plan["fields"][0]["projection_root"]=1; reject(plan);
    std::cout << "PASS native async plan: complete bound owned plan and " << rejections << " strict rejections\n";
}
