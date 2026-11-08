import struct
from types import SimpleNamespace

import pytest

import collector  # Adds the pinned vendor namespace without connecting a device.
from tools.enemy_health import game_structs as gs
from tools.enemy_health.battle_lifecycle import LifecycleReader
from tools.stage_global_state.memory import MemoryAccess, ReadFailure


class MemoryFixture:
    def __init__(self, state=2, battle=True):
        self.mc = SimpleNamespace(is_ptr=lambda address: address >= 1000)
        self.blocks = {}
        self.pointers = {1000 + 0x58: 1100, 1100 + 0xB8: 1200,
                         2000 + 0x58: 2100, 2100 + 0xB8: 2200,
                         8000 + gs.LevelDataFields.LEVEL_ID: 0}
        self.names = {1000: ('BattleController', 'Torappu.Battle'),
                      1100: ('SingletonMonoBehaviour`1', 'Torappu'),
                      2000: ('GameFlowController', 'Torappu'),
                      2100: ('SingletonMonoBehaviour`1', 'Torappu')}
        self.blocks[1200] = struct.pack('<Q', 3000 if battle else 0)
        self.blocks[2200] = struct.pack('<Q', 4000)
        flow = bytearray(0x38)
        struct.pack_into('<Q', flow, 0, 2000)
        struct.pack_into('<Q', flow, 0x10, 5000)
        self.blocks[4000] = bytes(flow)
        fields = gs.BattleControllerFields
        bc = bytearray(max(fields.M_STATE + 4, fields.SCHEDULER + 8,
                           fields.LEVEL_DATA + 8, fields.UNIT_MANAGER + 8, 0x18))
        struct.pack_into('<Q', bc, 0, 1000)
        struct.pack_into('<Q', bc, 0x10, 6000)
        struct.pack_into('<i', bc, fields.M_STATE, state)
        for offset, address in ((fields.SCHEDULER, 7000),
                                (fields.LEVEL_DATA, 8000),
                                (fields.UNIT_MANAGER, 9000)):
            struct.pack_into('<Q', bc, offset, address)
        self.blocks[3000] = bytes(bc)
        self.identities = {7000: 'Scheduler', 8000: 'LevelData', 9000: 'UnitManager'}
        self.final_slot_override = None
        self.slot_reads = 0

    def read(self, address, size):
        for start, raw in self.blocks.items():
            if start <= address and address + size <= start + len(raw):
                return raw[address - start:address - start + size]
        raise ReadFailure('read_failed')

    def read_many(self, requests, structural_check=False):
        self.slot_reads += 1
        values = [self.read(address, size) for address, size in requests]
        if self.slot_reads == 2 and self.final_slot_override is not None:
            values[0] = struct.pack('<Q', self.final_slot_override)
        return values

    def pointer(self, address):
        return self.pointers[address]

    def integer(self, address):
        return struct.unpack('<i', self.read(address, 4))[0]

    def class_identity(self, address):
        return self.names[address]

    def identity(self, address, expected):
        if self.identities.get(address) != expected:
            raise ReadFailure('unsupported_layout')


@pytest.mark.parametrize('state', [0, 1, 2, 3])
def test_native_state_is_preserved_separately_from_readiness(state):
    memory = MemoryFixture(state)
    value = LifecycleReader(memory, 1000, 2000).sample()
    assert value['battle'] == (3000, 6000)
    assert value['state'] == state
    assert value['ready'] == (state == 2)


def test_observed_null_is_distinct_from_failed_read():
    memory = MemoryFixture(battle=False)
    reader = LifecycleReader(memory, 1000, 2000)
    assert reader.sample()['battle'] is None
    memory.blocks.pop(1200)
    with pytest.raises(ReadFailure):
        reader.sample()


def test_singleton_rebinding_during_read_is_rejected():
    memory = MemoryFixture()
    memory.final_slot_override = 10000
    with pytest.raises(ReadFailure, match='binding_changed'):
        LifecycleReader(memory, 1000, 2000).sample()


def test_invalid_state_never_becomes_a_finished_or_empty_battle():
    with pytest.raises(ReadFailure, match='unsupported_layout'):
        LifecycleReader(MemoryFixture(99), 1000, 2000).sample()


@pytest.mark.parametrize(('state', 'result', 'expected'), [
    (3, 1, 'victory'), (3, 2, 'defeat'), (3, 0, None), (3, 3, None), (2, 2, None),
])
def test_native_result_requires_known_value_in_finished_state(state, result, expected):
    memory = MemoryFixture(state)
    block = bytearray(memory.blocks[3000])
    struct.pack_into('<i', block, 0x22C, result)
    memory.blocks[3000] = bytes(block)
    metadata = LifecycleReader(memory, 1000, 2000).sample()['metadata'] or {}
    assert metadata.get('outcome') == expected


def test_abandon_requires_active_battle_and_its_finish_transition():
    memory = MemoryFixture()
    flow = bytearray(memory.blocks[4000])
    flow[0x20] = 1
    for offset, address, name in ((0x28, 10000, 'level_act3d0_01'),
                                  (0x30, 11000, 'battle_finish')):
        struct.pack_into('<Q', flow, offset, address)
        memory.blocks[address] = bytes(0x10) + struct.pack('<i', len(name)) + name.encode('utf-16-le')
    memory.blocks[4000] = bytes(flow)
    assert LifecycleReader(memory, 1000, 2000).sample()['metadata']['outcome'] == 'abandoned'


