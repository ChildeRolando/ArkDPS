"""Bound reply admission without dropping accepted samples or archive commits."""
from copy import deepcopy
from dataclasses import replace
import io
import json
import queue
import time
from types import SimpleNamespace

import pytest

from battle_history import BattleHistory, HistoryDiagnostic, HistoryResult
from collection_session import CollectionSession
from desktop_bridge import (BRIDGE_EVENT_LIMIT, DesktopState, _drain_worker_events, serve)
from sample_data import sample
from test_battle_history import record
from test_desktop_native_autochess_projection import populated


def worker_with(*events):
    worker = SimpleNamespace(events=queue.Queue(), close=lambda: None)
    for event in events:
        worker.events.put(event)
    return worker


def test_reply_drain_finishes_with_a_producer_that_never_leaves_queue_empty():
    observed = []

    class ContinuousQueue:
        count = 0
        def get_nowait(self):
            self.count += 1
            assert self.count <= BRIDGE_EVENT_LIMIT
            return 'connection', str(self.count)

    worker = SimpleNamespace(events=ContinuousQueue())
    state = SimpleNamespace(accept=lambda kind, value: observed.append((kind, value)))
    _drain_worker_events(worker, state)
    assert observed == [('connection', str(i)) for i in range(1, BRIDGE_EVENT_LIMIT + 1)]


def test_budget_stops_admitting_updates_but_finishes_each_started_update(monkeypatch):
    import desktop_bridge
    clock = [0.]
    observed = []
    monkeypatch.setattr(desktop_bridge.time, 'perf_counter', lambda: clock[0])
    worker = worker_with(*[('connection', i) for i in range(10)])
    def accept(kind, value):
        observed.append(value)
        clock[0] += .01
    _drain_worker_events(worker, SimpleNamespace(accept=accept))
    assert observed == [0, 1, 2] and worker.events.qsize() == 7
    assert worker.events.get_nowait() == ('connection', 3)


def test_regular_five_hz_source_catches_up_after_a_finite_burst_without_coalescing(tmp_path, monkeypatch):
    import desktop_bridge
    monkeypatch.setattr(desktop_bridge, 'BRIDGE_EVENT_LIMIT', 32)
    state = DesktopState(tmp_path)
    seen = []
    update = state.update_live_analysis
    def analysis(value):
        seen.append(value['frame'])
        update(value)
    monkeypatch.setattr(state, 'update_live_analysis', analysis)
    worker = worker_with()
    values = []
    for index in range(101):
        value = sample()
        value['frame'] = index
        values.append(value)
    # Fifty accepted reads accumulated while a caller was busy. Each normal
    # polling turn then has exactly one additional 5Hz lifecycle/sample pair.
    for value in values[:50]:
        worker.events.put(('lifecycle', 'playing'))
        worker.events.put(('sample', value))
    next_frame = 50
    polls = 0
    while not worker.events.empty():
        worker.events.put(('lifecycle', 'playing'))
        worker.events.put(('sample', values[next_frame]))
        next_frame += 1
        _drain_worker_events(worker, state)
        polls += 1
        assert polls <= 12
    assert seen == list(range(next_frame))
    assert state.last is values[next_frame - 1]


def test_fifo_burst_preserves_each_analysis_peak_warning_and_stage_boundary(tmp_path, monkeypatch):
    import desktop_bridge
    monkeypatch.setattr(desktop_bridge, 'BRIDGE_EVENT_LIMIT', 3)
    state = DesktopState(tmp_path)
    seen = []
    original = state.update_live_analysis
    def analysis(value):
        seen.append((value['battle_id'], value['frame']))
        original(value)
    monkeypatch.setattr(state, 'update_live_analysis', analysis)
    values = []
    for index, rate in enumerate((1, 90, 2, 3), 1):
        value = sample()
        value.update(frame=index, game_time=20 + index)
        value['statistics'].update(game_time=20 + index, totals_complete=True)
        for row in value['statistics']['rows'].values():
            row.update(recent_damage=rate, recent_damage_window=(19 + index, 20 + index))
        values.append(value)
    events = [('lifecycle', 'playing'), ('sample', values[0]), ('warning', 'first-warning'),
              ('sample', values[1]), ('warning', 'second-warning'), ('sample', values[2]),
              ('sample', values[3]), ('lifecycle', 'preparing')]
    worker = worker_with(*events)
    _drain_worker_events(worker, state)
    assert seen == [('battle-a', 1)]
    _drain_worker_events(worker, state)
    assert seen == [('battle-a', i) for i in (1, 2, 3)]
    _drain_worker_events(worker, state)
    assert seen == [('battle-a', i) for i in (1, 2, 3, 4)]
    assert state.last is values[3]
    assert state.live_analysis['observed_peak_dps'] == 180
    assert state.warnings[-2:] == ['first-warning', 'second-warning']
    assert state.status()[0] == 'preparing' and '上一局' in state.status()[1]
    assert worker.events.empty()


