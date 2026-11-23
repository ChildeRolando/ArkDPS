"""Operator and automatic requests traverse the real worker loop."""
from types import SimpleNamespace
from threading import Event, Thread
from queue import Queue

import pytest

import capture_cli
import desktop_bridge
import native_pc_collector
from collector import SampleUnavailable
from connection import ConnectionConfig, ConnectionDiagnostics
from desktop_bridge import CollectionWorker, DesktopState


def run_sequence(tmp_path, monkeypatch, mode):
    clock = [0.0]
    trace, faults, owners, sessions = [], [], [], []
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(desktop_bridge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    worker = CollectionWorker(DesktopState(tmp_path / 'state'))
    config = ConnectionConfig(platform='native_pc', native_game_root='test-pc-client')

    def check(condition, message):
        if not condition:
            faults.append(message)
            raise AssertionError(message)

    class Startup:
        phase = 'starting'
        armed = 123
        retries = 0
        generation = 1

        def start(self):
            self.phase = ('starting' if mode == 'pending_rescan' else
                          'waiting_for_process' if mode == 'armed_rescan' else
                          'ready' if mode in ('endpoint_recovery', 'healthy') else 'failed')
            trace.append(('start', self.armed))

        @property
        def is_pending(self):
            return self.phase in ('starting', 'waiting_for_process', 'authenticating')

        def describe(self):
            trace.append(('describe', self.phase))
            return {'status': self.phase, 'error': None, 'ready_generation': self.generation,
                    'armed_filetime': self.armed, 'thread_alive': True, 'code': None}

        def retry(self):
            trace.append(('retry', self.phase, self.armed))
            self.retries += 1
            if self.phase == 'failed':
                self.phase = 'ready'
                self.generation += 1

        def observer_factory(self, *_args):
            pytest.fail('The synthetic session forwards the real worker provider seam')

        def close(self):
            trace.append(('startup_close',))

    def startup(*_args, startup_trace):
        assert callable(startup_trace)
        owner = Startup()
        owners.append(owner)
        return owner

    monkeypatch.setattr(capture_cli, '_native_pc_frida_runtime', startup)

    class Stop:
        stopped = False
        steps = 0
        requested = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, delay):
            clock[0] += delay
            self.steps += 1
            check(self.steps < 200, 'Worker did not finish the finite request trace')
            if mode in ('pending_rescan', 'armed_rescan') and owners:
                if not self.requested:
                    self.requested = True
                    worker.request(config, True)
                elif owners[0].retries:
                    owners[0].phase = 'ready'
            return self.stopped

    worker.stop = Stop()

    class Collector:
        diagnostics = ConnectionDiagnostics(platform='native_pc', code='native_unavailable')

        def __init__(self, *_args, **_kwargs):
            check(not owners[0].is_pending, 'Snapshot collector opened while startup was pending')
            trace.append(('collector', owners[0].phase))

    class Session:
        cleanup_errors = {}

        def __init__(self, *_args, collector, observer_factory, **_kwargs):
            self.collector = collector
            self.number = len(sessions) + 1
            self.closes = 0
            self.event_status = 'ready' if owners[0].phase == 'ready' else 'failed'
            self.event_error = None if self.event_status == 'ready' else 'prerequisite missing'
            self.observer = (SimpleNamespace(status='ready', error=None)
                             if self.event_status == 'ready' else None)
            check(observer_factory == owners[0].observer_factory, 'Worker changed provider ownership')
            sessions.append(self)

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            trace.append(('connect', self.number))
            if mode == 'endpoint_recovery' and self.number == 1:
                self.event_status, self.event_error = 'failed', 'transport detached'
                self.observer = SimpleNamespace(status='failed', error='transport detached')
                owners[0].phase = 'authenticating'
            if mode == 'automatic_then_rescan' and self.number <= 2:
                check(owners[0].retries == 0, 'Automatic retry rechecked or replaced startup')
                if self.number == 2:
                    worker.request(config, True)
                raise SampleUnavailable('game module initializing')

        def read(self):
            check(not (mode == 'endpoint_recovery' and self.number == 1),
                  'Failed observer blocked bootstrap reauthentication without being released')
            if mode == 'cleanup_then_rescan' and self.number == 1:
                worker.request(config, True)
            else:
                worker.stop.set()
            return None

        def close(self):
            self.closes += 1
            trace.append(('session_close', self.number, self.closes))
            if mode == 'endpoint_recovery' and self.number == 1:
                owners[0].phase = 'ready'
                owners[0].generation += 1
            if mode == 'cleanup_then_rescan' and self.number == 1 and self.closes == 1:
                raise RuntimeError('old observer still owns handles')

    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', Collector)
    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(config, True)
    worker.run()
    assert not faults
    assert not any(kind == 'worker_error' for kind, _ in worker.events.queue)
    assert len(owners) == 1 and owners[0].retries == (0 if mode in ('endpoint_recovery', 'healthy') else 1)
    assert trace[-1] == ('startup_close',)
    return trace, worker


