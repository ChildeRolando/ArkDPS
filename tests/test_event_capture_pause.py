"""Temporary event controls keep owned transports and native snapshots alive."""
import json
from io import StringIO
from queue import Queue
from types import SimpleNamespace

import pytest

import desktop_bridge
from battle_history import BattleHistory
from collection_session import CollectionSession
from desktop_bridge import CollectionWorker, DesktopState, serve
from event_observer import ObserverReadRetry, ObserverStatus
from test_event_capture import event
from test_lifecycle_events import native_connection


def native_session(tmp_path, *, audit=None):
    runtime, collector, observer, snapshot, scene, events, calls, commits = native_connection()
    collector.diagnostics = SimpleNamespace(platform='native_pc', connection_ok=True)
    collector.connect = lambda **_: None
    collector.locate_lifecycle = lambda: None
    collector.close = lambda: calls.append('collector_close')
    collector.event_identity_only = False
    observer.start = lambda: calls.append('start')
    observer.stop = lambda: calls.append('stop')
    observer.status = ObserverStatus.READY
    observer.capture_pause_supported = True
    observer.capture_paused = False
    observer.capture_generation = 0
    controls = []

    def control(paused, revision):
        controls.append((paused, revision))
        if observer.capture_paused != paused:
            observer.capture_generation += 1
        observer.capture_paused = paused
        return dict(paused=paused, revision=revision, capture_generation=observer.capture_generation,
                    sequence=events.queue[-1]['sequence'] if events.queue else 0)

    observer.set_capture_paused = control
    history = BattleHistory(tmp_path / 'history')
    session = CollectionSession(None, None, collector=collector, frida_remote=None,
        observer_factory=lambda *_: observer, history=history, audit=audit)
    session.enable_lifecycle(runtime)
    session.connect()
    return session, collector, observer, snapshot, scene, events, calls, commits, controls, history


def test_pause_before_first_sample_keeps_native_snapshot_and_same_observer(tmp_path):
    session, collector, observer, snapshot, _, _, calls, _, controls, history = native_session(tmp_path)
    try:
        session.set_events_paused(True, 1)
        snapshot.damage = 120
        value = session.read()
        assert value['statistics']['rows']['char_a']['damage'] == 120
        assert value['statistics']['rows']['char_a']['shield_absorbed'] is None
        assert value['event_status'] == 'paused'
        assert calls == ['start', 'lifecycle', 'pair']
        assert session.observer is observer and observer.capture_paused
        assert controls == [(True, 1)] and not history.list_recent().records
    finally:
        session.close()
    assert calls.count('stop') == calls.count('collector_close') == 1


def test_same_native_battle_keeps_counters_and_archives_partial_event_fragments(tmp_path):
    session, _, observer, snapshot, _, events, calls, commits, _, history = native_session(tmp_path)
    try:
        first = session.read()
        snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
        events.queue = [event(1, frame=2, game_time=1., amount=20)]
        assert session.read()['statistics']['rows']['char_a']['observed_damage'] == 20
        session.set_events_paused(True, 1)
        mark_calls = calls.count('mark')
        snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2., 60.
        paused = session.read()
        snapshot.frame, snapshot.game_time, snapshot.damage = 4, 3., 90.
        assert session.read()['statistics']['rows']['char_a']['damage'] == 90
        assert paused['statistics']['rows']['char_a']['damage'] == 60
        assert paused['statistics']['rows']['char_a']['shield_absorbed'] is None
        assert calls.count('mark') == mark_calls and session.observer is observer
        session.set_events_paused(False, 2)
        resumed = session.read()
        snapshot.frame, snapshot.game_time, snapshot.damage = 5, 4., 100.
        events.queue.append(event(2, frame=5, game_time=4., amount=10))
        result = session.read()
        assert result['statistics']['rows']['char_a']['damage'] == 100
        assert result['statistics']['rows']['char_a']['observed_damage'] == 10
        assert result['statistics']['rows']['char_a']['event_coverage_complete'] is False
        assert result['battle_identity'] == first['battle_identity'] == resumed['battle_identity']
        records = history.list_recent().records
        assert len(records) == 2 and len({record.battle_id for record in records}) == 2
        assert {record.to_dict()['reason'] for record in records} == {'event_capture_paused', 'event_capture_resumed'}
        assert all(record.to_dict()['metadata']['capture_segment']['complete'] is False for record in records)
        assert 'stop' not in calls and commits[-1] == 2
    finally:
        session.close()


def test_pending_ack_settles_before_the_final_owned_tail_is_admitted(tmp_path):
    session, _, observer, snapshot, _, events, _, commits, controls, history = native_session(tmp_path)
    try:
        session.read()
        confirmed = False

        def acknowledge(sequence):
            commits.append(sequence)
            return confirmed

        observer.commit_events = acknowledge
        snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
        events.queue = [event(1, frame=2, game_time=1., amount=20)]
        session.read()
        assert session.runtime._pending_event_ack[1] == 1
        # The producer advanced; this second event was never admitted by statistics.
        snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2., 30.
        events.queue.append(event(2, frame=3, game_time=2., amount=10))
        with pytest.raises(ObserverReadRetry):
            session.set_events_paused(True, 1)
        assert not controls and not session.events_paused and 2 not in commits
        confirmed = True
        session.set_events_paused(True, 1)
        assert controls == [(True, 1)] and session.events_paused
        assert commits[-1] == 2 and session.runtime._pending_event_ack is None
        record, = history.list_recent().records
        assert record.statistics['rows']['char_a']['observed_damage'] == 30
    finally:
        session.close()


