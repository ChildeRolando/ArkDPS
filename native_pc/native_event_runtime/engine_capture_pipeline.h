#pragma once
#include "engine_capture_config.h"
#include "engine_capture_lane.h"

namespace arkdps::capture {
// All pieces share one budget and guard session. In particular the original
// callback clock closes only after the scope graph has closed; independently
// finalized snapshots cannot certify a combined historical observation.
inline void capture_scope_point(const CompiledPlan& base, Phase phase,
                                const Context& context,
                                const CaptureConfiguration* config,
                                ReadMemory reader, void* user,
                                Snapshot& snapshot, RecordHeader& header, Lane* lane=nullptr) noexcept {
    capture_begin(snapshot,phase,context,max_bytes,max_reads);
    InvocationState* invocation=lane && lane->depth ? &lane->calls[lane->depth-1] : nullptr;
    if (base.hp_profile && invocation) {
        if (phase==Phase::entry)
            invocation->hp_ignored=base.hp_dirty && (context.args_valid&2u)
                && static_cast<std::uint32_t>(context.args[1])!=0;
        if (invocation->hp_ignored) { capture_finalize(snapshot); return; }
        if (phase==Phase::entry) {
            for (std::uint16_t i=0;i<base.plan->field_count;++i)
                if (base.hp_field_kind[i]==1 || base.hp_field_kind[i]==2)
                    capture_append(*base.plan,phase,context,reader,user,snapshot,i,i+1);
            std::uintptr_t klass=0; bool has_klass=false, unborn=false;
            for (unsigned i=0;i<snapshot.field_count;++i) {
                const auto& state=snapshot.fields[i];
                if (state.error!=Error::ok || state.final_segment>=snapshot.segment_count) continue;
                const auto& segment=snapshot.segments[state.final_segment];
                for (unsigned j=0;j<base.plan->field_count;++j) if (base.plan->fields[j].id==state.id) {
                    if (base.hp_field_kind[j]==1 && segment.size>=8) {
                        std::memcpy(&klass,snapshot.bytes.data()+segment.offset,8);has_klass=true;
                    } else if (base.hp_field_kind[j]==2 && segment.size>=1)
                        unborn=snapshot.bytes[segment.offset]==std::byte{0};
                }
            }
            if (has_klass && base.hp_enemy_class && klass==base.hp_enemy_class && unborn) {
                invocation->hp_ignored=true; capture_finalize(snapshot); return;
            }
        }
    }
    const auto base_stage=[&](unsigned stage) noexcept {
        for (std::uint16_t i=0;i<base.plan->field_count;++i)
            if (base.field_stage[i]==stage && !(phase==Phase::entry && base.hp_profile && invocation
                && (base.hp_field_kind[i]==1 || base.hp_field_kind[i]==2)))
                capture_append(*base.plan,phase,context,reader,user,snapshot,i,i+1);
    };
    base_stage(1);
    const bool forced=phase==Phase::entry ? base.force_full_entry
        : phase==Phase::leave && base.force_cold_leave && (!base.force_cold_conditional
          || ((context.args_valid&(1u<<base.force_cold_arg)) && context.args[base.force_cold_arg]==base.force_cold_value));
    const bool cold=base.cold && (forced || (header.publication_request
        && (!config || !config->scope || !config->scope->available)));
    header.projection_candidate=forced || header.publication_request;
    if (cold) {
        header.cold_revision=1;
        capture_append(*base.cold->plan,Phase::checkpoint,context,reader,user,snapshot,0,1);
    }
    const CaptureGraph* scope=!cold && config && config->scope && config->scope->available
        ? config->scope.get() : nullptr;
    Context graph_context=context;
    if (scope) {
        graph_context.checkpoint=0;
        header.configuration_id=config->id;
        header.scope_revision=scope->revision;
        header.scope_unavailable=false;
        capture_append(*scope->code->plan,Phase::checkpoint,graph_context,reader,user,
                       snapshot,0,scope->body_insertion);
    }
    base_stage(0);
    if (lane && base.parent_checkpoint_fields) {
        for (unsigned i=0;i<lane->depth;++i) {
            const auto& parent=lane->calls[i];
            if (!parent.apply_modifier || parent.id==header.invocation) continue;
            Context parent_context=context;
            parent_context.checkpoint=header.parent_checkpoint_count+1;
            parent_context.parent_refs[1]=parent.context.args[1];
            parent_context.parent_valid=(parent_context.parent_valid&~2u)|(parent.context.args_valid&2u);
            lane->parent_checkpoints[header.parent_checkpoint_count++]={parent.id,parent_context.checkpoint,0};
            capture_append(*base.plan,Phase::checkpoint,parent_context,reader,user,snapshot);
        }
    }
    const CaptureGraph* publication=!cold && header.projection_candidate && config
        && config->publication && config->publication->available ? config->publication.get() : nullptr;
    if (publication) {
        header.publication_revision=publication->revision;
        header.publication_captured=true;
        capture_append(*publication->code->plan,Phase::checkpoint,graph_context,
                       reader,user,snapshot);
    }
    if (cold)
        capture_append(*base.cold->plan,Phase::checkpoint,context,reader,user,snapshot,1,3);
    if (scope)
        capture_append(*scope->code->plan,Phase::checkpoint,graph_context,reader,user,
                       snapshot,scope->body_insertion,scope->code->plan->field_count);
    base_stage(2);
    capture_finalize(snapshot);
}
} // namespace arkdps::capture
