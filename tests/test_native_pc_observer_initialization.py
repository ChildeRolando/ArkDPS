"""Real Session/PC readiness composition with controlled process memory only."""
from types import SimpleNamespace

import pytest

from collection_session import CollectionSession, WAITING_GAME_INIT
from collector import SampleUnavailable
from native_pc_collector import NativePcCollector
from test_native_pc_battle_probe import battle, runtime


def test_valid_battle_class_cannot_attach_before_completed_vm_init(runtime, monkeypatch):
    root, api, _ = runtime
    probe = battle.NativePcBattleProbe(root, api)
    calls = []
    clock = [0.0]
    monkeypatch.setattr('collection_session.time.monotonic', lambda: clock[0])

    class Observer:
        def __init__(self, collector, remote):
            calls.append('construct')

        def start(self):
            calls.append('start')

        def stop(self):
            calls.append('stop')

    collector = SimpleNamespace(process=probe,
        diagnostics=SimpleNamespace(platform='native_pc'), event_capability={},
        connect=lambda **_kwargs: None, observer_boot_ready=probe.observer_boot_ready,
        locate_lifecycle=lambda: None, close=probe.close)
    current = CollectionSession(None, None, collector=collector, frida_remote=None,
                                observer_factory=Observer)
    current.enable_lifecycle()
    try:
        probe._binding()  # Old class/static gate already reports readiness.
        api.put(api.base + battle.RUNTIME_INIT_FLAGS_RVA, '<BB', 0, 0)
        current.connect()
        assert current.event_status == WAITING_GAME_INIT
        assert current.observer is current.capture is None and calls == []
        # Even a done byte cannot admit hooks while Init still owns its lock.
        api.put(api.base + battle.RUNTIME_INIT_FLAGS_RVA, '<BB', 1, 0)
        api.put(api.base + battle.RUNTIME_INIT_LOCK_RVA, '<Qi', 123, 1)
        clock[0] = 1.0
        assert not current._finish_when_game_ready() and calls == []
        api.put(api.base + battle.RUNTIME_INIT_LOCK_RVA, '<Qi', 0, 0)
        clock[0] = 2.0
        assert current._finish_when_game_ready()
        assert current.event_status == 'ready' and calls == ['construct', 'start']
        assert current._finish_when_game_ready() and calls == ['construct', 'start']
    finally:
        current.close()


def test_stable_lazy_field_table_times_out_without_observer_or_ready_receipt(runtime, monkeypatch):
    root, api, p = runtime
    api.put(p['klass'] + battle.CLASS_FIELDS, '<Q', 0)
    probe = battle.NativePcBattleProbe(root, api)
    collector = NativePcCollector(root)
    collector.process = probe
    collector.connect = lambda **_: None
    collector.locate_lifecycle = lambda: None
    clock, constructed, audit = [0.0], [], []
    monkeypatch.setattr('collection_session.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('collection_session.time.sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def forbidden_observer(*args):
        constructed.append(True)
        raise AssertionError('empty metadata must never construct an observer')

    current = CollectionSession(None, None, collector=collector, frida_remote=None,
        observer_factory=forbidden_observer, audit=lambda kind, data: audit.append((kind, data)))
    current.enable_lifecycle()
    try:
        current.connect()
        assert current.event_status == WAITING_GAME_INIT
        with pytest.raises(SampleUnavailable, match='初始化超时'):
            current.wait_for_game_init(wait_seconds=.6)
        assert current.event_status == WAITING_GAME_INIT and not constructed
        assert collector.observer_boot_reason == 'field_table_missing'
        assert collector.diagnostics.details['observer_boot'] == dict(ready=False, reason='field_table_missing')
        assert not any(kind in ('observer_game_init_ready', 'observer_ready') for kind, _ in audit)
    finally:
        current.close()
