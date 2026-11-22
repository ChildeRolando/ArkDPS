"""Synthetic Windows wave/action structure only: no process, game or UI."""

import struct

import pytest

from native_pc import probe_native_pc_stage_spawns as spawns
from test_probe_native_pc_presence import Memory


@pytest.fixture
def fixture():
    mem, p = Memory(), {}

    def typed(name, count, fields, namespace=''):
        klass = mem.klass(name, count, tuple(row[:3] for row in fields), namespace=namespace)
        table, = struct.unpack('<Q', mem.read(klass + spawns.stage.presence.CLASS_FIELDS, 8))
        for index, field, _offset, code in fields:
            type_ref = mem.allocate()
            data = 0
            if code == spawns.stage.TYPE_SZARRAY:
                data = mem.allocate()
                mem.put(data, '<QI', 4737, spawns.stage.TYPE_CLASS << 16)
                p[field + '_element_type'] = data
            mem.put(type_ref, '<QI', data, code << 16)
            mem.put(table + index * 32 + 8, '<Q', type_ref)
            p[field + '_record'] = table + index * 32
            p[field + '_type'] = type_ref
        return klass

    def array(name, values):
        obj = mem.obj(mem.klass(name, namespace=''))
        mem.put(obj + 0x18, '<Q', len(values))
        if values:
            mem.put(obj + 0x20, '<' + 'Q' * len(values), *values)
        return obj

    p['level_class'] = typed('LevelData', 23, spawns.LEVEL_FIELDS, 'Torappu')
    wave_class = p['wave_class'] = typed('WaveData', 5, spawns.WAVE_FIELDS)
    fragment_class = p['fragment_class'] = typed('FragmentData', 2, spawns.FRAGMENT_FIELDS)
    p['action_class'] = typed('ActionData', 24, spawns.ACTION_FIELDS)
    p['string_class'] = mem.klass('String', namespace='System')
    p['key'] = mem.string('enemy_1007_slime_2', p['string_class'])
    p['hidden'] = mem.string('hidden_a', p['string_class'])
    p['scheduled'], p['conditional'], p['invalid'], p['other'] = [mem.obj(p['action_class']) for _ in range(4)]
    for obj, kind, valid, count, hidden, excluded in (
        (p['scheduled'], 0, 1, 3, 0, 0),
        (p['conditional'], 0, 1, 4, p['hidden'], 1),
        (p['invalid'], 0, 0, -999, 0, 255),
        (p['other'], 2, 255, -999, 0, 255),
    ):
        mem.put(obj + 0x10, '<i', kind)
        mem.put(obj + 0x14, '<B', 1)
        mem.put(obj + 0x18, '<Qi', p['key'], count)
        mem.put(obj + 0x38, '<QQQ', hidden, 0, 0)
        mem.put(obj + 0x5E, '<BB', valid, excluded)
    p['actions'] = array('ActionData[]', [p['scheduled'], p['scheduled'], p['conditional'], p['invalid'], p['other']])
    p['fragment'] = mem.obj(fragment_class)
    mem.put(p['fragment'] + 0x18, '<Q', p['actions'])
    p['fragments'] = array('FragmentData[]', [p['fragment']])
    p['wave'] = mem.obj(wave_class)
    mem.put(p['wave'] + 0x20, '<Q', p['fragments'])
    p['waves'] = array('WaveData[]', [p['wave']])
    p['level'] = mem.obj(p['level_class'])
    # A valid but deliberately unreadable branch object: only presence is read.
    p['branches'] = 0x300000000
    mem.put(p['level'] + 0x80, '<QQ', p['waves'], p['branches'])
    mem.reads.clear()
    return mem, {'level_data': p['level']}, p


