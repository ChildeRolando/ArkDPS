#pragma once
#include "engine_capture_plan.h"
#include <atomic>
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
        if (next->available) next->code=compile_graph(graph,publication);
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
            return config.get()!=latest;
        }),owned_.end());
        if (previous==owned_.size()) return nullptr;
        nlohmann::json bounds{{"mode_before",producer_epoch}};
        if (latest->scope) bounds["scope_before"]=latest->scope->revision;
        if (latest->publication) bounds["publication_before"]=latest->publication->revision;
        return bounds;
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
    std::uint64_t serial_=0;
};
} // namespace arkdps::capture
