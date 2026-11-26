#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace arkdps::capture {
static_assert(sizeof(std::uintptr_t) == 8, "capture plans require the verified 64-bit ABI");
constexpr std::size_t max_fields = 256, max_depth = 12, max_segments = 1024, max_bytes = 65536;
constexpr std::size_t max_roots = 16, max_string_units = 200, max_guards = 64, max_reads = 4096;
enum class Phase : std::uint8_t { entry, leave, checkpoint };
enum class Root : std::uint8_t { arg, entry_ref, parent_ref, global_slot, return_value, checkpoint_ref, absolute };
enum class Kind : std::uint8_t { bytes, utf16, counted_bytes };
enum class Guard : std::uint8_t { none, before, after, member };
enum class Error : std::uint8_t {
    ok, invalid_plan, invalid_phase, bounds, missing_root, field_unreadable,
    class_mismatch, invalid_length, changed_guard, missing_plan_field, ambiguous_read
};
struct Field {
    std::uint16_t id = 0, role = 0;
    Phase phase = Phase::entry;
    Root root = Root::arg;
    std::uint8_t root_index = 0, depth = 0;
    std::array<std::uint32_t, max_depth> pointer_offsets{};
    std::uintptr_t absolute_address = 0;
    std::uint32_t offset = 0, bytes = 0;
    Kind kind = Kind::bytes;
    std::uint16_t max_units = 0;
    std::uintptr_t expected_class = 0;
    std::uint16_t count_field_id = 0, element_bytes = 0, max_count = 0;
    std::uint8_t guard_id = 0;
    Guard guard = Guard::none;
    bool required = true;
};
struct HookCapturePlan {
    std::uint16_t schema_version = 1, field_count = 0;
    std::uint32_t max_bytes = 0, max_reads = 0;
    std::uint16_t arg_mask = 0;
    std::uint8_t arg_count = 0;
    std::array<Field, max_fields> fields{};
};
struct Context {
    std::array<std::uintptr_t, max_roots> args{}, entry_refs{}, parent_refs{}, globals{}, checkpoint_refs{};
    std::uint16_t args_valid = 0, entry_valid = 0, parent_valid = 0, globals_valid = 0, checkpoint_valid = 0;
    std::uintptr_t return_value = 0;
    bool return_valid = false;
    std::uint64_t invocation_id = 0, parent_invocation_id = 0;
    std::uint32_t checkpoint = 0;
};
struct Segment {
    std::uintptr_t address = 0;
    std::uint16_t field_id = 0, role = 0;
    std::uint32_t offset = 0, size = 0;
    std::uint8_t guard_id = 0;
};
struct FieldState {
    std::uint16_t id = 0, role = 0;
    Error error = Error::missing_plan_field;
    std::uint16_t final_segment = 0xffff;
    std::uintptr_t attempted_address = 0;
    std::uint32_t attempted_size = 0;
};
// Owned, trivially copyable record. Addresses are labels, never delayed read permissions.
struct Snapshot {
    std::uint64_t invocation_id = 0, parent_invocation_id = 0;
    std::uint32_t checkpoint = 0;
    Phase phase = Phase::entry;
    std::uint16_t segment_count = 0, field_count = 0;
    std::uint32_t byte_count = 0;
    std::uint32_t read_count = 0, read_bytes = 0; // Attempted native reads/bytes, including failed reads.
    Error error = Error::ok;
    std::array<bool, max_guards> guard_valid{};
    std::array<Segment, max_segments> segments{};
    std::array<FieldState, max_fields> fields{};
    std::array<std::byte, max_bytes> bytes{};
};
using ReadMemory = bool (*)(void*, std::uintptr_t, void*, std::size_t) noexcept;

// Initialization-time validation. No parser, native calls or arbitrary executable predicates.
Error validate_plan(const HookCapturePlan&) noexcept;
// Callback-time capture of an initialization-validated immutable plan.
// No heap, locks, exceptions, JS, queue waits or borrowed storage.
Error capture(const HookCapturePlan&, Phase, const Context&, ReadMemory, void*, Snapshot&) noexcept;
// Worker-only immutable read. Missing/invalid/ambiguous evidence is a hard error, never a live read.
Error read_snapshot(const Snapshot&, std::uint16_t role, std::uint32_t checkpoint,
                    std::uint16_t field_id, std::uintptr_t address, void*, std::size_t) noexcept;
}
