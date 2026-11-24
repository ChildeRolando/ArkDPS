from pathlib import Path
import json
from threading import Event
from types import SimpleNamespace
import time

import pytest

from acceptance import capture_pc_probe_pair as pair


def test_pair_requires_explicit_dual_hook_opt_in_before_any_runtime(tmp_path, monkeypatch):
    binary = tmp_path / 'controlled.dll'
    binary.write_bytes(b'not loaded')
    monkeypatch.setattr(pair, 'native_hook_binary', lambda _: binary)
    args = SimpleNamespace(allow_dual_hooks=False, output_dir=tmp_path / 'evidence')
    def forbidden(*args, **kwargs):
        raise AssertionError('must not start a runtime')
    with pytest.raises(ValueError, match='allow-dual-hooks'):
        pair.run_pair(args, runtime_factory=forbidden, capture=forbidden)
    assert not args.output_dir.exists()


def test_pair_uses_separate_capture_and_state_directories_without_ui_control(tmp_path):
    stop = tmp_path / 'STOP'
    native = pair.capture_arguments(Path('D:/game'), tmp_path / 'native', 'native',
        seconds=300, wait_seconds=120, stop_file=stop, stage='CE-5')
    frida = pair.capture_arguments(Path('D:/game'), tmp_path / 'frida', 'frida',
        seconds=300, wait_seconds=120, stop_file=stop, stage='CE-5')
    assert native.platform == frida.platform == 'native_pc'
    assert native.native_pc_event_backend == 'native' and frida.native_pc_event_backend == 'frida'
    assert native.output_dir != frida.output_dir
    assert native.require_events and native.require_progress
    assert native.frida_remote is None and native.maa_job is None
    assert native.stop_file == frida.stop_file == stop
    assert native.baseline_timeout == frida.baseline_timeout == 120