def fragmented_fixture(fixture):
    """Representative workload, not a claim about the real CE-5 action count."""
    mem, context, p = fixture
    arrays = {name: mem.klass(name, namespace='')
              for name in ('WaveData[]', 'FragmentData[]', 'ActionData[]')}

    def array(name, values):
        address = mem.obj(arrays[name])
        mem.put(address + 0x18, '<Q', len(values))
        mem.put(address + 0x20, '<' + 'Q' * len(values), *values)
        return address

    keys = [mem.string('enemy_fragmented_' + str(i), p['string_class']) for i in range(12)]
    waves = []
    index = 0
    for _wi in range(3):
        fragments = []
        for _fi in range(3):
            actions = []
            for _ai in range(6):
                action = mem.obj(p['action_class'])
                mem.put(action + 0x10, '<i', 0)
                mem.put(action + 0x14, '<B', 1)
                mem.put(action + 0x18, '<Qi', keys[index % 12], index % 3 + 1)
                mem.put(action + 0x38, '<QQQ', p['hidden'] if index % 6 == 5 else 0, 0, 0)
                mem.put(action + 0x5E, '<BB', 1, int(index % 7 == 0))
                actions.append(action)
                index += 1
            fragment = mem.obj(p['fragment_class'])
            mem.put(fragment + 0x18, '<Q', array('ActionData[]', actions))
            fragments.append(fragment)
        wave = mem.obj(p['wave_class'])
        mem.put(wave + 0x20, '<Q', array('FragmentData[]', fragments))
        waves.append(wave)
    mem.put(p['level'] + 0x80, '<Q', array('WaveData[]', waves))
    mem.reads.clear()
    return mem, context, p


def test_fragmented_representative_plan_fits_unchanged_budget_without_dropping_actions(fixture):
    mem, context, _ = fragmented_fixture(fixture)
    report = spawns.capture_stage_spawns(mem.read, context)
    assert report['stage_spawns_status'] == 'available'
    result = report['stage_spawns']
    assert result['wave_count'] == 3 and result['fragment_occurrences'] == 9
    assert result['action_occurrences'] == result['valid_spawn_action_occurrences'] == 54
    assert len(result['spawn_actions']) == 54
    assert result['scheduled_spawn_count'] == 81 and result['conditional_spawn_count'] == 27
    assert result['conditional_spawn_action_count'] == 9
    assert result['scheduled_counted_spawn_count'] == 69
    assert result['not_count_in_total_spawn_count'] == 15
    assert len({(row['wave_index'], row['fragment_index'], row['action_index'])
                for row in result['spawn_actions']}) == 54
    assert [row['enemy_key'] for row in result['spawn_actions']] == [
        'enemy_fragmented_' + str(i % 12) for i in range(54)]
    assert report['stage_spawns_read_cost'] == {'calls': len(mem.reads),
                                               'bytes': sum(size for _, size in mem.reads)}
    assert len(mem.reads) <= 512 and sum(size for _, size in mem.reads) <= 64 * 1024
    assert spawns.MAX_READ_CALLS == 512 and spawns.MAX_READ_BYTES == 64 * 1024


@pytest.mark.parametrize('object_key,offset', [('scheduled', 0x08), ('scheduled', 0x15),
    ('scheduled', 0x24), ('scheduled', 0x50), ('scheduled', 0x5C), ('wave', 0x10),
    ('fragment', 0x10), ('waves', 0x08), ('waves', 0x10), ('key', 0x08), ('action_class', 0x90)])
def test_coalescing_does_not_guard_previously_unconsumed_bytes(fixture, object_key, offset):
    mem, context, p = fixture
    baseline = spawns.capture_stage_spawns(mem.read, context)
    changed = p[object_key] + offset
    reads = 0

    def mutate(where, count):
        nonlocal reads
        if where <= changed < where + count:
            reads += 1
            if reads == 2:
                mem.put(changed, '<B', 1)

    mem.on_read = mutate
    report = spawns.capture_stage_spawns(mem.read, context)
    assert reads == 2
    assert report['stage_spawns_status'] == 'available'
    assert report['stage_spawns'] == baseline['stage_spawns']


