"""Owned script/session failures across retries; no game or Frida transport."""
from types import SimpleNamespace

import pytest

from collection_session import CollectionSession
from event_observer import FridaEventObserver, ObserverStatus
from test_collection_session import Collector, LiveObserver


def test_failed_probe_keeps_structured_script_error_before_cleanup():
    collector = Collector([])
    collector.diagnostics.platform = 'native_pc'
    collector.process = collector.core.mc
    audit = []
    detail = {'description': 'probe layout mismatch', 'fileName': '/script1.js',
              'lineNumber': 134, 'stack': 'at initialize (/script1.js:134)'}

    class FailedProbe(LiveObserver):
        def start(self):
            self.script_error = dict(detail)
            raise RuntimeError('probe layout mismatch')

        def stop(self):
            self.script_error.clear()

    current = CollectionSession(None, None, collector=collector, frida_remote=None,
                                observer_factory=FailedProbe,
                                audit=lambda kind, data: audit.append((kind, data)))
    current.enable_lifecycle()
    current.connect()
    try:
        failure = next(data for kind, data in audit if kind == 'observer_error')
        assert failure == {'reason': 'probe layout mismatch', 'error_type': 'RuntimeError', 'script_error': detail}
        assert current.event_status == 'failed' and current.event_error == 'probe layout mismatch'
        assert current.observer is None and current._pending_observers == []
    finally:
        current.close()