@pytest.mark.parametrize('native_failure', [False, True])
@pytest.mark.parametrize('bootstrap_mode', ['fresh', 'reuse', 'preload'])
def test_pair_releases_native_before_frida_and_keeps_frida_until_exit_on_failure(
        tmp_path, monkeypatch, native_failure, bootstrap_mode):
    reuse_bootstrap, preload_native = bootstrap_mode == 'reuse', bootstrap_mode == 'preload'
    events = []
    both_ready = Event()
    binary = tmp_path / 'controlled.dll'
    binary.write_bytes(b'not loaded')
    monkeypatch.setattr(pair, 'native_hook_binary', lambda _: binary)
    monkeypatch.setattr(pair, 'candidate_identity', lambda: {'head': 'controlled'})
    def print_status(message, *args, **kwargs):
        if message.startswith('BOTH_READY'):
            both_ready.set()
    monkeypatch.setattr(pair, 'print', print_status, raising=False)
    prepared_instances = []

    class Prepared:
        def __init__(self, game_root, *, root, runtime_dir):
            assert runtime_dir == tmp_path / 'pair/native-preload'
            self.process_instance = None
            self.claimed = False
            prepared_instances.append(self)
        def prepare(self, instance):
            assert self.cancelled() is False
            self.commit(lambda: events.append('ordinary-native-preload'))
            self.process_instance = instance
        def close(self):
            assert self.claimed
            events.append('prepared-close-claimed-noop')
        def diagnostics(self):
            return dict(prepared=self.process_instance is not None, claimed=self.claimed)
    monkeypatch.setattr(pair, '_prepared_native_hooks', Prepared)

    class Native:
        def __init__(self, collector, remote, **kwargs):
            self.hook_disable_confirmed = False
            if preload_native:
                assert kwargs['prepared'] is prepared_instances[0]
                assert kwargs['prepared'].process_instance == (42, 100)
                kwargs['prepared'].claimed = True
            else:
                assert 'prepared' not in kwargs
        def stop(self):
            events.append('native-stop')
            if native_failure:
                raise RuntimeError('controlled disable failure')
            self.hook_disable_confirmed = True
    monkeypatch.setattr(pair, 'NativeHookEventProbe', Native)
    bootstrap_dir = tmp_path / 'previous-runtime'
    if reuse_bootstrap:
        (bootstrap_dir / 'native-pc-frida').mkdir(parents=True)
        (bootstrap_dir / 'native-pc-frida/lease.json').write_text('{"controlled":"previous lease"}')

    class Runtime:
        def __init__(self, *args, **kwargs):
            assert kwargs['runtime_dir'] == (bootstrap_dir if reuse_bootstrap else tmp_path / 'pair/frida-runtime')
            self.prepare_process = kwargs.get('prepare_process')
            self._cancel = Event()
            assert callable(self.prepare_process) is preload_native
        def _commit(self, operation):
            assert not self._cancel.is_set()
            return operation()
        def start(self):
            events.append('runtime-start')
        def wait_ready(self, *args, **kwargs):
            if self.prepare_process is not None:
                self.prepare_process((42, 100))
            return dict(pid=42, created=100)
        def describe(self):
            return dict(prepared_process_instance=[42, 100] if preload_native else None)
        def observer_factory(self, collector, remote):
            return SimpleNamespace(stop=lambda: events.append('frida-stop'))
        def close(self):
            events.append('runtime-close')

    def captured(options, *, native_pc_observer_factory, state_dir, native_pc_bootstrap,
                 before_session_close, after_session_close, session_factory, baseline_poll_seconds):
        assert isinstance(native_pc_bootstrap, Runtime)
        assert session_factory is pair.PrefixAuditSession and baseline_poll_seconds == .01
        label = options.native_pc_event_backend
        collector = SimpleNamespace(process=SimpleNamespace(process_instance=(42, 100)))
        observer = native_pc_observer_factory(collector, None)
        session = SimpleNamespace(observer=observer)
        options.output_dir.mkdir()
        (options.output_dir / 'READY.json').write_text('{}')
        assert both_ready.wait(3), 'pair did not reach both-ready'
        if label == 'native':
            deadline = time.monotonic() + 3
            while not options.stop_file.exists():
                assert time.monotonic() < deadline
                time.sleep(.001)
        before_session_close(session)
        try:
            observer.stop()
        except Exception:
            after_session_close(session, False)
            raise
        after_session_close(session, True)
        return dict(error=None, samples=2, process_instance=[42, 100])

    def protected_wait(instance):
        assert instance == (42, 100)
        assert 'frida-stop' not in events and 'runtime-close' not in events
        events.append('game-exit-confirmed')

    args = SimpleNamespace(allow_dual_hooks=True, output_dir=tmp_path / 'pair', game_root=tmp_path / 'game',
                           seconds=1, wait_seconds=3, stage='CE-5',
                           bootstrap_dir=bootstrap_dir if reuse_bootstrap else None, preload_native=preload_native)
    if native_failure:
        with pytest.raises(RuntimeError, match='controlled disable failure'):
            pair.run_pair(args, runtime_factory=Runtime, capture=captured, on_unsafe_wait=protected_wait)
    else:
        result = pair.run_pair(args, runtime_factory=Runtime, capture=captured, on_unsafe_wait=protected_wait)
        assert result['status'] == 'captured'
    assert events.index('native-stop') < events.index('frida-stop') < events.index('runtime-close')
    if native_failure:
        assert events.index('native-stop') < events.index('game-exit-confirmed') < events.index('frida-stop')
    result = json.loads((args.output_dir / 'pair-receipt.json').read_text(encoding='utf-8'))
    assert result['native_hook_disable_confirmed'] is not native_failure
    assert result['protected_game_exit_wait'] is native_failure
    assert result['game_exit_confirmed'] is native_failure
    assert result['bootstrap_reused'] is reuse_bootstrap
    assert result['native_preload_requested'] is result['native_preloaded'] is preload_native
    if preload_native:
        assert events.index('ordinary-native-preload') < events.index('native-stop')
        assert events.index('frida-stop') < events.index('prepared-close-claimed-noop') < events.index('runtime-close')
        assert result['native_preload_mechanism'] == 'ordinary_loader'
        assert result['native_preload_requires_frida_initialization'] is False
    if reuse_bootstrap:
        assert (bootstrap_dir / 'native-pc-frida/lease.json').read_text() == '{"controlled":"previous lease"}'


def test_explicit_bootstrap_directory_requires_previous_lease_before_starting(tmp_path, monkeypatch):
    binary = tmp_path / 'controlled.dll'
    binary.write_bytes(b'not loaded')
    monkeypatch.setattr(pair, 'native_hook_binary', lambda _: binary)
    args = SimpleNamespace(allow_dual_hooks=True, output_dir=tmp_path / 'pair', bootstrap_dir=tmp_path / 'missing')
    with pytest.raises(ValueError, match='lease.json'):
        pair.run_pair(args, runtime_factory=lambda *_args, **_kwargs: pytest.fail('must not start'))
    assert not args.output_dir.exists()


