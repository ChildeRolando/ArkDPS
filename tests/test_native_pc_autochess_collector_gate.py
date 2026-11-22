"""Real NativePcCollector admission must not require legacy AutoChess counters.

The low-level probe returns an owned, already-guarded native report. Collector,
native binding, BattleRuntime, event capture, four ledgers and ACK ownership are
production implementations; no game handle or Frida session is created.

The roster failure mirrors the rc.16 live report: sample_available with usable
actor rows/presence, but roster unavailable/counter_list_size.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from battle_lifecycle import BattleRuntime
from event_capture import CaptureRetry, LiveEventCapture
from event_probe import NATIVE_PC_CONTRACT, bind_native_autochess_pair, native_autochess_context_bound
from native_pc.probe_native_pc import BILIBILI_BUILD_ID, BUILD_ID
from native_pc.probe_native_pc_battle import NativePcBattleProbe
from native_pc_collector import NativePcCollector
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable
from roster import CharacterEntry, RosterSnapshot
from test_multiplayer_native_context import CID, QUALITY, context, hit


ROOT_NAMES = ('controller', 'native', 'logger', 'stats', 'level')
ROOT_VALUES = (0x10000, 0x20000, 0x30000, 0x40000, 0x50000)
ROOTS = dict(zip(ROOT_NAMES, map(hex, ROOT_VALUES)))
SCOPE = ':'.join(ROOTS.values()) + ':autochess:1'
AUTOCHESS = 'Activities/ACT3AUTOCHESS/level_fixture'


class GuardedReportProbe(NativePcBattleProbe):
    """Replace only memory acquisition, retaining real battle-serial semantics."""

    def __init__(self, failed_field, stage, source_build):
        self.pid, self.process_instance = 4242, (4242, 123456)
        self.build = {'known_build': True, 'source_build': source_build, 'fixture': True}
        self._identity, self._last_clock = ROOT_VALUES, (0, 0)
        self._serial, self._stage_id = 1, stage
        self.frame = self.when = 0
        self.phase, self.state = 4, 2
        self.failed_field, self.stage = failed_field, stage
        self.reads = 0

    def process_alive(self):
        return True

    def sample(self, *, extra_reader, idle_reader=None):
        assert callable(extra_reader) and idle_reader is None
        self.reads += 1
        self._last_clock = self.frame, int(self.when * (1 << 32))
        raw = dict(battle_serial=1, frame=self.frame, game_time=self.when,
            battle_state=self.state, stage_status='available', stage_id=self.stage,
            total_damage=90000., total_heal=0., outcome='victory' if self.state == 3 else None,
            actor_rows_status='available', actor_rows=[dict(id=CID, damage=90000., healing=0.,
                physical=90000., arts=0., true=0., elemental=0.)],
            roster_status='available', roster=dict(local_side=0, entries=[]),
            presence_status='available', presence=dict(local_side=0, entities=[]))
        if self.failed_field:
            raw[self.failed_field + '_status'] = 'unavailable'
            raw[self.failed_field + '_reason'] = 'counter_list_size'
            raw[self.failed_field] = None
        return dict(status='sample_available', sample=raw,
                    acquisition=dict(duration_ms=1., verified_same_frame=True))

    def close(self):
        pass


def fixture(tmp_path, *, failed_field='roster', mode='valid', stage=AUTOCHESS, source_build=BUILD_ID):
    probe = GuardedReportProbe(failed_field, stage, source_build)
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    collector.connect()
    collector.event_capability = dict(status='ready', error=None)
    probe.observer_mode = mode
    commits, records = [], []

    def envelope():
        roots = dict(ROOTS)
        if probe.observer_mode == 'foreign':
            roots['stats'] = '0x60000'
        scope = ':'.join(roots.values()) + ':autochess:1'
        ctx = context(probe.when, root=scope, phase=probe.phase)
        raw = {key: value for key, value in ctx.items() if key in (
            'protocol_version', 'status', 'verified_same_frame', 'frame', 'game_time',
            'scope_key', 'phase', 'round', 'self_player_index', 'players', 'memberships')}
        if probe.observer_mode == 'unknown':
            raw.update(status='unknown', verified_same_frame=False, reason='fixture unavailable')
        binding = collector.observer_binding(frame=probe.frame, game_time=probe.when,
            native_identity=roots, native_process_pid=probe.pid,
            native_fixed_time=str(int(probe.when * (1 << 32))), native_stage_id=stage)
        return dict(binding, native_identity=roots, native_autochess_context=raw,
                    frame=probe.frame, game_time=probe.when, sequence=len(records),
                    epoch=len(records), active=0, **QUALITY)

    def batch(through):
        return dict(envelope(), events=deepcopy([e for e in records if e['sequence'] <= through]),
                    error=None, stopped=False, probe_contract=NATIVE_PC_CONTRACT.describe())

    observer = SimpleNamespace(contract=NATIVE_PC_CONTRACT, mark=envelope,
        read_through=batch, commit_events=commits.append,
        read_terminal_mark=lambda: dict(terminal_boundary=envelope()))
    return collector, probe, observer, commits, records


@pytest.mark.parametrize('failed_field', ['roster', 'actor_rows'])
@pytest.mark.parametrize('source_build', [BUILD_ID, BILIBILI_BUILD_ID], ids=['official', 'bilibili'])
def test_guarded_native_combat_can_reach_existing_event_pipeline_without_legacy_tables(tmp_path, failed_field, source_build):
    collector, probe, observer, commits, records = fixture(tmp_path, failed_field=failed_field, source_build=source_build)
    runtime = BattleRuntime()
    observation = runtime.poll(collector, observer)
    assert observation.kind == 'sample'
    value = runtime.display_sample()
    assert value['battle_metadata']['source_build'] == source_build
    assert value['native_counter_scope'] == 'events_only'
    assert value['native_snapshot_witnesses'][failed_field] == dict(
        status='unavailable', reason='counter_list_size')
    assert value['statistics']['rows'] == {} and value['statistics']['totals_complete'] is False
    assert all(p['damage'] is None for p in value['multiplayer']['players'])
    assert commits == [0]
    probe.frame, probe.when = 30, 1
    records.append(hit(1, 3, 1, 30, event_scope=dict(protocol_version=1, status='available',
        verified_same_frame=True, scope_key=SCOPE, phase=4, round=4, frame=30, game_time=1)))
    runtime.poll(collector, observer)
    value = runtime.display_sample()
    assert value['multiplayer']['players'][2]['observed_damage'] == 30
    assert all(p['damage'] is None for p in value['multiplayer']['players'])
    assert commits == [0, 1] and probe.reads == 2


@pytest.mark.parametrize('mode', ['unknown', 'foreign', 'no_observer'])
def test_incomplete_legacy_tables_cannot_bypass_missing_or_foreign_context_proof(tmp_path, mode):
    collector, _, observer, commits, _ = fixture(tmp_path, mode=mode)
    runtime = BattleRuntime()
    with pytest.raises(CaptureRetry):
        runtime.poll(collector, None if mode == 'no_observer' else observer)
    assert commits == []
    assert runtime.display_sample() is None
    assert runtime.sessions.multiplayer is None


def test_ordinary_stage_still_rejects_missing_legacy_roster_even_with_observer(tmp_path):
    collector, _, observer, commits, _ = fixture(tmp_path, stage='Obt/Main/level_main_01-07')
    runtime = BattleRuntime()
    observations = []
    runtime.record = observations.append
    with pytest.raises(CaptureRetry):
        runtime.poll(collector, observer)
    assert commits == [] and runtime.display_sample() is None
    assert observations[-1].reason == 'read_unavailable'
    assert observations[-1].read_error['type'] == 'SnapshotGap'
    assert 'counter_list_size' in observations[-1].read_error['message']


def test_complete_legacy_probe_control_reaches_real_collector_and_runtime(tmp_path):
    collector, _, observer, commits, _ = fixture(tmp_path, failed_field=None)
    runtime = BattleRuntime()
    assert runtime.poll(collector, observer).kind == 'sample'
    assert runtime.display_sample()['multiplayer']['schema_version'] == 2
    assert runtime.sessions.adapter.last_game_time == 0
    assert commits == [0]


@pytest.mark.parametrize('mode', ['unknown', 'foreign', 'no_observer'])
def test_established_event_only_mode_does_not_admit_unbound_fallback_on_later_frame(tmp_path, mode):
    collector, probe, observer, commits, _ = fixture(tmp_path, failed_field=None)
    runtime = BattleRuntime()
    runtime.poll(collector, observer)
    assert commits == [0]
    probe.frame, probe.when, probe.failed_field, probe.observer_mode = 30, 1, 'roster', mode
    with pytest.raises(CaptureRetry):
        runtime.poll(collector, None if mode == 'no_observer' else observer)
    assert commits == [0]
    assert runtime.sessions.adapter.last_game_time == 0
    view = runtime.display_sample()
    assert view['statistics']['rows'] == {} and view['statistics']['totals_complete'] is False
    assert all(p['observed_damage'] is None for p in view['multiplayer']['players'])


def test_direct_candidate_requires_real_paired_context_before_adapter_or_capture_acceptance(tmp_path):
    collector, _, observer, commits, _ = fixture(tmp_path)
    before = observer.mark()
    lifecycle = collector.read_lifecycle()
    sample, roster = collector.read_guarded_pair(lifecycle)
    sample['battle_metadata'] = lifecycle['metadata']
    assert sample['native_counter_scope'] == 'events_only'
    assert roster.entries == () and roster.present_known is False
    adapter = BattleStatisticsAdapter()
    with pytest.raises(MappingUnavailable):
        adapter.update(sample, roster)
    after = observer.mark()
    bind_native_autochess_pair(sample, before, after)
    adapter.update(sample, roster)
    capture = LiveEventCapture(None)
    capture.adapter = adapter
    capture.ingest(sample, roster, 0, observer.read_through(0), mark=after)
    assert adapter.event_stream.sequence == 0
    assert adapter.result()['rows'] == {} and adapter.result()['totals_complete'] is False
    assert commits == []  # Only the owning runtime, not this direct consumer, ACKs.


def test_known_prepare_context_with_missing_legacy_roster_still_has_no_combat_baseline(tmp_path):
    collector, probe, observer, commits, _ = fixture(tmp_path)
    probe.phase = 3
    runtime = BattleRuntime()
    observation = runtime.poll(collector, observer)
    assert observation.kind == 'preparing'
    assert runtime.sessions.state == 'preparing'
    assert runtime.sessions.adapter is None
    assert runtime.display_sample() is None and commits == []


def test_known_end_context_still_freezes_last_combat_details_with_legacy_witness_missing(tmp_path):
    collector, probe, observer, commits, records = fixture(tmp_path)
    runtime = BattleRuntime()
    runtime.poll(collector, observer)
    probe.frame, probe.when = 30, 1
    records.append(hit(1, 3, 1, 30, event_scope=dict(protocol_version=1, status='available',
        verified_same_frame=True, scope_key=SCOPE, phase=4, round=4, frame=30, game_time=1)))
    runtime.poll(collector, observer)
    probe.frame, probe.when, probe.phase, probe.state = 60, 2, 8, 3
    records.extend([
        hit(2, 3, 2, 20, event_scope=dict(protocol_version=1, status='available',
            verified_same_frame=True, scope_key=SCOPE, phase=4, round=4, frame=60, game_time=2)),
        hit(3, 3, 2, 999, event_scope=dict(protocol_version=1, status='available',
            verified_same_frame=True, scope_key=SCOPE, phase=8, round=4, frame=60, game_time=2))])
    observation = runtime.poll(collector, observer)
    view = runtime.display_sample()
    assert observation.kind == 'terminal'
    assert view['multiplayer']['scope_kind'] == 'defense'
    assert view['multiplayer']['status'] == 'ended'
    assert view['multiplayer']['players'][2]['observed_damage'] == 50
    assert view['statistics']['rows'] == {} and view['statistics']['totals_complete'] is False
    assert commits == [0, 1, 3]


def test_same_session_legacy_core_rejects_even_bound_event_only_candidate_until_new_generation(tmp_path):
    collector, probe, observer, _, _ = fixture(tmp_path)
    probe.frame, probe.when = 30, 1
    before_mark = observer.mark()
    lifecycle = collector.read_lifecycle()
    candidate, candidate_roster = collector.read_guarded_pair(lifecycle)
    candidate['battle_metadata'] = lifecycle['metadata']
    bind_native_autochess_pair(candidate, before_mark, observer.mark())
    assert native_autochess_context_bound(candidate)
    legacy = {key: deepcopy(value) for key, value in candidate.items()
              if key not in ('native_counter_scope', 'native_snapshot_witnesses', 'autochess_context')}
    legacy.update(frame=0, game_time=0, rows=[dict(id=CID, damage=10., healing=0.)])
    roster = RosterSnapshot(0, entries=(CharacterEntry(CID, 301, False, False, None, None, 3),))
    adapter = BattleStatisticsAdapter()
    adapter.update(legacy, roster)
    retained = deepcopy(adapter.result())
    assert retained['rows'][CID]['damage'] == 10
    with pytest.raises(MappingUnavailable, match='等待新的战局代次'):
        adapter.update(candidate, candidate_roster)
    assert adapter.result() == retained
    assert adapter.last_frame == 0 and adapter.last_game_time == 0
