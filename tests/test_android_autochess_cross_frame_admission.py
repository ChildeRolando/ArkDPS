"""Offline guarded MuMu windows; simulated time is never measured game FPS."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from event_capture import CaptureRetry
from event_probe import StreamGap
from snapshot_adapter import FrameMismatch
from test_android_autochess_acquisition import AndroidAutoChessBattle


class CrossingAndroidBattle(AndroidAutoChessBattle):
    """Advance only after each independent collector snapshot/roster exists."""

    def __init__(self):
        super().__init__()
        self.window = None
        self.snapshot_clock = self.closing_clock = None
        self.snapshot_guard = None
        self.virtual_host_time = 0
        self.snapshot_since_mark = False
        self.observer.mark = self.window_mark

    def cross(self, *, snapshot_shift=0, closing_shift=.5, amount=10, transition=None):
        self.window = dict(snapshot_shift=snapshot_shift, closing_shift=closing_shift,
                           amount=amount, transition=transition, emitted=False)

    def read(self):
        if self.window and self.window['snapshot_shift']:
            self.advance(self.when + self.window['snapshot_shift'])
        result = super().read()
        self.snapshot_clock = dict(frame=result['frame'], game_time=result['game_time'])
        self.snapshot_guard = deepcopy(result['guard'])
        self.snapshot_since_mark = True
        return result

    def window_mark(self):
        if self.window is not None and self.snapshot_since_mark:
            window = self.window
            self.advance(self.when + window['closing_shift'],
                amounts=() if window['emitted'] else ((3, window['amount']),))
            if window['transition'] is not None and not window['emitted']:
                window['transition'](self)
            window['emitted'] = True
            # Failed real rc28 attempts took 157--250ms, beyond the feed's
            # 40ms retry budget. Model 188ms here, without sleeping or changing
            # the production clock/binding algorithm.
            self.virtual_host_time += .188
            self.closing_clock = dict(frame=self.frame, game_time=self.when)
        self.snapshot_since_mark = False
        return self.mark()

    def poll(self):
        import battle_lifecycle
        clock = battle_lifecycle.time
        battle_lifecycle.time = SimpleNamespace(monotonic=lambda: self.virtual_host_time)
        try:
            return super().poll()
        finally:
            battle_lifecycle.time = clock


def own_amount(battle):
    sample = battle.runtime.display_sample()
    if not sample:
        return None
    payload = sample.get('multiplayer') or {}
    rows = [p for p in payload.get('players') or [] if p.get('is_local') is True]
    return rows[0]['observed_damage'] if len(rows) == 1 else None


def established():
    battle = CrossingAndroidBattle()
    assert battle.poll().kind == 'sample'
    battle.advance(.5, amounts=((3, 30),))
    assert battle.poll().kind == 'sample'
    assert own_amount(battle) == 30
    assert battle.commits == [0, 1]
    return battle


def sustained_window():
    battle = established()
    publications = rejected = 0
    anchors = []
    cpu, wall = time.process_time(), time.perf_counter()
    for step in range(34):
        battle.virtual_host_time = step * .5
        battle.advance(step + 1)
        battle.cross()
        try:
            observation = battle.poll()
        except (CaptureRetry, StreamGap, FrameMismatch):
            rejected += 1
            continue
        publications += observation.kind == 'sample'
        anchors.append(observation.sample.get('autochess_snapshot_anchor'))
        assert {key: observation.sample[key] for key in ('frame', 'game_time')} == battle.closing_clock
        assert observation.sample['guard'] == battle.snapshot_guard
    report = dict(virtual_host_seconds=17, attempts=34, publications=publications, rejected=rejected,
        own_amount=own_amount(battle), acknowledgements=deepcopy(battle.commits),
        input_events=len(battle.events), anchors=anchors,
        process_cpu_seconds=time.process_time()-cpu, host_wall_seconds=time.perf_counter()-wall)
    return battle, report


def test_sustained_verified_scope_does_not_wait_for_a_quiet_game_frame(record_property):
    battle, report = sustained_window()
    record_property('offline_synthetic_measurement', repr(report))
    # 34 attempts represent 17 virtual host seconds. The prior path publishes
    # nothing during this interval and catches up only after crossing stops.
    assert {key: report[key] for key in ('publications', 'rejected', 'own_amount', 'input_events')} == {
        'publications': 34, 'rejected': 0, 'own_amount': 370, 'input_events': 35}
    assert report['acknowledgements'] == list(range(36))
    assert report['anchors'] == [dict(frame=(step+1)*30, game_time=step+1) for step in range(34)]
    assert battle.runtime.feed.clock == 34.5
    assert battle.runtime.sessions.adapter.event_stream.sequence == 35
    assert battle.runtime.sessions.adapter.event_stream.game_time == 34.5
    assert [event['sequence'] for event in battle.events] == list(range(1, 36))
    assert [event['game_time'] for event in battle.events] == [.5] + [step+1.5 for step in range(34)]


def test_snapshot_strictly_inside_window_is_preserved_as_anchor():
    battle = established()
    battle.advance(1)
    battle.cross(snapshot_shift=.5, closing_shift=.5, amount=60)
    observation = battle.poll()
    assert observation.kind == 'sample'
    assert observation.sample['autochess_snapshot_anchor'] == dict(frame=45, game_time=1.5)
    assert observation.sample['frame'] == 60 and observation.sample['game_time'] == 2
    assert observation.sample['guard'] == dict(start=45, end=45, complete=True)
    assert observation.roster.frame == 60 and not observation.roster.entries
    assert observation.sample['native_counter_scope'] == 'events_only'
    assert own_amount(battle) == 90
    assert battle.commits == [0, 1, 2]


def test_first_crossing_window_uses_empty_bounded_baseline_then_real_followup():
    battle = CrossingAndroidBattle()
    battle.cross(amount=100)
    observation = battle.poll()
    assert observation.kind == 'sample'
    assert observation.sample['autochess_snapshot_anchor'] == dict(frame=0, game_time=0)
    assert observation.sample['frame'] == 15 and observation.sample['game_time'] == .5
    assert observation.events['drain']['events'] == []
    assert battle.commits == [1]
    assert own_amount(battle) is None
    battle.advance(1)
    battle.cross(amount=60)
    assert battle.poll().kind == 'sample'
    assert own_amount(battle) == 60  # The discarded baseline event is never replayed.
    assert battle.commits == [1, 2]
    own = battle.runtime.display_sample()['multiplayer']['players'][2]
    assert own['coverage_complete'] is False


def _closing_fault(mark, fault):
    mark = deepcopy(mark)
    context = mark['native_autochess_context']
    if fault == 'active':
        mark['active'] = 1
    elif fault == 'scope':
        context['scope_key'] += '1'
    elif fault == 'match':
        context['match_key'] = '0x500000:0x510000'
    elif fault == 'phase':
        context['phase'] = 5
    elif fault == 'round':
        context['round'] += 1
    elif fault == 'account':
        context['players'][0]['uid'] = '091000099'
    elif fault == 'membership':
        context['memberships'][0]['data_uid'] += 10000
    elif fault == 'mode':
        context['collection_mode'] = 'all'
    elif fault == 'root':
        mark['native_identity']['logger'] = '0x520000'
    elif fault == 'process':
        mark['native_process_pid'] += 1
    elif fault == 'stage':
        mark['native_stage_id'] = 'Activities/ACT3AUTOCHESS/level_other'
    elif fault == 'native_clock':
        mark['native_fixed_time'] = '0'
    elif fault == 'native_frame':
        mark['native_frame'] += 1
    elif fault == 'unverified':
        context['verified_same_frame'] = False
    elif fault == 'missing_context':
        mark.pop('native_autochess_context')
    elif fault == 'sequence_rollback':
        mark['sequence'] = 0
    elif fault == 'epoch_rollback':
        mark['epoch'] = 0
    else:
        raise AssertionError(fault)
    return mark


@pytest.mark.parametrize('fault', ['active', 'scope', 'match', 'phase', 'round', 'account',
    'membership', 'mode', 'root', 'process', 'stage', 'unverified', 'missing_context',
    'sequence_rollback', 'epoch_rollback', 'native_clock', 'native_frame'])
def test_uncertain_or_changed_closing_evidence_cannot_take_a_new_receipt(fault):
    battle = established()
    battle.advance(1)
    battle.cross(amount=60)
    mark, calls = battle.observer.mark, []

    def changed():
        value = mark()
        calls.append(None)
        return _closing_fault(value, fault) if len(calls) == 2 else value

    battle.observer.mark = changed
    observation = None
    try:
        observation = battle.poll()
    except (CaptureRetry, StreamGap, FrameMismatch):
        pass
    assert observation is None or observation.kind != 'sample'
    assert battle.commits == [0, 1]
    assert own_amount(battle) == 30


def test_opening_callback_in_flight_cannot_use_scoped_cross_frame_path():
    battle = established()
    battle.advance(1)
    battle.cross(amount=60)
    mark, calls = battle.observer.mark, []

    def active():
        value = mark()
        calls.append(None)
        return dict(value, active=1) if len(calls) == 1 else value

    battle.observer.mark = active
    with pytest.raises((CaptureRetry, StreamGap, FrameMismatch)):
        battle.poll()
    assert battle.commits == [0, 1] and own_amount(battle) == 30


@pytest.mark.parametrize('snapshot_shift,closing_shift', [(-.5, .5), (0, -.5)])
def test_collector_or_closing_clock_cannot_regress(snapshot_shift, closing_shift):
    battle = established()
    battle.advance(1)
    battle.cross(snapshot_shift=snapshot_shift, closing_shift=closing_shift)
    with pytest.raises((CaptureRetry, StreamGap, FrameMismatch)):
        battle.poll()
    assert battle.commits == [0, 1] and own_amount(battle) == 30


@pytest.mark.parametrize('legacy', ['unknown', 'ordinary', 'terminal'])
def test_no_cross_frame_relaxation_for_legacy_or_terminal(legacy):
    battle = established()
    if legacy == 'unknown':
        battle.context_available = False
    elif legacy == 'ordinary':
        battle.stage = 'Obt/Main/level_main_01-01'
    else:
        battle.state = 3
    battle.advance(1)
    battle.cross(amount=60)
    try:
        observation = battle.poll()
    except (CaptureRetry, StreamGap, FrameMismatch):
        observation = None
    assert observation is None or observation.sample is None
    assert battle.commits == [0, 1] and own_amount(battle) == 30


def test_crossing_does_not_publish_the_older_native_counter_values():
    battle = established()
    original = battle.read
    cid = battle.mark()['native_autochess_context']['memberships'][2]['cid']

    def counted():
        return dict(original(), global_total=90000, global_heal=80000, sum_damage=90000,
            rows=[dict(id=cid, damage=90000, healing=80000, physical=90000,
                       arts=0, true=0, elemental=0)])

    battle.read = counted
    battle.advance(1)
    battle.cross(amount=60)
    observation = battle.poll()
    assert observation.sample['rows'] == []
    assert observation.sample['global_total'] is None
    assert observation.sample['global_heal'] is None
    assert observation.sample['sum_damage'] is None
    assert own_amount(battle) == 90


def test_unchanged_frame_keeps_authentic_zero_counter_initial_evidence():
    battle = CrossingAndroidBattle()
    original = battle.read
    cid = battle.mark()['native_autochess_context']['memberships'][2]['cid']
    zero = dict(id=cid, damage=0, healing=0, physical=0, arts=0, true=0, elemental=0)

    def counted():
        return dict(original(), global_total=0, global_heal=0, sum_damage=0, rows=[dict(zero)])

    battle.read = counted
    observation = battle.poll()
    assert observation.sample['rows'] == [zero]
    assert observation.sample['global_total'] == observation.sample['global_heal'] == 0
    assert observation.sample['sum_damage'] == 0
    assert battle.commits == [0]
    battle.advance(1, amounts=((3, 60),))
    assert battle.poll().kind == 'sample'
    own = battle.runtime.display_sample()['multiplayer']['players'][2]
    assert own['observed_damage'] == 60 and own['coverage_complete'] is True


@pytest.mark.parametrize('fault', ['missing_sequence', 'event_clock_ahead'])
def test_scoped_window_never_hides_invalid_bounded_event_sequence_or_clock(fault):
    battle = established()
    battle.advance(1)
    battle.cross(amount=60)
    read = battle.observer.read_through

    def invalid(through):
        result = deepcopy(read(through))
        if fault == 'missing_sequence':
            result['events'] = []
        else:
            result['events'][0]['frame'] += 1
        return result

    battle.observer.read_through = invalid
    try:
        battle.poll()
    except (CaptureRetry, StreamGap, FrameMismatch):
        pass
    assert battle.commits == [0, 1]
    assert own_amount(battle) == 30


def test_all_mode_keeps_each_verified_account_and_one_global_ack():
    battle = CrossingAndroidBattle()
    battle.mode = 'all'
    assert battle.poll().kind == 'sample'
    battle.advance(.5, amounts=((1, 10), (3, 30)))
    assert battle.poll().kind == 'sample'
    battle.advance(1)
    battle.cross(amount=60, transition=lambda target: target.advance(target.when, amounts=((1, 20),)))
    assert battle.poll().kind == 'sample'
    players = battle.runtime.display_sample()['multiplayer']['players']
    assert [p['observed_damage'] for p in players] == [30, None, 90, None]
    assert len(battle.runtime.sessions.multiplayer._players) == 4
    assert battle.commits == [0, 2, 4]


@pytest.mark.parametrize('tamper', ['anchor_frame', 'anchor_time', 'guard_start', 'guard_end', 'guard_complete'])
def test_replayed_projection_still_requires_original_anchor_guard(tamper):
    battle = established()
    battle.advance(1)
    battle.cross(amount=60)
    sessions = battle.runtime.sessions
    observation = battle.runtime.feed.acquire(battle, battle.observer,
        published_generation=sessions.generation, preparing_generation=sessions.preparing_generation,
        published_capture=sessions.capture)
    sample = deepcopy(observation.sample)
    if tamper == 'anchor_frame':
        sample['autochess_snapshot_anchor']['frame'] -= 1
    elif tamper == 'anchor_time':
        sample['autochess_snapshot_anchor']['game_time'] = .5
    elif tamper == 'guard_start':
        sample['guard']['start'] += 1
    elif tamper == 'guard_end':
        sample['guard']['end'] += 1
    else:
        sample['guard']['complete'] = False
    try:
        accepted = battle.runtime._consume(replace(observation, sample=sample))
    except (CaptureRetry, StreamGap, FrameMismatch):
        accepted = False
    assert accepted is False or sessions.event_status == 'gap'
    assert battle.commits == [0, 1]
    assert own_amount(battle) == 30
