#pragma once
#include "native_capture.h"
#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <optional>

namespace arkdps::capture {
// Worker-only borrowed view. All three spans must remain immutable and alive
// until reset/reinitialize. An address is an identity label, never a read permit.
class OwnedReplayView final {
public:
    struct ReadResult { Error error = Error::missing_plan_field; const std::byte* data = nullptr; };
    struct PointInfo {
        Error error = Error::missing_plan_field;
        std::uint16_t field_count = 0, single_field_id = 0;
        Error single_field_error = Error::missing_plan_field;
    };
    static bool structural(Error error) noexcept {
        return error == Error::invalid_plan || error == Error::invalid_phase || error == Error::bounds
            || error == Error::missing_plan_field || error == Error::ambiguous_read;
    }
    // The metadata may be serialized at unaligned addresses; all decoding uses
    // memcpy. No JS objects, live memory readers, strings or heap allocations.
    Error initialize(const void* segments, std::size_t segment_count,
                     const void* fields, std::size_t field_count,
                     const std::byte* payload, std::size_t payload_bytes,
                     std::uint16_t max_role_id, Error record_error = Error::ok) noexcept {
        reset();
        if (structural(record_error)) return failure_ = record_error;
        if (!known(record_error) || segment_count > max_segments || field_count > max_fields
            || payload_bytes > max_bytes || (!max_role_id && (segment_count || field_count)) || (segment_count && !segments)
            || (field_count && !fields) || (payload_bytes && !payload)) return failure_ = Error::invalid_plan;
        segments_ = static_cast<const std::byte*>(segments); fields_ = static_cast<const std::byte*>(fields);
        payload_ = payload; segment_count_ = segment_count; field_count_ = field_count; payload_bytes_ = payload_bytes;
        for (std::size_t i = 0; i < field_count; ++i) {
            const auto field = field_at(i);
            if (!field.id || !field.role || field.role > max_role_id || !known(field.error)
                || field.guard_id >= max_guards || static_cast<unsigned>(field.guard) > static_cast<unsigned>(Guard::member)
                || ((field.guard == Guard::none) != (field.guard_id == 0))
                || field.segment_begin > field.segment_end || field.segment_end > segment_count
                || (field.final_segment != 0xffff && (field.final_segment >= segment_count
                    || field.final_segment < field.segment_begin || field.final_segment >= field.segment_end))
                || !valid_range(field.attempted_address, field.attempted_size)) return failure_ = Error::invalid_plan;
            if (structural(field.error)) return failure_ = field.error;
            const auto key = field_key(field.role, field.checkpoint, field.id);
            auto slot = hash_slot(key);
            while (field_keys_[slot] && field_keys_[slot] != key) slot = (slot + 1) & (field_key_slots - 1);
            if (field_keys_[slot]) return failure_ = Error::invalid_plan;
            field_keys_[slot] = key; field_indices_[slot] = static_cast<std::uint16_t>(i);
            used_slots_[used_count_++] = static_cast<std::uint16_t>(slot);
            field_order_[i] = static_cast<std::uint16_t>(i);
        }
        std::sort(field_order_.begin(), field_order_.begin() + field_count, [&](auto a, auto b) noexcept {
            const auto x = field_at(a), y = field_at(b);
            const auto p = point_key(x.role, x.checkpoint), q = point_key(y.role, y.checkpoint);
            return p != q ? p < q : x.id < y.id;
        });
        for (std::size_t i = 0; i < field_count; ++i) {
            const auto field = field_at(field_order_[i]); const auto key = point_key(field.role, field.checkpoint);
            if (!point_count_ || points_[point_count_ - 1].key != key) {
                points_[point_count_++] = {key, static_cast<std::uint16_t>(i), static_cast<std::uint16_t>(i + 1), 0, 0, 0};
            } else points_[point_count_ - 1].field_end = static_cast<std::uint16_t>(i + 1);
        }
        for (std::size_t i = 0; i < segment_count; ++i) {
            const auto segment = segment_at(i);
            if (!segment.field_id || !segment.role || segment.role > max_role_id || segment.guard_id >= max_guards
                || segment.offset > payload_bytes || segment.size > payload_bytes - segment.offset
                || !valid_range(segment.address, segment.size)) return failure_ = Error::invalid_plan;
            const auto field_index = lookup_field(segment.role, segment.checkpoint, segment.field_id);
            if (field_index == none) return failure_ = Error::missing_plan_field;
            if (field_at(field_index).guard_id != segment.guard_id) return failure_ = Error::invalid_plan;
            segment_fields_[i] = field_index; segment_order_[i] = static_cast<std::uint16_t>(i);
        }
        std::sort(segment_order_.begin(), segment_order_.begin() + segment_count, [&](auto a, auto b) noexcept {
            const auto x = segment_at(a), y = segment_at(b);
            const auto p = point_key(x.role, x.checkpoint), q = point_key(y.role, y.checkpoint);
            if (p != q) return p < q;
            if (x.address != y.address) return x.address < y.address;
            return a < b;
        });
        std::size_t cursor = 0;
        for (std::size_t i = 0; i < point_count_; ++i) {
            auto& point = points_[i]; point.segment_begin = static_cast<std::uint16_t>(cursor);
            while (cursor < segment_count) {
                const auto segment = segment_at(segment_order_[cursor]);
                if (point_key(segment.role, segment.checkpoint) != point.key) break;
                point.max_span = (std::max)(point.max_span, segment.size); ++cursor;
            }
            point.segment_end = static_cast<std::uint16_t>(cursor);
        }
        if (cursor != segment_count) return failure_ = Error::invalid_plan;
        failure_ = Error::ok; return Error::ok;
    }
    void reset() noexcept {
        for (std::size_t i = 0; i < used_count_; ++i) field_keys_[used_slots_[i]] = 0;
        used_count_ = point_count_ = 0; segment_count_ = field_count_ = payload_bytes_ = 0;
        segments_ = fields_ = payload_ = nullptr; failure_ = Error::missing_plan_field;
    }
    PointInfo point_info(std::uint16_t role, std::uint32_t checkpoint) const noexcept {
        if (failure_ != Error::ok) return {failure_, 0, 0, failure_};
        const auto* point = lookup_point(role, checkpoint);
        if (!point) return {};
        const auto count = static_cast<std::uint16_t>(point->field_end - point->field_begin);
        if (count == 1) {
            const auto field = field_at(field_order_[point->field_begin]);
            return {Error::ok, count, field.id, field.error};
        }
        return {Error::ok, count, 0, Error::missing_plan_field};
    }
    ReadResult read(std::uint16_t role, std::uint32_t checkpoint, std::optional<std::uint16_t> field_id,
                    std::uintptr_t address, std::size_t size) const noexcept {
        if (failure_ != Error::ok) return {failure_, nullptr};
        if (size > max_bytes || !valid_range(address, size)) return {Error::bounds, nullptr};
        const auto* point = lookup_point(role, checkpoint);
        if (!point) return {};
        std::size_t low = point->segment_begin, high = point->segment_end;
        while (low < high) {
            const auto middle = (low + high) / 2;
            if (segment_at(segment_order_[middle]).address <= address) low = middle + 1; else high = middle;
        }
        const std::byte* chosen = nullptr; bool success = false, guarded = false;
        std::size_t successes = 0; Error failed = Error::ok;
        for (std::size_t i = low; i > point->segment_begin;) {
            const auto index = segment_order_[--i]; const auto segment = segment_at(index);
            const auto delta = address - segment.address;
            if (delta > point->max_span || size > point->max_span - delta) break;
            if (field_id && segment.field_id != *field_id) continue;
            if (delta > segment.size || size > segment.size - delta) continue;
            const auto state = field_at(segment_fields_[index]);
            if (state.error != Error::ok) {
                if (failed == Error::ok || state.error == Error::changed_guard) failed = state.error;
                continue;
            }
            const auto* candidate = payload_ ? payload_ + segment.offset + delta : nullptr;
            if (success && size && std::memcmp(chosen, candidate, size)) return {Error::ambiguous_read, nullptr};
            chosen = candidate; success = true; ++successes; guarded |= segment.guard_id != 0;
        }
        if (success) {
            if (!field_id && successes > 1 && guarded) return {Error::ambiguous_read, nullptr};
            return {Error::ok, chosen};
        }
        // Visited bytes of a failed field are planned unavailable, never a
        // loan of the links copied before the failure.
        if (failed != Error::ok) return {failed, nullptr};
        Error attempted = Error::ok; unsigned attempts = 0;
        for (std::size_t i = point->field_begin; i < point->field_end; ++i) {
            const auto field = field_at(field_order_[i]);
            if ((!field_id || field.id == *field_id) && field.error != Error::ok
                && covers(field.attempted_address, field.attempted_size, address, size)) {
                attempted = field.error; ++attempts;
            }
        }
        return attempts == 1 ? ReadResult{attempted, nullptr} : ReadResult{};
    }
private:
    static constexpr std::uint16_t none = 0xffff;
    struct Point {
        std::uint64_t key = 0;
        std::uint16_t field_begin = 0, field_end = 0, segment_begin = 0, segment_end = 0;
        std::uint32_t max_span = 0;
    };
    static bool known(Error error) noexcept { return static_cast<unsigned>(error) <= static_cast<unsigned>(Error::ambiguous_read); }
    static bool valid_range(std::uintptr_t address, std::size_t size) noexcept {
        return !size || size - 1 <= std::numeric_limits<std::uintptr_t>::max() - address;
    }
    static bool covers(std::uintptr_t begin, std::size_t span, std::uintptr_t address, std::size_t size) noexcept {
        return address >= begin && address - begin <= span && size <= span - (address - begin);
    }
    static std::uint64_t point_key(std::uint16_t role, std::uint32_t checkpoint) noexcept {
        return (static_cast<std::uint64_t>(role) << 32) | checkpoint;
    }
    static std::uint64_t field_key(std::uint16_t role, std::uint32_t checkpoint, std::uint16_t id) noexcept {
        return (static_cast<std::uint64_t>(role) << 48) | (static_cast<std::uint64_t>(checkpoint) << 16) | id;
    }
    static std::size_t hash_slot(std::uint64_t key) noexcept {
        key ^= key >> 33; key *= 0xff51afd7ed558ccdULL; key ^= key >> 33;
        key *= 0xc4ceb9fe1a85ec53ULL; key ^= key >> 33; return key & (field_key_slots - 1);
    }
    std::uint16_t lookup_field(std::uint16_t role, std::uint32_t checkpoint, std::uint16_t id) const noexcept {
        const auto key = field_key(role, checkpoint, id); auto slot = hash_slot(key);
        while (field_keys_[slot]) {
            if (field_keys_[slot] == key) return field_indices_[slot];
            slot = (slot + 1) & (field_key_slots - 1);
        }
        return none;
    }
    const Point* lookup_point(std::uint16_t role, std::uint32_t checkpoint) const noexcept {
        const auto key = point_key(role, checkpoint); std::size_t low = 0, high = point_count_;
        while (low < high) { const auto mid = (low + high) / 2; if (points_[mid].key < key) low = mid + 1; else high = mid; }
        return low < point_count_ && points_[low].key == key ? &points_[low] : nullptr;
    }
    FieldState field_at(std::size_t index) const noexcept {
        FieldState value; std::memcpy(&value, fields_ + index * sizeof value, sizeof value); return value;
    }
    Segment segment_at(std::size_t index) const noexcept {
        Segment value; std::memcpy(&value, segments_ + index * sizeof value, sizeof value); return value;
    }
    const std::byte *segments_ = nullptr, *fields_ = nullptr, *payload_ = nullptr;
    std::size_t segment_count_ = 0, field_count_ = 0, payload_bytes_ = 0, point_count_ = 0, used_count_ = 0;
    Error failure_ = Error::missing_plan_field;
    std::array<std::uint64_t, field_key_slots> field_keys_{};
    std::array<std::uint16_t, field_key_slots> field_indices_{};
    std::array<std::uint16_t, max_fields> used_slots_{}, field_order_{};
    std::array<std::uint16_t, max_segments> segment_order_{}, segment_fields_{};
    std::array<Point, max_fields> points_{};
};
} // namespace arkdps::capture
