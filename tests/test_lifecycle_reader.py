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


def test_lifecycle_metadata_shares_two_scans_and_keeps_namespaces(monkeypatch):
    locator = collector.DeployTrackerReader(SimpleNamespace(
        regions=[(0x1000, 0x10000, 'rw-p', '')], is_ptr=lambda address: address >= 0x1000,
        adb_serial='lifecycle-scan-fixture', package='game', pid=1, process_instance=('boot', 1),
    ))
    targets = {'BattleController': 'Torappu.Battle',
               'GameFlowController': 'Torappu', 'SquadPage': 'Torappu.UI'}
    name_addresses = dict(zip(targets, (0x2000, 0x2100, 0x2200)))
    headers = {0x3010: (0x2000, 0x5000), 0x3110: (0x2100, 0x5100),
               0x3210: (0x2200, 0x5200), 0x3310: (0x2000, 0x5100)}
    hits = {name.encode('ascii') + b'\0': [address] for name, address in name_addresses.items()}
    hits.update({struct.pack('<Q', address): [ref for ref, header in headers.items() if header[0] == address]
                 for address in name_addresses.values()})
    memory = {address - 1: b'\0' for address in name_addresses.values()}
    memory.update({address: name.encode('ascii') + b'\0' for name, address in name_addresses.items()})
    memory.update({ref: struct.pack('<QQ', *header) for ref, header in headers.items()})
    memory.update({0x5000: b'Torappu.Battle\0', 0x5100: b'Torappu\0', 0x5200: b'Torappu.UI\0'})
    scans = []

    def scan(regions, needles):
        scans.append(tuple(needles))
        return {needle: hits[needle] for needle in needles}

    monkeypatch.setattr(locator, '_device_scan_regions', scan)
    monkeypatch.setattr(locator, '_scan_read_many',
                        lambda requests: [memory.get(address) for address, _ in requests])
    expected = {0x3000: 'BattleController', 0x3100: 'GameFlowController', 0x3200: 'SquadPage'}
    assert locator.scan_class_metadata(targets) == expected
    assert scans == [
        tuple(name.encode('ascii') + b'\0' for name in targets),
        tuple(struct.pack('<Q', address) for address in name_addresses.values()),
    ]

    # A different namespace cannot be substituted, even when the name matches.
    memory[0x5200] = b'Torappu\0'
    scans.clear()
    assert locator.scan_class_metadata(targets) == {0x3000: 'BattleController', 0x3100: 'GameFlowController'}
    assert scans == [(b'SquadPage\0',), (struct.pack('<Q', 0x2200),)]


def test_lifecycle_can_bind_without_optional_squad_metadata(monkeypatch):
    from tools.enemy_health import battle_lifecycle as native

    memory = MemoryFixture()
    memory.mc.channel = lambda: None
    monkeypatch.setattr(native, 'MemoryAccess', lambda *_: memory)
    scans = []

    def scan(namespaces):
        scans.append(tuple(namespaces))
        if 'SquadPage' in namespaces:
            raise OSError('scan interrupted')
        return {1000: 'BattleController', 2000: 'GameFlowController'}

    locator = SimpleNamespace(scan_class_metadata=scan)
    reader = native.locate_lifecycle(memory.mc, None, locator)
    assert reader.sample()['ready']
    assert reader.locate_squads is None
    assert scans == [
        ('GameFlowController', 'SquadPage', 'BattleController'),
        ('GameFlowController', 'BattleController'),
    ]


@pytest.mark.parametrize('failure', [None, 'build', 'slot', 'identity', 'singleton', 'ambiguous'])
def test_lifecycle_uses_build_checked_slots_or_falls_back(monkeypatch, failure):
    from tools.enemy_health import battle_lifecycle as native

    memory = MemoryFixture()
    library = '/app/lib/arm64/libil2cpp.so'
    base = 0x7000000000
    memory.mc.regions = [
        (base - 0x100000000, base - 0xF0000000, 'r--p', library),
        (base, base + 0xC515000, 'r--p', library),
        (base + 0xC515000, base + 0xD34F000, 'rw-p', library),
    ]
    if failure == 'ambiguous':
        memory.mc.regions.extend([
            (base * 2, base * 2 + 0xC515000, 'r--p', library),
            (base * 2 + 0xC515000, base * 2 + 0xD34F000, 'rw-p', library),
        ])
    memory.names[5000] = ('SquadPage', 'Torappu.UI')
    memory.names[6000] = ('BattleController', 'Torappu.Battle')
    memory.names[6100] = ('UnrelatedParent', 'Torappu')
    memory.pointers[6000 + 0x58] = 6100
    addresses = {'BattleController': 1000, 'GameFlowController': 2000, 'SquadPage': 5000}
    if failure == 'slot':
        addresses['SquadPage'] = 0x200241D7
    elif failure == 'identity':
        addresses['SquadPage'] = 2000
    elif failure == 'singleton':
        addresses['BattleController'] = 6000
    for name, rva in native._CLASS_SLOT_RVAS.items():
        memory.blocks[base + rva] = struct.pack('<Q', addresses[name])

    note_reads = []

    def read_code(address, size):
        note_reads.append((address, size))
        return bytes(size) if failure == 'build' else native._BUILD_NOTE

    memory.read_code = read_code
    monkeypatch.setattr(native, 'MemoryAccess', lambda *_: memory)
    scans = []

    def scan(namespaces):
        scans.append(namespaces)
        return {1000: 'BattleController', 2000: 'GameFlowController', 5000: 'SquadPage'}

    locator = SimpleNamespace(scan_class_metadata=scan, _status=lambda _: None)
    reader = native.locate_lifecycle(memory.mc, None, locator, channel=object())
    assert reader.sample()['ready']
    assert reader.battle_klass == 1000
    assert len(scans) == (0 if failure is None else 1)
    assert note_reads == ([] if failure == 'ambiguous' else [
        (base + native._BUILD_NOTE_RVA, len(native._BUILD_NOTE)),
    ])


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


@pytest.mark.parametrize(('output', 'expected'), [
    ('123 456\n\nARKDPS_PID_QUERY:0\n', 123),
    ('\nARKDPS_PID_QUERY:1\n', 0),
])
def test_process_lookup_requires_device_side_status(output, expected):
    from tools.enemy_health.memcore import MemCore

    channel = SimpleNamespace(package='game', shell=lambda *args, **kwargs: output)
    assert MemCore._pid_for_known_package(channel)[1] == expected


@pytest.mark.parametrize('output', ['', 'error: device offline', '123\n',
                                    '\nARKDPS_PID_QUERY:127\n',
                                    '\nARKDPS_PID_QUERY:0\n',
                                    'bad\nARKDPS_PID_QUERY:0\n',
                                    '123\nARKDPS_PID_QUERY:1\n'])
def test_failed_adb_or_pidof_output_cannot_confirm_process_exit(output):
    from tools.enemy_health.memcore import MemCore

    channel = SimpleNamespace(package='game', shell=lambda *args, **kwargs: output)
    with pytest.raises(RuntimeError):
        MemCore._pid_for_known_package(channel)