def test_source_barrier_and_global_archive_survive_a_batch_boundary(tmp_path, monkeypatch):
    import desktop_bridge
    monkeypatch.setattr(desktop_bridge, 'BRIDGE_EVENT_LIMIT', 2)
    state = DesktopState(tmp_path)
    state.expect_connection_request(state.connection, 3)
    archive = record(2)
    committed = state.history.save(archive)
    monkeypatch.setattr(state.history, 'list_recent', lambda: pytest.fail('hot history notification read disk'))
    old, current = sample(), sample()
    current.update(battle_id='new-source', session='new-session', frame=900)
    events = [('connection_source', dict(revision=2, platform=state.connection.platform)),
              ('sample', old), ('history', committed), ('error', 'old transport'),
              ('connection_source', dict(revision=3, platform=state.connection.platform)),
              ('scanning', False), ('lifecycle', 'playing'), ('sample', current)]
    worker = worker_with(*events)
    _drain_worker_events(worker, state)
    assert state.last is None and state._awaiting_source
    _drain_worker_events(worker, state)
    assert archive.battle_id in state.records and state.error == '' and state.last is None
    _drain_worker_events(worker, state)
    assert not state._awaiting_source and state.last is None
    _drain_worker_events(worker, state)
    assert state.last is current and state.error == '' and state.lifecycle == 'playing'
    assert state.selected.battle_id == archive.battle_id


def test_old_samples_do_not_become_fresh_when_dequeued_and_real_errors_stay_distinct(tmp_path):
    state = DesktopState(tmp_path)
    value = sample()
    value['captured_monotonic'] = time.monotonic() - 5
    captured = value['captured_monotonic']
    worker = worker_with(('sample', value))
    output = io.StringIO()
    serve(io.StringIO('{"version":1,"id":1,"op":"hello"}\n'
                     '{"version":1,"id":2,"op":"snapshot"}\n'), output, state, worker)
    reply = json.loads(output.getvalue().splitlines()[-1])
    assert reply['ok'] is True and reply['state']['status'] == 'stale'
    assert reply['state']['error'] == '' and value['captured_monotonic'] == captured
    worker.events.put(('error', 'transport lost'))
    _drain_worker_events(worker, state)
    assert state.status()[0] == 'disconnected' and state.last is value
    fresh = sample()
    _drain_worker_events(worker_with(('sample', fresh)), state)
    assert state.error == '' and state.last is fresh and state.status()[0] != 'stale'


def test_broken_response_pipe_stops_serving_instead_of_reporting_a_successful_refresh(tmp_path):
    class BrokenPipe:
        def write(self, _value):
            raise OSError('controlled closed stdout pipe')
        def flush(self):
            pytest.fail('closed response pipe was flushed')
    state = DesktopState(tmp_path)
    value = sample()
    worker = worker_with(('sample', value))
    incoming = io.StringIO('{"version":1,"id":1,"op":"hello"}\n'
                           '{"version":1,"id":2,"op":"snapshot"}\n')
    serve(incoming, BrokenPipe(), state, worker)
    assert state.last is value  # Accepted host state is not rolled back by IO.
    assert '"snapshot"' in incoming.read()  # The dead transport has no next reply.


def test_replayed_reply_does_not_redrain_the_next_batch_or_repeat_a_commit(tmp_path, monkeypatch):
    import desktop_bridge
    monkeypatch.setattr(desktop_bridge, 'BRIDGE_EVENT_LIMIT', 1)
    state = DesktopState(tmp_path)
    accepted = []
    accept = state.accept
    def capture(kind, value):
        accepted.append(kind)
        accept(kind, value)
    monkeypatch.setattr(state, 'accept', capture)
    worker = worker_with(('warning', 'first'), ('warning', 'second'), ('warning', 'third'))
    output = io.StringIO()
    serve(io.StringIO('{"version":1,"id":1,"op":"hello"}\n'
                     '{"version":1,"id":2,"op":"snapshot"}\n'
                     '{"version":1,"id":2,"op":"snapshot"}\n'
                     '{"version":1,"id":3,"op":"snapshot"}\n'), output, state, worker)
    first, snapshot, replay, latest = output.getvalue().splitlines()
    assert snapshot == replay
    assert accepted == ['warning', 'warning', 'warning']
    assert json.loads(snapshot)['state']['warnings'][-2:] == ['first', 'second']
    assert json.loads(latest)['state']['warnings'][-3:] == ['first', 'second', 'third']


