import sys
from types import SimpleNamespace

import pytest

from connection import VersionStatus
from event_observer import FridaEventObserver, ObserverReadRetry, ObserverStatus


def collector(status):
    return SimpleNamespace(diagnostics=SimpleNamespace(connection_ok=True, version_status=status))


def test_incompatible_version_never_attaches():
    observer = FridaEventObserver(collector(VersionStatus.INCOMPATIBLE), 'unused')
    with pytest.raises(RuntimeError):
        observer.start()
    assert observer.status == ObserverStatus.INCOMPATIBLE


@pytest.mark.parametrize('native_error, expected', [
    ('Process is not using emulation', '请重启设备端 Frida 服务后重新定位'),
    ('Unexpected realm failure', 'Unexpected realm failure'),
])
def test_emulated_attach_failure_keeps_native_realm_untouched(monkeypatch, native_error, expected):
    class NotSupportedError(Exception):
        pass

    realms = []

    def attach(pid, *, realm):
        realms.append((pid, realm))
        raise NotSupportedError(native_error)

    device = SimpleNamespace(attach=attach)
    manager = SimpleNamespace(add_remote_device=lambda remote: device)
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(
        __version__='17.26.1', get_device_manager=lambda: manager,
        NotSupportedError=NotSupportedError,
    ))
    active = collector(VersionStatus.COMPATIBLE)
    active.core = SimpleNamespace(mc=SimpleNamespace(pid=17561))
    observer = FridaEventObserver(active, '127.0.0.1:27043')
    with pytest.raises((NotSupportedError, RuntimeError), match=expected):
        observer.start()
    assert observer.status == ObserverStatus.FAILED
    assert expected in observer.error
    assert realms == [(17561, 'emulated')]
    assert observer.session is observer.script is None


def test_unload_failure_still_detaches_own_session():
    calls = []

    class Script:
        is_destroyed = False

        def unload(self):
            calls.append('unload')
            raise RuntimeError('script gone')

    class Session:
        is_detached = False

        def detach(self):
            calls.append('detach')

    observer = FridaEventObserver(collector(VersionStatus.COMPATIBLE), 'unused')
    observer.script, observer.session = Script(), Session()
    with pytest.raises(RuntimeError):
        observer.stop()
    assert calls == ['unload', 'detach']
    assert observer.status == ObserverStatus.STOPPED


@pytest.mark.parametrize('already_detached', [True, False])
def test_stop_after_target_exit_is_idempotent(monkeypatch, already_detached):
    class InvalidOperationError(Exception):
        pass

    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(InvalidOperationError=InvalidOperationError))
    observer = FridaEventObserver(collector(VersionStatus.COMPATIBLE), 'unused')
    calls = []

    class Script:
        is_destroyed = already_detached

        def unload(self):
            pytest.fail('destroyed script was unloaded')

        def stop(self):
            calls.append('stop')
            self.is_destroyed = True
            session.is_detached = True
            raise InvalidOperationError('script destroyed during stop')

        @property
        def exports_sync(self):
            return self

    class Session:
        is_detached = already_detached

        def detach(self):
            pytest.fail('detached session was detached again')

    session = Session()
    observer.script, observer.session = Script(), session
    observer.status = ObserverStatus.READY
    observer.stop()
    observer.stop()
    assert calls == ([] if already_detached else ['stop'])
    assert observer.script is observer.session is None
    assert observer.status == ObserverStatus.STOPPED


def test_read_and_ack_timeouts_reuse_the_committed_cursor():
    observer = FridaEventObserver(collector(VersionStatus.COMPATIBLE), 'unused')
    observer.status = ObserverStatus.READY
    observer.request_timeout = 0
    requests = []
    missed = {}

    def response(request, result):
        observer._message({'type': 'send', 'payload': {
            'kind': 'arkdps-event-response', 'id': request['id'], 'result': result,
        }}, None)

    def post(request):
        requests.append(request)
        operation = request['operation']
        if operation not in missed:
            missed[operation] = request
            return
        response(missed[operation], {'error': 'late response'})
        if operation == 'read':
            response(request, {'events': list(range(request['after'] + 1, request['through'] + 1))})
        else:
            response(request, {'acknowledged': request['sequence'], 'error': None})

    observer.script = SimpleNamespace(post=post, is_destroyed=False)
    with pytest.raises(ObserverReadRetry, match='response timed out'):
        observer.read_through(2)
    assert observer.read_through(3)['events'] == [1, 2, 3]
    observer.commit_events(3)
    assert observer.read_through(4)['events'] == [4]
    observer.commit_events(4)
    observer.commit_events(4)
    assert [(r['operation'], r.get('after'), r.get('through'), r.get('sequence')) for r in requests] == [
        ('read', 0, 2, None), ('read', 0, 3, None), ('ack', None, None, 3),
        ('read', 3, 4, None), ('ack', None, None, 4),
    ]
    assert observer.status == ObserverStatus.READY
