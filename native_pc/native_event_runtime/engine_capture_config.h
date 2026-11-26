#pragma once
#include "engine_capture_plan.h"
#include <atomic>
#include <cmath>
#include <memory>
#include <unordered_map>
#include <vector>

namespace arkdps::capture {
constexpr std::size_t capture_mode_slots=256;
// Static field IDs occupy the low half; the core owns dynamic IDs >=32768.
constexpr unsigned scope_field_base=4096, publication_field_base=12288, cold_field_base=20480;
constexpr unsigned scope_guard_base=32, publication_guard_base=544, cold_guard_base=3616;
struct CaptureGraph {
    std::uint64_t revision=0, mode_epoch=0;
    bool available=false;
    std::uint16_t body_insertion=0;
    std::unique_ptr<CompiledPlan> code;
    std::vector<OwnedReadProgram> read_chunks;
    OwnedReadProgram check_program;bool check_ready=false,primitive_ready=false;
};
struct CaptureConfiguration {
    std::uint64_t id=0, mode_epoch=0;
    std::shared_ptr<const CaptureGraph> scope, publication;
};

// Worker mutates ownership; producers only acquire a published immutable
// pointer while their invocation is already counted in native active.
class CaptureConfigurations final {
public:
    std::uint16_t intern_role(const std::string& role) {
        const auto found=role_ids_.find(role);
        if (found!=role_ids_.end()) return found->second;
        if (roles.size()>=65535) throw std::runtime_error("native capture role budget exceeded");
        roles.push_back(role);
        const auto id=static_cast<std::uint16_t>(roles.size());
        role_ids_.emplace(role,id); return id;
    }
    void intern(CompiledPlan& compiled) {
        if (compiled.cold) intern(*compiled.cold);
        for (unsigned i=0;i<compiled.plan->field_count;++i) {
            auto& field=compiled.plan->fields[i];
            const auto& role=compiled.roles.at(field.role-1);
            compiled.field_stage[i]=role=="clock_open" ? 1 : role=="clock_close" ? 2 : 0;
            field.role=intern_role(role);
        }
        if(compiled.hp_profile&&!compiled.observation_specs.empty()) {
            unsigned fields=0;for(unsigned i=0;i<compiled.plan->field_count;++i)fields+=compiled.plan->fields[i].phase!=Phase::checkpoint;
            if(fields!=compiled.observation_specs.size())throw std::runtime_error("partial HP observation metadata");
            for(const auto& spec:compiled.observation_specs) {
                const auto& field=compiled.plan->fields[spec.source_index];
                if(field.phase==Phase::checkpoint||(field.kind!=Kind::bytes&&field.kind!=Kind::utf16)
                    ||(field.kind==Kind::bytes&&(!field.bytes||field.bytes>128)))throw std::runtime_error("unsupported HP observation field");
                ObservationField output;output.presence_id=field.id;output.name=spec.name;output.values=spec.values;
                auto& raw=output.raw;raw.role_id=field.role;raw.current_checkpoint=true;
                raw.selector=compiled.field_stage[spec.source_index]? -2:-1;raw.expected_field_id=raw.selector==-2?field.id:0;
                if(field.root==Root::global_slot&&(compiled.globals_valid&(1u<<field.root_index))){raw.absolute_root=true;raw.absolute=compiled.globals[field.root_index];}
                else if(field.root==Root::absolute){raw.absolute_root=true;raw.absolute=field.absolute_address;}
                else if((field.root!=Root::arg&&field.root!=Root::entry_ref)||field.root_index)throw std::runtime_error("HP observation root is not the current receiver");
                raw.path=field.pointer_offsets;raw.depth=field.depth;raw.offset=field.offset;raw.bytes=field.bytes;
                raw.kind=field.kind==Kind::utf16?1:0;raw.max_units=field.max_units;
                auto& edge=compiled.hp_observations[field.phase==Phase::entry?0:1];
                for(const auto& existing:edge)if(existing.name==output.name)throw std::runtime_error("duplicate HP observation name");
                if(edge.size()>=16)throw std::runtime_error("HP observation field budget exceeded");
                edge.push_back(std::move(output));
            }
            compiled.hp_observations_ready=true;
        }
    }
    const CaptureConfiguration* pin(std::uint64_t epoch) const noexcept {
        return epoch<capture_mode_slots ? current_[epoch].load(std::memory_order_acquire) : nullptr;
    }
    // All graph reads are raw all-player reads. Desired mode is applied by the
    // one shared JS reducer, never by pruning native read evidence.
    void publish(const nlohmann::json& graph, bool publication) {
        if (!graph.is_object() || plan_uint(graph.at("version"),1)!=1 || graph.at("raw_collection")!="all")
            throw std::runtime_error("native capture graph lacks all-player provenance");
        auto next=std::make_shared<CaptureGraph>();
        next->revision=plan_uint(graph.at("revision"),9007199254740991ULL);
        next->mode_epoch=plan_uint(graph.at("mode_epoch"),capture_mode_slots-1);
        if (!next->revision) throw std::runtime_error("invalid capture graph revision");
        next->available=graph.at("available").get<bool>();
        if (next->available) {
            next->code=compile_graph(graph,publication);
            for(unsigned i=0;i<next->code->plan->field_count;++i) {
                if(i%32==0)next->read_chunks.emplace_back();
                const auto& field=next->code->plan->fields[i];
                if(field.root!=Root::absolute || (field.kind!=Kind::bytes&&field.kind!=Kind::utf16)
                    || (field.kind==Kind::bytes&&(!field.bytes||field.bytes>128)))
                    {next->read_chunks.clear();break;}
                OwnedReadField op;op.role_id=field.role;op.selector=-2;op.expected_field_id=field.id;op.tier=1;
                op.absolute_root=true;op.absolute=field.absolute_address;op.depth=field.depth;
                op.path=field.pointer_offsets;op.offset=field.offset;op.bytes=field.bytes;
                op.kind=field.kind==Kind::utf16?1:0;op.max_units=field.max_units;
                op.current_checkpoint=true;
                auto& chunk=next->read_chunks.back();chunk.tiers=1;chunk.fields.push_back(std::move(op));
            }
        }
        if(next->available&&!next->read_chunks.empty()) {
            nlohmann::json original=nlohmann::json::array();
            if(publication){for(const auto& chunk:graph.at("chunks"))for(const auto& field:chunk.at("fields"))original.push_back(field);}
            else original=graph.at("fields");
            bool supported=true;unsigned projected=0,index=0;
            for(const auto& chunk:next->read_chunks) {
              if(!supported)break;
              for(auto op:chunk.fields) {
                const auto& field=original.at(index++);
                if(!field.contains("projection")||(field.contains("comparison")&&field.at("comparison").is_null())){supported=false;break;}
                op.project=field.at("projection").get<bool>();projected+=op.project;
                if(!field.contains("comparison")&&!op.project){supported=false;break;}
                const auto comparison=field.value("comparison",nlohmann::json{{"version",1},{"words",nlohmann::json::array()}});
                if(!comparison.is_object()||comparison.size()<2||comparison.size()>3||plan_uint(comparison.at("version"),1)!=1
                    ||!comparison.at("words").is_array()||comparison.at("words").size()>64)
                    throw std::runtime_error("invalid graph comparison");
                for(auto it=comparison.begin();it!=comparison.end();++it)if(it.key()!="version"&&it.key()!="words"&&it.key()!="utf16")throw std::runtime_error("unknown graph comparison");
                for(const auto& word:comparison.at("words")) {
                    if(!word.is_object()||word.size()!=3)throw std::runtime_error("invalid graph word");
                    OwnedReadWord value;value.offset=plan_uint(word.at("offset"),128);const auto kind=word.at("kind").get<std::string>();
                    value.kind=kind=="u8"?0:kind=="u32"?1:kind=="u64_number"?2:255;
                    const unsigned bytes=value.kind==0?1:value.kind==1?4:8;
                    if(value.kind==255||op.kind||value.offset>op.bytes||bytes>op.bytes-value.offset)throw std::runtime_error("graph word range");
                    if(value.kind<2)value.bits=plan_uint(word.at("value"),value.kind==0?255:UINT32_MAX);
                    else {value.number=word.at("value").get<double>();if(!std::isfinite(value.number)||value.number<0||value.number>18446744073709551616.0)throw std::runtime_error("graph number comparison");}
                    op.words.push_back(value);
                }
                if(comparison.contains("utf16")) {
                    if(!op.kind||!comparison.at("utf16").is_array()||comparison.at("utf16").size()>op.max_units)throw std::runtime_error("graph string comparison");
                    op.compare_utf16=true;for(const auto& unit:comparison.at("utf16"))op.utf16.push_back(plan_uint(unit,65535));
                }
                if(!op.project&&op.words.empty()&&!op.compare_utf16)throw std::runtime_error("empty static graph comparison");
                if(op.project&&field.contains("observation")) {
                    const auto& observation=field.at("observation");
                    if(!observation.is_object()||observation.size()!=1||!observation.contains("values")||!observation.at("values").is_array()||observation.at("values").empty()||observation.at("values").size()>4)
                        throw std::runtime_error("invalid graph observation");
                    for(const auto& item:observation.at("values")) {
                        if(!item.is_object()||item.size()!=2)throw std::runtime_error("invalid graph observation value");
                        ObservationValue codec;codec.offset=plan_uint(item.at("offset"),128);const auto name=item.at("kind").get<std::string>();
                        codec.kind=name=="u32"?1:name=="u64"?2:name=="i64"?3:name=="i32"?5:255;
                        const unsigned width=codec.kind==1||codec.kind==5?4:8;
                        if(codec.kind==255||op.kind||codec.offset>op.bytes||width>op.bytes-codec.offset)throw std::runtime_error("graph observation range");
                        op.observations.push_back(codec);
                    }
                }
                next->check_program.fields.push_back(std::move(op));
              }
            }
            next->check_ready=supported&&projected<=32&&index==original.size();
            if(!next->check_ready)next->check_program.fields.clear();else next->check_program.tiers=1;
            next->primitive_ready=next->check_ready;
            for(const auto& field:next->check_program.fields)if(field.project&&field.observations.empty())next->primitive_ready=false;
            if(next->primitive_ready){
                // Exact declarative layout, not a collision-prone hash. It is
                // derived once from this immutable program, never live values.
                auto& layout=next->check_program.typed_layout;
                layout="v1|"+std::to_string(next->check_program.fields.size());
                unsigned original=0;
                for(const auto& field:next->check_program.fields){
                    const auto index=original++;if(!field.project)continue;
                    layout+="|"+std::to_string(index)+":"+std::to_string(field.bytes)+":"+std::to_string(field.observations.size());
                    for(const auto& codec:field.observations){
                        const char* kind=codec.kind==1?"u32":codec.kind==5?"i32":codec.kind==2?"u64":"i64";
                        layout+=":"+std::string(kind)+"@"+std::to_string(codec.offset);
                    }
                }
            }
        }
        if (!publication && next->available) {
            next->body_insertion=plan_uint(graph.at("body_insertion_index"),next->code->plan->field_count);
            for (unsigned i=0;i<next->code->plan->field_count;++i) {
                const auto side=next->code->plan->fields[i].guard;
                if ((i<next->body_insertion && side==Guard::after)
                    || (i>=next->body_insertion && side!=Guard::after))
                    throw std::runtime_error("scope graph bracket is not contiguous");
            }
        }
        const auto* previous=pin(next->mode_epoch);
        auto config=std::make_unique<CaptureConfiguration>();
        config->id=++serial_; config->mode_epoch=next->mode_epoch;
        if (previous) { config->scope=previous->scope; config->publication=previous->publication; }
        if (publication) {
            if (config->publication && next->revision<=config->publication->revision)
                throw std::runtime_error("publication graph revision regressed");
            config->publication=next;
        } else {
            if (config->scope && next->revision<=config->scope->revision)
                throw std::runtime_error("scope graph revision regressed");
            config->scope=next;
        }
        if (owned_.size()>=512) throw std::runtime_error("native capture configuration retention budget exceeded");
        auto* pointer=config.get();
        owned_.push_back(std::move(config));
        try {by_id_.emplace(pointer->id,pointer);} catch (...) {owned_.pop_back();throw;}
        current_[next->mode_epoch].store(pointer,std::memory_order_release);
        // Store->load grace checks on a different atomic need this fence.
        std::atomic_thread_fence(std::memory_order_seq_cst);
    }
    // Caller has established no replay, active==0 and processed==reserved after
    // publishing latest pointers. No producer or queued record can still need a
    // retired configuration. Keep the latest pointer for every retained epoch.
    nlohmann::json collect_quiescent(std::uint64_t producer_epoch) {
        const auto* latest=pin(producer_epoch);
        if (!latest) return nullptr;
        for (unsigned epoch=0;epoch<capture_mode_slots;++epoch)
            if (epoch!=producer_epoch) current_[epoch].store(nullptr,std::memory_order_release);
        const auto previous=owned_.size();
        owned_.erase(std::remove_if(owned_.begin(),owned_.end(),[&](const auto& config) {
            if(config.get()==latest)return false;
            by_id_.erase(config->id);return true;
        }),owned_.end());
        if (previous==owned_.size()) return nullptr;
        nlohmann::json bounds{{"mode_before",producer_epoch}};
        if (latest->scope) bounds["scope_before"]=latest->scope->revision;
        if (latest->publication) bounds["publication_before"]=latest->publication->revision;
        return bounds;
    }
    const CaptureConfiguration* find(std::uint64_t id) const noexcept {
        const auto found=by_id_.find(id);return found==by_id_.end()?nullptr:found->second;
    }
    std::vector<std::string> roles;
private:
    std::unique_ptr<CompiledPlan> compile_graph(const nlohmann::json& graph,bool publication) {
        using Json=nlohmann::json;
        Json fields=Json::array();
        const auto chunks=publication ? graph.at("chunks") : Json::array({{{"fields",graph.at("fields")}}});
        if (!chunks.is_array()) throw std::runtime_error("invalid capture graph chunks");
        unsigned group=publication ? publication_guard_base : scope_guard_base;
        const unsigned group_end=publication ? cold_guard_base : publication_guard_base;
        unsigned id=publication ? publication_field_base : scope_field_base;
        const unsigned id_end=publication ? cold_field_base : publication_field_base;
        for (const auto& chunk : chunks) {
            std::unordered_map<unsigned,unsigned> guards,ids;
            const auto& original=chunk.at("fields");
            if (!original.is_array()) throw std::runtime_error("invalid capture graph fields");
            for (const auto& field : original) {
                if (id>=id_end) throw std::runtime_error("capture graph exceeds static field namespace");
                const auto old=plan_uint(field.at("field_id"),32767);
                if (!old || !ids.emplace(old,id++).second) throw std::runtime_error("duplicate graph field ID");
            }
            for (auto field : original) {
                field.erase("observation"); // Projection metadata is not another capture contract.
                field["field_id"]=ids.at(plan_uint(field.at("field_id"),32767));
                const auto old=plan_uint(field.at("guard_id"),max_guards-1);
                if (old) {
                    if (!guards.count(old)) {
                        if (group>=group_end) throw std::runtime_error("capture graph exceeds guard namespace");
                        guards.emplace(old,group++);
                    }
                    field["guard_id"]=guards.at(old);
                }
                if (field.contains("count_field_id")) field["count_field_id"]=ids.at(plan_uint(field.at("count_field_id"),32767));
                fields.push_back(std::move(field));
            }
        }
        Json normalized{{"schema_version",1},{"complete",true},{"name","native.graph"},{"address","0x1"},
            {"abi","win64-gp"},{"profile","engine-graph"},{"arg_count",0},{"arg_mask",0},
            {"globals",Json::array()},{"fields",std::move(fields)},{"max_bytes",max_bytes},{"max_reads",max_reads},
            {"required_projections",Json::array()}};
        auto result=std::make_unique<CompiledPlan>(compile_plan(normalized,1));
        intern(*result); return result;
    }
    std::array<std::atomic<const CaptureConfiguration*>,capture_mode_slots> current_{};
    std::vector<std::unique_ptr<CaptureConfiguration>> owned_;
    std::unordered_map<std::string,std::uint16_t> role_ids_;
    std::unordered_map<std::uint64_t,const CaptureConfiguration*> by_id_;
    std::uint64_t serial_=0;
};
} // namespace arkdps::capture
