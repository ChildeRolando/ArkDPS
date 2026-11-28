"""Integration contract for retained pending scopes and real transition activity.

The source-state delta is measured by executing the production JS hook. Python
then applies that measured delta to the existing owned collector/window fixture;
this is not claimed to replay a user's complete native callback stream.
"""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess

import pytest

from battle_lifecycle import BattleSessions
from capture_battles import observation_from_json
from event_capture import CaptureRetry
from event_probe import StreamGap
from test_pending_scope_start import live_scope_case as pending_case
from test_pc_serial_clock_reset_end import reset_native_clock


@pytest.fixture(scope='module')
def production_transition_delta():
    root = Path(__file__).resolve().parents[1]
    script = r"""
const path=require('node:path'),assert=require('node:assert/strict');
const root=process.argv[1];
const {fixture}=require(path.join(root,'tests/native_pc_frida_probe_fixture.js'));
const f=fixture({sourcePath:path.join(root,'event_probe_native_pc.js'),researchHpReference:false});
f.clock(1401,46n*4294967296n);
const before=f.context.rpc.exports.mark();
f.write(f.controller.add(0x228),3);f.clock(5,715827882n);
const hook=f.callback('set_state',f.controllerClass),call={threadId:1};
hook.onEnter.call(call,[f.controller,f.ptr(2)]);
f.write(f.controller.add(0x228),2);hook.onLeave.call(call);
const after=f.context.rpc.exports.mark();
assert.equal(after.active,0);assert.equal(after.sequence,before.sequence);
assert.equal(f.context.rpc.exports.readEvents(before.sequence,after.sequence).events.length,0);
console.log(JSON.stringify({epoch:after.epoch-before.epoch,
 generation:after.native_generation-before.native_generation,
 sequence:after.sequence-before.sequence}));
"""
    result = subprocess.run(['node', '-e', script, str(root)], check=True,
                            capture_output=True, text=True, timeout=20)
    delta = json.loads(result.stdout)
    assert delta == {'epoch': 1, 'generation': 1, 'sequence': 0}
    return delta


def enter_empty_combat_scope(case):
    """A new live scope installs its real partial baseline, never a pending FSM."""
    case.advance(1.5, phase=5)
    source = case.fixture
    observation = source.poll()
    assert observation.kind == 'sample'
    assert source.runtime.feed._pending_scope is None
    assert source.runtime.sessions.adapter.event_stream.game_time == 1.5
    assert source.runtime.sessions.multiplayer.result()['coverage_start'] == 1.5
    assert max(source.commits) == 1
    return observation


def replay_matches(case, expected_history_damage=None):
    source = case.fixture
    replay = BattleSessions(source.runtime.feed.id)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == source.runtime.sessions.multiplayer.result()
    archives = list(source.runtime.sessions.pop_archives())
    assert list(replay.pop_archives()) == archives
    if expected_history_damage is not None:
        assert [record['last_sample']['multiplayer']['players'][2]['observed_damage']
                for record in archives] == expected_history_damage


def install_stop_sentinel(source):
    owner = source.observer
    owner.stop = lambda: pytest.fail('scope recovery must not stop the same source')
    return owner


def test_real_state_transition_epoch_is_not_an_unread_output_tail(
        monkeypatch, production_transition_delta):
    case = pending_case(monkeypatch)
    source = case.fixture
    owner = install_stop_sentinel(source)
    enter_empty_combat_scope(case)
    original_clock = source.runtime.sessions.adapter.event_stream.game_time
    commits = list(source.commits)
    reset_native_clock(case, generation_step=production_transition_delta['generation'])
    case.extra_epoch += production_transition_delta['epoch']
    resumed = source.poll()
    assert resumed.kind == 'sample'
    assert source.runtime.feed._pending_scope is None
    assert source.observer is owner
    assert source.queue == [] and max(source.commits) == max(commits)
    assert resumed.sample['autochess_snapshot_anchor']['game_time'] == 0
    assert original_clock == 1.5
    case.advance(.5, amount=75)
    source.poll()
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    replay_matches(case)


