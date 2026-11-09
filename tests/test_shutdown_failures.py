"""Cleanup reports must use operation outcomes, not discarded handles."""
from types import SimpleNamespace

import pytest

from app import App
from collection_session import CollectionSession
from connection import VersionStatus
from event_observer import FridaEventObserver


@pytest.mark.parametrize('failures', [{'script'}, {'session'}, {'script', 'session'}])
def test_failed_observer_release_is_reported_and_native_cleanup_still_runs(failures):
    failures = set(failures)
    calls = []

    class Script:
        is_destroyed = False

        def unload(self):
            calls.append('unload')
            if 'script' in failures:
                raise RuntimeError('unload refused')
            self.is_destroyed = True

    class Session:
        is_detached = False

        def detach(self):
            calls.append('detach')
            if 'session' in failures:
                raise RuntimeError('detach refused')
            self.is_detached = True

    collector = SimpleNamespace(
        diagnostics=SimpleNamespace(connection_ok=True, version_status=VersionStatus.COMPATIBLE),
        forward_registry=SimpleNamespace(owned={}), close=lambda: calls.append('collector_close'))
    observer = FridaEventObserver(collector, 'unused')
    observer.script, observer.session = Script(), Session()
    current = CollectionSession('adb', 'serial', collector=collector)
    current.observer = observer
    with pytest.raises(RuntimeError):
        current.close()
    report = App.verify_shutdown(SimpleNamespace(session=current))
    assert calls == ['unload', 'detach', 'collector_close']
    assert not current.closed
    assert not report['ok'] and not report['session_closed']
    assert not report['observer_released'] and report['forwards_released']
    assert ('unload refused' in ' '.join(report['issues'])) == ('script' in failures)
    assert ('detach refused' in ' '.join(report['issues'])) == ('session' in failures)
    # A failed release retains its handle so it can be retried, not forgotten.
    assert (observer.script is not None) == ('script' in failures)
    assert (observer.session is not None) == ('session' in failures)
    failures.clear()
    current.close()
    assert observer.script is observer.session is None
    # Even a retry cannot erase the fact that this shutdown had cleanup errors.
    assert not App.verify_shutdown(SimpleNamespace(session=current))['ok']


def test_collector_close_exception_is_in_shutdown_report_even_without_owned_forwards():
    def fail():
        raise OSError('transport close refused')

    current = CollectionSession('adb', 'serial', collector=SimpleNamespace(
        close=fail, forward_registry=SimpleNamespace(owned={})))
    with pytest.raises(OSError, match='transport close refused'):
        current.close()
    report = App.verify_shutdown(SimpleNamespace(session=current))
    assert not report['ok'] and not report['session_closed']
    assert report['observer_released'] and report['forwards_released']
    assert 'transport close refused' in ' '.join(report['issues'])


def test_failed_start_observer_cleanup_is_not_forgotten_after_snapshot_fallback():
    class FailedObserver:
        def __init__(self, *args):
            pass

        def start(self):
            raise RuntimeError('attach failed')

        def stop(self):
            raise RuntimeError('startup detach refused')

    current = CollectionSession('adb', 'serial', observer_factory=FailedObserver,
                                collector=SimpleNamespace(close=lambda: None))
    current._attach_observer()
    assert current.observer is None
    with pytest.raises(RuntimeError, match='startup detach refused'):
        current.close()
    report = App.verify_shutdown(SimpleNamespace(session=current))
    assert not report['ok'] and not report['observer_released']
    assert 'startup detach refused' in ' '.join(report['issues'])


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_all_close_errors_are_retained_without_skipping_other_resources(error_type):
    calls = []

    def fail(resource):
        def close():
            calls.append(resource)
            raise error_type(resource + ' close refused')
        return close

    current = CollectionSession('adb', 'serial', collector=SimpleNamespace(
        close=fail('collector'), forward_registry=SimpleNamespace(owned={})))
    current.capture = SimpleNamespace(close=fail('capture'))
    current.observer = SimpleNamespace(stop=fail('observer'), script=None, session=None)
    with pytest.raises(error_type, match='capture close refused'):
        current.close()
    assert calls == ['capture', 'observer', 'collector']
    assert set(current.cleanup_errors) == {'capture', 'observer', 'collector'}
    # Even a stale/foreign pre-set closed flag and cleared observer handles
    # cannot override the recorded outcome of actual cleanup operations.
    current.closed = True
    report = App.verify_shutdown(SimpleNamespace(session=current))
    assert not report['ok'] and not report['session_closed']
    assert not report['observer_released']
    for resource in calls:
        assert resource + ' close refused' in ' '.join(report['issues'])


def test_interrupted_unload_still_attempts_detach_and_retains_failed_handle():
    calls = []

    def unload():
        calls.append('unload')
        raise KeyboardInterrupt('unload interrupted')

    observer = FridaEventObserver(SimpleNamespace(), 'unused')
    observer.script = SimpleNamespace(is_destroyed=False, unload=unload)
    observer.session = SimpleNamespace(is_detached=False, detach=lambda: calls.append('detach'))
    with pytest.raises(KeyboardInterrupt, match='unload interrupted'):
        observer.stop()
    assert calls == ['unload', 'detach']
    assert observer.session is None and observer.script is not None
    assert observer.cleanup_errors == {'script': 'unload interrupted'}
