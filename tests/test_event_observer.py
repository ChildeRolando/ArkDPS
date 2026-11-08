import sys
from types import SimpleNamespace

import pytest

from connection import VersionStatus
from event_observer import FridaEventObserver, ObserverStatus


def collector(status):
    return SimpleNamespace(diagnostics=SimpleNamespace(connection_ok=True, version_status=status))


def test_incompatible_version_never_attaches():
    observer = FridaEventObserver(collector(VersionStatus.INCOMPATIBLE), 'unused')
    with pytest.raises(RuntimeError):
        observer.start()
    assert observer.status == ObserverStatus.INCOMPATIBLE


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
