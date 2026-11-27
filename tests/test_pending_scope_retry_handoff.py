"""A pending installation commits its baseline and current prefix atomically."""
import pytest

from battle_lifecycle import BattleSessions
from event_capture import CaptureRetry
from event_probe import reset_probe_cursor
from event_stream import BarrierBusy
from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch, MappingUnavailable
from test_pending_scope_start import pending_case, start_pending


@pytest.mark.parametrize('error', [MappingUnavailable, BarrierBusy, CaptureRetry, FrameMismatch])
@pytest.mark.parametrize('extra_hit', [False, True])
def test_pending_install_retry_preserves_old_state_and_recovers_without_ack(monkeypatch, error, extra_hit):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    runtime = source.runtime
    old_generation = runtime.sessions.generation
    old_capture = runtime.sessions.capture
    old_multiplayer = runtime.sessions.multiplayer.result()
    old_records = len(case.records)
    pending = runtime.feed._pending_scope
    case.advance(2, amount=75)
    original = BattleStatisticsAdapter.update
    calls = []

    def transient(adapter, sample, *args, **kwargs):
        calls.append(sample['game_time'])
        if len(calls) == 2:
            raise error('current update is temporarily unavailable')
        return original(adapter, sample, *args, **kwargs)

    with monkeypatch.context() as retry:
        retry.setattr(BattleStatisticsAdapter, 'update', transient)
        with pytest.raises((CaptureRetry, FrameMismatch)):
            source.poll()
    assert calls == [1.5, 2]
    runtime = source.runtime
    assert runtime.feed._pending_scope is pending
    assert runtime.sessions.generation == old_generation
    assert runtime.sessions.capture is old_capture
    assert runtime.sessions.multiplayer.result() == old_multiplayer
    assert len(case.records) == old_records
    assert runtime._pending_event_ack is None
    assert source.commits == [0, 1] and len(source.queue) == 1
    if extra_hit:
        case.advance(2.5, amount=25)
    assert source.poll().kind == 'sample'
    assert source.commits == [0, 1, 3 if extra_hit else 2]
    player = runtime.sessions.multiplayer.result()['players'][2]
    assert player['observed_damage'] == (100 if extra_hit else 75)
    source.poll()
    assert runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == player['observed_damage']
    assert source.queue == []
    replay = BattleSessions(runtime.feed.id)
    for observation in case.records:
        replay.consume(observation)
    assert replay.multiplayer.result() == runtime.sessions.multiplayer.result()


@pytest.mark.parametrize('fault', ['before_baseline', 'inside_baseline', 'cursor', 'observer'])
def test_retry_cannot_transfer_an_unadmitted_or_foreign_pending_baseline(monkeypatch, fault):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    pending = source.runtime.feed._pending_scope
    case.advance(2, amount=75)
    update = BattleStatisticsAdapter.update
    events = BattleSessions._events
    calls = []

    def transient(adapter, sample, *args, **kwargs):
        calls.append(sample['game_time'])
        if len(calls) == (1 if fault == 'before_baseline' else 2):
            if fault == 'cursor':
                reset_probe_cursor(source.observer)
            elif fault == 'observer':
                sequence, _, lifetime = source.runtime.feed._emitted_cursor
                source.runtime.feed._emitted_cursor = sequence, object(), lifetime
            raise MappingUnavailable('unadmitted or foreign baseline')
        return update(adapter, sample, *args, **kwargs)

    def baseline_busy(sessions, observation, **kwargs):
        if sessions.generation == pending['generation']:
            raise BarrierBusy('baseline itself has not been admitted')
        return events(sessions, observation, **kwargs)

    with monkeypatch.context() as retry:
        if fault == 'inside_baseline':
            retry.setattr(BattleSessions, '_events', baseline_busy)
        else:
            retry.setattr(BattleStatisticsAdapter, 'update', transient)
        with pytest.raises(CaptureRetry):
            source.poll()
    assert source.runtime.feed._pending_scope is pending
    assert source.commits == [0, 1] and len(source.queue) == 1