def test_committed_history_delta_matches_scan_without_rereading_or_changing_selection(tmp_path, monkeypatch):
    state = DesktopState(tmp_path)
    first = state.history.save(record(1))
    state.accept('history', first)
    selected = state.selected
    commits = [state.history.save(record(i)) for i in range(2, 6)]
    expected = DesktopState(tmp_path).snapshot()['history']
    monkeypatch.setattr(state.history, 'list_recent', lambda: pytest.fail('notification rescanned archive'))
    for result in commits:
        state.accept('history', result)
    assert state.snapshot()['history'] == expected
    assert state.selected is selected and state.snapshot()['history_selected']['battle_id'] == selected.battle_id
    assert all(result.records[0].battle_id in state.records for result in commits)


def test_existing_writer_scan_supplies_actual_catalog_including_external_changes(tmp_path, monkeypatch):
    state = DesktopState(tmp_path)
    first = state.history.save(record(1))
    state.accept('history', first)
    selected = state.selected
    external = BattleHistory(state.history.directory)
    external.save(record(2))
    # This external deletion must disappear from the list while the user's
    # selected immutable copy remains usable, just as with explicit refresh.
    external.path_for(record(1).battle_id).unlink()
    scans = []
    scan = state.history._scan
    def counted():
        scans.append(True)
        return scan()
    monkeypatch.setattr(state.history, '_scan', counted)
    committed = state.history.save(record(3))
    assert len(scans) == 1  # Existing retention scan; no second scan for notify.
    assert len(committed.records) == 1
    assert committed.records[0].battle_id == 'battle:3' and committed.written
    assert [row.battle_id for row in committed.recent] == ['battle:3', 'battle:2']
    monkeypatch.setattr(state.history, 'list_recent', lambda: pytest.fail('notification rescanned'))
    state.accept('history', committed)
    assert list(state.records) == ['battle:3', 'battle:2']
    assert len(scans) == 1 and state.selected is selected
    assert state.snapshot()['history_selected']['battle_id'] == 'battle:1'


def test_history_incremental_retention_and_diagnostics_keep_selected_evicted_copy(tmp_path):
    state = DesktopState(tmp_path)
    diagnostic = HistoryDiagnostic('prune_failed', 'kept.json', 'retained file')
    for index in range(105):
        state.accept('history', HistoryResult((record(index),), (), written=True))
    selected = state.selected
    assert len(state.records) == len(state.snapshot()['history']) == 100
    assert selected.battle_id not in state.records
    assert state.snapshot()['history_selected']['battle_id'] == selected.battle_id
    latest = record(200)
    state.accept('history', HistoryResult((latest,), (diagnostic,), written=True))
    assert latest.battle_id in state.records and len(state.records) == 100
    assert state.history_notice == 'prune_failed: retained file'
    before = deepcopy(state.snapshot()['history'])
    state.accept('history', HistoryResult((record(300),), (), written=False))
    assert state.snapshot()['history'] == before


def test_real_native_end8_archive_notifies_committed_record_once_and_keeps_preparing_history(tmp_path, monkeypatch):
    battle = populated()
    battle.native_phase, battle.autochess_epoch = 8, 2
    battle.advance(3, state=3)
    assert battle.poll().kind == 'terminal'
    battle.active = False
    assert battle.poll().kind == 'end'
    state = DesktopState(tmp_path)
    state.layout = replace(state.layout, multiplayer_enabled=True)
    notices, legacy = [], []
    session = CollectionSession(None, None, collector=SimpleNamespace(), history=state.history,
        on_history_result=notices.append, on_history=lambda: legacy.append(True))
    session.enable_lifecycle(battle.runtime)
    session._drain_archives()
    assert len(notices) == len(legacy) == 1 and notices[0].written
    record_value, = notices[0].records
    path = state.history.path_for(record_value.battle_id)
    raw_bytes = path.read_bytes()
    session._drain_archives()
    assert len(notices) == 1
    monkeypatch.setattr(state.history, 'list_recent', lambda: pytest.fail('notification did another scan'))
    _drain_worker_events(worker_with(('history', notices[0]), ('lifecycle', 'preparing')), state)
    history = state.snapshot()['history_selected']
    assert history['battle_id'] == record_value.battle_id
    assert history['view']['footer_total'] == 300
    assert [p['observed_damage'] for p in history['multiplayer']['players']] == [100, 200, 300, 400]
    assert record_value.statistics['rows'] == {} and path.read_bytes() == raw_bytes
    current = populated().runtime.display_sample()
    state.accept('sample', current)
    assert state.snapshot()['history_selected'] == history
