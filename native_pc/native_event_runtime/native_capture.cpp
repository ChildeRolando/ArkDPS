#include "native_capture.h"
#include <cstring>
#include <limits>

namespace arkdps::capture {
namespace {
bool phase_ok(Phase phase) noexcept { return phase == Phase::entry || phase == Phase::leave || phase == Phase::checkpoint; }
bool address_add(std::uintptr_t base, std::size_t offset, std::uintptr_t& out) noexcept {
    if (offset > std::numeric_limits<std::uintptr_t>::max() - base) return false;
    out = base + offset;
    return true;
}
bool compatible(const Field& a, const Field& b) noexcept {
    return a.kind == b.kind && a.root == b.root && a.root_index == b.root_index
        && a.absolute_address == b.absolute_address && a.depth == b.depth && a.pointer_offsets == b.pointer_offsets && a.offset == b.offset
        && a.bytes == b.bytes && a.max_units == b.max_units && a.expected_class == b.expected_class
        && a.count_field_id == b.count_field_id && a.element_bytes == b.element_bytes && a.max_count == b.max_count
        && a.element_pointer_offset == b.element_pointer_offset && a.stop_at_dynamic == b.stop_at_dynamic
        && a.projection_root == b.projection_root;
}
struct Writer {
    Snapshot& out;
    ReadMemory reader;
    void* user;
    std::uint32_t byte_budget, read_budget, checkpoint;
    std::uintptr_t attempted_address = 0;
    std::uint32_t attempted_size = 0;
    Error raw(std::uintptr_t address, void* target, std::size_t size) noexcept {
        attempted_address = address; attempted_size = static_cast<std::uint32_t>(size);
        if (!size) return Error::ok;
        if (out.read_count >= read_budget || size > byte_budget - out.read_bytes) return Error::bounds;
        ++out.read_count; out.read_bytes += static_cast<std::uint32_t>(size);
        return reader && reader(user, address, target, size) ? Error::ok : Error::field_unreadable;
    }
    Error copy(const Field& field, std::uintptr_t address, std::size_t size) noexcept {
        if (!address) return Error::missing_root;
        std::uintptr_t end;
        if (!address_add(address, size, end)) return Error::bounds;
        if (out.segment_count >= max_segments || size > max_bytes - out.byte_count) return Error::bounds;
        const auto result = raw(address, out.bytes.data() + out.byte_count, size);
        if (result != Error::ok) return result;
        out.segments[out.segment_count++] = {address, field.id, field.role, out.byte_count,
                                            static_cast<std::uint32_t>(size), field.guard_id, checkpoint};
        out.byte_count += static_cast<std::uint32_t>(size);
        return Error::ok;
    }
    Error scalar(const Field& field, std::uintptr_t address, void* value, std::size_t size) noexcept {
        const auto offset = out.byte_count;
        const auto error = copy(field, address, size);
        if (error == Error::ok) std::memcpy(value, out.bytes.data() + offset, size);
        return error;
    }
};
Error captured_count(const HookCapturePlan& plan, std::size_t index, Writer& writer, std::uint32_t& count) noexcept {
    const auto& field = plan.fields[index];
        const Field* count_field = nullptr;
        for (std::size_t previous = 0; previous < index; ++previous)
            if (plan.fields[previous].id == field.count_field_id && plan.fields[previous].phase == field.phase)
                count_field = &plan.fields[previous];
        if (!count_field) return Error::invalid_length;
        const FieldState* count_state = nullptr;
        for (std::size_t previous = 0; previous < writer.out.field_count; ++previous) {
            const auto& state = writer.out.fields[previous];
            if (state.id == count_field->id && state.role == count_field->role && state.checkpoint == writer.checkpoint) count_state = &state;
        }
        if (!count_state || count_state->error != Error::ok
            || count_state->final_segment >= writer.out.segment_count) return Error::invalid_length;
        const auto* count_segment = &writer.out.segments[count_state->final_segment];
        if (count_segment->field_id != count_field->id || count_segment->role != count_field->role
            || count_segment->size != sizeof(std::uint32_t)) return Error::invalid_length;
        writer.attempted_address = count_segment->address; writer.attempted_size = count_segment->size;
        std::memcpy(&count, writer.out.bytes.data() + count_segment->offset, sizeof(count));
        if (count > field.max_count) return Error::invalid_length;
        return Error::ok;

}
Error capture_string(const Field& field, std::uintptr_t address, Writer& writer, bool* matches_dynamic = nullptr) noexcept {
    std::uintptr_t klass = 0, length_address = 0;
    std::int32_t length = 0;
    if (!address_add(address, 16, length_address)) return Error::bounds;
    auto error = writer.scalar(field, address, &klass, sizeof(klass));
    if (error != Error::ok) return error;
    if ((error = writer.scalar(field, length_address, &length, sizeof(length))) != Error::ok) return error;
    if (!field.expected_class || klass != field.expected_class) return Error::class_mismatch;
    if (length < 0 || length > field.max_units || field.max_units > max_string_units) return Error::invalid_length;
    std::uintptr_t text;
    if (!address_add(address, 20, text)) return Error::bounds;
    const auto text_offset = writer.out.byte_count;
    const auto bytes = static_cast<std::size_t>(length) * 2;
    if ((error = writer.copy(field, text, bytes)) != Error::ok) return error;
    // The monitor and padding are not string identity facts. Only class,
    // length and the exact text are owned and checked again.
    std::uintptr_t after_class = 0;
    std::int32_t after_length = 0;
    if ((error = writer.raw(address, &after_class, sizeof(after_class))) != Error::ok) return error;
    if ((error = writer.raw(length_address, &after_length, sizeof(after_length))) != Error::ok) return error;
    if (klass != after_class || length != after_length) return Error::changed_guard;
    std::array<std::byte, max_string_units * 2> repeated;
    if (bytes && (error = writer.raw(text, repeated.data(), bytes)) != Error::ok) return error;
    if (bytes && std::memcmp(writer.out.bytes.data() + text_offset, repeated.data(), bytes)) return Error::changed_guard;
    if (matches_dynamic) {
        constexpr char16_t key[] = u"dynamic";
        *matches_dynamic = length == 7 && std::memcmp(writer.out.bytes.data() + text_offset, key, 14) == 0;
    }
    return Error::ok;
}
// Each node owns its local witnesses before descending and independently reads
// them again afterwards. A complete tree is one field: any changing branch
// invalidates every byte, rather than retaining a plausible partial roster.
struct ProjectionWitness {
    std::uintptr_t address;
    std::array<std::byte, max_element_bytes> bytes;
    std::uint16_t size;
};
Error projection(const HookCapturePlan& plan, const Field& field, std::uint16_t index,
                 std::uintptr_t base, Writer& writer,
                 std::array<std::uint16_t, max_projection_nodes>& visits,
                 std::size_t level) noexcept {
    if (index >= plan.projection_count || index >= max_projection_nodes || level >= max_projection_depth)
        return Error::invalid_plan;
    const auto& node = plan.projections[index];
    if (++visits[index] > node.max_visits) return Error::bounds;
    // At most 12 pointer links + 11 class/parent observations + payload/count/items.
    std::array<ProjectionWitness, max_depth + 14> witnesses;
    std::size_t witness_count = 0;
    auto own = [&](std::uintptr_t address, void* target, std::size_t size) noexcept -> Error {
        if (witness_count == witnesses.size() || size > max_element_bytes) return Error::bounds;
        auto& witness = witnesses[witness_count++];
        witness.address = address; witness.size = static_cast<std::uint16_t>(size);
        const auto error = writer.scalar(field, address, witness.bytes.data(), size);
        if (error == Error::ok && target && size) std::memcpy(target, witness.bytes.data(), size);
        return error;
    };
    auto close = [&]() noexcept -> Error {
        std::array<std::byte, max_element_bytes> repeated;
        for (std::size_t i = 0; i < witness_count; ++i) {
            const auto& witness = witnesses[i];
            const auto error = writer.raw(witness.address, repeated.data(), witness.size);
            if (error != Error::ok) return error;
            if (std::memcmp(witness.bytes.data(), repeated.data(), witness.size)) return Error::changed_guard;
        }
        return Error::ok;
    };
    auto address = base;
    Error error = Error::ok;
    for (std::size_t i = 0; i < node.depth; ++i) {
        if (!address) return node.allow_null ? close() : Error::missing_root;
        std::uintptr_t link = 0;
        if (!address_add(address, node.pointer_offsets[i], link)) return Error::bounds;
        if ((error = own(link, &address, 8)) != Error::ok) return error;
    }
    if (!address) return node.allow_null ? close() : Error::missing_root;
    if (!address_add(address, node.offset, address)) return Error::bounds;
    if (node.gate != TypeGate::none) {
        std::uintptr_t klass = 0;
        if ((error = own(address, &klass, 8)) != Error::ok) return error;
        bool matched = klass == node.expected_class;
        for (std::size_t i = 1; !matched && klass && node.gate == TypeGate::ancestor && i < node.ancestry_depth; ++i) {
            std::uintptr_t parent = 0;
            if (!address_add(klass, 0x58, parent)) return Error::bounds;
            if ((error = own(parent, &klass, 8)) != Error::ok) return error;
            matched = klass == node.expected_class;
        }
        // Type mismatch means this branch has no verified projected layout.
        // Its owned class chain remains available to the normal JS validator.
        if (!matched) return close();
    }
    if (node.kind == ProjectionKind::utf16) {
        auto string_field = field;
        string_field.expected_class = node.expected_class;
        string_field.max_units = node.max_units;
        if ((error = capture_string(string_field, address, writer)) != Error::ok) return error;
    } else if (node.kind == ProjectionKind::object) {
        if (node.bytes && (error = own(address, nullptr, node.bytes)) != Error::ok) return error;
        for (std::size_t i = 0; i < node.child_count; ++i)
            if ((error = projection(plan, field, static_cast<std::uint16_t>(node.child_begin + i), address,
                                    writer, visits, level + 1)) != Error::ok) return error;
    } else if (node.kind == ProjectionKind::array) {
        std::uintptr_t count_address = 0, items_address = 0, items = 0;
        std::uint32_t count = 0;
        if (!address_add(address, node.count_offset, count_address)
            || !address_add(address, node.items_offset, items_address)) return Error::bounds;
        if ((error = own(count_address, &count, 4)) != Error::ok
            || (error = own(items_address, &items, 8)) != Error::ok) return error;
        if (count > node.max_count) return Error::invalid_length;
        if (count && !items) return Error::missing_root;
        for (std::size_t i = 0; i < count; ++i) {
            std::uintptr_t slot = 0;
            if (!address_add(items, node.data_offset + i * node.element_bytes, slot)) return Error::bounds;
            const auto slot_offset = writer.out.byte_count;
            if ((error = writer.copy(field, slot, node.element_bytes)) != Error::ok) return error;
            std::int32_t occupancy = 0;
            if (node.skip_negative_i32_offset >= 0)
                std::memcpy(&occupancy, writer.out.bytes.data() + slot_offset + node.skip_negative_i32_offset, 4);
            for (std::size_t j = 0; occupancy >= 0 && j < node.child_count; ++j)
                if ((error = projection(plan, field, static_cast<std::uint16_t>(node.child_begin + j), slot,
                                        writer, visits, level + 1)) != Error::ok) return error;
            std::array<std::byte, max_element_bytes> repeated;
            if ((error = writer.raw(slot, repeated.data(), node.element_bytes)) != Error::ok) return error;
            if (std::memcmp(writer.out.bytes.data() + slot_offset, repeated.data(), node.element_bytes))
                return Error::changed_guard;
        }
    } else return Error::invalid_plan;
    return close();
}
struct ProjectionBudget { std::size_t bytes = 0, reads = 0, segments = 0; };
Error projection_budget(const HookCapturePlan& plan, std::uint16_t index, std::size_t visits,
                        std::size_t level, std::array<bool, max_projection_nodes>& seen,
                        ProjectionBudget& budget) noexcept {
    if (index >= plan.projection_count || index >= max_projection_nodes || level >= max_projection_depth || seen[index])
        return Error::invalid_plan;
    seen[index] = true;
    const auto& node = plan.projections[index];
    if (node.depth > max_depth || !node.max_visits || node.max_visits > max_projection_visits
        || node.child_begin > plan.projection_count || node.child_count > plan.projection_count - node.child_begin
        || (node.gate != TypeGate::none && node.gate != TypeGate::exact && node.gate != TypeGate::ancestor)
        || (node.gate != TypeGate::none && !node.expected_class)
        || (node.gate == TypeGate::ancestor && (!node.ancestry_depth || node.ancestry_depth > 10))
        || node.skip_negative_i32_offset < -1
        || (node.skip_negative_i32_offset >= 0 && (node.kind != ProjectionKind::array
            || static_cast<std::size_t>(node.skip_negative_i32_offset) + 4 > node.element_bytes))) return Error::invalid_plan;
    visits = visits < node.max_visits ? visits : node.max_visits;
    const std::size_t gate = node.gate == TypeGate::none ? 0 : node.gate == TypeGate::exact ? 1 : node.ancestry_depth;
    std::size_t local_bytes = (node.depth + gate) * 8, local_reads = node.depth + gate;
    std::size_t local_segments = local_reads, children_visits = visits;
    if (node.kind == ProjectionKind::object) {
        if (node.bytes > max_element_bytes) return Error::invalid_plan;
        local_bytes += node.bytes; local_reads += node.bytes ? 1 : 0; local_segments += node.bytes ? 1 : 0;
    } else if (node.kind == ProjectionKind::utf16) {
        if (node.child_count || !node.expected_class || !node.max_units || node.max_units > max_string_units)
            return Error::invalid_plan;
        budget.bytes += visits * 2 * (12 + node.max_units * 2);
        budget.reads += visits * 6; budget.segments += visits * 3;
    } else if (node.kind == ProjectionKind::array) {
        if (!node.element_bytes || node.element_bytes > max_element_bytes || !node.max_count || node.max_count > max_array_elements)
            return Error::invalid_plan;
        local_bytes += 12; local_reads += 2; local_segments += 2;
        budget.bytes += visits * node.max_count * node.element_bytes * 2;
        budget.reads += visits * node.max_count * 2;
        budget.segments += visits * node.max_count;
        children_visits *= node.max_count;
    } else return Error::invalid_plan;
    budget.bytes += visits * local_bytes * 2; budget.reads += visits * local_reads * 2;
    budget.segments += visits * local_segments;
    if (budget.bytes > plan.max_bytes || budget.reads > plan.max_reads || budget.segments > max_segments) return Error::bounds;
    for (std::size_t i = 0; i < node.child_count; ++i) {
        const auto error = projection_budget(plan, static_cast<std::uint16_t>(node.child_begin + i), children_visits,
                                             level + 1, seen, budget);
        if (error != Error::ok) return error;
    }
    return Error::ok;
}
Error field_capture(const HookCapturePlan& plan, std::size_t index, const Context& context, Writer& writer) noexcept {
    const auto& field = plan.fields[index];
    if (field.root_index >= max_roots || field.depth > max_depth) return Error::invalid_plan;
    const std::array<std::uintptr_t, max_roots>* roots = nullptr;
    std::uint16_t valid = 0;
    std::uintptr_t direct = 0;
    switch (field.root) {
    case Root::arg: roots = &context.args; valid = context.args_valid & plan.arg_mask; break;
    case Root::entry_ref: roots = &context.entry_refs; valid = context.entry_valid; break;
    case Root::parent_ref: roots = &context.parent_refs; valid = context.parent_valid; break;
    case Root::global_slot: roots = &context.globals; valid = context.globals_valid; break;
    case Root::checkpoint_ref: roots = &context.checkpoint_refs; valid = context.checkpoint_valid; break;
    case Root::return_value:
        if (!context.return_valid) return Error::missing_root;
        direct = context.return_value; break;
    case Root::absolute: direct = field.absolute_address; break;
    default: return Error::invalid_plan;
    }
    if (roots && !(valid & (1u << field.root_index))) return Error::missing_root;
    auto address = roots ? (*roots)[field.root_index] : direct;
    for (std::size_t depth = 0; depth < field.depth; ++depth) {
        std::uintptr_t location;
        if (!address || !address_add(address, field.pointer_offsets[depth], location)) return Error::missing_root;
        const auto error = writer.scalar(field, location, &address, sizeof(address));
        if (error != Error::ok) return error;
    }
    if (!address || !address_add(address, field.offset, address)) return Error::missing_root;
    if (field.kind == Kind::bytes) return writer.copy(field, address, field.bytes);
    if (field.kind == Kind::projection_tree) {
        std::array<std::uint16_t, max_projection_nodes> visits{};
        return projection(plan, field, field.projection_root, address, writer, visits, 0);
    }
    if (field.kind == Kind::counted_bytes || field.kind == Kind::utf16_array) {
        std::uint32_t count = 0;
        auto error = captured_count(plan, index, writer, count);
        if (error != Error::ok) return error;
        if (!count) return writer.copy(field, address, 0);
        if (field.kind == Kind::counted_bytes)
            return writer.copy(field, address, static_cast<std::size_t>(count) * field.element_bytes);
        if (count > max_array_elements || field.element_bytes > max_element_bytes
            || field.element_pointer_offset > field.element_bytes
            || sizeof(std::uintptr_t) > static_cast<std::size_t>(field.element_bytes - field.element_pointer_offset))
            return Error::invalid_plan;
        for (std::size_t element = 0; element < count; ++element) {
            std::uintptr_t slot = 0;
            if (!address_add(address, element * field.element_bytes, slot)) return Error::bounds;
            const auto slot_offset = writer.out.byte_count;
            if ((error = writer.copy(field, slot, field.element_bytes)) != Error::ok) return error;
            std::uintptr_t string = 0;
            std::memcpy(&string, writer.out.bytes.data() + slot_offset + field.element_pointer_offset, sizeof(string));
            bool matched = false;
            if (string && (error = capture_string(field, string, writer, &matched)) != Error::ok) return error;
            std::array<std::byte, max_element_bytes> repeated;
            if ((error = writer.raw(slot, repeated.data(), field.element_bytes)) != Error::ok) return error;
            if (std::memcmp(writer.out.bytes.data() + slot_offset, repeated.data(), field.element_bytes)) return Error::changed_guard;
            if (field.stop_at_dynamic && matched) break;
        }
        return Error::ok;
    }
    if (field.kind != Kind::utf16) return Error::invalid_plan;
    return capture_string(field, address, writer);
}
}

Error validate_plan(const HookCapturePlan& plan) noexcept {
    if (plan.schema_version != 1 || plan.field_count > max_fields || plan.arg_count > max_roots
        || plan.projection_count > max_projection_nodes
        || !plan.max_bytes || plan.max_bytes > max_bytes || !plan.max_reads || plan.max_reads > max_reads
        || (plan.arg_count < max_roots && (plan.arg_mask >> plan.arg_count))) return Error::invalid_plan;
    std::array<std::array<int, max_guards>, 3> before{}, after{};
    for (auto& phase : before) phase.fill(-1);
    for (auto& phase : after) phase.fill(-1);
    std::array<std::size_t, 3> bytes{}, segments{}, reads{};
    for (std::size_t i = 0; i < plan.field_count; ++i) {
        const auto& field = plan.fields[i];
        if (!field.id || !field.role || !phase_ok(field.phase) || field.root_index >= max_roots
            || field.depth > max_depth || field.guard_id >= max_guards
            || (field.root != Root::arg && field.root != Root::entry_ref && field.root != Root::parent_ref && field.root != Root::global_slot
                && field.root != Root::return_value && field.root != Root::checkpoint_ref && field.root != Root::absolute)
            || (field.root == Root::absolute && !field.absolute_address)
            || (field.root == Root::return_value && field.phase != Phase::leave)
            || (field.root == Root::arg && !(plan.arg_mask & (1u << field.root_index)))
            || ((field.guard == Guard::none) != (field.guard_id == 0))) return Error::invalid_plan;
        for (std::size_t j = 0; j < i; ++j) if (plan.fields[j].id == field.id) return Error::invalid_plan;
        if (field.stop_at_dynamic && field.kind != Kind::utf16_array) return Error::invalid_plan;
        auto n = field.bytes;
        std::size_t count = field.depth + 1, read_count = field.depth + 1;
        if (field.kind == Kind::projection_tree) {
            ProjectionBudget budget;
            std::array<bool, max_projection_nodes> seen{};
            const auto error = projection_budget(plan, field.projection_root, 1, 0, seen, budget);
            if (error != Error::ok) return error;
            n = static_cast<std::uint32_t>(budget.bytes); count = field.depth + budget.segments;
            read_count = field.depth + budget.reads;
        } else if (field.kind == Kind::utf16) {
            if (!field.expected_class || !field.max_units || field.max_units > max_string_units) return Error::invalid_plan;
            n = 2 * (12 + field.max_units * 2); count += 2; read_count = field.depth + 6;
        } else if (field.kind == Kind::counted_bytes || field.kind == Kind::utf16_array) {
            bool found = false;
            for (std::size_t j = 0; j < i; ++j) {
                const auto& prior = plan.fields[j];
                found |= prior.id == field.count_field_id && prior.kind == Kind::bytes
                    && prior.phase == field.phase && prior.bytes == sizeof(std::uint32_t)
                    && field.guard_id != 0 && prior.guard_id == field.guard_id;
            }
            if (!found || !field.element_bytes || !field.max_count) return Error::invalid_plan;
            n = static_cast<std::uint32_t>(field.element_bytes) * field.max_count;
            if (field.kind == Kind::utf16_array) {
                if (field.max_count > max_array_elements || field.element_bytes > max_element_bytes
                    || field.element_pointer_offset > field.element_bytes
                    || sizeof(std::uintptr_t) > static_cast<std::size_t>(field.element_bytes - field.element_pointer_offset)
                    || !field.expected_class || !field.max_units || field.max_units > max_string_units) return Error::invalid_plan;
                n = field.max_count * (2 * field.element_bytes + 2 * (12 + field.max_units * 2));
                count = field.depth + field.max_count * 4;
                read_count = field.depth + field.max_count * 8;
            }
        } else if (field.kind != Kind::bytes || !n) return Error::invalid_plan;
        const auto phase = static_cast<std::size_t>(field.phase);
        bytes[phase] += field.depth * sizeof(std::uintptr_t) + n;
        segments[phase] += count; reads[phase] += read_count;
        if (bytes[phase] > plan.max_bytes || reads[phase] > plan.max_reads || segments[phase] > max_segments) return Error::bounds;
        if (field.guard == Guard::before || field.guard == Guard::after) {
            auto& slot = (field.guard == Guard::before ? before : after)[phase][field.guard_id];
            if (slot != -1) return Error::invalid_plan;
            slot = static_cast<int>(i);
        } else if (field.guard != Guard::none && field.guard != Guard::member) return Error::invalid_plan;
    }
    for (std::size_t i = 0; i < plan.field_count; ++i) {
        const auto& field = plan.fields[i];
        if (!field.guard_id) continue;
        const auto a = before[static_cast<std::size_t>(field.phase)][field.guard_id];
        const auto b = after[static_cast<std::size_t>(field.phase)][field.guard_id];
        if (a < 0 || b <= a || !compatible(plan.fields[a], plan.fields[b])
            || (field.guard == Guard::member && !(a < static_cast<int>(i) && static_cast<int>(i) < b))) return Error::invalid_plan;
    }
    return Error::ok;
}

Error capture_begin(Snapshot& out, Phase phase, const Context& context,
                    std::uint32_t byte_budget, std::uint32_t read_budget) noexcept {
    // Reset only groups actually touched by the previous session. The large
    // owned byte/segment/field arenas are preallocated and never cleared here.
    if (out.guard_count > max_guards) return Error::invalid_plan;
    for (std::size_t i = 0; i < out.guard_count; ++i) out.guard_seen[out.guard_ids[i]] = false;
    out.guard_count = 0; out.guard_valid[0] = true;
    if (out.field_key_count > max_fields) return Error::invalid_plan;
    for (std::size_t i = 0; i < out.field_key_count; ++i) out.field_keys[out.used_key_slots[i]] = 0;
    out.field_key_count = 0; out.field_key_probes = 0;
    out.segment_count = out.field_count = 0; out.byte_count = 0; out.read_count = out.read_bytes = 0;
    out.error = Error::ok; out.session_open = true; out.finalized = false;
    out.phase = phase; out.checkpoint = context.checkpoint;
    out.invocation_id = context.invocation_id; out.parent_invocation_id = context.parent_invocation_id;
    out.byte_budget = byte_budget; out.read_budget = read_budget;
    if (!phase_ok(phase)) return out.error = Error::invalid_phase;
    if (!byte_budget || byte_budget > max_bytes || !read_budget || read_budget > max_reads)
        return out.error = Error::invalid_plan;
    return Error::ok;
}
Error capture_append(const HookCapturePlan& plan, Phase phase, const Context& context,
                     ReadMemory reader, void* user, Snapshot& out,
                     std::uint16_t field_begin, std::uint16_t field_end) noexcept {
    if (!out.session_open || out.finalized) return Error::invalid_plan;
    if (!phase_ok(phase)) return out.error = Error::invalid_phase;
    if (field_end == 0xffff) field_end = plan.field_count;
    if (plan.field_count > max_fields || plan.schema_version != 1 || !reader
        || field_begin > field_end || field_end > plan.field_count)
        return out.error = Error::invalid_plan;
    // Plan budgets constrain each append too, but never reset the raw record's
    // cumulative work counters. Whole-session budgets are the hard boundary.
    if (!plan.max_bytes || plan.max_bytes > max_bytes || !plan.max_reads || plan.max_reads > max_reads)
        return out.error = Error::invalid_plan;
    const auto first_reads = out.read_count, first_bytes = out.read_bytes;
    const auto byte_limit = first_bytes + plan.max_bytes < out.byte_budget ? first_bytes + plan.max_bytes : out.byte_budget;
    const auto read_limit = first_reads + plan.max_reads < out.read_budget ? first_reads + plan.max_reads : out.read_budget;
    Writer writer{out, reader, user, byte_limit, read_limit, context.checkpoint};
    for (std::size_t i = field_begin; i < field_end; ++i) {
        const auto& field = plan.fields[i];
        if (field.phase != phase) continue;
        if (out.field_count >= max_fields || field.guard_id >= max_guards) return out.error = Error::bounds;
        if (!field.id) return out.error = Error::invalid_plan;
        const auto key = (static_cast<std::uint64_t>(context.checkpoint) << 16) | field.id;
        auto mixed = key;
        mixed ^= mixed >> 33; mixed *= 0xff51afd7ed558ccdULL;
        mixed ^= mixed >> 33; mixed *= 0xc4ceb9fe1a85ec53ULL; mixed ^= mixed >> 33;
        std::size_t slot = mixed & (field_key_slots - 1);
        for (;;) {
            ++out.field_key_probes;
            if (out.field_keys[slot] == key) return out.error = Error::invalid_plan;
            if (!out.field_keys[slot]) break;
            slot = (slot + 1) & (field_key_slots - 1);
        }
        out.field_keys[slot] = key;
        out.used_key_slots[out.field_key_count++] = static_cast<std::uint16_t>(slot);
        if (field.guard_id && !out.guard_seen[field.guard_id]) {
            out.guard_seen[field.guard_id] = true; out.guard_valid[field.guard_id] = true;
            out.guards[field.guard_id] = GuardState{};
            out.guard_ids[out.guard_count++] = field.guard_id;
        }
        const auto begin = out.segment_count;
        writer.attempted_address = 0; writer.attempted_size = 0;
        auto error = field_capture(plan, i, context, writer);
        if (out.read_count - first_reads > plan.max_reads || out.read_bytes - first_bytes > plan.max_bytes)
            error = Error::bounds;
        const auto state_index = out.field_count++;
        auto& state = out.fields[state_index];
        state = {field.id, field.role, error,
            static_cast<std::uint16_t>(error == Error::ok && out.segment_count > begin ? out.segment_count - 1 : 0xffff),
            error == Error::ok ? 0 : writer.attempted_address, error == Error::ok ? 0 : writer.attempted_size,
            context.checkpoint, begin, out.segment_count, field.guard_id, field.guard, field.required};
        if (field.required && error != Error::ok && out.error == Error::ok) out.error = error;
        if (field.guard_id) {
            if (error != Error::ok) out.guard_valid[field.guard_id] = false;
            auto& group = out.guards[field.guard_id];
            if (field.guard == Guard::before) {
                if (group.before != 0xffff) return out.error = Error::invalid_plan;
                group.before = state_index;
            } else if (field.guard == Guard::after) {
                if (group.after != 0xffff) return out.error = Error::invalid_plan;
                group.after = state_index;
            }
        }
    }
    return out.error;
}
Error capture_finalize(Snapshot& out) noexcept {
    if (!out.session_open || out.finalized) return Error::invalid_plan;
    for (std::size_t i = 0; i < out.guard_count; ++i) {
        const auto id = out.guard_ids[i]; const auto& group = out.guards[id];
        bool equal = group.before != 0xffff && group.after != 0xffff
            && group.before < group.after && out.guard_valid[id];
        if (equal) {
            const auto& a = out.fields[group.before]; const auto& b = out.fields[group.after];
            equal = a.checkpoint == b.checkpoint
                && a.segment_end - a.segment_begin == b.segment_end - b.segment_begin;
            for (std::size_t n = 0; equal && n < static_cast<std::size_t>(a.segment_end - a.segment_begin); ++n) {
                const auto& x = out.segments[a.segment_begin + n];
                const auto& y = out.segments[b.segment_begin + n];
                equal = x.address == y.address && x.size == y.size
                    && std::memcmp(out.bytes.data() + x.offset, out.bytes.data() + y.offset, x.size) == 0;
            }
        }
        out.guard_valid[id] = equal;
    }
    for (std::size_t i = 0; i < out.field_count; ++i) {
        auto& field = out.fields[i];
        if (field.guard_id && field.guard == Guard::member) {
            const auto& group = out.guards[field.guard_id];
            if (!(group.before < i && i < group.after)) out.guard_valid[field.guard_id] = false;
        }
    }
    for (std::size_t i = 0; i < out.field_count; ++i) {
        auto& field = out.fields[i];
        if (field.guard_id && !out.guard_valid[field.guard_id]) {
            if (field.error == Error::ok) field.error = Error::changed_guard;
            if (field.required && out.error == Error::ok) out.error = Error::changed_guard;
        }
    }
    out.finalized = true; out.session_open = false;
    return out.error;
}
Error capture(const HookCapturePlan& plan, Phase phase, const Context& context,
              ReadMemory reader, void* user, Snapshot& out) noexcept {
    const auto error = capture_begin(out, phase, context, plan.max_bytes, plan.max_reads);
    if (error != Error::ok) return error;
    capture_append(plan, phase, context, reader, user, out);
    return capture_finalize(out);
}

Error read_snapshot(const Snapshot& snapshot, std::uint16_t role, std::uint32_t checkpoint,
                    std::uint16_t field_id, std::uintptr_t address, void* target, std::size_t size) noexcept {
    if (!snapshot.finalized || (!target && size) || snapshot.field_count > max_fields
        || snapshot.segment_count > max_segments || snapshot.byte_count > max_bytes) return Error::missing_plan_field;
    const FieldState* field = nullptr;
    for (std::size_t i = 0; i < snapshot.field_count; ++i)
        if (snapshot.fields[i].id == field_id && snapshot.fields[i].role == role && snapshot.fields[i].checkpoint == checkpoint) field = &snapshot.fields[i];
    if (!field) return Error::missing_plan_field;
    if (field->error != Error::ok) return field->error;
    const std::byte* selected = nullptr;
    for (std::size_t i = 0; i < snapshot.segment_count; ++i) {
        const auto& segment = snapshot.segments[i];
        if (segment.field_id != field_id || segment.role != role || segment.checkpoint != checkpoint || address < segment.address) continue;
        const auto delta = address - segment.address;
        if (delta > segment.size || size > segment.size - delta) continue;
        if (segment.offset > snapshot.byte_count || segment.size > snapshot.byte_count - segment.offset
            || segment.guard_id >= max_guards || !snapshot.guard_valid[segment.guard_id]) return Error::changed_guard;
        const auto* value = snapshot.bytes.data() + segment.offset + delta;
        if (selected && std::memcmp(selected, value, size)) return Error::ambiguous_read;
        selected = value;
    }
    if (!selected) return Error::missing_plan_field;
    if (size) std::memcpy(target, selected, size);
    return Error::ok;
}
}
