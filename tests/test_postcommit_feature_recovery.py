"""Owned fault injection: optional owners recover without source replacement.

These are local robustness cases, not claims that a live payload raised them.
"""
import errno
import json
from threading import Lock
from types import SimpleNamespace

import pytest

import desktop_audit
import desktop_bridge as bridge
import native_pc_collector
from battle_history import BattleHistory, HistoryError
from collection_session import CollectionSession, HISTORY_NOTICE_FAILED
from collector import BattleChanged, ProcessChanged, UnsupportedVersion
from connection import ConnectionConfig
from event_capture import CaptureRetry, LiveEventCapture
from event_observer import ObserverStatus
from event_probe import ObserverReadRetry, StreamGap
from native_pc.native_hook_source import NativePipe
from test_battle_history import archive
from test_sampling_state_machine_recovery import transport_world


def owned_session(tmp_path, **kwargs):
    source, probe, _, _ = transport_world()
    request = probe._pipe.request
    pipe = NativePipe.__new__(NativePipe)
    pipe.handle, pipe._poisoned, pipe.request = 1, False, request
    probe._pipe = pipe
    probe._owned_process_instance = tuple(source.collector.process.process_instance)
    source.collector.close = lambda: None
    session = CollectionSession(None, None, collector=source.collector, **kwargs)
    session.enable_lifecycle(source.runtime)
    session.observer, session.event_status = probe, 'ready'
    session.capture = LiveEventCapture(source.collector, probe, audit=session.audit)
    return source, session, probe, pipe


def _raise(error):
    raise error


@pytest.mark.parametrize('error', [RuntimeError('private'), RecursionError('private'),
                                 CaptureRetry('private'), ObserverReadRetry('private'),
                                 StreamGap('private')])
