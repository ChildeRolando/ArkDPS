import struct

import pytest

from acceptance import probe_native_pc_rows as rows


class FakeCounters:
    def __init__(self):
        self.base = 0x10000
        self.memory = bytearray(8192)
        self.stats = self.base + 0x100
        self.actors = self.base + 0x200
        self.pairs = self.base + 0x280
        self.key = self.base + 0x300
        self.output = self.base + 0x400
        self.types = self.base + 0x500
        self.type_array = self.base + 0x600
        for index, (address, name) in enumerate((
            (self.actors, b'ListDict`2'), (self.key, b'String'),
            (self.output, b'CharAdvancedStats'), (self.types, b'List`1'),
        )):
            klass = self.base + 0x800 + index * 0x80
            name_address = klass + 0x30
            self.put(address, '<Q', klass)
            self.put(klass + 0x10, '<Q', name_address)
            self.put(name_address, '<' + str(len(name) + 1) + 's', name + b'\0')
        self.put(self.stats + 0x30, '<Q', self.actors)
        self.put(self.actors + 0x10, '<Qii', self.pairs, 1, 3)
        self.put(self.pairs + 0x18, '<QQQ', 2, self.key, self.output)
        actor_id = 'char_001'
        self.put(self.key + 0x10, '<i16s', len(actor_id), actor_id.encode('utf-16-le'))
        self.put(self.output + 0x20, '<f', -215.0)
        self.put(self.output + 0x38, '<Q', self.types)
        self.put(self.types + 0x10, '<Qii', self.type_array, 6, 0)
        self.put(self.type_array + 0x18, '<Q6f', 6, 0, 120, 50, 10, 75, 35)

    def put(self, address, fmt, *values):
        struct.pack_into(fmt, self.memory, address - self.base, *values)

    def read(self, address, size):
        assert self.base <= address <= address + size <= self.base + len(self.memory)
        return bytes(self.memory[address - self.base:address - self.base + size])

    def sample(self):
        return rows.capture_character_rows(self.read, {'battle_stats': self.stats})


def test_reads_typed_totals_and_keeps_healing_out_of_damage():
    report = FakeCounters().sample()
    assert report['actor_rows_status'] == 'available'
    actor, = report['actor_rows']
    assert actor == {'id': 'char_001', 'physical': 120, 'arts': 50, 'true': 10,
                     'healing': 75, 'elemental': 35, 'damage': 215, 'raw_output': -215}


@pytest.mark.parametrize('base,offset,fmt,value,reason', [
    ('actors', 0x18, '<i', 129, 'counter_list_size'),
    ('types', 0x18, '<i', 5, 'counter_damage_type_count'),
    ('pairs', 0x18, '<Q', 0, 'short_counter_array'),
    ('key', 0x10, '<i', 1000, 'counter_id_length'),
    ('type_array', 0x24, '<f', float('nan'), 'invalid_counter_by_type'),
])
def test_bad_optional_layout_keeps_actor_counters_unavailable(base, offset, fmt, value, reason):
    mem = FakeCounters()
    mem.put(getattr(mem, base) + offset, fmt, value)
    assert mem.sample() == {'actor_rows_status': 'unavailable', 'actor_rows_reason': reason,
                            'actor_rows': None}