def test_counts_occurrences_not_distinct_pointers_and_never_adds_branches(fixture):
    mem, context, p = fixture
    report = spawns.capture_stage_spawns(mem.read, context)
    assert report['stage_spawns_status'] == 'available'
    result = report['stage_spawns']
    assert result['schema_version'] == 1 and result['scope'] == 'current_stage_waves_spawn_structure'
    assert result['wave_count'] == result['fragment_occurrences'] == 1
    assert result['action_occurrences'] == 5 and result['valid_spawn_action_occurrences'] == 3
    assert result['scheduled_spawn_count'] == result['scheduled_counted_spawn_count'] == 6
    assert result['conditional_spawn_count'] == result['not_count_in_total_spawn_count'] == 4
    assert result['conditional_spawn_action_count'] == result['invalid_spawn_action_occurrences'] == 1
    assert [row['action_index'] for row in result['spawn_actions']] == [0, 1, 2]
    assert result['branch_container_present'] and not result['branches_counted']
    assert not result['dynamic_spawns_counted']
    assert not any(p['branches'] <= address < p['branches'] + 4096 for address, _ in mem.reads)
    assert str(p['level']) not in repr(report)
    assert len(mem.reads) <= spawns.MAX_READ_CALLS
    assert sum(size for _, size in mem.reads) <= spawns.MAX_READ_BYTES


def test_reused_wave_counts_again_at_its_second_position(fixture):
    mem, context, p = fixture
    mem.put(p['waves'] + 0x18, '<Q', 2)
    mem.put(p['waves'] + 0x20, '<QQ', p['wave'], p['wave'])
    result = spawns.capture_stage_spawns(mem.read, context)['stage_spawns']
    assert result['scheduled_spawn_count'] == 12 and result['conditional_spawn_count'] == 8
    assert [row['wave_index'] for row in result['spawn_actions']] == [0, 0, 0, 1, 1, 1]


def test_empty_waves_are_observed_empty_and_empty_group_string_is_unconditional(fixture):
    mem, context, p = fixture
    empty = mem.string('', p['string_class'])
    mem.put(p['scheduled'] + 0x38, '<Q', empty)
    assert spawns.capture_stage_spawns(mem.read, context)['stage_spawns']['scheduled_spawn_count'] == 6
    mem.put(p['waves'] + 0x18, '<Q', 0)
    result = spawns.capture_stage_spawns(mem.read, context)['stage_spawns']
    assert result['scheduled_spawn_count'] == result['conditional_spawn_count'] == 0
    assert result['spawn_actions'] == []


@pytest.mark.parametrize('offset', [0x38, 0x40, 0x48])
def test_each_hidden_or_random_group_is_conditional_without_assuming_execution(fixture, offset):
    mem, context, p = fixture
    mem.put(p['scheduled'] + offset, '<Q', p['hidden'])
    result = spawns.capture_stage_spawns(mem.read, context)['stage_spawns']
    assert result['scheduled_spawn_count'] == result['scheduled_counted_spawn_count'] == 0
    assert result['conditional_spawn_count'] == 10
    assert result['conditional_spawn_action_count'] == 3


def test_not_count_in_total_is_separate_and_null_branches_are_not_counted(fixture):
    mem, context, p = fixture
    mem.put(p['scheduled'] + 0x5F, '<B', 1)
    mem.put(p['level'] + 0x88, '<Q', 0)
    result = spawns.capture_stage_spawns(mem.read, context)['stage_spawns']
    assert result['scheduled_spawn_count'] == 6
    assert result['scheduled_counted_spawn_count'] == 0
    assert result['not_count_in_total_spawn_count'] == 10
    assert not result['branch_container_present'] and not result['branches_counted']


def test_repeated_arrays_still_obey_global_occurrence_budget(fixture, monkeypatch):
    mem, context, p = fixture
    mem.put(p['waves'] + 0x18, '<Q', 2)
    mem.put(p['waves'] + 0x20, '<QQ', p['wave'], p['wave'])
    monkeypatch.setattr(spawns, 'MAX_ACTION_OCCURRENCES', 6)
    report = spawns.capture_stage_spawns(mem.read, context)
    assert report['stage_spawns_status'] == 'unavailable'
    assert report['stage_spawns_reason'] == 'stage_spawns_action_occurrence_count'


@pytest.mark.parametrize('fault', ['context', 'null_array', 'array_count', 'null_slot', 'type', 'count',
                                  'negative_count', 'large_count',
                                  'valid', 'boolean', 'key', 'field_count', 'field_offset', 'field_type',
                                  'inline_array', 'occurrences', 'total', 'calls', 'bytes'])
