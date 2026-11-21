"""Synthetic runtime roots exercise the native research reader without a game."""

import importlib.util
from pathlib import Path
import struct

import pytest


SPEC = importlib.util.spec_from_file_location(
    'native_pc_battle_probe', Path(__file__).parents[1] / 'acceptance' / 'probe_native_pc_battle.py')
battle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(battle)


class FakeApi:
    def __init__(self, root):
        self.path = root / 'Arknights.exe'
        self.module_path = root / 'GameAssembly.dll'
        self.base = 0x180000000
        self.memory = {}
        self.opens, self.closes, self.reads = [], [], []
        self.on_read = None
        self.fail_open = False
        self.fail_read = False
        self.next_address = 0x200000000
        self.created = 123456789
        self.exited = 0
        self.time_error = None
        self.exit_error = None
        self.exit_value = None

    def allocate(self, size=4096):
        result = self.next_address
        self.next_address += (size + 4095) // 4096 * 4096
        return result

    def write(self, address, data):
        self.memory.update({address + index: value for index, value in enumerate(data)})

    def put(self, address, format, *values):
        self.write(address, struct.pack(format, *values))

    def named_processes(self):
        return [42]

    def open_process(self, pid, rights):
        self.opens.append((pid, rights))
        if self.fail_open and rights == battle.access.READ_ACCESS:
            raise battle.access.ApiFailure('OpenProcess', 5)
        return rights

    def image_path(self, handle):
        return self.path

    def process_times(self, handle):
        if self.time_error is not None:
            raise battle.access.ApiFailure('GetProcessTimes', self.time_error)
        return self.created, self.exited

    def exit_code(self, handle):
        if self.exit_error is not None:
            raise battle.access.ApiFailure('GetExitCodeProcess', self.exit_error)
        if self.exit_value is not None:
            return self.exit_value
        return 0 if self.exited else battle.access.STILL_ACTIVE

    def modules(self, handle):
        return [(self.module_path, self.base, 0x10000000)]

    def close(self, handle):
        self.closes.append(handle)

    def read(self, handle, address, size):
        self.reads.append((address, size))
        if self.fail_read:
            raise battle.access.ApiFailure('ReadProcessMemory', 299, 0)
        if self.on_read:
            self.on_read(address, size)
        return bytes(self.memory.get(address + index, 0) for index in range(size))

    def text(self, value):
        address = self.allocate()
        self.write(address, value.encode('ascii') + b'\0')
        return address

    def klass(self, name, namespace, count=0, fields=()):
        address, table = self.allocate(), self.allocate(max(4096, count * 32))
        self.put(address + 0x10, '<QQ', self.text(name), self.text(namespace))
        self.put(address + battle.CLASS_FIELDS, '<Q', table)
        self.put(address + battle.CLASS_FIELD_COUNT, '<H', count)
        for index, field, offset in fields:
            self.put(table + index * 32, '<QQQiI', self.text(field), 0, address, offset, 0)
        return address

    def ustring(self, value, klass):
        address = self.allocate()
        raw = value.encode('utf-16-le')
        self.put(address, '<Q', klass)
        self.put(address + 0x10, '<i', len(raw) // 2)
        self.write(address + 0x14, raw)
        return address


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(battle.access, 'verify_build', lambda root: (
        {'known_build': True}, [('known_code', 0x1000, b'known')]))
    api = FakeApi(tmp_path)
    api.write(api.base + 0x1000, b'known')
    klass = api.klass('BattleController', 'Torappu.Battle', 515, battle.BATTLE_FIELDS)
    parent = api.klass('SingletonMonoBehaviour`1', 'Torappu')
    logger_class = api.klass('BattleLogger', 'Torappu.Battle', 46, battle.LOGGER_FIELDS)
    stats_class = api.klass('BattleStats', '', 44, battle.STATS_FIELDS)
    level_class = api.klass('LevelData', 'Torappu', 23, battle.LEVEL_FIELDS)
    string_class = api.klass('String', 'System')
    pause_class = api.klass('EnableStateWithKey`1', 'Torappu', 1, battle.PAUSE_FIELDS)
    key_set_class = api.klass('ListSet`1', 'Torappu', 1, battle.KEY_SET_FIELDS)
    key_list_class = api.klass('List`1', 'System.Collections.Generic', 6, battle.KEY_LIST_FIELDS)
    singleton, clock = api.allocate(), api.allocate()
    controller, native, logger, stats, level = [api.allocate() for _ in range(5)]
    api.put(api.base + battle.BATTLE_CLASS_SLOT_RVA, '<Q', klass)
    api.put(klass + battle.CLASS_PARENT, '<Q', parent)
    api.put(klass + battle.CLASS_STATICS, '<Q', clock)
    api.put(parent + battle.CLASS_STATICS, '<Q', singleton)
    api.put(singleton, '<Q', controller)
    api.put(controller, '<Q', klass)
    api.put(controller + 0x10, '<Q', native)
    api.put(controller + battle.BATTLE_LOGGER, '<Q', logger)
    api.put(controller + battle.BATTLE_LEVEL, '<Q', level)
    api.put(controller + battle.BATTLE_STATE, '<i', 2)
    api.put(controller + battle.BATTLE_SPEED, '<i', 1)
    api.put(controller + battle.BATTLE_ORIGIN_TIME_SCALE, '<f', 1)
    api.put(level, '<Q', level_class)
    stage = api.ustring('Obt/Main/level_main_01-07', string_class)
    api.put(level + battle.LEVEL_ID, '<Q', stage)
    pause, key_set, key_list = [api.allocate() for _ in range(3)]
    api.put(controller + battle.BATTLE_PAUSE, '<Q', pause)
    api.put(pause, '<Q', pause_class)
    api.put(pause + 0x10, '<Q', key_set)
    api.put(key_set, '<Q', key_set_class)
    api.put(key_set + 0x10, '<Q', key_list)
    api.put(key_list, '<Q', key_list_class)
    api.put(logger, '<Q', logger_class)
    api.put(logger + battle.LOGGER_CONTROLLER, '<Q', controller)
    api.put(logger + battle.LOGGER_STATS, '<Q', stats)
    api.put(stats, '<Q', stats_class)
    api.put(stats + battle.STATS_TOTAL_HEAL, '<ff', 50.25, -123.5)
    api.put(clock + battle.CLOCK_FRAME, '<Iq', 30, 1 << 32)
    return tmp_path, api, dict(klass=klass, parent=parent, logger_class=logger_class,
                              stats_class=stats_class, singleton=singleton, clock=clock,
                              controller=controller, native=native, logger=logger,
                              stats=stats, level=level, stage=stage, string_class=string_class,
                              pause=pause, key_set=key_set, key_list=key_list)


def test_stable_native_sample_preserves_raw_counter_sign_and_closes_handle(runtime):
    root, api, p = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        assert probe.pid == 42
        assert probe.process_instance == (42, api.created)
        assert probe.battle_identity is None
        result = probe.sample()
        assert probe.battle_identity == tuple(p[key] for key in (
            'controller', 'native', 'logger', 'stats', 'level'))
        assert result['status'] == 'sample_available'
        sample = result['sample']
        assert {key: sample[key] for key in ('battle_serial', 'frame', 'game_time', 'battle_state',
                'total_damage', 'total_heal', 'verbose_recorder_present')} == {
                    'battle_serial': 1, 'frame': 30, 'game_time': 1, 'battle_state': 2,
                    'total_damage': -123.5, 'total_heal': 50.25, 'verbose_recorder_present': False}
        assert sample['stage_id'] == 'Obt/Main/level_main_01-07'
        assert sample['paused'] is False and sample['pause_status'] == 'available'
        assert sample['speed_level'] == 1 and sample['speed_name'] == 'STANDARD'
        assert sample['outcome'] is None and sample['outcome_reason'] == 'battle_not_finished'
        assert result['research_only'] and result['limitations']
        assert probe.reader.calls <= battle.MAX_READ_CALLS
        assert probe.reader.bytes_requested <= battle.MAX_READ_BYTES
    assert api.closes == [battle.access.PROCESS_QUERY_LIMITED_INFORMATION, battle.access.READ_ACCESS]
    assert probe.sample()['status'] == 'closed'


@pytest.mark.parametrize('exit_code', [0, 1, 0xFFFFFFFF])
def test_confirmed_exit_before_poll_performs_no_memory_reads(runtime, exit_code):
    root, api, _ = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        api.reads.clear()
        api.exit_value = exit_code
        result = probe.sample()
        assert result['status'] == 'process_exited'
        assert result['reason'] == 'owned_process_exited'
        assert api.reads == []
        assert 'sample' not in result
    assert api.opens == [(42, 0x1000), (42, 0x410)]


@pytest.mark.parametrize('null_singleton', [False, True])
def test_exit_during_acquisition_overrides_sample_or_confirmed_null(runtime, null_singleton):
    root, api, p = runtime
    if null_singleton:
        api.put(p['singleton'], '<Q', 0)
    with battle.NativePcBattleProbe(root, api) as probe:
        api.on_read = lambda _address, _size: setattr(api, 'exit_value', 0)
        result = probe.sample()
    assert result['status'] == 'process_exited'
    assert 'sample' not in result


@pytest.mark.parametrize('query', ['creation', 'exit'])
@pytest.mark.parametrize('during_read', [False, True])
def test_lifetime_query_failure_never_fabricates_process_exit(runtime, query, during_read):
    root, api, _ = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        def fail_query(*_args):
            setattr(api, 'time_error' if query == 'creation' else 'exit_error', 5)
        if during_read:
            api.on_read = fail_query
        else:
            fail_query()
        result = probe.sample()
    assert result['status'] == 'read_failed'
    assert result['diagnostic'] == {
        'api': 'GetProcessTimes' if query == 'creation' else 'GetExitCodeProcess',
        'win32_error': 5}
    assert 'sample' not in result


def test_reserved_exit_code_and_undefined_exit_time_do_not_confirm_departure(runtime):
    root, api, _ = runtime
    # Windows leaves GetProcessTimes.exitTime undefined while running. Even a
    # positive value must be ignored when termination status is STILL_ACTIVE.
    api.exited = 987654321
    api.exit_value = battle.access.STILL_ACTIVE
    with battle.NativePcBattleProbe(root, api) as probe:
        assert probe.sample()['status'] == 'sample_available'
        api.fail_read = True
        assert probe.sample()['status'] == 'read_failed'


@pytest.mark.parametrize('during_read', [False, True])
def test_owned_process_creation_identity_is_checked_before_and_after(runtime, during_read):
    root, api, _ = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        def change_identity(*_args):
            api.created += 1
        if during_read:
            api.on_read = change_identity
        else:
            change_identity()
        result = probe.sample()
    assert result['status'] == 'process_changed'
    assert result['reason'] == 'process_creation_time_changed'
    assert 'sample' not in result


def test_pid_reuse_between_discovery_and_owned_handle_never_reads_memory(runtime, monkeypatch):
    root, api, _ = runtime
    original_open = api.open_process
    def open_reused(pid, rights):
        handle = original_open(pid, rights)
        if rights == battle.access.READ_ACCESS:
            api.created += 1
        return handle
    monkeypatch.setattr(api, 'open_process', open_reused)
    with pytest.raises(battle.ResearchFailure, match='process_creation_time_changed'):
        battle.NativePcBattleProbe(root, api)
    assert api.reads == []
    assert api.closes == [0x1000, 0x410]


def test_verbose_recorder_presence_exposes_only_the_boolean(runtime):
    root, api, p = runtime
    recorder = api.allocate()
    api.put(p['logger'] + battle.LOGGER_VERBOSE, '<Q', recorder)
    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample()
    assert result['sample']['verbose_recorder_present'] is True
    assert str(recorder) not in repr(result)


def test_unsupported_build_closes_query_handles_before_any_module_or_memory_read(monkeypatch, tmp_path):
    monkeypatch.setattr(battle.access, 'verify_build', lambda root: ({'known_build': False}, None))
    api = FakeApi(tmp_path)
    def forbidden_modules(_handle):
        pytest.fail('unsupported build must not query modules')
    monkeypatch.setattr(api, 'modules', forbidden_modules)
    with pytest.raises(battle.ResearchFailure, match='build_fingerprint_mismatch'):
        battle.NativePcBattleProbe(tmp_path, api)
    assert api.reads == []
    assert api.closes == [0x1000, 0x410]


def test_no_matching_process_reconnect_avoids_expensive_build_hashes(monkeypatch, tmp_path):
    api = FakeApi(tmp_path)
    monkeypatch.setattr(api, 'named_processes', lambda: [])
    def forbidden_hashes(_root):
        pytest.fail('absent process must not repeatedly hash game files')
    monkeypatch.setattr(battle.access, 'verify_build', forbidden_hashes)
    with pytest.raises(battle.ResearchFailure, match='process_not_unique'):
        battle.NativePcBattleProbe(tmp_path, api)
    assert api.opens == api.reads == []


def test_denial_and_module_mismatch_do_not_read_game_memory(runtime):
    root, api, _ = runtime
    api.fail_open = True
    with pytest.raises(battle.ResearchFailure) as error:
        battle.NativePcBattleProbe(root, api)
    assert error.value.diagnostic == {'api': 'OpenProcess', 'win32_error': 5}
    assert api.reads == []
    api.fail_open = False
    api.module_path = root / 'other' / 'GameAssembly.dll'
    with pytest.raises(battle.ResearchFailure, match='module_path_mismatch'):
        battle.NativePcBattleProbe(root, api)
    assert api.reads == []
    assert api.closes[-1] == battle.access.READ_ACCESS


def test_fixed_module_bytes_are_checked_before_dynamic_roots(runtime):
    root, api, _ = runtime
    api.write(api.base + 0x1000, b'wrong')
    with pytest.raises(battle.ResearchFailure, match='module_bytes_mismatch'):
        battle.NativePcBattleProbe(root, api)
    assert api.reads == [(api.base + 0x1000, 5)]
    assert api.closes[-1] == battle.access.READ_ACCESS


@pytest.mark.parametrize('missing', ['class', 'singleton', 'native', 'logger', 'level', 'stats'])
def test_null_roots_wait_without_fabricating_aggregate_zero(runtime, missing):
    root, api, pointers = runtime
    addresses = {'class': api.base + battle.BATTLE_CLASS_SLOT_RVA,
                 'singleton': pointers['singleton'], 'native': pointers['controller'] + 0x10,
                 'logger': pointers['controller'] + battle.BATTLE_LOGGER,
                 'level': pointers['controller'] + battle.BATTLE_LEVEL,
                 'stats': pointers['logger'] + battle.LOGGER_STATS}
    api.put(addresses[missing], '<Q', 0)
    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample()
    assert result['status'] in ('waiting_game_init', 'waiting_for_battle', 'battle_not_ready')
    assert 'sample' not in result


@pytest.mark.parametrize('change', ['class', 'parent', 'singleton_storage', 'clock_storage', 'battle'])
def test_null_singleton_must_keep_the_whole_binding_stable(runtime, change):
    root, api, p = runtime
    api.put(p['singleton'], '<Q', 0)
    class_reads = 0
    def rebind(address, _size):
        nonlocal class_reads
        if address != api.base + battle.BATTLE_CLASS_SLOT_RVA:
            return
        class_reads += 1
        if class_reads == 2:
            changes = {
                'class': (api.base + battle.BATTLE_CLASS_SLOT_RVA, '<Q', 0),
                'parent': (p['klass'] + battle.CLASS_PARENT, '<Q', api.allocate()),
                'singleton_storage': (p['parent'] + battle.CLASS_STATICS, '<Q', api.allocate()),
                'clock_storage': (p['klass'] + battle.CLASS_STATICS, '<Q', api.allocate()),
                'battle': (p['singleton'], '<Q', p['controller']),
            }
            api.put(*changes[change])
    with battle.NativePcBattleProbe(root, api) as probe:
        api.on_read = rebind
        result = probe.sample()
    assert result['status'] == 'sample_retry'
    assert result['reason'] == 'frame_or_lifecycle_changed'
    assert 'sample' not in result


def test_confirmed_null_clears_only_the_in_process_battle_identity(runtime):
    root, api, p = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        assert probe.sample()['status'] == 'sample_available'
        assert probe.battle_identity is not None
        api.put(p['singleton'], '<Q', 0)
        result = probe.sample()
        assert probe.battle_identity is None
    assert result['status'] == 'waiting_for_battle'
    assert result['reason'] == 'singleton_is_null'
    assert 'battle_identity' not in result


def test_unresolved_class_usage_uses_only_the_checked_definition_table(runtime):
    root, api, pointers = runtime
    table = api.allocate(battle.BATTLE_TYPE_INDEX * 8 + 8)
    api.put(api.base + battle.BATTLE_CLASS_SLOT_RVA, '<Q', 0x20000001)
    api.put(api.base + battle.CLASS_DEFINITION_TABLE_RVA, '<Q', table)
    api.put(table + battle.BATTLE_TYPE_INDEX * 8, '<Q', pointers['klass'])
    with battle.NativePcBattleProbe(root, api) as probe:
        assert probe.sample()['status'] == 'sample_available'


@pytest.mark.parametrize('change', ['clock', 'state', 'singleton', 'owner', 'stats', 'counters',
                                  'clock_storage', 'singleton_storage'])
def test_mutation_within_acquisition_rejects_the_whole_sample(runtime, change):
    root, api, p = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        counts = {}
        def mutate(address, size):
            counts[(address, size)] = counts.get((address, size), 0) + 1
            if address == p['controller'] and counts[(address, size)] == 2:
                changes = {
                    'clock': (p['clock'] + battle.CLOCK_FRAME, '<I', 31),
                    'state': (p['controller'] + battle.BATTLE_STATE, '<i', 3),
                    'singleton': (p['singleton'], '<Q', 0),
                    'owner': (p['logger'] + battle.LOGGER_CONTROLLER, '<Q', 0),
                    'stats': (p['logger'] + battle.LOGGER_STATS, '<Q', 0),
                    'counters': (p['stats'] + battle.STATS_TOTAL_DAMAGE, '<f', -999),
                    'clock_storage': (p['klass'] + battle.CLASS_STATICS, '<Q', api.allocate()),
                    'singleton_storage': (p['parent'] + battle.CLASS_STATICS, '<Q', api.allocate()),
                }
                api.put(*changes[change])
        api.on_read = mutate
        assert probe.sample()['status'] == 'sample_retry'


@pytest.mark.parametrize('change', ['class', 'field_offset', 'owner', 'counter'])
def test_invalid_metadata_or_aggregate_is_rejected(runtime, change):
    root, api, p = runtime
    if change == 'class':
        api.put(p['stats_class'] + 0x10, '<Q', api.text('OtherStats'))
    elif change == 'field_offset':
        table = struct.unpack('<Q', api.read(0, p['klass'] + battle.CLASS_FIELDS, 8))[0]
        api.put(table + 42 * 32 + 24, '<i', battle.BATTLE_LOGGER + 8)
    elif change == 'owner':
        api.put(p['logger'] + battle.LOGGER_CONTROLLER, '<Q', 0)
    else:
        api.put(p['stats'] + battle.STATS_TOTAL_DAMAGE, '<f', float('nan'))
    with battle.NativePcBattleProbe(root, api) as probe:
        assert probe.sample()['status'] == 'unsupported_layout'


def test_expensive_immutable_validation_is_memoized_and_reads_remain_bounded(runtime):
    root, api, p = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        assert probe.sample()['status'] == 'sample_available'
        first = probe.reader.calls
        api.reads.clear()
        assert probe.sample()['status'] == 'sample_available'
        assert probe.reader.calls < first
        assert not any(address == p['klass'] + battle.CLASS_FIELD_COUNT for address, _ in api.reads)


def test_battle_serial_changes_only_for_confirmed_new_identity_or_clock_reset(runtime):
    root, api, p = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        assert probe.sample()['sample']['battle_serial'] == 1
        api.put(p['clock'] + battle.CLOCK_FRAME, '<Iq', 33, int(1.1 * (1 << 32)))
        assert probe.sample()['sample']['battle_serial'] == 1
        api.put(p['clock'] + battle.CLOCK_FRAME, '<Iq', 0, 0)
        assert probe.sample()['sample']['battle_serial'] == 2
        api.put(p['singleton'], '<Q', 0)
        assert probe.sample()['status'] == 'waiting_for_battle'
        api.put(p['singleton'], '<Q', p['controller'])
        assert probe.sample()['sample']['battle_serial'] == 3


def test_extra_reader_runs_inside_the_same_guard_without_exposing_addresses(runtime):
    root, api, p = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        def extra(read_bytes, context):
            assert context['battle_stats'] == p['stats']
            assert context['level_data'] == p['level']
            assert struct.unpack('<f', read_bytes(p['stats'] + battle.STATS_TOTAL_DAMAGE, 4))[0] == -123.5
            return {'operator_rows': []}
        result = probe.sample(extra_reader=extra)
        assert result['sample']['operator_rows'] == []
        assert str(p['stats']) not in repr(result)
        def crossed(read_bytes, context):
            api.put(p['clock'] + battle.CLOCK_FRAME, '<I', 31)
            return {'operator_rows': []}
        assert probe.sample(extra_reader=crossed)['status'] == 'sample_retry'


def test_failed_reads_and_budgets_are_controlled(runtime):
    root, api, p = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        api.fail_read = True
        result = probe.sample()
        assert result['status'] == 'read_failed'
        assert result['diagnostic'] == {'api': 'ReadProcessMemory', 'win32_error': 299}
        api.fail_read = False
        reader = probe.reader
        reader.reset_budget()
        reader.calls = battle.MAX_READ_CALLS
        with pytest.raises(battle.ResearchFailure, match='read_budget_exhausted'):
            reader.read(p['stats'], 8)
        with pytest.raises(battle.ResearchFailure, match='invalid_read_range'):
            reader.read(p['stats'], battle.MAX_SINGLE_READ + 1)


@pytest.mark.parametrize('state,result,outcome,reason', [
    (2, 0, None, 'battle_not_finished'), (2, 1, None, 'battle_not_finished'),
    (3, 0, None, 'result_not_yet'), (3, 1, 'victory', None),
    (3, 2, 'defeat', None), (3, 99, None, 'unrecognized_result'),
])
def test_outcome_uses_only_the_finished_native_result(runtime, state, result, outcome, reason):
    root, api, p = runtime
    api.put(p['controller'] + battle.BATTLE_STATE, '<ii', state, result)
    with battle.NativePcBattleProbe(root, api) as probe:
        sample = probe.sample()['sample']
    assert sample['battle_state'] == state
    assert sample['battle_state_name'] == ('PLAYING' if state == 2 else 'FINISHED')
    assert sample['battle_result'] == result
    assert sample['outcome'] == outcome and sample['outcome_reason'] == reason


@pytest.mark.parametrize('stored,slow,effective,name', [
    (0, 0, 0, 'SLOW_MOTION'), (1, 0, 1, 'STANDARD'), (2, 0, 2, 'FAST'),
    (3, 0, 3, 'SUPER_FAST'), (2, 1, 0, 'SLOW_MOTION'),
])
def test_stock_speed_getter_semantics_include_the_slow_flag(runtime, stored, slow, effective, name):
    root, api, p = runtime
    api.put(p['controller'] + battle.BATTLE_SPEED, '<i', stored)
    api.put(p['controller'] + battle.BATTLE_SLOW_FLAG, '<B', slow)
    with battle.NativePcBattleProbe(root, api) as probe:
        sample = probe.sample()['sample']
    assert sample['speed_level_raw'] == stored
    assert sample['speed_level'] == effective and sample['speed_name'] == name
    assert sample['speed_status'] == 'available'


@pytest.mark.parametrize('count,paused', [(0, False), (1, True), (3, True)])
def test_pause_comes_from_verified_key_count_including_stagnant_clock(runtime, count, paused):
    root, api, p = runtime
    api.put(p['key_list'] + 0x18, '<ii', count, 4)
    with battle.NativePcBattleProbe(root, api) as probe:
        first = probe.sample()['sample']
        second = probe.sample()['sample']
    assert first['frame'] == second['frame']
    assert first['paused'] is paused and second['paused'] is paused
    assert first['pause_key_count'] == count
    assert first['pause_reason'] is None


@pytest.mark.parametrize('field,reason', [
    ('wrapper', 'pause_state_missing'), ('key_set', 'pause_key_set_missing'),
    ('key_list', 'pause_key_list_missing'), ('count', 'invalid_pause_key_count'),
])
def test_missing_pause_evidence_is_explicit_without_discarding_totals(runtime, field, reason):
    root, api, p = runtime
    address = {'wrapper': p['controller'] + battle.BATTLE_PAUSE,
               'key_set': p['pause'] + 0x10, 'key_list': p['key_set'] + 0x10,
               'count': p['key_list'] + 0x18}[field]
    api.put(address, '<i' if field == 'count' else '<Q', -1 if field == 'count' else 0)
    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample()
    assert result['status'] == 'sample_available'
    assert result['sample']['total_damage'] == -123.5
    assert result['sample']['paused'] is None
    assert result['sample']['pause_status'] == 'unavailable'
    assert result['sample']['pause_reason'] == reason


def test_getter_overrides_disable_inferred_speed_and_pause(runtime):
    root, api, p = runtime
    api.put(p['clock'] + battle.PAUSE_GETTER_OVERRIDE, '<Q', api.allocate())
    api.put(p['clock'] + battle.SPEED_GETTER_OVERRIDE, '<Q', api.allocate())
    with battle.NativePcBattleProbe(root, api) as probe:
        sample = probe.sample()['sample']
    assert sample['paused'] is None and sample['speed_level'] is None
    assert sample['pause_reason'] == 'pause_getter_override_present'
    assert sample['speed_reason'] == 'speed_getter_override_present'


@pytest.mark.parametrize('case,reason', [
    ('missing', 'level_id_missing'), ('length', 'invalid_level_id_length'),
    ('utf16', 'invalid_level_id_utf16'), ('class', 'class_identity_mismatch'),
])
def test_optional_stage_failures_do_not_turn_into_a_guessed_stage(runtime, case, reason):
    root, api, p = runtime
    if case == 'missing':
        api.put(p['level'] + battle.LEVEL_ID, '<Q', 0)
    elif case == 'length':
        api.put(p['stage'] + 0x10, '<i', 513)
    elif case == 'utf16':
        api.put(p['stage'] + 0x10, '<i', 1)
        api.write(p['stage'] + 0x14, b'\x00\xd8')
    else:
        api.put(p['stage'], '<Q', p['stats_class'])
    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample()
    assert result['status'] == 'sample_available'
    assert result['sample']['stage_id'] is None
    assert result['sample']['stage_status'] == 'unavailable'
    assert result['sample']['stage_reason'] == reason


@pytest.mark.parametrize('change', ['result', 'speed', 'pause', 'stage', 'getter_override'])
def test_metadata_changes_inside_the_frame_bracket_are_retried(runtime, change):
    root, api, p = runtime
    counts = {}
    def mutate(address, size):
        counts[(address, size)] = counts.get((address, size), 0) + 1
        if address == p['controller'] and counts[(address, size)] == 2:
            changes = {
                'result': (p['controller'] + battle.BATTLE_RESULT, '<i', 1),
                'speed': (p['controller'] + battle.BATTLE_SPEED, '<i', 2),
                'pause': (p['key_list'] + 0x18, '<i', 1),
                'stage': (p['level'] + battle.LEVEL_ID, '<Q', 0),
                'getter_override': (p['clock'] + battle.PAUSE_GETTER_OVERRIDE, '<Q', api.allocate()),
            }
            api.put(*changes[change])
    with battle.NativePcBattleProbe(root, api) as probe:
        api.on_read = mutate
        assert probe.sample()['status'] == 'sample_retry'


def test_origin_scale_is_explicit_and_nonfinite_values_stay_unavailable(runtime):
    root, api, p = runtime
    api.put(p['controller'] + battle.BATTLE_ORIGIN_TIME_SCALE, '<f', float('nan'))
    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample()
    assert result['status'] == 'sample_available'
    assert result['sample']['origin_time_scale'] is None
    assert result['sample']['origin_time_scale_reason'] == 'invalid_origin_time_scale'
    assert 'current_time_scale' not in result['sample']


def test_acquisition_timing_uses_monotonic_wall_time_outside_sample(runtime, monkeypatch):
    root, api, _ = runtime
    times = iter([20.0, 20.125])
    monkeypatch.setattr(battle.time, 'monotonic', lambda: next(times))
    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample()
    assert result['acquisition'] == {'started_monotonic': 20.0, 'finished_monotonic': 20.125,
                                     'duration_ms': 125.0}
    assert 'acquisition' not in result['sample']


def test_optional_enemy_budget_leaves_the_closing_frame_guard_intact(runtime):
    root, api, p = runtime

    def exhausted(read, _context):
        with pytest.raises(battle.ResearchFailure, match='optional_read_budget_exhausted'):
            for _ in range(battle.MAX_READ_CALLS):
                read(p['controller'], 8)
        return {'enemies_status': 'unavailable', 'enemies': None}

    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample(extra_reader=exhausted)

    assert result['status'] == 'sample_available'
    assert result['sample']['enemies_status'] == 'unavailable'


def test_confirmed_stage_change_increments_the_opaque_battle_serial(runtime):
    root, api, p = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        assert probe.sample()['sample']['battle_serial'] == 1
        stage = api.ustring('Obt/Resource/level_tough_ce_05', p['string_class'])
        api.put(p['level'] + battle.LEVEL_ID, '<Q', stage)
        sample = probe.sample()['sample']
    assert sample['battle_serial'] == 2
    assert sample['stage_id'] == 'Obt/Resource/level_tough_ce_05'
