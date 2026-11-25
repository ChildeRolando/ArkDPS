"""PC's real session hash includes the serial advanced by a clock reset."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import struct
from types import SimpleNamespace

import pytest

from battle_lifecycle import BattleSessions, _native_pc_guarded_clock_reset
from capture_battles import observation_from_json
from test_autochess_scope_end import OwnedScopeEnd, old_local
from test_native_pc_autochess_acquisition import ROOTS
from native_pc.probe_native_pc_battle import NativePcBattleProbe
from native_pc_collector import NativePcCollector
from test_pc_autochess_cross_frame_admission import CrossingPcBattle


def reset_native_clock(case, *, serial_step=1, generation_step=1):
    source = case.fixture
    # Exercise the production sampler's transition rule without a process read.
    roots = tuple(int(value, 16) for value in ROOTS.values())
    probe = SimpleNamespace(_identity=roots, _stage_id=source.stage, _serial=source.serial,
                            _last_clock=(source.frame, int(source.when * (1 << 32))))
    predicted = NativePcBattleProbe.battle_serial_for(probe, roots, 0, 0, source.stage)
    assert predicted == source.serial + 1
    source.serial = predicted + serial_step - 1
    source.native_generation += generation_step
    source.autochess_epoch += 1
    source.digest = hashlib.sha256(struct.pack('<6Q',
        *[int(value, 16) for value in ROOTS.values()], source.serial)).hexdigest()
    source.session = f'{source.birth[0]}:{source.birth[1]}:{source.digest}'
    case.advance(0)


class CollectorClockBattle(CrossingPcBattle):
    """Real collector methods over owned reports, without a process handle."""
    def __init__(self):
        super().__init__()
        self.identity_roots = deepcopy(ROOTS)
        self.changed_account = False
        collector = NativePcCollector(None, collect_actions=False)
        process = object.__new__(NativePcBattleProbe)
        process.pid, process.process_instance = self.birth[0], self.birth
        process._identity = process._last_clock = process._stage_id = None
        process._serial = 0
        process.process_alive = lambda: True
        process.build = dict(known_build=True, source_build=self.build)
        collector.process = process
        collector.diagnostics.version_actual = self.build
        collector.event_capability = dict(status='ready', error=None)
        self.collector = collector

        def sample(*, extra_reader):
            roots = tuple(int(value, 16) for value in self.identity_roots.values())
            fixed = int(self.when * (1 << 32))
            process._serial = process.battle_serial_for(roots, self.frame, fixed, self.stage)
            process._identity, process._last_clock, process._stage_id = roots, (self.frame, fixed), self.stage
            point = dict(native_identity=deepcopy(self.identity_roots), frame=self.frame,
                         native_fixed_time=str(fixed), stage_id=self.stage)
            return dict(status='sample_available', sample=dict(
                battle_serial=process._serial, frame=self.frame, game_time=self.when,
                battle_state=2, stage_status='available', stage_id=self.stage,
                actor_rows_status='unavailable', actor_rows=None,
                roster_status='unavailable', roster=None, total_damage=0, total_heal=0,
                native_window_guard=dict(version=1, source='native_pc_guarded_read',
                    process_instance_before=list(self.birth), process_instance_after=list(self.birth),
                    before=deepcopy(point), after=deepcopy(point), source_build=self.build,
                    battle_serial=process._serial)))

        process.sample = sample

    @property
    def native_scope(self):
        return ':'.join(self.identity_roots.values()) + f':autochess:{self.autochess_epoch}'

    def binding(self):
        value = super().binding()
        value['native_identity'] = deepcopy(self.identity_roots)
        if self.changed_account:
            value['native_autochess_context']['players'][0]['uid'] += '-other'
        value.update(self.collector.observer_binding(frame=self.frame, game_time=self.when,
            native_identity=value['native_identity'], native_stage_id=self.stage,
            native_process_pid=self.birth[0], native_fixed_time=value['native_fixed_time']))
        return value


@pytest.mark.parametrize('fault', [None, 'unread_tail', 'roots', 'account', 'serial_skip'])
def test_actual_native_pc_collector_binding_pair_and_serial_reset_reach_lifecycle(monkeypatch, fault):
    import test_autochess_scope_end
    monkeypatch.setattr(test_autochess_scope_end, 'CrossingPcBattle', CollectorClockBattle)
    case = OwnedScopeEnd('native_pc', phase=4, mode='all')
    source, collector = case.fixture, case.fixture.collector
    old = deepcopy(source.runtime.sessions.latest_sample)
    if fault == 'unread_tail':
        case.advance(1.25, amount=75)
    elif fault == 'roots':
        source.identity_roots['stats'] = '0x60000'
    elif fault == 'account':
        source.changed_account = True
    elif fault == 'serial_skip':
        collector.process._serial += 1
    # The actual collector predicts the new serial in observer_binding, then
    # commits it in read_lifecycle before returning its guarded pair.
    source.native_generation += 1
    source.autochess_epoch += 1
    case.advance(0)
    successor = source.poll()
    assert collector.process._serial == (3 if fault == 'serial_skip' else 2)
    assert successor.sample['native_session'] != old['native_session']
    assert (successor.round_reset is not None) is (fault in (None, 'unread_tail'))
    archive, = source.runtime.sessions.pop_archives()
    assert archive['event_status'] == ('finished' if fault is None else 'gap')
    assert archive['last_sample']['native_window_guard'] == old['native_window_guard']
    assert archive['statistics']['game_time'] == archive['last_sample']['game_time'] == 1
    assert old_local(archive)['observed_damage'] == 300


@pytest.mark.parametrize('phase', [4, 5, 6])
def test_guarded_pc_serial_clock_reset_finishes_without_forging_the_old_scope_clock(phase):
    case = OwnedScopeEnd('native_pc', phase=phase, mode='all')
    old = deepcopy(case.fixture.runtime.sessions.latest_sample)
    reset_native_clock(case)
    successor = case.fixture.poll()
    assert successor.round_reset is not None
    assert successor.sample['native_session'] != old['native_session']
    assert successor.sample['battle_identity'] != old['battle_identity']
    assert _native_pc_guarded_clock_reset(old, successor.sample)
    archive, = case.fixture.runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished' and archive['event_error'] is None
    assert archive['statistics']['game_time'] == archive['last_sample']['game_time'] == 1
    assert successor.sample['game_time'] == 0
    assert old_local(archive)['observed_damage'] == 300
    assert all(row['lifecycle_terminal_complete'] is False
               for row in old_local(archive)['statistics']['rows'].values())
    replay = BattleSessions(successor.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    recreated, = replay.pop_archives()
    assert recreated == archive


@pytest.mark.parametrize('fault', ['unread_tail', 'callback_without_output', 'observer',
                                  'serial_skip', 'source_generation_skip'])
def test_new_serial_does_not_authorize_a_missing_prefix_or_replaced_observer(fault):
    case = OwnedScopeEnd('native_pc', phase=4, mode='all')
    if fault == 'unread_tail':
        case.advance(1.25, amount=75)
    elif fault == 'callback_without_output':
        case.extra_epoch += 1
    elif fault == 'observer':
        case.fixture.observer = SimpleNamespace(**vars(case.fixture.observer))
    reset_native_clock(case, serial_step=2 if fault == 'serial_skip' else 1,
                       generation_step=2 if fault == 'source_generation_skip' else 1)
    case.fixture.poll()
    archive, = case.fixture.runtime.sessions.pop_archives()
    assert archive['event_status'] == 'gap' and archive['event_error'] == '未取得最终事件水位'
    assert old_local(archive)['observed_damage'] == 300
    assert archive['statistics']['game_time'] == 1


@pytest.mark.parametrize('fault', ['birth', 'roots', 'digest', 'serial_bool', 'guard_clock',
                                  'missing_guard', 'account', 'build', 'epoch', 'quiet_deferred',
                                  'fence_account', 'fence_raw_clock', 'fence_scope_clock', 'fence_scope'])
def test_pc_serial_reset_replay_rechecks_independent_guard_and_source_fences(fault):
    case = OwnedScopeEnd('native_pc', phase=4, mode='all')
    reset_native_clock(case)
    successor = case.fixture.poll()
    archive, = case.fixture.runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished'
    sample, reset = deepcopy(successor.sample), deepcopy(successor.round_reset)
    guard = sample['native_window_guard']
    if fault == 'birth':
        guard['process_instance_before'][1] += 1
        guard['process_instance_after'][1] += 1
    elif fault == 'roots':
        for side in ('before', 'after'):
            guard[side]['native_identity']['controller'] = '0x60000'
    elif fault == 'digest':
        sample['native_session'] += '-invalid'
    elif fault == 'serial_bool':
        guard['battle_serial'] = True
    elif fault == 'guard_clock':
        for side in ('before', 'after'):
            guard[side]['frame'] = 40
    elif fault == 'missing_guard':
        sample.pop('native_window_guard')
    elif fault == 'account':
        sample['autochess_context']['players'][0]['uid'] += '-other'
    elif fault == 'build':
        guard['source_build'] += '-other'
    elif fault == 'epoch':
        reset['after']['epoch'] += 1
    elif fault == 'fence_account':
        for side in ('before', 'after'):
            reset[side]['native_autochess_context']['players'][0]['uid'] += '-forged'
    elif fault == 'fence_raw_clock':
        for side in ('before', 'after'):
            reset[side].update(native_frame=999, native_fixed_time='not-q32')
    elif fault == 'fence_scope_clock':
        for side in ('before', 'after'):
            reset[side]['native_autochess_context']['frame'] += 1
    elif fault == 'fence_scope':
        for side in ('before', 'after'):
            reset[side]['native_autochess_context']['scope_key'] += '-forged'
    else:
        reset['after'].update(native_quiet_boundary_version=1, native_deferred_callbacks=1)
    replay = BattleSessions(successor.feed)
    for record in case.records[:-1]:
        assert replay.consume(record)
    assert replay.consume(replace(successor, sample=sample, round_reset=reset))
    rejected, = replay.pop_archives()
    assert rejected['event_status'] == 'gap'
