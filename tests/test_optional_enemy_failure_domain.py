"""Enemy feature faults cannot revoke admitted samples or hide source failures."""
import errno
import json
from threading import Lock
from types import SimpleNamespace

import pytest

import collection_session as session_module
from collection_session import CollectionSession
from collector import BattleChanged, ProcessChanged, UnsupportedVersion
from event_capture import CaptureRetry, LiveEventCapture
from event_observer import ObserverReadRetry, ObserverStatus
from event_probe import StreamGap
from native_pc.native_hook_source import NativePipe
from test_sampling_state_machine_recovery import transport_world


def owned_session():
    source, probe, _, _ = transport_world()
    request = probe._pipe.request
    pipe = NativePipe.__new__(NativePipe)
    pipe.handle, pipe._poisoned, pipe.request = 1, False, request
    probe._pipe = pipe
    probe._owned_process_instance = tuple(source.collector.process.process_instance)
    source.collector.close = lambda: None
    session = CollectionSession(None, None, collector=source.collector)
    session.enable_lifecycle(source.runtime)
    session.observer = probe
    session.capture = LiveEventCapture(source.collector, probe)
    session.event_status = 'ready'
    return source, probe, pipe, session


def clock(monkeypatch):
    value = [0.]
    monkeypatch.setattr(session_module, 'time', SimpleNamespace(monotonic=lambda: value[0]))
    return value


def fault_target(monkeypatch, source, where, callback):
    if where == 'read':
        original = source.collector.read_enemies
        monkeypatch.setattr(source.collector, 'read_enemies', callback(original))
    else:
        original = session_module.defense_enemy_projection
        monkeypatch.setattr(session_module, 'defense_enemy_projection', callback(original))


@pytest.mark.parametrize('where', ['read', 'projection'])
@pytest.mark.parametrize('kind', [ValueError, CaptureRetry, ObserverReadRetry, StreamGap, RecursionError])
def test_local_fault_does_not_undo_ack_and_recovers_next_due(monkeypatch, where, kind):
    source, probe, pipe, session = owned_session()
    now = clock(monkeypatch)
    session.read()
    attempts = []
    def callback(original):
        def run(*args, **kwargs):
            attempts.append(now[0])
            if len(attempts) == 1:
                raise kind('owned feature-local failure')
            return original(*args, **kwargs)
        return run
    fault_target(monkeypatch, source, where, callback)
    source.advance(1, [source.scoped_hit(1, 3, 75)])
    failed = session.read()
    assert failed['enemy']['status'] == 'unavailable'
    assert failed['captured_monotonic'] == source.runtime.sessions.latest_sample['captured_monotonic']
    assert source.commits == [0, 1] and source.queue == []
    now[0] = .2
    source.advance(2)
    waiting = session.read()
    assert waiting['enemy']['status'] == 'unavailable' and len(attempts) == 1
    now[0] = 1.1
    source.advance(3)
    session.read()
    assert len(attempts) == 2
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert session.observer is probe and probe.status == ObserverStatus.READY
    assert pipe.handle == 1 and not pipe._poisoned


@pytest.mark.parametrize('where', ['read', 'projection'])
def test_persistent_feature_failure_has_cooldown_and_new_scope_clears_it(monkeypatch, where):
    source, probe, pipe, session = owned_session()
    now = clock(monkeypatch)
    attempts = []
    failing = [True]
    def callback(original):
        def run(*args, **kwargs):
            attempts.append(now[0])
            if failing[0]:
                raise ValueError('owned persistent optional failure')
            return original(*args, **kwargs)
        return run
    fault_target(monkeypatch, source, where, callback)
    for i, at in enumerate((0., .2, .4, .8, 1., 1.2, 1.8, 2.)):
        now[0] = at
        source.advance(i, [source.scoped_hit(1, 3, 75)] if i == 1 else ())
        sample = session.read()
        assert sample['enemy']['status'] == 'unavailable'
    assert attempts == [0., 1., 2.]
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    failing[0] = False
    now[0] = 2.1
    source.native_round += 1
    source.autochess_epoch += 1
    source.advance(8)
    session.read()
    assert attempts == [0., 1., 2., 2.1]
    assert session._enemy_projection_retry_at == 0.
    source.advance(9, [source.scoped_hit(2, 3, 75)])
    session.read()
    source.advance(10)
    session.read()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert source.queue == [] and source.commits[-1] == 2
    assert session.observer is probe and probe.status == ObserverStatus.READY


def exhausted_windows():
    result = OSError('owned resource exhaustion')
    result.winerror = 8
    return result


