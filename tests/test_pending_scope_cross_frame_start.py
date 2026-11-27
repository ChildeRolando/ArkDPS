"""Late empty starts retain the original independent counter window."""
from copy import deepcopy
from dataclasses import asdict

from battle_lifecycle import BattleSessions
from capture_battles import observation_from_json
from test_pending_scope_start import pending_case


def crossframe_pending(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    case.advance(1.5, phase=5)
    sample = source.collector.process.sample
    mark = source.observer.mark
    captured = [False]

    def independent(**kwargs):
        case.advance(1.75)
        value = sample(**kwargs)
        captured[0] = True
        return value

    def clocked_mark():
        if captured[0]:
            case.advance(2)
        return mark()

    with monkeypatch.context() as capture:
        capture.setattr(source.collector.process, 'sample', independent)
        capture.setattr(source.observer, 'mark', clocked_mark)
        assert source.poll().kind == 'preparing'
    anchor = source.runtime.feed._pending_scope['anchor']
    assert anchor['before']['game_time'] == 1.5
    assert anchor['sample']['autochess_snapshot_anchor']['game_time'] == 1.75
    assert anchor['after']['game_time'] == anchor['sample']['game_time'] == 2
    assert anchor['sequence'] == 1 and source.commits == [0, 1]
    return case, deepcopy(anchor)


def test_crossframe_pending_start_consumes_first75_once_without_retiming(monkeypatch):
    case, anchor = crossframe_pending(monkeypatch)
    source = case.fixture
    case.advance(2.5, amount=75)
    released = source.poll()
    assert released.kind == 'sample'
    assert released.events['pending_scope_start']['anchor'] == anchor
    assert source.runtime.feed._pending_scope is None
    assert source.runtime.sessions.capture.baseline_sequence == 1
    assert source.commits == [0, 1, 2] and source.queue == []
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    replay = BattleSessions(released.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == source.runtime.sessions.multiplayer.result()


def test_crossframe_empty_scope_can_close_normally_and_next_scope_progresses(monkeypatch):
    case, anchor = crossframe_pending(monkeypatch)
    source = case.fixture
    case.advance(2.25)
    assert source.poll().kind == 'preparing'
    case.advance(2.5, phase=7)
    ending = source.poll()
    assert ending.events['pending_scope_start']['anchor'] == anchor
    assert source.runtime.feed._pending_scope is None
    assert source.commits == [0, 1]
    case.advance(3, phase=4, next_round=True)
    assert source.poll().kind == 'sample'
    case.advance(3.5, amount=75)
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
