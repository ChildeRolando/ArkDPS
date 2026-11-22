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


@pytest.fixture
def lifetime_fixture(fixture):
    mem, context, p = fixture
    module_base = 0x100000000
    context['game_assembly_range'] = (module_base, 0x9000000)

    def typed_field(klass, field):
        index, name, offset, code, type_index = field
        type_ref = module_base + presence.FIELD_TYPE_RVAS[type_index]
        type_slot = module_base + presence.FIELD_TYPE_TABLE_RVA + type_index * 8
        mem.put(type_slot, '<Q', type_ref)
        # Memory registration can replace a disk metadata index with a handle.
        mem.put(type_ref, '<QI', 0x123400000000 | type_index, 0x80000001 | code << 16)
        table, = struct.unpack('<Q', mem.read(klass + presence.CLASS_FIELDS, 8))
        mem.put(table + index * 32, '<QQQiI', mem.text(name), type_ref, klass, offset, 0)
        return type_ref

    value = mem.klass('ValueType', namespace='System')
    enum = mem.klass('Enum', namespace='System')
    fp = mem.klass('FP', 42, parent=value, namespace='Torappu')
    finish = mem.klass('FinishReason', 14, parent=enum, namespace='')
    typed_field(fp, (0, '_serializedValue', 0x10, presence.TYPE_I8, presence.FP_VALUE_FIELD_TYPE))
    typed_field(finish, (0, 'value__', 0x10, presence.TYPE_I4, presence.ENUM_VALUE_FIELD_TYPE))
    mem.put(p['classes']['entity'] + presence.CLASS_FIELD_COUNT, '<H', 410)
    fp_type = None
    for field in presence.LIFETIME_CHARACTER_FIELDS:
        fp_type = typed_field(p['classes']['character'], field)
    finish_type = typed_field(p['classes']['entity'], presence.LIFETIME_ENTITY_FIELDS[0])
    class_table = mem.allocate((presence.FP_TYPE_INDEX + 1) * 8)
    class_slot = module_base + presence.native.CLASS_DEFINITION_TABLE_RVA
    mem.put(class_slot, '<Q', class_table)
    mem.put(class_table + presence.FP_TYPE_INDEX * 8, '<Q', fp)
    mem.put(class_table + presence.FINISH_REASON_TYPE_INDEX * 8, '<Q', finish)
    context['class_definition_table_slot'] = class_slot
    mem.put(p['data'] + 0x50, '<Q', p['host_key'])
    mem.put(p['data'] + 0x90, '<B', 0)
    for index, address in enumerate(p['cameras']):
        # Entity side need not equal local player side: select by player_side.
        mem.put(address + 0x1C, '<i', 2)
        mem.put(address + presence.ENTITY_FINISH_REASON, '<i', index * 4)
        mem.put(address + presence.CHARACTER_CREATED_TIME, '<qq',
                (index + 1) << 32, -(index + 2) << 32)
    p.update(fp=fp, finish=finish, fp_type=fp_type, finish_type=finish_type, module_base=module_base,
             class_table=class_table, class_slot=class_slot, subject='char_336_folivo')
    return mem, context, p


def test_lifetime_is_opt_in_and_does_not_change_default_reads_or_json(lifetime_fixture):
    mem, context, p = lifetime_fixture
    mem.reads.clear()
    original = presence.capture_presence(mem.read, context)
    original_reads = list(mem.reads)
    mem.reads.clear()
    explicit_none = presence.capture_presence(mem.read, context, lifetime_subject=None)
    assert original == explicit_none and mem.reads == original_reads
    assert not any(key.startswith('presence_lifetime') for key in original)
    assert not any(address in (p['class_slot'], p['class_table'], p['fp_type'], p['finish_type'])
                   for address, _ in original_reads)
    assert not any(address == instance + presence.CHARACTER_CREATED_TIME
                   or address == instance + presence.ENTITY_FINISH_REASON
                   for address, _ in original_reads for instance in p['cameras'])