def test_history_failure_retains_the_confirmed_pause_and_pending_archive(tmp_path, monkeypatch):
    from battle_history import HistoryError
    session, _, _, _, _, _, _, _, _, history = native_session(tmp_path)
    try:
        session.read()
        monkeypatch.setattr(history, 'save', lambda _: (_ for _ in ()).throw(HistoryError('disk full')))
        session.set_events_paused(True, 1)
        assert session.events_paused and len(session.runtime.pending_history) == 1
        assert session.history_error == 'disk full'
        assert session.read()['event_status'] == 'paused'
    finally:
        session.close()


def test_superseded_pending_pause_still_cuts_a_gap_when_resume_returns_running(tmp_path):
    session, _, observer, snapshot, _, events, _, _, _, history = native_session(tmp_path)
    try:
        first = session.read()
        snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
        events.queue = [event(1, frame=2, game_time=1., amount=20)]
        session.read()

        def pending_pause(paused, revision):
            if paused:
                if observer.capture_generation == 0:
                    observer.capture_generation = 1
                    raise ObserverReadRetry('gate closed, callback still draining')
                observer.capture_paused = True
                return dict(paused=True, revision=revision, capture_generation=1, sequence=1)
            observer.capture_generation = 2
            observer.capture_paused = False
            return dict(paused=False, revision=revision, capture_generation=2, sequence=1)

        observer.set_capture_paused = pending_pause
        with pytest.raises(ObserverReadRetry):
            session.set_events_paused(True, 1)
        assert session.events_paused is False
        session.set_events_paused(False, 2)
        assert session.events_paused is False
        assert session.runtime.feed.id != first['battle_id'].split(':')[0]
        record, = history.list_recent().records
        assert record.to_dict()['reason'] == 'event_capture_paused'
        assert record.statistics['rows']['char_a']['event_coverage_complete'] is False
    finally:
        session.close()


def test_pause_tail_is_reduced_into_old_fragment_before_resume_baseline(tmp_path):
    session, _, observer, snapshot, _, events, _, commits, _, history = native_session(tmp_path)
    try:
        session.read()
        snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
        events.queue = [event(1, frame=2, game_time=1., amount=20)]
        session.read()
        snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2., 30.
        events.queue.append(event(2, frame=3, game_time=2., amount=10))
        session.set_events_paused(True, 1)
        assert commits[-1] == 2
        record, = history.list_recent().records
        assert record.statistics['rows']['char_a']['observed_damage'] == 30
        snapshot.frame, snapshot.game_time, snapshot.damage = 4, 3., 90.
        session.read()
        session.set_events_paused(False, 2)
        real_mark = observer.mark
        observer.mark = lambda: dict(real_mark(), frame=snapshot.frame + 1)
        with pytest.raises(desktop_bridge.CaptureRetry):
            session.read()
        assert commits[-1] == 2
        observer.mark = real_mark
        result = session.read()
        assert commits[-1] == 2
        assert result['statistics']['rows']['char_a']['observed_damage'] == 0
        assert result['statistics']['rows']['char_a']['event_coverage_complete'] is False
        snapshot.frame, snapshot.game_time, snapshot.damage = 5, 4., 100.
        events.queue.append(event(3, frame=5, game_time=4., amount=10))
        assert session.read()['statistics']['rows']['char_a']['observed_damage'] == 10
        assert commits[-1] == 3
    finally:
        session.close()


def test_worker_request_is_pending_until_collection_owner_gets_producer_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    state = DesktopState(tmp_path / 'state')
    worker = CollectionWorker(state)
    session, _, observer, _, _, _, _, _, controls, _ = native_session(tmp_path)
    worker.session = session
    try:
        worker._advance_event_pause()
        assert worker._event_capture_control['state'] == 'running'
        reply = worker.request_event_pause(True)
        assert reply['state'] == 'pausing' and reply['paused'] is False and not controls
        real_control = observer.set_capture_paused
        attempts = 0

        def pending(paused, revision):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ObserverReadRetry('native callback busy')
            return real_control(paused, revision)

        observer.set_capture_paused = pending
        assert worker._advance_event_pause() is True
        assert worker._event_capture_control['paused'] is False
        assert worker._advance_event_pause() is False
        assert worker._event_capture_control['state'] == 'paused'
        assert controls == [(True, 1)]
        assert state.layout.event_observation is True
    finally:
        session.close()


@pytest.mark.parametrize('revision', [0, -1, True, 2147483648])
def test_pause_revision_must_be_a_positive_int32(tmp_path, revision):
    session, *_ = native_session(tmp_path)
    try:
        with pytest.raises(ValueError):
            session.set_events_paused(True, revision)
    finally:
        session.close()


