"""Host-owned same-frame event races, with the real collector and event cores.

The probe's owned report is synthetic. No memory handle, game or observer is
opened; callbacks are queued after the native pair and before its closing mark.
"""
from copy import deepcopy

import pytest

from battle_lifecycle import BattleRuntime, BattleSessions
from event_capture import CaptureRetry
from event_stream import baseline_barrier, BarrierBusy
from event_probe import NATIVE_PC_CONTRACT
from test_multiplayer_integration import NativeBattle
from test_native_pc_autochess_collector_gate import fixture, SCOPE
from test_multiplayer_native_context import hit, stamp


def battle(tmp_path):
    collector, probe, observer, commits, records = fixture(tmp_path)
    original_mark = observer.mark

    def marked():
        return dict(original_mark(), native_frame=probe.frame, damage_settlement_scope=0,
                    native_fixed_time=str(int(probe.when * (1 << 32))))

    observer.mark = marked
    runtime = BattleRuntime()
    return runtime, collector, probe, observer, commits, records


def race(collector, records, callback):
    paired = collector.read_guarded_pair

    def pair(lifecycle):
        result = paired(lifecycle)
        callback(records)
        collector.read_guarded_pair = paired
        return result

    collector.read_guarded_pair = pair


def test_established_events_only_admits_same_frame_callback_and_replay_once(tmp_path):
    runtime, collector, probe, observer, commits, records = battle(tmp_path)
    runtime.poll(collector, observer)
    probe.frame, probe.when = 30, 1
    original = hit(1, 3, 1, 100, event_scope=stamp(root=SCOPE))
    race(collector, records, lambda queue: queue.append(deepcopy(original)))

    observation = runtime.poll(collector, observer)
    assert observation.events['before']['sequence'] == 0
    assert observation.events['after']['sequence'] == observation.events['sequence'] == 1
    assert observation.events['before']['epoch'] == 0
    assert observation.events['after']['epoch'] == 1
    # The cumulative-counter barrier stays strict. Only the already-established
    # events-only acquisition/consumption path may accept this ordered prefix.
    with pytest.raises(BarrierBusy):
        baseline_barrier(observation.events['before'], observation.sample, observation.events['after'])
    view = runtime.display_sample()
    assert view['statistics']['rows'] == {} and view['event_status'] == 'recording'
    assert [p['observed_damage'] for p in view['multiplayer']['players']] == [None, None, 100, None]
    assert commits == [0, 1]
    assert runtime.sessions.capture.adapter.event_stream.received[1] == original

    runtime.poll(collector, observer)  # Bounded raw replay has the same record.
    assert runtime.display_sample()['multiplayer']['players'][2]['observed_damage'] == 100
    assert commits == [0, 1, 1]


def test_epoch_only_callback_is_not_a_new_counter_baseline(tmp_path):
    runtime, collector, probe, observer, commits, records = battle(tmp_path)
    runtime.poll(collector, observer)
    probe.frame, probe.when = 30, 1
    marked, counter = observer.mark, [0]

    def mark():
        counter[0] += 1
        return dict(marked(), epoch=int(counter[0] > 1))

    observer.mark = mark
    observation = runtime.poll(collector, observer)
    assert observation.events['before']['epoch'] == 0
    assert observation.events['after']['epoch'] == 1
    assert observation.events['sequence'] == 0
    assert runtime.sessions.event_status == 'recording' and commits == [0, 0]
    assert all(p['observed_damage'] is None for p in runtime.display_sample()['multiplayer']['players'])


def test_same_frame_exception_is_revalidated_by_recorded_observation_consumer(tmp_path):
    runtime, collector, probe, observer, commits, records = battle(tmp_path)
    first = runtime.poll(collector, observer)
    probe.frame, probe.when = 30, 1
    race(collector, records, lambda queue: queue.append(hit(1, 3, 1, event_scope=stamp(root=SCOPE))))
    later = runtime.poll(collector, observer)
    consumer = BattleSessions(first.feed)
    assert consumer.consume(first) and consumer.consume(later)
    assert consumer.event_status == 'recording'
    assert consumer.multiplayer.result()['players'][2]['observed_damage'] == 100

    changed = deepcopy(later)
    changed.events['after']['epoch'] = -1
    invalid_consumer = BattleSessions(first.feed)
    invalid_consumer.consume(first)
    invalid_consumer.consume(changed)
    assert invalid_consumer.event_status == 'gap'
    assert invalid_consumer.adapter.event_stream.sequence == 0
    assert all(p['observed_damage'] is None for p in invalid_consumer.multiplayer.result()['players'])


