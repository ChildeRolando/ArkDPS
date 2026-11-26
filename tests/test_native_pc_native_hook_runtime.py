"""Native startup tests use fake process births and preparations, never a game."""

from pathlib import Path
import subprocess
import sys
from threading import Event
import time
from types import SimpleNamespace

import pytest

from event_observer import ObserverStatus
from native_pc import native_hook_runtime as module
from native_pc.native_hook_runtime import NativeHookRuntime, RuntimeFailure


class Api:
    def __init__(self, root):
        self.root, self.rows, self.opened, self.closed, self.handles = root, {}, [], [], {}
        self.denied = set()

    def named_processes(self):
        return list(self.rows)

    def open_process(self, pid, rights):
        if pid in self.denied:
            raise module.access.ApiFailure('OpenProcess', 5)
        if pid not in self.rows:
            raise module.access.ApiFailure('OpenProcess', 87)
        handle = object()
        self.handles[handle] = self.rows[pid]
        self.opened.append(handle)
        return handle

    def process_times(self, handle):
        return self.handles[handle].created, 0

    def image_path(self, handle):
        return self.handles[handle].path

    def exit_code(self, handle):
        return 259 if self.handles[handle].alive else 0

    def close(self, handle):
        assert handle not in self.closed
        self.closed.append(handle)


class Preparations:
    def __init__(self):
        self.instances, self.loads = [], []
        self.failure = self.before_commit = self.after_commit = None
        self.source_build = lambda: module.access.BUILD_ID
        self.fail_cleanup = False

    def __call__(self, game_root, *, root, runtime_dir, material):
        owner = self

        class Prepared:
            claimed, _prepared, _closed = False, False, False
            expires_at = None

            def __init__(self):
                self.process_instance = None
                self.source_build = None
                self.close_calls = 0
                self.root, self.runtime_dir = root, runtime_dir
                self.material = material

            def prepare(self, birth):
                self.process_instance = birth
                self.source_build = owner.source_build()
                if owner.before_commit:
                    owner.before_commit(self)
                if owner.failure:
                    raise RuntimeFailure('owned_prepare_failed', owner.failure)
                self.commit(lambda: owner.loads.append(birth))
                if owner.after_commit:
                    owner.after_commit(self)
                self._prepared = True
                self.expires_at = time.monotonic() + 120

            def diagnostics(self):
                return dict(prepared=self._prepared, claimed=self.claimed, closed=self._closed,
                            process_instance=self.process_instance, expires_at=self.expires_at,
                            source_build=self.source_build,
                            loader=dict(submitted=bool(self.process_instance), verification_complete=self._prepared))

            def close(self):
                if self.claimed:
                    return
                self.close_calls += 1
                if owner.fail_cleanup:
                    raise RuntimeFailure('cleanup_pending', 'owned loader still pending')
                self._closed = True

        prepared = Prepared()
        self.instances.append(prepared)
        return prepared


def wait_for(runtime, condition):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        report = runtime.describe()
        if condition(report):
            return report
        time.sleep(.005)
    raise AssertionError(runtime.describe())


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'owned-game-path'
    root.mkdir()
    api, factory, runtimes = Api(root), Preparations(), []
    material, material_calls = object(), []
    def make_material(source):
        material_calls.append(source)
        return material
    build = {'known_build': True, 'amd64': True, 'source_build': module.access.BUILD_ID}
    factory.source_build = lambda: build.get('source_build')

    def make(**options):
        runtime = NativeHookRuntime(root, runtime_dir=tmp_path / 'state', collector_root=tmp_path / 'source',
            api=api, admin_check=options.pop('admin_check', lambda: True), prepared_factory=factory,
            material_factory=options.pop('material_factory', make_material),
            build_check=lambda _: (dict(build), []), **options)
        runtimes.append(runtime)
        return runtime

    def row(created, *, correct=True):
        return SimpleNamespace(created=created, alive=True,
                               path=root / ('Arknights.exe' if correct else 'Other.exe'))

    yield SimpleNamespace(root=root, api=api, factory=factory, runtimes=runtimes, build=build, make=make, row=row,
                          material=material, material_calls=material_calls)
    factory.fail_cleanup = False
    for runtime in runtimes:
        for observer in tuple(runtime._observers):
            observer.hook_disable_confirmed = True
            monkeypatch.setattr(module.NativeHookEventProbe, 'stop', lambda self: setattr(self, 'cleanup_errors', {}))
            observer.stop()
        runtime.close()
    assert set(api.opened) == set(api.closed)


def arm(env):
    runtime = env.make().start()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    return runtime


def ready(env, runtime, pid=100):
    env.api.rows[pid] = env.row(runtime.describe()['armed_filetime'] + pid)
    return runtime.wait_ready(3)


@pytest.mark.parametrize('source_build', list(module.access.KNOWN_BUILDS))
def test_only_new_exact_birth_is_prepared_and_close_releases_unclaimed(env, source_build):
    env.build['source_build'] = source_build
    env.api.rows[3] = env.row(1)
    runtime = arm(env)
    assert env.factory.loads == []
    env.api.rows.clear()
    lease = ready(env, runtime)
    assert lease == dict(pid=100, created=runtime.describe()['armed_filetime'] + 100, source_build=source_build)
    assert env.factory.loads == [(lease['pid'], lease['created'])]
    assert runtime.describe()['frida_required'] is False
    assert runtime._alive(lease) is True
    assert not runtime.describe()['pending_loader']
    runtime.close()
    assert env.factory.instances[0]._closed