def test_stdio_command_returns_pending_runtime_state_without_saving_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    state = DesktopState(tmp_path)
    before = state.layout
    control = dict(supported=True, requested_paused=True, paused=False, state='pausing', revision=1,
                   error=None, reason='waiting')
    worker = SimpleNamespace(events=Queue(), request_event_pause=lambda paused: dict(control))
    output = StringIO()
    serve(StringIO('{"version":1,"id":1,"op":"hello"}\n'
                   '{"version":1,"id":2,"op":"event_pause","paused":true}\n'), output, state, worker)
    reply = json.loads(output.getvalue().splitlines()[-1])
    assert reply['ok'] and reply['state']['event_capture_control'] == control
    assert state.layout == before and not (tmp_path / 'layout.json').exists()
    state.accept('event_capture_control', dict(control, revision=0, state='running', paused=False))
    assert state.event_capture_control['state'] == 'pausing'


@pytest.mark.parametrize('pending_tail_ack', [False, True])
def test_complete_debug_journal_replays_pause_snapshot_resume_and_end(tmp_path, pending_tail_ack):
    from capture_battles import replay, replay_product
    from capture_cli import json_value
    from desktop_audit import DesktopAuditReader, DesktopCaptureJournal

    capture_dir = tmp_path / 'captures'
    with DesktopCaptureJournal(capture_dir, encoder=json_value, warn=pytest.fail,
            header=lambda *_args, **_kwargs: dict(kind='log_header', data=dict(debug_mode=True)),
            debug_mode=True) as journal:
        session, _, observer, snapshot, scene, events, _, _, _, _ = native_session(tmp_path, audit=journal.write)
        try:
            session.read()
            snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
            events.queue = [event(1, frame=2, game_time=1., amount=20)]
            session.read()
            if pending_tail_ack:
                snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2., 30.
                events.queue.append(event(2, frame=3, game_time=2., amount=10))
                real_commit = observer.commit_events
                observer.commit_events = lambda sequence: False
                with pytest.raises(ObserverReadRetry):
                    session.set_events_paused(True, 1)
                snapshot.frame, snapshot.game_time, snapshot.damage = 4, 3., 60.
                assert session.read()['statistics']['rows']['char_a']['damage'] == 60
                observer.commit_events = real_commit
            session.set_events_paused(True, 1)
            snapshot.frame, snapshot.game_time, snapshot.damage = 5, 4., 60.
            session.read()
            session.set_events_paused(False, 2)
            session.read()
            snapshot.frame, snapshot.game_time, snapshot.damage = 6, 5., 70.
            sequence = 3 if pending_tail_ack else 2
            events.queue.append(event(sequence, frame=6, game_time=5., amount=10))
            session.read()
            snapshot.frame, snapshot.game_time, scene.battle_state = 7, 6., 3
            session.read()
            scene.active = False
            with pytest.raises(desktop_bridge.CaptureRetry):
                session.read()
        finally:
            session.close()
    lines = next(capture_dir.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()
    product = replay_product(lines)
    assert product['archives'] == 3
    assert product['last_state']['statistics']['rows']['char_a']['damage'] == 70
    assert product['last_state']['statistics']['rows']['char_a']['observed_damage'] == 10
    reader = DesktopAuditReader()
    records = [reader.restore(json.loads(line)) for line in lines]
    raw = []; samples = archives = 0
    for record in records:
        kind, data = record['kind'], record['data']
        if kind == 'lifecycle_header':
            raw.append(dict(header=dict(feed=data['feed'])))
        elif kind == 'lifecycle_observation':
            samples += data['kind'] == 'sample'
            value = dict(observation=data)
            if data['kind'] == 'gap' and data['reason'] == 'capture_stopped':
                value['connection'] = 'disconnected'
            raw.append(value)
        elif kind == 'event_capture_segment':
            raw.append(dict(event_capture_segment=data))
        elif kind == 'archive_ready':
            archives += 1
            raw.append(dict(archive_ready=data))
        elif kind == 'lifecycle_summary':
            raw.append(dict(summary=dict(samples=samples, archives=archives, last_state=data)))
    path = tmp_path / 'observations.jsonl'
    path.write_text(''.join(json.dumps(record) + '\n' for record in raw), encoding='utf-8')
    assert replay(path) == product
    tampered = [json.loads(line) for line in lines]
    boundary = next(row for row in tampered if row['kind'] == 'event_capture_segment')
    boundary['data']['previous_feed'] = 'unrelated-observation-owner'
    with pytest.raises(ValueError, match='capture segment'):
        replay_product([json.dumps(row) for row in tampered])


def test_pending_control_does_not_stop_the_worker_snapshot_loop(tmp_path, monkeypatch):
    from test_native_pc_observer_worker import run_worker, trusted_factory
    attempts = []

    def pending(worker, *_args):
        attempts.append(True)
        return True

    monkeypatch.setattr(CollectionWorker, '_advance_event_pause', pending)
    _, _, records = run_worker(tmp_path, monkeypatch, factory=trusted_factory)
    assert attempts and any(kind == 'sample' for kind, _ in records)
