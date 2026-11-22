"""Synthetic Windows GameFlow roots; no process, GUI or game access."""

import struct

import pytest

from native_pc import probe_native_pc_flow as flow
from test_probe_native_pc_presence import Memory


@pytest.fixture
def fixture():
    mem, p = Memory(), {}

    def typed_class(name, count, fields, namespace):
        klass = mem.klass(name, count, tuple(row[:3] for row in fields), namespace=namespace)
        table, = struct.unpack('<Q', mem.read(klass + flow.presence.CLASS_FIELDS, 8))
        for index, field, _offset, codes, is_static in fields:
            type_ref = mem.allocate()
            mem.put(type_ref, '<QI', 0, (codes[0] << 16) | (0x10 if is_static else 0))
            mem.put(table + index * 32 + 8, '<Q', type_ref)
            p[field + '_record'] = table + index * 32
            p[field + '_type'] = type_ref
        return klass

    p['flow_class'] = typed_class('GameFlowController', 76, flow.FLOW_FIELDS, 'Torappu')
    p['parent'] = typed_class('SingletonMonoBehaviour`1', 2, flow.SINGLETON_FIELDS, 'Torappu')
    p['ancestor'] = mem.klass('MonoBehaviour', namespace='UnityEngine')
    p['bundle_class'] = typed_class('SceneBundle', 3, flow.BUNDLE_FIELDS, 'Torappu')
    p['bundle_parent'] = mem.klass('ValueType', namespace='System')
    p['string_class'] = mem.klass('String', namespace='System')
    mem.put(p['flow_class'] + flow.presence.CLASS_PARENT, '<Q', p['parent'])
    mem.put(p['parent'] + flow.presence.CLASS_PARENT, '<Q', p['ancestor'])
    mem.put(p['bundle_class'] + flow.presence.CLASS_PARENT, '<Q', p['bundle_parent'])
    p['slot'], p['table'], p['statics'] = mem.allocate(), mem.allocate(0x4000), mem.allocate()
    mem.put(p['slot'], '<Q', p['table'])
    mem.put(p['table'] + flow.FLOW_TYPE_INDEX * 8, '<Q', p['flow_class'])
    mem.put(p['table'] + flow.BUNDLE_TYPE_INDEX * 8, '<Q', p['bundle_class'])
    mem.put(p['parent'] + flow.presence.native.CLASS_STATICS, '<Q', p['statics'])
    p['object'] = mem.obj(p['flow_class'])
    p['native'] = mem.allocate()
    p['param'] = 0x300000000  # Valid reference, deliberately not readable.
    mem.put(p['statics'], '<Q', p['object'])
    mem.put(p['object'] + 0x10, '<Q', p['native'])
    p['source'] = mem.string('level_main_01-07', p['string_class'])
    p['target'] = mem.string('battle_finish', p['string_class'])
    mem.put(p['object'] + 0x20, '<B7xQQQ', 1, p['source'], p['target'], p['param'])
    mem.reads.clear()
    return mem, {'class_definition_table_slot': p['slot']}, p


def test_raw_transition_is_guarded_and_contains_no_outcome_or_addresses(fixture):
    mem, context, p = fixture
    result = flow.capture_game_flow(mem.read, context)
    assert result == {'game_flow_status': 'available', 'game_flow': {
        'transiting': True, 'source_scene': 'level_main_01-07', 'target_scene': 'battle_finish'}}
    assert 'outcome' not in repr(result) and 'abandon' not in repr(result)
    assert str(p['object']) not in repr(result)
    assert not any(p['param'] <= address < p['param'] + 4096 for address, _ in mem.reads)
    assert len(mem.reads) <= flow.MAX_READ_CALLS
    assert sum(size for _, size in mem.reads) <= flow.MAX_READ_BYTES


