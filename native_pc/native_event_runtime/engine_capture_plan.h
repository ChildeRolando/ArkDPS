#pragma once
#include "native_capture.h"
#include "json.hpp"
#include <algorithm>
#include <string>
#include <vector>
#include <memory>
#include <stdexcept>

namespace arkdps::capture {
struct SavedEntryRoot {
    std::uint8_t slot=0, index=0;
    Root root=Root::arg;
    std::uintptr_t absolute=0;
    std::uint32_t offset=0;
    std::uint16_t field_id=0;
};
struct CompiledPlan {
    std::unique_ptr<HookCapturePlan> plan=std::make_unique<HookCapturePlan>();
    std::array<std::uintptr_t, max_roots> globals{};
    std::uint16_t globals_valid = 0;
    std::vector<std::string> roles;
    std::array<SavedEntryRoot, max_roots> entry_roots{};
    std::uint8_t entry_root_count=0;
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
    result.plan->arg_count=plan_uint(source.at("arg_count"), 16);
    result.plan->arg_mask=plan_uint(source.at("arg_mask"), 65535);
    result.plan->max_bytes=plan_uint(source.at("max_bytes"), max_bytes);
    result.plan->max_reads=plan_uint(source.at("max_reads"), max_reads);
    const auto& globals=source.at("globals");
    if (!globals.is_array() || globals.size()>16) throw std::runtime_error("invalid capture globals");
    for (std::size_t i=0;i<globals.size();++i) {
        result.globals[i]=plan_address(globals[i]); result.globals_valid|=1u<<i;
    }
    const auto nodes=source.value("projections",nlohmann::json::array());
    if (!nodes.is_array() || nodes.size()>max_projection_nodes)
        throw std::runtime_error("invalid capture projection nodes");
    for (const auto& value : nodes) {
        auto& node=result.plan->projections[result.plan->projection_count++];
        const auto kind=value.at("kind").get<std::string>();
        if (kind=="object") node.kind=ProjectionKind::object;
        else if (kind=="utf16") node.kind=ProjectionKind::utf16;
        else if (kind=="array") node.kind=ProjectionKind::array;
        else throw std::runtime_error("invalid capture projection node kind");
        const auto path=value.value("pointer_offsets",nlohmann::json::array());
        if (!path.is_array() || path.size()>max_depth)
            throw std::runtime_error("invalid projection pointer path");
        for (const auto& offset : path) node.pointer_offsets[node.depth++]=plan_uint(offset,UINT32_MAX);
        node.offset=plan_uint(value.value("offset",nlohmann::json(0)),UINT32_MAX);
        node.bytes=plan_uint(value.value("bytes",nlohmann::json(0)),65535);
        node.child_begin=plan_uint(value.value("child_begin",nlohmann::json(0)),max_projection_nodes);
        node.child_count=plan_uint(value.value("child_count",nlohmann::json(0)),max_projection_nodes);
        node.max_visits=plan_uint(value.value("max_visits",nlohmann::json(1)),max_projection_visits);
        const auto gate=value.value("gate",std::string("none"));
        if (gate=="none") node.gate=TypeGate::none;
        else if (gate=="exact") node.gate=TypeGate::exact;
        else if (gate=="ancestor") node.gate=TypeGate::ancestor;
        else throw std::runtime_error("invalid capture projection type gate");
        if (value.contains("expected_class")) node.expected_class=plan_address(value.at("expected_class"));
        node.ancestry_depth=plan_uint(value.value("ancestry_depth",nlohmann::json(0)),10);
        if (value.contains("allow_null")) {
            if (!value.at("allow_null").is_boolean()) throw std::runtime_error("invalid projection null flag");
            node.allow_null=value.at("allow_null").get<bool>();
        }
        node.max_units=plan_uint(value.value("max_units",nlohmann::json(0)),max_string_units);
        node.count_offset=plan_uint(value.value("count_offset",nlohmann::json(0)),UINT32_MAX);
        node.items_offset=plan_uint(value.value("items_offset",nlohmann::json(0)),UINT32_MAX);
        node.data_offset=plan_uint(value.value("data_offset",nlohmann::json(0)),UINT32_MAX);
        node.element_bytes=plan_uint(value.value("element_bytes",nlohmann::json(0)),max_element_bytes);
        node.max_count=plan_uint(value.value("max_count",nlohmann::json(0)),max_array_elements);
        const auto tombstone=value.value("skip_negative_i32_offset",nlohmann::json(-1));
        if (!tombstone.is_number_integer() || tombstone.is_boolean()
            || tombstone.get<std::int64_t>() < -1 || tombstone.get<std::int64_t>() > 32767)
            throw std::runtime_error("invalid projection tombstone offset");
        node.skip_negative_i32_offset=tombstone.get<std::int16_t>();
        if (value.contains("local_fail_boundary")) {
            if (!value.at("local_fail_boundary").is_boolean()) throw std::runtime_error("invalid local projection boundary");
            node.local_fail_boundary=value.at("local_fail_boundary").get<bool>();
        }
        const auto parent_offset=value.value("parent_byte_offset",nlohmann::json(-1));
        if (!parent_offset.is_number_integer() || parent_offset.is_boolean()
            || parent_offset.get<std::int64_t>() < -1 || parent_offset.get<std::int64_t>() > INT32_MAX)
            throw std::runtime_error("invalid projection parent byte offset");
        node.parent_byte_offset=parent_offset.get<std::int32_t>();
        node.parent_byte_value=plan_uint(value.value("parent_byte_value",nlohmann::json(1)),255);

    }
    const auto& fields=source.at("fields");
    if (!fields.is_array() || fields.size()>max_fields) throw std::runtime_error("invalid capture fields");
    for (const auto& value : fields) {
        auto& field=result.plan->fields[result.plan->field_count++];
        field.id=plan_uint(value.at("field_id"), 32767);
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
        else if (kind=="utf16_array") field.kind=Kind::utf16_array;
        else if (kind=="projection_tree") field.kind=Kind::projection_tree;
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
        field.element_pointer_offset=plan_uint(value.value("element_pointer_offset", nlohmann::json(0)), 65535);
        field.projection_root=plan_uint(value.value("projection_root", nlohmann::json(0)),max_projection_nodes-1);
        if (value.contains("stop_utf16_literal")) {
            if (value.at("stop_utf16_literal")!="dynamic")
                throw std::runtime_error("unsupported capture UTF16 stop literal");
            field.stop_at_dynamic=true;
        }
    }
    // No named projection is implemented by this generic read-recipe compiler.
    // Producers must lower every operation to fields before declaring complete.
    const auto& required=source.at("required_projections");
    if (!required.is_array() || !required.empty())
        throw std::runtime_error("native capture projection has not been lowered to a bounded plan");
    const auto roots=source.value("entry_roots",nlohmann::json::array());
    if (!roots.is_array() || roots.size()>max_roots) throw std::runtime_error("invalid saved entry roots");
    std::uint16_t used_slots=0, next_id=0;
    for (unsigned i=0;i<result.plan->field_count;++i) next_id=(std::max)(next_id,result.plan->fields[i].id);
    for (const auto& value : roots) {
        auto& saved=result.entry_roots[result.entry_root_count++];
        saved.slot=plan_uint(value.at("slot"),15);
        if (saved.slot<result.plan->arg_count || (used_slots&(1u<<saved.slot)))
            throw std::runtime_error("saved entry root overwrites an argument alias or another root");
        used_slots|=1u<<saved.slot;
        const auto root=value.at("root").get<std::string>();
        if (root=="arg") saved.root=Root::arg;
        else if (root=="global_slot") saved.root=Root::global_slot;
        else if (root=="entry_ref") saved.root=Root::entry_ref;
        else if (root=="absolute") saved.root=Root::absolute;
        else throw std::runtime_error("unsupported saved entry root source");
        saved.index=plan_uint(value.value("root_index",nlohmann::json(0)),15);
        if (saved.root==Root::entry_ref && saved.index>=result.plan->arg_count)
            throw std::runtime_error("saved entry roots cannot depend on unexecuted saved roots");
        if ((saved.root==Root::arg || saved.root==Root::entry_ref) && !(result.plan->arg_mask&(1u<<saved.index)))
            throw std::runtime_error("saved entry root requires an uncaptured argument");
        if (saved.root==Root::global_slot && !(result.globals_valid&(1u<<saved.index)))
            throw std::runtime_error("saved entry root requires an undefined global");
        saved.offset=plan_uint(value.value("offset",nlohmann::json(0)),UINT32_MAX);
        if (saved.root==Root::absolute) saved.absolute=plan_address(value.at("absolute_address"));
        const auto path=value.value("pointer_offsets",nlohmann::json::array());
        if (!path.is_array() || path.size()>max_depth) throw std::runtime_error("invalid saved entry root path");
        if (path.empty()) continue; // A direct pointer alias needs no memory read.
        if (next_id==32767 || result.plan->field_count>=max_fields)
            throw std::runtime_error("saved entry roots exceed bounded field budget");
        auto& field=result.plan->fields[result.plan->field_count++];
        saved.field_id=field.id=++next_id;
        result.roles.push_back("engine.entry_root."+std::to_string(saved.slot));
        field.role=result.roles.size(); field.phase=Phase::entry;
        field.root=saved.root; field.root_index=saved.index; field.absolute_address=saved.absolute;
        field.offset=saved.offset; field.bytes=8; field.required=false;
        for (const auto& offset : path) field.pointer_offsets[field.depth++]=plan_uint(offset,UINT32_MAX);
    }
    if (validate_plan(*result.plan)!=Error::ok) throw std::runtime_error("invalid native capture plan");
    return result;
}

// Called after the entry snapshot. The final segment's address is the pointer
// reached by the core's already-checked path, not a second live dereference.
inline void save_entry_roots(const CompiledPlan& compiled, const Snapshot& snapshot, Context& context) noexcept {
    for (unsigned i=0;i<compiled.entry_root_count;++i) {
        const auto& saved=compiled.entry_roots[i];
        std::uintptr_t address=0; bool valid=false;
        if (saved.field_id) {
            for (unsigned j=0;j<snapshot.field_count;++j) {
                const auto& field=snapshot.fields[j];
                if (field.id==saved.field_id && field.error==Error::ok && field.final_segment<snapshot.segment_count) {
                    address=snapshot.segments[field.final_segment].address; valid=true; break;
                }
            }
        } else {
            switch (saved.root) {
            case Root::arg: address=context.args[saved.index]; valid=context.args_valid&(1u<<saved.index); break;
            case Root::entry_ref: address=context.entry_refs[saved.index]; valid=context.entry_valid&(1u<<saved.index); break;
            case Root::global_slot: address=context.globals[saved.index]; valid=context.globals_valid&(1u<<saved.index); break;
            case Root::absolute: address=saved.absolute; valid=true; break;
            default: break;
            }
            if (address>UINT64_MAX-saved.offset) valid=false;
            else address+=saved.offset;
        }
        if (valid) { context.entry_refs[saved.slot]=address; context.entry_valid|=1u<<saved.slot; }
    }
}
} // namespace arkdps::capture
