"""Owned guarded PC inputs exercise the shared MuMu event-window rule."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import struct

import pytest

from event_capture import CaptureRetry
from event_probe import BarrierBusy, StreamGap
from snapshot_adapter import FrameMismatch
from test_native_pc_autochess_acquisition import ScopedNativeBattle, ROOTS
from test_native_pc_battle_probe import runtime
from test_android_autochess_cross_frame_admission import _closing_fault
from native_pc import probe_native_pc_battle as native_probe
from native_pc_collector import NativePcCollector


class CrossingPcBattle(ScopedNativeBattle):
    def __init__(self):
        super().__init__()
        self.birth = (10, 1234)
        self.serial, self.native_generation = 1, 1
        self.digest = hashlib.sha256(struct.pack('<6Q', *[int(v, 16) for v in ROOTS.values()], self.serial)).hexdigest()
        self.session = f'{self.birth[0]}:{self.birth[1]}:{self.digest}'
        self.window = None
        self.snapshot_clock = self.closing_clock = None
        self.snapshot_guard = None
        self.observer.mark = self.window_mark
        self.observer.read_baseline = lambda sequence: dict(self.batch(sequence), events=[])

    def binding(self):
        return dict(super().binding(), native_process_pid=self.birth[0], native_generation=self.native_generation,
                    native_frame=self.frame, native_fixed_time=str(int(self.when * (1 << 32))),
                    native_stage_id=self.stage, active_hooks=[])

    def lifecycle(self):
        result = super().lifecycle()
        life, sample, roster = self.captured
        life['battle'] = (self.digest,)
        sample['battle_identity'] = (self.digest,)
        stamp = dict(native_identity=deepcopy(ROOTS), frame=self.frame,
                     native_fixed_time=str(int(self.when * (1 << 32))), stage_id=self.stage)
        sample['native_window_guard'] = dict(version=1, source='native_pc_guarded_read',
            process_instance_before=list(self.birth), process_instance_after=list(self.birth),
            before=deepcopy(stamp), after=deepcopy(stamp), source_build=self.build, battle_serial=self.serial)
        self.snapshot_clock = dict(frame=self.frame, game_time=self.when)
        self.snapshot_guard = deepcopy(sample['native_window_guard'])
        self.captured = life, sample, roster
        return result

    def cross(self, *, amount=10):
        self.window = dict(amount=amount, emitted=False)

    def window_mark(self):
        if self.window is not None and self.reads and self.reads[-1] == 'pair' and not self.window['emitted']:
            self.advance(self.when + .5)
            record = self.scoped_hit(self.sequence + 1, 3, self.window['amount'])
            self.advance(self.when, [record])
            self.window['emitted'] = True
            self.closing_clock = dict(frame=self.frame, game_time=self.when)
        return self.mark()


def established():
    battle = CrossingPcBattle()
    assert battle.poll().kind == 'sample'
    battle.advance(.5)
    battle.advance(.5, [battle.scoped_hit(1, 3, 30)])
    assert battle.poll().kind == 'sample'
    return battle


def own_amount(battle):
    return battle.runtime.display_sample()['multiplayer']['players'][2]['observed_damage']


def test_pc_uses_shared_sustained_cross_frame_window_without_borrowing_cumulative_rows():
    battle = established()
    for index in range(8):
        battle.advance(index + 1, damage=90000)
        battle.cross(amount=10)
        observation = battle.poll()
        assert observation.kind == 'sample'
        assert observation.sample['autochess_snapshot_anchor'] == battle.snapshot_clock
        assert observation.sample['native_window_guard'] == battle.snapshot_guard
        assert {key: observation.sample[key] for key in ('frame', 'game_time')} == battle.closing_clock
        assert observation.sample['rows'] == []
        assert observation.sample['global_total'] is None
        assert observation.sample['global_heal'] is None
        assert observation.sample['sum_damage'] is None
        assert observation.roster.frame == observation.sample['frame'] and not observation.roster.entries
    assert own_amount(battle) == 110
    assert battle.commits == list(range(10))
    assert battle.runtime.sessions.adapter.event_stream.sequence == 9


def test_native_guard_retains_actual_pair_roots_raw_clock_and_two_verified_births(runtime):
    root, api, pointers = runtime
    with native_probe.NativePcBattleProbe(root, api) as probe:
        report = probe.sample()
        assert report['status'] == 'sample_available'
        guard = report['sample']['native_window_guard']
        assert guard['source'] == 'native_pc_guarded_read' and guard['version'] == 1
        assert guard['process_instance_before'] == guard['process_instance_after'] == list(probe.process_instance)
        assert guard['before'] == guard['after']
        assert guard['before']['native_identity'] == {
            key: hex(value) for key, value in zip(ROOTS, probe.battle_identity)}
        assert int(guard['before']['native_fixed_time']) / (1 << 32) == report['sample']['game_time']
        assert guard['before']['frame'] == report['sample']['frame']
        assert guard['source_build'] == probe.build['source_build']


def test_native_closing_birth_failure_cannot_leak_a_successful_window_guard(runtime):
    root, api, pointers = runtime
    with native_probe.NativePcBattleProbe(root, api) as probe:
        def replaced(_read, _context):
            api.created += 1
            return {}
        report = probe.sample(extra_reader=replaced)
        assert report['status'] == 'process_changed'
        assert 'sample' not in report
        assert 'native_window_guard' not in report


@pytest.mark.parametrize('fault', ['active', 'scope', 'match', 'phase', 'round', 'account',
    'membership', 'mode', 'root', 'process', 'stage', 'unverified', 'missing_context',
    'sequence_rollback', 'epoch_rollback', 'native_clock', 'native_frame', 'generation', 'birth', 'build'])
def test_pc_cross_frame_window_rejects_changed_source_proof_without_ack(fault):
    battle = established()
    battle.advance(1)
    battle.cross(amount=60)
    original, calls = battle.observer.mark, []

    def changed():
        value = original()
        calls.append(None)
        if len(calls) != 2:
            return value
        if fault == 'generation':
            value['native_generation'] += 1
        elif fault == 'birth':
            value['native_process_created_filetime'] = battle.birth[1] + 1
        elif fault == 'build':
            value['source_build'] = 'another-build'
        else:
            value = _closing_fault(value, fault)
        return value

    battle.observer.mark = changed
    observation = None
    try:
        observation = battle.poll()
    except (CaptureRetry, StreamGap, FrameMismatch):
        pass
    assert observation is None or observation.kind != 'sample'
    assert battle.commits == [0, 1] and own_amount(battle) == 30


@pytest.mark.parametrize('fault', ['birth_before', 'birth_after', 'birth_bool', 'roots', 'serial',
    'raw_time', 'raw_frame', 'raw_after_type', 'raw_stage', 'raw_build', 'source', 'missing'])
def test_pc_window_cannot_use_a_forged_or_incomplete_independent_snapshot_guard(fault):
    battle = established()
    battle.advance(1)
    battle.cross(amount=60)
    original = battle.collector.read_guarded_pair

    def pair(lifecycle):
        sample, roster = original(lifecycle)
        guard = sample['native_window_guard']
        if fault == 'birth_before':
            guard['process_instance_before'][1] += 1
        elif fault == 'birth_after':
            guard['process_instance_after'][1] += 1
        elif fault == 'birth_bool':
            guard['process_instance_after'][0] = True
        elif fault == 'roots':
            for side in ('before', 'after'):
                guard[side]['native_identity']['logger'] = '0x60000'
        elif fault == 'serial':
            guard['battle_serial'] += 1
        elif fault == 'raw_time':
            for side in ('before', 'after'):
                guard[side]['native_fixed_time'] = '0'
        elif fault == 'raw_frame':
            for side in ('before', 'after'):
                guard[side]['frame'] += 1
        elif fault == 'raw_after_type':
            guard['after']['frame'] = float(guard['before']['frame'])
        elif fault == 'raw_stage':
            for side in ('before', 'after'):
                guard[side]['stage_id'] += '-wrong'
        elif fault == 'raw_build':
            guard['source_build'] += '-wrong'
        elif fault == 'source':
            guard['source'] = 'observer-mark'
        else:
            sample.pop('native_window_guard')
        return sample, roster

    battle.collector.read_guarded_pair = pair
    observation = None
    try:
        observation = battle.poll()
    except (CaptureRetry, StreamGap, FrameMismatch):
        pass
    assert observation is None or observation.kind != 'sample'
    assert battle.commits == [0, 1] and own_amount(battle) == 30


@pytest.mark.parametrize('gate', ['ordinary', 'terminal'])
def test_pc_ordinary_cumulative_and_terminal_do_not_take_the_event_only_window(gate):
    battle = established()
    if gate == 'ordinary':
        battle.stage = 'Obt/Main/level_main_01-01'
    else:
        battle.state = 3
    battle.advance(1, damage=90000)
    battle.cross(amount=60)
    observation = None
    try:
        observation = battle.poll()
    except (CaptureRetry, StreamGap, FrameMismatch):
        pass
    assert observation is None or observation.sample is None
    assert battle.commits == [0, 1] and own_amount(battle) == 30


def test_pc_first_crossing_window_is_an_empty_late_baseline_not_a_fake_zero_snapshot():
    battle = CrossingPcBattle()
    battle.cross(amount=100)
    opening = battle.poll()
    assert opening.sample['autochess_snapshot_anchor'] == dict(frame=0, game_time=0)
    assert opening.sample['frame'] == 15
    assert opening.sample['rows'] == [] and opening.sample['global_total'] is None
    assert opening.events['drain']['events'] == [] and battle.commits == [1]
    assert own_amount(battle) is None
    battle.advance(1)
    battle.cross(amount=60)
    assert battle.poll().kind == 'sample'
    assert own_amount(battle) == 60 and battle.commits == [1, 2]


@pytest.mark.parametrize('fault', ['anchor_frame', 'anchor_time', 'guard_before', 'guard_after', 'guard_birth', 'counter_scope'])
def test_pc_replayed_window_requires_the_original_independent_anchor(fault):
    battle = established()
    battle.advance(1)
    battle.cross(amount=60)
    sessions = battle.runtime.sessions
    observation = battle.runtime.feed.acquire(battle.collector, battle.observer,
        published_generation=sessions.generation, preparing_generation=sessions.preparing_generation,
        published_capture=sessions.capture)
    sample = deepcopy(observation.sample)
    if fault == 'anchor_frame':
        sample['autochess_snapshot_anchor']['frame'] -= 1
    elif fault == 'anchor_time':
        sample['autochess_snapshot_anchor']['game_time'] -= .5
    elif fault == 'guard_birth':
        sample['native_window_guard']['process_instance_after'][1] += 1
    elif fault == 'counter_scope':
        sample['native_counter_scope'] = 'native_character_counters'
    else:
        sample['native_window_guard']['before' if fault == 'guard_before' else 'after']['frame'] += 1
    try:
        accepted = battle.runtime._consume(replace(observation, sample=sample))
    except (CaptureRetry, StreamGap, FrameMismatch):
        accepted = False
    assert accepted is False or sessions.event_status == 'gap'
    assert battle.commits == [0, 1] and own_amount(battle) == 30


def test_collector_session_and_guard_use_same_read_roots_not_later_process_cache():
    from test_native_pc_collector import Probe, sample as native_sample, BUILD_ID
    report = native_sample(30, 20)
    stamp = dict(native_identity=deepcopy(ROOTS), frame=30,
                 native_fixed_time=str(1 << 32), stage_id=report['sample']['stage_id'])
    report['sample']['native_window_guard'] = dict(version=1, source='native_pc_guarded_read',
        source_build=BUILD_ID, battle_serial=1, before=deepcopy(stamp), after=deepcopy(stamp),
        process_instance_before=[456, 100], process_instance_after=[456, 100])
    process = Probe([report])
    process.battle_identity = (900, 1000, 1100, 1200, 1300)  # A deliberately later cache.
    current = NativePcCollector('game', probe_factory=lambda _: process)
    current.connect()
    try:
        lifecycle = current.read_lifecycle()
        paired, _ = current.read_guarded_pair(lifecycle)
        expected = hashlib.sha256(struct.pack('<6Q', *[int(v, 16) for v in ROOTS.values()], 1)).hexdigest()
        assert paired['session'] == f'456:100:{expected}'
        assert paired['native_window_guard'] == report['sample']['native_window_guard']
        paired['native_window_guard']['before']['frame'] = 999
        assert report['sample']['native_window_guard']['before']['frame'] == 30
    finally:
        current.close()

@pytest.mark.parametrize('fail_twice', [False, True])
def test_busy_closing_mark_retries_same_guarded_snapshot_once(fail_twice, monkeypatch):
    from event_probe import CallbackBusyMark
    from event_capture import CaptureRetry
    battle = established()
    battle.advance(1)
    battle.cross(amount=20)
    attempts = 0
    def busy_closing():
        nonlocal attempts
        if battle.reads[-1:] == ['pair']:
            attempts += 1
            if attempts == 1 or fail_twice:
                raise CallbackBusyMark('pending callback')
        return battle.window_mark()
    battle.reads.clear()
    battle.observer.mark = busy_closing
    monkeypatch.setattr('battle_lifecycle.time.sleep', lambda delay: None)
    if fail_twice:
        with pytest.raises(CaptureRetry):
            battle.poll()
        assert own_amount(battle) == 30 and battle.commits == [0, 1]
    else:
        assert battle.poll().kind == 'sample'
        assert own_amount(battle) == 50 and battle.commits == [0, 1, 2]
    assert attempts == 2 and battle.reads.count('pair') == 1
