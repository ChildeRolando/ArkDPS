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
        self.failure = self.before_commit = None
        self.fail_cleanup = False

    def __call__(self, game_root, *, root, runtime_dir):
        owner = self

        class Prepared:
            claimed, _prepared, _closed = False, False, False
            expires_at = None

            def __init__(self):
                self.process_instance = None
                self.close_calls = 0
                self.root, self.runtime_dir = root, runtime_dir

            def prepare(self, birth):
                self.process_instance = birth
                if owner.before_commit:
                    owner.before_commit(self)
                if owner.failure:
                    raise RuntimeFailure('owned_prepare_failed', owner.failure)
                self.commit(lambda: owner.loads.append(birth))
                self._prepared = True
                self.expires_at = time.monotonic() + 120

            def diagnostics(self):
                return dict(prepared=self._prepared, claimed=self.claimed, closed=self._closed,
                            process_instance=self.process_instance, expires_at=self.expires_at,
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
    build = {'known_build': True, 'amd64': True, 'source_build': module.access.BUILD_ID}

    def make(**options):
        runtime = NativeHookRuntime(root, runtime_dir=tmp_path / 'state', collector_root=tmp_path / 'source',
            api=api, admin_check=options.pop('admin_check', lambda: True), prepared_factory=factory,
            build_check=lambda _: (dict(build), []), **options)
        runtimes.append(runtime)
        return runtime

    def row(created, *, correct=True):
        return SimpleNamespace(created=created, alive=True,
                               path=root / ('Arknights.exe' if correct else 'Other.exe'))

    yield SimpleNamespace(root=root, api=api, factory=factory, runtimes=runtimes, build=build, make=make, row=row)
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


def test_only_new_exact_birth_is_prepared_and_close_releases_unclaimed(env):
    env.api.rows[3] = env.row(1)
    runtime = arm(env)
    assert env.factory.loads == []
    env.api.rows.clear()
    lease = ready(env, runtime)
    assert lease == dict(pid=100, created=runtime.describe()['armed_filetime'] + 100, source_build=module.access.BUILD_ID)
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
    env.api.rows[1] = env.row(runtime.describe()['armed_filetime'] + 2)  # PID reuse, new birth.
    wait_for(runtime, lambda r: r['status'] == 'ready')
    assert runtime.wait_ready(3)['created'] == runtime.describe()['armed_filetime'] + 2
    assert len(env.factory.instances) == 2


def test_permission_preflight_never_elevates_or_prepares(env):
    runtime = env.make(admin_check=lambda: False).start()
    wait_for(runtime, lambda r: r['status'] == 'failed')
    assert runtime.is_failed and runtime.code == 'native_permission_denied'
    assert not env.api.opened and not env.factory.instances


@pytest.mark.parametrize('field,value', [('known_build', False), ('amd64', False), ('source_build', 'bilibili-build')])
def test_only_official_fingerprint_is_admitted(env, field, value):
    env.build[field] = value
    runtime = env.make().start()
    wait_for(runtime, lambda r: r['status'] == 'failed')
    assert runtime.code == 'native_unsupported_build'
    assert not env.factory.instances


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
