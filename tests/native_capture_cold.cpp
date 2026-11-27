#include "native_capture.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <new>
using namespace arkdps::capture;
static std::size_t allocations = 0;
void* operator new(std::size_t n) { ++allocations; if (auto p = std::malloc(n)) return p; throw std::bad_alloc(); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void* operator new[](std::size_t n) { return ::operator new(n); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
#include "cold_plan_fixture.inc"
struct Arena {
    static constexpr std::uintptr_t base = 0x10000;
    std::array<std::byte, 8 * 1024 * 1024> memory{};
    std::uintptr_t next = 0x40000;
    template<class T> T get(std::uintptr_t address) const {
        T value; assert(address >= base && address - base + sizeof(T) <= memory.size());
        std::memcpy(&value, memory.data() + address - base, sizeof(T)); return value;
    }
    template<class T> void put(std::uintptr_t address, T value) {
        assert(address >= base && address - base + sizeof(T) <= memory.size());
        std::memcpy(memory.data() + address - base, &value, sizeof(T));
    }
    std::uintptr_t reserve(std::size_t size = 2048) {
        const auto result = next; next += (size + 15) & ~std::size_t(15);
        assert(next - base <= memory.size()); return result;
    }
    static bool read(void* user, std::uintptr_t address, void* target, std::size_t size) noexcept {
        const auto& a = *static_cast<const Arena*>(user);
        if (address < base || address - base > a.memory.size() || size > a.memory.size() - (address - base)) return false;
        std::memcpy(target, a.memory.data() + address - base, size); return true;
    }
};
static Arena arena;
static HookCapturePlan plan;
static Snapshot snapshot;
static std::array<unsigned, max_projection_nodes> visits{};
static void materialize(std::uint16_t index, std::uintptr_t base) {
    const auto& node = plan.projections[index];
    const auto ordinal = visits[index]++;
    if (node.parent_byte_offset >= 0) arena.put<std::uint8_t>(base + node.parent_byte_offset, node.parent_byte_value);
    auto address = base;
    for (std::size_t i = 0; i < node.depth; ++i) {
        const auto link = address + node.pointer_offsets[i];
        auto value = arena.get<std::uintptr_t>(link);
        if (!value) { value = arena.reserve(); arena.put(link, value); }
        address = value;
    }
    address += node.offset;
    if (node.gate != TypeGate::none) {
        // A real dictionary can hold 31 unrelated values and one manager.
        if (index == 13 && ordinal) { arena.put<std::uintptr_t>(address, 0x22222); return; }
        if (node.gate == TypeGate::ancestor) {
            // Maximum ten-class lineage, with Character at the last class.
            arena.put<std::uintptr_t>(address, 0x31000);
            for (unsigned i = 0; i < 9; ++i)
                arena.put<std::uintptr_t>(0x31000 + i * 0x100 + 0x58,
                    i == 8 ? node.expected_class : 0x31000 + (i + 1) * 0x100);
        } else arena.put(address, node.expected_class);
    }
    if (node.kind == ProjectionKind::utf16) {
        arena.put(address, node.expected_class); arena.put<std::int32_t>(address + 16, node.max_units);
        for (std::size_t i = 0; i < node.max_units; ++i) arena.put<char16_t>(address + 20 + i * 2, u'X');
    } else if (node.kind == ProjectionKind::array) {
        // Four x 64 ChessInst is the supported total 256; eight players and
        // maximum 128 live Unit objects exist independently in this graph.
        const std::uint32_t count = index == 31 ? 4 : node.max_count;
        arena.put(address + node.count_offset, count);
        auto items = arena.get<std::uintptr_t>(address + node.items_offset);
        if (!items) { items = arena.reserve(node.data_offset + node.element_bytes * count + 32); arena.put(address + node.items_offset, items); }
        // Existing array-header sibling can have created a 2048-byte object;
        // every actual array in this fixture needs at most 1056 bytes.
        for (std::size_t i = 0; i < count; ++i) {
            const auto slot = items + node.data_offset + i * node.element_bytes;
            if (node.skip_negative_i32_offset >= 0) arena.put<std::int32_t>(slot + node.skip_negative_i32_offset, 1);
            for (std::size_t j = 0; j < node.child_count; ++j) materialize(static_cast<std::uint16_t>(node.child_begin + j), slot);
        }
    } else {
        for (std::size_t i = 0; i < node.child_count; ++i) materialize(static_cast<std::uint16_t>(node.child_begin + i), address);
    }
}
static const FieldState& state(std::uint16_t id) {
    for (std::size_t i = 0; i < snapshot.field_count; ++i)
        if (snapshot.fields[i].id == id) return snapshot.fields[i];
    std::abort();
}
int main() {
    load_cold_plan(plan);
    assert(plan.projection_count == 52);
    plan.max_bytes = 131072;
    assert(validate_plan(plan) == Error::bounds); // Previous cap cannot fit the real complete plan.
    plan.max_bytes = 1048576;
    const auto valid = validate_plan(plan);
    if (valid != Error::ok) { std::cout << "validate error " << unsigned(valid) << '\n'; return 1; }
    for (std::size_t i = 0; i < plan.field_count; ++i) materialize(plan.fields[i].projection_root, plan.fields[i].absolute_address);
    assert(visits[35] == 256 && visits[45] == 128 && visits[40] == 256 && visits[50] == 128);
    Context context; context.checkpoint = 4;
    const auto before = allocations;
    const bool has_boundaries = plan.projections[35].local_fail_boundary;
    const auto captured = capture(plan, Phase::checkpoint, context, Arena::read, &arena, snapshot);
    if (captured != Error::ok || state(2).error != Error::ok) {
        std::cout << "capture error " << unsigned(captured) << " field " << unsigned(state(2).error)
            << " address " << state(2).attempted_address << " bytes " << snapshot.read_bytes << '\n'; return 2;
    }
    if (has_boundaries) {
        assert(snapshot.field_count == 388);
        assert(snapshot.next_dynamic_field_id - 1 == 33152);
        assert(state(33152).checkpoint == 4 && state(33152).error == Error::ok);
    }
    assert(snapshot.read_bytes > 400000 && snapshot.read_count > 10000 && snapshot.segment_count > 4000);
    assert(snapshot.read_bytes <= max_bytes && snapshot.read_count <= max_reads && snapshot.segment_count <= max_segments);
    const auto read_bytes = snapshot.read_bytes, read_count = snapshot.read_count;
    // One over the domain visit bound makes the optional cold body UNKNOWN;
    // no truncated membership/zero baseline is exposed as a successful field.
    plan.projections[35].max_visits = 255;
    assert(capture(plan, Phase::checkpoint, context, Arena::read, &arena, snapshot) == Error::ok);
    assert(state(2).error == Error::bounds);
    plan.projections[35].max_visits = 256;
    assert(capture(plan, Phase::checkpoint, context, Arena::read, &arena, snapshot) == Error::ok);
    assert(state(2).error == Error::ok);
    const auto owned_bytes = snapshot.byte_count; const auto segments = snapshot.segment_count;
    // Post-capture poisoning never changes a readable owned CID.
    const auto& cid = plan.projections[40]; (void)cid;
    const auto& segment = snapshot.segments[snapshot.segment_count - 1];
    std::array<std::byte, 128> value{}; const auto n = segment.size < value.size() ? segment.size : value.size();
    arena.memory.fill(std::byte{0xcc});
    assert(read_snapshot(snapshot, segment.role, 4, segment.field_id, segment.address, value.data(), n) == Error::ok);
    assert(allocations == before);
    std::cout << "cold384: reads=" << read_count << " read_bytes=" << read_bytes
        << " last_dynamic_id=" << (snapshot.next_dynamic_field_id - 1) << " owned_bytes=" << owned_bytes << " segments=" << segments << " scratch_bytes=" << sizeof(Snapshot) << " noheap=1\n";
}

