import json
from types import SimpleNamespace

import pytest

import capture_cli
from native_pc import event_source
from collection_session import CollectionSession
from event_capture import CaptureRetry
from test_collection_session import Collector, LiveObserver
from test_event_capture import event
from test_lifecycle_events import native_connection


def options(tmp_path):
    return SimpleNamespace(expect_head='candidate', stop_file=None,
                           output_dir=tmp_path / 'evidence', stage='OF-8',
                           frida_remote='remote', adb='adb', serial='serial',
                           capture_seconds=.01, wait_battle_seconds=1, baseline_timeout=1,
                           maa_job=None, maa_log=None, require_progress=False, require_events=True)


class Session:
    instances = []
    failed = False

    def __init__(self, adb, serial, *, frida_remote, audit):
        self.audit = audit
        self.calls = 0
        self.closed = False
        self.capture = SimpleNamespace(adapter=SimpleNamespace(result=lambda: {'rows': {}}))
        self.instances.append(self)

    def connect(self):
        self.event_status = 'ready'
        self.event_error = None
        self.audit('observer_ready', {'remote': 'remote'})

    def locate(self, **kwargs):
        pass

    def read(self):
        self.calls += 1
        if self.calls == 1:
            raise CaptureRetry('transient frame mismatch')
        sample = {'frame': 2, 'game_time': 1, 'event_status': 'gap' if self.failed else 'recording'}
        self.audit('published', sample)
        return sample

    def close(self):
        self.closed = True
        self.audit('coverage_closed', {'rows': {}})