@pytest.mark.parametrize('instance_type', [flow.TYPE_VAR, flow.TYPE_CLASS])
def test_singleton_field_accepts_formal_or_inflated_reference_type(fixture, instance_type):
    mem, context, p = fixture
    mem.put(p['s_instance_type'] + 8, '<I', (instance_type << 16) | 0x10)
    assert flow.capture_game_flow(mem.read, context)['game_flow_status'] == 'available'


def test_nontransition_and_null_scene_strings_are_raw_empty_strings(fixture):
    mem, context, p = fixture
    mem.put(p['object'] + 0x20, '<B7xQQQ', 0, 0, 0, 0)
    assert flow.capture_game_flow(mem.read, context)['game_flow'] == {
        'transiting': False, 'source_scene': '', 'target_scene': ''}


@pytest.mark.parametrize('fault,reason', [
    ('missing_slot', 'game_flow_class_table_slot_missing'),
    ('null_table', 'game_flow_class_table_uninitialized'),
    ('null_class', 'game_flow_class_uninitialized'),
    ('null_bundle', 'game_flow_class_uninitialized'),
    ('null_statics', 'game_flow_singleton_storage_uninitialized'),
    ('null_singleton', 'game_flow_singleton_unavailable'),
    ('null_native', 'game_flow_native_missing'),
    ('wrong_object', 'game_flow_concrete_class'),
    ('wrong_namespace', 'game_flow_class_identity'),
    ('wrong_parent', 'game_flow_class_identity'),
    ('wrong_ancestor', 'game_flow_class_identity'),
    ('wrong_bundle_parent', 'game_flow_class_identity'),
    ('field_count', 'game_flow_field_count'),
    ('field_name', 'game_flow_field_layout'),
    ('field_offset', 'game_flow_field_layout'),
    ('field_owner', 'game_flow_field_layout'),
    ('field_type', 'game_flow_field_type'),
    ('nonstatic_singleton', 'game_flow_field_type'),
    ('transiting', 'game_flow_transiting_flag'),
    ('long_scene', 'game_flow_scene_length'),
    ('bad_utf16', 'game_flow_scene_encoding'),
    ('control_scene', 'game_flow_scene_control_character'),
])
def test_unavailable_and_invalid_roots_never_become_battle_absence(fixture, fault, reason):
    mem, context, p = fixture
    if fault == 'missing_slot':
        context = {}
    elif fault == 'null_table':
        mem.put(p['slot'], '<Q', 0)
    elif fault in ('null_class', 'null_bundle'):
        index = flow.FLOW_TYPE_INDEX if fault == 'null_class' else flow.BUNDLE_TYPE_INDEX
        mem.put(p['table'] + index * 8, '<Q', 0)
    elif fault == 'null_statics':
        mem.put(p['parent'] + flow.presence.native.CLASS_STATICS, '<Q', 0)
    elif fault == 'null_singleton':
        mem.put(p['statics'], '<Q', 0)
    elif fault == 'null_native':
        mem.put(p['object'] + 0x10, '<Q', 0)
    elif fault == 'wrong_object':
        mem.put(p['object'], '<Q', p['parent'])
    elif fault == 'wrong_namespace':
        mem.put(p['flow_class'] + 0x18, '<Q', mem.text('Other'))
    elif fault == 'wrong_parent':
        mem.put(p['flow_class'] + flow.presence.CLASS_PARENT, '<Q', p['ancestor'])
    elif fault == 'wrong_ancestor':
        mem.put(p['parent'] + flow.presence.CLASS_PARENT, '<Q', p['parent'])
    elif fault == 'wrong_bundle_parent':
        mem.put(p['bundle_class'] + flow.presence.CLASS_PARENT, '<Q', p['ancestor'])
    elif fault == 'field_count':
        mem.put(p['flow_class'] + flow.presence.CLASS_FIELD_COUNT, '<H', 75)
    elif fault in ('field_name', 'field_offset', 'field_owner'):
        offset, encoding, value = {
            'field_name': (0, '<Q', mem.text('transiting')),
            'field_offset': (24, '<i', 0x21),
            'field_owner': (16, '<Q', p['parent']),
        }[fault]
        mem.put(p['m_isTransiting_record'] + offset, encoding, value)
    elif fault == 'field_type':
        mem.put(p['m_isTransiting_type'] + 8, '<I', flow.TYPE_CLASS << 16)
    elif fault == 'nonstatic_singleton':
        mem.put(p['s_instance_type'] + 8, '<I', flow.TYPE_VAR << 16)
    elif fault == 'transiting':
        mem.put(p['object'] + 0x20, '<B', 2)
    elif fault == 'long_scene':
        mem.put(p['source'] + 0x10, '<i', flow.MAX_SCENE_UNITS + 1)
    elif fault == 'bad_utf16':
        mem.put(p['source'] + 0x10, '<i', 1)
        mem.put(p['source'] + 0x14, '<H', 0xD800)
    else:
        mem.put(p['source'] + 0x14, '<H', 10)
    assert flow.capture_game_flow(mem.read, context) == {
        'game_flow_status': 'unavailable', 'game_flow_reason': reason, 'game_flow': None}


