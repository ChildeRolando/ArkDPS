"""Worker source routing with injected in-memory sessions; no PC, ADB or feed."""
import sys
from types import SimpleNamespace

import pytest

import desktop_bridge
from connection import ConnectionConfig, ConnectionDiagnostics, DetectionStatus
from desktop_bridge import CollectionWorker, DesktopState
from sample_data import sample


def run_worker(tmp_path, monkeypatch, *, factory=None, enabled=True, platform='native_pc',
               provider_state='recording', publish=True, observer_display_attributes=True):
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    state = DesktopState(tmp_path / 'state')
    state.command(dict(version=1, op='panel_update',
                       config=dict(state.layout.panels[0].to_dict(), rate_basis='engagement')))
    worker = CollectionWorker(state, endpoint='expert-android-endpoint',
                              native_pc_observer_factory=factory)
    calls = []

    def diagnostic(source):
        return ConnectionDiagnostics(status=DetectionStatus.READY_UNIQUE, platform=source,
                                     selected_serial='test-serial', connection_ok=True,
                                     version_actual='test-build')

    class NativeCollector:
        diagnostics = diagnostic('native_pc')

        def __init__(self, root):
            calls.append(('native_collector', root))

    class Session:
        cleanup_errors = {}

        def __init__(self, adb, serial, *, frida_remote, collector=None, **kwargs):
            self.collector = collector or SimpleNamespace(diagnostics=diagnostic('mumu'))
            self.is_pc = collector is not None
            self.provider = kwargs.get('observer_factory')
            calls.append(('session', self.is_pc, adb, serial, frida_remote, self.provider))
            assert frida_remote is None if self.is_pc else frida_remote == (
                worker.endpoint if enabled else None)
            active = self.provider is not None if self.is_pc else enabled
            failed = active and provider_state == 'failed'
            self.event_status = 'failed' if failed else 'ready' if active else 'unavailable'
            self.event_error = 'trusted provider unavailable' if failed else None
            self.observer = (SimpleNamespace(status=desktop_bridge.ObserverStatus.READY, error=None)
                             if active and not failed else None)
            if self.observer is not None and not observer_display_attributes:
                self.observer = SimpleNamespace()
            self.reads = 0

        def enable_lifecycle(self, runtime):
            self.runtime = runtime
            runtime.sessions.state = 'playing'

        def connect(self):
            pass

        def read(self):
            self.reads += 1
            if not publish or self.reads > 1:
                worker.stop.set()
                return None
            if self.provider is not None or not self.is_pc and enabled:
                self.event_status = provider_state
            return dict(sample(), state=2, frame=1, battle_id='synthetic-source-battle',
                        event_status=self.event_status, event_error=self.event_error)

        def close(self):
            calls.append(('closed', self.is_pc))

    def resolve(config):
        assert config.platform == 'mumu', 'PC must not resolve ADB'
        calls.append(('resolve_android',))
        return SimpleNamespace(adb_path='test-adb', serial='test-serial', diagnostics=diagnostic('mumu'))

    def endpoint(*args, **kwargs):
        assert platform == 'mumu', 'PC must not provision or keep an Android endpoint'
        calls.append(('android_endpoint', kwargs['remote']))
        return True, 'ready'

    monkeypatch.setitem(sys.modules, 'native_pc_collector', SimpleNamespace(NativePcCollector=NativeCollector))
    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', resolve)
    monkeypatch.setattr(desktop_bridge, 'ensure_frida_endpoint', endpoint)
    monkeypatch.setattr(desktop_bridge, 'keep_frida_endpoint', endpoint)
    worker.request(ConnectionConfig(platform=platform, native_game_root='recorded-game-root'), enabled)
    worker.run()
    events = list(worker.events.queue)
    assert not any(kind in ('error', 'worker_error') for kind, _ in events)
    state.effective_connection = worker.effective[0]
    for kind, value in events:
        state.accept(kind, value)
    return state, calls, events


def trusted_factory(*_args):
    pytest.fail('the worker test only forwards the factory to its in-memory session')


def test_configured_pc_provider_is_forwarded_without_android_and_publishes_recording(tmp_path, monkeypatch):
    state, calls, events = run_worker(tmp_path, monkeypatch, factory=trusted_factory)
    assert ('session', True, None, None, None, trusted_factory) in calls
    assert not any(call[0].startswith(('android_', 'resolve_')) for call in calls)
    assert ('observer', ('ready', None)) in events
    assert ('observer', ('recording', None)) in events
    snapshot = state.snapshot()
    assert snapshot['observer_status'] == snapshot['event_status'] == 'recording'
    assert snapshot['event_text'].startswith('事件观察中')
    assert '逐事件不可用' not in snapshot['event_text']


def test_configured_pc_provider_ready_before_baseline_is_not_snapshot_only(tmp_path, monkeypatch):
    state, _, _ = run_worker(tmp_path, monkeypatch, factory=trusted_factory, publish=False)
    snapshot = state.snapshot()
    assert snapshot['observer_status'] == 'ready'
    assert snapshot['event_text'] == '事件已就绪 · 等待同帧基线'
    assert '正在建立' in snapshot['panels'][0]['rate_reason']
    assert '缺少接敌速率' not in snapshot['panels'][0]['rate_reason']


def test_pc_provider_without_optional_display_attributes_uses_session_state(tmp_path, monkeypatch):
    state, _, events = run_worker(tmp_path, monkeypatch, factory=trusted_factory,
                                  observer_display_attributes=False)
    assert ('observer', ('ready', None)) in events
    assert ('observer', ('recording', None)) in events
    snapshot = state.snapshot()
    assert snapshot['event_status'] == snapshot['observer_status'] == 'recording'
    assert snapshot['event_text'].startswith('事件观察中')


def test_pc_provider_startup_failure_retains_real_failure_without_snapshot_label(tmp_path, monkeypatch):
    state, _, events = run_worker(tmp_path, monkeypatch, factory=trusted_factory,
                                  provider_state='failed', publish=False)
    assert ('observer', ('failed', 'trusted provider unavailable')) in events
    snapshot = state.snapshot()
    assert snapshot['observer_status'] == 'failed'
    assert 'trusted provider unavailable' in snapshot['event_text']
    assert '逐事件不可用' not in snapshot['event_text']
    assert 'trusted provider unavailable' in snapshot['panels'][0]['rate_reason']


@pytest.mark.parametrize('factory,enabled', [(None, True), (trusted_factory, False)])
def test_pc_without_enabled_internal_provider_preserves_snapshot_only(tmp_path, monkeypatch, factory, enabled):
    state, calls, events = run_worker(tmp_path, monkeypatch, factory=factory, enabled=enabled)
    assert ('session', True, None, None, None, None) in calls
    assert ('observer', ('snapshot_only', None)) in events
    assert state.snapshot()['event_text'] == '官服 PC 累计快照 · 逐事件不可用'


@pytest.mark.parametrize('enabled', [True, False])
def test_native_factory_does_not_change_android_endpoint_or_observer_path(tmp_path, monkeypatch, enabled):
    state, calls, _ = run_worker(tmp_path, monkeypatch, factory=trusted_factory,
                                  enabled=enabled, platform='mumu')
    assert ('session', False, 'test-adb', 'test-serial',
            'expert-android-endpoint' if enabled else None, None) in calls
    assert ('resolve_android',) in calls
    assert (('android_endpoint', 'expert-android-endpoint') in calls) is enabled
    assert state.snapshot()['observer_status'] == ('ready' if enabled else 'disabled')
