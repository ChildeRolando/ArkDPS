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
                                Snapshot& snapshot, RecordHeader& header) noexcept {
    capture_begin(snapshot,phase,context,max_bytes,max_reads);
    const auto base_stage=[&](unsigned stage) noexcept {
        for (std::uint16_t i=0;i<base.plan->field_count;++i)
            if (base.field_stage[i]==stage)
                capture_append(*base.plan,phase,context,reader,user,snapshot,i,i+1);
    };
    base_stage(1);
    const CaptureGraph* scope=config && config->scope && config->scope->available
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
    const CaptureGraph* publication=header.publication_request && config
        && config->publication && config->publication->available ? config->publication.get() : nullptr;
    if (publication) {
        header.publication_revision=publication->revision;
        header.publication_captured=true;
        capture_append(*publication->code->plan,Phase::checkpoint,graph_context,
                       reader,user,snapshot);
    }
    if (scope)
        capture_append(*scope->code->plan,Phase::checkpoint,graph_context,reader,user,
                       snapshot,scope->body_insertion,scope->code->plan->field_count);
    base_stage(2);
    capture_finalize(snapshot);
}
} // namespace arkdps::capture