def test_existing_birth_and_wrong_path_are_not_loaded(env):
    runtime = arm(env)
    armed = runtime.describe()['armed_filetime']
    env.api.rows[1] = env.row(armed)
    env.api.rows[2] = env.row(armed + 100, correct=False)
    wait_for(runtime, lambda r: r['code'] == 'restart_required')
    assert not env.factory.instances


def test_ambiguous_exact_path_waits_then_selects_remaining_new_birth(env):
    runtime = arm(env)
    armed = runtime.describe()['armed_filetime']
    env.api.rows.update({1: env.row(armed + 1), 2: env.row(armed + 2)})
    wait_for(runtime, lambda r: r['code'] == 'ambiguous_process')
    assert not env.factory.instances
    del env.api.rows[1]
    assert runtime.wait_ready(3)['pid'] == 2


def test_unqueryable_named_process_blocks_unique_selection(env):
    runtime = arm(env)
    armed = runtime.describe()['armed_filetime']
    env.api.rows.update({1: env.row(armed + 1), 2: env.row(armed + 2)})
    env.api.denied.add(2)
    wait_for(runtime, lambda r: r['code'] == 'process_query_pending')
    assert not env.factory.instances
    env.api.denied.clear()
    del env.api.rows[2]
    assert runtime.wait_ready(3)['pid'] == 1


def test_prepare_failure_is_not_reloaded_by_retry_same_birth(env):
    runtime = arm(env)
    env.factory.failure = 'owned failure'
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    wait_for(runtime, lambda r: r['status'] == 'process_failed')
    env.factory.failure = None
    runtime.retry()
    time.sleep(.08)
    assert len(env.factory.instances) == 1
    previous_fence = runtime.describe()['armed_filetime']
    env.api.rows[1] = env.row(previous_fence + 2)  # PID reuse while old owner retires is not yet admissible.
    wait_for(runtime, lambda r: r['lease'] is None and r['armed_filetime'] > previous_fence)
    assert len(env.factory.instances) == 1
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 2)  # A genuinely newer birth.
    wait_for(runtime, lambda r: r['status'] == 'ready')
    assert runtime.wait_ready(3)['created'] == runtime.describe()['armed_filetime'] + 2
    assert len(env.factory.instances) == 2


def test_permission_preflight_never_elevates_or_prepares(env):
    runtime = env.make(admin_check=lambda: False).start()
    wait_for(runtime, lambda r: r['status'] == 'failed')
    assert runtime.is_failed and runtime.code == 'native_permission_denied'
    assert not env.api.opened and not env.factory.instances


@pytest.mark.parametrize('field,value', [
    ('known_build', False), ('known_build', 1), ('amd64', False), ('amd64', 1),
    ('source_build', 'unregistered-build'), ('source_build', None), ('source_build', []),
])
def test_only_registered_pc_fingerprints_are_admitted(env, field, value):
    env.build[field] = value
    runtime = env.make().start()
    wait_for(runtime, lambda r: r['status'] == 'failed')
    assert runtime.code == 'native_unsupported_build'
    assert not env.factory.instances and not env.material_calls


def test_cancel_before_windows_commit_submits_nothing(env):
    entered, release = Event(), Event()
    env.factory.before_commit = lambda _: (entered.set(), release.wait(3))
    runtime = arm(env)
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    assert entered.wait(3)
    runtime.cancel()
    release.set()
    runtime.close()
    assert not env.factory.loads and env.factory.instances[0]._closed
    assert runtime.describe()['status'] == 'closed'


def test_commit_rechecks_birth_path_uniqueness_and_build(env):
    def change(prepared):
        env.api.rows[prepared.process_instance[0]].path = env.root / 'Other.exe'
    env.factory.before_commit = change
    runtime = arm(env)
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    wait_for(runtime, lambda r: r['status'] == 'process_failed')
    assert not env.factory.loads


@pytest.mark.parametrize('source_build', list(module.access.KNOWN_BUILDS))
def test_commit_rejects_another_prepared_build_even_if_file_signature_is_unchanged(env, source_build):
    env.build['source_build'] = source_build
    other_build = next(build for build in module.access.KNOWN_BUILDS if build != source_build)
    runtime = arm(env)
    signature = runtime._files()
    # The preparation's independent verification observes another registered
    # build without relying on a file-stat change to reveal the disagreement.
    env.factory.source_build = lambda: other_build
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    report = wait_for(runtime, lambda r: r['status'] == 'process_failed')
    assert report['code'] == 'native_build_changed' and runtime._files() == signature
    assert report['lease']['source_build'] == source_build and report['ready_generation'] == 0
    assert not env.factory.loads and env.factory.instances[0]._closed


@pytest.mark.parametrize('source_build', list(module.access.KNOWN_BUILDS))
def test_ready_commit_rejects_prepared_build_change_after_windows_submission(env, source_build):
    env.build['source_build'] = source_build
    other_build = next(build for build in module.access.KNOWN_BUILDS if build != source_build)
    runtime = arm(env)
    env.factory.after_commit = lambda prepared: setattr(prepared, 'source_build', other_build)
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    report = wait_for(runtime, lambda r: r['status'] == 'process_failed')
    assert report['code'] == 'native_build_changed' and report['ready_generation'] == 0
    assert env.factory.loads == [(1, report['lease']['created'])]
    assert env.factory.instances[0]._closed


def test_alive_never_uses_recycled_pid_or_wrong_path(env):
    runtime = arm(env)
    lease = ready(env, runtime)
    env.api.rows[100] = env.row(lease['created'] + 1)
    assert runtime._alive(lease) is False
    env.api.rows[100] = env.row(lease['created'], correct=False)
    assert runtime._alive(lease) is False
    assert runtime._alive({'pid': True, 'created': lease['created']}) is False


