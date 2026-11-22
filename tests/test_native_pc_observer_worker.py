"""Worker source routing with injected in-memory sessions; no PC, ADB or feed."""
import sys
import threading
from io import StringIO
from types import SimpleNamespace

import pytest

import desktop_bridge
from connection import ConnectionConfig, ConnectionDiagnostics, DetectionStatus
from desktop_bridge import CollectionWorker, DesktopState
from sample_data import sample


def run_worker(tmp_path, monkeypatch, *, factory=None, enabled=True, platform='native_pc',
               provider_state='recording', publish=True, observer_display_attributes=True,
               arkframework_root=None, bootstrap_pending=False, bootstrap_pending_state='starting'):
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    state = DesktopState(tmp_path / 'state')
    state.command(dict(version=1, op='panel_update',
                       config=dict(state.layout.panels[0].to_dict(), rate_basis='engagement')))
    worker = CollectionWorker(state, endpoint='expert-android-endpoint',
                              arkframework_root=arkframework_root,
                              native_pc_observer_factory=factory)
    calls = []

    class Startup:
        checks = 0
        phase = 'ready'
        @staticmethod
        def observer_factory(*_args):
            pytest.fail('the in-memory worker session only forwards this provider')

        def start(self):
            calls.append(('pc_startup', 'started'))

        def close(self):
            calls.append(('pc_startup', 'closed'))

        def retry(self):
            calls.append(('pc_startup', 'retry'))

        @property
        def is_pending(self):
            return self.phase != 'ready'

        def describe(self):
            self.checks += 1
            if bootstrap_pending:
                calls.append(('pc_bootstrap_state', bootstrap_pending_state if self.checks == 1 else 'ready'))
                if self.checks == 1:
                    worker.last_connect_attempt = float('-inf')
                    self.phase = bootstrap_pending_state
                    return {'status': bootstrap_pending_state}
            self.phase = 'ready'
            return {'status': 'ready'}

    def startup(game_root, runtime_dir):
        calls.append(('pc_startup_created', game_root, runtime_dir))
        return Startup()

    import capture_cli
    monkeypatch.setattr(capture_cli, '_native_pc_frida_runtime', startup)

    def diagnostic(source):
        return ConnectionDiagnostics(status=DetectionStatus.READY_UNIQUE, platform=source,
                                     selected_serial='test-serial', connection_ok=True,
                                     version_actual='test-build')

    class NativeCollector:
        diagnostics = diagnostic('native_pc')

        def __init__(self, root, *, collect_actions, event_identity_only=False):
            assert event_identity_only is (enabled and arkframework_root is None and factory is None)
            calls.append(('native_collector', root, collect_actions))

    class Session:
        cleanup_errors = {}

        def __init__(self, adb, serial, *, frida_remote, collector=None, **kwargs):
            self.collector = collector or SimpleNamespace(diagnostics=diagnostic('mumu'))
            self.is_pc = collector is not None
            self.provider = kwargs.get('observer_factory')
            calls.append(('session', self.is_pc, adb, serial, frida_remote, self.provider))
            assert frida_remote == (None if enabled else '') if self.is_pc else frida_remote == (
                worker.endpoint if enabled else None)
            active = enabled
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
            if enabled:
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
    timed_out = threading.Event()
    def expire():
        timed_out.set()
        worker.stop.set()
    watchdog = threading.Timer(10, expire)
    watchdog.daemon = True
    watchdog.start()
    try:
        worker.run()
    finally:
        watchdog.cancel()
    assert not timed_out.is_set(), 'In-memory worker did not reach its scripted stop condition'
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
    assert ('native_collector', 'recorded-game-root', True) in calls
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


def test_default_pc_frida_starts_bootstrap_before_collector_and_preserves_transport(tmp_path, monkeypatch):
    state, calls, events = run_worker(tmp_path, monkeypatch)
    created = next(call for call in calls if call[0] == 'session')
    assert created[1:5] == (True, None, None, None) and callable(created[5])
    assert calls.index(('pc_startup', 'started')) < calls.index(('native_collector', 'recorded-game-root', True))
    assert calls.index(('closed', True)) < calls.index(('pc_startup', 'closed'))
    assert ('observer', ('ready', None)) in events
    assert state.snapshot()['event_status'] == 'recording'