def test_pair_cli_accepts_explicit_existing_bootstrap_root(tmp_path, monkeypatch):
    received = []
    monkeypatch.setattr(pair, 'run_pair', received.append)
    assert pair.main(['--game-root', str(tmp_path / 'game'), '--output-dir', str(tmp_path / 'new'),
                      '--bootstrap-dir', str(tmp_path / 'old'), '--allow-dual-hooks']) == 0
    assert received[0].bootstrap_dir == tmp_path / 'old'


def test_preload_cli_rejects_warm_bootstrap_combination(tmp_path, monkeypatch):
    monkeypatch.setattr(pair, 'run_pair', lambda _: pytest.fail('conflicting flags must not start'))
    with pytest.raises(SystemExit) as caught:
        pair.main(['--game-root', str(tmp_path / 'game'), '--output-dir', str(tmp_path / 'new'),
                   '--bootstrap-dir', str(tmp_path / 'old'), '--preload-native', '--allow-dual-hooks'])
    assert caught.value.code == 2


@pytest.mark.parametrize('pending_cleanup', [False, True])
def test_unclaimed_preparation_cleanup_is_owned_by_pair_and_pending_owner_is_retained(tmp_path, monkeypatch, pending_cleanup):
    from native_pc.frida_runtime import RuntimeFailure
    events, prepared_instances = [], []
    binary = tmp_path / 'controlled.dll'
    binary.write_bytes(b'not loaded')
    monkeypatch.setattr(pair, 'native_hook_binary', lambda _: binary)
    monkeypatch.setattr(pair, 'candidate_identity', lambda: {'head': 'controlled'})
    monkeypatch.setattr(pair, '_RETAINED_PREPARATIONS', [])
    class Prepared:
        def __init__(self, *args, **kwargs):
            self.process_instance = None
            self.claimed = False
            prepared_instances.append(self)
        def prepare(self, instance):
            self.process_instance = instance  # A selected birth is not a verified load.
            events.append('prepare')
            raise RuntimeFailure('ordinary_prepare_failed', 'controlled ordinary preload failure')
        def close(self):
            events.append('prepared-close')
            if pending_cleanup:
                raise RuntimeError('controlled ordinary loader still pending')
        def diagnostics(self):
            return dict(prepared=False, claimed=False, cleanup_pending=pending_cleanup)
    monkeypatch.setattr(pair, '_prepared_native_hooks', Prepared)
    class Runtime:
        def __init__(self, *args, **kwargs):
            self.prepare_process = kwargs['prepare_process']
            self._cancel = Event()
        def _commit(self, operation):
            assert not self._cancel.is_set()
            return operation()
        def start(self):
            pass
        def wait_ready(self, *args, **kwargs):
            self.prepare_process((42, 100))
        def describe(self):
            return dict(prepared_process_instance=None)
        def close(self):
            events.append('runtime-close')
    args = SimpleNamespace(allow_dual_hooks=True, output_dir=tmp_path / 'pair', game_root=tmp_path / 'game',
                           seconds=1, wait_seconds=3, stage='CE-5', preload_native=True)
    with pytest.raises(RuntimeError, match='controlled ordinary preload failure'):
        pair.run_pair(args, runtime_factory=Runtime, capture=lambda *_args, **_kwargs: pytest.fail('no Gadget ready'))
    assert events[0] == 'prepare' and events[-1] == 'runtime-close'
    assert events.count('prepared-close') == (2 if pending_cleanup else 1)
    assert pair._RETAINED_PREPARATIONS == (prepared_instances if pending_cleanup else [])
    result = json.loads((args.output_dir / 'pair-receipt.json').read_text(encoding='utf-8'))
    assert result['native_preloaded'] is False
    assert bool(result['native_preload_cleanup_error']) is pending_cleanup