def test_ordinary_native_cumulative_path_keeps_quiescent_event_guard():
    original = NativeBattle(stage='Obt/Main/level_main_01-07')
    original.observer.contract = NATIVE_PC_CONTRACT
    original.poll()
    original.advance(1)

    def queue_event(queue):
        queue.append(hit(1, 3, 1))
        original.sequence = 1

    race(original.collector, original.queue, queue_event)
    with pytest.raises(CaptureRetry, match='baseline crossed'):
        original.poll()
    assert original.commits == [0]


def test_conflicting_boundary_replay_still_closes_gap_without_new_ack(tmp_path):
    runtime, collector, probe, observer, commits, records = battle(tmp_path)
    runtime.poll(collector, observer)
    probe.frame, probe.when = 30, 1
    race(collector, records, lambda queue: queue.append(hit(1, 3, 1, event_scope=stamp(root=SCOPE))))
    runtime.poll(collector, observer)
    records[0]['amount'] = 999
    race(collector, records, lambda queue: queue.append(hit(2, 3, 1, 50, event_scope=stamp(root=SCOPE))))
    runtime.poll(collector, observer)
    assert runtime.sessions.event_status == 'gap'
    assert commits == [0, 1]
    assert runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 100


def test_first_events_only_baseline_still_requires_quiescent_bracket(tmp_path):
    runtime, collector, probe, observer, commits, records = battle(tmp_path)
    race(collector, records, lambda queue: queue.append(
        hit(1, 3, 0, 100, event_scope=stamp(root=SCOPE))))
    with pytest.raises(CaptureRetry, match='baseline crossed'):
        runtime.poll(collector, observer)
    assert commits == [] and runtime.sessions.capture is None


@pytest.mark.parametrize('problem', ['frame', 'time', 'active', 'epoch_regression',
    'epoch_missing_callback', 'sequence_regression', 'settlement_scope', 'boolean_settlement_scope',
    'missing_fixed_clock', 'unknown_context', 'scope_change',
    'player_change', 'member_change', 'foreign_session', 'foreign_build', 'terminal'])
def test_same_frame_exception_retains_each_identity_and_boundary_guard(tmp_path, problem):
    runtime, collector, probe, observer, commits, records = battle(tmp_path)
    runtime.poll(collector, observer)
    probe.frame, probe.when = 30, 1
    if problem == 'terminal':
        probe.state = 3
    original_mark = observer.mark
    mark_number = 0

    def marked():
        nonlocal mark_number
        mark_number += 1
        result = original_mark()
        if mark_number == 2:
            if problem == 'frame':
                result['frame'] += 1
            elif problem == 'time':
                result['game_time'] += .5
            elif problem == 'active':
                result['active'] = 1
            elif problem == 'epoch_regression':
                result['epoch'] = 0
            elif problem == 'epoch_missing_callback':
                result['epoch'] = 0
            elif problem == 'settlement_scope':
                result['damage_settlement_scope'] = 1
            elif problem == 'boolean_settlement_scope':
                result['damage_settlement_scope'] = False
            elif problem == 'sequence_regression':
                result['sequence'] = 0
            elif problem == 'missing_fixed_clock':
                result.pop('native_fixed_time')
            elif problem == 'unknown_context':
                result['native_autochess_context'].update(status='unknown', verified_same_frame=False)
            elif problem == 'scope_change':
                result['native_autochess_context']['scope_key'] = SCOPE[:-1] + '2'
            elif problem == 'player_change':
                result['native_autochess_context']['players'][0]['uid'] = '920000001'
            elif problem == 'member_change':
                result['native_autochess_context']['memberships'][0]['data_uid'] += 1
            elif problem == 'foreign_session':
                result['native_session'] += ':foreign'
            elif problem == 'foreign_build':
                result['source_build'] += ':foreign'
        elif problem == 'epoch_regression':
            result['epoch'] = 2
        elif problem == 'sequence_regression':
            result['sequence'] = 1
        return result

    observer.mark = marked
    race(collector, records, lambda queue: queue.append(
        hit(1, 3, 1, 100, event_scope=stamp(root=SCOPE))))
    try:
        runtime.poll(collector, observer)
    except CaptureRetry:
        pass
    assert commits == [0]
    previous = runtime.sessions.latest_sample or {}
    assert previous.get('frame', 0) == 0

