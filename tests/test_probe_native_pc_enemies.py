"""Synthetic exact-build layouts; no game, process handle or admin access."""

import struct

import pytest

import collector  # Initialize the vendored imports used by the projection.
from enemy_telemetry import NativeEnemyObservations
from native_pc import probe_native_pc_enemies as enemies
from test_probe_native_pc_presence import Memory


@pytest.fixture
def fixture():
    mem = Memory()
    p = {}
    p['battle_class'] = mem.klass('BattleController', 515, enemies.presence.BATTLE_FIELDS)
    p['manager_class'] = mem.klass('UnitManager', 7, enemies.MANAGER_FIELDS)
    container_class = mem.klass('UnorderedArray`1', 3, enemies.presence.CONTAINER_FIELDS,
                                namespace='Torappu')
    visual = mem.klass('VisualObject')
    obj = mem.klass('BObject', 9, enemies.presence.OBJECT_FIELDS, visual)
    p['entity_class'] = mem.klass('Entity', 410, (*enemies.ENTITY_FIELDS, *enemies.ENTITY_ID_FIELDS), obj)
    p['statics'] = mem.allocate()
    mem.put(p['entity_class'] + enemies.presence.native.CLASS_STATICS, '<Q', p['statics'])
    unit = mem.klass('Unit', parent=p['entity_class'])
    p['unit_class'] = unit
    p['enemy_class'] = mem.klass('Enemy', 408, enemies.ENEMY_FIELDS, unit)
    p['data_class'] = mem.klass('EnemyData', 18, enemies.DATA_FIELDS, namespace='')
    string = mem.klass('String', namespace='System')
    p['string_class'] = string
    p['battle'], p['manager'] = mem.obj(p['battle_class']), mem.obj(p['manager_class'])
    p['container'], p['array'] = mem.obj(container_class), mem.allocate()
    p['data'] = mem.obj(p['data_class'])
    p['key'] = mem.string('enemy_1001_soldier', string)
    mem.put(p['data'] + 0x20, '<Q', p['key'])
    mem.put(p['battle'] + enemies.presence.BATTLE_SIDE, '<i', 1)
    mem.put(p['battle'] + enemies.presence.BATTLE_UNIT_MANAGER, '<Q', p['manager'])
    mem.put(p['manager'] + enemies.ENEMIES, '<Q', p['container'])
    mem.put(p['container'] + 0x10, '<QQi', p['array'], 0, 2)
    mem.put(p['array'] + 0x18, '<Q', 16)
    p['objects'] = [mem.obj(p['enemy_class']) for _ in range(2)]
    for index, address in enumerate(p['objects']):
        mem.put(address + 0x10, '<Q', mem.allocate())
        mem.put(address + 0x18, '<IiiBBB', 101 + index, 2, 2, 1, 1, 0)
        mem.put(address + enemies.ENEMY_DATA, '<Q', p['data'])
        mem.put(address + enemies.ENTITY_HP, '<q', int((1000.5 - index * 250) * (1 << 32)))
    mem.put(p['array'] + 0x20, '<QQ', *p['objects'])
    return mem, {'battle_controller': p['battle']}, p


def dense_fixture(fixture, count):
    mem, context, p = fixture
    objects = list(p['objects'])
    while len(objects) < count:
        address = mem.obj(p['enemy_class'])
        mem.put(address + 0x10, '<Q', mem.allocate())
        mem.put(address + 0x18, '<IiiBBB', 101 + len(objects), 2, 2, 1, 1, 0)
        mem.put(address + enemies.ENEMY_DATA, '<Q', p['data'])
        mem.put(address + enemies.ENTITY_HP, '<q', int(1000 * (1 << 32)))
        objects.append(address)
    mem.put(p['container'] + 0x20, '<i', count)
    mem.put(p['array'] + 0x18, '<Q', max(16, count))
    mem.put(p['array'] + 0x20, '<' + 'Q' * count, *objects[:count])
    mem.reads.clear()
    return mem, context, p


class LegacyEnemyRead(enemies.EnemyRead):
    """Pre-optimization guard strategy, retained only as an offline comparator."""

    def enemy_values(self, address):
        klass = self.ref(address, guard=True)
        lineage = self.enemy_lineage(klass)
        native = self.ref(address + 0x10, nullable=True, guard=True)
        flags = struct.unpack('<IiiBBB', self.guarded(address + 0x18, 15))
        hp_raw, = struct.unpack('<q', self.guarded(address + enemies.ENTITY_HP, 8))
        return lineage, native, flags, hp_raw

    def enemy_key(self, data):
        klass = self.expect_class(data, 'EnemyData', '')
        self.fields(klass, 18, enemies.DATA_FIELDS)
        return self.actor_id(self.ref(data + 0x20, guard=True))


