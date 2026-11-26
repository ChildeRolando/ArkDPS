#pragma once
#include "engine_capture_config.h"
#include "engine_capture_lane.h"

namespace arkdps::capture {
inline bool publication_claimable(std::uint64_t request,unsigned attempts,unsigned active) noexcept {
    return request && attempts<2 && active==1;
}

// A failed candidate can be replayed after its host mark deadline. Keep the
// cold retry obligation across later request IDs; a new deadline is not proof
// that the stale warm graph became valid again.
inline bool cold_publication_required(std::uint64_t requested,std::uint64_t required) noexcept {
    return requested && required && requested>=required;
}
inline void require_cold_publication(std::atomic<std::uint64_t>& required,std::uint64_t request) noexcept {
    auto current=required.load(std::memory_order_acquire);
    while(request>current && !required.compare_exchange_weak(current,request,std::memory_order_acq_rel)) {}
}
inline bool clear_cold_publication(std::atomic<std::uint64_t>& required,std::uint64_t request,
                                   std::uint64_t ticket,std::uint64_t minimum_ticket,
                                   std::uint64_t scope_mode,std::uint64_t desired_mode,bool verified_closed) noexcept {
    if(!verified_closed || ticket<minimum_ticket || scope_mode!=desired_mode)return false;
    auto current=required.load(std::memory_order_acquire);
    return cold_publication_required(request,current)
        && required.compare_exchange_strong(current,0,std::memory_order_acq_rel);
}

// All pieces share one budget and guard session. In particular the original
// callback clock closes only after the scope graph has closed; independently
// finalized snapshots cannot certify a combined historical observation.
inline void capture_scope_point(const CompiledPlan& base, Phase phase,
                                const Context& context,
                                const CaptureConfiguration* config,
                                ReadMemory reader, void* user,
                                Snapshot& snapshot, RecordHeader& header, Lane* lane=nullptr, bool request_cold=false) noexcept {
    capture_begin(snapshot,phase,context,max_bytes,max_reads);
    InvocationState* invocation=lane && lane->depth ? &lane->calls[lane->depth-1] : nullptr;
    if(invocation && phase==Phase::entry) {
        // A lane stack slot is reused by unrelated hooks. HP filtering belongs
        // to this invocation, never the next callback that occupies the slot.
        invocation->hp_ignored=false;invocation->hp_before_valid=false;
        invocation->hp_exact_enemy=false;invocation->hp_nested_dirty=false;
    }
    const auto hp_value=[&](unsigned kind,std::size_t bytes) noexcept -> const std::byte* {
        for (unsigned i=0;i<snapshot.field_count;++i) {
            const auto& state=snapshot.fields[i];
            if (state.error!=Error::ok || state.final_segment>=snapshot.segment_count) continue;
            const auto& segment=snapshot.segments[state.final_segment];
            for (unsigned j=0;j<base.plan->field_count;++j)
                if (base.plan->fields[j].id==state.id && base.hp_field_kind[j]==kind && segment.size>=bytes)
                    return snapshot.bytes.data()+segment.offset;
        }
        return nullptr;
    };
    const bool publication_only=base.hp_profile&&invocation&&invocation->hp_ignored
        &&phase==Phase::leave&&header.publication_request;
    bool hp_id_captured=false;
    if (base.hp_profile && invocation) {
        if (phase==Phase::entry) {
            if (base.hp_dirty && (context.args_valid&2u) && static_cast<std::uint32_t>(context.args[1])==0)
                for (unsigned i=0;i+1<lane->depth;++i) lane->calls[i].hp_nested_dirty=true;
        }
        if (phase==Phase::entry)
            invocation->hp_ignored=base.hp_dirty && (context.args_valid&2u)
                && static_cast<std::uint32_t>(context.args[1])!=0;
        if (invocation->hp_ignored&&!publication_only) { capture_finalize(snapshot); return; }
        if (phase==Phase::entry) {
            for (std::uint16_t i=0;i<base.plan->field_count;++i)
                if (base.hp_field_kind[i]==1)
                    capture_append(*base.plan,phase,context,reader,user,snapshot,i,i+1);
            std::uintptr_t klass=0;
            const auto class_bytes=hp_value(1,8),identity=hp_value(1,0x1c);
            if(class_bytes)std::memcpy(&klass,class_bytes,8);
            bool exact_non_enemy=false;
            if(identity)for(unsigned i=0;i<base.hp_non_enemy_count;++i)
                exact_non_enemy|=klass==base.hp_non_enemy_classes[i];
            if(exact_non_enemy) {
                for(std::uint16_t i=0;i<base.plan->field_count;++i) {
                    const auto& field=base.plan->fields[i];
                    if(base.hp_field_kind[i]!=6 ||field.phase!=Phase::entry||field.kind!=Kind::utf16)continue;
                    hp_id_captured=true;
                    capture_append(*base.plan,phase,context,reader,user,snapshot,i,i+1);
                    for(unsigned j=0;j<snapshot.field_count;++j) {
                        const auto& state=snapshot.fields[j];
                        if(state.id!=field.id||state.checkpoint!=context.checkpoint||state.error!=Error::ok
                            ||state.final_segment>=snapshot.segment_count)continue;
                        const auto& text=snapshot.segments[state.final_segment];
                        if(text.field_id!=field.id||text.role!=field.role||text.checkpoint!=context.checkpoint
                            ||text.offset>snapshot.byte_count||text.size>snapshot.byte_count-text.offset||text.size%2)continue;
                        constexpr char16_t trap_prefix[]=u"trap_";
                        if(text.size<10||std::memcmp(snapshot.bytes.data()+text.offset,trap_prefix,10)!=0) {
                            invocation->hp_ignored=true;capture_finalize(snapshot);return;
                        }
                    }
                }
            }
            for (std::uint16_t i=0;i<base.plan->field_count;++i)
                if (base.hp_field_kind[i]==2)
                    capture_append(*base.plan,phase,context,reader,user,snapshot,i,i+1);
            const auto born=hp_value(2,1);
            const bool unborn=born && *born==std::byte{0};
            invocation->hp_exact_enemy=class_bytes && base.hp_enemy_class && klass==base.hp_enemy_class;
            if(identity)std::memcpy(&invocation->hp_uid,identity+0x18,4);
            if (invocation->hp_exact_enemy && unborn) {
                invocation->hp_ignored=true; capture_finalize(snapshot); return;
            }
        }
    }
    bool nochange=false;
    const auto base_stage=[&](unsigned stage) noexcept {
        if(publication_only&&stage==0)return;
        for (std::uint16_t i=0;i<base.plan->field_count;++i)
            if (base.field_stage[i]==stage && !(nochange && base.hp_field_kind[i]==5)
                && !(phase==Phase::leave && base.hp_profile && invocation && base.hp_field_kind[i]==4) && !(phase==Phase::entry && base.hp_profile && invocation
                && (base.hp_field_kind[i]==1 || base.hp_field_kind[i]==2 || (hp_id_captured&&base.hp_field_kind[i]==6))))
                capture_append(*base.plan,phase,context,reader,user,snapshot,i,i+1);
    };
    base_stage(1);
    if (phase==Phase::leave && base.hp_profile && invocation && !publication_only) {
        for (std::uint16_t i=0;i<base.plan->field_count;++i)
            if (base.hp_field_kind[i]==4)
                capture_append(*base.plan,phase,context,reader,user,snapshot,i,i+1);
        if (base.hp_normal && invocation->hp_exact_enemy && invocation->hp_before_valid
            && !invocation->hp_nested_dirty && !header.publication_request) {
            if (const auto closing=hp_value(4,0x30)) {
                std::uint32_t uid=0;std::uint64_t hp=0;
                std::memcpy(&uid,closing,4);std::memcpy(&hp,closing+0x28,8);
                nochange=uid==invocation->hp_uid && hp==invocation->hp_before;
            }
        }
    }
    const bool forced=phase==Phase::entry ? base.force_full_entry
        : phase==Phase::leave && base.force_cold_leave && (!base.force_cold_conditional
          || ((context.args_valid&(1u<<base.force_cold_arg)) && context.args[base.force_cold_arg]==base.force_cold_value));
    const bool cold=base.cold && (forced || request_cold || (header.publication_request
        && (!config || !config->scope || !config->scope->available)));
    const bool terminal_leave=phase==Phase::leave && base.terminal_destroy;
    header.projection_candidate=forced || header.publication_request || terminal_leave;
    if (cold) {
        header.cold_revision=1;
        capture_append(*base.cold->plan,Phase::checkpoint,context,reader,user,snapshot,0,1);
    }
    // The canonical normal setter defers stampEventScope until a changed
    // closing HP. time(false,false) still consumes both independent clocks.
    const bool deferred_open_scope=phase==Phase::entry && base.hp_defer_scope && invocation
        && invocation->hp_exact_enemy && !header.publication_request;
    const CaptureGraph* scope=!cold && !(nochange && base.hp_defer_scope) && !deferred_open_scope && !terminal_leave && config && config->scope && config->scope->available
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
    if (phase==Phase::entry && base.hp_profile && invocation && hp_value(1,0x1c)) {
        if (const auto before=hp_value(3,8)) {
            std::memcpy(&invocation->hp_before,before,8);invocation->hp_before_valid=true;
        }
    }
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
    const CaptureGraph* publication=!cold && !terminal_leave && header.projection_candidate && config
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