def test_invalid_or_over_budget_structure_is_unknown_not_zero(fixture, monkeypatch, fault):
    mem, context, p = fixture
    if fault == 'context': context = {}
    elif fault == 'null_array': mem.put(p['level'] + 0x80, '<Q', 0)
    elif fault == 'array_count': mem.put(p['waves'] + 0x18, '<Q', spawns.MAX_WAVES + 1)
    elif fault == 'null_slot': mem.put(p['waves'] + 0x20, '<Q', 0)
    elif fault == 'type': mem.put(p['scheduled'] + 0x10, '<i', 14)
    elif fault == 'count': mem.put(p['scheduled'] + 0x20, '<i', 0)
    elif fault == 'negative_count': mem.put(p['scheduled'] + 0x20, '<i', -1)
    elif fault == 'large_count': mem.put(p['scheduled'] + 0x20, '<i', spawns.MAX_SPAWN_PER_ACTION + 1)
    elif fault == 'valid': mem.put(p['scheduled'] + 0x5E, '<B', 2)
    elif fault == 'boolean': mem.put(p['scheduled'] + 0x5F, '<B', 2)
    elif fault == 'key': mem.put(p['scheduled'] + 0x18, '<Q', 0)
    elif fault == 'field_count': mem.put(p['action_class'] + spawns.stage.presence.CLASS_FIELD_COUNT, '<H', 23)
    elif fault == 'field_offset': mem.put(p['count_record'] + 24, '<i', 0x24)
    elif fault == 'field_type': mem.put(p['count_type'] + 8, '<I', spawns.stage.TYPE_STRING << 16)
    elif fault == 'inline_array': mem.put(p['waves_element_type'] + 8, '<I', spawns.TYPE_ENUM << 16)
    elif fault == 'occurrences': monkeypatch.setattr(spawns, 'MAX_ACTION_OCCURRENCES', 4)
    elif fault == 'total': monkeypatch.setattr(spawns, 'MAX_TOTAL_SPAWN', 9)
    elif fault == 'calls': monkeypatch.setattr(spawns, 'MAX_READ_CALLS', 1)
    elif fault == 'bytes': monkeypatch.setattr(spawns, 'MAX_READ_BYTES', 1)
    result = spawns.capture_stage_spawns(mem.read, context)
    assert result['stage_spawns_status'] == 'unavailable' and result['stage_spawns'] is None
    assert result['stage_spawns_reason'].startswith('stage_spawns_')


def test_read_denial_or_short_read_does_not_publish_an_empty_plan(fixture):
    mem, context, _ = fixture

    def denied(_address, _size):
        raise spawns.stage.presence.native.access.ApiFailure('ReadProcessMemory', 299)

    for read in (denied, lambda address, size: mem.read(address, size)[:-1]):
        report = spawns.capture_stage_spawns(read, context)
        assert report['stage_spawns_status'] == 'unavailable' and report['stage_spawns'] is None


@pytest.mark.parametrize('field', ['level', 'slots', 'count', 'valid', 'key', 'group'])
def test_same_frame_mutation_rejects_the_whole_witness(fixture, field):
    mem, context, p = fixture
    address, size, changed, encoding, value = {
        'level': (p['level'] + 0x80, 16, p['level'] + 0x80, '<Q', 0),
        'slots': (p['waves'] + 0x20, 8, p['waves'] + 0x20, '<Q', p['fragment']),
        'count': (p['scheduled'] + 0x18, 12, p['scheduled'] + 0x20, '<i', 8),
        'valid': (p['scheduled'] + 0x5E, 1, p['scheduled'] + 0x5E, '<B', 0),
        'key': (p['scheduled'] + 0x18, 12, p['scheduled'] + 0x18, '<Q', p['hidden']),
        'group': (p['conditional'] + 0x38, 24, p['conditional'] + 0x38, '<Q', 0),
    }[field]
    reads = 0

    def mutate(where, count):
        nonlocal reads
        if where <= changed and changed + struct.calcsize(encoding) <= where + count:
            reads += 1
            if reads == 2:
                mem.put(changed, encoding, value)

    mem.on_read = mutate
    report = spawns.capture_stage_spawns(mem.read, context)
    assert report['stage_spawns_status'] == 'unavailable'
    assert report['stage_spawns_reason'] == 'stage_spawns_changed_during_read'