def test_pending_preparation_cleanup_retains_owner_and_blocks_close(env):
    runtime = arm(env)
    env.factory.failure = 'owned loader pending'
    env.factory.fail_cleanup = True
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    wait_for(runtime, lambda r: r['status'] == 'cleanup_pending')
    prepared = runtime._prepared
    with pytest.raises(RuntimeFailure, match='pending'):
        runtime.close()
    assert runtime._prepared is prepared and not prepared._closed
    env.factory.fail_cleanup = False
    runtime.close()
    assert runtime._prepared is None and prepared._closed


def test_expired_unclaimed_preparation_requires_new_birth(env):
    runtime = arm(env)
    ready(env, runtime)
    prepared = runtime._prepared
    prepared.expires_at = time.monotonic() - 1
    wait_for(runtime, lambda r: r['code'] == 'preparation_expired')
    assert prepared._closed
    runtime.retry()
    time.sleep(.05)
    assert len(env.factory.instances) == 1


def test_observer_owner_blocks_close_and_failed_stop_retains_owner(env, monkeypatch):
    runtime = arm(env)
    lease = ready(env, runtime)
    collector = SimpleNamespace(process=SimpleNamespace(pid=lease['pid'], process_instance=(lease['pid'], lease['created'])))
    observer = runtime.observer_factory(collector)
    prepared = runtime._prepared
    assert observer.prepared is prepared
    with pytest.raises(RuntimeFailure, match='观察者'):
        runtime.close()
    assert runtime._prepared is prepared and observer in runtime._observers
    monkeypatch.setattr(module.NativeHookEventProbe, 'stop', lambda self: setattr(self, 'cleanup_errors', {'hooks': 'pending'}))
    with pytest.raises(RuntimeFailure, match='保留所有权'):
        observer.stop()
    assert observer in runtime._observers
    monkeypatch.setattr(module.NativeHookEventProbe, 'stop', lambda self: setattr(self, 'cleanup_errors', {}))
    observer.hook_disable_confirmed = True
    observer.stop()
    runtime.close()
    assert prepared._closed


def test_adopted_resources_are_only_released_by_observer(env, monkeypatch):
    runtime = arm(env)
    lease = ready(env, runtime)
    collector = SimpleNamespace(process=SimpleNamespace(pid=lease['pid'], process_instance=(lease['pid'], lease['created'])))
    observer = runtime.observer_factory(collector)

    def start(self):
        self.prepared.claimed = True
        self.hook_disable_confirmed = False
        self.status = ObserverStatus.READY

    def stop(self):
        self.hook_disable_confirmed = True
        self.cleanup_errors = {}
        self.status = ObserverStatus.STOPPED

    monkeypatch.setattr(module.NativeHookEventProbe, 'start', start)
    monkeypatch.setattr(module.NativeHookEventProbe, 'stop', stop)
    observer.start()
    prepared = runtime._prepared
    observer.stop()
    assert runtime.describe()['status'] == 'process_failed'
    runtime.close()
    assert prepared.close_calls == 0  # Already transferred; startup must not clean observer resources.


def test_sampling_rejection_keeps_the_claimed_process_input_ready(env, monkeypatch):
    from collection_session import CollectionSession
    runtime = arm(env)
    lease = ready(env, runtime)
    collector = SimpleNamespace(
        diagnostics=SimpleNamespace(platform='native_pc'),
        process=SimpleNamespace(pid=lease['pid'], process_instance=(lease['pid'], lease['created'])))
    observer = runtime.observer_factory(collector)
    stopped = []
    def start(self):
        self.prepared.claimed = True
        self.hook_disable_confirmed = False
        self.status = ObserverStatus.READY
    def stop(self):
        stopped.append(self)
        self.hook_disable_confirmed = True
        self.cleanup_errors = {}
        self.status = ObserverStatus.STOPPED
    monkeypatch.setattr(module.NativeHookEventProbe, 'start', start)
    monkeypatch.setattr(module.NativeHookEventProbe, 'stop', stop)
    observer.start()
    session = CollectionSession(None, None, collector=collector, frida_remote=None,
                                observer_factory=runtime.observer_factory)
    session.runtime = SimpleNamespace(observer_retired=lambda _observer: None)
    session.observer, session._observer_generation = observer, 53
    try:
        for generation in (54, 54, 55):
            retried, preserved = session._prepare_battle_observer(SimpleNamespace(
                generation=generation, events={'error': 'temporary sampling scope rejection'}))
            assert not retried and preserved is observer
        assert not stopped and observer in runtime._observers
        assert runtime.describe()['status'] == 'ready'
        assert len(env.factory.loads) == 1 and observer.prepared.claimed
        session._retire_observer()
        assert stopped == [observer] and observer not in runtime._observers
        assert runtime.describe()['code'] == 'restart_required'
    finally:
        if observer in runtime._observers:
            observer.stop()
        runtime.close()


def test_wait_ready_checks_external_cancel_even_if_already_ready(env):
    runtime = arm(env)
    ready(env, runtime)
    with pytest.raises(InterruptedError):
        runtime.wait_ready(0, cancelled=lambda: True)


@pytest.mark.parametrize('timeout', [-1, True, float('inf'), float('nan')])
def test_wait_timeout_is_bounded(env, timeout):
    runtime = env.make()
    with pytest.raises(ValueError):
        runtime.wait_ready(timeout)