def test_automatic_same_revision_preserves_owner_but_operator_rescan_retries(tmp_path, monkeypatch):
    trace, _ = run_sequence(tmp_path, monkeypatch, 'automatic_then_rescan')
    assert trace.index(('connect', 2)) < trace.index(('retry', 'failed', 123))
    assert trace.index(('retry', 'failed', 123)) < trace.index(('connect', 3))


def test_explicit_retry_intent_survives_failed_session_release(tmp_path, monkeypatch):
    trace, worker = run_sequence(tmp_path, monkeypatch, 'cleanup_then_rescan')
    assert trace.index(('session_close', 1, 2)) < trace.index(('retry', 'failed', 123))
    assert worker.retired_errors and 'old observer still owns handles' in worker.retired_errors[0]


def test_rescan_during_startup_preserves_original_owner_and_arming_boundary(tmp_path, monkeypatch):
    trace, _ = run_sequence(tmp_path, monkeypatch, 'pending_rescan')
    assert ('retry', 'starting', 123) in trace
    assert trace.count(('start', 123)) == 1


def test_explicit_rescan_republishes_unchanged_owner_watch_after_source_marker(tmp_path, monkeypatch):
    trace, worker = run_sequence(tmp_path, monkeypatch, 'armed_rescan')
    events = list(worker.events.queue)
    marker = events.index(('connection_source', {'revision': 2, 'platform': 'native_pc'}))
    fresh = next(value for kind, value in events[marker + 1:] if kind == 'native_pc_bootstrap')
    assert fresh['status'] == 'waiting_for_process' and fresh['armed_filetime'] == 123
    assert sum(kind == 'native_pc_bootstrap' and value is not None
               and value['status'] == 'waiting_for_process' for kind, value in events) == 2
    assert trace.count(('start', 123)) == 1 and ('retry', 'waiting_for_process', 123) in trace


