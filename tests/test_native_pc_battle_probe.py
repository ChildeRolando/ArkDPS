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
        address, table = self.allocate(), self.allocate()
        self.put(address + 0x10, '<QQ', self.text(name), self.text(namespace))
        self.put(address + battle.CLASS_FIELDS, '<Q', table)
        self.put(address + battle.CLASS_FIELD_COUNT, '<H', count)
        for index, field, offset in fields:
            self.put(table + index * 32, '<QQQiI', self.text(field), 0, address, offset, 0)
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
    level_class = api.klass('LevelData', 'Torappu')
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
    api.put(level, '<Q', level_class)
    api.put(logger, '<Q', logger_class)
    api.put(logger + battle.LOGGER_CONTROLLER, '<Q', controller)
    api.put(logger + battle.LOGGER_STATS, '<Q', stats)
    api.put(stats, '<Q', stats_class)
    api.put(stats + battle.STATS_TOTAL_HEAL, '<ff', 50.25, -123.5)
    api.put(clock + battle.CLOCK_FRAME, '<Iq', 30, 1 << 32)
    return tmp_path, api, dict(klass=klass, parent=parent, logger_class=logger_class,
                              stats_class=stats_class, singleton=singleton, clock=clock,
                              controller=controller, native=native, logger=logger,
                              stats=stats, level=level)


def test_stable_native_sample_preserves_raw_counter_sign_and_closes_handle(runtime):
    root, api, _ = runtime
    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample()
        assert result['status'] == 'sample_available'
        assert result['sample'] == {'battle_serial': 1, 'frame': 30, 'game_time': 1,
                                    'battle_state': 2, 'total_damage': -123.5, 'total_heal': 50.25,
                                    'verbose_recorder_present': False}
        assert result['research_only'] and result['limitations']
        assert probe.reader.calls <= battle.MAX_READ_CALLS
        assert probe.reader.bytes_requested <= battle.MAX_READ_BYTES
    assert api.closes == [battle.access.PROCESS_QUERY_LIMITED_INFORMATION, battle.access.READ_ACCESS]
    assert probe.sample()['status'] == 'closed'


def test_verbose_recorder_presence_exposes_only_the_boolean(runtime):
    root, api, p = runtime
    recorder = api.allocate()
    api.put(p['logger'] + battle.LOGGER_VERBOSE, '<Q', recorder)
    with battle.NativePcBattleProbe(root, api) as probe:
        result = probe.sample()
    assert result['sample']['verbose_recorder_present'] is True
    assert str(recorder) not in repr(result)


def test_unsupported_build_never_discovers_or_opens_a_process(monkeypatch, tmp_path):
    monkeypatch.setattr(battle.access, 'verify_build', lambda root: ({'known_build': False}, None))
    with pytest.raises(battle.ResearchFailure, match='build_fingerprint_mismatch'):
        battle.NativePcBattleProbe(tmp_path, object())


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