def test_import_does_not_load_frida_or_its_runtime():
    root = Path(__file__).resolve().parents[1]
    code = "import native_pc.native_hook_runtime,sys; assert 'frida' not in sys.modules; assert 'native_pc.frida_runtime' not in sys.modules"
    result = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_startup_trace_runs_outside_condition_and_preserves_single_load(env):
    records = []
    runtime = env.make()
    def trace(record):
        assert not runtime._condition._is_owned()
        records.append(record)
    runtime.startup_trace = trace
    runtime.start()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    lease = ready(env, runtime)
    runtime.close()
    assert [r['event'] for r in records] == ['native_prepare_started', 'native_prepare_completed', 'native_prepare_closed']
    assert all(r['pid'] == lease['pid'] and r['created_filetime'] == lease['created'] for r in records)
    assert all(r['source_build'] == module.access.BUILD_ID for r in records)
    assert all(r['armed_filetime'] == runtime.describe()['armed_filetime'] for r in records)
    assert all(type(r['armed_filetime']) is int and r['armed_filetime'] > 0 for r in records)
    assert all(type(r['watch_generation']) is int and r['watch_generation'] == 1 for r in records)
    assert [r['material_ready'] for r in records] == [True, True, False]
    assert records[-1]['cleanup_ok'] is True
    assert len(env.factory.instances) == len(env.factory.loads) == 1


@pytest.mark.parametrize('callback_failure', [RuntimeError, KeyboardInterrupt])
def test_trace_callback_failure_does_not_drop_owner_or_retry_load(env, callback_failure):
    def broken(_record):
        raise callback_failure('do-not-echo-auth-or-source')
    runtime = env.make(startup_trace=broken).start()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    lease = ready(env, runtime)
    prepared = runtime._prepared
    assert prepared is env.factory.instances[0] and not prepared._closed
    assert runtime.wait_ready(0) == lease
    runtime.retry()
    time.sleep(.05)
    assert len(env.factory.instances) == len(env.factory.loads) == 1
    runtime.close()
    assert prepared._closed and runtime._prepared is None


def test_failure_trace_keeps_safe_loader_facts_before_cleanup_not_arbitrary_text(env):
    records = []
    def diagnostic(prepared):
        original = prepared.diagnostics
        def facts():
            raw = original()
            raw.update(source_sha256='a' * 64, dll_sha256='b' * 64, token='secret-token',
                       source='private-js', config='private-config', fullpath='private-path')
            raw['loader'].update(stage='verifying', operation='find_module', check='loaded_component',
                thread_exit_code=0xffffffff, module_found=False, module_query_succeeded=True,
                verification_alive=True, process_exit_code=259, path_utf16_units=220,
                path_utf16_bytes=440, path_buffer_bytes=442, return_matches=None,
                module_base='0x123456789', module_base_low32=None, fullpath='private-path',
                token='secret-token', timing_seconds=dict(submitted=.01, thread_completed=.02, auth='private'))
            return raw
        prepared.diagnostics = facts
    def trace(record):
        if record['event'] == 'native_prepare_failed':
            assert env.factory.instances[0].close_calls == 0
        records.append(record)
    env.factory.before_commit = diagnostic
    env.factory.failure = 'private-token/source/path exception text'
    runtime = env.make(startup_trace=trace).start()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    wait_for(runtime, lambda r: r['status'] == 'process_failed')
    failed = next(r for r in records if r['event'] == 'native_prepare_failed')
    assert failed['source_sha256'] == 'a' * 64 and failed['dll_sha256'] == 'b' * 64
    assert failed['loader']['thread_exit_code'] == 0xffffffff
    assert failed['loader']['module_found'] is False and failed['loader']['return_matches'] is None
    assert failed['loader']['path_utf16_units'] == 220
    assert 'private' not in repr(records) and 'secret-token' not in repr(records)
    assert 'module_base' not in failed['loader']
    assert env.factory.instances[0]._closed and runtime._prepared is None
    runtime.retry()
    time.sleep(.05)
    assert len(env.factory.instances) == 1


def test_diagnostic_read_failure_is_unknown_and_never_causes_cleanup_of_success(env):
    records = []
    def break_diagnostic(prepared):
        def fail():
            raise RuntimeError('private diagnostic read failure')
        prepared.diagnostics = fail
    env.factory.before_commit = break_diagnostic
    runtime = env.make(startup_trace=records.append).start()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    ready(env, runtime)
    assert runtime._prepared is env.factory.instances[0] and not runtime._prepared._closed
    completed = next(r for r in records if r['event'] == 'native_prepare_completed')
    assert completed['preparation'] is None and completed['loader'] is None
    assert runtime.describe()['preparation'] is None
    runtime.close()


def test_close_failure_trace_does_not_drop_pending_owner(env):
    records = []
    runtime = env.make(startup_trace=records.append).start()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    ready(env, runtime)
    prepared = runtime._prepared
    env.factory.fail_cleanup = True
    with pytest.raises(RuntimeFailure):
        runtime.close()
    assert runtime._prepared is prepared and not prepared._closed
    assert records[-1]['event'] == 'native_prepare_closed' and records[-1]['cleanup_ok'] is False
    env.factory.fail_cleanup = False
    runtime.close()
    assert records[-1]['cleanup_ok'] is True


def test_missing_diagnostic_clock_is_none_not_a_reason_to_drop_a_trace(env):
    records = []
    clock = SimpleNamespace(time_ns=time.time_ns, monotonic=time.monotonic)
    runtime = env.make(startup_trace=records.append, clock=clock).start()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    def unavailable():
        raise RuntimeError('clock unavailable')
    clock.time_ns = unavailable
    ready(env, runtime)
    assert records and all(record['process_age_seconds'] is None for record in records)
    runtime.close()


