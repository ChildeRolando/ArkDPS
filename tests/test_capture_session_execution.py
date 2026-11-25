"""Diagnostic evidence uses the production lifecycle, without live I/O."""
import json

import pytest

import capture_battles
from event_capture import CaptureRetry
from test_collection_session import Collector, session


@pytest.mark.parametrize('final_damage', [0, 120])
def test_snapshot_diagnostic_journal_replays_the_production_session(tmp_path, monkeypatch, final_damage):
    now = [0.0]
    collector = Collector([])
    collector.damage = 0
    lifecycle = collector.read_lifecycle

    def read_lifecycle():
        value = lifecycle()
        if now[0] >= .4:
            value.update(battle=None, ready=False)
        return value

    def advance(seconds):
        now[0] += seconds
        if now[0] >= .2:
            collector.state = 3
            collector.frame, collector.game_time = 2, 1
            collector.damage = final_damage

    monkeypatch.setattr(collector, 'read_lifecycle', read_lifecycle)
    monkeypatch.setattr(capture_battles, 'Collector', lambda *_args: collector)
    monkeypatch.setattr(capture_battles.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(capture_battles.time, 'sleep', advance)
    summary = capture_battles.capture(.7, tmp_path)
    records = [json.loads(line) for line in (tmp_path / 'observations.jsonl').read_text(encoding='utf-8').splitlines()]
    assert summary['samples'] == 1 and summary['archives'] == 1
    archives = [value['archive_ready'] for value in records if 'archive_ready' in value]
    assert archives[0]['statistics']['rows']['char_a']['damage'] == final_damage
    assert archives[0]['statistics']['ended'] is True
    assert archives[0]['event_status'] == 'unavailable'
    assert 'attach' not in collector.calls and 'locate' not in collector.calls
    assert collector.calls.count('lifecycle') == collector.calls.count('collector_close') == 1
    replayed = capture_battles.replay(tmp_path / 'observations.jsonl')
    assert replayed['archives'] == 1


def test_unbracketed_state_three_cannot_finish_a_default_session(monkeypatch):
    current, collector, _ = session(live=False)
    current.connect()
    try:
        initial = current.read()
        original = collector.read
        monkeypatch.setattr(collector, 'read', lambda: dict(original(), state=3))
        with pytest.raises(CaptureRetry):
            current.read()
        assert current.runtime.sessions.adapter.result()['ended'] is False
        assert current.runtime.sessions.latest_sample['frame'] == initial['frame']
    finally:
        current.close()


def test_empty_diagnostic_keeps_its_disconnect_replayable(tmp_path):
    summary = capture_battles.capture(0, tmp_path)
    replayed = capture_battles.replay(tmp_path / 'observations.jsonl')
    assert summary['samples'] == replayed['samples'] == 0
    assert summary['archives'] == replayed['archives'] == 0
    assert replayed['last_state']['connection'] == 'disconnected'


def test_sessions_cannot_implicitly_retire_a_generation_on_backward_clock():
    from copy import deepcopy
    from dataclasses import replace
    from snapshot_adapter import FrameMismatch
    from test_battle_lifecycle import begin

    _, runtime, first = begin()
    sessions = runtime.sessions
    original_stats = sessions.adapter.statistics
    original_result = sessions.adapter.result()
    sample = deepcopy(first.sample)
    sample.update(frame=first.sample['frame'] - 1, game_time=first.sample['game_time'] - 1)
    sample['rows'][0]['damage'] = 1
    backwards = replace(first, sequence=first.sequence + 1, sample=sample,
                        roster=replace(first.roster, frame=sample['frame']))
    with pytest.raises(FrameMismatch, match='new generation'):
        sessions.consume(backwards)
    assert sessions.adapter.statistics is original_stats
    assert sessions.adapter.result() == original_result
    assert original_stats.end_time is None
    assert sessions.sequence == first.sequence
    assert not sessions.pop_archives()