@pytest.mark.parametrize('pending_state', ['starting', 'waiting_for_endpoint', 'waiting_for_process', 'loading'])
def test_pending_pc_startup_defers_snapshot_access_without_recreating_watcher(tmp_path, monkeypatch, pending_state):
    _, calls, events = run_worker(tmp_path, monkeypatch, bootstrap_pending=True,
                                  bootstrap_pending_state=pending_state)
    assert calls.count(('pc_startup', 'started')) == 1
    assert calls.index(('pc_bootstrap_state', pending_state)) < calls.index(('pc_bootstrap_state', 'ready'))
    assert calls.index(('pc_bootstrap_state', 'ready')) < calls.index(('native_collector', 'recorded-game-root', True))
    assert ('observer', ('attaching', None)) in events


def test_pc_startup_survives_same_source_retries_and_stops_on_mumu(tmp_path, monkeypatch):
    import capture_cli
    calls = []

    class Startup:
        def start(self):
            calls.append('start')

        def close(self):
            calls.append('close')

        def observer_factory(self, *_args):
            pass

    startup = Startup()
    monkeypatch.setattr(capture_cli, '_native_pc_frida_runtime', lambda *_args: startup)
    worker = CollectionWorker(DesktopState(tmp_path))
    config = ConnectionConfig(platform='native_pc', native_game_root='recorded-game-root')
    first = worker._pc_provider(config, True)
    assert worker._pc_provider(config, True) == first
    assert calls == ['start']
    assert worker._pc_provider(ConnectionConfig(platform='mumu'), True) is None
    assert calls == ['start', 'close']
    assert worker.pc_runtime is None


