"""Synthetic UI stack memory only; no process, GUI or live capture access."""

import struct

import pytest

from native_pc import probe_native_pc_practice as practice
from test_probe_native_pc_presence import Memory


@pytest.fixture
def fixture():
    mem, p = Memory(), {}

    def klass(key, name, count=0, fields=(), namespace=''):
        result = mem.klass(name, count, tuple(field[:3] for field in fields), namespace=namespace)
        p[key] = result
        table, = struct.unpack('<Q', mem.read(result + practice.presence.CLASS_FIELDS, 8))
        for index, name, _offset, codes, is_static in fields:
            type_ref = mem.allocate()
            mem.put(type_ref, '<QI', 0, (codes[0] << 16) | (0x10 if is_static else 0))
            mem.put(table + index * 32 + 8, '<Q', type_ref)
            p[name + '_record'] = table + index * 32
            p[name + '_type'] = type_ref
        return result

    klass('controller_class', 'UIPageController', 108, practice.CONTROLLER_FIELDS, 'Torappu.UI')
    klass('stack_class', 'StackElmt', 3, practice.STACK_FIELDS)
    klass('page_class', 'UIPage', 93, practice.PAGE_FIELDS, 'Torappu.UI')
    klass('squad_class', 'SquadPage', 8, namespace='Torappu.UI')
    klass('params_class', 'Params', 16, practice.PARAMS_FIELDS)
    klass('stage_class', 'BattleStageInfo', 21, practice.STAGE_FIELDS, 'Torappu.Battle')
    klass('singleton_class', 'SingletonMonoBehaviour`1', 2, practice.flow.SINGLETON_FIELDS, 'Torappu')
    klass('mono_class', 'MonoBehaviour', namespace='UnityEngine')
    klass('state_class', 'StateEnginePage', namespace='Torappu.UI')
    klass('value_class', 'ValueType', namespace='System')
    klass('list_class', 'List`1', 6, practice.LIST_FIELDS, 'System.Collections.Generic')
    klass('array_class', 'StackElmt[]')
    klass('string_class', 'String', namespace='System')
    for child, parent in [('controller_class', 'singleton_class'), ('singleton_class', 'mono_class'),
                          ('squad_class', 'state_class'), ('state_class', 'page_class'),
                          ('stack_class', 'value_class'), ('params_class', 'value_class'),
                          ('stage_class', 'value_class')]:
        mem.put(p[child] + practice.presence.CLASS_PARENT, '<Q', p[parent])
    mem.put(p['params_class'] + 0x50, '<Q', p['squad_class'])
    p['slot'], p['table'], p['statics'] = mem.allocate(), mem.allocate(0x25000), mem.allocate()
    mem.put(p['slot'], '<Q', p['table'])
    for name, index in [('controller', practice.CONTROLLER_TYPE_INDEX), ('stack', practice.STACK_ELEMENT_TYPE_INDEX),
                        ('page', practice.PAGE_TYPE_INDEX), ('squad', practice.SQUAD_TYPE_INDEX),
                        ('params', practice.PARAMS_TYPE_INDEX), ('stage', practice.STAGE_TYPE_INDEX)]:
        mem.put(p['table'] + index * 8, '<Q', p[name + '_class'])
    mem.put(p['singleton_class'] + practice.presence.native.CLASS_STATICS, '<Q', p['statics'])
    for name, type_name in [('controller', 'controller'), ('list', 'list'), ('array', 'array'),
                            ('page', 'squad'), ('args', 'params'), ('cached', 'page')]:
        p[name] = mem.obj(p[type_name + '_class'])
    p['native'] = mem.allocate()
    mem.put(p['statics'], '<Q', p['controller'])
    mem.put(p['controller'] + 0x10, '<Q', p['native'])
    mem.put(p['controller'] + 0x68, '<Q', p['list'])
    mem.put(p['list'] + 0x10, '<Qii', p['array'], 2, 7)
    mem.put(p['array'] + 0x18, '<Q', 4)
    mem.put(p['array'] + 0x28, '<Q', p['cached'])
    p['top_slot'] = p['array'] + 0x20 + 32 + 8
    mem.put(p['top_slot'], '<Q', p['page'])
    mem.put(p['page'] + 0x10, '<Q', p['native'])
    mem.put(p['page'] + 0x28, '<I', 123)  # Deliberately no READY/ROUTED numeric assumption.
    mem.put(p['page'] + 0x30, '<Q', p['args'])
    mem.put(p['args'] + 0x28, '<B', 1)
    p['stage'] = mem.string('main_01-07', p['string_class'])
    mem.put(p['args'] + 0x48, '<Q', p['stage'])
    mem.reads.clear()
    return mem, {'class_definition_table_slot': p['slot']}, p


@pytest.mark.parametrize('value', [0, 1])
def test_only_current_stack_top_selection_is_exported(fixture, value):
    mem, context, p = fixture
    mem.put(p['args'] + 0x28, '<B', value)
    result = practice.capture_practice_selection(mem.read, context)
    assert result == {'practice_selection_status': 'available', 'practice_selection': {
        'stage_id': 'main_01-07', 'practice': bool(value)}}
    assert not any(p['cached'] <= address < p['cached'] + 0x1000 for address, _ in mem.reads)
    assert str(p['args']) not in repr(result) and 'battle' not in result['practice_selection']
    assert len(mem.reads) <= practice.MAX_READ_CALLS
    assert sum(size for _, size in mem.reads) <= practice.MAX_READ_BYTES


