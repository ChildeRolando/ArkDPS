#include "native_capture.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <new>
#include <type_traits>

using namespace arkdps::capture;
static std::size_t allocations = 0;
void* operator new(std::size_t size) { ++allocations; if (auto p = std::malloc(size)) return p; throw std::bad_alloc(); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }

struct Arena {
    static constexpr std::uintptr_t base = 0x10000;
    std::array<std::byte, 2048> memory{};
    unsigned reads = 0, mutate_at = 0, mutation_offset = 0;
    template<class T> void set(std::size_t offset, T value) { std::memcpy(memory.data() + offset, &value, sizeof(value)); }
    static bool read(void* context, std::uintptr_t address, void* out, std::size_t size) noexcept {
        auto& self = *static_cast<Arena*>(context);
        if (++self.reads == self.mutate_at) self.set<std::uint32_t>(self.mutation_offset, 99);
        if (address < base || address - base > self.memory.size() || size > self.memory.size() - (address - base)) return false;
        std::memcpy(out, self.memory.data() + address - base, size);
        return true;
    }
};
static HookCapturePlan plan() {
    HookCapturePlan value;
    value.arg_count = 1; value.arg_mask = 1; value.max_bytes = 2048; value.max_reads = 64;
    return value;
}
static Field field(std::uint16_t id, std::uint16_t role, std::uint32_t offset, std::uint32_t bytes) {
    Field value; value.id = id; value.role = role; value.offset = offset; value.bytes = bytes;
    return value;
}
static Context context() {
    Context result; result.args[0] = Arena::base; result.args_valid = 1; result.checkpoint = 7;
    result.invocation_id = 101; result.parent_invocation_id = 100;
    return result;
}
int main() {
    static_assert(std::is_trivially_copyable_v<Snapshot>);
    Arena arena;
    auto p = plan(); p.field_count = 1; p.fields[0] = field(1, 1, 0, 4);
    assert(validate_plan(p) == Error::ok);
    auto c = context(); Snapshot owned;
    arena.set<std::uint32_t>(0, 1234);
    const auto before = allocations;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    arena.memory.fill(std::byte{0xee});
    std::uint32_t value = 0;
    assert(read_snapshot(owned, 1, 7, 1, Arena::base, &value, 4) == Error::ok && value == 1234);
    assert(allocations == before);
    assert(owned.invocation_id == 101 && owned.parent_invocation_id == 100);
    assert(read_snapshot(owned, 2, 7, 1, Arena::base, &value, 4) == Error::missing_plan_field);
    assert(read_snapshot(owned, 1, 8, 1, Arena::base, &value, 4) == Error::missing_plan_field);
    assert(read_snapshot(owned, 1, 7, 2, Arena::base, &value, 4) == Error::missing_plan_field);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 4, &value, 4) == Error::missing_plan_field);
    assert(owned.read_count == 1 && owned.read_bytes == 4);
    assert(arena.reads == 1); // Worker cannot touch the poisoned source.

    // Independent observations at the same address retain distinct field IDs.
    Snapshot later;
    arena.set<std::uint32_t>(0, 2345); p.fields[0].id = 2;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, later) == Error::ok);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base, &value, 4) == Error::ok && value == 1234);
    assert(read_snapshot(later, 1, 7, 2, Arena::base, &value, 4) == Error::ok && value == 2345);

    // Changing a guard invalidates the guarded data even if the data read succeeded.
    p = plan(); p.field_count = 3;
    p.fields[0] = field(1, 1, 0, 4); p.fields[0].guard = Guard::before; p.fields[0].guard_id = 1;
    p.fields[1] = field(2, 2, 8, 4); p.fields[1].guard = Guard::member; p.fields[1].guard_id = 1;
    p.fields[2] = p.fields[0]; p.fields[2].id = 3; p.fields[2].role = 3; p.fields[2].guard = Guard::after;
    assert(validate_plan(p) == Error::ok);
    arena.reads = 0; arena.mutate_at = 2; arena.set<std::uint32_t>(0, 1);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::changed_guard);
    assert(read_snapshot(owned, 2, 7, 2, Arena::base + 8, &value, 4) == Error::changed_guard);
    arena.mutate_at = 0;
    p.fields[2].bytes = 8;
    assert(validate_plan(p) == Error::invalid_plan);

    // Same-address independent roles must not become an address->latest map.
    p = plan(); p.field_count = 2; p.fields[0] = field(1, 1, 0, 4); p.fields[1] = field(2, 2, 0, 4);
    arena.set<std::uint32_t>(0, 10); arena.reads = 0; arena.mutate_at = 2;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base, &value, 4) == Error::ok && value == 10);
    assert(read_snapshot(owned, 2, 7, 2, Arena::base, &value, 4) == Error::ok && value == 99);
    arena.mutate_at = 0;

    // Pointer links are owned too; no borrowed pointer dereference on replay.
    p = plan(); p.field_count = 1; p.fields[0] = field(1, 1, 0, 4); p.fields[0].depth = 1;
    arena.set<std::uintptr_t>(0, Arena::base + 64); arena.set<std::uint32_t>(64, 3456);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    arena.memory.fill(std::byte{0xff}); std::uintptr_t pointer = 0;
    assert(read_snapshot(owned, 1, 7, 1, Arena::base, &pointer, 8) == Error::ok && pointer == Arena::base + 64);
    assert(read_snapshot(owned, 1, 7, 1, pointer, &value, 4) == Error::ok && value == 3456);
    c.args[0] = Arena::base + arena.memory.size() - 1;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::field_unreadable);
    assert(read_snapshot(owned, 1, 7, 1, c.args[0], &value, 4) == Error::field_unreadable);
    c = context();
    arena.set<std::uintptr_t>(0, Arena::base + 2047);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::field_unreadable);
    assert(owned.segment_count == 1); // Pointer link succeeded; the required leaf did not.
    assert(owned.fields[0].attempted_address == Arena::base + 2047 && owned.fields[0].attempted_size == 4);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base, &pointer, 8) == Error::field_unreadable);

    // Parent checkpoint references are explicit; a missing causal parent stays unknown.
    p.fields[0].depth = 0; p.fields[0].root = Root::parent_ref;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::missing_root);
    c.parent_refs[0] = Arena::base; c.parent_valid = 1;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);

    // Dynamic UTF16 reads only its verified length, not a maximum-size block.
    p = plan(); p.field_count = 1; p.fields[0] = field(1, 1, 0, 0);
    p.fields[0].kind = Kind::utf16; p.fields[0].max_units = 200; p.fields[0].expected_class = 0x777;
    assert(validate_plan(p) == Error::ok);
    arena.memory.fill(std::byte{0}); arena.set<std::uintptr_t>(0, 0x777); arena.set<std::int32_t>(16, 2);
    arena.set<std::uint32_t>(20, 0x00420041);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(owned.byte_count == 16);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 8, &value, 4) == Error::missing_plan_field);
    arena.memory.fill(std::byte{0});
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 20, &value, 4) == Error::ok && value == 0x00420041);
    arena.set<std::uintptr_t>(0, 0x777); arena.set<std::int32_t>(16, 0);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 20, nullptr, 0) == Error::ok);
    arena.set<std::int32_t>(16, 2); arena.set<std::uint32_t>(20, 0x00420041);
    arena.reads = 0; arena.mutate_at = 4; arena.mutation_offset = 8;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(owned.read_count == 6 && owned.read_bytes == 32);
    arena.reads = 0; arena.mutate_at = 5; arena.mutation_offset = 16;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::changed_guard);
    arena.mutate_at = 0; arena.mutation_offset = 0;
    arena.set<std::int32_t>(16, 201);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::invalid_length);
    arena.set<std::int32_t>(16, 1); arena.set<std::uintptr_t>(0, 0x778);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::class_mismatch);

    // Counted arrays use an earlier guarded count, with no truncation.
    p = plan(); p.field_count = 3;
    p.fields[0] = field(1, 1, 0, 4); p.fields[0].guard_id = 1; p.fields[0].guard = Guard::before;
    p.fields[1] = field(2, 2, 32, 0); p.fields[1].kind = Kind::counted_bytes;
    p.fields[1].count_field_id = 1; p.fields[1].element_bytes = 4; p.fields[1].max_count = 128;
    p.fields[1].guard_id = 1; p.fields[1].guard = Guard::member;
    p.fields[2] = p.fields[0]; p.fields[2].id = 3; p.fields[2].role = 3; p.fields[2].guard = Guard::after;
    assert(validate_plan(p) == Error::ok);
    arena.set<std::uint32_t>(0, 2); arena.set<std::uint32_t>(32, 4567);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(read_snapshot(owned, 2, 7, 2, Arena::base + 32, &value, 4) == Error::ok && value == 4567);
    arena.set<std::uint32_t>(0, 129);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::invalid_length);
    arena.set<std::uint32_t>(0, 0);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(read_snapshot(owned, 2, 7, 2, Arena::base + 32, nullptr, 0) == Error::ok);
    p.fields[1].guard = Guard::none; p.fields[1].guard_id = 0;
    assert(validate_plan(p) == Error::invalid_plan);

    // Return and checkpoint/absolute roots are explicit and phase-restricted.
    p = plan(); p.field_count = 1; p.fields[0] = field(1, 1, 0, 4);
    p.fields[0].phase = Phase::leave; p.fields[0].root = Root::return_value;
    c.return_value = Arena::base; c.return_valid = true;
    assert(validate_plan(p) == Error::ok);
    assert(capture(p, Phase::leave, c, Arena::read, &arena, owned) == Error::ok);
    p.fields[0].phase = Phase::entry;
    assert(validate_plan(p) == Error::invalid_plan);
    p.fields[0].root = Root::checkpoint_ref;
    c.checkpoint_refs[0] = Arena::base; c.checkpoint_valid = 1;
    assert(validate_plan(p) == Error::ok);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    p.fields[0].root = Root::absolute; p.fields[0].absolute_address = Arena::base;
    assert(validate_plan(p) == Error::ok);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    p.fields[0].absolute_address = Arena::base + 2048;
    p.fields[0].required = false;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 2048, &value, 4) == Error::field_unreadable);

    // Initialize-time budgets reject a plan, never trim its evidence silently.
    p.fields[0].required = true; p.max_bytes = 1;
    assert(validate_plan(p) == Error::bounds);
    p.max_bytes = 2048; p.max_reads = 0;
    assert(validate_plan(p) == Error::invalid_plan);
    assert(allocations == before); // All capture/read/validation paths above stayed allocation-free.
    std::cout << "owned capture: poison/roles/guards/pointers/parents/strings/budgets passed\n";
}
