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
    std::cout << "PASS native async plan: complete bound owned plan and " << rejections << " strict rejections\n";
}
