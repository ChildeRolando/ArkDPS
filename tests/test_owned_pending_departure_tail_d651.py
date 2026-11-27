"""Departure cannot turn an unaccepted pending tail into a new baseline."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from battle_lifecycle import BattleSessions
from capture_battles import observation_from_json
from event_capture import CaptureRetry
from event_probe import StreamGap, stream_gap_failure_context
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable
from test_pending_scope_start import pending_case, start_pending


def depart_pending(case):
    source = case.fixture
    original_lifecycle = source.collector.read_lifecycle
    life = deepcopy(original_lifecycle())
    life['battle'] = None
    source.collector.read_lifecycle = lambda: deepcopy(life)
    ending = source.poll()
    source.collector.read_lifecycle = original_lifecycle
    assert ending.events['pending_scope_end']['reason'] == 'battle_absent'
    assert source.commits == [0, 1]
    return ending


def test_pending_departure_unread_tail_does_not_become_new_baseline(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    case.advance(2, amount=75)
    depart_pending(case)
    assert len(source.queue) == 1
    certificate = source.runtime.sessions.capture.rejected_pending_prefix
    case.advance(3, phase=4, next_round=True)
    for _ in range(2):
        with pytest.raises(StreamGap) as rejected:
            source.poll()
        assert stream_gap_failure_context(rejected.value, 'acquire')['reason'] == 'pending_handoff_tail'
        assert source.commits == [0, 1]
        assert source.queue[0]['amount'] == 75
        assert source.runtime.sessions.capture.rejected_pending_prefix is certificate


def test_empty_departure_handoff_recovers_then_counts_first75_once_and_replays(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    depart_pending(case)
    assert source.runtime.sessions.capture.rejected_pending_prefix.departure is not None
    case.advance(3, phase=5, next_round=True)
    resumed = source.poll()
    assert resumed.kind == 'sample'
    assert resumed.events['pending_scope_superseded']['mode'] == 'scope_end'
    assert not hasattr(source.runtime.sessions.capture, 'rejected_pending_prefix')
    assert source.commits == [0, 1, 1]
    case.advance(3.5, amount=75)
    source.poll()
    assert source.commits == [0, 1, 1, 2]
    source.poll()
    assert source.commits == [0, 1, 1, 2, 2]
    assert source.queue == []
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    replay = BattleSessions(resumed.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == source.runtime.sessions.multiplayer.result()
    assert replay.pop_archives() == source.runtime.sessions.pop_archives()


def test_verified_empty_departure_candidate_retry_keeps_certificate_until_commit(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    depart_pending(case)
    certificate = source.runtime.sessions.capture.rejected_pending_prefix
    case.advance(3, phase=5, next_round=True)

    def unavailable(*args, **kwargs):
        raise MappingUnavailable('owned candidate retry after valid empty handoff')

    with monkeypatch.context() as retry:
        retry.setattr(BattleStatisticsAdapter, 'update', unavailable)
        with pytest.raises(CaptureRetry):
            source.poll()
    assert source.runtime.sessions.capture.rejected_pending_prefix is certificate
    assert source.commits == [0, 1]
    assert source.queue == []
    source.poll()
    assert not hasattr(source.runtime.sessions.capture, 'rejected_pending_prefix')
    case.advance(3.5, amount=75)
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75


@pytest.mark.parametrize('fault', ['latest_epoch', 'latest_sequence', 'latest_quality', 'anchor_epoch'])
def test_serialized_departure_successor_rechecks_old_proof_before_install(monkeypatch, fault):
    """Feed admission cannot replace the replay consumer's independent check."""
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    depart_pending(case)
    records = list(case.records)
    case.advance(3, phase=5, next_round=True)
    candidate = source.runtime._acquire(source.collector, source.observer)
    original = asdict(candidate)
    forged = deepcopy(original)
    proof = forged['events']['pending_scope_superseded']
    if fault == 'latest_epoch':
        proof['latest']['after']['epoch'] += 1
    elif fault == 'latest_sequence':
        proof['latest']['after']['sequence'] += 1
    elif fault == 'latest_quality':
        proof['latest']['after']['damage_unknown'] = True
    else:
        proof['anchor']['after']['epoch'] += 1
    replay = BattleSessions(candidate.feed)
    for record in records:
        assert replay.consume(observation_from_json(asdict(record)))
    generation, sequence = replay.generation, replay.sequence
    with pytest.raises(StreamGap):
        replay.consume(observation_from_json(forged))
    assert replay.generation == generation and replay.sequence == sequence
    assert replay.consume(observation_from_json(original))
    assert replay.event_status == 'recording' and not replay.capture.closed
    assert source.commits == [0, 1]
    assert source.queue == []


@pytest.mark.parametrize('stage', ['before', 'inside', 'after'])
@pytest.mark.parametrize('continuation', ['original_scope', 'new_scope'])
def test_pending_end_retry_can_resume_without_losing_first_hit_or_replay(
        monkeypatch, stage, continuation):
    from event_stream import BarrierBusy

    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    pending = source.runtime.feed._pending_scope
    old_capture = source.runtime.sessions.capture
    old_generation = source.runtime.sessions.generation
    old_multiplayer = deepcopy(source.runtime.sessions.multiplayer.result())
    original_records = len(case.records)
    if continuation == 'original_scope':
        case.advance(2, amount=75)
    original_lifecycle = source.collector.read_lifecycle
    life = deepcopy(original_lifecycle())
    life['battle'] = None
    source.collector.read_lifecycle = lambda: deepcopy(life)
    events = BattleSessions._events
    event_calls = []

    def before_update(*args, **kwargs):
        raise MappingUnavailable('owned pending end before baseline update')

    def staged_events(sessions, observation):
        event_calls.append(observation.sample['game_time'])
        target = 1 if stage == 'inside' else 2
        if len(event_calls) == target:
            raise BarrierBusy('owned pending end event stage retry')
        return events(sessions, observation)

    with monkeypatch.context() as retry:
        if stage == 'before':
            retry.setattr(BattleStatisticsAdapter, 'update', before_update)
        else:
            retry.setattr(BattleSessions, '_events', staged_events)
        with pytest.raises(CaptureRetry):
            source.poll()
    assert source.runtime.feed.identity is None
    assert source.runtime.feed._pending_scope is pending
    assert source.runtime.sessions.capture is old_capture
    assert source.runtime.sessions.generation == old_generation
    assert source.runtime.sessions.multiplayer.result() == old_multiplayer
    assert len(case.records) == original_records
    assert source.commits == [0, 1]
    assert len(source.queue) == int(continuation == 'original_scope')
    if source.queue:
        assert source.queue[0]['amount'] == 75
    source.collector.read_lifecycle = original_lifecycle

    if continuation == 'original_scope':
        case.advance(2.5)
        resumed = source.poll()
    else:
        case.advance(2, phase=5, next_round=True)
        ending = source.poll()
        assert ending.events['pending_scope_exit']['version'] == 1
        assert source.runtime.feed._pending_scope is None
        assert source.commits == [0, 1]
        resumed = source.poll()
        case.advance(2.5, amount=75)
        source.poll()
    assert resumed.kind == 'sample'
    assert source.runtime.feed._pending_scope is None
    assert source.queue == []
    assert source.commits[-1] == 2
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    replay = BattleSessions(resumed.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == source.runtime.sessions.multiplayer.result()
    assert replay.pop_archives() == source.runtime.sessions.pop_archives()
