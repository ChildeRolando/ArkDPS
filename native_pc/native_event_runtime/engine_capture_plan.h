#pragma once
#include "native_capture.h"
#include "json.hpp"
#include <algorithm>
#include <string>
#include <vector>
#include <memory>
#include <stdexcept>

namespace arkdps::capture {
inline constexpr std::array<const char*,6> capture_projection_names{{"live_scope_guards","same_point_class_ancestry",
    "open_parent_checkpoint","token_host_and_character_form","full_native_binding","current_buff_and_blackboard_keys"}};
inline nlohmann::json capture_capabilities(bool product_ready=false) {
    nlohmann::json names=nlohmann::json::array();
    if(product_ready)for(const auto* name:capture_projection_names)names.push_back(name);
    return {{"version",1},{"raw_collection","all"},{"lowered_projections",std::move(names)}};
}

struct ObservationValue {std::uint16_t offset=0;std::uint8_t kind=0;};
struct OwnedReadWord {std::uint32_t offset=0;std::uint8_t kind=0;std::uint32_t bits=0;double number=0;};
struct OwnedReadField {
    std::string role;
    std::uint16_t role_id=0,expected_field_id=0;
    std::uint32_t checkpoint=0,offset=0,bytes=0,max_units=0;
    std::array<std::uint32_t,capture::max_depth> path{};
    std::uintptr_t absolute=0;
    int selector=-1;
    std::uint8_t tier=0,depth=0,kind=0;
    bool current_checkpoint=false,absolute_root=false,project=false;
    std::vector<ObservationValue> observations;
    std::vector<OwnedReadWord> words;std::vector<std::uint16_t> utf16;bool compare_utf16=false;
};
struct OwnedReadProgram {unsigned tiers=0;std::vector<OwnedReadField> fields;};

struct ObservationSpec {std::uint16_t source_index=0;std::string name;std::vector<ObservationValue> values;};
struct ObservationField {OwnedReadField raw;std::uint16_t presence_id=0;std::string name;std::vector<ObservationValue> values;};
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
    std::vector<ObservationSpec> observation_specs;
    std::array<std::vector<ObservationField>,2> hp_observations;
    bool hp_observations_ready=false;std::uintptr_t hook_address=0;
    // Initialized once; callback dispatch never reads the mutable role registry.
    std::array<std::uint8_t,max_fields> field_stage{};
    std::array<SavedEntryRoot, max_roots> entry_roots{};
    std::uint8_t entry_root_count=0;
    std::array<std::uint16_t,3> terminal_guard_fields{};
    std::array<std::uint16_t,14> terminal_retained_fields{};
    bool terminal_destroy=false;
    bool apply_modifier=false, buff_damage=false, parent_checkpoint_fields=false;
    bool hp_profile=false, hp_dirty=false, hp_normal=false, hp_defer_scope=false;
    std::uintptr_t hp_enemy_class=0;
    std::array<std::uintptr_t,16> hp_non_enemy_classes{};
    std::uint8_t hp_non_enemy_count=0;
    std::array<std::uint8_t,max_fields> hp_field_kind{};
    std::unique_ptr<CompiledPlan> cold;
    bool force_cold_leave=false, force_full_entry=false, force_cold_conditional=false;
    std::uint8_t force_cold_arg=0;
    std::uint64_t force_cold_value=0;
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
    CompiledPlan result;result.hook_address=hook_address;
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
        else if (gate=="class_set") node.gate=TypeGate::class_set;
        else throw std::runtime_error("invalid capture projection type gate");
        if (node.gate == TypeGate::class_set) {
            if (value.contains("expected_class") || value.contains("ancestry_depth"))
                throw std::runtime_error("class set cannot declare another type gate");
            const auto& classes=value.at("expected_classes");
            if (!classes.is_array() || classes.empty() || classes.size() > max_class_set_entries - result.plan->class_set_entries.size())
                throw std::runtime_error("invalid capture class set bound");
            node.class_set_begin=static_cast<std::uint32_t>(result.plan->class_set_entries.size());
            for (const auto& entry:classes) {
                const auto address=plan_address(entry);
                if (!address) throw std::runtime_error("null capture class set entry");
                result.plan->class_set_entries.push_back(address);
            }
            auto first=result.plan->class_set_entries.begin()+node.class_set_begin;
            std::sort(first,result.plan->class_set_entries.end());
            if (std::adjacent_find(first,result.plan->class_set_entries.end()) != result.plan->class_set_entries.end())
                throw std::runtime_error("duplicate capture class set entry");
            node.class_set_count=static_cast<std::uint32_t>(classes.size());
        } else if (value.contains("expected_classes")) throw std::runtime_error("class set on incompatible gate");
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
        if(value.contains("observation")) {
            const auto& descriptor=value.at("observation");
            if(!descriptor.is_object()||descriptor.size()!=2||!descriptor.at("values").is_array()
                ||descriptor.at("values").empty()||descriptor.at("values").size()>4)
                throw std::runtime_error("invalid HP observation descriptor");
            ObservationSpec spec;spec.source_index=result.plan->field_count-1;spec.name=descriptor.at("name").get<std::string>();
            if(spec.name.empty()||spec.name.size()>96||spec.name!=value.at("name").get<std::string>())throw std::runtime_error("unbound HP observation name");
            for(const auto& codec:descriptor.at("values")) {
                if(!codec.is_object()||codec.size()!=2)throw std::runtime_error("invalid HP observation codec");
                ObservationValue output;output.offset=plan_uint(codec.at("offset"),128);const auto kind=codec.at("kind").get<std::string>();
                output.kind=kind=="u8"?0:kind=="u32"?1:kind=="u64"?2:kind=="i64"?3:kind=="utf16"?4:255;
                const unsigned bytes=output.kind==0?1:output.kind==1?4:8;
                if(output.kind==255||(output.kind==4?(field.kind!=Kind::utf16||output.offset||descriptor.at("values").size()!=1)
                    :(field.kind!=Kind::bytes||output.offset>field.bytes||bytes>field.bytes-output.offset)))
                    throw std::runtime_error("HP observation codec exceeds original field");
                spec.values.push_back(output);
            }
            result.observation_specs.push_back(std::move(spec));
        }
        if (value.contains("stop_utf16_literal")) {
            if (value.at("stop_utf16_literal")!="dynamic")
                throw std::runtime_error("unsupported capture UTF16 stop literal");
            field.stop_at_dynamic=true;
        }
    }
    if (source.contains("hp_filter")) {
        const auto& filter=source.at("hp_filter");
        result.hp_profile=true;
        result.hp_dirty=!filter.at("attribute_index_arg").is_null();
        if (result.hp_dirty && plan_uint(filter.at("attribute_index_arg"),15)!=1)
            throw std::runtime_error("unsupported HP attribute argument");
        if (plan_uint(filter.at("receiver_arg"),15)!=0 || plan_uint(filter.at("max_hp_attribute"),UINT32_MAX)!=0)
            throw std::runtime_error("unsupported HP early filter layout");
        if (!filter.at("enemy_class").is_null()) result.hp_enemy_class=plan_address(filter.at("enemy_class"));
        const auto non_enemy=filter.value("hp_non_enemy_classes",nlohmann::json::array());
        if(!non_enemy.is_array() || non_enemy.size()>result.hp_non_enemy_classes.size()
            ||(!non_enemy.empty()&&!result.hp_enemy_class))throw std::runtime_error("invalid exact non-enemy HP classes");
        for(const auto& value:non_enemy) {
            const auto klass=plan_address(value);
            if(!klass || klass==result.hp_enemy_class)throw std::runtime_error("non-enemy HP class overlaps Enemy");
            for(unsigned i=0;i<result.hp_non_enemy_count;++i)
                if(result.hp_non_enemy_classes[i]==klass)throw std::runtime_error("duplicate non-enemy HP class");
            result.hp_non_enemy_classes[result.hp_non_enemy_count++]=klass;
        }
        result.hp_normal=filter.at("normal_setter").get<bool>();
        result.hp_defer_scope=filter.value("defer_entry_scope",nlohmann::json(false)).get<bool>();
        if (result.hp_defer_scope && (!result.hp_normal || result.hp_dirty))
            throw std::runtime_error("deferred HP scope is not a normal coalesced setter");
        for (unsigned i=0;i<source.at("fields").size();++i) {
            const auto& field=source.at("fields")[i];
            const auto name=field.value("name",std::string());
            result.hp_field_kind[i]=name=="hp.id" ? 6 : name=="hp.identity" ? 1 : name=="hp.born" ? 2
                : name=="hp.before" ? 3 : name=="hp.closing" ? 4
                : field.value("tier",std::string())=="changed" ? 5 : 0;
        }
        if(result.hp_non_enemy_count) {
            unsigned identities=0,ids=0;
            for(unsigned i=0;i<result.plan->field_count;++i) {
                const auto& field=result.plan->fields[i];
                if(result.hp_field_kind[i]==1) {
                    ++identities;
                    if(field.phase!=Phase::entry||field.root!=Root::arg||field.root_index||field.depth
                        ||field.kind!=Kind::bytes||field.offset||field.bytes!=0x1c)
                        throw std::runtime_error("non-enemy HP filter lacks same-point class/UID");
                }
                if(result.hp_field_kind[i]==6) {
                    ++ids;
                    if(field.phase!=Phase::entry||field.root!=Root::arg||field.root_index||field.depth!=1
                        ||field.pointer_offsets[0]!=0x148||field.offset||field.kind!=Kind::utf16||!field.expected_class)
                        throw std::runtime_error("non-enemy HP filter lacks typed same-object ID");
                }
            }
            if(identities!=1||ids!=1)throw std::runtime_error("non-enemy HP filter identity/ID is not unique");
        }
    }
    result.terminal_destroy=source.value("name",std::string())=="BattleController.Destroy";
    if (result.terminal_destroy) for (unsigned i=0;i<source.at("fields").size();++i) {
        const auto& value=source.at("fields")[i];
        if (value.at("phase")!="leave") continue;
        const auto name=value.value("name",std::string());
        const auto role=value.at("role").get<std::string>();
        const int slot=name=="terminal.singleton" ? 0 : role=="clock_open" ? 1 : role=="clock_close" ? 2 : -1;
        if (slot>=0) result.terminal_guard_fields[slot]=result.plan->fields[i].id;
        constexpr std::array<const char*,14> retained{{"controller","controller.state","native","native.owner","logger",
            "logger.owner","stats","level","receiver.logger_view","receiver.level_class","receiver.stats_class",
            "terminal.old_logger_stats","terminal.old_stage_pointer","terminal.stage_text"}};
        for(unsigned field=0;field<retained.size();++field)
            if(name==retained[field])result.terminal_retained_fields[field]=result.plan->fields[i].id;
    }
    result.apply_modifier=source.value("name",std::string())=="Entity.ApplyModifier";
    result.buff_damage=source.value("name",std::string())=="Buff.OnTakeDamage";
    for (unsigned i=0;i<result.plan->field_count;++i) {
        const auto& f=result.plan->fields[i];
        if (f.phase==Phase::checkpoint && source.value("profile",std::string())=="modifier") {
            result.parent_checkpoint_fields=true;
            if (f.guard_id) throw std::runtime_error("parent checkpoint guards need an independent namespace");
        }
    }
    const auto& required=source.at("required_projections");
    if (!required.is_array()) throw std::runtime_error("invalid native capture requirements");
    unsigned required_mask=0;
    for(const auto& value:required) {
        if(!value.is_string())throw std::runtime_error("invalid native capture requirement name");
        unsigned bit=0;
        for(unsigned i=0;i<capture_projection_names.size();++i)if(value==capture_projection_names[i])bit=1u<<i;
        if(!bit || (required_mask&bit))throw std::runtime_error("unknown or duplicate native capture requirement");
        required_mask|=bit;
    }
    const auto profile=source.value("profile",std::string());
    const bool production=profile=="modifier"||profile=="hp"||profile=="lifecycle"||profile=="skill_cast"
        ||profile=="skill_transition"||profile=="buff"||profile=="shield"||profile=="formula"||profile=="boundary";
    const unsigned mandatory=3u|(profile=="modifier"?12u:0u)|(profile=="boundary"?16u:0u)|(profile=="shield"?32u:0u);
    if(production && (required_mask&mandatory)!=mandatory)
        throw std::runtime_error("production capture requirements were removed");
    if(required_mask && source.value("raw_collection",std::string())!="all")
        throw std::runtime_error("named native capture projection requires raw all-player evidence");
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
    if (source.contains("cold_plan")) {
        auto cold=source.at("cold_plan");
        if (!cold.is_object() || cold.contains("cold_plan") || plan_uint(cold.at("version"),1)!=1)
            throw std::runtime_error("invalid cold capture template");
        cold["schema_version"]=1; cold["complete"]=true; cold["address"]="1";cold["profile"]="engine-cold";
        cold["abi"]="win64-gp"; cold["arg_count"]=0; cold["arg_mask"]=0;
        cold["globals"]=nlohmann::json::array(); cold["required_projections"]=nlohmann::json::array();
        for (auto& field : cold.at("fields")) {
            field["field_id"]=20480+plan_uint(field.at("field_id"),4095);
            const auto guard=plan_uint(field.value("guard_id",nlohmann::json(0)),479);
            if (guard) field["guard_id"]=3616+guard;
        }
        const auto& cold_fields=cold.at("fields");
        if (cold_fields.size()!=3 || cold_fields[0].at("role")!="cold.root_open"
            || cold_fields[1].at("role")!="cold.body" || cold_fields[2].at("role")!="cold.root_close")
            throw std::runtime_error("invalid cold capture bracket roles");
        result.cold=std::make_unique<CompiledPlan>(compile_plan(cold,1));
        const auto& first=result.cold->plan->fields[0];
        const auto& last=result.cold->plan->fields[2];
        if (first.phase!=Phase::checkpoint || last.phase!=Phase::checkpoint
            || result.cold->plan->fields[1].phase!=Phase::checkpoint
            || first.guard!=Guard::before || last.guard!=Guard::after
            || !first.guard_id || first.guard_id!=last.guard_id)
            throw std::runtime_error("cold capture root guard is not paired");
        if (result.cold->plan->field_count!=3)
            throw std::runtime_error("cold capture template must bracket one body tree");
    }
    if (source.contains("force_cold_boundary")) {
        const auto& force=source.at("force_cold_boundary");
        if (force.at("phase")!="leave") throw std::runtime_error("invalid forced cold phase");
        result.force_cold_leave=true;
        if (force.at("condition")=="arg_equals") {
            result.force_cold_conditional=true;
            result.force_cold_arg=plan_uint(force.at("arg_index"),15);
            result.force_cold_value=plan_uint(force.at("value"),UINT64_MAX);
            if (!(result.plan->arg_mask&(1u<<result.force_cold_arg)))
                throw std::runtime_error("forced cold condition uses uncaptured argument");
        } else if (force.at("condition")!="always") throw std::runtime_error("invalid forced cold condition");
    }
    if (source.contains("force_full_binding")) {
        if (source.at("force_full_binding").at("phase")!="entry") throw std::runtime_error("invalid full binding phase");
        result.force_full_entry=true;
    }
    if(required_mask) {
        if(!result.cold || !result.cold->plan->projection_count)
            throw std::runtime_error("named capture projection lacks a verified cold recipe");
        const auto& cold=*result.cold->plan;
        if(cold.fields[0].kind!=Kind::projection_tree || cold.fields[1].kind!=Kind::projection_tree
            ||cold.fields[2].kind!=Kind::projection_tree || !cold.fields[0].required || cold.fields[1].required
            ||!cold.fields[2].required ||cold.fields[0].projection_root!=cold.fields[2].projection_root)
            throw std::runtime_error("cold native binding is not independently bracketed");
        unsigned clocks=0;bool gated_class=false,parent=false,host=false,cid=false,flags=false,buff=false,keys=false;
        for(unsigned i=0;i<source.at("fields").size();++i){
            const auto& value=source.at("fields")[i];const auto& field=result.plan->fields[i];
            const auto role=value.at("role").get<std::string>(),name=value.value("name",std::string());
            if((role=="clock_open"||role=="clock_close") && (field.kind!=Kind::bytes || field.bytes!=12
                ||field.root!=Root::global_slot||field.root_index||field.depth||field.offset||!(result.globals_valid&1u)))
                throw std::runtime_error("callback clock is not the native twelve-byte global witness");
            if(role=="clock_open"&&field.guard==Guard::before)clocks|=field.phase==Phase::entry?1:4;
            if(role=="clock_close"&&field.guard==Guard::after)clocks|=field.phase==Phase::entry?2:8;
            parent|=field.phase==Phase::checkpoint&&field.root==Root::parent_ref&&field.root_index==1;
            host|=name=="source.data.host"&&field.kind==Kind::utf16;
            cid|=name=="source.data.cid"&&field.kind==Kind::utf16;
            flags|=name=="source.data.flags"&&field.bytes==2;
            buff|=field.root==Root::parent_ref&&field.root_index==0;
            keys|=field.kind==Kind::utf16_array&&field.stop_at_dynamic;
        }
        for(unsigned i=0;i<cold.projection_count;++i)gated_class|=cold.projections[i].gate!=TypeGate::none&&cold.projections[i].expected_class;
        if((required_mask&1u)&&clocks!=15)throw std::runtime_error("live scope lacks independent entry/leave clocks");
        if((required_mask&2u)&&!gated_class)throw std::runtime_error("class ancestry lacks immutable typed projection metadata");
        if((required_mask&4u)&&(!result.parent_checkpoint_fields||!parent))throw std::runtime_error("open parent checkpoint recipe absent");
        if((required_mask&8u)&&(!host||!cid||!flags))throw std::runtime_error("token/form recipe absent");
        if((required_mask&32u)&&(!buff||!keys))throw std::runtime_error("current buff/blackboard recipe absent");
        if(required_mask&16u) {
            unsigned slots=0;for(unsigned i=0;i<result.entry_root_count;++i)slots|=1u<<result.entry_roots[i].slot;
            const auto name=source.value("name",std::string());
            const bool boundary=name=="BattleController.Destroy" ? result.force_full_entry
                :name=="BattleController.ClearRunTimeData" ? result.force_cold_leave&&!result.force_cold_conditional
                :name=="BattleController.set_state" &&result.force_cold_leave&&result.force_cold_conditional&&result.force_cold_arg==1&&result.force_cold_value==2;
            if((slots&0xf00u)!=0xf00u||!boundary)throw std::runtime_error("full binding boundary/saved roots absent");
        }
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
