#pragma once
#include "capture_queue.h"
#include "capture_spill_pool.h"
#include "native_capture.h"
#include <windows.h>
#include <array>
#include <atomic>
#include <cstring>
#include <memory>

namespace arkdps::capture {
struct CaptureConfiguration;
constexpr unsigned lane_count = 16, invocation_depth = 256;
inline bool structural_error(Error error) noexcept {
    return error==Error::invalid_plan || error==Error::invalid_phase || error==Error::bounds
        || error==Error::missing_plan_field || error==Error::ambiguous_read
        || static_cast<unsigned>(error)>static_cast<unsigned>(Error::ambiguous_read);
}
// Entry must become visible to a retirement grace check before reading any
// reclaimable epoch/configuration pointer. A writer publishes its new pointer,
// executes a seq_cst fence, then checks active==0 and processed==reserved.
inline std::uint64_t pin_mode_entry(std::atomic<unsigned>& active,
                                    std::atomic<std::uint64_t>& activity,
                                    const std::atomic<std::uint64_t>& epoch) noexcept {
    active.fetch_add(1,std::memory_order_acq_rel);
    activity.fetch_add(1,std::memory_order_acq_rel);
    return epoch.load(std::memory_order_acquire);
}
enum class LaneFault : unsigned { none=0, thread_identity=1, threads_full=2, depth_full=3, queue_full=4,
    invalid_stack=5, replay_structure=6, invalid_record=7, live_replay_access=8, publication_failure=9 };
struct InvocationState {
    void* hook = nullptr;
    bool apply_modifier=false, buff_damage=false, hp_ignored=false;
    bool hp_before_valid=false, hp_exact_enemy=false, hp_nested_dirty=false;
    bool planned_hp_omission=false;
    std::uint64_t hp_before=0;
    std::uint32_t hp_uid=0;
    std::uint64_t id = 0, parent = 0, mode_epoch = 0;
    std::uintptr_t return_address = 0;
    const CaptureConfiguration* configuration=nullptr;
    Context context;
};
struct ParentCheckpoint { std::uint64_t invocation=0; std::uint32_t checkpoint=0, reserved=0; };
struct RecordHeader {
    std::uint64_t ticket = 0, invocation = 0, parent = 0, activity = 0, activity_begin = 0;
    std::uint64_t return_value = 0, thread_birth = 0, captured_qpc = 0;
    std::uint64_t publication_request = 0, mode_epoch = 0, scope_mode_epoch = 0;
    std::uint64_t configuration_id=0, scope_revision=0, publication_revision=0, cold_revision=0;
    bool scope_unavailable=true, publication_captured=false, projection_candidate=false, capture_concurrent=false;
    // Uses existing header padding. Planned telemetry omission is not a gap;
    // tickets, topology, activity and publication fences remain complete.
    bool planned_hp_omission=false;
    std::uintptr_t return_address = 0;
    std::array<std::uint64_t, 16> args{};
    std::uint32_t hook_id = 0, thread_id = 0, lane = 0, depth = 0;
    std::uint32_t checkpoint = 0, byte_count = 0, active = 0, active_after = 0;
    bool closed_after = false;
    std::uint16_t arg_mask = 0, segment_count = 0, field_count = 0, parent_checkpoint_count=0;
    Phase phase = Phase::entry;
    Error error = Error::ok;
};
static_assert(sizeof(RecordHeader)==312,"capture header changed: update its version and owned consumers");
inline bool entry_capture_concurrent(unsigned active,unsigned depth,std::uint64_t begin,std::uint64_t end,unsigned active_end) noexcept {
    return active>depth || active_end>depth || begin!=end;
}
inline bool leave_capture_concurrent(unsigned active,unsigned depth,std::uint64_t begin,std::uint64_t end,unsigned active_end) noexcept {
    return !depth || active>depth || end!=begin+1 || active_end>depth-1;
}
constexpr std::size_t record_capacity = invocation_depth*sizeof(ParentCheckpoint) + sizeof(RecordHeader) + max_segments*sizeof(Segment)
    + max_fields*sizeof(FieldState) + max_bytes;
struct Lane {
    Lane() : Lane({8192,8*1024*1024},1) {}
    Lane(CaptureQueue::Config capacity, std::uint32_t tag) : spill_owner_tag(tag), queue(capacity) {}
    ~Lane() { if (worker_thread_handle) CloseHandle(worker_thread_handle); }
    std::atomic<DWORD> owner{0};
    std::atomic<std::uint64_t> birth{0};
    std::atomic<unsigned> depth{0}, generation{0};
    std::atomic<std::uint32_t> spill_owner_tag{1};
    // Written only by this lane's producer; owner CAS validates stale hints.
    CaptureSpillPool::ProducerCursor spill_cursor;
    // Only worker reads/writes these handles; producers never OpenThread.
    HANDLE worker_thread_handle=nullptr;
    DWORD worker_handle_tid=0;
    std::uint64_t worker_handle_birth=0;
    std::array<InvocationState, invocation_depth> calls{};
    CaptureQueue queue;
    Snapshot snapshot{};
    std::array<ParentCheckpoint,invocation_depth> parent_checkpoints{};
    std::array<std::byte, record_capacity> scratch{};
    // One producer writes this cache line; only worker reads diagnostic mirrors.
    struct alignas(64) ProducerCounts {
        std::atomic<std::uint64_t> records{0}, bytes{0}, peak_records{0}, peak_bytes{0}, uncertain_records{0};
    } submitted;
    // Only worker writes this cache line; producer reads reclamation mirrors.
    struct alignas(64) ConsumerCounts {
        std::atomic<std::uint64_t> records{0}, bytes{0};
        std::uint64_t observed_pending_qpc=0, observed_pending_ticket=0;
        std::uint64_t last_age=0, maximum_age=0, age_samples=0;
    } consumed;
};
struct LaneMetricSnapshot {
    DWORD thread_id=0;
    std::uint64_t thread_birth=0;
    unsigned generation=0;
    std::uint64_t submitted=0, consumed=0, submitted_bytes=0, consumed_bytes=0;
    std::uint64_t peak_records=0, peak_bytes=0, age_samples=0, uncertain_records=0;
    bool consistent=false, age_available=false;
    std::optional<std::uint64_t> resident_records, resident_bytes, last_age, maximum_age, observed_pending_age;
};
inline void observe_pending_head(Lane& lane, std::optional<std::uint64_t> ticket, std::uint64_t now) noexcept {
    if (!ticket) { lane.consumed.observed_pending_ticket=0; lane.consumed.observed_pending_qpc=0; }
    else if (*ticket!=lane.consumed.observed_pending_ticket) {
        lane.consumed.observed_pending_ticket=*ticket; lane.consumed.observed_pending_qpc=now;
    }
}
// Worker only. Mirrors can straddle publication/reclamation; never turn an
// inconsistent observation into a known empty queue or wait for the producer.
inline LaneMetricSnapshot lane_metric_snapshot(const Lane& lane, bool timing_enabled, std::uint64_t now) noexcept {
    LaneMetricSnapshot result;
    result.thread_id=lane.owner.load(std::memory_order_acquire);
    result.thread_birth=lane.birth.load(std::memory_order_acquire);
    result.generation=lane.generation.load(std::memory_order_acquire);
    result.submitted=lane.submitted.records.load(std::memory_order_acquire);
    result.consumed=lane.consumed.records.load(std::memory_order_relaxed);
    result.submitted_bytes=lane.submitted.bytes.load(std::memory_order_acquire);
    result.consumed_bytes=lane.consumed.bytes.load(std::memory_order_relaxed);
    result.uncertain_records=lane.submitted.uncertain_records.load(std::memory_order_relaxed);
    result.consistent=result.submitted>=result.consumed && result.submitted_bytes>=result.consumed_bytes && result.uncertain_records<=result.submitted;
    if (result.consistent) {
        result.resident_records=result.submitted-result.consumed;
        result.resident_bytes=result.submitted_bytes-result.consumed_bytes;
    }
    result.peak_records=lane.submitted.peak_records.load(std::memory_order_acquire);
    result.peak_bytes=lane.submitted.peak_bytes.load(std::memory_order_acquire);
    result.age_samples=lane.consumed.age_samples;
    result.age_available=timing_enabled && result.age_samples!=0;
    if (result.age_available) { result.last_age=lane.consumed.last_age; result.maximum_age=lane.consumed.maximum_age; }
    if (timing_enabled && lane.consumed.observed_pending_ticket && lane.consumed.observed_pending_qpc<=now)
        result.observed_pending_age=now-lane.consumed.observed_pending_qpc;
    return result;
}
class CaptureLanes final {
public:
    struct Config {
        CaptureQueue::Config lane_queue{8192,8*1024*1024};
        CaptureSpillPool::Config spill{};
        std::uint32_t requested_budget_mib=0;
    };
    static Config budget_config(std::uint32_t mib) {
        if(mib<512||mib>2048)throw std::invalid_argument("capture queue budget must be 512..2048 MiB");
        Config c;c.requested_budget_mib=mib;c.lane_queue={2048,2*1024*1024};
        c.spill.metadata_slots=65536;c.spill.arena_bytes=16*1024*1024;c.spill.elastic=true;
        const auto fixed=sizeof(CaptureLanes)+lane_count*(sizeof(Lane)-capture_working_per_lane())
            +lane_count*CaptureQueue::storage_bytes(c.lane_queue);
        const auto bank=sizeof(CaptureQueue)+CaptureQueue::storage_bytes({c.spill.metadata_slots,c.spill.arena_bytes},true);
        const auto budget=static_cast<std::uint64_t>(mib)*1024*1024;
        if(budget<=fixed)throw std::invalid_argument("capture queue budget has no spill capacity");
        c.spill.banks=static_cast<std::size_t>((budget-fixed)/bank);
        if(c.spill.banks>CaptureSpillPool::max_bank_count)c.spill.banks=CaptureSpillPool::max_bank_count;
        if(c.spill.banks<c.spill.warm_banks)throw std::invalid_argument("capture queue budget cannot prepare warm banks");
        return c;
    }
    enum class Source : std::uint8_t { private_lane, spill_bank };
    struct ReadyRecord { std::uint64_t ticket=0; Source source=Source::private_lane; std::uint16_t index=0; };
    CaptureLanes() : CaptureLanes(Config{}) {}
    explicit CaptureLanes(Config config) : config_(config),spill_(config.spill) {
        for (std::size_t index=0;index<lane_count;++index) {
            auto& lane=lanes[index];
            lane = std::make_unique<Lane>(config.lane_queue,static_cast<std::uint32_t>(index+1));
            // Members are already constructed; only touch existing zeroed
            // scratch/call storage, never overwrite atomic or queue internals.
            auto* scratch = reinterpret_cast<volatile unsigned char*>(lane->scratch.data());
            for (std::size_t i=0;i<lane->scratch.size();++i) scratch[i]=0;
            auto* calls = reinterpret_cast<volatile unsigned char*>(lane->calls.data());
            for (std::size_t i=0;i<sizeof lane->calls;++i) calls[i]=0;
            auto* snapshot = reinterpret_cast<volatile unsigned char*>(&lane->snapshot);
            for (std::size_t i=0;i<sizeof lane->snapshot;++i) snapshot[i]=0;
        }
    }
    void poison(LaneFault fault) noexcept {
        unsigned zero=0;
        failure.compare_exchange_strong(zero, static_cast<unsigned>(fault), std::memory_order_relaxed);
    }
    Lane* current() noexcept {
        FILETIME created{}, exited{}, kernel{}, user{};
        if (!GetThreadTimes(GetCurrentThread(), &created, &exited, &kernel, &user)) {
            poison(LaneFault::thread_identity); return nullptr;
        }
        const auto tid=GetCurrentThreadId();
        const auto birth=(std::uint64_t(created.dwHighDateTime)<<32)|created.dwLowDateTime;
        for (auto& lane : lanes)
            if (lane->owner.load(std::memory_order_acquire)==tid && lane->birth.load(std::memory_order_relaxed)==birth) return lane.get();
        for (auto& lane : lanes) {
            DWORD zero=0;
            if (lane->owner.compare_exchange_strong(zero, MAXDWORD, std::memory_order_acquire)) {
                lane->birth.store(birth,std::memory_order_relaxed);
                lane->owner.store(tid, std::memory_order_release);
                return lane.get();
            }
        }
        poison(LaneFault::threads_full); return nullptr;
    }
    bool publish(Lane& lane, RecordHeader header) noexcept {
        if (header.lane>=lane_count || lanes[header.lane].get()!=&lane) { poison(LaneFault::invalid_record); return false; }
        return publish_to_lane(lane,header,failure,&spill_,&queue_failure_,&queue_failure_state_);
    }
    // Three-argument API remains a private-queue-only fixture/legacy entry.
    static bool publish_to_lane(Lane& lane, RecordHeader header, std::atomic<unsigned>& failure,
                                CaptureSpillPool* spill=nullptr,QueueFailureSnapshot* diagnostic=nullptr,
                                std::atomic<unsigned>* diagnostic_state=nullptr) noexcept {
        const auto& snapshot=lane.snapshot;
        if (snapshot.segment_count>max_segments || snapshot.field_count>max_fields || snapshot.byte_count>max_bytes
            || header.parent_checkpoint_count>invocation_depth) {
            unsigned zero=0; failure.compare_exchange_strong(zero,static_cast<unsigned>(LaneFault::invalid_record),std::memory_order_relaxed);
            return false;
        }
        header.segment_count=snapshot.segment_count; header.field_count=snapshot.field_count;
        header.byte_count=snapshot.byte_count; header.error=snapshot.error;
        header.checkpoint=snapshot.checkpoint;
        std::size_t offset=0;
        auto append=[&](const void* source, std::size_t size) {
            std::memcpy(lane.scratch.data()+offset, source, size); offset+=size;
        };
        append(&header, sizeof header);
        append(lane.parent_checkpoints.data(),header.parent_checkpoint_count*sizeof(ParentCheckpoint));
        append(snapshot.segments.data(), snapshot.segment_count*sizeof(Segment));
        append(snapshot.fields.data(), snapshot.field_count*sizeof(FieldState));
        append(snapshot.bytes.data(), snapshot.byte_count);
        if (!lane.queue.push(header.ticket, lane.scratch.data(), offset)
            && !(spill && spill->try_push(lane.spill_owner_tag.load(std::memory_order_relaxed),header.ticket,lane.scratch.data(),offset,&lane.spill_cursor))) {
            unsigned empty=0;
            if(diagnostic&&diagnostic_state&&diagnostic_state->compare_exchange_strong(empty,1,std::memory_order_acq_rel)) {
                diagnostic->ticket=header.ticket;diagnostic->lane=header.lane;diagnostic->attempted_bytes=offset;
                diagnostic->private_queue=lane.queue.producer_occupancy();
                if(spill)diagnostic->spill=spill->failure_snapshot(lane.spill_owner_tag.load(std::memory_order_relaxed),offset);
                diagnostic->available=true;diagnostic_state->store(2,std::memory_order_release);
            }
            unsigned zero=0; failure.compare_exchange_strong(zero,static_cast<unsigned>(LaneFault::queue_full),std::memory_order_relaxed); return false;
        }
        if(header.capture_concurrent)lane.submitted.uncertain_records.store(
            lane.submitted.uncertain_records.load(std::memory_order_relaxed)+1,std::memory_order_relaxed);
        const auto records=lane.submitted.records.load(std::memory_order_relaxed)+1;
        const auto bytes=lane.submitted.bytes.load(std::memory_order_relaxed)+offset;
        lane.submitted.records.store(records,std::memory_order_relaxed);
        lane.submitted.bytes.store(bytes,std::memory_order_relaxed);
        const auto reclaimed_records=lane.consumed.records.load(std::memory_order_acquire);
        const auto reclaimed_bytes=lane.consumed.bytes.load(std::memory_order_acquire);
        const auto resident_records=records-reclaimed_records;
        const auto resident_bytes=bytes-reclaimed_bytes;
        if (resident_records>lane.submitted.peak_records.load(std::memory_order_relaxed))
            lane.submitted.peak_records.store(resident_records,std::memory_order_relaxed);
        if (resident_bytes>lane.submitted.peak_bytes.load(std::memory_order_relaxed))
            lane.submitted.peak_bytes.store(resident_bytes,std::memory_order_relaxed);
        return true;
    }
    // Worker only. The expected raw prefix can be in any private or spill
    // bank; a later head is never treated as permission to skip a reserved ID.
    std::optional<ReadyRecord> next_ready(std::uint64_t expected) const noexcept {
        // Storage hint only. A later head never licenses skipping a ticket.
        if(last_ready_){const auto& previous=*last_ready_;
            const auto head=previous.source==Source::private_lane?lanes[previous.index]->queue.head_ticket():spill_.head_ticket(previous.index);
            if(head&&*head==expected)return ReadyRecord{expected,previous.source,previous.index};
        }
        for (std::uint16_t i=0;i<lane_count;++i) {
            const auto head=lanes[i]->queue.head_ticket();
            if (head && *head==expected) {last_ready_=ReadyRecord{expected,Source::private_lane,i};return last_ready_;}
        }
        for (std::uint16_t i=0;i<spill_.configured_banks();++i) {
            const auto head=spill_.head_ticket(i);
            if (head && *head==expected) {last_ready_=ReadyRecord{expected,Source::spill_bank,i};return last_ready_;}
        }
        return std::nullopt;
    }
    CaptureQueue::PopResult try_pop(const ReadyRecord& ready, std::uint64_t& ticket, void* destination,
                                    std::size_t capacity, std::size_t& size, Lane*& owner) noexcept {
        owner=nullptr;
        CaptureQueue::PopResult result;
        if (ready.source==Source::private_lane && ready.index<lane_count)
            result=lanes[ready.index]->queue.try_pop(ticket,destination,capacity,size);
        else if (ready.source==Source::spill_bank && ready.index<spill_.configured_banks())
            result=spill_.try_pop(ready.index,ticket,destination,capacity,size);
        else { poison(LaneFault::invalid_record); size=0; return CaptureQueue::PopResult::empty; }
        if (result!=CaptureQueue::PopResult::popped) return result;
        RecordHeader header;
        if (size<sizeof header) { poison(LaneFault::invalid_record); return result; }
        std::memcpy(&header,destination,sizeof header);
        if (ticket!=ready.ticket || header.ticket!=ticket || header.lane>=lane_count) {
            poison(LaneFault::invalid_record); return result;
        }
        auto& lane=*lanes[header.lane];
        if (header.thread_birth!=lane.birth.load(std::memory_order_acquire)
            || (ready.source==Source::private_lane && ready.index!=header.lane)
            || (ready.source==Source::spill_bank && spill_.bank_owner(ready.index)!=lane.spill_owner_tag.load(std::memory_order_acquire))) {
            poison(LaneFault::invalid_record); return result;
        }
        owner=&lane;
        if (ready.source==Source::spill_bank) spill_.reap_empty(ready.index);
        // Metrics are intentionally left to engine, exactly once per record.
        return result;
    }
    // Worker must also prove its replay invocation stack for this lane is
    // empty. Reclamation is not a timer, a TID guess or a game-thread syscall.
    bool reclaim_terminated(std::size_t index, bool replay_clear) noexcept {
        if (index>=lane_count || !replay_clear) return false;
        auto& lane=*lanes[index];
        const auto tid=lane.owner.load(std::memory_order_acquire);
        if (!tid || tid==MAXDWORD || lane.depth.load(std::memory_order_acquire)
            || lane.queue.head_ticket()) return false;
        const auto tag=lane.spill_owner_tag.load(std::memory_order_acquire);
        // Empty assigned banks still retain an owner; release these under the
        // pool's writer exclusion before deciding that a tag is quiescent.
        for (std::size_t bank=0;bank<spill_.configured_banks();++bank)
            if (spill_.bank_owner(bank)==tag) spill_.reap_empty(bank);
        if (spill_.has_owner_pending(tag)) return false;
        if (lane.submitted.records.load(std::memory_order_acquire)!=lane.consumed.records.load(std::memory_order_acquire)
            || lane.submitted.bytes.load(std::memory_order_acquire)!=lane.consumed.bytes.load(std::memory_order_acquire)) return false;
        const auto birth=lane.birth.load(std::memory_order_acquire);
        if (lane.worker_thread_handle && (lane.worker_handle_tid!=tid || lane.worker_handle_birth!=birth)) {
            CloseHandle(lane.worker_thread_handle); lane.worker_thread_handle=nullptr;
        }
        bool terminated=false;
        if (!lane.worker_thread_handle) {
            lane.worker_thread_handle=OpenThread(SYNCHRONIZE|THREAD_QUERY_LIMITED_INFORMATION,FALSE,tid);
            if (!lane.worker_thread_handle) {
                if (GetLastError()!=ERROR_INVALID_PARAMETER) return false;
                terminated=true; // No thread currently has this nonzero ID.
            } else { lane.worker_handle_tid=tid; lane.worker_handle_birth=birth; }
        }
        if (lane.worker_thread_handle) {
            FILETIME created{},exited{},kernel{},user{};
            if (!GetThreadTimes(lane.worker_thread_handle,&created,&exited,&kernel,&user)) return false;
            const auto actual=(std::uint64_t(created.dwHighDateTime)<<32)|created.dwLowDateTime;
            // A reused TID with another creation time proves the ORIGINAL
            // owner ended; it grants no access to the replacement thread.
            terminated=actual!=birth || WaitForSingleObject(lane.worker_thread_handle,0)==WAIT_OBJECT_0;
        }
        if (!terminated) return false;
        auto expected=tid;
        if (!lane.owner.compare_exchange_strong(expected,MAXDWORD,std::memory_order_acq_rel)) return false;
        if (lane.birth.load(std::memory_order_acquire)!=birth || lane.depth.load(std::memory_order_acquire)
            || lane.queue.head_ticket() || spill_.has_owner_pending(tag)) {
            lane.owner.store(tid,std::memory_order_release); return false;
        }
        const auto next=static_cast<std::uint64_t>(tag)+lane_count;
        if (next>CaptureSpillPool::owner_mask) {
            poison(LaneFault::threads_full); lane.owner.store(tid,std::memory_order_release); return false;
        }
        if (lane.worker_thread_handle) { CloseHandle(lane.worker_thread_handle); lane.worker_thread_handle=nullptr; }
        // This slot's metrics now belong only to its next birth/generation.
        lane.submitted.uncertain_records.store(0,std::memory_order_relaxed);
        lane.submitted.records.store(0,std::memory_order_relaxed); lane.submitted.bytes.store(0,std::memory_order_relaxed);
        lane.submitted.peak_records.store(0,std::memory_order_relaxed); lane.submitted.peak_bytes.store(0,std::memory_order_relaxed);
        lane.consumed.records.store(0,std::memory_order_relaxed); lane.consumed.bytes.store(0,std::memory_order_relaxed);
        lane.consumed.observed_pending_qpc=lane.consumed.observed_pending_ticket=0;
        lane.consumed.last_age=lane.consumed.maximum_age=lane.consumed.age_samples=0;
        lane.generation.fetch_add(1,std::memory_order_relaxed);
        lane.spill_cursor=CaptureSpillPool::ProducerCursor{};
        lane.spill_owner_tag.store(static_cast<std::uint32_t>(next),std::memory_order_relaxed);
        lane.birth.store(0,std::memory_order_relaxed);
        lane.owner.store(0,std::memory_order_release);
        return true;
    }
    CaptureSpillPool& spill_pool() noexcept { return spill_; }
    void maintain_spill()noexcept{spill_.maintain();}
    QueueMemoryStatus memory_status()const noexcept {
        QueueMemoryStatus s;s.enabled=config_.requested_budget_mib!=0;s.requested_budget_mib=config_.requested_budget_mib;
        s.budget_bytes=static_cast<std::uint64_t>(config_.requested_budget_mib)*1024*1024;
        const auto c=spill_.capacity();s.maximum_banks=static_cast<std::uint32_t>(c.banks);
        s.prepared_banks=spill_.prepared_banks();s.free_banks=spill_.free_banks();
        const auto fixed=sizeof(CaptureLanes)+lane_count*(sizeof(Lane)-capture_working_per_lane())+lane_count*CaptureQueue::storage_bytes(config_.lane_queue);
        const auto bank=sizeof(CaptureQueue)+CaptureQueue::storage_bytes({c.metadata_slots,c.arena_bytes},c.elastic);
        s.maximum_storage_bytes=fixed+c.banks*bank;s.prepared_storage_bytes=fixed+s.prepared_banks*bank;
        s.fixed_capture_working_bytes=lane_count*capture_working_per_lane();
        s.slot_capacity=lane_count*config_.lane_queue.metadata_slots+c.banks*c.metadata_slots;
        s.arena_capacity=lane_count*config_.lane_queue.arena_bytes+c.banks*c.arena_bytes;
        s.prepared_slot_capacity=lane_count*config_.lane_queue.metadata_slots+s.prepared_banks*c.metadata_slots;
        s.prepared_arena_capacity=lane_count*config_.lane_queue.arena_bytes+s.prepared_banks*c.arena_bytes;
        s.trimmed=spill_.trimmed();s.prepare_failures=spill_.prepare_failures();return s;
    }
    QueueFailureSnapshot queue_failure_snapshot()const noexcept {
        return queue_failure_state_.load(std::memory_order_acquire)==2?queue_failure_:QueueFailureSnapshot{};
    }
    std::array<std::unique_ptr<Lane>, lane_count> lanes;
    std::atomic<unsigned> failure{0};
    std::atomic<std::uint64_t> reserved{0};
    std::atomic<bool> enabled{false};
    // Host writes minimum then pending revision; worker clears only by CAS of
    // the exact served revision, so a concurrent fresh mark cannot be lost.
    std::atomic<std::uint64_t> request_serial{0}, pending_publication{0}, minimum_publication_ticket{0};
    std::atomic<std::uint64_t> publication_claim{0};
    std::atomic<unsigned> publication_attempts{0};
private:
    static constexpr std::size_t capture_working_per_lane()noexcept {
        return sizeof(decltype(Lane::calls))+sizeof(decltype(Lane::snapshot))
            +sizeof(decltype(Lane::parent_checkpoints))+sizeof(decltype(Lane::scratch));
    }
    const Config config_;
    CaptureSpillPool spill_;
    mutable std::optional<ReadyRecord> last_ready_;
    QueueFailureSnapshot queue_failure_;
    std::atomic<unsigned> queue_failure_state_{0};
};
inline bool capture_read(void*, std::uintptr_t address, void* target, std::size_t bytes) noexcept {
    if (!bytes) return true;
    if (!address || address+bytes<address) return false;
    SIZE_T actual=0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), target, bytes, &actual)
        && actual==bytes;
}
} // namespace arkdps::capture