def test_journal_local_format_failure_retries_and_resumes(tmp_path, monkeypatch, error):
    clock, warnings, calls = [0.], [], []
    marker = object()
    monkeypatch.setattr(desktop_audit, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def encoder(value):
        assert value is marker
        calls.append(clock[0])
        if len(calls) == 1:
            raise error
        return {'owned_value': 1}
    with desktop_audit.DesktopCaptureJournal(tmp_path, encoder=encoder, warn=warnings.append,
            header=lambda *a, **k: {'kind': 'fixture_header'}, debug_mode=True) as journal:
        journal.write('published', {'marker': marker})
        assert journal.retry_at == 10. and journal.stream is None
        clock[0] = 5.
        journal.write('published', {'marker': marker})
        assert calls == [0.]
        clock[0] = 11.
        journal.write('published', {'marker': marker})
        assert journal.retry_at == 0. and calls == [0., 11.]
    rows = [json.loads(line) for path in tmp_path.glob('*.jsonl')
            for line in path.read_text(encoding='utf-8').splitlines()]
    assert any(row.get('data', {}).get('marker') == {'owned_value': 1} for row in rows)
    assert len(warnings) == 1 and 'private' not in warnings[0]


def _windows_memory_error(code):
    error = OSError('windows memory')
    error.winerror = code
    return error


FATAL = [MemoryError(), SystemError(), InterruptedError(), BattleChanged('changed'),
         ProcessChanged('changed'), UnsupportedVersion('unsupported'),
         OSError(errno.ENOMEM, 'memory'), _windows_memory_error(8),
         _windows_memory_error(14), KeyboardInterrupt(), SystemExit()]


@pytest.mark.parametrize('error', FATAL)
def test_journal_resource_lifecycle_and_interrupt_signals_propagate(tmp_path, error):
    journal = desktop_audit.DesktopCaptureJournal(tmp_path, encoder=str, warn=pytest.fail,
                                                 debug_mode=True)
    journal.writer.write = lambda *a: _raise(error)
    with pytest.raises(type(error)) as raised:
        journal.write('published', {})
    assert raised.value is error and journal.retry_at == 0.


@pytest.mark.parametrize('debug', [False, True])
def test_history_notice_diagnostic_is_finite_in_both_log_modes(tmp_path, debug):
    with desktop_audit.DesktopCaptureJournal(tmp_path, encoder=str, warn=pytest.fail,
            header=lambda *a, **k: {'kind': 'fixture_header'}, debug_mode=debug) as journal:
        journal.write('history_notice_error', dict(code='saved_notice_failed', path='private', uid='private'))
        journal.write('history_notice_error', dict(code='private'))
    log, = tmp_path.glob('*.jsonl')
    rows = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 2
    assert rows[1]['kind'] == 'history_notice_error'
    assert {key: value for key, value in rows[1]['data'].items() if key != 'utc'} == {
        'code': 'saved_notice_failed'}
    assert 'private' not in log.read_text(encoding='utf-8')


def test_known_journal_oserror_keeps_existing_warning_path(tmp_path):
    warnings = []
    journal = desktop_audit.DesktopCaptureJournal(tmp_path, encoder=str, warn=warnings.append,
                                                 debug_mode=True)
    journal.writer.write = lambda *a: _raise(PermissionError('owned disk unavailable'))
    journal.write('published', {})
    assert len(warnings) == 1 and 'owned disk unavailable' in warnings[0]
    assert journal.retry_at > 0.


@pytest.mark.parametrize('secondary', ['close', 'warning'])
def test_journal_error_reporter_failure_preserves_retry_and_stream_ownership(tmp_path, monkeypatch, secondary):
    clock, warnings = [0.], []
    monkeypatch.setattr(desktop_audit, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    def warn(message):
        if secondary == 'warning' and not warnings:
            warnings.append('injected failure')
            raise RuntimeError('optional warning callback failed')
        warnings.append(message)
    journal = desktop_audit.DesktopCaptureJournal(tmp_path, encoder=str, warn=warn,
                                                 debug_mode=True)
    journal.write('sample', {})
    stream = journal.stream
    if secondary == 'close':
        class OnceFailingClose:
            attempts = 0
            def close(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError('optional stream close failed')
                stream.close()
        journal.stream = OnceFailingClose()
    retained = journal.stream
    journal.writer.write = lambda *a: _raise(RuntimeError('optional format failed'))
    journal.write('published', {})
    assert journal.retry_at == 10.
    if secondary == 'close':
        assert journal.stream is retained and not stream.closed
    clock[0] = 11.
    journal.write('sample', {'recovered': True})
    assert journal.retry_at == 0.
    journal._close_segment()
    assert stream.closed
    rows = [json.loads(line) for path in tmp_path.glob('*.jsonl')
            for line in path.read_text(encoding='utf-8').splitlines()]
    assert any(row.get('data', {}).get('recovered') is True for row in rows)


def test_journal_warning_resource_failure_still_propagates(tmp_path):
    error = MemoryError()
    journal = desktop_audit.DesktopCaptureJournal(tmp_path, encoder=str,
        warn=lambda message: _raise(error), debug_mode=True)
    journal.writer.write = lambda *a: _raise(RuntimeError('format failure'))
    with pytest.raises(MemoryError) as raised:
        journal.write('published', {})
    assert raised.value is error


def test_journal_enter_local_failure_uses_existing_deadline_and_recovers(tmp_path, monkeypatch):
    clock, warnings = [0.], []
    monkeypatch.setattr(desktop_audit, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    journal = desktop_audit.DesktopCaptureJournal(tmp_path, encoder=str, warn=warnings.append,
                                                 debug_mode=True)
    make_room = journal._make_room
    attempts = []
    def once(required):
        attempts.append(required)
        if len(attempts) == 1:
            raise RuntimeError('private optional directory failure')
        return make_room(required)
    journal._make_room = once
    with journal:
        assert journal.retry_at == 10.
        journal.write('sample', {'frame': 1})
        assert len(attempts) == 1
        clock[0] = 11.
        journal.write('sample', {'frame': 2})
        assert journal.retry_at == 0.
    assert len(warnings) == 1 and 'private' not in warnings[0]
    assert any('"frame":2' in path.read_text(encoding='utf-8') for path in tmp_path.glob('*.jsonl'))


def test_journal_exit_local_cleanup_failure_does_not_replace_body_failure(tmp_path):
    warnings = []
    journal = desktop_audit.DesktopCaptureJournal(tmp_path, encoder=str, warn=warnings.append,
                                                 debug_mode=True)
    core_error = StreamGap('core proof failure')
    with pytest.raises(StreamGap) as raised:
        with journal:
            journal.write('sample', {})
            stream = journal.stream
            class OnceFailingClose:
                attempts = 0
                def close(self):
                    self.attempts += 1
                    if self.attempts == 1:
                        raise RuntimeError('private cleanup failure')
                    stream.close()
            retained = journal.stream = OnceFailingClose()
            raise core_error
    assert raised.value is core_error
    assert journal.stream is retained and not stream.closed
    assert len(warnings) == 1 and 'private' not in warnings[0]
    journal._close_segment()
    assert stream.closed and journal.stream is None


@pytest.mark.parametrize('edge', ['enter', 'exit'])
def test_journal_enter_exit_resource_failure_propagates(tmp_path, edge):
    error = MemoryError()
    journal = desktop_audit.DesktopCaptureJournal(tmp_path, encoder=str, warn=pytest.fail)
    if edge == 'enter':
        journal._make_room = lambda *a: _raise(error)
        call = journal.__enter__
    else:
        journal._close_segment = lambda: _raise(error)
        call = journal.__exit__
    with pytest.raises(MemoryError) as raised:
        call()
    assert raised.value is error


@pytest.mark.parametrize('callback', ['result', 'legacy'])
@pytest.mark.parametrize('error', [RuntimeError('private'), CaptureRetry('private'),
                                 ObserverReadRetry('private'), StreamGap('private'),
                                 RecursionError('private')])
def test_saved_history_notice_failure_does_not_resave_or_revoke_ack(tmp_path, callback, error):
    events, notices = [], []
    state = bridge.DesktopState(tmp_path / 'state')
    def result_notice(result):
        if callback == 'result':
            raise error
        notices.append(result)
    def legacy_notice():
        if callback == 'legacy':
            raise error
    source, session, probe, pipe = owned_session(tmp_path, history=state.history,
        on_history_result=result_notice, on_history=legacy_notice,
        audit=lambda kind, data: events.append((kind, data)))
    source.advance(0)
    session.read()
    target = archive()
    source.runtime.sessions._archives.append(target)
    source.advance(1, [source.scoped_hit(1, 3, 75)])
    session.read()
    saved_bytes = state.history.path_for(target['battle_id']).read_bytes()
    assert not session.runtime.pending_history
    assert session.history_error == HISTORY_NOTICE_FAILED
    assert ('history_notice_error', {'code': 'saved_notice_failed'}) in events
    assert source.commits == [0, 1]
    assert probe.status is ObserverStatus.READY and pipe.handle == 1 and not pipe._poisoned
    source.advance(2)
    session.read()
    assert source.commits == [0, 1, 1]
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert session.history_error is None
    assert state.history.path_for(target['battle_id']).read_bytes() == saved_bytes
    assert sum(kind == 'history_saved' for kind, _ in events) == 1
    if callback == 'result':
        assert not notices and target['battle_id'] not in state.records
    state.command({'version': 1, 'op': 'history_refresh'})
    assert target['battle_id'] in state.records


@pytest.mark.parametrize('error', FATAL)
def test_saved_history_notice_fatal_signal_propagates_after_single_save(tmp_path, error):
    history = BattleHistory(tmp_path / 'history')
    source, session, _, _ = owned_session(tmp_path, history=history,
                                          on_history_result=lambda result: _raise(error))
    target = archive()
    source.runtime.sessions._archives.append(target)
    with pytest.raises(type(error)) as raised:
        session._drain_archives()
    assert raised.value is error
    assert history.get(target['battle_id']).records and not session.runtime.pending_history


def test_real_history_save_failure_retains_retry_and_overrides_notice_warning(tmp_path, monkeypatch):
    history = BattleHistory(tmp_path / 'history')
    _, session, _, _ = owned_session(tmp_path, history=history,
        on_history_result=lambda result: _raise(RuntimeError('notice')))
    first, second = archive(), archive()
    session.runtime.sessions._archives.extend([first, second])
    save = history.save
    def fail_second(record):
        if record.battle_id == second['battle_id']:
            raise HistoryError('real pending write failure')
        return save(record)
    monkeypatch.setattr(history, 'save', fail_second)
    session._drain_archives()
    assert session.history_error == 'real pending write failure'
    assert [item['battle_id'] for item in session.runtime.pending_history] == [second['battle_id']]
    monkeypatch.setattr(history, 'save', save)
    session.on_history_result = None
    session._drain_archives()
    assert not session.runtime.pending_history and session.history_error is None


@pytest.mark.parametrize('fault', ['journal', 'history_notice'])
def test_actual_worker_keeps_owned_source_and_reaches_next_legal_poll(tmp_path, monkeypatch, fault):
    source, _, probe, pipe = owned_session(tmp_path)
    trace, clock, fired, instances = [], [0.], [False], []
    monkeypatch.setattr(bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(bridge, 'time', SimpleNamespace(monotonic=lambda: clock[0],
                                                      perf_counter=lambda: clock[0]))
    state = bridge.DesktopState(tmp_path / 'state')
    worker = bridge.CollectionWorker(state)
    worker.set_debug_mode(True)
    monkeypatch.setattr(worker, '_pc_provider', lambda *a, **k: None)
    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', lambda *a, **k: source.collector)
    def wait(delay):
        clock[0] += max(delay, .05)
        if clock[0] > 12:
            worker.stop.set()
        return worker.stop.is_set()
    worker.stop.wait = wait
    def stop():
        trace.append(('stop', len([item for item in trace if item[0] == 'read'])))
        probe.status, pipe.handle = ObserverStatus.STOPPED, None
    probe.stop = stop
    emit = desktop_audit.DesktopCaptureJournal._emit
    def fail_format(journal, record):
        if fault == 'journal' and instances and instances[0].reads == 2 and not fired[0] and record['kind'] == 'published':
            fired[0] = True
            raise RuntimeError('injected optional format failure')
        return emit(journal, record)
    monkeypatch.setattr(desktop_audit.DesktopCaptureJournal, '_emit', fail_format)
    class OwnedSession(CollectionSession):
        def __init__(self, *args, collector, **kwargs):
            self.reads = 0
            super().__init__(None, None, collector=collector, **kwargs)
            instances.append(self)
            original_notice = self.on_history_result
            def notice(result):
                if fault == 'history_notice' and self.reads == 2 and not fired[0]:
                    fired[0] = True
                    raise RuntimeError('injected already-saved notice failure')
                original_notice(result)
            self.on_history_result = notice
        def enable_lifecycle(self, runtime):
            super().enable_lifecycle(runtime)
            source.runtime = runtime
        def connect(self):
            assert probe.status is ObserverStatus.READY
            self.observer, self.event_status = probe, 'ready'
            self.capture = LiveEventCapture(self.collector, probe, audit=self.audit)
        def read(self):
            self.reads += 1
            trace.append(('read', self.reads))
            source.advance(self.reads - 1, [source.scoped_hit(1, 3, 75)] if self.reads == 2 else ())
            if fault == 'history_notice' and self.reads == 2:
                source.runtime.sessions._archives.append(archive())
            result = super().read()
            if self.reads == 3:
                worker.stop.set()
            return result
    monkeypatch.setattr(bridge, 'CollectionSession', OwnedSession)
    worker.request(ConnectionConfig(platform='native_pc', native_game_root='owned-offline'), True)
    worker.run()
    events = list(worker.events.queue)
    assert fired[0] and len(instances) == 1 and ('read', 3) in trace, (trace, events)
    assert all(count >= 3 for op, count in trace if op == 'stop')
    assert source.commits == [0, 1, 1]
    assert not any(kind in ('error', 'worker_error') for kind, _ in events)
    assert any(kind == 'warning' for kind, _ in events)
    if fault == 'history_notice':
        assert ('warning', HISTORY_NOTICE_FAILED) in events


def test_real_binding_rejection_remains_outside_optional_guards(tmp_path):
    events = []
    source, session, probe, pipe = owned_session(tmp_path,
        audit=lambda kind, data: events.append((kind, data)))
    source.advance(0)
    session.read()
    source.advance(1, [source.scoped_hit(1, 3, 75)])
    request = pipe.request
    def malformed(op, **kwargs):
        raw = request(op, **kwargs)
        raw['native_publication']['sequence'] += 1  # Contradicts actual mark.
        return raw
    pipe.request = malformed
    with pytest.raises(CaptureRetry, match='scope_unavailable'):
        session.read()
    assert source.commits == [0] and len(source.queue) == 1
    assert not any(kind == 'history_notice_error' for kind, _ in events)
    pipe.request = request
    source.advance(2)
    session.read()
    assert source.commits == [0, 1] and len(source.queue) == 0
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75


@pytest.mark.parametrize('code', [4, 6, 9])
def test_real_native_pipe_failure_is_not_a_feature_failure(code):
    from test_host_gap_source_ownership import source
    session, probe, pipe = source()
    pipe._lock, pipe._id = Lock(), 0
    pipe._buffer = bytearray((json.dumps({'id': 1, 'error': f'native capture failed: code={code}'}) + '\n').encode())
    pipe._io = lambda operation, buffer, size, deadline: size
    session.runtime = SimpleNamespace(poll=lambda *a, **k: probe.mark(),
        sessions=SimpleNamespace(pop_archives=lambda: []))
    with pytest.raises(RuntimeError):
        session._read_lifecycle()
    assert pipe._poisoned and probe.status is ObserverStatus.FAILED
    assert not session.preserve_host_stream_gap(StreamGap('host wrapper'))