def test_lifetime_preserves_raw_signed_values_and_instance_identity(lifetime_fixture):
    mem, context, p = lifetime_fixture
    mem.put(p['cameras'][0] + presence.CHARACTER_CREATED_TIME, '<qq', -(1 << 63), (1 << 63) - 1)
    mem.put(p['cameras'][0] + presence.ENTITY_FINISH_REASON, '<i', -(1 << 31))
    normal = presence.capture_presence(mem.read, context)
    result = presence.capture_presence(mem.read, context, lifetime_subject=p['subject'])
    assert {key: value for key, value in result.items()
            if not key.startswith('presence_lifetime')} == normal
    assert result['presence_lifetime_status'] == 'available'
    assert result['presence_lifetime'] == {'subject_id': p['subject'], 'witnesses': [
        {'instance_uid': 100, 'finish_reason': -(1 << 31),
         'created_time_raw': -(1 << 63), 'dead_time_raw': (1 << 63) - 1},
        {'instance_uid': 101, 'finish_reason': 4,
         'created_time_raw': 2 << 32, 'dead_time_raw': -3 << 32},
    ]}
    assert 'presence_lifetime_reason' not in result
    assert not any(key in ('birth_time', 'death_time', 'duration', 'deployed')
                   for row in result['presence_lifetime']['witnesses'] for key in row)
    for address in p['cameras']:
        assert str(address) not in repr(result)


@pytest.mark.parametrize('excluded', ['different_id', 'token', 'other_player', 'empty'])
def test_lifetime_empty_match_is_available_without_type_or_time_reads(fixture, excluded):
    mem, context, p = fixture
    subject = 'char_336_folivo'
    if excluded in ('token', 'other_player'):
        mem.put(p['data'] + 0x50, '<Q', p['host_key'])
    if excluded == 'other_player':
        mem.put(p['data'] + 0x90, '<B', 0)
        for address in p['cameras']:
            mem.put(address + 0x20, '<i', 2)
    if excluded == 'empty':
        mem.put(p['container'] + 0x20, '<i', 0)
    result = presence.capture_presence(mem.read, context, lifetime_subject=subject)
    assert result['presence_status'] == result['presence_lifetime_status'] == 'available'
    assert result['presence_lifetime'] == {'subject_id': subject, 'witnesses': []}
    assert not any(address == instance + presence.CHARACTER_CREATED_TIME
                   for address, _ in mem.reads for instance in p['cameras'])


@pytest.mark.parametrize('subject', ['', 'token_10000_folivo_camera', 'char_bad value', 123, False])
def test_lifetime_invalid_subject_is_explicit_and_preserves_presence(fixture, subject):
    mem, context, _ = fixture
    result = presence.capture_presence(mem.read, context, lifetime_subject=subject)
    assert result['presence_status'] == 'available'
    assert result['presence_lifetime_status'] == 'unavailable'
    assert result['presence_lifetime_reason'] == 'presence_lifetime_invalid_subject'
    assert result['presence_lifetime'] is None


@pytest.mark.parametrize('count', [4, 5])
def test_lifetime_four_instances_are_bounded_and_overflow_is_not_truncated(lifetime_fixture, count):
    mem, context, p = lifetime_fixture
    for index in range(2, count):
        address = mem.obj(p['classes']['character'])
        mem.put(address + 0x18, '<IiiBBB', 100 + index, 1, 1, 1, 1, 0)
        mem.put(address + presence.CHARACTER_DATA, '<Q', p['data'])
        p['cameras'].append(address)
    mem.put(p['container'] + 0x20, '<i', count)
    mem.put(p['array'] + presence.ARRAY_DATA, f'<{count}Q', *p['cameras'])
    mem.reads.clear()
    presence.capture_presence(mem.read, context)
    baseline_calls = len(mem.reads)
    baseline_bytes = sum(size for _, size in mem.reads)
    mem.reads.clear()
    result = presence.capture_presence(mem.read, context, lifetime_subject=p['subject'])
    assert result['presence_status'] == 'available'
    if count == 4:
        assert result['presence_lifetime_status'] == 'available'
        assert len(result['presence_lifetime']['witnesses']) == 4
        assert 0 < len(mem.reads) - baseline_calls <= presence.MAX_LIFETIME_READ_CALLS
        assert sum(size for _, size in mem.reads) - baseline_bytes <= presence.MAX_LIFETIME_READ_BYTES
    else:
        assert result['presence_lifetime_status'] == 'unavailable'
        assert result['presence_lifetime_reason'] == 'presence_lifetime_instance_count'
        assert result['presence_lifetime'] is None
        assert len(mem.reads) == baseline_calls