def test_independent_crossframe_successor_does_not_hold_an_empty_scope(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    enter_empty_combat_scope(case)
    case.advance(2, phase=7)
    sample, mark = source.collector.process.sample, source.observer.mark
    captured = [False]
    def independent(**kwargs):
        value = sample(**kwargs)
        captured[0] = True
        return value
    def closing():
        if captured[0]:
            case.advance(2.25)
        return mark()
    with monkeypatch.context() as window:
        window.setattr(source.collector.process, 'sample', independent)
        window.setattr(source.observer, 'mark', closing)
        ending = source.poll()
    assert ending.kind == 'preparing'
    assert source.runtime.feed._pending_scope is None
    assert max(source.commits) == 1 and source.queue == []
    assert ending.sample['autochess_snapshot_anchor']['game_time'] == 2
    source.poll()
    case.advance(3, phase=4, next_round=True)
    source.poll()
    case.advance(3.5, amount=75)
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    replay_matches(case)


def test_first_new_scope_hit_is_consumed_without_baseline_discard(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    owner = install_stop_sentinel(source)
    enter_empty_combat_scope(case)
    case.advance(2, phase=5, next_round=True, amount=75)
    raw = deepcopy(source.queue)
    assert raw[0]['amount'] == 75
    source.poll()
    assert source.runtime.feed._pending_scope is None
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert source.commits[-1] == raw[-1]['sequence'] and source.queue == []
    assert source.observer is owner
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    replay_matches(case)


def test_old_tail_without_closing_proof_cannot_be_acked_as_new_scope(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    enter_empty_combat_scope(case)
    case.advance(1.75, amount=75)  # No independent old-scope closing sample.
    case.advance(2, phase=5, next_round=True, amount=75)
    raw = deepcopy(source.queue)
    for _ in range(2):
        with pytest.raises((StreamGap, CaptureRetry)):
            source.poll()
        assert source.queue == raw
        assert max(source.commits) == 1
        assert source.runtime.sessions.adapter.event_stream.sequence == 1


def test_consecutive_phases_keep_every_first75_once(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    owner = install_stop_sentinel(source)
    # Each combat scope's first output is already in the bounded source range
    # before its first host sample; preparation itself has no invented output.
    for when, phase, next_round in [(1.5, 5, False), (2, 6, False),
                                     (3, 4, True), (3.5, 5, False)]:
        if when == 3:
            case.advance(2.5, phase=7)
            source.poll()
        case.advance(when, phase=phase, next_round=next_round, amount=75)
        source.poll()
        assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
        source.poll()
        assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
        assert source.queue == [] and source.observer is owner
    replay_matches(case, [300, 75, 75, 75])



def test_transient_prefix_busy_then_new_scope_first75_recovers(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    owner = install_stop_sentinel(source)
    enter_empty_combat_scope(case)
    baseline_generation = source.runtime.sessions.generation
    baseline_capture = source.runtime.sessions.capture
    baseline_records = len(case.records)
    case.advance(2, phase=5, next_round=True, amount=75)
    read_range = source.observer.read_round_start_range
    attempts = []
    def busy_once(after, through):
        attempts.append((after, through))
        if len(attempts) == 1:
            return dict(retry=True)
        return read_range(after, through)
    monkeypatch.setattr(source.observer, 'read_round_start_range', busy_once)
    with pytest.raises(CaptureRetry):
        source.poll()
    assert source.runtime.feed._pending_scope is None
    assert source.runtime.sessions.generation == baseline_generation
    assert source.runtime.sessions.capture is baseline_capture
    assert max(source.commits) == 1 and source.queue[0]['amount'] == 75
    assert len(case.records) == baseline_records
    source.poll()
    assert len(attempts) >= 2
    assert source.runtime.feed._pending_scope is None
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert source.observer is owner and source.queue == []
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    replay_matches(case)


def test_new_guarded_battle_keeps_same_source_and_first75(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    owner = install_stop_sentinel(source)
    case.advance(1.5, phase=7)
    source.poll()
    old_instance = source.collector.process.process_instance
    source.identity_roots['controller'] = '0x60000'
    source.native_generation += 1
    source.autochess_epoch += 1
    source.native_round = 1
    case.advance(0, phase=4, amount=75)
    # An opening root proposal may await its independent read; that retry
    # cannot acknowledge or discard the retained new-battle output.
    try:
        source.poll()
    except CaptureRetry:
        assert max(source.commits) == 1 and source.queue[0]['amount'] == 75
        source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert source.collector.process.process_instance == old_instance
    assert source.observer is owner and source.queue == []
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    replay_matches(case, [300])



def test_one_source_survives_three_battles_six_rounds_with_every_first_hit(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    owner = install_stop_sentinel(source)
    owner.start = lambda: pytest.fail('one observer must not restart between scopes')
    process_owner = source.collector.process.process_instance
    original_binding = source.binding
    match_key = ['0x400000:0x410000']
    def binding():
        value = original_binding()
        value['native_autochess_context']['match_key'] = match_key[0]
        return value
    monkeypatch.setattr(source, 'binding', binding)
    read_range = source.observer.read_round_start_range
    busy = [False]
    busy_count = []
    def retained_range(after, through):
        if busy[0]:
            busy[0] = False
            busy_count.append((after, through))
            return dict(retry=True)
        return read_range(after, through)
    monkeypatch.setattr(source.observer, 'read_round_start_range', retained_range)

    expected_sequence = source.sequence
    completed_segments = []
    root_sets = []
    crossed = []
    for game in range(3):
        source.identity_roots['controller'] = hex(0x60000 + game * 0x10000)
        match_key[0] = f'{hex(0x500000 + game * 0x10000)}:{hex(0x510000 + game * 0x10000)}'
        root_sets.append((source.identity_roots['controller'], match_key[0]))
        source.native_generation += 1
        source.autochess_epoch += 1
        source.native_round = 1
        for round_index in range(6):
            for phase, offset in ((4, 0), (5, 1), (6, 2)):
                when = round_index * 4 + offset
                case.advance(when, phase=phase,
                             next_round=phase == 4 and round_index > 0, amount=75)
                expected_sequence += 1
                assert source.sequence == expected_sequence
                assert len(source.queue) == 1 and source.queue[0]['sequence'] == expected_sequence
                before_ack = max(source.commits)
                use_busy = (game, round_index, phase) in ((0, 2, 5), (2, 4, 6))
                if use_busy:
                    busy[0] = True
                    with pytest.raises(CaptureRetry):
                        source.poll()
                    assert max(source.commits) == before_ack
                    assert len(source.queue) == 1 and source.queue[0]['amount'] == 75
                use_crossframe = (game, round_index, phase) in ((1, 1, 5), (2, 3, 6))
                if use_crossframe:
                    sample = source.collector.process.sample
                    mark = source.observer.mark
                    sampled = [False]
                    def independent(**kwargs):
                        value = sample(**kwargs)
                        sampled[0] = True
                        return value
                    def closing():
                        if sampled[0]:
                            case.advance(when + .25)
                        return mark()
                    with monkeypatch.context() as window:
                        window.setattr(source.collector.process, 'sample', independent)
                        window.setattr(source.observer, 'mark', closing)
                        observation = source.poll()
                    assert observation.sample['autochess_snapshot_anchor']['game_time'] == when
                    assert observation.sample['game_time'] == when + .25
                    crossed.append((game, round_index, phase))
                else:
                    observation = source.poll()
                assert observation.kind == 'sample'
                assert source.runtime.feed._pending_scope is None
                result = source.runtime.sessions.multiplayer.result()
                assert result['players'][2]['observed_damage'] == 75
                assert result['players'][2]['damage'] is None
                assert result['players'][2]['coverage_complete'] is False
                assert source.commits[-1] == expected_sequence and source.queue == []
                assert source.observer is owner
                assert source.collector.process.process_instance == process_owner
                source.poll()
                assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
                completed_segments.append((game, round_index, phase))
            case.advance(round_index * 4 + 3, phase=7)
            assert source.poll().kind == 'preparing'
            assert max(source.commits) == expected_sequence
            assert source.queue == []
    assert len(completed_segments) == 54
    assert len(set(root_sets)) == 3
    assert len(busy_count) == 2 and len(crossed) == 2
    assert all(left <= right for left, right in zip(source.commits, source.commits[1:]))
    assert max(source.commits) == 55  # The initial fixture's 300 is global event1.
    replay_matches(case, [300] + [75] * 54)



def test_sessions_rejects_self_consistent_prefix_that_skips_unaccepted_first_hit(monkeypatch):
    from event_probe import verified_partial_scope_prefix

    case = pending_case(monkeypatch)
    source = case.fixture
    case.advance(1.5, phase=5, amount=75)
    case.advance(2, amount=75)
    observation = source.runtime._acquire(source.collector, source.observer)
    assert observation.events['partial_scope_prefix']['after'] == 1
    raw = asdict(observation)
    evidence = raw['events']
    tx = evidence['partial_scope_prefix']
    tx['after'] = 2
    page, = tx['pages']
    page['after'] = 2
    page['batch']['events'] = page['batch']['events'][1:]
    evidence['drain']['events'] = evidence['drain']['events'][1:]
    # The raw current-scope page is internally consistent; only the existing
    # receiver knows that seq2 remains unconsumed and cannot be the new anchor.
    assert verified_partial_scope_prefix(raw['sample'], evidence)['after'] == 2
    sessions = source.runtime.sessions
    baseline = sessions.generation, sessions.sequence, sessions.capture
    with pytest.raises(StreamGap):
        sessions.consume(observation_from_json(raw))
    assert (sessions.generation, sessions.sequence, sessions.capture) == baseline
    assert max(source.commits) == 1 and [event['sequence'] for event in source.queue] == [2, 3]
    consumed = source.runtime._consume(observation)
    source.runtime._acknowledge_observation(observation, source.observer, consumed)
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 150
    assert source.queue == [] and source.commits[-1] == 3
    replay_matches(case, [300])