@pytest.mark.parametrize('count', [2, 8, 16])
def test_merged_reads_preserve_baseline_payload_and_every_guarded_byte(fixture, monkeypatch, count):
    mem, context, _ = dense_fixture(fixture, count)
    readers = []
    optimized_type = enemies.EnemyRead

    class Baseline(LegacyEnemyRead):
        def __init__(self, read):
            super().__init__(read)
            readers.append(self)

    class Optimized(optimized_type):
        def __init__(self, read):
            super().__init__(read)
            readers.append(self)

    monkeypatch.setattr(enemies, 'EnemyRead', Baseline)
    baseline = enemies.capture_enemies(mem.read, context)
    baseline_calls = len(mem.reads)
    mem.reads.clear()
    monkeypatch.setattr(enemies, 'EnemyRead', Optimized)
    optimized = enemies.capture_enemies(mem.read, context)
    assert optimized == baseline
    assert optimized['enemies_status'] == 'available'
    assert len(mem.reads) < baseline_calls
    if count >= 16:
        assert len(mem.reads) < baseline_calls * .6
    # Each old class/ref/identity/HP/string guard remains inside a closing
    # optimized guard. Shared EnemyData guards may appear only once now.
    old, new = readers
    new_guards = new.guards + [(address + start, raw[start:end])
                              for address, raw in new.enemy_prefixes
                              for start, end in enemies.ENEMY_PREFIX_GUARDS]
    assert all(any(start <= address and address + len(raw) <= start + len(block)
                   for start, block in new_guards) for address, raw in old.guards)


@pytest.mark.parametrize('offset', [0x08, 0x0F, 0x27, 0x30, 0x3F])
def test_unconsumed_prefix_bytes_can_change_without_rejecting_a_valid_witness(fixture, monkeypatch, offset):
    mem, context, p = fixture
    optimized_type = enemies.EnemyRead
    results = []
    for reader_type in (LegacyEnemyRead, optimized_type):
        reads = 0
        mem.put(p['objects'][0] + offset, '<B', 0)

        def mutate(where, count):
            nonlocal reads
            if where == p['objects'][0] and count in (8, enemies.ENTITY_HP + 8):
                reads += 1
                if reads == 2:
                    mem.put(where + offset, '<B', 1)

        mem.on_read = mutate
        monkeypatch.setattr(enemies, 'EnemyRead', reader_type)
        results.append(enemies.capture_enemies(mem.read, context))
        assert reads == 2
        assert results[-1]['enemies_status'] == 'available'
    assert results[0] == results[1]


@pytest.mark.parametrize('count', [32, 64, 96])
def test_dense_shared_data_stays_complete_within_the_same_budgets(fixture, count):
    mem, context, _ = dense_fixture(fixture, count)
    result = enemies.capture_enemies(mem.read, context)
    assert result['enemies_status'] == 'available'
    assert result['enemies']['collection_count'] == count
    assert len(result['enemies']['entities']) == count
    assert len({row['instance_uid'] for row in result['enemies']['entities']}) == count
    assert len(mem.reads) <= 512
    assert sum(size for _, size in mem.reads) <= 64 * 1024


def test_dense_comparison_improves_availability_without_raising_limits(fixture, monkeypatch):
    mem, context, _ = dense_fixture(fixture, 32)
    optimized = enemies.capture_enemies(mem.read, context)
    mem.reads.clear()
    monkeypatch.setattr(enemies, 'EnemyRead', LegacyEnemyRead)
    baseline = enemies.capture_enemies(mem.read, context)
    assert optimized['enemies_status'] == 'available'
    assert baseline == {'enemies_status': 'unavailable',
                        'enemies_reason': 'enemy_read_budget_exhausted', 'enemies': None}
    assert enemies.MAX_READ_CALLS == 512 and enemies.MAX_READ_BYTES == 64 * 1024


def test_unsupported_dense_limit_is_unavailable_not_a_partial_or_empty_roster(fixture):
    mem, context, _ = dense_fixture(fixture, 128)
    result = enemies.capture_enemies(mem.read, context)
    assert result == {'enemies_status': 'unavailable',
                      'enemies_reason': 'enemy_read_budget_exhausted', 'enemies': None}


def test_data_key_cache_is_exact_per_pointer_and_rebuilt_each_transaction(fixture):
    mem, context, p = fixture
    other_data = mem.obj(p['data_class'])
    other_key = mem.string('enemy_1001_soldier_2', p['string_class'])
    mem.put(other_data + 0x20, '<Q', other_key)
    mem.put(p['objects'][1] + enemies.ENEMY_DATA, '<Q', other_data)
    first = enemies.capture_enemies(mem.read, context)['enemies']['entities']
    assert [row['enemy_key'] for row in first] == ['enemy_1001_soldier', 'enemy_1001_soldier_2']
    mem.put(other_data + 0x20, '<Q', p['key'])
    second = enemies.capture_enemies(mem.read, context)['enemies']['entities']
    assert [row['enemy_key'] for row in second] == ['enemy_1001_soldier'] * 2