@pytest.mark.parametrize('cancel_source', ['parent', 'stop_file', 'stop_after_load'])
def test_preload_binds_parent_cancellation_and_mutation_gate_before_prepare(tmp_path, monkeypatch, cancel_source):
    binary = tmp_path / 'controlled.dll'
    binary.write_bytes(b'not loaded')
    monkeypatch.setattr(pair, 'native_hook_binary', lambda _: binary)
    monkeypatch.setattr(pair, 'candidate_identity', lambda: {'head': 'controlled'})
    preparations = []
    class Prepared:
        def __init__(self, *args, **kwargs):
            preparations.append(self)
        def prepare(self, instance):
            assert cancel_source == 'stop_after_load'
            (tmp_path / 'pair/STOP').touch()
            return self
        def close(self):
            assert self.cancelled() is True
            assert self.commit == runtime_instance[0]._commit
        def diagnostics(self):
            return dict(prepared=False)
    monkeypatch.setattr(pair, '_prepared_native_hooks', Prepared)
    runtime_instance = []
    class Runtime:
        def __init__(self, *args, **kwargs):
            runtime_instance.append(self)
            self._cancel = Event()
            self.prepare_process = kwargs['prepare_process']
        def _commit(self, operation):
            pytest.fail('cancelled preparation must not reach mutation commit')
        def start(self):
            pass
        def wait_ready(self, *args, **kwargs):
            if cancel_source == 'parent':
                self._cancel.set()
            elif cancel_source == 'stop_file':
                (tmp_path / 'pair/STOP').touch()
            self.prepare_process((42, 100))
        def describe(self):
            return dict(prepared_process_instance=None)
        def close(self):
            pass
    args = SimpleNamespace(allow_dual_hooks=True, output_dir=tmp_path / 'pair', game_root=tmp_path / 'game',
                           seconds=1, wait_seconds=3, stage='CE-5', preload_native=True)
    with pytest.raises(RuntimeError, match='cancelled (before|after) process preparation'):
        pair.run_pair(args, runtime_factory=Runtime, capture=lambda *_args, **_kwargs: pytest.fail('no ready capture'))


def test_capture_cleanup_gate_can_retain_session_and_reports_actual_outcome(tmp_path, monkeypatch):
    import capture_cli
    from test_capture_cli import Session, options, stub_git_and_clock
    stub_git_and_clock(monkeypatch)
    received = []
    args = options(tmp_path)
    with pytest.raises(RuntimeError, match='cleanup deferred'):
        capture_cli.run_capture(args, session_factory=Session,
            before_session_close=lambda session: False,
            after_session_close=lambda session, succeeded: received.append((session, succeeded)))
    assert not Session.instances[-1].closed
    assert received == [(Session.instances[-1], False)]
    journal = [json.loads(line) for line in (args.output_dir / 'capture.jsonl').read_text(encoding='utf-8').splitlines()]
    assert any(record['kind'] == 'cleanup_deferred' for record in journal)


def test_capture_cleanup_callback_observes_success_after_close(tmp_path, monkeypatch):
    import capture_cli
    from test_capture_cli import Session, options, stub_git_and_clock
    stub_git_and_clock(monkeypatch)
    received = []
    capture_cli.run_capture(options(tmp_path), session_factory=Session,
        after_session_close=lambda session, succeeded: received.append((session.closed, succeeded)))
    assert received == [(True, True)]


@pytest.mark.parametrize('backend', ['native', 'frida'])
def test_capture_borrows_parent_bootstrap_and_retries_temporary_missing_module(tmp_path, monkeypatch, backend):
    import capture_cli
    from collector import SampleUnavailable
    from test_capture_cli import Session, options, stub_git_and_clock
    clock = stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    args.platform, args.native_game_root, args.frida_remote = 'native_pc', str(tmp_path / 'game'), None
    args.native_pc_event_backend = backend
    calls = []
    observer_factory = lambda *_args: None

    class Borrowed:
        is_failed = False
        def start(self):
            pytest.fail('borrowed startup owner must not be started again')
        def wait_ready(self, timeout, cancelled=None):
            calls.append('wait_ready')
        def describe(self):
            return dict(status='ready', borrowed_fixture=True)
        def close(self):
            pytest.fail('capture must not close its borrowed parent startup owner')

    class PcSession(Session):
        def __init__(self, adb, serial, *, collector, observer_factory, **kwargs):
            assert callable(observer_factory)
            self.collector = collector
            self.attempts = 0
            super().__init__(adb, serial, **kwargs)
        def connect(self):
            self.attempts += 1
            calls.append('connect')
            if self.attempts == 1:
                assert not (args.output_dir / 'READY.json').exists()
                self.collector.diagnostics.code = 'native_unavailable'
                self.collector.diagnostics.details = {'reason': 'module_not_unique'}
                assert self.collector.process is None
                raise SampleUnavailable('module_not_unique: cold-start GameAssembly is not yet enumerated')
            super().connect()
        def close(self):
            calls.append('session_close')
            super().close()

    result = capture_cli.run_capture(args, session_factory=PcSession,
        native_pc_observer_factory=observer_factory, native_pc_bootstrap=Borrowed())
    assert result['error'] is None
    assert calls == ['wait_ready', 'connect', 'connect', 'session_close']
    assert clock.now >= .1
    records = [json.loads(line) for line in
               (args.output_dir / 'capture.jsonl').read_text(encoding='utf-8').splitlines()]
    assert any(record['kind'] == 'native_pc_bootstrap' and
               record['data'].get('borrowed_fixture') is True for record in records)
    assert (args.output_dir / 'READY.json').exists()