def stub_git_and_clock(monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(capture_cli.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(capture_cli.time, 'sleep', lambda seconds: setattr(clock, 'now', clock.now + seconds))
    monkeypatch.setattr(capture_cli.subprocess, 'check_output',
                        lambda command, **kwargs: 'candidate\n' if command[1] == 'rev-parse' else '')
    Session.failed = False
    return clock


def test_cli_retries_and_writes_ready_raw_journal_and_summary(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    result = capture_cli.run_capture(args, session_factory=Session)
    assert result['samples'] == 1 and result['retries'] == 1
    assert result['error'] is None
    assert Session.instances[-1].closed
    assert json.loads((args.output_dir / 'READY.json').read_text())['head'] == 'candidate'
    records = [json.loads(line) for line in (args.output_dir / 'capture.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [record['kind'] for record in records] == ['metadata', 'observer_ready', 'retry', 'published', 'coverage_closed']
    header = records[0]['data']['log_header']
    assert header['schema'] == 1
    assert header['source'] == 'foreground_capture' and header['debug_mode'] is True
    assert header['settings']['platform'] == 'mumu' and header['settings']['event_observation'] is True
    assert header['settings']['manual_target_configured'] is True
    assert 'serial' not in header['settings'] and 'adb_path' not in header['settings']


def test_cli_writes_log_context_before_session_initialization(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    args = options(tmp_path)

    def fail(*_args, **_kwargs):
        raise RuntimeError('test setup failure')

    with pytest.raises(RuntimeError, match='test setup failure'):
        capture_cli.run_capture(args, session_factory=fail)
    first = (args.output_dir / 'capture.jsonl').read_text(encoding='utf-8').splitlines()[0]
    assert json.loads(first)['data']['log_header']['source'] == 'foreground_capture'


@pytest.mark.parametrize('startup_failed,required', [(False, True), (True, True), (True, False)])
def test_pc_cli_owns_startup_before_connect_and_releases_it_last(
        tmp_path, monkeypatch, startup_failed, required):
    stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    args.platform, args.native_game_root, args.frida_remote = 'native_pc', str(tmp_path / 'game'), None
    args.require_events = required
    calls = []

    class Startup:
        def start(self):
            calls.append('arm')

        def wait_ready(self, timeout, cancelled=None):
            assert calls == ['arm']
            calls.append('wait_ready')
            if startup_failed:
                raise RuntimeError('PC component missing')

        def observer_factory(self, *_args):
            pytest.fail('the in-memory session only forwards the observer factory')

        def describe(self):
            return {'status': 'failed' if startup_failed else 'ready'}

        @property
        def is_failed(self):
            return startup_failed

        def close(self):
            calls.append('startup_close')

    startup = Startup()
    monkeypatch.setattr(event_source, '_native_pc_frida_runtime', lambda *_args: startup)

    class PcSession(Session):
        def __init__(self, adb, serial, *, collector, observer_factory, **kwargs):
            assert observer_factory == startup.observer_factory
            self.collector = collector
            super().__init__(adb, serial, **kwargs)

        def connect(self):
            assert calls[:2] == ['arm', 'wait_ready']
            calls.append('connect')
            super().connect()
            if startup_failed:
                self.event_status, self.event_error = 'failed', 'PC component missing'

        def close(self):
            calls.append('session_close')
            super().close()

    if startup_failed and required:
        with pytest.raises(RuntimeError, match='PC component missing'):
            capture_cli.run_capture(args, session_factory=PcSession)
        assert 'connect' not in calls
        assert not (args.output_dir / 'READY.json').exists()
    else:
        summary = capture_cli.run_capture(args, session_factory=PcSession)
        assert summary['error'] is None
        ready = json.loads((args.output_dir / 'READY.json').read_text())
        assert ready['event_status'] == ('failed' if startup_failed else 'ready')
    assert calls[-2:] == ['session_close', 'startup_close']


@pytest.mark.parametrize('permanent', [False, True])
def test_pc_connect_bounds_initial_access_denial_after_verified_bootstrap(monkeypatch, permanent):
    from collector import SampleUnavailable
    clock = stub_git_and_clock(monkeypatch)
    calls = []
    collector = SimpleNamespace(process=None,
        diagnostics=SimpleNamespace(code='native_permission_denied', details={}))

    def connect():
        calls.append(clock.now)
        if permanent or len(calls) == 1:
            raise SampleUnavailable('initial read access denied')

    current = SimpleNamespace(collector=collector, connect=connect)
    startup = SimpleNamespace(wait_ready=lambda *_args, **_kwargs: None)
    if permanent:
        with pytest.raises(SampleUnavailable, match='initial read access denied'):
            capture_cli._connect_pc_after_bootstrap(current, startup, timeout=30, cancelled=None)
        assert 5 <= clock.now <= 5.2
    else:
        capture_cli._connect_pc_after_bootstrap(current, startup, timeout=30, cancelled=None)
        assert len(calls) == 2 and clock.now == .1


@pytest.mark.parametrize('phase', ['before_ready', 'after_ready', 'connect_success',
                                   'connect_failure', 'retry_sleep'])
def test_pc_connect_cancellation_prevents_later_waits_and_attempts(monkeypatch, phase):
    from collector import SampleUnavailable
    clock = stub_git_and_clock(monkeypatch)
    stopped = [phase == 'before_ready']
    calls = []
    collector = SimpleNamespace(process=None,
        diagnostics=SimpleNamespace(code='native_unavailable', details={'reason': 'module_not_unique'}))

    def wait_ready(timeout, cancelled=None):
        calls.append('ready')
        if phase == 'after_ready':
            stopped[0] = True

    def connect():
        calls.append('connect')
        if phase == 'connect_success':
            collector.process = object()
            stopped[0] = True
            return
        if phase == 'connect_failure':
            stopped[0] = True
        raise SampleUnavailable('module still initializing')

    def sleep(duration):
        calls.append('sleep')
        clock.now += duration
        stopped[0] = True

    monkeypatch.setattr(capture_cli.time, 'sleep', sleep)
    with pytest.raises(InterruptedError):
        capture_cli._connect_pc_after_bootstrap(
            SimpleNamespace(collector=collector, connect=connect),
            SimpleNamespace(wait_ready=wait_ready), timeout=2, cancelled=lambda: stopped[0])
    assert calls == {
        'before_ready': [], 'after_ready': ['ready'],
        'connect_success': ['ready', 'connect'], 'connect_failure': ['ready', 'connect'],
        'retry_sleep': ['ready', 'connect', 'sleep'],
    }[phase]
    if phase == 'connect_success':
        assert collector.process is not None  # Caller still owns cleanup after cancellation.


@pytest.mark.parametrize('ready_elapsed', [1, 1.5])
def test_pc_connect_does_not_start_after_readiness_uses_positive_budget(monkeypatch, ready_elapsed):
    clock = stub_git_and_clock(monkeypatch)
    calls = []

    def wait_ready(timeout, cancelled=None):
        assert timeout == 1
        clock.now += ready_elapsed

    with pytest.raises(TimeoutError, match='wait budget'):
        capture_cli._connect_pc_after_bootstrap(
            SimpleNamespace(connect=lambda: calls.append('connect')),
            SimpleNamespace(wait_ready=wait_ready), timeout=1, cancelled=None)
    assert calls == []


@pytest.mark.parametrize('oversleep', [0, .2])
def test_pc_connect_retries_share_remaining_readiness_budget(monkeypatch, oversleep):
    from collector import SampleUnavailable
    clock = stub_git_and_clock(monkeypatch)
    attempts, sleeps = [], []
    failure = SampleUnavailable('module not initialized within shared budget')
    collector = SimpleNamespace(process=None,
        diagnostics=SimpleNamespace(code='native_unavailable', details={'reason': 'module_not_unique'}))

    def wait_ready(timeout, cancelled=None):
        clock.now = .95

    def connect():
        attempts.append(clock.now)
        raise failure

    def sleep(duration):
        sleeps.append(duration)
        clock.now += duration + oversleep

    monkeypatch.setattr(capture_cli.time, 'sleep', sleep)
    with pytest.raises(SampleUnavailable) as caught:
        capture_cli._connect_pc_after_bootstrap(SimpleNamespace(collector=collector, connect=connect),
            SimpleNamespace(wait_ready=wait_ready), timeout=1, cancelled=None)
    assert caught.value is failure
    assert attempts == [.95] and sleeps == pytest.approx([.05])


@pytest.mark.parametrize('ready_elapsed,timeout,end', [(3, 4, 4), (27, 30, 30), (1, 8, 6)])
def test_pc_connect_access_grace_never_extends_shared_budget_or_retries_after_grace(
        monkeypatch, ready_elapsed, timeout, end):
    from collector import SampleUnavailable
    clock = stub_git_and_clock(monkeypatch)
    attempts = []
    failure = SampleUnavailable('verified startup access denial')
    collector = SimpleNamespace(process=None,
        diagnostics=SimpleNamespace(code='native_permission_denied', details={}))

    def connect():
        attempts.append(clock.now)
        raise failure

    startup = SimpleNamespace(wait_ready=lambda *_args, **_kwargs: setattr(clock, 'now', ready_elapsed))
    with pytest.raises(SampleUnavailable) as caught:
        capture_cli._connect_pc_after_bootstrap(SimpleNamespace(collector=collector, connect=connect),
                                               startup, timeout=timeout, cancelled=None)
    assert caught.value is failure
    assert attempts and max(attempts) < end and clock.now == pytest.approx(end)


@pytest.mark.parametrize('result', ['success', 'module_initializing', 'access_denied', 'not_ready'])
def test_pc_connect_zero_budget_allows_only_one_immediate_ready_check(monkeypatch, result):
    from collector import SampleUnavailable
    clock = stub_git_and_clock(monkeypatch)
    calls = []
    collector = SimpleNamespace(process=None, diagnostics=SimpleNamespace(
        code='native_permission_denied' if result == 'access_denied' else 'native_unavailable',
        details={'reason': 'module_not_unique'}))

    def wait_ready(timeout, cancelled=None):
        calls.append('ready')
        assert timeout == 0
        if result == 'not_ready':
            raise TimeoutError('not immediately ready')

    def connect():
        calls.append('connect')
        if result != 'success':
            raise SampleUnavailable('initial connection unavailable')

    current, startup = SimpleNamespace(collector=collector, connect=connect), SimpleNamespace(wait_ready=wait_ready)
    if result == 'success':
        capture_cli._connect_pc_after_bootstrap(current, startup, timeout=0, cancelled=None)
    else:
        with pytest.raises(TimeoutError if result == 'not_ready' else SampleUnavailable):
            capture_cli._connect_pc_after_bootstrap(current, startup, timeout=0, cancelled=None)
    assert calls == (['ready'] if result == 'not_ready' else ['ready', 'connect'])
    assert clock.now == 0


@pytest.mark.parametrize('already_owned', [False, True])
def test_pc_connect_does_not_retry_after_initial_attempt_owns_a_process(monkeypatch, already_owned):
    from collector import SampleUnavailable
    clock = stub_git_and_clock(monkeypatch)
    owner = object()
    collector = SimpleNamespace(process=owner if already_owned else None,
        diagnostics=SimpleNamespace(code='native_unavailable', details={'reason': 'module_not_unique'}))
    calls = []
    failure = SampleUnavailable('connected process has further initialization failure')

    def connect():
        calls.append('connect')
        collector.process = owner
        raise failure

    with pytest.raises(SampleUnavailable) as caught:
        capture_cli._connect_pc_after_bootstrap(SimpleNamespace(collector=collector, connect=connect),
            SimpleNamespace(wait_ready=lambda *_args, **_kwargs: None), timeout=5, cancelled=None)
    assert caught.value is failure and collector.process is owner
    assert calls == ['connect'] and clock.now == 0


@pytest.mark.parametrize('typed_version_error,code', [
    (True, 'native_permission_denied'), (True, 'native_unavailable'),
    (False, 'native_unsupported_build'), (False, 'native_root_required'),
])
def test_pc_connect_keeps_permanent_failure_without_retry(monkeypatch, typed_version_error, code):
    from collector import SampleUnavailable, UnsupportedVersion
    clock = stub_git_and_clock(monkeypatch)
    calls = []
    failure = (UnsupportedVersion if typed_version_error else SampleUnavailable)('permanent checked failure')
    collector = SimpleNamespace(process=None,
        diagnostics=SimpleNamespace(code=code, details={'reason': 'module_not_unique'}))

    def connect():
        calls.append('connect')
        raise failure

    with pytest.raises(type(failure)) as caught:
        capture_cli._connect_pc_after_bootstrap(SimpleNamespace(collector=collector, connect=connect),
            SimpleNamespace(wait_ready=lambda *_args, **_kwargs: None), timeout=5, cancelled=None)
    assert caught.value is failure and calls == ['connect'] and clock.now == 0


@pytest.mark.parametrize('timeout', [-1, float('nan'), float('inf'), True, '1'])
def test_pc_connect_rejects_invalid_budget_before_bootstrap(monkeypatch, timeout):
    stub_git_and_clock(monkeypatch)
    with pytest.raises(ValueError, match='finite and nonnegative'):
        capture_cli._connect_pc_after_bootstrap(
            SimpleNamespace(connect=lambda: pytest.fail('invalid budget connected')),
            SimpleNamespace(wait_ready=lambda *_args, **_kwargs: pytest.fail('invalid budget waited')),
            timeout=timeout, cancelled=None)


def test_cli_retains_failure_evidence_and_cleans_up(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    Session.failed = True
    args = options(tmp_path)
    with pytest.raises(RuntimeError):
        capture_cli.run_capture(args, session_factory=Session)
    result = json.loads((args.output_dir / 'summary.json').read_text())
    assert 'event coverage is unavailable' in result['error']
    assert Session.instances[-1].closed


def test_cli_refuses_overwriting_evidence_before_connecting(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    args.output_dir.mkdir()
    previous = args.output_dir / 'capture.jsonl'
    previous.write_text('earlier run')
    before = len(Session.instances)
    with pytest.raises(RuntimeError):
        capture_cli.run_capture(args, session_factory=Session)
    assert previous.read_text() == 'earlier run' and len(Session.instances) == before


def test_cli_writes_ready_only_after_cold_start_observer_attaches(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    args.wait_battle_seconds = 5
    collector = Collector([])
    checks = iter((False, True))

    def boot_ready():
        assert not (args.output_dir / 'READY.json').exists()
        return next(checks)

    monkeypatch.setattr(collector, 'observer_boot_ready', boot_ready)

    def factory(adb, serial, **kwargs):
        return CollectionSession(adb, serial, collector=collector,
                                 observer_factory=LiveObserver, **kwargs)

    summary = capture_cli.run_capture(args, session_factory=factory)
    ready = json.loads((args.output_dir / 'READY.json').read_text(encoding='utf-8'))
    assert summary['samples'] == 1
    assert ready['status'] == 'observer_ready'
    assert collector.calls.count('attach') == 1


def test_cli_wait_preserves_native_counters_before_lifecycle_observer_readiness(tmp_path, monkeypatch):
    clock = stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    args.wait_battle_seconds = 3
    collector = Collector([])
    monkeypatch.setattr(collector, 'observer_boot_ready', lambda: False)
    monkeypatch.setattr(collector, 'observer_lifecycle_ready', lambda: clock.now >= 1)
    original_read = collector.read

    def read():
        if clock.now >= 1:
            collector.frame, collector.game_time, collector.damage = 2, 1, 120
        return original_read()

    monkeypatch.setattr(collector, 'read', read)

    def factory(adb, serial, **kwargs):
        return CollectionSession(adb, serial, collector=collector,
                                 observer_factory=LiveObserver, **kwargs)

    summary = capture_cli.run_capture(args, session_factory=factory)
    records = [json.loads(line) for line in
               (args.output_dir / 'capture.jsonl').read_text(encoding='utf-8').splitlines()]
    attached = next(i for i, item in enumerate(records) if item['kind'] == 'observer_ready')
    early = [item['data'] for item in records[:attached] if item['kind'] == 'published']
    assert early and early[0]['statistics']['rows']['char_a']['damage'] == 100
    assert all(item['event_status'] == 'waiting_game_init' and item['event_watermark'] is None
               for item in early)
    assert ('observer_game_init_ready', {'pid': 123, 'source': 'lifecycle'}) in [
        (item['kind'], item['data']) for item in records]
    assert collector.calls.count('attach') == 1
    assert summary['error'] is None
    row = summary['last_sample']['statistics']['rows']['char_a']
    assert row['damage'] == 120 and row['shield_coverage_start'] == 1
    assert row['observed_damage'] == 0
    assert row['skill_split_reason'] == '观察者就绪前已有伤害'


@pytest.mark.parametrize('require_events', [False, True])
def test_cli_start_failure_reports_readiness_and_optionally_continues_snapshots(tmp_path, monkeypatch,
                                                                            require_events):
    stub_git_and_clock(monkeypatch)
    calls = []

    class MissingFrida(LiveObserver):
        def start(self):
            raise ModuleNotFoundError('optional frida missing')

    def factory(adb, serial, **kwargs):
        return CollectionSession(adb, serial, collector=Collector(calls),
                                 observer_factory=MissingFrida, **kwargs)

    args = options(tmp_path)
    args.require_events = require_events
    if require_events:
        with pytest.raises(RuntimeError, match='optional frida missing'):
            capture_cli.run_capture(args, session_factory=factory)
    else:
        capture_cli.run_capture(args, session_factory=factory)
    summary = json.loads((args.output_dir / 'summary.json').read_text(encoding='utf-8'))
    if require_events:
        assert not (args.output_dir / 'READY.json').exists()
        assert summary['samples'] == 0 and 'optional frida missing' in summary['error']
    else:
        ready = json.loads((args.output_dir / 'READY.json').read_text(encoding='utf-8'))
        assert ready['event_status'] == 'failed' and ready['event_error'] == 'optional frida missing'
        assert summary['error'] is None and summary['samples'] == 1
        assert summary['last_sample']['event_status'] == 'failed'
        row = summary['last_sample']['statistics']['rows']['char_a']
        assert row['damage'] == 100 and row['shield_absorbed'] is None
    assert calls[-1] == 'collector_close'


def test_normal_cli_continues_after_event_gap(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    Session.failed = True
    args = options(tmp_path)
    args.require_events = False
    summary = capture_cli.run_capture(args, session_factory=Session)
    assert summary['error'] is None
    assert summary['last_sample']['event_status'] == 'gap'
    assert Session.instances[-1].closed


@pytest.mark.parametrize('flags, remote', [([], '127.0.0.1:27043'),
                                         (['--no-events'], None),
                                         (['--frida-remote', 'custom:1234'], 'custom:1234')])
def test_app_capture_entry_uses_shared_event_policy(tmp_path, monkeypatch, flags, remote):
    import app
    received = []
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--capture-seconds', '1', *flags])
    monkeypatch.setattr(capture_cli, 'run_capture', received.append)

    class Lock:
        def bind(self, _address):
            pass

        def listen(self, _backlog):
            pass

        def close(self):
            pass

    monkeypatch.setattr(app.socket, 'socket', Lock)
    app.main()
    assert received[0].frida_remote == remote
    assert received[0].require_events is False


def test_app_rejects_disabled_events_in_event_acceptance(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--no-events', '--require-events'])
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 2


def test_arkframework_path_selects_pc_for_source_launcher(tmp_path, monkeypatch):
    import app
    import desktop_launcher
    received = []
    monkeypatch.setattr(desktop_launcher, 'launch', received.append)
    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--arkframework-root', str(tmp_path)])
    app.main()
    assert received[0].platform == 'native_pc'
    assert received[0].arkframework_root == tmp_path
    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--platform', 'mumu',
                                           '--arkframework-root', str(tmp_path)])
    with pytest.raises(SystemExit):
        app.main()


def test_optional_replay_failure_does_not_interrupt_headless_pc_capture(tmp_path, monkeypatch):
    from native_pc import arkframework_replay

    stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    args.platform = 'native_pc'
    args.native_game_root = str(tmp_path / 'game')
    args.arkframework_root = tmp_path / 'framework'
    args.frida_remote = None
    args.require_events = False
    calls = []

    class UpdatingReplay(arkframework_replay.RegenerationRecorder):
        def __init__(self, timeline_path, output_dir):
            assert timeline_path == args.arkframework_root / 'battle_timeline.json'
            assert output_dir == args.output_dir / 'arkframework'
            super().__init__(timeline_path, output_dir)

        def poll_if_due(self, now, *, force=False):
            calls.append(force)
            if not force:
                raise ValueError('timeline being rewritten')
            return args.output_dir / 'arkframework' / 'regeneration-fixture.json'

    def session(adb, serial, *, collector, observer_factory, **kwargs):
        assert callable(observer_factory)
        return Session(adb, serial, **kwargs)

    monkeypatch.setattr(arkframework_replay, 'RegenerationRecorder', UpdatingReplay)
    summary = capture_cli.run_capture(args, session_factory=session,
                                      native_pc_observer_factory=lambda *_args: None)
    assert summary['error'] is None and summary['samples'] == 1
    assert calls[0] is False and calls[-1] is True
    audit = [json.loads(line)['kind'] for line in
             (args.output_dir / 'capture.jsonl').read_text(encoding='utf-8').splitlines()]
    assert audit.count('arkframework_timeline_unavailable') == 1
    assert audit.count('arkframework_regeneration_saved') == 1


def test_native_headless_entry_uses_saved_platform_and_allows_event_acceptance(tmp_path, monkeypatch):
    import app
    from connection import ConnectionConfig, ConnectionConfigStore

    game_root = str(tmp_path / 'PC game')
    ConnectionConfigStore(tmp_path / 'runtime' / 'connection.json').save(
        ConnectionConfig(platform='native_pc', native_game_root=game_root))
    received = []
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--capture-seconds', '1'])
    monkeypatch.setattr(capture_cli, 'run_capture', received.append)

    class Lock:
        def bind(self, _address):
            pass

        def listen(self, _backlog):
            pass

        def close(self):
            pass

    monkeypatch.setattr(app.socket, 'socket', Lock)
    app.main()
    assert received[0].platform == 'native_pc'
    assert received[0].native_game_root == game_root
    assert received[0].frida_remote is received[0].adb is received[0].serial is None
    assert received[0].collect_native_actions is True

    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--capture-seconds', '1', '--require-events'])
    app.main()
    assert len(received) == 2 and received[-1].require_events is True


@pytest.mark.parametrize('mode', ['ready', 'failed', 'disabled'])
def test_headless_pc_observer_uses_the_event_acceptance_path(tmp_path, monkeypatch, mode):
    import app
    import native_pc_collector

    clock = stub_git_and_clock(monkeypatch)
    _, collector, observer, snapshot, _, events, calls, committed = native_connection()

    class Diagnostics(SimpleNamespace):
        def as_dict(self):
            return vars(self)

    collector.diagnostics = Diagnostics(platform='native_pc', version_actual='fixture')
    collector.event_capability = {'status': 'unavailable', 'error': 'no observer'}
    collector.connect = lambda **_: None
    collector.close = lambda: calls.append('collector closed')
    collector.locate_lifecycle = lambda: None
    observer.start = lambda: calls.append('observer started')
    observer.stop = lambda: calls.append('observer stopped')
    evidence = tmp_path / 'evidence'

    def provider(connected, remote):
        assert connected is collector and remote is None
        assert not (evidence / 'READY.json').exists()
        calls.append('provider requested')
        if mode == 'failed':
            raise RuntimeError('fixture observer unavailable')
        return observer

    def advance(seconds):
        clock.now += seconds
        snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
        events.queue = [event(1, 2, 1., 20)]

    def create_collector(root, *, collect_actions, event_identity_only=False):
        assert root == str(tmp_path / 'game')
        assert collect_actions is (mode != 'disabled')
        assert event_identity_only is False  # An injected provider retains legacy witnesses.
        return collector

    monkeypatch.setattr(capture_cli.time, 'sleep', advance)
    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', create_collector)
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app.socket, 'socket', lambda: SimpleNamespace(
        bind=lambda _: None, listen=lambda _: None, close=lambda: None))
    monkeypatch.setattr(app.sys, 'argv', [
        'app.py', '--platform', 'native_pc', '--native-game-root', str(tmp_path / 'game'),
        '--capture-seconds', '0.11', '--output-dir', str(evidence), '--require-progress',
        '--no-events' if mode == 'disabled' else '--require-events',
    ])

    result = app.main(native_pc_observer_factory=provider)
    summary = json.loads((evidence / 'summary.json').read_text(encoding='utf-8'))
    assert summary['platform'] == 'native_pc' and summary['frida_remote'] is None
    assert 'collector closed' in calls
    if mode == 'failed':
        assert result == 2 and summary['samples'] == 0
        assert 'fixture observer unavailable' in summary['error']
        assert not (evidence / 'READY.json').exists()
        assert committed == []
        return

    ready = json.loads((evidence / 'READY.json').read_text(encoding='utf-8'))
    assert result is None and summary['error'] is None and summary['samples'] == 2
    row = summary['last_sample']['statistics']['rows']['char_a']
    assert row['damage'] == 20
    if mode == 'ready':
        assert ready['status'] == 'observer_ready'
        assert summary['last_sample']['event_status'] == 'recording'
        assert row['observed_damage'] == 20 and committed == [0, 1]
        assert calls.count('observer started') == calls.count('observer stopped') == 1
    else:
        assert ready['status'] == 'connected'
        assert summary['last_sample']['event_status'] == 'unavailable'
        assert 'provider requested' not in calls and committed == []
