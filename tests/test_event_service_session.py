"""Managed event service inside CollectionSession: endpoint, fallback, retry."""
from types import SimpleNamespace

import pytest

from collection_session import CollectionSession
from connection import VersionStatus
from event_service import FridaServerError, ServerDiagnostic


class Collector:
    def __init__(self):
        self.diagnostics = SimpleNamespace(connection_ok=True,
                                           version_status=VersionStatus.COMPATIBLE)
        self.core = SimpleNamespace(mc=SimpleNamespace(pid=123,
                                                       process_instance=('boot', 1)))
        self.forward_registry = SimpleNamespace(owned={})
        self.adb_path = 'adb.exe'
        self.serial = '127.0.0.1:16384'
        self.closed = False

    def connect(self, locate=False):
        assert locate is False

    def locate_lifecycle(self):
        pass

    def close(self):
        self.closed = True


class Observer:
    def __init__(self, collector, remote):
        self.remote = remote
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class Server:
    """Scripted manager: fail the first ``failures`` preparations."""
    def __init__(self, failures=0, code='no_root', message='设备未授予 root，无法启动终端 frida-server。'):
        self.failures = failures
        self.code = code
        self.message = message
        self.ensure_calls = 0
        self.stopped = False
        self.address = '127.0.0.1:28100'

    def ensure(self):
        self.ensure_calls += 1
        if self.ensure_calls <= self.failures:
            raise FridaServerError(self.code, self.message, hint='开启 root 后重试。',
                                   detail='uid=2000')
        return ServerDiagnostic(state='ready', code='', message='设备端事件服务已就绪。',
                                local_port=28100, remote_port=28100, pid=4242,
                                forward=True, steps={'start': 'ok', 'forward': 'ok'})

    def diagnostic(self):
        if self.ensure_calls <= self.failures:
            return ServerDiagnostic(state='failed', code=self.code, message=self.message,
                                    hint='开启 root 后重试。', steps={'root': 'failed'})
        return ServerDiagnostic(state='ready', message='设备端事件服务已就绪。',
                                local_port=28100, remote_port=28100, pid=4242,
                                forward=True, steps={'start': 'ok', 'forward': 'ok'})

    def stop(self):
        self.stopped = True


def session(server, *, remote='127.0.0.1:27043', manage=True, observer=Observer):
    calls, audit = [], []
    result = CollectionSession('adb.exe', '127.0.0.1:16384', collector=Collector(),
                               frida_remote=remote, manage_server=manage,
                               observer_factory=observer,
                               server_factory=lambda *_args, **_kwargs: server,
                               audit=lambda kind, data: audit.append((kind, data)))
    return result, calls, audit


def test_managed_server_endpoint_is_used_and_released_with_the_session():
    server = Server()
    current, _calls, audit = session(server)
    current.connect()
    assert current.event_status == 'ready'
    assert current.observer.remote == '127.0.0.1:28100'
    assert current.event_diagnostic['steps']['start'] == 'ok'
    assert any(kind == 'event_server_ready' for kind, _ in audit)
    current.close()
    assert server.stopped and current.collector.closed


def test_preparation_failure_keeps_native_fallback_and_actionable_code():
    server = Server(failures=1)
    current, _calls, audit = session(server)
    current.connect()
    assert current.event_status == 'failed'
    assert current.observer is None
    assert 'root' in current.event_error
    assert current.event_diagnostic['code'] == 'no_root'
    assert current.event_diagnostic['steps'] == {'root': 'failed'}
    assert current.capture is not None
    assert ('observer_error', {'reason': current.event_error, 'code': 'no_root',
                               'steps': {'root': 'failed'}}) in audit


def test_manual_retry_recovers_within_the_same_app_session():
    server = Server(failures=1)
    current, _calls, audit = session(server)
    current.connect()
    status, error, diagnostic = current.retry_events()
    assert (status, error) == ('ready', None)
    assert diagnostic['state'] == 'ready'
    assert current.observer is not None and current.observer.started
    assert server.ensure_calls == 2
    assert any(kind == 'observer_manual_retry' for kind, _ in audit)
    current.close()
    assert server.stopped


def test_manual_retry_does_not_remount_a_mid_battle_gap():
    server = Server()
    current, _calls, _audit = session(server)
    current.connect()
    current.event_status = 'gap'
    status, error, _diagnostic = current.retry_events()
    assert status == 'gap'
    assert '下一局' in error
    assert current.observer.started and not current.observer.stopped
    current.close()


def test_manual_retry_does_nothing_for_disabled_or_healthy_events():
    disabled, _calls, _audit = session(Server(), remote=None, manage=False)
    disabled.connect()
    assert disabled.retry_events()[0] == 'unavailable'

    healthy, _calls, _audit = session(Server())
    healthy.connect()
    observer = healthy.observer
    assert healthy.retry_events()[0] == 'ready'
    assert healthy.observer is observer
    healthy.close()


def test_retry_before_connect_is_rejected():
    current, _calls, _audit = session(Server())
    with pytest.raises(RuntimeError):
        current.retry_events()