def test_trace_and_loading_describe_never_acquire_a_live_preparation_lock(env):
    entered, release = Event(), Event()
    in_prepare = [False]
    diagnostic_checks = []
    def hold(prepared):
        original = prepared.diagnostics
        def facts():
            diagnostic_checks.append(in_prepare[0])
            assert not in_prepare[0]
            return original()
        prepared.diagnostics = facts
        in_prepare[0] = True
        entered.set()
        release.wait(3)
        in_prepare[0] = False
    env.factory.before_commit = hold
    records = []
    runtime = env.make(startup_trace=records.append).start()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    assert entered.wait(3)
    try:
        assert runtime.describe()['status'] == 'loading'
        assert runtime.describe()['preparation'] is None
        assert diagnostic_checks == []
        assert records[0]['event'] == 'native_prepare_started'
    finally:
        release.set()
    runtime.wait_ready(3)
    runtime.close()
    assert not any(diagnostic_checks)


def test_trace_fields_reject_boolean_integer_aliases_invalid_hashes_and_free_text():
    raw = dict(prepared=1, closed=False, source_sha256='private-token', dll_sha256='a' * 64,
               process_instance=[True, 123], valid_seconds=float('nan'), source_build='private-source')
    safe = module._trace_preparation(raw)
    assert safe['prepared'] is None and safe['closed'] is False
    assert safe['source_sha256'] is None and safe['dll_sha256'] == 'a' * 64
    assert safe['process_instance'] is safe['valid_seconds'] is safe['source_build'] is None
    loader = module._trace_loader(dict(stage='private-token', operation='private-path', check='private-js',
        thread_exit_code=True, process_exit_code=-1, module_base_low32=0x100000000,
        module_found=0, return_matches=None, path_utf16_units=False,
        timing_seconds={'submitted': True, 'closed': float('inf'), 'auth': 'private-token'}))
    assert all(loader[key] is None for key in ('stage', 'operation', 'check', 'thread_exit_code',
        'process_exit_code', 'module_base_low32', 'module_found', 'return_matches', 'path_utf16_units'))
    assert loader['timing_seconds']['submitted'] is loader['timing_seconds']['closed'] is None
    assert 'private' not in repr(safe) + repr(loader)


@pytest.mark.parametrize('operation', ['GetProcAddress/GetModuleHandleExW', 'GetModuleInformation(local)'])
def test_trace_preserves_existing_combined_loader_api_literals(env, operation):
    records = []
    runtime = env.make(startup_trace=records.append)
    runtime._trace_startup('native_prepare_failed', preparation=dict(loader=dict(
        operation=operation, last_error=dict(operation=operation, win32_error=5))))
    assert records[0]['loader']['operation'] == operation
    assert records[0]['loader']['last_error']['operation'] == operation
    assert records[0]['loader']['last_error']['win32_error'] == 5
    assert not env.factory.instances and not env.api.opened


@pytest.mark.parametrize('operation', ['GetProcAddress/GetModuleHandleExW/PRIVATE_TOKEN',
    'GetModuleInformation(C:/PRIVATE_PATH)', 'GetModuleInformation(local) PRIVATE_SOURCE'])
def test_trace_does_not_treat_combined_api_literal_as_a_free_text_prefix(env, operation):
    records = []
    runtime = env.make(startup_trace=records.append)
    runtime._trace_startup('native_prepare_failed', preparation=dict(loader=dict(
        operation=operation, last_error=dict(operation=operation))))
    assert records[0]['loader']['operation'] is None
    assert records[0]['loader']['last_error']['operation'] is None
    assert 'PRIVATE_' not in repr(records)
    assert not env.factory.instances and not env.api.opened