@pytest.mark.parametrize('fault,reason', [
    ('missing_slot', 'class_table_slot_missing'),
    ('missing_module', 'module_range'),
    ('null_table', 'class_table_uninitialized'),
    ('null_class', 'class_uninitialized'),
    ('class_name', 'class_identity'),
    ('value_parent', 'value_inheritance'),
    ('fp_count', 'field_count'),
    ('enum_count', 'field_count'),
    ('entity_count', 'field_count'),
    ('field_offset', 'field_layout'),
    ('field_owner', 'field_layout'),
    ('field_name', 'field_layout'),
    ('field_primitive', 'field_kind_54452'),
    ('field_registration', 'field_binding_98015'),
    ('field_static', 'field_kind_54452'),
    ('fp_underlying', 'field_kind_60349'),
    ('enum_underlying', 'field_kind_60333'),
])
def test_lifetime_rejects_unverified_layout_without_invalidating_presence(lifetime_fixture, fault, reason):
    mem, context, p = lifetime_fixture
    table, = struct.unpack('<Q', mem.read(p['classes']['character'] + presence.CLASS_FIELDS, 8))
    if fault == 'missing_slot':
        del context['class_definition_table_slot']
    elif fault == 'missing_module':
        del context['game_assembly_range']
    elif fault == 'null_table':
        mem.put(p['class_slot'], '<Q', 0)
    elif fault == 'null_class':
        mem.put(p['class_table'] + presence.FP_TYPE_INDEX * 8, '<Q', 0)
    elif fault == 'class_name':
        mem.put(p['fp'] + 0x10, '<Q', mem.text('Other'))
    elif fault == 'value_parent':
        mem.put(p['fp'] + presence.CLASS_PARENT, '<Q', p['classes']['object'])
    elif fault in ('fp_count', 'enum_count', 'entity_count'):
        owner = {'fp_count': p['fp'], 'enum_count': p['finish'],
                 'entity_count': p['classes']['entity']}[fault]
        mem.put(owner + presence.CLASS_FIELD_COUNT, '<H', 1)
    elif fault in ('field_offset', 'field_owner', 'field_name'):
        offset, fmt, value = {'field_offset': (24, '<i', 0),
                              'field_owner': (16, '<Q', 0),
                              'field_name': (0, '<Q', mem.text('otherTime'))}[fault]
        mem.put(table + 42 * 32 + offset, fmt, value)
    elif fault == 'field_primitive':
        mem.put(p['fp_type'] + 8, '<I', presence.TYPE_I8 << 16)
    elif fault == 'field_registration':
        type_slot = p['module_base'] + presence.FIELD_TYPE_TABLE_RVA + presence.FINISH_REASON_FIELD_TYPE * 8
        mem.put(type_slot, '<Q', p['fp_type'])
    elif fault == 'field_static':
        mem.put(p['fp_type'] + 8, '<I', presence.TYPE_VALUETYPE << 16 | 0x10)
    elif fault in ('fp_underlying', 'enum_underlying'):
        owner = p['fp'] if fault == 'fp_underlying' else p['finish']
        fields, = struct.unpack('<Q', mem.read(owner + presence.CLASS_FIELDS, 8))
        type_ref, = struct.unpack('<Q', mem.read(fields + 8, 8))
        mem.put(type_ref + 8, '<I', presence.TYPE_VALUETYPE << 16)
    result = presence.capture_presence(mem.read, context, lifetime_subject=p['subject'])
    assert result['presence_status'] == 'available'
    assert result['presence_lifetime_status'] == 'unavailable'
    assert result['presence_lifetime_reason'] == 'presence_lifetime_' + reason
    assert result['presence_lifetime'] is None


