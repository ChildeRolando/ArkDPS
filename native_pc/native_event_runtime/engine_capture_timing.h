#pragma once
#include <array>
#include <atomic>
#include <cstdint>
#include <optional>

namespace arkdps::capture {
constexpr unsigned timing_lanes=16, timing_hooks=128;
struct CallbackTimingValues {
    std::uint64_t generation=0,samples=0,ticks=0,maximum=0,reads=0,read_bytes=0,enqueued_bytes=0,drops=0;
};
struct CallbackTimingCell {
    std::atomic<std::uint64_t> sequence{0},generation{0},samples{0},ticks{0},maximum{0},reads{0},read_bytes{0},enqueued_bytes{0},drops{0};
    void record(std::uint64_t birth_generation,std::uint64_t elapsed,std::uint64_t read_count,
                std::uint64_t bytes,std::uint64_t enqueued,bool dropped) noexcept {
        sequence.fetch_add(1,std::memory_order_acq_rel);
        if(generation.load(std::memory_order_relaxed)!=birth_generation) {
            samples=0;ticks=0;maximum=0;reads=0;read_bytes=0;enqueued_bytes=0;drops=0;
            generation.store(birth_generation,std::memory_order_relaxed);
        }
        samples.store(samples.load(std::memory_order_relaxed)+1,std::memory_order_relaxed);
        ticks.store(ticks.load(std::memory_order_relaxed)+elapsed,std::memory_order_relaxed);
        if(elapsed>maximum.load(std::memory_order_relaxed))maximum.store(elapsed,std::memory_order_relaxed);
        reads.store(reads.load(std::memory_order_relaxed)+read_count,std::memory_order_relaxed);
        read_bytes.store(read_bytes.load(std::memory_order_relaxed)+bytes,std::memory_order_relaxed);
        enqueued_bytes.store(enqueued_bytes.load(std::memory_order_relaxed)+enqueued,std::memory_order_relaxed);
        drops.store(drops.load(std::memory_order_relaxed)+dropped,std::memory_order_relaxed);
        sequence.fetch_add(1,std::memory_order_release);
    }
    std::optional<CallbackTimingValues> snapshot(std::uint64_t live_generation) const noexcept {
        const auto before=sequence.load(std::memory_order_acquire);
        if(before&1) return std::nullopt;
        CallbackTimingValues value{generation.load(std::memory_order_relaxed),samples.load(std::memory_order_relaxed),
            ticks.load(std::memory_order_relaxed),maximum.load(std::memory_order_relaxed),reads.load(std::memory_order_relaxed),
            read_bytes.load(std::memory_order_relaxed),enqueued_bytes.load(std::memory_order_relaxed),drops.load(std::memory_order_relaxed)};
        std::atomic_thread_fence(std::memory_order_acquire);
        if(before!=sequence.load(std::memory_order_acquire) || value.generation!=live_generation || !value.samples)
            return std::nullopt;
        return value;
    }
};
struct CallbackTimingRegistry {
    struct alignas(64) Random { std::atomic<std::uint32_t> state{0}; };
    std::array<Random,timing_lanes> random{};
    std::array<CallbackTimingCell,timing_lanes*timing_hooks*2> cells{};
    bool select(unsigned lane) noexcept {
        auto value=random[lane].state.load(std::memory_order_relaxed);
        if(!value)value=0x9e3779b9u^(lane+1)*0x85ebca6bu;
        value^=value<<13;value^=value>>17;value^=value<<5;
        random[lane].state.store(value,std::memory_order_relaxed);
        return (value&127u)==0;
    }
    CallbackTimingCell& cell(unsigned lane,unsigned hook,bool leave) noexcept {return cells[(lane*timing_hooks+hook)*2+leave];}
};
} // namespace arkdps::capture