def test_full_material_validation_precedes_arm_and_is_not_repeated_after_birth(env):
    entered, release = Event(), Event()
    calls = []
    runtime = None
    def material(source):
        assert not runtime._condition._is_owned()
        calls.append(source)
        entered.set()
        release.wait(3)
        return env.material
    runtime = env.make(material_factory=material)
    runtime.start()
    assert entered.wait(3)
    try:
        assert runtime.describe()['status'] == 'starting'
        assert runtime.describe()['armed_filetime'] is None
        assert runtime.describe()['material_ready'] is False
        env.api.rows[1] = env.row(time.time_ns() // 100 + module.FILETIME_EPOCH)
        assert not env.api.opened and not env.factory.instances
    finally:
        release.set()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process' and r['code'] == 'restart_required')
    assert calls == [runtime.collector_root] and not env.factory.instances
    env.api.rows.clear()
    lease = ready(env, runtime)
    assert calls == [runtime.collector_root]
    assert env.factory.instances[0].material is env.material
    report = runtime.describe()
    assert report['material_ready'] is True and report['material_generation'] == 1
    assert type(report['material_preflight_seconds']) is float and report['material_preflight_seconds'] >= 0
    assert env.factory.loads == [(lease['pid'], lease['created'])]


def test_material_failure_does_not_hot_loop_and_needs_explicit_retry(env):
    calls = []
    def material(source):
        calls.append(source)
        if len(calls) == 1:
            raise RuntimeError('owned invalid material')
        return env.material
    runtime = env.make(material_factory=material).start()
    wait_for(runtime, lambda r: r['status'] == 'failed')
    time.sleep(.08)
    assert len(calls) == 1 and runtime.describe()['armed_filetime'] is None
    assert not env.api.opened and not env.factory.instances
    runtime.retry()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    assert len(calls) == 2 and runtime.describe()['material_generation'] == 1


def test_none_material_is_never_armed_or_given_to_a_loader(env):
    runtime = env.make(material_factory=lambda _: None).start()
    wait_for(runtime, lambda r: r['status'] == 'failed')
    assert runtime.code == 'native_material_not_ready'
    assert runtime.describe()['material_ready'] is False and runtime.describe()['armed_filetime'] is None
    assert not env.factory.instances and not env.api.opened


def test_cancellation_discards_late_material_without_arming(env):
    entered, release = Event(), Event()
    def material(_):
        entered.set()
        release.wait(3)
        return env.material
    runtime = env.make(material_factory=material).start()
    assert entered.wait(3)
    runtime.cancel()
    release.set()
    runtime.close()
    report = runtime.describe()
    assert report['status'] == 'closed' and report['material_ready'] is False
    assert report['armed_filetime'] is None and report['material_generation'] == 0
    assert runtime._material is None and not env.factory.instances


def test_retry_during_validation_cannot_publish_or_load_superseded_material(env):
    entered, release = [Event(), Event()], [Event(), Event()]
    values, calls = [object(), object()], []
    def material(_):
        index = len(calls)
        calls.append(index)
        entered[index].set()
        release[index].wait(3)
        return values[index]
    runtime = env.make(material_factory=material).start()
    assert entered[0].wait(3)
    runtime.retry()
    release[0].set()
    assert entered[1].wait(3)
    try:
        report = runtime.describe()
        assert report['armed_filetime'] is None and report['material_generation'] == 0
        assert runtime._material is None and report['material_ready'] is False
        assert not env.factory.instances and not env.api.opened
    finally:
        release[1].set()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    ready(env, runtime)
    assert calls == [0, 1] and env.factory.instances[0].material is values[1]


def test_slow_idle_retry_is_starting_not_an_old_armed_prompt(env):
    runtime = arm(env)
    old_material, old_fence = runtime._material, runtime.describe()['armed_filetime']
    entered, release = Event(), Event()
    def material(_):
        entered.set()
        release.wait(3)
        return object()
    runtime.material_factory = material
    runtime.retry()
    assert entered.wait(3)
    try:
        report = runtime.describe()
        assert report['status'] == 'starting' and report['material_ready'] is False
        assert runtime._material is old_material and report['armed_filetime'] == old_fence
    finally:
        release.set()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process')
    assert runtime.describe()['material_generation'] == 2


def test_failed_retry_preserves_existing_preparation_material_and_attempted_birth(env):
    runtime = arm(env)
    lease = ready(env, runtime)
    prepared, material, fence = runtime._prepared, runtime._material, runtime.describe()['armed_filetime']
    def invalid(_):
        raise RuntimeError('owned changed package')
    runtime.material_factory = invalid
    runtime.retry()
    wait_for(runtime, lambda r: r['status'] == 'process_failed')
    assert runtime._prepared is prepared and not prepared._closed and runtime._material is material
    assert runtime.describe()['armed_filetime'] == fence and runtime.describe()['material_ready'] is False
    assert (lease['pid'], lease['created']) in runtime._attempted
    with pytest.raises(RuntimeFailure):
        runtime.observer_factory(SimpleNamespace())
    time.sleep(.05)
    assert len(env.factory.instances) == len(env.factory.loads) == 1


def test_retry_does_not_invalidate_an_active_observer_or_revalidate_material(env):
    runtime = arm(env)
    lease = ready(env, runtime)
    observer = runtime.observer_factory(SimpleNamespace(process=SimpleNamespace(
        pid=lease['pid'], process_instance=(lease['pid'], lease['created']))))
    material, generation = runtime._material, runtime.describe()['material_generation']
    assert runtime.retry() is False
    time.sleep(.06)
    assert runtime.describe()['material_ready'] is True and runtime._material is material
    assert runtime.describe()['material_generation'] == generation and len(env.material_calls) == 1
    assert runtime.wait_ready(0) == lease and observer in runtime._observers
    assert len(env.factory.loads) == 1


def test_process_query_pending_does_not_repeat_full_material_preflight(env):
    runtime = arm(env)
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 1)
    env.api.denied.add(1)
    wait_for(runtime, lambda r: r['code'] == 'process_query_pending')
    time.sleep(.06)
    assert len(env.material_calls) == 1 and not env.factory.instances


def manual_ready(env):
    """Drive the owning watcher explicitly; no timing window or real process."""
    clock = SimpleNamespace(filetime=module.FILETIME_EPOCH + 10000000,
                            monotonic=time.monotonic)
    clock.time_ns = lambda: (clock.filetime - module.FILETIME_EPOCH) * 100
    runtime = env.make(clock=clock)
    runtime._step()
    env.api.rows[100] = env.row(clock.filetime + 1)
    runtime._step()
    assert runtime.status == module.RuntimeState.READY
    return runtime, clock, dict(runtime._lease)


def test_dead_lease_rearms_once_after_cleanup_and_rejects_already_born_candidate(env):
    runtime, clock, lease = manual_ready(env)
    old_fence = runtime.describe()['armed_filetime']
    old_generation = runtime.describe()['watch_generation']
    env.api.rows[100].alive = False
    env.api.rows[200] = env.row(clock.filetime + 5)
    clock.filetime += 10
    runtime._step()
    report = runtime.describe()
    assert report['armed_filetime'] == clock.filetime > old_fence
    assert report['watch_generation'] == old_generation + 1
    assert report['code'] == 'restart_required' and runtime._lease is None
    assert env.factory.instances[0]._closed and len(env.factory.instances) == 1
    assert env.factory.loads == [(lease['pid'], lease['created'])]
    assert (lease['pid'], lease['created']) in runtime._attempted
    assert (200, env.api.rows[200].created) not in runtime._attempted
    assert len(env.material_calls) == 1
    clock.filetime += 10
    runtime._step()
    assert runtime.describe()['armed_filetime'] == report['armed_filetime']
    assert runtime.describe()['watch_generation'] == report['watch_generation']
    del env.api.rows[200]
    env.api.rows[201] = env.row(clock.filetime + 1)
    runtime._step()
    assert runtime._lease['pid'] == 201 and len(env.factory.loads) == 2
    assert len(env.material_calls) == 1