@pytest.mark.parametrize('changed', [
    'table_slot', 'class_slot', 'parent', 'statics', 'singleton', 'native',
    'object_class', 'scalars', 'scene_text', 'field_type', 'metadata_name',
])
def test_root_metadata_and_scene_rereads_reject_changes(fixture, changed):
    mem, context, p = fixture
    address, size, encoding, values = {
        'table_slot': (p['slot'], 8, '<Q', (0,)),
        'class_slot': (p['table'] + flow.FLOW_TYPE_INDEX * 8, 8, '<Q', (0,)),
        'parent': (p['flow_class'] + flow.presence.CLASS_PARENT, 8, '<Q', (p['ancestor'],)),
        'statics': (p['parent'] + flow.presence.native.CLASS_STATICS, 8, '<Q', (0,)),
        'singleton': (p['statics'], 8, '<Q', (0,)),
        'native': (p['object'] + 0x10, 8, '<Q', (0,)),
        'object_class': (p['object'], 8, '<Q', (p['parent'],)),
        'scalars': (p['object'] + 0x20, 0x20, '<B', (0,)),
        'scene_text': (p['source'] + 0x14, len('level_main_01-07') * 2, '<H', (ord('x'),)),
        'field_type': (p['m_isTransiting_type'], 12, '<QI', (0, flow.TYPE_CLASS << 16)),
        'metadata_name': (p['flow_class'] + 0x10, 16, '<QQ', (mem.text('Other'), mem.text('Torappu'))),
    }[changed]
    reads = 0
    def mutate(where, count):
        nonlocal reads
        if (where, count) == (address, size):
            reads += 1
            if reads == 2:
                mem.put(where, encoding, *values)
    mem.on_read = mutate
    assert flow.capture_game_flow(mem.read, context) == {
        'game_flow_status': 'unavailable', 'game_flow_reason': 'game_flow_changed_during_read',
        'game_flow': None}


def test_read_errors_and_budgets_remain_optional(fixture, monkeypatch):
    mem, context, _ = fixture
    def denied(_address, _size):
        raise flow.presence.native.access.ApiFailure('ReadProcessMemory', 299)
    assert flow.capture_game_flow(denied, context)['game_flow_reason'] == 'game_flow_read_failed'
    assert flow.capture_game_flow(lambda a, s: mem.read(a, s)[:-1], context)[
        'game_flow_reason'] == 'game_flow_short_read'
    def parent_exhausted(_address, _size):
        raise flow.presence.native.ResearchFailure('unsupported_layout', 'optional_read_budget_exhausted')
    assert flow.capture_game_flow(parent_exhausted, context)[
        'game_flow_reason'] == 'game_flow_read_budget_exhausted'
    monkeypatch.setattr(flow, 'MAX_READ_CALLS', 1)
    assert flow.capture_game_flow(mem.read, context)['game_flow_reason'] == 'game_flow_read_budget_exhausted'