def owned_session(platform, *, fail_start=True):
    collector = Collector([])
    collector.diagnostics.platform = platform
    collector.process = collector.core.mc
    failures = {'unload', 'detach'}
    owners, audit = [], []

    class OwnedObserver(LiveObserver):
        def __init__(self, collector, remote):
            if owners:
                # The real observer release implementation must have confirmed
                # both resources before even constructing the replacement.
                assert owners[0].script is owners[0].session is None
            super().__init__(collector, remote)
            self.status = ObserverStatus.STOPPED
            self.cleanup_errors = {}
            self.script = self.session = None
            owners.append(self)

        def start(self):
            super().start()

            def release(resource, state):
                collector.calls.append(resource)
                if resource in failures:
                    raise RuntimeError(resource + ' refused')
                setattr(state, 'is_destroyed' if resource == 'unload' else 'is_detached', True)

            script = SimpleNamespace(is_destroyed=False)
            native = SimpleNamespace(is_detached=False)
            script.unload = lambda: release('unload', script)
            native.detach = lambda: release('detach', native)
            self.script, self.session = script, native
            self.status = ObserverStatus.FAILED if fail_start and len(owners) == 1 else ObserverStatus.READY
            if self.status == ObserverStatus.FAILED:
                raise RuntimeError('initial observer start failed')

        def stop(self):
            self.status = ObserverStatus.STOPPED
            FridaEventObserver._release(self)

    current = CollectionSession(None, None, collector=collector,
                                frida_remote=None if platform == 'native_pc' else 'remote',
                                observer_factory=OwnedObserver,
                                audit=lambda kind, data: audit.append((kind, data)))
    current.enable_lifecycle()
    return current, collector, failures, owners, audit


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
def test_initial_retry_never_constructs_replacement_before_owned_handles_release(platform):
    current, _, failures, owners, audit = owned_session(platform)
    current.connect()
    try:
        old = owners[0]
        assert current.observer is current.capture.observer is None
        assert current._pending_observers == [old]
        assert not current._attach_observer()
        assert len(owners) == 1 and old.script is not None and old.session is not None
        assert current.event_status == 'failed'
        assert any(kind == 'observer_attach_blocked' for kind, _ in audit)

        failures.remove('unload')
        assert not current._attach_observer()
        assert len(owners) == 1 and old.script is None and old.session is not None
        failures.clear()
        assert current._attach_observer()
        assert len(owners) == 2 and current.observer is owners[1]
        assert old.script is old.session is None and current._pending_observers == []
        assert current.event_status == 'ready' and current.event_error is None
        assert len(current.cleanup_errors['observer']) == 3
    finally:
        failures.clear()
        current.close()
    assert current.cleanup_errors['observer']  # Historical failure remains reportable.


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
def test_failed_start_new_battle_cleanup_failure_then_success_preserves_coverage(platform):
    current, collector, failures, owners, audit = owned_session(platform)
    current.connect()
    try:
        first = current.read()
        assert first['event_status'] == 'failed' and len(owners) == 1

        collector.session, collector.damage = 'second-battle', 0
        blocked = current.read()
        assert blocked['battle_id'] != first['battle_id']
        assert blocked['event_status'] == 'failed' and len(owners) == 1
        assert current._pending_observers == [owners[0]]
        assert blocked['statistics']['rows']['char_a']['shield_absorbed'] is None
        assert not blocked['statistics']['rows']['char_a']['event_coverage_complete']

        # The blocked generation is not repeatedly reattached or retrospectively
        # marked complete. A later battle can retry cleanup before fresh hooks.
        failures.clear()
        collector.frame, collector.game_time, collector.damage = 2, 1, 20
        still_blocked = current.read()
        assert still_blocked['event_status'] == 'failed' and len(owners) == 1
        assert still_blocked['statistics']['rows']['char_a']['damage'] == 20
        collector.session, collector.frame, collector.game_time, collector.damage = 'third-battle', 1, 0, 0
        recovered = current.read()
        assert recovered['battle_id'] != blocked['battle_id']
        assert recovered['event_status'] == 'recording' and len(owners) == 2
        assert current._pending_observers == []
        assert owners[0].script is owners[0].session is None
        assert current.event_error is None and current.cleanup_errors['observer']
        second_archive = next(data for kind, data in audit
                              if kind == 'archive_ready' and data['battle_id'] == blocked['battle_id'])
        assert second_archive['event_status'] == 'failed'
        assert not second_archive['statistics']['rows']['char_a']['event_coverage_complete']
    finally:
        failures.clear()
        current.close()


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
def test_cleanup_retry_does_not_reopen_closed_same_battle_capture(platform):
    current, _, failures, owners, _ = owned_session(platform, fail_start=False)
    current.connect()
    try:
        first = current.read()
        capture = current.capture
        capture.close()
        current.runtime.sessions.event_status = 'gap'
        current.runtime.sessions.event_error = 'confirmed event coverage gap'
        assert not current._attach_observer()
        assert current.capture is capture and capture.closed and len(owners) == 1
        failures.clear()
        assert current._attach_observer()
        resumed = current.read()
        assert resumed['battle_id'] == first['battle_id']
        assert resumed['event_status'] == 'gap'
        assert resumed['event_error'] == 'confirmed event coverage gap'
        assert current.capture is capture and capture.closed
        assert not resumed['statistics']['rows']['char_a']['event_coverage_complete']
    finally:
        failures.clear()
        current.close()


def test_every_pending_owner_must_release_before_replacement_construction():
    calls, failures = [], {'second'}

    def retired(name):
        def stop():
            calls.append(name)
            if name in failures:
                raise RuntimeError(name + ' still owns native handles')
        return SimpleNamespace(stop=stop)

    first, second = retired('first'), retired('second')

    def factory(*_args):
        assert current._pending_observers == []
        calls.append('construct')
        return SimpleNamespace(start=lambda: calls.append('start'), stop=lambda: calls.append('stop'))

    current = CollectionSession(None, None, collector=SimpleNamespace(close=lambda: None),
                                observer_factory=factory)
    current._pending_observers = [first, second]
    assert not current._attach_observer()
    assert calls == ['first', 'second'] and current._pending_observers == [second]
    failures.clear()
    assert current._attach_observer()
    assert calls == ['first', 'second', 'second', 'construct', 'start']
    assert current.cleanup_errors['observer'] == ['second still owns native handles']
    current.close()