def test_dead_lease_equal_new_fence_is_not_new_enough(env):
    runtime, clock, _lease = manual_ready(env)
    env.api.rows[100].alive = False
    clock.filetime += 10
    env.api.rows[200] = env.row(clock.filetime)
    runtime._step()
    assert runtime.describe()['code'] == 'restart_required'
    assert len(env.factory.instances) == len(env.factory.loads) == 1


def test_dead_lease_pending_observer_does_not_rearm_until_observer_released(env):
    runtime, clock, lease = manual_ready(env)
    observer = runtime.observer_factory(SimpleNamespace(process=SimpleNamespace(
        pid=lease['pid'], process_instance=(lease['pid'], lease['created']))))
    old_fence = runtime.describe()['armed_filetime']
    env.api.rows[100].alive = False
    env.api.rows[200] = env.row(clock.filetime + 5)
    clock.filetime += 10
    runtime._step()
    assert runtime.describe()['code'] == 'observer_cleanup_pending'
    assert runtime.describe()['armed_filetime'] == old_fence
    assert runtime._lease == lease and runtime._prepared is env.factory.instances[0]
    assert len(env.factory.loads) == 1 and len(env.material_calls) == 1
    observer.stop()
    runtime._step()
    assert runtime.describe()['armed_filetime'] == clock.filetime
    assert runtime.describe()['code'] == 'restart_required'
    assert runtime._lease is None and len(env.factory.loads) == 1


def test_dead_lease_cleanup_failure_preserves_owner_and_fence(env):
    runtime, clock, lease = manual_ready(env)
    prepared, old_fence = runtime._prepared, runtime.describe()['armed_filetime']
    env.api.rows[100].alive = False
    env.api.rows[200] = env.row(clock.filetime + 5)
    clock.filetime += 10
    env.factory.fail_cleanup = True
    with pytest.raises(RuntimeFailure, match='pending'):
        runtime._step()
    assert runtime._lease == lease and runtime._prepared is prepared and not prepared._closed
    assert runtime.describe()['armed_filetime'] == old_fence and len(env.factory.loads) == 1
    with pytest.raises(RuntimeFailure, match='尚未就绪'):
        runtime.observer_factory(SimpleNamespace())
    env.factory.fail_cleanup = False
    runtime._step()
    assert runtime._lease is None and runtime.describe()['armed_filetime'] == clock.filetime
    assert runtime.describe()['code'] == 'restart_required' and len(env.factory.loads) == 1


def test_cancel_during_dead_lease_cleanup_does_not_publish_new_fence(env):
    runtime, clock, lease = manual_ready(env)
    old_fence = runtime.describe()['armed_filetime']
    prepared = runtime._prepared
    original = prepared.close
    def close():
        original()
        runtime.cancel()
    prepared.close = close
    env.api.rows[100].alive = False
    clock.filetime += 10
    with pytest.raises(InterruptedError):
        runtime._step()
    assert runtime._lease == lease and runtime.describe()['armed_filetime'] == old_fence
    assert len(env.factory.loads) == len(env.material_calls) == 1


def test_retry_during_dead_lease_cleanup_does_not_arm_stale_admission(env):
    runtime, clock, lease = manual_ready(env)
    old_fence = runtime.describe()['armed_filetime']
    prepared, original = runtime._prepared, runtime._prepared.close
    def close():
        original()
        runtime.retry()
    prepared.close = close
    env.api.rows[100].alive = False
    env.api.rows[200] = env.row(clock.filetime + 5)
    clock.filetime += 10
    runtime._step()
    assert runtime._lease == lease and runtime.describe()['armed_filetime'] == old_fence
    assert runtime.describe()['material_ready'] is False
    assert len(env.factory.loads) == len(env.material_calls) == 1
    runtime._step()
    assert runtime._lease is None and runtime.describe()['armed_filetime'] == clock.filetime
    assert runtime.describe()['code'] == 'restart_required'
    assert len(env.factory.loads) == 1 and len(env.material_calls) == 2


def test_unknown_old_lease_liveness_never_closes_or_rearms(env):
    runtime, clock, lease = manual_ready(env)
    prepared, old_fence = runtime._prepared, runtime.describe()['armed_filetime']
    env.api.denied.add(100)
    env.api.rows[200] = env.row(clock.filetime + 5)
    clock.filetime += 10
    try:
        with pytest.raises(module.access.ApiFailure):
            runtime._step()
        assert runtime._lease == lease and runtime._prepared is prepared and not prepared._closed
        assert runtime.describe()['armed_filetime'] == old_fence
        assert len(env.factory.loads) == len(env.material_calls) == 1
    finally:
        env.api.denied.clear()


@pytest.mark.parametrize('field,value', [
    ('birth', None), ('birth', True), ('birth', -1),
    ('exit', None), ('exit', True), ('exit', -1), ('exit', 0x100000000), ('exit', '0')])
