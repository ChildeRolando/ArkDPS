#pragma once
#include "native_capture.h"
#include "json.hpp"
#include <algorithm>
#include <string>
#include <vector>
#include <stdexcept>

namespace arkdps::capture {
struct CompiledPlan {
    HookCapturePlan plan;
    std::array<std::uintptr_t, max_roots> globals{};
    std::uint16_t globals_valid = 0;
    std::vector<std::string> roles;
};
inline std::uint64_t plan_uint(const nlohmann::json& value, std::uint64_t maximum) {
    std::uint64_t number=0;
    if (value.is_number_unsigned()) number=value.get<std::uint64_t>();
    else if (value.is_number_integer() && value.get<std::int64_t>()>=0) number=value.get<std::uint64_t>();
    else throw std::runtime_error("capture plan requires an unsigned integer");
    if (number>maximum) throw std::runtime_error("capture plan integer out of bounds");
    return number;
}
inline std::uintptr_t plan_address(const nlohmann::json& value) {
    if (!value.is_string()) throw std::runtime_error("capture address must be a string");
    const auto text=value.get<std::string>();
    if (text.empty() || text.size()>18 || text[0]=='-' || text[0]=='+')
        throw std::runtime_error("invalid capture address");
    const auto first=text.rfind("0x",0)==0 ? 2u : 0u;
    if (text.size()==first) throw std::runtime_error("empty capture address");
    for (std::size_t i=first;i<text.size();++i) {
        const char c=text[i];
        if (!((c>='0' && c<='9') || (first && ((c>='a' && c<='f') || (c>='A' && c<='F')))))
            throw std::runtime_error("invalid capture address character");
    }
    std::size_t used=0;
    const auto address=std::stoull(text, &used, text.rfind("0x",0)==0 ? 16 : 10);
    if (used!=text.size()) throw std::runtime_error("invalid capture address suffix");
    return address;
}
inline CompiledPlan compile_plan(const nlohmann::json& source, std::uintptr_t hook_address) {
    if (!source.is_object() || source.value("complete", nlohmann::json())!=true
        || !source.contains("schema_version") || plan_uint(source.at("schema_version"), 65535)!=1
        || source.value("abi", "")!="win64-gp"
        || plan_address(source.at("address"))!=hook_address)
        throw std::runtime_error("native async capture plan is absent, incomplete or unbound");
    CompiledPlan result;
    result.plan.arg_count=plan_uint(source.at("arg_count"), 16);
    result.plan.arg_mask=plan_uint(source.at("arg_mask"), 65535);
    result.plan.max_bytes=plan_uint(source.at("max_bytes"), max_bytes);
    result.plan.max_reads=plan_uint(source.at("max_reads"), max_reads);
    const auto& globals=source.at("globals");
    if (!globals.is_array() || globals.size()>16) throw std::runtime_error("invalid capture globals");
    for (std::size_t i=0;i<globals.size();++i) {
        result.globals[i]=plan_address(globals[i]); result.globals_valid|=1u<<i;
    }
    const auto& fields=source.at("fields");
    if (!fields.is_array() || fields.size()>max_fields) throw std::runtime_error("invalid capture fields");
    for (const auto& value : fields) {
        auto& field=result.plan.fields[result.plan.field_count++];
        field.id=plan_uint(value.at("field_id"), 65535);
        const auto role=value.at("role").get<std::string>();
        if (role.empty() || role.size()>96) throw std::runtime_error("invalid capture role");
        auto found=std::find(result.roles.begin(), result.roles.end(), role);
        if (found==result.roles.end()) { result.roles.push_back(role); field.role=result.roles.size(); }
        else field.role=1+std::distance(result.roles.begin(), found);
        const auto phase=value.at("phase").get<std::string>();
        if (phase=="entry") field.phase=Phase::entry;
        else if (phase=="leave") field.phase=Phase::leave;
        else if (phase=="checkpoint") field.phase=Phase::checkpoint;
        else throw std::runtime_error("invalid capture phase");
        const auto root=value.at("root").get<std::string>();
        if (root=="arg") field.root=Root::arg;
        else if (root=="entry_ref") field.root=Root::entry_ref;
        else if (root=="parent_ref") field.root=Root::parent_ref;
        else if (root=="global_slot") field.root=Root::global_slot;
        else if (root=="return_value") field.root=Root::return_value;
        else if (root=="checkpoint_ref") field.root=Root::checkpoint_ref;
        else if (root=="absolute") field.root=Root::absolute;
        else throw std::runtime_error("invalid capture root");
        field.root_index=plan_uint(value.value("root_index", nlohmann::json(0)), 15);
        field.offset=plan_uint(value.at("offset"), UINT32_MAX);
        field.bytes=plan_uint(value.at("bytes"), max_bytes);
        const auto& path=value.at("pointer_offsets");
        if (!path.is_array() || path.size()>max_depth) throw std::runtime_error("invalid capture pointer path");
        for (const auto& offset : path) field.pointer_offsets[field.depth++]=plan_uint(offset, UINT32_MAX);
        const auto kind=value.at("kind").get<std::string>();
        if (kind=="bytes") field.kind=Kind::bytes;
        else if (kind=="utf16") field.kind=Kind::utf16;
        else if (kind=="counted_bytes") field.kind=Kind::counted_bytes;
        else throw std::runtime_error("invalid capture kind");
        field.guard_id=plan_uint(value.at("guard_id"), max_guards-1);
        const auto guard=value.at("guard_side").get<std::string>();
        if (guard=="none") field.guard=Guard::none;
        else if (guard=="before") field.guard=Guard::before;
        else if (guard=="after") field.guard=Guard::after;
        else if (guard=="member") field.guard=Guard::member;
        else throw std::runtime_error("invalid capture guard");
        if (!value.at("required").is_boolean()) throw std::runtime_error("invalid capture required flag");
        field.required=value.at("required").get<bool>();
        if (value.contains("expected_class")) field.expected_class=plan_address(value.at("expected_class"));
        if (value.contains("absolute_address")) field.absolute_address=plan_address(value.at("absolute_address"));
        field.max_units=plan_uint(value.value("max_units", nlohmann::json(0)), max_string_units);
        field.count_field_id=plan_uint(value.value("count_field_id", nlohmann::json(0)), 65535);
        field.element_bytes=plan_uint(value.value("element_bytes", nlohmann::json(0)), 65535);
        field.max_count=plan_uint(value.value("max_count", nlohmann::json(0)), 65535);
    }
    // No named projection is implemented by this generic read-recipe compiler.
    // Producers must lower every operation to fields before declaring complete.
    const auto& required=source.at("required_projections");
    if (!required.is_array() || !required.empty())
        throw std::runtime_error("native capture projection has not been lowered to a bounded plan");
    if (validate_plan(result.plan)!=Error::ok) throw std::runtime_error("invalid native capture plan");
    return result;
}
} // namespace arkdps::capture
