"""Synthetic stage-reference layouts only; never access a game process."""

import struct

import pytest

from native_pc import probe_native_pc_stage_enemies as stage
from test_probe_native_pc_presence import Memory


@pytest.fixture
def fixture():
    mem, p = Memory(), {}

    def typed_class(name, count, fields, namespace=''):
        klass = mem.klass(name, count, tuple(row[:3] for row in fields), namespace=namespace)
        table, = struct.unpack('<Q', mem.read(klass + stage.presence.CLASS_FIELDS, 8))
        for index, field, _offset, code in fields:
            type_ref = mem.allocate()
            data = 0
            if code == stage.TYPE_SZARRAY:
                data = mem.allocate()
                mem.put(data, '<QI', 4733, stage.TYPE_CLASS << 16)
                p['element_type'] = data
            mem.put(type_ref, '<QI', data, code << 16)
            mem.put(table + index * 32 + 8, '<Q', type_ref)
            p[field + '_type'] = type_ref
            p[field + '_record'] = table + index * 32
        return klass

    p['level_class'] = typed_class('LevelData', 23, stage.LEVEL_FIELDS, 'Torappu')
    p['ref_class'] = typed_class('EnemyDataDbReference', 4, stage.REF_FIELDS)
    p['array_class'] = mem.klass('EnemyDataDbReference[]', namespace='')
    p['string_class'] = mem.klass('String', namespace='System')
    p['level'], p['array'] = mem.obj(p['level_class']), mem.obj(p['array_class'])
    mem.put(p['level'] + stage.LEVEL_REFS, '<Q', p['array'])
    p['key'] = mem.string('enemy_1007_slime_2', p['string_class'])
    p['other_key'] = mem.string('enemy_1002_nsabr', p['string_class'])
    p['overwrite'] = 0x300000000  # Deliberately unreadable: presence must be enough.
    p['refs'] = [mem.obj(p['ref_class']) for _ in range(3)]
    for obj, use_db, key, variant, overwrite in (
        (p['refs'][0], 1, p['key'], 0, 0),
        (p['refs'][1], 1, p['key'], 1, p['overwrite']),
        (p['refs'][2], 0, p['other_key'], 2, p['overwrite']),
    ):
        mem.put(obj + 0x10, '<B7xQi4xQ', use_db, key, variant, overwrite)
    mem.put(p['array'] + 0x18, '<Q', 3)
    mem.put(p['array'] + 0x20, '<QQQ', *p['refs'])
    mem.reads.clear()
    return mem, {'level_data': p['level']}, p


def test_reference_array_keeps_variants_and_flags_without_reading_overwrites(fixture):
    mem, context, p = fixture
    result = stage.capture_stage_enemy_refs(mem.read, context)
    assert result == {'stage_enemy_refs_status': 'available', 'stage_enemy_refs': {
        'scope': 'current_stage_enemy_db_refs', 'collection_count': 3, 'entries': [
            {'enemy_key': 'enemy_1007_slime_2', 'variant_level': 0,
             'use_db': True, 'has_overwrite': False},
            {'enemy_key': 'enemy_1007_slime_2', 'variant_level': 1,
             'use_db': True, 'has_overwrite': True},
            {'enemy_key': 'enemy_1002_nsabr', 'variant_level': 2,
             'use_db': False, 'has_overwrite': True},
        ]}}
    assert not any(p['overwrite'] <= address < p['overwrite'] + 4096
                   for address, _size in mem.reads)
    assert str(p['level']) not in repr(result)
    assert str(p['refs'][0]) not in repr(result)


def test_empty_reference_array_is_observed_empty(fixture):
    mem, context, p = fixture
    mem.put(p['array'] + 0x18, '<Q', 0)
    result = stage.capture_stage_enemy_refs(mem.read, context)
    assert result['stage_enemy_refs_status'] == 'available'
    assert result['stage_enemy_refs']['entries'] == []