def test_untyped_old_lease_liveness_is_unknown_not_dead(env, monkeypatch, field, value):
    runtime, clock, lease = manual_ready(env)
    prepared, old_fence = runtime._prepared, runtime.describe()['armed_filetime']
    method = 'process_times' if field == 'birth' else 'exit_code'
    with monkeypatch.context() as patch:
        patch.setattr(env.api, method, lambda handle: (value, 0) if field == 'birth' else value)
        clock.filetime += 10
        with pytest.raises(RuntimeFailure) as error:
            runtime._step()
        assert error.value.code == 'process_query_pending'
        assert runtime._lease == lease and runtime._prepared is prepared and not prepared._closed
        assert runtime.describe()['armed_filetime'] == old_fence
        assert len(env.factory.loads) == len(env.material_calls) == 1


def test_birth_read_failure_preserves_old_owner_and_fence(env, monkeypatch):
    runtime, clock, lease = manual_ready(env)
    prepared, old_fence = runtime._prepared, runtime.describe()['armed_filetime']
    def unavailable(_handle):
        raise module.access.ApiFailure('GetProcessTimes', 299)
    with monkeypatch.context() as patch:
        patch.setattr(env.api, 'process_times', unavailable)
        clock.filetime += 10
        with pytest.raises(module.access.ApiFailure):
            runtime._step()
        assert runtime._lease == lease and runtime._prepared is prepared and not prepared._closed
        assert runtime.describe()['armed_filetime'] == old_fence and len(env.factory.loads) == 1


def test_alive_observer_does_not_retire_lease_or_advance_fence(env):
    runtime, clock, lease = manual_ready(env)
    observer = runtime.observer_factory(SimpleNamespace(process=SimpleNamespace(
        pid=lease['pid'], process_instance=(lease['pid'], lease['created']))))
    old_fence = runtime.describe()['armed_filetime']
    runtime.retry()
    clock.filetime += 10
    runtime._step()
    assert runtime._lease == lease and observer in runtime._observers
    assert runtime.describe()['armed_filetime'] == old_fence
    assert runtime.describe()['material_ready'] is True
    assert len(env.factory.loads) == len(env.material_calls) == 1


def test_old_enumeration_ticket_cannot_prepare_after_retirement_fence(env):
    runtime, clock, _lease = manual_ready(env)
    old_ticket = (runtime.describe()['armed_filetime'], runtime.describe()['watch_generation'])
    env.api.rows[100].alive = False
    clock.filetime += 10
    runtime._step()
    env.api.rows[200] = env.row(clock.filetime + 1)
    runtime._prepare((200, env.api.rows[200].created), fence=old_ticket)
    assert runtime._lease is None and len(env.factory.instances) == len(env.factory.loads) == 1
    runtime._step()
    assert runtime._lease['pid'] == 200 and len(env.factory.loads) == 2


@pytest.mark.parametrize('interruption', ['cancel', 'retry'])
def test_retirement_enumeration_cannot_admit_after_cancel_or_retry(env, interruption):
    runtime, clock, _lease = manual_ready(env)
    env.api.rows[100].alive = False
    clock.filetime += 10
    runtime._step()
    env.api.rows[200] = env.row(clock.filetime + 1)
    original = runtime._instances
    def instances():
        rows = original()
        getattr(runtime, interruption)()
        return rows
    runtime._instances = instances
    if interruption == 'cancel':
        with pytest.raises(InterruptedError):
            runtime._step()
    else:
        runtime._step()
    assert runtime._lease is None and len(env.factory.instances) == len(env.factory.loads) == 1


def test_game_build_change_during_material_validation_does_not_arm(env, monkeypatch):
    current = [0]
    def material(_):
        current[0] = 1
        return env.material
    runtime = env.make(material_factory=material)
    monkeypatch.setattr(runtime, '_files', lambda: (current[0],))
    runtime.start()
    wait_for(runtime, lambda r: r['status'] == 'failed')
    assert runtime.code == 'native_build_changed' and runtime.describe()['armed_filetime'] is None
    assert runtime._material is None and not env.factory.instances


def test_retry_arriving_during_target_query_blocks_old_material_admission(env, monkeypatch):
    entered, release = Event(), Event()
    values, calls = [object(), object()], []
    def material(_):
        index = len(calls)
        calls.append(index)
        if index == 1:
            entered.set()
            release.wait(3)
        return values[index]
    runtime = env.make(material_factory=material)
    original = runtime._instances
    triggered = [False]
    def instances():
        if not triggered[0]:
            triggered[0] = True
            env.api.rows[1] = env.row(time.time_ns() // 100 + module.FILETIME_EPOCH)
            runtime.retry()
        return original()
    monkeypatch.setattr(runtime, '_instances', instances)
    runtime.start()
    assert entered.wait(3)
    try:
        assert runtime.describe()['material_ready'] is False
        assert runtime.describe()['status'] == 'starting'
        assert not env.factory.instances and not runtime._attempted
    finally:
        release.set()
    wait_for(runtime, lambda r: r['status'] == 'waiting_for_process' and r['code'] == 'restart_required')
    assert not env.factory.instances
    env.api.rows.clear()
    ready(env, runtime)
    assert env.factory.instances[0].material is values[1] and calls == [0, 1]


def test_failed_arming_clock_does_not_partially_publish_material(env):
    def bad_clock():
        raise RuntimeError('owned unavailable arming clock')
    clock = SimpleNamespace(time_ns=bad_clock, monotonic=time.monotonic)
    runtime = env.make(clock=clock).start()
    wait_for(runtime, lambda r: r['status'] == 'failed')
    assert runtime.describe()['material_ready'] is False
    assert runtime.describe()['material_generation'] == 0 and runtime.describe()['armed_filetime'] is None
    assert runtime._material is None and not env.factory.instances
