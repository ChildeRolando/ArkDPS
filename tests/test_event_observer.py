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
        def unload(self):
            calls.append('unload')
            raise RuntimeError('script gone')

    class Session:
        def detach(self):
            calls.append('detach')

    observer = FridaEventObserver(collector(VersionStatus.COMPATIBLE), 'unused')
    observer.script, observer.session = Script(), Session()
    with pytest.raises(RuntimeError):
        observer.stop()
    assert calls == ['unload', 'detach']
    assert observer.status == ObserverStatus.STOPPED
