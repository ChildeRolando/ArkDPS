"""Decode optional character counters from the same guarded native PC sample."""

import math
import re
import struct


MAX_ACTORS = 128
MAX_ACTOR_ID_UNITS = 110
CLASS_NAME = 0x10
ADVANCED_STATS = 0x30
LIST_HEADER = 0x10
ARRAY_LENGTH = 0x18
ARRAY_DATA = 0x20
OUTPUT_DAMAGE = 0x20
TYPED_OUTPUT = 0x38
TYPE_NAMES = ('physical', 'arts', 'true', 'healing', 'elemental')
ACTOR_ID = re.compile(r'[A-Za-z0-9_#.-]+')


class RowLayoutError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise RowLayoutError(reason)


def pointer(value):
    require(0x10000 <= value < (1 << 47) and value % 8 == 0, 'invalid_object_reference')
    return value


def read_uint64(read, address):
    return struct.unpack('<Q', read(address, 8))[0]


def expect_class(read, address, name, known):
    klass = pointer(read_uint64(read, address))
    if (klass, name) not in known:
        name_address = read_uint64(read, klass + CLASS_NAME)
        require(0x10000 <= name_address < (1 << 47), 'invalid_class_name_reference')
        require(read(name_address, len(name) + 1) == name + b'\0', 'unexpected_counter_class')
        known.add((klass, name))


def checked_list(read, address, maximum):
    header = read(address + LIST_HEADER, 16)
    items, count, _version = struct.unpack('<Qii', header)
    require(0 <= count <= maximum, 'counter_list_size')
    if count:
        pointer(items)
        require(read_uint64(read, items + ARRAY_LENGTH) >= count, 'short_counter_array')
    return items, count, header


def read_actor_id(read, address, known):
    expect_class(read, address, b'String', known)
    length, = struct.unpack('<i', read(address + 0x10, 4))
    require(1 <= length <= MAX_ACTOR_ID_UNITS, 'counter_id_length')
    try:
        actor_id = read(address + 0x14, length * 2).decode('utf-16-le')
    except UnicodeDecodeError:
        raise RowLayoutError('counter_id_encoding') from None
    require(ACTOR_ID.fullmatch(actor_id), 'counter_id_format')
    return actor_id


def read_actor_output(read, address, known):
    expect_class(read, address, b'CharAdvancedStats', known)
    block = read(address + OUTPUT_DAMAGE, TYPED_OUTPUT - OUTPUT_DAMAGE + 8)
    raw, = struct.unpack_from('<f', block)
    totals = pointer(struct.unpack_from('<Q', block, TYPED_OUTPUT - OUTPUT_DAMAGE)[0])
    expect_class(read, totals, b'List`1', known)
    items, count, header = checked_list(read, totals, 6)
    require(count == 6, 'counter_damage_type_count')
    values = struct.unpack('<6f', read(items + ARRAY_DATA, 24))
    require(math.isfinite(raw) and abs(raw) <= 1e15, 'invalid_counter_total')
    require(all(math.isfinite(value) and 0 <= value <= 1e15 for value in values),
            'invalid_counter_by_type')
    require(read(totals + LIST_HEADER, 16) == header
            and read_uint64(read, address + TYPED_OUTPUT) == totals,
            'counter_type_list_changed')
    output = dict(zip(TYPE_NAMES, values[1:]))
    output['damage'] = sum(values[index] for index in (1, 2, 3, 5))
    output['raw_output'] = raw
    return output


def capture_character_rows(read, context):
    known = set()
    stats = context['battle_stats']
    try:
        container = pointer(read_uint64(read, stats + ADVANCED_STATS))
        expect_class(read, container, b'ListDict`2', known)
        items, count, header = checked_list(read, container, MAX_ACTORS)
        pairs = read(items + ARRAY_DATA, count * 16) if count else b''
        rows = {}
        for key, summary in struct.iter_unpack('<QQ', pairs):
            actor_id = read_actor_id(read, pointer(key), known)
            require(actor_id not in rows, 'duplicate_counter_id')
            rows[actor_id] = {'id': actor_id, **read_actor_output(read, pointer(summary), known)}
        require(read(container + LIST_HEADER, 16) == header
                and read_uint64(read, stats + ADVANCED_STATS) == container,
                'character_counter_list_changed')
    except RowLayoutError as error:
        return {'actor_rows_status': 'unavailable', 'actor_rows_reason': str(error), 'actor_rows': None}
    return {'actor_rows_status': 'available', 'actor_rows': list(rows.values())}