@pytest.mark.parametrize('changed', ['time', 'finish', 'field', 'type_descriptor', 'type_registration', 'membership'])
def test_lifetime_rereads_values_metadata_and_base_selection(lifetime_fixture, changed):
    mem, context, p = lifetime_fixture
    if changed == 'time':
        target = p['cameras'][0] + presence.CHARACTER_CREATED_TIME, 16, 2, '<q', 999
    elif changed == 'finish':
        target = p['cameras'][0] + presence.ENTITY_FINISH_REASON, 4, 2, '<i', 12
    elif changed == 'field':
        table, = struct.unpack('<Q', mem.read(p['classes']['character'] + presence.CLASS_FIELDS, 8))
        target = table + 42 * 32, 32, 2, '<Q', 0
    elif changed == 'type_descriptor':
        target = p['fp_type'], 12, 3, '<Q', 0  # shared FP type used by two fields
    elif changed == 'type_registration':
        type_slot = p['module_base'] + presence.FIELD_TYPE_TABLE_RVA + presence.FINISH_REASON_FIELD_TYPE * 8
        target = type_slot, 8, 2, '<Q', p['fp_type']
    else:
        target = p['cameras'][0] + 0x18, 15, 3, '<I', 999
    counts = {}
    def change(address, size):
        counts[address, size] = counts.get((address, size), 0) + 1
        if (address, size) == target[:2] and counts[address, size] == target[2]:
            mem.put(address, target[3], target[4])
    mem.on_read = change
    result = presence.capture_presence(mem.read, context, lifetime_subject=p['subject'])
    assert result['presence_status'] == 'available'
    assert result['presence_lifetime_status'] == 'unavailable'
    assert result['presence_lifetime_reason'] == ('presence_lifetime_membership_changed'
            if changed == 'membership' else 'presence_lifetime_changed_during_read')
    assert result['presence_lifetime'] is None


@pytest.mark.parametrize('fault,reason', [
    ('denied', 'presence_lifetime_read_failed'),
    ('short', 'presence_lifetime_short_read'),
    ('parent_budget', 'presence_lifetime_read_budget_exhausted'),
    ('calls', 'presence_lifetime_read_budget_exhausted'),
    ('bytes', 'presence_lifetime_read_budget_exhausted'),
])
def test_lifetime_read_failures_and_own_budget_are_isolated(lifetime_fixture, monkeypatch, fault, reason):
    mem, context, p = lifetime_fixture
    target = p['cameras'][1] + presence.CHARACTER_CREATED_TIME
    def read(address, size):
        if address == target:
            if fault == 'denied':
                raise presence.native.access.ApiFailure('ReadProcessMemory', 299, 0)
            if fault == 'short':
                return mem.read(address, size)[:-1]
            if fault == 'parent_budget':
                raise presence.native.ResearchFailure('unsupported_layout', 'optional_read_budget_exhausted')
        return mem.read(address, size)
    if fault == 'calls':
        monkeypatch.setattr(presence, 'MAX_LIFETIME_READ_CALLS', 1)
    elif fault == 'bytes':
        monkeypatch.setattr(presence, 'MAX_LIFETIME_READ_BYTES', 16)
    result = presence.capture_presence(read, context, lifetime_subject=p['subject'])
    assert result['presence_status'] == 'available'
    assert result['presence_lifetime_status'] == 'unavailable'
    assert result['presence_lifetime_reason'] == reason
    assert result['presence_lifetime'] is None


def test_lifetime_does_not_read_optional_fields_when_base_presence_is_unavailable(fixture):
    mem, context, p = fixture
    mem.put(p['battle'] + presence.BATTLE_UNIT_MANAGER, '<Q', 0)
    result = presence.capture_presence(mem.read, context, lifetime_subject='char_336_folivo')
    assert result == {'presence_status': 'unavailable', 'presence_reason': 'presence_invalid_reference',
                      'presence': None, 'presence_lifetime_status': 'unavailable',
                      'presence_lifetime_reason': 'presence_lifetime_presence_unavailable',
                      'presence_lifetime': None}