@pytest.mark.parametrize('fault,reason', [
    ('missing_context', 'stage_enemy_refs_level_data_missing'),
    ('null_level', 'stage_enemy_refs_invalid_reference'),
    ('null_array', 'stage_enemy_refs_invalid_reference'),
    ('count', 'stage_enemy_refs_count'),
    ('null_slot', 'stage_enemy_refs_invalid_reference'),
    ('duplicate_object', 'stage_enemy_refs_duplicate_object'),
    ('bad_flag', 'stage_enemy_refs_use_db_flag'),
    ('negative_variant', 'stage_enemy_refs_variant_level'),
    ('large_variant', 'stage_enemy_refs_variant_level'),
    ('bad_overwrite', 'stage_enemy_refs_invalid_reference'),
    ('bad_key', 'stage_enemy_refs_id_format'),
    ('bad_namespace', 'stage_enemy_refs_class_identity'),
    ('wrong_array_class', 'stage_enemy_refs_class_identity'),
    ('count_layout', 'stage_enemy_refs_field_count'),
    ('field_offset', 'stage_enemy_refs_field_layout'),
    ('field_owner', 'stage_enemy_refs_field_layout'),
    ('field_name', 'stage_enemy_refs_field_layout'),
    ('field_type', 'stage_enemy_refs_field_type'),
    ('inline_value_array', 'stage_enemy_refs_array_element_type'),
])
def test_invalid_sources_fail_closed_without_empty_or_catalog_fallback(fixture, fault, reason):
    mem, context, p = fixture
    if fault == 'missing_context':
        context = {}
    elif fault == 'null_level':
        context['level_data'] = 0
    elif fault == 'null_array':
        mem.put(p['level'] + stage.LEVEL_REFS, '<Q', 0)
    elif fault == 'count':
        mem.put(p['array'] + 0x18, '<Q', stage.MAX_REFS + 1)
    elif fault in ('null_slot', 'duplicate_object'):
        mem.put(p['array'] + 0x28, '<Q', 0 if fault == 'null_slot' else p['refs'][0])
    elif fault == 'bad_flag':
        mem.put(p['refs'][0] + 0x10, '<B', 2)
    elif fault in ('negative_variant', 'large_variant'):
        mem.put(p['refs'][0] + 0x20, '<i', -1 if fault == 'negative_variant' else stage.MAX_VARIANT_LEVEL + 1)
    elif fault == 'bad_overwrite':
        mem.put(p['refs'][0] + 0x28, '<Q', 1)
    elif fault == 'bad_key':
        mem.put(p['key'] + 0x14, '<H', ord(' '))
    elif fault == 'bad_namespace':
        mem.put(p['ref_class'] + 0x18, '<Q', mem.text('Other'))
    elif fault == 'wrong_array_class':
        mem.put(p['array_class'] + 0x10, '<Q', mem.text('EnemyDataDbReference'))
    elif fault == 'count_layout':
        mem.put(p['ref_class'] + stage.presence.CLASS_FIELD_COUNT, '<H', 5)
    elif fault in ('field_offset', 'field_owner', 'field_name'):
        offset, encoding, value = {
            'field_offset': (24, '<i', 0x24),
            'field_owner': (16, '<Q', p['level_class']),
            'field_name': (0, '<Q', mem.text('variant')),
        }[fault]
        mem.put(p['level_record'] + offset, encoding, value)
    elif fault == 'field_type':
        mem.put(p['level_type'] + 8, '<I', stage.TYPE_BOOL << 16)
    else:
        mem.put(p['element_type'] + 8, '<I', 0x11 << 16)
        mem.put(p['array'] + 0x18, '<Q', 0)
    assert stage.capture_stage_enemy_refs(mem.read, context) == {
        'stage_enemy_refs_status': 'unavailable', 'stage_enemy_refs_reason': reason,
        'stage_enemy_refs': None}


