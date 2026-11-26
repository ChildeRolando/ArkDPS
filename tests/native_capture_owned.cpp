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

template<std::size_t Capacity> struct OwnedArena {
    static constexpr std::uintptr_t base = 0x10000;
    std::array<std::byte, Capacity> memory{};
    unsigned reads = 0, mutate_at = 0, mutation_offset = 0;
    template<class T> void set(std::size_t offset, T value) { std::memcpy(memory.data() + offset, &value, sizeof(value)); }
    static bool read(void* context, std::uintptr_t address, void* out, std::size_t size) noexcept {
        auto& self = *static_cast<OwnedArena*>(context);
        if (++self.reads == self.mutate_at) self.template set<std::uint32_t>(self.mutation_offset, 99);
        if (address < base || address - base > self.memory.size() || size > self.memory.size() - (address - base)) return false;
        std::memcpy(out, self.memory.data() + address - base, size);
        return true;
    }
};
using Arena = OwnedArena<2048>;
using LargeArena = OwnedArena<65536>;
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
    static auto p = plan(); p.field_count = 1; p.fields[0] = field(1, 1, 0, 4);
    assert(validate_plan(p) == Error::ok);
    auto c = context(); static Snapshot owned;
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
    static Snapshot later;
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

    // Blackboard keys are captured by live count, preserving first-match order
    // and the matched slot value; a poisoned tail after "dynamic" is never read.
    p = plan(); p.max_bytes = 131072; p.max_reads = 4096; p.field_count = 3;
    p.fields[0] = field(1, 1, 0, 4); p.fields[0].guard_id = 1; p.fields[0].guard = Guard::before;
    p.fields[1] = field(2, 2, 64, 0); p.fields[1].kind = Kind::utf16_array;
    p.fields[1].count_field_id = 1; p.fields[1].element_bytes = 24; p.fields[1].max_count = 128;
    p.fields[1].guard_id = 1; p.fields[1].guard = Guard::member;
    p.fields[1].max_units = 200; p.fields[1].expected_class = 0x777; p.fields[1].stop_at_dynamic = true;
    p.fields[2] = p.fields[0]; p.fields[2].id = 3; p.fields[2].role = 3; p.fields[2].guard = Guard::after;
    assert(validate_plan(p) == Error::ok);
    arena.memory.fill(std::byte{0}); arena.set<std::uint32_t>(0, 2);
    arena.set<std::uintptr_t>(64, Arena::base + 256); arena.set<float>(72, 12.5f);
    arena.set<std::uintptr_t>(88, 0x90000);
    arena.set<std::uintptr_t>(256, 0x777); arena.set<std::uint32_t>(272, 7);
    constexpr char16_t dynamic_key[] = u"dynamic";
    std::memcpy(arena.memory.data() + 276, dynamic_key, 14);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    float shield = 0;
    arena.memory.fill(std::byte{0xaa});
    assert(read_snapshot(owned, 2, 7, 2, Arena::base + 72, &shield, 4) == Error::ok && shield == 12.5f);
    assert(read_snapshot(owned, 2, 7, 2, Arena::base + 88, &pointer, 8) == Error::missing_plan_field);
    assert(read_snapshot(owned, 2, 7, 2, Arena::base + 276, &value, 4) == Error::ok);
    // Turning early stop off exposes the real unreadable second key, not a
    // fabricated null or an instruction to dereference that key later.
    arena.memory.fill(std::byte{0}); arena.set<std::uint32_t>(0, 2);
    arena.set<std::uintptr_t>(64, Arena::base + 256); arena.set<std::uintptr_t>(88, 0x90000);
    arena.set<std::uintptr_t>(256, 0x777); arena.set<std::uint32_t>(272, 7);
    std::memcpy(arena.memory.data() + 276, dynamic_key, 14);
    p.fields[1].stop_at_dynamic = false;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::field_unreadable);
    assert(owned.fields[1].attempted_address == 0x90000 && owned.fields[1].attempted_size == 8);
    p.fields[1].max_count = 129;
    assert(validate_plan(p) == Error::invalid_plan);

    // The original 128 x 200 legal maximum fits the declared 128KiB read budget.
    p.fields[1].max_count = 128; p.fields[1].stop_at_dynamic = true;
    LargeArena large;
    large.set<std::uint32_t>(0, 128);
    for (std::size_t i = 0; i < 128; ++i) {
        const auto string_offset = 4096 + i * 424;
        large.set<std::uintptr_t>(64 + i * 24, LargeArena::base + string_offset);
        large.set<std::uintptr_t>(string_offset, 0x777);
        large.set<std::uint32_t>(string_offset + 16, 200);
        std::memset(large.memory.data() + string_offset + 20, 0x41, 400);
    }
    assert(validate_plan(p) == Error::ok);
    assert(capture(p, Phase::entry, c, LargeArena::read, &large, owned) == Error::ok);
    assert(owned.read_count == 1026 && owned.read_bytes == 111624 && owned.byte_count == 55816);
    p.max_bytes = 111623;
    assert(validate_plan(p) == Error::bounds);

    // Cold projection starts from the CURRENT root and expands the CURRENT
    // count/slots. Mixed dictionary values expose class only until typed.
    p = plan(); p.max_bytes = 131072; p.max_reads = 4096;
    p.field_count = 1; p.fields[0] = field(1, 1, 0, 0);
    p.fields[0].kind = Kind::projection_tree; p.projection_count = 3;
    auto& collection = p.projections[0]; collection.kind = ProjectionKind::array;
    collection.count_offset = 0; collection.items_offset = 8; collection.data_offset = 0;
    collection.element_bytes = 16; collection.max_count = 2;
    collection.child_begin = 1; collection.child_count = 1;
    auto& object = p.projections[1]; object.depth = 1; object.pointer_offsets[0] = 8;
    object.gate = TypeGate::exact; object.expected_class = 0x777; object.bytes = 16;
    object.max_visits = 2; object.child_begin = 2; object.child_count = 1;
    auto& text_node = p.projections[2]; text_node.kind = ProjectionKind::utf16;
    text_node.depth = 1; text_node.pointer_offsets[0] = 8; text_node.expected_class = 0x888;
    text_node.max_units = 8; text_node.max_visits = 2;
    assert(validate_plan(p) == Error::ok);
    arena.memory.fill(std::byte{0}); arena.set<std::uint32_t>(0, 2);
    arena.set<std::uintptr_t>(8, Arena::base + 64);
    arena.set<std::uintptr_t>(72, Arena::base + 256);
    arena.set<std::uintptr_t>(88, Arena::base + 384);
    arena.set<std::uintptr_t>(256, 0x777); arena.set<std::uintptr_t>(264, Arena::base + 512);
    arena.set<std::uintptr_t>(384, 0x999); // Not the projected class: +8 is deliberately poisoned.
    arena.set<std::uintptr_t>(392, 0x90000);
    arena.set<std::uintptr_t>(512, 0x888); arena.set<std::int32_t>(528, 2);
    arena.set<char16_t>(532, u'A'); arena.set<char16_t>(534, u'B');
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    arena.memory.fill(std::byte{0xdd});
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 532, &value, 4) == Error::ok);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 392, &pointer, 8) == Error::missing_plan_field);
    // Tombstones retain and double-guard their complete slot but cannot
    // trigger a dereference of its stale nonnull value pointer.
    arena.memory.fill(std::byte{0}); arena.set<std::uint32_t>(0, 1);
    arena.set<std::uintptr_t>(8, Arena::base + 64);
    arena.set<std::int32_t>(64, -1); arena.set<std::uintptr_t>(72, 0x90000);
    collection.skip_negative_i32_offset = 0;
    assert(validate_plan(p) == Error::ok);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, later) == Error::ok);
    assert(read_snapshot(later, 1, 7, 1, Arena::base + 72, &pointer, 8) == Error::ok && pointer == 0x90000);
    collection.skip_negative_i32_offset = -1;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, later) == Error::field_unreadable);
    assert(later.fields[0].attempted_address == 0x90000);
    collection.skip_negative_i32_offset = 14;
    assert(validate_plan(p) == Error::invalid_plan);
    collection.skip_negative_i32_offset = -1;
    // A new root with a new graph must be captured afresh, never by re-stamping
    // this snapshot or reading a cached oldscope body.
    c.args[0] = Arena::base + 1024;
    arena.set<std::uint32_t>(1024, 0); arena.set<std::uintptr_t>(1032, 0);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, later) == Error::ok);
    assert(read_snapshot(later, 1, 7, 1, Arena::base + 532, &value, 4) == Error::missing_plan_field);
    c.args[0] = Arena::base;
    // Changed local count invalidates the entire cold graph, not only count.
    arena.memory.fill(std::byte{0}); arena.set<std::uint32_t>(0, 0);
    arena.reads = 0; arena.mutate_at = 3; arena.mutation_offset = 0;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::changed_guard);
    arena.mutate_at = 0;
    assert(read_snapshot(owned, 1, 7, 1, Arena::base, &value, 4) == Error::changed_guard);
    // A domain total-visits bound rejects the whole graph instead of
    // silently truncating the second live slot and publishing a smaller roster.
    object.max_visits = 1; arena.memory.fill(std::byte{0});
    arena.set<std::uint32_t>(0, 2); arena.set<std::uintptr_t>(8, Arena::base + 64);
    arena.set<std::uintptr_t>(72, Arena::base + 256); arena.set<std::uintptr_t>(88, Arena::base + 384);
    arena.set<std::uintptr_t>(256, 0x999); arena.set<std::uintptr_t>(384, 0x999);
    assert(validate_plan(p) == Error::ok);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::bounds);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 64, &pointer, 8) == Error::bounds);
    arena.set<std::uint32_t>(0, 3);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::invalid_length);
    // Bounded ancestry is a layout gate only: unrelated short Unit objects
    // never expose Character data; derived Character objects do.
    p.projection_count = 1; auto& unit = p.projections[0]; unit = ProjectionNode{};
    unit.gate = TypeGate::ancestor; unit.expected_class = Arena::base + 768;
    unit.ancestry_depth = 10; unit.bytes = 8;
    arena.memory.fill(std::byte{0}); arena.set<std::uintptr_t>(0, Arena::base + 640);
    arena.set<std::uintptr_t>(640 + 0x58, Arena::base + 768);
    assert(validate_plan(p) == Error::ok);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base + 640 + 0x58, &pointer, 8) == Error::ok
        && pointer == Arena::base + 768);
    unit.ancestry_depth = 11;
    assert(validate_plan(p) == Error::invalid_plan);
    unit.ancestry_depth = 10; unit.child_begin = 0; unit.child_count = 1;
    assert(validate_plan(p) == Error::invalid_plan); // Cycles/reusing the same node are forbidden.
    unit.child_count = 0; unit.max_visits = 257;
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

    // One raw record brackets independent parent contexts with the same
    // field IDs but distinct checkpoints; budgets and guards span appends.
    p = plan(); p.field_count = 3;
    p.fields[0] = field(1, 1, 0, 4); p.fields[0].guard_id = 3000; p.fields[0].guard = Guard::before;
    p.fields[1] = field(2, 2, 8, 4); p.fields[1].guard_id = 3000; p.fields[1].guard = Guard::member;
    p.fields[2] = p.fields[0]; p.fields[2].id = 3; p.fields[2].guard = Guard::after;
    assert(validate_plan(p) == Error::ok);
    static auto parent_plan = plan(); parent_plan.field_count = 1;
    parent_plan.fields[0] = field(7, 7, 0, 4); parent_plan.fields[0].root = Root::parent_ref;
    arena.memory.fill(std::byte{0}); arena.set<std::uint32_t>(0, 10);
    arena.set<std::uint32_t>(64, 123); arena.set<std::uint32_t>(128, 456);
    assert(capture_begin(owned, Phase::entry, c, 64, 8) == Error::ok);
    assert(capture_append(p, Phase::entry, c, Arena::read, &arena, owned, 0, 2) == Error::ok);
    assert(read_snapshot(owned, 1, 7, 1, Arena::base, &value, 4) == Error::missing_plan_field);
    auto parent_context = c; parent_context.parent_valid = 1; parent_context.checkpoint = 101;
    parent_context.parent_refs[0] = Arena::base + 64;
    assert(capture_append(parent_plan, Phase::entry, parent_context, Arena::read, &arena, owned) == Error::ok);
    parent_context.checkpoint = 102; parent_context.parent_refs[0] = Arena::base + 128;
    assert(capture_append(parent_plan, Phase::entry, parent_context, Arena::read, &arena, owned) == Error::ok);
    assert(capture_append(p, Phase::entry, c, Arena::read, &arena, owned, 2, 3) == Error::ok);
    assert(capture_finalize(owned) == Error::ok && owned.read_count == 5 && owned.read_bytes == 20);
    assert(read_snapshot(owned, 7, 101, 7, Arena::base + 64, &value, 4) == Error::ok && value == 123);
    assert(read_snapshot(owned, 7, 102, 7, Arena::base + 128, &value, 4) == Error::ok && value == 456);
    assert(read_snapshot(owned, 7, 101, 7, Arena::base + 128, &value, 4) == Error::missing_plan_field);
    assert(capture_append(parent_plan, Phase::entry, c, Arena::read, &arena, owned) == Error::invalid_plan);
    // Total raw budget is not reset for the second parent.
    assert(capture_begin(owned, Phase::entry, c, 4, 1) == Error::ok);
    assert(capture_append(parent_plan, Phase::entry, parent_context, Arena::read, &arena, owned) == Error::ok);
    parent_context.checkpoint = 103;
    assert(capture_append(parent_plan, Phase::entry, parent_context, Arena::read, &arena, owned) == Error::bounds);
    assert(capture_finalize(owned) == Error::bounds && owned.read_count == 1);
    // Missing closing outer guard cannot authenticate an earlier body slice.
    assert(capture_begin(owned, Phase::entry, c, 64, 8) == Error::ok);
    assert(capture_append(p, Phase::entry, c, Arena::read, &arena, owned, 0, 2) == Error::ok);
    assert(capture_finalize(owned) == Error::changed_guard);
    assert(read_snapshot(owned, 2, 7, 2, Arena::base + 8, &value, 4) == Error::changed_guard);
    // Reusing the scratch for a tiny HP plan visits no unused guard tail.
    owned.guard_valid[4095] = false;
    assert(capture(parent_plan, Phase::entry, parent_context, Arena::read, &arena, owned) == Error::ok);
    assert(owned.guard_count == 0 && !owned.guard_seen[3000] && !owned.guard_valid[4095]);

    // Publication-size field batches use a half-loaded fixed hash rather
    // than rescanning every prior field on the native thread.
    p = plan(); p.field_count = 4000; p.max_bytes = 20000; p.max_reads = 5000;
    for (std::size_t i = 0; i < p.field_count; ++i) p.fields[i] = field(static_cast<std::uint16_t>(i + 1), 1, 0, 4);
    assert(validate_plan(p) == Error::ok);
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(owned.field_key_count == 4000 && owned.field_key_probes < 8000);
    const auto probes = owned.field_key_probes;
    p.field_count = 1;
    assert(capture(p, Phase::entry, c, Arena::read, &arena, owned) == Error::ok);
    assert(owned.field_key_count == 1 && owned.field_key_probes == 1 && probes > 4000);

    // Initialize-time budgets reject a plan, never trim its evidence silently.
    p.fields[0].required = true; p.max_bytes = 1;
    assert(validate_plan(p) == Error::bounds);
    p.max_bytes = 2048; p.max_reads = 0;
    assert(validate_plan(p) == Error::invalid_plan);
    assert(allocations == before); // All capture/read/validation paths above stayed allocation-free.
    std::cout << "owned capture: poison/roles/guards/pointers/parents/strings/budgets passed field4000_probes=" << probes << "\n";
}