@pytest.mark.parametrize('where', ['read', 'projection'])
@pytest.mark.parametrize('error', [MemoryError('owned'), SystemError('owned'), InterruptedError('owned'),
    ProcessChanged('owned'), BattleChanged('owned'), UnsupportedVersion('owned'),
    OSError(errno.ENOMEM, 'owned'), exhausted_windows(), KeyboardInterrupt()])
def test_resource_lifecycle_and_cancellation_signals_escape(monkeypatch, where, error):
    source, probe, pipe, session = owned_session()
    session.read()
    source.advance(1, [source.scoped_hit(1, 3, 75)])
    def callback(original):
        def run(*args, **kwargs):
            raise error
        return run
    fault_target(monkeypatch, source, where, callback)
    with pytest.raises(type(error)):
        session.read()


def test_core_native_error_stays_failed_and_cannot_enter_optional_guard():
    source, probe, pipe, session = owned_session()
    session.read()
    source.advance(1, [source.scoped_hit(1, 3, 75)])
    pipe._lock, pipe._id = Lock(), 0
    pipe._buffer = bytearray((json.dumps({'id': 1, 'error': 'native_replay_gap code=6'}) + '\n').encode())
    pipe._io = lambda operation, buffer, size, deadline: size
    del pipe.request
    with pytest.raises(RuntimeError):
        session.read()
    assert pipe._poisoned and probe.status == ObserverStatus.FAILED
    assert source.commits == [0] and len(source.queue) == 1


def test_core_process_change_does_not_enter_enemy_guard(monkeypatch):
    source, _, _, session = owned_session()
    session.read()
    def changed(**kwargs):
        raise ProcessChanged('owned core process boundary')
    monkeypatch.setattr(source.collector, 'read_lifecycle', changed)
    monkeypatch.setattr(session, '_attach_enemy_guarded',
                        lambda _: pytest.fail('core failure cannot enter optional guard'))
    with pytest.raises(ProcessChanged):
        session.read()
    assert source.commits == [0]


def test_actual_worker_keeps_one_shot_source_after_optional_projection_fault(tmp_path, monkeypatch):
    import desktop_bridge as bridge
    import native_pc_collector
    from connection import ConnectionConfig

    source, probe, pipe, _ = owned_session()
    now = [0.]
    trace = []
    created = []
    fired = [False]
    monkeypatch.setattr(bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(bridge, 'time', SimpleNamespace(monotonic=lambda: now[0],
                                                     perf_counter=lambda: now[0]))
    monkeypatch.setattr(session_module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', lambda *a, **k: source.collector)
    worker = bridge.CollectionWorker(bridge.DesktopState(tmp_path / 'state'))
    monkeypatch.setattr(worker, '_pc_provider', lambda *a, **k: None)
    def wait(delay):
        now[0] += max(delay, .05)
        if now[0] > 12:
            worker.stop.set()
        return worker.stop.is_set()
    worker.stop.wait = wait
    def stop():
        trace.append('observer_stop')
        probe.status = ObserverStatus.STOPPED
        pipe.handle = None
    probe.stop = stop
    project = session_module.defense_enemy_projection
    def projection(value):
        if created and created[0].reads == 2 and not fired[0]:
            fired[0] = True
            raise ValueError('owned postcommit optional error')
        return project(value)
    monkeypatch.setattr(session_module, 'defense_enemy_projection', projection)

    class OwnedSession(CollectionSession):
        def __init__(self, *args, collector, audit, **kwargs):
            super().__init__(None, None, collector=collector, audit=audit, **kwargs)
            self.reads = 0
            created.append(self)
        def enable_lifecycle(self, runtime):
            super().enable_lifecycle(runtime)
            source.runtime = runtime
        def connect(self):
            trace.append('connect')
            assert probe.status == ObserverStatus.READY
            self.observer = probe
            self.event_status = 'ready'
            self.capture = LiveEventCapture(self.collector, probe, audit=self.audit)
        def read(self):
            self.reads += 1
            trace.append('read:' + str(self.reads))
            source.advance(self.reads - 1,
                           [source.scoped_hit(1, 3, 75)] if self.reads == 2 else ())
            result = super().read()
            if self.reads >= 3:
                worker.stop.set()
            return result

    monkeypatch.setattr(bridge, 'CollectionSession', OwnedSession)
    worker.request(ConnectionConfig(platform='native_pc', native_game_root='owned-offline'), True)
    worker.run()
    assert fired[0]
    assert trace.count('connect') == 1
    assert trace.index('read:3') < trace.index('observer_stop')
    notices = list(worker.events.queue)
    assert len([value for kind, value in notices if kind == 'sample']) == 3
    assert not any(kind in ('error', 'worker_error') for kind, _ in notices)
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