def test_failed_pc_startup_cleanup_retains_owner_and_historical_failure(tmp_path, monkeypatch):
    import capture_cli

    class Startup:
        calls = 0

        def start(self):
            pass

        def observer_factory(self, *_args):
            pass

        def close(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('owned loader call is pending')

    startup = Startup()
    monkeypatch.setattr(capture_cli, '_native_pc_frida_runtime', lambda *_args: startup)
    worker = CollectionWorker(DesktopState(tmp_path))
    worker._pc_provider(ConnectionConfig(platform='native_pc', native_game_root='game'), True)
    with pytest.raises(RuntimeError, match='owned loader call is pending'):
        worker._pc_provider(ConnectionConfig(platform='mumu'), True)
    assert worker.pc_runtime is startup
    assert worker._pc_provider(ConnectionConfig(platform='mumu'), True) is None
    assert worker.pc_runtime is None
    assert len(worker.retired_errors) == 1 and 'owned loader call is pending' in worker.retired_errors[0]


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


@pytest.mark.parametrize('factory,enabled', [(None, False), (trusted_factory, False)])
def test_pc_without_enabled_internal_provider_preserves_snapshot_only(tmp_path, monkeypatch, factory, enabled):
    state, calls, events = run_worker(tmp_path, monkeypatch, factory=factory, enabled=enabled)
    assert ('native_collector', 'recorded-game-root', enabled) in calls
    assert ('session', True, None, None, '', None) in calls
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


@pytest.mark.parametrize('platform,enabled,configure_root,should_read', [
    ('native_pc', True, True, True),
    ('native_pc', True, False, False),
    ('native_pc', False, True, False),
    ('mumu', True, True, False),
])
def test_desktop_worker_reads_optional_timeline_in_lobby_only_when_enabled(
        tmp_path, monkeypatch, platform, enabled, configure_root, should_read):
    from native_pc.arkframework_replay import RegenerationRecorder

    polls = []
    def poll(recorder):
        polls.append((recorder.timeline_path, recorder.output_dir))
        raise FileNotFoundError('timeline not yet available')

    monkeypatch.setattr(RegenerationRecorder, 'poll', poll)
    directory = tmp_path / 'ArkFramework' if configure_root else None
    state, _, _ = run_worker(tmp_path, monkeypatch, platform=platform, enabled=enabled,
                             arkframework_root=directory, publish=False)
    assert bool(polls) == should_read
    assert state.last is None
    if should_read:
        journals = list((state.directory / 'captures').glob('wpf-*.jsonl'))
        assert len(journals) == 1
        run = journals[0].name.removesuffix('-part-000001.jsonl')
        assert polls == [(directory / 'battle_timeline.json',
                          journals[0].with_name(run + '.arkframework'))] * 2
        assert journals[0].read_text('utf-8').count('timeline not yet available') == 1


def test_source_launcher_preserves_framework_directory_argument(tmp_path, monkeypatch):
    import desktop_launcher

    executable = tmp_path / 'ArkDPS.Desktop.exe'
    executable.touch()
    project = tmp_path / 'desktop' / 'ArkDPS.Desktop.csproj'
    project.parent.mkdir()
    monkeypatch.setattr(desktop_launcher, 'ROOT', tmp_path)
    monkeypatch.setattr(desktop_launcher, 'PROJECT', project)
    monkeypatch.setattr(desktop_launcher, 'EXECUTABLE', executable)
    calls = []
    def launch(args, *, cwd):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(desktop_launcher.subprocess, 'run', launch)
    framework = tmp_path / 'Ark Framework'
    assert desktop_launcher.launch(SimpleNamespace(arkframework_root=framework, platform='native_pc')) == 0
    assert calls[0][-4:] == ['--platform', 'native_pc', '--arkframework-root', str(framework)]


@pytest.mark.parametrize('factory,flags', [
    (trusted_factory, []), (None, []),
    (trusted_factory, ['--no-events']), (trusted_factory, ['--offline']),
])
def test_bridge_main_forwards_explicit_factory_and_preserves_entry_modes(tmp_path, monkeypatch, factory, flags):
    calls = []

    class Lock:
        def bind(self, address):
            calls.append(('bind', address))

        def listen(self, _backlog):
            pass

        def close(self):
            calls.append(('lock_closed',))

    class Worker:
        def __init__(self, state, *, endpoint, native_pc_observer_factory=None, arkframework_root=None):
            calls.append(('worker', endpoint, native_pc_observer_factory, arkframework_root))
            self.thread = SimpleNamespace(start=lambda: calls.append(('started',)))

        def request(self, config, enabled):
            calls.append(('request', config.platform, enabled))
            return 7

        def close(self):
            calls.append(('closed',))

    def serve(_input, _output, state, worker):
        assert state.effective_connection.platform == 'native_pc'
        calls.append(('served', worker is not None))

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(desktop_bridge.socket, 'socket', Lock)
    monkeypatch.setattr(desktop_bridge, 'CollectionWorker', Worker)
    monkeypatch.setattr(desktop_bridge, 'serve', serve)
    monkeypatch.setattr(desktop_bridge.sys, 'stdout', StringIO())
    monkeypatch.setattr(desktop_bridge.sys, 'stderr', StringIO())
    monkeypatch.setattr(desktop_bridge.sys, 'argv', [
        'desktop_bridge.py', '--state-dir', str(tmp_path / 'state'),
        '--platform', 'native_pc', '--native-game-root', 'recorded-game-root',
        '--arkframework-root', str(tmp_path / 'framework'), *flags])
    desktop_bridge.main(native_pc_observer_factory=factory)

    if '--offline' in flags:
        assert calls == [('served', False)]
    else:
        assert ('worker', desktop_bridge.DEFAULT_FRIDA_REMOTE, factory, tmp_path / 'framework') in calls
        assert ('request', 'native_pc', '--no-events' not in flags) in calls
        assert ('started',) in calls and ('served', True) in calls
        assert calls[-2:] == [('closed',), ('lock_closed',)]