@pytest.mark.parametrize('fault', [
    'missing_context', 'null_table', 'null_class', 'wrong_namespace', 'wrong_count', 'wrong_field',
    'wrong_type', 'wrong_singleton_parent', 'null_singleton', 'null_native', 'null_stack',
    'zero_count', 'large_count', 'small_capacity', 'large_capacity', 'wrong_array',
    'null_page', 'cached_top', 'wrong_page_parent', 'wrong_declaring', 'wrong_params_parent', 'null_args',
    'wrong_args', 'invalid_bool', 'null_stage', 'empty_stage', 'control_stage', 'invalid_utf16',
])
def test_bad_or_unavailable_selection_never_becomes_false(fixture, fault):
    mem, context, p = fixture
    if fault == 'missing_context': context = {}
    elif fault == 'null_table': mem.put(p['slot'], '<Q', 0)
    elif fault == 'null_class': mem.put(p['table'] + practice.PARAMS_TYPE_INDEX * 8, '<Q', 0)
    elif fault == 'wrong_namespace': mem.put(p['squad_class'] + 0x18, '<Q', mem.text('Other'))
    elif fault == 'wrong_count': mem.put(p['page_class'] + practice.presence.CLASS_FIELD_COUNT, '<H', 92)
    elif fault == 'wrong_field': mem.put(p['isPractice_record'] + 24, '<i', 0x29)
    elif fault == 'wrong_type': mem.put(p['isPractice_type'] + 8, '<I', practice.TYPE_I4 << 16)
    elif fault == 'wrong_singleton_parent': mem.put(p['controller_class'] + practice.presence.CLASS_PARENT, '<Q', p['mono_class'])
    elif fault == 'null_singleton': mem.put(p['statics'], '<Q', 0)
    elif fault == 'null_native': mem.put(p['controller'] + 0x10, '<Q', 0)
    elif fault == 'null_stack': mem.put(p['controller'] + 0x68, '<Q', 0)
    elif fault == 'zero_count': mem.put(p['list'] + 0x18, '<i', 0)
    elif fault == 'large_count': mem.put(p['list'] + 0x18, '<i', 65)
    elif fault == 'small_capacity': mem.put(p['array'] + 0x18, '<Q', 1)
    elif fault == 'large_capacity': mem.put(p['array'] + 0x18, '<Q', practice.MAX_CAPACITY + 1)
    elif fault == 'wrong_array': mem.put(p['array'], '<Q', p['list_class'])
    elif fault == 'null_page': mem.put(p['top_slot'], '<Q', 0)
    elif fault == 'cached_top': mem.put(p['top_slot'], '<Q', p['cached'])
    elif fault == 'wrong_page_parent': mem.put(p['state_class'] + practice.presence.CLASS_PARENT, '<Q', p['mono_class'])
    elif fault == 'wrong_declaring': mem.put(p['params_class'] + 0x50, '<Q', p['page_class'])
    elif fault == 'wrong_params_parent': mem.put(p['params_class'] + practice.presence.CLASS_PARENT, '<Q', p['page_class'])
    elif fault == 'null_args': mem.put(p['page'] + 0x30, '<Q', 0)
    elif fault == 'wrong_args': mem.put(p['args'], '<Q', p['page_class'])
    elif fault == 'invalid_bool': mem.put(p['args'] + 0x28, '<B', 2)
    elif fault == 'null_stage': mem.put(p['args'] + 0x48, '<Q', 0)
    elif fault == 'empty_stage': mem.put(p['stage'] + 0x10, '<i', 0)
    elif fault == 'control_stage': mem.put(p['stage'] + 0x14, '<H', 10)
    else:
        mem.put(p['stage'] + 0x10, '<i', 1)
        mem.put(p['stage'] + 0x14, '<H', 0xD800)
    result = practice.capture_practice_selection(mem.read, context)
    assert result['practice_selection_status'] == 'unavailable'
    assert result['practice_selection'] is None and result['practice_selection_reason']


@pytest.mark.parametrize('field,size', [
    ('slot', 8), ('statics', 8), ('top_slot', 8), ('page', 8),
    ('list_header', 16), ('array_length', 8), ('state', 4), ('args_slot', 8),
    ('boolean', 1), ('stage_slot', 8), ('stage_text', 20),
])
def test_every_selection_watermark_is_reread(fixture, field, size):
    mem, context, p = fixture
    addresses = {'list_header': p['list'] + 0x10, 'array_length': p['array'] + 0x18,
                 'state': p['page'] + 0x28, 'args_slot': p['page'] + 0x30,
                 'boolean': p['args'] + 0x28, 'stage_slot': p['args'] + 0x48,
                 'stage_text': p['stage'] + 0x14}
    address = addresses.get(field, p.get(field))
    count = 0
    def mutate(where, requested):
        nonlocal count
        if (where, requested) == (address, size):
            count += 1
            if count == 2: mem.put(where, '<B', 0xAA)
    mem.on_read = mutate
    result = practice.capture_practice_selection(mem.read, context)
    assert result['practice_selection_status'] == 'unavailable'
    assert result['practice_selection_reason'] == 'practice_selection_changed_during_read'


def test_optional_read_failure_and_budget(fixture, monkeypatch):
    mem, context, _ = fixture
    def denied(_address, _size):
        raise practice.presence.native.access.ApiFailure('ReadProcessMemory', 299)
    assert practice.capture_practice_selection(denied, context)['practice_selection_reason'] == 'practice_selection_read_failed'
    monkeypatch.setattr(practice, 'MAX_READ_CALLS', 1)
    assert practice.capture_practice_selection(mem.read, context)['practice_selection_reason'] == 'practice_selection_read_budget_exhausted'
