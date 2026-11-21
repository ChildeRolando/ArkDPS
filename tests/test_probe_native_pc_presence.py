"""Synthetic managed layouts only; these tests never inspect a game process."""

import struct

import pytest

from acceptance import probe_native_pc_presence as presence


class Memory:
    def __init__(self):
        self.memory = {}
        self.next_address = 0x10000
        self.on_read = None
        self.reads = []

    def allocate(self, size=0x1000):
        address = self.next_address
        self.next_address += (size + 0xFFF) // 0x1000 * 0x1000
        self.memory.update((address + offset, 0) for offset in range(size))
        return address

    def put(self, address, encoding, *values):
        self.memory.update((address + index, value)
                           for index, value in enumerate(struct.pack(encoding, *values)))

    def read(self, address, size):
        self.reads.append((address, size))
        if self.on_read:
            self.on_read(address, size)
        return bytes(self.memory[address + offset] for offset in range(size))

    def text(self, value):
        address = self.allocate()
        encoded = value.encode('ascii') + b'\0'
        self.put(address, f'<{len(encoded)}s', encoded)
        return address

    def klass(self, name, count=0, fields=(), parent=0, namespace='Torappu.Battle'):
        address = self.allocate()
        table = self.allocate(max(4096, count * 32))
        self.put(address + 0x10, '<QQ', self.text(name), self.text(namespace))
        self.put(address + presence.CLASS_PARENT, '<Q', parent)
        self.put(address + presence.CLASS_FIELDS, '<Q', table)
        self.put(address + presence.CLASS_FIELD_COUNT, '<H', count)
        for index, name, offset in fields:
            self.put(table + index * 32, '<QQQiI', self.text(name), 0, address, offset, 0)
        return address

    def obj(self, klass):
        address = self.allocate()
        self.put(address, '<Q', klass)
        return address

    def string(self, value, klass):
        address = self.obj(klass)
        encoded = value.encode('utf-16-le')
        self.put(address + 0x10, '<i', len(encoded) // 2)
        self.put(address + 0x14, f'<{len(encoded)}s', encoded)
        return address


@pytest.fixture
def fixture():
    mem = Memory()
    classes = {}
    classes['battle'] = mem.klass('BattleController', 515, presence.BATTLE_FIELDS)
    classes['manager'] = mem.klass('UnitManager', 7, presence.MANAGER_FIELDS)
    classes['container'] = mem.klass('UnorderedArray`1', 3, presence.CONTAINER_FIELDS, namespace='Torappu')
    classes['visual'] = mem.klass('VisualObject')
    classes['object'] = mem.klass('BObject', 9, presence.OBJECT_FIELDS, classes['visual'])
    classes['entity'] = mem.klass('Entity', parent=classes['object'])
    classes['unit'] = mem.klass('Unit', parent=classes['entity'])
    classes['character'] = mem.klass('Character', 356, presence.CHARACTER_FIELDS, classes['unit'])
    classes['enemy'] = mem.klass('Enemy', parent=classes['unit'])
    classes['data'] = mem.klass('BattleCharacterData', 36, presence.DATA_FIELDS)
    classes['string'] = mem.klass('String', namespace='System')
    battle, manager, container, array = [mem.obj(classes[name])
                                        for name in ('battle', 'manager', 'container', 'container')]
    mem.put(battle + presence.BATTLE_SIDE, '<i', 1)
    mem.put(battle + presence.BATTLE_UNIT_MANAGER, '<Q', manager)
    mem.put(manager + presence.MANAGER_CHARACTERS, '<Q', container)
    mem.put(container + presence.CONTAINER_HEADER, '<QQi', array, 0, 2)
    mem.put(array + presence.ARRAY_LENGTH, '<Q', 16)
    host_key = mem.string('char_336_folivo', classes['string'])
    token_key = mem.string('token_10000_folivo_camera', classes['string'])
    data = mem.obj(classes['data'])
    mem.put(data + 0x50, '<Q', token_key)
    mem.put(data + 0x6C, '<I', 42)
    mem.put(data + 0x90, '<BB', 1, 0)
    mem.put(data + 0x98, '<QI', host_key, 7)
    cameras = [mem.obj(classes['character']) for _ in range(2)]
    for index, address in enumerate(cameras):
        mem.put(address + 0x18, '<IiiBBB', 100 + index, 1, 1, 1, 1, 0)
        mem.put(address + presence.CHARACTER_DATA, '<Q', data)
    mem.put(array + presence.ARRAY_DATA, '<QQ', *cameras)
    return mem, {'battle_controller': battle}, dict(
        classes=classes, battle=battle, manager=manager, container=container,
        array=array, data=data, cameras=cameras, host_key=host_key, token_key=token_key)


def test_two_scene_cameras_preserve_shared_data_and_distinct_instance_uids(fixture):
    mem, context, p = fixture
    result = presence.capture_presence(mem.read, context)
    assert result['presence_status'] == 'available'
    value = result['presence']
    assert value['scope'] == 'unit_manager_characters_membership'
    assert (value['local_side'], value['collection_count'], value['non_character_count']) == (1, 2, 0)
    assert len(value['entities']) == 2
    assert {entry['instance_uid'] for entry in value['entities']} == {100, 101}
    for entry in value['entities']:
        assert (entry['id'], entry['data_uid'], entry['host_uid']) == ('token_10000_folivo_camera', 42, 7)
        assert entry['host_key'] == 'char_336_folivo' and entry['is_token']
        assert entry['inited'] and entry['borned'] and not entry['is_disappeared']
        assert not any('hp' in key or 'damage' in key or 'duration' in key for key in entry)
    assert value['limitations']
    for address in (p['battle'], p['data'], *p['cameras']):
        assert str(address) not in repr(result)


def test_redeployment_changes_instance_not_shared_roster_identity(fixture):
    mem, context, p = fixture
    first = presence.capture_presence(mem.read, context)['presence']['entities']
    mem.put(p['cameras'][0] + 0x18, '<I', 102)
    second = presence.capture_presence(mem.read, context)['presence']['entities']
    assert first[0]['instance_uid'] == 100 and second[0]['instance_uid'] == 102
    assert first[0]['data_uid'] == second[0]['data_uid'] == 42
    assert first[1] == second[1]


def test_empty_collection_is_observed_empty_and_raw_disappeared_flag_is_preserved(fixture):
    mem, context, p = fixture
    mem.put(p['cameras'][0] + 0x26, '<B', 1)
    result = presence.capture_presence(mem.read, context)
    assert result['presence']['entities'][0]['is_disappeared'] is True
    mem.put(p['container'] + 0x20, '<i', 0)
    result = presence.capture_presence(mem.read, context)
    assert result['presence_status'] == 'available'
    assert result['presence']['collection_count'] == 0 and result['presence']['entities'] == []


def test_non_character_unit_is_counted_without_reading_character_data(fixture):
    mem, context, p = fixture
    enemy = mem.obj(p['classes']['enemy'])
    mem.put(enemy + 0x18, '<IiiBBB', 150, 2, 2, 1, 1, 0)
    mem.put(p['array'] + presence.ARRAY_DATA + 8, '<Q', enemy)
    result = presence.capture_presence(mem.read, context)
    assert result['presence_status'] == 'available'
    assert result['presence']['non_character_count'] == 1
    assert len(result['presence']['entities']) == 1
    assert (enemy + presence.CHARACTER_DATA, 8) not in mem.reads


@pytest.mark.parametrize('fault,reason', [
    ('missing_manager', 'presence_invalid_reference'),
    ('negative_count', 'presence_entity_count'),
    ('large_count', 'presence_entity_count'),
    ('short_array', 'presence_array_capacity'),
    ('large_capacity', 'presence_array_capacity'),
    ('missing_array', 'presence_missing_array'),
    ('null_slot', 'presence_invalid_reference'),
    ('duplicate_object', 'presence_duplicate_object'),
    ('duplicate_uid', 'presence_duplicate_instance_uid'),
    ('lifecycle_flag', 'presence_lifecycle_flags'),
    ('data_flag', 'presence_data_flags'),
    ('missing_data', 'presence_invalid_reference'),
    ('field_count', 'presence_field_count'),
    ('field_offset', 'presence_field_layout'),
    ('field_owner', 'presence_field_layout'),
    ('wrong_namespace', 'presence_class_identity'),
    ('wrong_inheritance', 'presence_unit_inheritance'),
    ('cycle', 'presence_class_cycle'),
    ('bad_id', 'presence_id_format'),
    ('long_id', 'presence_id_length'),
])
def test_missing_or_inconsistent_witness_is_unknown_not_absent(fixture, fault, reason):
    mem, context, p = fixture
    if fault == 'missing_manager':
        mem.put(p['battle'] + presence.BATTLE_UNIT_MANAGER, '<Q', 0)
    elif fault in ('negative_count', 'large_count'):
        mem.put(p['container'] + 0x20, '<i', -1 if fault == 'negative_count' else 129)
    elif fault in ('short_array', 'large_capacity'):
        mem.put(p['array'] + presence.ARRAY_LENGTH, '<Q', 1 if fault == 'short_array' else 4097)
    elif fault == 'missing_array':
        mem.put(p['container'] + 0x10, '<Q', 0)
    elif fault in ('null_slot', 'duplicate_object'):
        mem.put(p['array'] + presence.ARRAY_DATA + 8, '<Q', 0 if fault == 'null_slot' else p['cameras'][0])
    elif fault == 'duplicate_uid':
        mem.put(p['cameras'][1] + 0x18, '<I', 100)
    elif fault == 'lifecycle_flag':
        mem.put(p['cameras'][0] + 0x24, '<B', 2)
    elif fault == 'data_flag':
        mem.put(p['data'] + 0x90, '<B', 2)
    elif fault == 'missing_data':
        mem.put(p['cameras'][0] + presence.CHARACTER_DATA, '<Q', 0)
    elif fault == 'field_count':
        mem.put(p['classes']['manager'] + presence.CLASS_FIELD_COUNT, '<H', 6)
    elif fault in ('field_offset', 'field_owner'):
        table, = struct.unpack('<Q', mem.read(p['classes']['character'] + presence.CLASS_FIELDS, 8))
        mem.put(table + 78 * 32 + (24 if fault == 'field_offset' else 16),
                '<i' if fault == 'field_offset' else '<Q', 0)
    elif fault == 'wrong_namespace':
        mem.put(p['classes']['manager'] + 0x18, '<Q', mem.text('Other'))
    elif fault == 'wrong_inheritance':
        mem.put(p['classes']['character'] + presence.CLASS_PARENT, '<Q', p['classes']['object'])
    elif fault == 'cycle':
        mem.put(p['classes']['character'] + presence.CLASS_PARENT, '<Q', p['classes']['character'])
    elif fault == 'bad_id':
        mem.put(p['token_key'] + 0x14, '<H', ord(' '))
    elif fault == 'long_id':
        mem.put(p['token_key'] + 0x10, '<i', 111)
    assert presence.capture_presence(mem.read, context) == {
        'presence_status': 'unavailable', 'presence_reason': reason, 'presence': None}


@pytest.mark.parametrize('changed', ['collection', 'slots', 'instance', 'data', 'host', 'class', 'side'])
def test_before_after_guards_reject_membership_changes_even_without_clock_change(fixture, changed):
    mem, context, p = fixture
    counts = {}
    guard = {
        'collection': (p['container'] + 0x10, 20, '<QQi', (p['array'], 0, 1)),
        'slots': (p['array'] + presence.ARRAY_DATA, 16, '<QQ', tuple(reversed(p['cameras']))),
        'instance': (p['cameras'][0] + 0x18, 15, '<I', (999,)),
        'data': (p['cameras'][0] + presence.CHARACTER_DATA, 8, '<Q', (0,)),
        'host': (p['data'] + 0x50, 0x54, '<Q', (p['host_key'],)),
        'class': (p['cameras'][0], 8, '<Q', (p['classes']['enemy'],)),
        'side': (p['battle'] + presence.BATTLE_SIDE, 4, '<i', (2,)),
    }[changed]
    def change(address, size):
        counts[address, size] = counts.get((address, size), 0) + 1
        if (address, size) == guard[:2] and counts[address, size] == 2:
            mem.put(address, guard[2], *guard[3])
    mem.on_read = change
    result = presence.capture_presence(mem.read, context)
    assert result == {'presence_status': 'unavailable',
                      'presence_reason': 'presence_changed_during_read', 'presence': None}


def test_read_failures_and_budgets_remain_nonfatal_without_leaking_addresses(fixture, monkeypatch):
    mem, context, _ = fixture
    def denied(address, size):
        raise presence.native.access.ApiFailure('ReadProcessMemory', 299, 0)
    assert presence.capture_presence(denied, context)['presence_reason'] == 'presence_read_failed'
    def parent_budget(address, size):
        raise presence.native.ResearchFailure('unsupported_layout', 'read_budget_exhausted')
    assert presence.capture_presence(parent_budget, context)['presence_reason'] == 'presence_parent_reader_unavailable'
    def short(address, size):
        return mem.read(address, size)[:-1]
    assert presence.capture_presence(short, context)['presence_reason'] == 'presence_short_read'
    monkeypatch.setattr(presence, 'MAX_READ_CALLS', 1)
    assert presence.capture_presence(mem.read, context)['presence_reason'] == 'presence_read_budget_exhausted'


def test_near_cap_synthetic_collection_stays_bounded(fixture):
    mem, context, p = fixture
    # Share persistent data (and statistic identity), but never entity UID.
    cameras = []
    for index in range(presence.MAX_ENTITIES):
        camera = mem.obj(p['classes']['character'])
        mem.put(camera + 0x18, '<IiiBBB', index + 1000, 1, 1, 1, 1, 0)
        mem.put(camera + presence.CHARACTER_DATA, '<Q', p['data'])
        cameras.append(camera)
    mem.put(p['container'] + 0x20, '<i', len(cameras))
    mem.put(p['array'] + presence.ARRAY_LENGTH, '<Q', len(cameras))
    mem.put(p['array'] + presence.ARRAY_DATA, f'<{len(cameras)}Q', *cameras)
    result = presence.capture_presence(mem.read, context)
    # The independent per-call budget is a supported unknown outcome at high
    # occupancy; it cannot silently return a partial list as complete presence.
    assert result['presence_status'] == 'unavailable'
    assert result['presence_reason'] == 'presence_read_budget_exhausted'
    assert len(mem.reads) <= presence.MAX_READ_CALLS