def test_failed_old_session_release_cannot_publish_retired_owner_as_new_source(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    clock = [0.0]
    monkeypatch.setattr(desktop_bridge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    worker = CollectionWorker(DesktopState(tmp_path / 'state'))
    owner = SimpleNamespace(
        describe=lambda: dict(status='waiting_for_process', armed_filetime=123, thread_alive=True),
        close=lambda: None)
    worker.pc_runtime, worker._pc_bootstrap_revision = owner, 1
    worker._last_pc_bootstrap = dict(status='starting', armed_filetime=None)

    class Session:
        closes = 0
        cleanup_errors = {}

        def close(self):
            self.closes += 1
            if self.closes == 1:
                raise OSError('old observer still owns handles')

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, delay):
            clock[0] += delay
            self.stopped = True
            return True

    worker.session, worker.stop = Session(), Stop()
    worker.request(ConnectionConfig(platform='native_pc', native_game_root='new-root'), True)
    # The outstanding request is newer than the retired owner's bound revision.
    worker._pc_bootstrap_revision = 0
    worker.run()
    events = list(worker.events.queue)
    assert any(kind == 'connection_source' for kind, _ in events)
    assert not any(kind == 'native_pc_bootstrap' and value is not None for kind, value in events)
    assert not any(kind == 'worker_error' for kind, _ in events)


def test_shutdown_rejects_new_operator_request_and_stale_automatic_retry(tmp_path):
    worker = CollectionWorker(DesktopState(tmp_path))
    worker.stop.set()
    with pytest.raises(RuntimeError, match='关闭'):
        worker.request(ConnectionConfig(platform='native_pc'), True)
    assert not worker._retry_connection(0)
    assert worker.requests.empty() and worker.pc_runtime is None


def test_transport_recovery_releases_old_session_before_attaching_new_generation(tmp_path, monkeypatch):
    trace, _ = run_sequence(tmp_path, monkeypatch, 'endpoint_recovery')
    assert trace.index(('session_close', 1, 1)) < trace.index(('connect', 2))


def test_usable_pc_session_keeps_existing_bootstrap_poll_cost(tmp_path, monkeypatch):
    trace, _ = run_sequence(tmp_path, monkeypatch, 'healthy')
    # One initial connection description and the existing observer-recovery
    # description. Startup telemetry adds no call to the usable sampling path.
    assert [step for step in trace if step[0] == 'describe'] == [('describe', 'ready')] * 2
    assert [step for step in trace if step[0] == 'collector'] == [('collector', 'ready')]


def test_unchanged_ready_bootstrap_does_not_spin_on_incompatible_probe(tmp_path):
    worker = CollectionWorker(DesktopState(tmp_path))
    worker.pc_runtime = SimpleNamespace(is_pending=False,
        describe=lambda: {'status': 'ready', 'ready_generation': 7})
    session = SimpleNamespace(event_status='failed', observer=None)
    assert not worker._pc_observer_recovery_needed(session, 7)
    assert worker._pc_observer_recovery_needed(session, 6)


@pytest.mark.parametrize('final_code', ['restart_required', 'native_permission_denied'])
def test_pending_bootstrap_changes_are_published_before_connection_retry(tmp_path, monkeypatch, final_code):
    clock = [0.0]
    publications, owners, calls = [], [], []
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(desktop_bridge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    worker = CollectionWorker(DesktopState(tmp_path / 'state'))

    class Events(Queue):
        def put(self, event, *args, **kwargs):
            if event[0] == 'native_pc_bootstrap' and event[1] is not None:
                publications.append((clock[0], dict(event[1])))
            return super().put(event, *args, **kwargs)

    class Startup:
        phase, code, armed = 'starting', None, None

        def start(self):
            calls.append('start')

        @property
        def is_pending(self):
            return self.phase in ('starting', 'waiting_for_process')

        def describe(self):
            return dict(status=self.phase, code=self.code, armed_filetime=self.armed,
                        thread_alive=True, error=self.code)

        def retry(self):
            pytest.fail('Telemetry must not request a startup retry')

        def observer_factory(self, *_args):
            pytest.fail('Telemetry must not create an observer')

        def close(self):
            calls.append('close')

    def factory(*_args, startup_trace):
        assert callable(startup_trace)
        owner = Startup()
        owners.append(owner)
        return owner

    class Stop:
        stopped, steps = False, 0

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, delay):
            clock[0] += delay
            self.steps += 1
            assert self.steps <= 4, 'Finite startup telemetry trace did not stop'
            if self.steps == 1:
                owners[0].phase, owners[0].armed = 'waiting_for_process', 123
            elif self.steps == 3:
                owners[0].phase = 'waiting_for_process' if final_code == 'restart_required' else 'failed'
                owners[0].code = final_code
            elif self.steps == 4:
                self.stopped = True
            return self.stopped

    def no_collector(*_args, **_kwargs):
        pytest.fail('Bootstrap telemetry must not construct a snapshot collector')

    worker.events, worker.stop = Events(), Stop()
    monkeypatch.setattr(capture_cli, '_native_pc_frida_runtime', factory)
    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', no_collector)
    worker.request(ConnectionConfig(platform='native_pc', native_game_root='test-client'), True)
    worker.run()
    assert not any(kind == 'worker_error' for kind, _ in worker.events.queue)
    assert [(item['status'], item['code'], item['armed_filetime']) for _, item in publications] == [
        ('starting', None, None), ('waiting_for_process', None, 123),
        ('waiting_for_process' if final_code == 'restart_required' else 'failed', final_code, 123)]
    assert [when for when, _ in publications] == pytest.approx([0, .2, .6])
    assert clock[0] < 5 and worker.last_connect_attempt == 0
    assert len(owners) == 1 and owners[0].armed == 123 and calls == ['start', 'close']
    assert worker.requests.empty()


def test_shutdown_winning_during_provider_construction_prevents_start_and_publication(tmp_path, monkeypatch):
    worker = CollectionWorker(DesktopState(tmp_path))
    entered, finish = Event(), Event()
    trace, errors = [], []

    class Startup:
        def start(self):
            trace.append('start')

        def close(self):
            trace.append('close_unstarted')

    def factory(*_args, startup_trace):
        assert callable(startup_trace)
        entered.set()
        assert finish.wait(2)
        return Startup()

    def provide():
        try:
            worker._pc_provider(ConnectionConfig(platform='native_pc', native_game_root='game'), True)
        except InterruptedError:
            trace.append('cancelled')
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(capture_cli, '_native_pc_frida_runtime', factory)
    worker.thread = Thread(target=provide)
    worker.thread.start()
    assert entered.wait(2)
    closer = Thread(target=worker.close)
    closer.start()
    assert worker.stop.wait(2)
    finish.set()
    closer.join(2)
    worker.thread.join(2)
    assert not closer.is_alive() and not worker.thread.is_alive() and not errors
    assert trace == ['close_unstarted', 'cancelled']
    assert worker.pc_runtime is None


def test_shutdown_cancels_inflight_startup_before_joining_collection_thread(tmp_path):
    worker = CollectionWorker(DesktopState(tmp_path))
    cancelled = Event()
    trace = []

    class Startup:
        def cancel(self):
            trace.append('cancel')
            cancelled.set()

        def close(self):
            trace.append('close')

    worker.pc_runtime = Startup()

    def collection():
        assert cancelled.wait(2)
        worker._close_pc_runtime()

    worker.thread = Thread(target=collection)
    worker.thread.start()
    worker.close()
    assert trace == ['cancel', 'close']
    assert worker.pc_runtime is None and not worker.thread.is_alive()


def test_shutdown_after_ready_before_session_admission_never_constructs_or_connects(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    worker = CollectionWorker(DesktopState(tmp_path / 'state'))
    ready, proceed = Event(), Event()
    trace, errors = [], []
    connect_session = worker._connect_session

    def delayed_admission(*args, **kwargs):
        ready.set()
        assert proceed.wait(2)
        return connect_session(*args, **kwargs)

    worker._connect_session = delayed_admission

    class Startup:
        is_pending = False

        def start(self):
            trace.append('startup')

        def describe(self):
            return {'status': 'ready', 'ready_generation': 1, 'error': None}

        def observer_factory(self, *_args):
            pytest.fail('No observer can start after shutdown won admission')

        def cancel(self):
            trace.append('cancel')

        def close(self):
            trace.append('startup_close')

    class Session:
        def __init__(self, *_args, **_kwargs):
            errors.append('Session constructed after shutdown won admission')

        def connect(self):
            errors.append('Connection started after shutdown won admission')

    monkeypatch.setattr(capture_cli, '_native_pc_frida_runtime', lambda *_args, **_kwargs: Startup())
    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(ConnectionConfig(platform='native_pc', native_game_root='test-client'), True)
    worker.thread.start()
    assert ready.wait(2)
    closer = Thread(target=worker.close)
    closer.start()
    assert worker.stop.wait(2)
    proceed.set()
    closer.join(2)
    assert not closer.is_alive() and not worker.thread.is_alive() and not errors
    assert trace == ['startup', 'cancel', 'startup_close']
    assert worker.session is worker.pc_runtime is None


def test_admitted_connection_does_not_hold_shutdown_gate_and_remains_owned_until_release(tmp_path, monkeypatch):
    worker = CollectionWorker(DesktopState(tmp_path))
    connecting, cancelled = Event(), Event()
    trace, errors = [], []

    class Startup:
        def cancel(self):
            trace.append('cancel')
            cancelled.set()

        def close(self):
            trace.append('startup_close')

    class Session:
        cleanup_errors = {}

        def __init__(self, *_args, **_kwargs):
            trace.append('construct')

        def enable_lifecycle(self, runtime):
            pass

        def connect(self):
            assert worker.session is self
            trace.append('connect')
            connecting.set()
            if not cancelled.wait(2):
                errors.append('Shutdown could not acquire gate to cancel in-flight connect')

        def close(self):
            trace.append('session_close')

    def collect():
        try:
            worker._connect_session(None, None, object())
        except Exception as exc:
            errors.append(str(exc))
        finally:
            worker.release()
            worker._close_pc_runtime()

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.pc_runtime = Startup()
    worker.thread = Thread(target=collect)
    worker.thread.start()
    assert connecting.wait(2)
    worker.close()
    assert not errors and not worker.thread.is_alive()
    assert trace == ['construct', 'connect', 'cancel', 'session_close', 'startup_close']
    assert worker.session is worker.pc_runtime is None