def test_distinct_instances_with_shared_enemy_key_have_current_hp_only(fixture):
    mem, context, p = fixture
    result = enemies.capture_enemies(mem.read, context)
    assert result['enemies_status'] == 'available'
    value = result['enemies']
    assert value['scope'] == 'unit_manager_enemies_membership'
    assert value['local_side'] == 1 and value['collection_count'] == 2
    rows = value['entities']
    assert [row['instance_uid'] for row in rows] == [101, 102]
    assert [row['hp'] for row in rows] == [1000.5, 750.5]
    for row in rows:
        assert row['enemy_key'] == 'enemy_1001_soldier'
        assert row['hp_status'] == 'available' and row['hp_reason'] is None
        assert row['max_hp'] is None and row['max_hp_status'] == 'unavailable'
        assert 'alive' not in row and 'death' not in row
    assert all(str(address) not in repr(result) for address in p['objects'])
    assert len(mem.reads) <= enemies.MAX_READ_CALLS
    assert sum(size for _, size in mem.reads) <= enemies.MAX_READ_BYTES


@pytest.mark.parametrize('eid,events_supported', [
    ('trap_1112_acblzd', True), ('char_143_ghost', False),
])
def test_guard_round_shows_enemies_and_other_hostile_units_in_the_same_snapshot(
        fixture, eid, events_supported):
    mem, context, p = fixture
    character = mem.klass('Character', parent=p['unit_class'])
    klass = character
    if events_supported:
        token = mem.klass('Token', parent=character)
        klass = mem.klass('Trap', parent=token)
    unit = p['objects'][0]
    mem.put(unit, '<Q', klass)
    mem.put(unit + enemies.ENEMY_DATA, '<Q', 0)
    mem.put(unit + enemies.ENTITY_ID, '<Q', mem.string(eid, p['string_class']))

    snapshot = enemies.capture_enemies(mem.read, context)

    assert snapshot['enemies_status'] == 'available'
    view = NativeEnemyObservations().observe(snapshot, frame=25, game_time=25 / 30, catalog={})
    assert view['ready']
    assert [(row['eid'], row['hp']) for row in view['rows']] == [
        (eid, 1000.5), ('enemy_1001_soldier', 750.5),
    ]
    assert view['totals']['remaining_hp_complete']
    assert view.get('enemy_events_supported', True) is events_supported


def test_changes_between_polls_do_not_become_damage_or_death_events(fixture):
    mem, context, p = fixture
    first = enemies.capture_enemies(mem.read, context)['enemies']['entities']
    mem.put(p['objects'][0] + enemies.ENTITY_HP, '<q', 0)
    mem.put(p['objects'][1] + 0x26, '<B', 1)
    second = enemies.capture_enemies(mem.read, context)['enemies']['entities']
    assert first[0]['instance_uid'] == second[0]['instance_uid']
    assert second[0]['hp'] == 0
    assert second[1]['is_disappeared']
    assert all('events' not in row for row in second)


def test_observed_empty_is_different_from_unavailable(fixture):
    mem, context, p = fixture
    mem.put(p['container'] + 0x20, '<i', 0)
    result = enemies.capture_enemies(mem.read, context)
    assert result['enemies_status'] == 'available'
    assert result['enemies']['entities'] == []


@pytest.mark.parametrize('condition,reason', [
    ('override', 'hp_getter_override_present'),
    ('statics', 'hp_getter_storage_uninitialized'),
    ('special_fp', 'hp_out_of_supported_range'),
])
def test_unverified_hp_stays_unknown_without_discarding_membership(fixture, condition, reason):
    mem, context, p = fixture
    if condition == 'override':
        mem.put(p['statics'] + enemies.HP_GETTER_OVERRIDE, '<Q', mem.allocate())
    elif condition == 'statics':
        mem.put(p['entity_class'] + enemies.presence.native.CLASS_STATICS, '<Q', 0)
    else:
        mem.put(p['objects'][0] + enemies.ENTITY_HP, '<q', (1 << 63) - 1)
    result = enemies.capture_enemies(mem.read, context)
    assert result['enemies_status'] == 'available'
    row = result['enemies']['entities'][0]
    assert row['hp'] is None and row['hp_reason'] == reason