def test_practice_requires_observed_launch_and_matching_fresh_squad_args(monkeypatch):
    memory = MemoryFixture()
    reader = LifecycleReader(memory, 1000, 2000, lambda: [12000])
    full_stage = 'Obt/Main/level_main_01-07'
    short_stage = 'main_01-07'
    for address, value in ((10000, full_stage), (16000, short_stage)):
        memory.blocks[address] = bytes(0x10) + struct.pack('<i', len(value)) + value.encode('utf-16-le')
    memory.pointers.update({8000 + gs.LevelDataFields.LEVEL_ID: 10000,
                            12000 + 0x30: 13000, 14000 + 0x50: 15000})
    memory.names[15000] = ('SquadPage', 'Torappu.UI')
    args = bytearray(0x50)
    args[0x28] = 1
    struct.pack_into('<Q', args, 0x48, 16000)
    memory.blocks[13000] = bytes(args)
    original_identity = memory.identity

    def identity(address, expected=None):
        if address == 12000:
            return 15000, 'SquadPage', 'Torappu.UI'
        if address == 13000:
            return 14000, 'Params', ''
        if address in (10000, 16000):
            assert expected == 'String'
            return 17000, 'String', 'System'
        return original_identity(address, expected)

    monkeypatch.setattr(memory, 'identity', identity)
    assert 'practice' not in reader.sample()['metadata']
    reader.launch_pending = True
    assert reader.sample()['metadata']['practice'] is True
    assert reader.sample()['metadata']['practice'] is True
    # A later launch must provide a new launch argument; a retained UI page
    # cannot reclassify a different generation, even for the same stage.
    reader.practice = None
    reader.launch_pending = True
    assert 'practice' not in reader.sample()['metadata']


def test_memory_access_rejects_short_reads_instead_of_decoding_zero():
    channel = SimpleNamespace(batch_read=lambda requests, **kwargs: [b'\0' * 7])
    memory = MemoryAccess(SimpleNamespace(is_ptr=lambda address: True), channel)
    with pytest.raises(ReadFailure):
        memory.pointer(1000)


def test_optional_native_stage_requires_readable_stable_string_but_no_result_guess():
    memory = MemoryFixture()
    stage = 'Activities/ACT3D0/level_act3d0_02'
    memory.pointers[8000 + gs.LevelDataFields.LEVEL_ID] = 10000
    memory.identities[10000] = 'String'
    raw = bytearray(0x14)
    struct.pack_into('<i', raw, 0x10, len(stage))
    memory.blocks[10000] = bytes(raw) + stage.encode('utf-16-le')
    result = LifecycleReader(memory, 1000, 2000).sample()
    assert result['metadata'] == dict(stage_id=stage,
                                       evidence='native.LevelData.levelId:Android2.7.91')
    assert 'outcome' not in result['metadata'] and 'practice' not in result['metadata']
    memory.blocks.pop(10000)
    result = LifecycleReader(memory, 1000, 2000).sample()
    assert result['metadata'] is None and result['ready']


@pytest.mark.parametrize('birth', [10, 11, 'invalid'])
def test_collector_checks_process_birth_before_using_cached_metadata(birth):
    value = collector.Collector.__new__(collector.Collector)
    reads = []
    fields = ['0'] * 20
    fields[19] = str(birth)
    value.core = SimpleNamespace(mc=SimpleNamespace(
        pid=123, process_instance=('boot', 10),
        _pid_for_known_package=lambda: ('package', 123),
        shell=lambda *args, **kwargs: 'boot\n123 (game) ' + ' '.join(fields)))
    value._lifecycle_reader = SimpleNamespace(sample=lambda: reads.append(True))
    if birth == 10:
        value.read_lifecycle()
        assert reads == [True]
    else:
        error = collector.SampleUnavailable if birth == 'invalid' else collector.BattleChanged
        with pytest.raises(error):
            value.read_lifecycle()
        assert not reads


def test_collector_refreshes_readable_ranges_when_scene_moves_objects():
    value = collector.Collector.__new__(collector.Collector)
    calls = []
    fields = ['0'] * 19 + ['10']
    value._lifecycle_maps_refresh_at = float('-inf')
    value.core = SimpleNamespace(mc=SimpleNamespace(
        pid=123, process_instance=('boot', 10),
        _pid_for_known_package=lambda: ('package', 123),
        shell=lambda *args, **kwargs: 'boot\n123 (game) ' + ' '.join(fields),
        reload_maps=lambda: calls.append('refresh')))

    def sample():
        calls.append('sample')
        if 'refresh' not in calls:
            raise ReadFailure('binding_invalid')
        return {'battle': None}

    value._lifecycle_reader = SimpleNamespace(sample=sample)
    assert value.read_lifecycle() == {'battle': None}
    assert calls == ['sample', 'refresh', 'sample']
