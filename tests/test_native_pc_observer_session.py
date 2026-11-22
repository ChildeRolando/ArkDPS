"""Explicit downstream observer injection; no target, Android transport or probe."""
from types import SimpleNamespace

import pytest

from collection_session import CollectionSession, DEFAULT_FRIDA_REMOTE, WAITING_GAME_INIT
from event_observer import ObserverStatus


class PcCollector:
    def __init__(self):
        self.calls = []
        self.process = SimpleNamespace(pid=123, process_instance=(123, 456))
        self.diagnostics = SimpleNamespace(platform='native_pc', connection_ok=True)
        self.event_capability = dict(status='unavailable', error='snapshot only')

    def connect(self, *, locate):
        assert locate is False
        self.calls.append('connect')

    def locate_lifecycle(self):
        self.calls.append('lifecycle')

    def close(self):
        self.calls.append('close')

    # Deliberately no core, observer_boot_ready, bind_battle/read/read_roster.


class TrustedObserver:
    def __init__(self, collector, remote):
        assert remote is None
        self.collector = collector
        self.status = ObserverStatus.READY
        self.error = None
        self.probe_modes = {}

    def start(self):
        self.collector.calls.append('observer_start')

    def stop(self):
        self.collector.calls.append('observer_stop')


def session(factory=None, *, remote=None, lifecycle=True):
    collector = PcCollector()
    current = CollectionSession(None, None, collector=collector, frida_remote=remote,
                                observer_factory=factory)
    if lifecycle:
        current.enable_lifecycle()
    return current, collector


def test_trusted_pc_factory_uses_existing_lifecycle_capture_without_android_boot():
    current, collector = session(TrustedObserver)
    current.connect()
    try:
        assert collector.calls == ['connect', 'observer_start', 'lifecycle']
        assert current.event_status == 'ready' and current.event_error is None
        assert current.capture.observer is current.observer
        assert collector.event_capability == dict(status='ready', error=None)
    finally:
        current.close()
    assert collector.calls[-2:] == ['observer_stop', 'close']


@pytest.mark.parametrize('remote', ['', DEFAULT_FRIDA_REMOTE, 'explicit-android-endpoint'])
def test_default_pc_remains_snapshot_only_even_with_an_android_endpoint(monkeypatch, remote):
    import event_observer

    def forbidden(*_args):
        pytest.fail('PC must not construct the default Android observer')

    monkeypatch.setattr(event_observer, 'FridaEventObserver', forbidden)
    current, collector = session(remote=remote)
    current.connect()
    try:
        assert current.observer is current.capture.observer is None
        assert current.event_status == 'unavailable'
        assert current.event_error == 'snapshot only'
        assert collector.calls == ['connect', 'lifecycle']
    finally:
        current.close()


def test_default_pc_selects_native_frida_without_android_transport(monkeypatch):
    import event_observer

    monkeypatch.setattr(event_observer, 'NativePcFridaObserver', TrustedObserver)
    current, collector = session()
    current.connect()
    try:
        assert isinstance(current.observer, TrustedObserver)
        assert current.event_status == 'ready'
        assert collector.calls == ['connect', 'observer_start', 'lifecycle']
    finally:
        current.close()


def test_native_pc_cold_start_waits_for_checked_runtime_before_loading_observer(monkeypatch):
    import collection_session

    current, collector = session(TrustedObserver)
    clock = SimpleNamespace(now=0)
    collector.observer_boot_ready = lambda: clock.now >= 1
    audit = []
    current.audit = lambda kind, payload: audit.append((kind, payload))
    monkeypatch.setattr(collection_session.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(collection_session.time, 'sleep', lambda duration: setattr(clock, 'now', clock.now + duration))
    current.connect()
    try:
        assert current.event_status == WAITING_GAME_INIT
        assert current.observer is current.capture is None
        assert collector.calls == ['connect']
        assert ('observer_waiting_game_init', {'pid': 123}) in audit
        current.wait_for_game_init(wait_seconds=2)
        assert current.event_status == 'ready'
        assert collector.calls == ['connect', 'observer_start', 'lifecycle']
        assert ('observer_game_init_ready', {'pid': 123}) in audit
    finally:
        current.close()


def test_disabled_native_pc_does_not_poll_boot_or_attach_observer():
    current, collector = session(remote='')
    collector.observer_boot_ready = lambda: pytest.fail('disabled observer polled game initialization')
    current.connect()
    try:
        assert current.event_status == 'unavailable'
        assert current.observer is None
        assert collector.calls == ['connect', 'lifecycle']
    finally:
        current.close()


@pytest.mark.parametrize('remote,lifecycle', [(DEFAULT_FRIDA_REMOTE, True), ('endpoint', True),
                                               (None, False)])
def test_pc_factory_cannot_opt_into_android_transport_or_non_lifecycle_mode(remote, lifecycle):
    def forbidden(*_args):
        pytest.fail('ineligible PC factory must not be constructed')

    current, collector = session(forbidden, remote=remote, lifecycle=lifecycle)
    current.connect()
    try:
        assert current.observer is current.capture.observer is None
        assert current.event_status == 'unavailable'
        assert '生命周期' in current.event_error and 'frida_remote=None' in current.event_error
        assert 'observer_start' not in collector.calls
    finally:
        current.close()


def test_pc_startup_failure_cleans_up_and_retries_only_at_next_battle_generation():
    class Recovering(TrustedObserver):
        attempts = 0

        def start(self):
            super().start()
            type(self).attempts += 1
            if self.attempts == 1:
                raise RuntimeError('trusted provider unavailable')

    current, collector = session(Recovering)
    current.connect()
    try:
        assert current.event_status == 'failed'
        assert current.observer is current.capture.observer is None
        assert collector.calls == ['connect', 'observer_start', 'observer_stop', 'lifecycle']
        for _ in range(2):
            assert current._prepare_battle_observer(SimpleNamespace(generation=1, events=None))[0] is False
        assert Recovering.attempts == 1
        retried, observer = current._prepare_battle_observer(SimpleNamespace(generation=2, events=None))
        assert retried and observer is current.observer
        assert current.event_status == 'ready' and Recovering.attempts == 2
        for generation in (2, 3):
            assert current._prepare_battle_observer(SimpleNamespace(generation=generation, events=None))[0] is False
        assert Recovering.attempts == 2
    finally:
        current.close()


def test_pc_failed_factory_construction_keeps_lifecycle_snapshot_capture_available():
    def unavailable(*_args):
        raise RuntimeError('provider construction failed')

    current, collector = session(unavailable)
    current.connect()
    try:
        assert current.event_status == 'failed' and current.event_error == 'provider construction failed'
        assert current.capture is not None and current.capture.observer is None
        assert collector.calls == ['connect', 'lifecycle']
    finally:
        current.close()


def test_pc_startup_cleanup_failure_retains_observer_for_shutdown_retry():
    class Failed(TrustedObserver):
        stops = 0

        def start(self):
            super().start()
            raise RuntimeError('provider startup failed')

        def stop(self):
            super().stop()
            type(self).stops += 1
            if self.stops == 1:
                raise RuntimeError('provider cleanup failed')

    current, collector = session(Failed)
    current.connect()
    assert current.event_status == 'failed' and len(current._pending_observers) == 1
    current.close()
    assert Failed.stops == 2 and current._pending_observers == []
    assert collector.calls[-1] == 'close'
    assert current.cleanup_errors['observer'] == ['provider cleanup failed']
