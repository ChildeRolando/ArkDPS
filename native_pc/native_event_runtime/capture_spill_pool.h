#pragma once
#include "capture_queue.h"
#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <optional>

namespace arkdps::capture {
// Spill banks are additional bounded storage, not a second ticket allocator.
// Producers never wait for a borrower or consumer and never move queued data.
class CaptureSpillPool final {
public:
    static constexpr std::size_t bank_count = 4;
    static constexpr std::uint32_t busy = 0x80000000u, releasing = 0x40000000u;
    static constexpr std::uint32_t owner_mask = releasing - 1;
    struct Config { std::size_t metadata_slots = 32768, arena_bytes = 24 * 1024 * 1024; };
    CaptureSpillPool() : CaptureSpillPool(Config{}) {}
    explicit CaptureSpillPool(Config config) : config_(config) {
        // CaptureQueue allocates and pre-touches both its record and byte arenas.
        for (auto& bank : banks_) bank = std::make_unique<Bank>(config);
    }
    CaptureSpillPool(const CaptureSpillPool&) = delete;
    CaptureSpillPool& operator=(const CaptureSpillPool&) = delete;

    // Exactly bounded to two scans of four banks. Existing owned banks are
    // preferred, allowing one hot lane to borrow all four without identifying
    // a main thread. Tags identify lanes in this stream, never OS thread IDs.
    bool try_push(std::uint32_t owner_tag, std::uint64_t ticket,
                  const void* payload, std::size_t size) noexcept {
        if (!owner_tag || owner_tag > owner_mask || size > config_.arena_bytes || (size && !payload)) return false;
        for (unsigned pass = 0; pass < 2; ++pass) {
            for (auto& bank : banks_) {
                auto expected = pass == 0 ? owner_tag : 0u;
                if (!bank->owner.compare_exchange_strong(expected, owner_tag | busy,
                                                        std::memory_order_acq_rel, std::memory_order_relaxed)) continue;
                const bool pushed = bank->queue.push(ticket, payload, size);
                // Publishes producer cursor ownership as well as the record.
                // A new borrower acquires through the reaper's release of free.
                bank->owner.store(owner_tag, std::memory_order_release);
                if (pushed) return true;
            }
        }
        return false;
    }
    // Only the single worker calls the consumer/reaper APIs. Engine merges
    // these heads with private lane queues using its existing global tickets.
    std::optional<std::uint64_t> head_ticket(std::size_t index) const noexcept {
        return index < bank_count ? banks_[index]->queue.head_ticket() : std::nullopt;
    }
    CaptureQueue::PopResult try_pop(std::size_t index, std::uint64_t& ticket, void* destination,
                                   std::size_t capacity, std::size_t& size) noexcept {
        if (index >= bank_count) { size = 0; return CaptureQueue::PopResult::empty; }
        return banks_[index]->queue.try_pop(ticket, destination, capacity, size);
    }
    bool reap_empty(std::size_t index) noexcept {
        if (index >= bank_count) return false;
        auto& bank = *banks_[index];
        // Avoid taking the producer's owner cache line for every consumed
        // record in a nonempty bank. This is only an early refusal: emptiness
        // must still be proved again AFTER the writer-excluding CAS below.
        if (bank.queue.head_ticket()) return false;
        auto owner = bank.owner.load(std::memory_order_acquire);
        if (!owner || (owner & (busy | releasing))) return false;
        const auto previous = owner;
        if (!bank.owner.compare_exchange_strong(owner, previous | releasing,
                                               std::memory_order_acq_rel, std::memory_order_relaxed)) return false;
        // Re-check only AFTER excluding writers. Checking empty before the CAS
        // would allow a just-published backlog to be reassigned to a new lane.
        if (bank.queue.head_ticket()) {
            bank.owner.store(previous, std::memory_order_release);
            return false;
        }
        bank.owner.store(0, std::memory_order_release);
        return true;
    }
    std::uint32_t owner_state(std::size_t index) const noexcept {
        return index < bank_count ? banks_[index]->owner.load(std::memory_order_acquire) : 0;
    }
    std::uint32_t bank_owner(std::size_t index) const noexcept { return owner_state(index) & owner_mask; }
    bool has_owner_pending(std::uint32_t owner_tag) const noexcept {
        if (!owner_tag || owner_tag > owner_mask) return false;
        for (std::size_t i = 0; i < bank_count; ++i) if (bank_owner(i) == owner_tag) return true;
        return false; // Assigned but empty/busy/releasing banks still pin their tag above.
    }
    Config capacity() const noexcept { return config_; }
private:
    struct Bank {
        explicit Bank(Config config) : queue({config.metadata_slots, config.arena_bytes}) {}
        alignas(64) std::atomic<std::uint32_t> owner{0};
        CaptureQueue queue;
    };
    const Config config_;
    std::array<std::unique_ptr<Bank>, bank_count> banks_;
};
static_assert(std::atomic<std::uint32_t>::is_always_lock_free,
              "spill bank ownership requires lock-free atomics");
} // namespace arkdps::capture
