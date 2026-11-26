#pragma once
#include "capture_queue.h"
#include "native_capture.h"
#include <windows.h>
#include <array>
#include <atomic>
#include <cstring>
#include <memory>

namespace arkdps::capture {
constexpr unsigned lane_count = 16, invocation_depth = 256;
enum class LaneFault : unsigned { none, thread_identity, threads_full, depth_full, queue_full,
    invalid_stack, replay_structure, invalid_record, live_replay_access };
struct InvocationState {
    void* hook = nullptr;
    std::uint64_t id = 0, parent = 0;
    std::uintptr_t return_address = 0;
    Context context;
};
struct RecordHeader {
    std::uint64_t ticket = 0, invocation = 0, parent = 0, activity = 0, activity_begin = 0;
    std::uint64_t return_value = 0, thread_birth = 0, captured_qpc = 0;
    std::uint64_t publication_request = 0;
    std::uintptr_t return_address = 0;
    std::array<std::uint64_t, 16> args{};
    std::uint32_t hook_id = 0, thread_id = 0, lane = 0, depth = 0;
    std::uint32_t checkpoint = 0, byte_count = 0, active = 0, active_after = 0;
    bool closed_after = false;
    std::uint16_t arg_mask = 0, segment_count = 0, field_count = 0;
    Phase phase = Phase::entry;
    Error error = Error::ok;
};
constexpr std::size_t record_capacity = sizeof(RecordHeader) + max_segments*sizeof(Segment)
    + max_fields*sizeof(FieldState) + max_bytes;
struct Lane {
    std::atomic<DWORD> owner{0};
    std::uint64_t birth = 0;
    unsigned depth = 0;
    std::array<InvocationState, invocation_depth> calls{};
    CaptureQueue queue{{8192, 8*1024*1024}};
    Snapshot snapshot{};
    std::array<std::byte, record_capacity> scratch{};
    // One producer writes this cache line; only worker reads diagnostic mirrors.
    struct alignas(64) ProducerCounts {
        std::atomic<std::uint64_t> records{0}, bytes{0}, peak_records{0}, peak_bytes{0};
    } submitted;
    // Only worker writes this cache line; producer reads reclamation mirrors.
    struct alignas(64) ConsumerCounts {
        std::atomic<std::uint64_t> records{0}, bytes{0};
        std::uint64_t observed_pending_qpc=0, last_age=0, maximum_age=0;
    } consumed;
};
class CaptureLanes final {
public:
    CaptureLanes() {
        for (auto& lane : lanes) {
            lane = std::make_unique<Lane>();
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
            if (lane->owner.load(std::memory_order_acquire)==tid && lane->birth==birth) return lane.get();
        for (auto& lane : lanes) {
            DWORD zero=0;
            if (lane->owner.compare_exchange_strong(zero, MAXDWORD, std::memory_order_acquire)) {
                lane->birth=birth;
                lane->owner.store(tid, std::memory_order_release);
                return lane.get();
            }
        }
        poison(LaneFault::threads_full); return nullptr;
    }
    bool publish(Lane& lane, RecordHeader header) noexcept {
        const auto& snapshot=lane.snapshot;
        header.segment_count=snapshot.segment_count; header.field_count=snapshot.field_count;
        header.byte_count=snapshot.byte_count; header.error=snapshot.error;
        header.checkpoint=snapshot.checkpoint;
        std::size_t offset=0;
        auto append=[&](const void* source, std::size_t size) {
            std::memcpy(lane.scratch.data()+offset, source, size); offset+=size;
        };
        append(&header, sizeof header);
        append(snapshot.segments.data(), snapshot.segment_count*sizeof(Segment));
        append(snapshot.fields.data(), snapshot.field_count*sizeof(FieldState));
        append(snapshot.bytes.data(), snapshot.byte_count);
        if (!lane.queue.push(header.ticket, lane.scratch.data(), offset)) {
            poison(LaneFault::queue_full); return false;
        }
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
    std::array<std::unique_ptr<Lane>, lane_count> lanes;
    std::atomic<unsigned> failure{0};
    std::atomic<std::uint64_t> reserved{0};
    std::atomic<bool> enabled{false};
    // Host writes minimum then pending revision; worker clears only by CAS of
    // the exact served revision, so a concurrent fresh mark cannot be lost.
    std::atomic<std::uint64_t> request_serial{0}, pending_publication{0}, minimum_publication_ticket{0};
};
inline bool capture_read(void*, std::uintptr_t address, void* target, std::size_t bytes) noexcept {
    if (!bytes) return true;
    if (!address || address+bytes<address) return false;
    SIZE_T actual=0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), target, bytes, &actual)
        && actual==bytes;
}
} // namespace arkdps::capture
