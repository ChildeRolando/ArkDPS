"""Retried pending installation progresses once and records replay truthfully."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from battle_lifecycle import BattleSessions
from capture_battles import observation_from_json
from event_capture import CaptureRetry
from event_stream import BarrierBusy
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable
from test_pending_scope_start import pending_case, start_pending


@pytest.mark.parametrize('stage', ['before', 'inside', 'after'])
def test_pending_install_retry_restores_first_hit_archive_and_replay(monkeypatch, stage):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    case.advance(2, amount=75)
    original_update = BattleStatisticsAdapter.update
    original_events = BattleSessions._events
    pending = source.runtime.feed._pending_scope
    original_generation = source.runtime.sessions.generation
    original_capture = source.runtime.sessions.capture
    original_multiplayer = deepcopy(source.runtime.sessions.multiplayer.result())
    original_records = len(case.records)
    calls = []

    def transient_update(adapter, sample, *args, **kwargs):
        calls.append(sample['game_time'])
        if len(calls) == (1 if stage == 'before' else 2):
            raise MappingUnavailable('owned transient installation mapping')
        return original_update(adapter, sample, *args, **kwargs)

    def baseline_busy(sessions, observation, **kwargs):
        if sessions.generation == pending['generation']:
            raise BarrierBusy('owned transient baseline')
        return original_events(sessions, observation, **kwargs)

    with monkeypatch.context() as retry:
        if stage == 'inside':
            retry.setattr(BattleSessions, '_events', baseline_busy)
        else:
            retry.setattr(BattleStatisticsAdapter, 'update', transient_update)
        with pytest.raises(CaptureRetry):
            source.poll()

    assert source.commits == [0, 1] and len(source.queue) == 1
    assert source.runtime.sessions.generation == original_generation
    assert source.runtime.sessions.capture is original_capture
    assert source.runtime.sessions.multiplayer.result() == original_multiplayer
    assert len(case.records) == original_records
    assert source.poll().kind == 'sample'
    assert source.runtime.feed._pending_scope is None
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert source.commits == [0, 1, 2]

    # A duplicate ordinary poll must neither replay damage nor duplicate archive.
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    archives = source.runtime.sessions.pop_archives()
    assert len(archives) == 1
    replay = BattleSessions(case.records[-1].feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == source.runtime.sessions.multiplayer.result()
    assert replay.pop_archives() == archives