@pytest.mark.parametrize('changed', [
    'root_class', 'root_ref', 'array_count', 'array_slot', 'entry_class',
    'entry_data', 'string_data', 'type_prefix', 'metadata_name',
])
def test_rereads_reject_mid_sample_mutations(fixture, changed):
    mem, context, p = fixture
    address, size, encoding, values = {
        'root_class': (p['level'], 8, '<Q', (p['ref_class'],)),
        'root_ref': (p['level'] + stage.LEVEL_REFS, 8, '<Q', (0,)),
        'array_count': (p['array'] + 0x18, 8, '<Q', (2,)),
        'array_slot': (p['array'] + 0x20, 24, '<QQQ', tuple(reversed(p['refs']))),
        'entry_class': (p['refs'][0], 8, '<Q', (p['level_class'],)),
        'entry_data': (p['refs'][0] + 0x10, 0x20, '<B', (0,)),
        'string_data': (p['key'] + 0x14, len('enemy_1007_slime_2') * 2, '<H', (ord('x'),)),
        'type_prefix': (p['level_type'], 12, '<QI', (0, stage.TYPE_BOOL << 16)),
        'metadata_name': (p['level_class'] + 0x10, 16, '<QQ', (mem.text('Other'), mem.text('Torappu'))),
    }[changed]
    reads = 0
    def mutate(where, count):
        nonlocal reads
        if (where, count) == (address, size):
            reads += 1
            if reads == 2:
                mem.put(where, encoding, *values)
    mem.on_read = mutate
    result = stage.capture_stage_enemy_refs(mem.read, context)
    assert result['stage_enemy_refs_status'] == 'unavailable'
    assert result['stage_enemy_refs_reason'] == 'stage_enemy_refs_changed_during_read'


def test_near_cap_reference_array_stays_within_its_own_budget(fixture):
    mem, context, p = fixture
    objects = []
    for index in range(stage.MAX_REFS):
        obj = mem.obj(p['ref_class'])
        key = mem.string('enemy_' + str(index), p['string_class'])
        mem.put(obj + 0x10, '<B7xQi4xQ', 1, key, index, 0)
        objects.append(obj)
    mem.put(p['array'] + 0x18, '<Q', len(objects))
    mem.put(p['array'] + 0x20, '<' + 'Q' * len(objects), *objects)
    mem.reads.clear()
    result = stage.capture_stage_enemy_refs(mem.read, context)
    assert result['stage_enemy_refs_status'] == 'available'
    assert result['stage_enemy_refs']['collection_count'] == stage.MAX_REFS
    assert len(mem.reads) <= stage.MAX_READ_CALLS
    assert sum(size for _address, size in mem.reads) <= stage.MAX_READ_BYTES


def test_denial_short_reads_and_local_parent_budgets_stay_optional(fixture, monkeypatch):
    mem, context, _ = fixture
    def denied(_address, _size):
        raise stage.presence.native.access.ApiFailure('ReadProcessMemory', 299)
    assert stage.capture_stage_enemy_refs(denied, context)['stage_enemy_refs_reason'] == 'stage_enemy_refs_read_failed'
    assert stage.capture_stage_enemy_refs(lambda a, s: mem.read(a, s)[:-1], context)[
        'stage_enemy_refs_reason'] == 'stage_enemy_refs_short_read'
    def parent_exhausted(_address, _size):
        raise stage.presence.native.ResearchFailure('unsupported_layout', 'optional_read_budget_exhausted')
    assert stage.capture_stage_enemy_refs(parent_exhausted, context)[
        'stage_enemy_refs_reason'] == 'stage_enemy_refs_read_budget_exhausted'
    monkeypatch.setattr(stage, 'MAX_READ_CALLS', 1)
    assert stage.capture_stage_enemy_refs(mem.read, context)[
        'stage_enemy_refs_reason'] == 'stage_enemy_refs_read_budget_exhausted'
