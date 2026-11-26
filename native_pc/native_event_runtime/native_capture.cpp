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
        && a.element_pointer_offset == b.element_pointer_offset && a.stop_at_dynamic == b.stop_at_dynamic;
}
struct Writer {
    Snapshot& out;
    ReadMemory reader;
    void* user;
    std::uint32_t byte_budget, read_budget;
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
                                            static_cast<std::uint32_t>(size), field.guard_id};
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
            if (state.id == count_field->id && state.role == count_field->role) count_state = &state;
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
    std::array<std::byte, max_string_units * 2> repeated{};
    if (bytes && (error = writer.raw(text, repeated.data(), bytes)) != Error::ok) return error;
    if (bytes && std::memcmp(writer.out.bytes.data() + text_offset, repeated.data(), bytes)) return Error::changed_guard;
    if (matches_dynamic) {
        constexpr char16_t key[] = u"dynamic";
        *matches_dynamic = length == 7 && std::memcmp(writer.out.bytes.data() + text_offset, key, 14) == 0;
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
            std::array<std::byte, max_element_bytes> repeated{};
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
        if (field.kind == Kind::utf16) {
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

Error capture(const HookCapturePlan& plan, Phase phase, const Context& context,
              ReadMemory reader, void* user, Snapshot& out) noexcept {
    out.segment_count = out.field_count = 0; out.byte_count = 0; out.read_count = out.read_bytes = 0; out.error = Error::ok;
    out.guard_valid.fill(true);
    out.phase = phase; out.checkpoint = context.checkpoint;
    out.invocation_id = context.invocation_id; out.parent_invocation_id = context.parent_invocation_id;
    if (!phase_ok(phase)) return out.error = Error::invalid_phase;
    if (plan.field_count > max_fields || plan.schema_version != 1 || !reader)
        return out.error = Error::invalid_plan;
    if (!plan.max_bytes || plan.max_bytes > max_bytes || !plan.max_reads || plan.max_reads > max_reads)
        return out.error = Error::invalid_plan;
    Writer writer{out, reader, user, plan.max_bytes, plan.max_reads};
    std::array<std::uint16_t, max_fields> begin{}, end{};
    std::array<int, max_guards> before{}, after{};
    before.fill(-1); after.fill(-1);
    for (std::size_t i = 0; i < plan.field_count; ++i) {
        const auto& field = plan.fields[i];
        if (field.phase != phase) continue;
        if (field.guard_id >= max_guards) return out.error = Error::invalid_plan;
        begin[i] = out.segment_count;
        writer.attempted_address = 0; writer.attempted_size = 0;
        const auto error = field_capture(plan, i, context, writer);
        end[i] = out.segment_count;
        out.fields[out.field_count++] = {field.id, field.role, error,
            static_cast<std::uint16_t>(error == Error::ok && end[i] > begin[i] ? end[i] - 1 : 0xffff),
            error == Error::ok ? 0 : writer.attempted_address, error == Error::ok ? 0 : writer.attempted_size};
        if (field.required && error != Error::ok && out.error == Error::ok) out.error = error;
        if (field.guard_id) {
            if (error != Error::ok) out.guard_valid[field.guard_id] = false;
            if (field.guard == Guard::before) before[field.guard_id] = static_cast<int>(i);
            if (field.guard == Guard::after) after[field.guard_id] = static_cast<int>(i);
        }
    }
    for (std::size_t group = 1; group < max_guards; ++group) {
        const auto a = before[group], b = after[group];
        if (a < 0 && b < 0) continue;
        bool equal = a >= 0 && b >= 0 && out.guard_valid[group];
        if (equal) {
            equal = end[a] - begin[a] == end[b] - begin[b];
            for (std::size_t n = 0; equal && n < static_cast<std::size_t>(end[a] - begin[a]); ++n) {
                const auto& x = out.segments[begin[a] + n];
                const auto& y = out.segments[begin[b] + n];
                equal = x.address == y.address && x.size == y.size
                    && std::memcmp(out.bytes.data() + x.offset, out.bytes.data() + y.offset, x.size) == 0;
            }
        }
        out.guard_valid[group] = equal;
        if (!equal) {
            for (std::size_t i = 0; i < plan.field_count; ++i) {
                const auto& field = plan.fields[i];
                if (field.phase != phase || field.guard_id != group) continue;
                for (std::size_t f = 0; f < out.field_count; ++f)
                    if (out.fields[f].id == field.id && out.fields[f].error == Error::ok) out.fields[f].error = Error::changed_guard;
                if (field.required && out.error == Error::ok) out.error = Error::changed_guard;
            }
        }
    }
    return out.error;
}

Error read_snapshot(const Snapshot& snapshot, std::uint16_t role, std::uint32_t checkpoint,
                    std::uint16_t field_id, std::uintptr_t address, void* target, std::size_t size) noexcept {
    if (snapshot.checkpoint != checkpoint || (!target && size) || snapshot.field_count > max_fields
        || snapshot.segment_count > max_segments || snapshot.byte_count > max_bytes) return Error::missing_plan_field;
    const FieldState* field = nullptr;
    for (std::size_t i = 0; i < snapshot.field_count; ++i)
        if (snapshot.fields[i].id == field_id && snapshot.fields[i].role == role) field = &snapshot.fields[i];
    if (!field) return Error::missing_plan_field;
    if (field->error != Error::ok) return field->error;
    const std::byte* selected = nullptr;
    for (std::size_t i = 0; i < snapshot.segment_count; ++i) {
        const auto& segment = snapshot.segments[i];
        if (segment.field_id != field_id || segment.role != role || address < segment.address) continue;
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