@pytest.mark.parametrize('fault', [
    'count', 'capacity', 'null_slot', 'duplicate_object', 'duplicate_uid',
    'field_count', 'field_offset', 'namespace', 'inheritance', 'not_unit', 'flags', 'bad_key',
])
def test_invalid_or_unsupported_source_is_unknown_not_empty(fixture, fault):
    mem, context, p = fixture
    if fault == 'count':
        mem.put(p['container'] + 0x20, '<i', enemies.MAX_ENEMIES + 1)
    elif fault == 'capacity':
        mem.put(p['array'] + 0x18, '<Q', 1)
    elif fault in ('null_slot', 'duplicate_object'):
        mem.put(p['array'] + 0x28, '<Q', 0 if fault == 'null_slot' else p['objects'][0])
    elif fault == 'duplicate_uid':
        mem.put(p['objects'][1] + 0x18, '<I', 101)
    elif fault == 'field_count':
        mem.put(p['entity_class'] + enemies.presence.CLASS_FIELD_COUNT, '<H', 409)
    elif fault == 'field_offset':
        table, = struct.unpack('<Q', mem.read(p['enemy_class'] + enemies.presence.CLASS_FIELDS, 8))
        mem.put(table + 104 * 32 + 24, '<i', 0x538)
    elif fault == 'namespace':
        mem.put(p['data_class'] + 0x18, '<Q', mem.text('Other'))
    elif fault == 'inheritance':
        mem.put(p['enemy_class'] + enemies.presence.CLASS_PARENT, '<Q', p['enemy_class'])
    elif fault == 'not_unit':
        mem.put(p['objects'][0], '<Q', p['entity_class'])
    elif fault == 'flags':
        mem.put(p['objects'][0] + 0x24, '<B', 3)
    else:
        mem.put(p['key'] + 0x14, '<H', ord(' '))
    result = enemies.capture_enemies(mem.read, context)
    assert result['enemies_status'] == 'unavailable'
    assert result['enemies'] is None


@pytest.mark.parametrize('field', ['hp', 'class', 'collection', 'slots', 'uid', 'data', 'key', 'native', 'override'])
def test_mutation_during_read_rejects_the_whole_optional_witness(fixture, field):
    mem, context, p = fixture
    address, size, changed, encoding, values = {
        'hp': (p['objects'][0], enemies.ENTITY_HP + 8,
               p['objects'][0] + enemies.ENTITY_HP, '<q', (1,)),
        'class': (p['objects'][0], enemies.ENTITY_HP + 8,
                  p['objects'][0], '<Q', (p['data_class'],)),
        'collection': (p['container'] + 0x10, 20, p['container'] + 0x10, '<QQi', (p['array'], 0, 1)),
        'slots': (p['array'] + 0x20, 16, p['array'] + 0x20, '<QQ', tuple(reversed(p['objects']))),
        'uid': (p['objects'][0], enemies.ENTITY_HP + 8,
                p['objects'][0] + 0x18, '<I', (999,)),
        'data': (p['objects'][0] + enemies.ENEMY_DATA, 8,
                 p['objects'][0] + enemies.ENEMY_DATA, '<Q', (mem.allocate(),)),
        'key': (p['data'] + 0x20, 8, p['data'] + 0x20,
                '<Q', (mem.string('enemy_changed', p['string_class']),)),
        'native': (p['objects'][0], enemies.ENTITY_HP + 8,
                   p['objects'][0] + 0x10, '<Q', (0,)),
        'override': (p['statics'] + enemies.HP_GETTER_OVERRIDE, 8,
                     p['statics'] + enemies.HP_GETTER_OVERRIDE, '<Q', (mem.allocate(),)),
    }[field]
    reads = 0
    def mutate(where, count):
        nonlocal reads
        if (where, count) == (address, size):
            reads += 1
            if reads == 2:
                mem.put(changed, encoding, *values)
    mem.on_read = mutate
    result = enemies.capture_enemies(mem.read, context)
    assert result == {'enemies_status': 'unavailable',
                      'enemies_reason': 'enemy_changed_during_read', 'enemies': None}


def test_read_denial_short_read_and_local_budget_are_optional_failures(fixture, monkeypatch):
    mem, context, _ = fixture
    def denied(_address, _size):
        raise enemies.presence.native.access.ApiFailure('ReadProcessMemory', 299)
    assert enemies.capture_enemies(denied, context)['enemies_reason'] == 'enemy_read_failed'
    assert enemies.capture_enemies(lambda a, s: mem.read(a, s)[:-1], context)[
        'enemies_reason'] == 'enemy_short_read'
    monkeypatch.setattr(enemies, 'MAX_READ_CALLS', 1)
    assert enemies.capture_enemies(mem.read, context)['enemies_reason'] == 'enemy_read_budget_exhausted'
