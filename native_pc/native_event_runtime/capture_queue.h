#pragma once

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <optional>
#include <stdexcept>

namespace arkdps::capture {

// Exactly one non-reentrant producer and one consumer per lane. The owner must
// keep this object alive until both have stopped. Thread identity, ingress ticket
// allocation, overflow poisoning and invocation nesting belong to the caller.
// No reset/resize is provided: a new stream must own a new, quiescent lane.
class CaptureQueue final {
public:
    struct Config { std::size_t metadata_slots; std::size_t arena_bytes; };
    enum class PopResult { empty, destination_too_small, popped };

    explicit CaptureQueue(Config config)
        : slots_(checked(config.metadata_slots)), bytes_(checked(config.arena_bytes)),
          records_(new Record[slots_]{}), arena_(new std::byte[bytes_]{}) {
        // Allocation and page commitment happen before hooks become enabled.
        // Touch each byte through volatile so initialization cannot be elided.
        auto* committed = reinterpret_cast<volatile unsigned char*>(arena_.get());
        for (std::size_t i = 0; i < bytes_; ++i) committed[i] = 0;
        for (std::size_t i = 0; i < slots_; ++i) {
            auto* cell = reinterpret_cast<volatile unsigned char*>(&records_[i]);
            for (std::size_t j = 0; j < sizeof(Record); ++j) cell[j] = 0;
        }
    }

    CaptureQueue(const CaptureQueue&) = delete;
    CaptureQueue& operator=(const CaptureQueue&) = delete;
    CaptureQueue(CaptureQueue&&) = delete;
    CaptureQueue& operator=(CaptureQueue&&) = delete;

    // Producer only. Payload is copied before publication; it may be changed or
    // destroyed immediately after return. False never changes queue contents.
    // Null is permitted only for a zero-byte record. A bad non-null pointer is
    // not probed here: capture memory validation belongs to the native capturer.
    bool push(std::uint64_t ticket, const void* payload, std::size_t size) noexcept {
        if (size > bytes_ || (size && !payload)) return false;
        const auto released = released_records_.load(std::memory_order_acquire);
        if (producer_.records - released >= slots_) return false;
        const auto reclaimed = released_bytes_.load(std::memory_order_acquire);
        if (size > bytes_ - (producer_.bytes - reclaimed)) return false;

        const auto first = std::min(size, bytes_ - producer_.offset);
        if (first) std::memcpy(arena_.get() + producer_.offset, payload, first);
        if (size > first)
            std::memcpy(arena_.get(), static_cast<const std::byte*>(payload) + first, size - first);
        records_[producer_.index] = {ticket, producer_.offset, size};
        producer_.offset = advance(producer_.offset, size, bytes_);
        producer_.bytes += size;
        producer_.index = advance(producer_.index, 1, slots_);
        ++producer_.records;
        published_records_.store(producer_.records, std::memory_order_release);
        return true;
    }

    // Consumer only; does not reserve or release. Ticket zero is valid. The
    // lane preserves caller order and does not enforce global ticket ordering.
    std::optional<std::uint64_t> head_ticket() const noexcept {
        if (published_records_.load(std::memory_order_acquire) == consumer_.records)
            return std::nullopt;
        return records_[consumer_.index].ticket;
    }

    // Consumer only. On insufficient/null storage, size reports the required
    // bytes and nothing is reclaimed. Empty sets size=0 and leaves ticket alone.
    // Destination must not alias queue storage. The copied result is caller-owned.
    PopResult try_pop(std::uint64_t& ticket, void* destination,
                      std::size_t capacity, std::size_t& size) noexcept {
        if (published_records_.load(std::memory_order_acquire) == consumer_.records) {
            size = 0;
            return PopResult::empty;
        }
        const auto record = records_[consumer_.index];
        size = record.size;
        if (capacity < size || (size && !destination)) return PopResult::destination_too_small;
        const auto first = std::min(size, bytes_ - record.offset);
        if (first) std::memcpy(destination, arena_.get() + record.offset, first);
        if (size > first)
            std::memcpy(static_cast<std::byte*>(destination) + first, arena_.get(), size - first);
        ticket = record.ticket;
        consumer_.bytes += size;
        consumer_.index = advance(consumer_.index, 1, slots_);
        ++consumer_.records;
        // Copy has finished before either resource becomes reusable. Byte and
        // metadata capacity are independent; stale loads can only reject a push.
        released_bytes_.store(consumer_.bytes, std::memory_order_release);
        released_records_.store(consumer_.records, std::memory_order_release);
        return PopResult::popped;
    }

    Config capacity() const noexcept { return {slots_, bytes_}; }

private:
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free,
                  "capture callbacks require lock-free 64-bit atomics");
    struct Record { std::uint64_t ticket; std::size_t offset; std::size_t size; };
    struct Cursor {
        std::uint64_t records = 0, bytes = 0;
        std::size_t index = 0, offset = 0;
    };
    static std::size_t checked(std::size_t value) {
        if (!value || value > std::numeric_limits<std::uint64_t>::max() / 2)
            throw std::invalid_argument("capture queue capacities must be positive and below 2^63");
        return value;
    }
    static std::size_t advance(std::size_t position, std::size_t amount,
                               std::size_t capacity) noexcept {
        const auto until_end = capacity - position;
        return amount >= until_end ? amount - until_end : position + amount;
    }
    const std::size_t slots_, bytes_;
    std::unique_ptr<Record[]> records_;
    std::unique_ptr<std::byte[]> arena_;
    alignas(64) Cursor producer_;
    alignas(64) std::atomic<std::uint64_t> published_records_{0};
    alignas(64) Cursor consumer_;
    alignas(64) std::atomic<std::uint64_t> released_records_{0};
    std::atomic<std::uint64_t> released_bytes_{0};
};

} // namespace arkdps::capture
