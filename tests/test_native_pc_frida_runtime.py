"""PC startup ownership checks use fake processes, loaders and endpoints only."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import time

import pytest

from connection import VersionStatus
from event_observer import ObserverStatus
from native_pc import frida_runtime as module
from native_pc.frida_runtime import NativePcFridaRuntime, RuntimeFailure


class Api:
    def __init__(self, root):
        self.root = root
        self.rows = {}
        self.opened, self.closed = [], []
        self.handles = {}

    def named_processes(self):
        return list(self.rows)

    def open_process(self, pid, rights):
        if pid not in self.rows:
            raise module.access.ApiFailure('OpenProcess', 87)
        handle = (pid, len(self.opened))
        self.opened.append(handle)
        self.handles[handle] = self.rows[pid]
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


class Loaders:
    def __init__(self, api):
        self.api = api
        self.loads = []
        self.instances = []
        self.failure = None
        self.pending = False
        self.complete = False

    def __call__(self, pid, *, access_rights=0x43A):
        owner = self
        process = owner.api.rows[pid]
        class Loader:
            def __init__(self):
                self.created_filetime = process.created
                self.pending_thread = None
                self.load_error = None
                self.closed = False

            def find_module(self, path):
                return {'base': 0x10000} if process.module else None

            def load(self, path):
                owner.loads.append((pid, Path(path)))
                if owner.failure:
                    raise RuntimeFailure('loader_failed', owner.failure)
                if owner.pending:
                    self.pending_thread = object()
                    raise TimeoutError('still running')
                process.module = True

            def poll(self):
                if self.pending_thread is not None:
                    if not owner.complete:
                        return False
                    self.pending_thread = None
                    process.module = True
                return True

            def close(self):
                if self.pending_thread is not None and process.alive and not self.poll():
                    raise RuntimeFailure('cleanup_pending', 'still running')
                self.closed = True
        instance = Loader()
        self.instances.append(instance)
        return instance


def wait_for(runtime, predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        state = runtime.describe()
        if predicate(state):
            return state
        time.sleep(.005)
    raise AssertionError(runtime.describe())


@pytest.fixture
def environment(tmp_path, monkeypatch):
    root = tmp_path / 'PC game'
    root.mkdir()
    gadget = tmp_path / module.GADGET_NAME
    gadget.write_bytes(b'controlled Gadget test bytes; never executed')
    monkeypatch.setattr(module, 'GADGET_SHA256', hashlib.sha256(gadget.read_bytes()).hexdigest())
    api = Api(root)
    loaders = Loaders(api)
    verified = []
    runtimes = []
    def make(**kwargs):
        runtime = NativePcFridaRuntime(root, runtime_dir=tmp_path / 'state', gadget_path=gadget,
            api=api, loader_factory=loaders, build_check=lambda _: ({'known_build': True}, []),
            admin_check=lambda: True, **kwargs)
        def verify(record):
            assert api.rows[record['pid']].module
            assert len(runtime._credentials(record)) >= 32
            if runtime._endpoint_owner is None:
                runtime._endpoint_owner = SimpleNamespace(
                    birth=runtime._birth(record), address='127.0.0.1:27382',
                    manager=SimpleNamespace(remove_remote_device=lambda _: None),
                    device=SimpleNamespace(enumerate_processes=lambda: [SimpleNamespace(pid=record['pid'])]))
            verified.append(record['pid'])
        runtime._verify_endpoint = verify
        runtime._port_occupied = lambda: False
        runtimes.append(runtime)
        return runtime
    yield SimpleNamespace(root=root, gadget=gadget, api=api, loaders=loaders, verified=verified, make=make)
    for runtime in runtimes:
        for row in api.rows.values():
            row.alive = False
        try:
            runtime.close()
        except RuntimeFailure:
            # Ownership-conflict tests deliberately preserve altered credentials.
            assert not runtime._thread or not runtime._thread.is_alive()


def row(environment, created, *, correct=True, loaded=False):
    return SimpleNamespace(created=created, path=environment.root / ('Arknights.exe' if correct else 'Other.exe'),
                           alive=True, module=loaded)


def arm(environment):
    runtime = environment.make().start()
    wait_for(runtime, lambda state: state['status'] == 'waiting_for_process')
    return runtime


def add_new(environment, runtime, pid=100):
    environment.api.rows[pid] = row(environment, runtime.describe()['armed_filetime'] + pid)
    return pid


def test_new_exact_process_loads_once_and_close_preserves_live_component(environment):
    runtime = arm(environment)
    pid = add_new(environment, runtime)
    lease = runtime.wait_ready(2)
    assert lease['pid'] == pid and lease['status'] == 'ready'
    assert environment.loaders.loads == [(pid, environment.gadget)]
    token = json.loads(runtime.config_path.read_text())['interaction']['token']
    assert token not in json.dumps(runtime.describe()) and 'token' not in lease
    runtime.close()
    assert runtime.describe()['status'] == 'closed' and runtime.describe()['expected_resident']
    assert runtime.config_path.exists() and runtime.lease_path.exists()
    assert all(loader.closed for loader in environment.loaders.instances)
    assert environment.api.opened == environment.api.closed


def test_old_equal_and_wrong_path_are_not_loaded_and_ambiguity_is_recoverable(environment):
    runtime = arm(environment)
    armed = runtime.describe()['armed_filetime']
    environment.api.rows[1] = row(environment, armed - 1)
    environment.api.rows[2] = row(environment, armed)
    environment.api.rows[3] = row(environment, armed + 1, correct=False)
    wait_for(runtime, lambda state: state['code'] == 'ambiguous_process')
    assert not environment.loaders.loads
    environment.api.rows[1].alive = environment.api.rows[2].alive = False
    with pytest.raises(TimeoutError):
        runtime.wait_ready(.05)
    assert not environment.loaders.loads
    add_new(environment, runtime, pid=4)
    assert runtime.wait_ready(2)['pid'] == 4


def test_running_process_before_arming_requires_restart(environment):
    environment.api.rows[1] = row(environment, 1)
    runtime = environment.make().start()
    wait_for(runtime, lambda state: state['code'] == 'restart_required')
    with pytest.raises(TimeoutError):
        runtime.wait_ready(.03)
    assert not environment.loaders.loads


def test_existing_ready_lease_reconnects_without_a_second_load(environment):
    first = arm(environment)
    pid = add_new(environment, first)
    original = first.wait_ready(2)
    first.close()
    second = environment.make().start()
    assert second.wait_ready(2) == original
    assert len(environment.loaders.loads) == 1
    assert environment.verified == [pid, pid]


def test_failed_loader_is_not_retried_for_the_same_instance(environment):
    environment.loaders.failure = 'controlled load failure'
    runtime = arm(environment)
    pid = add_new(environment, runtime)
    wait_for(runtime, lambda state: state['status'] == 'process_failed')
    with pytest.raises(RuntimeFailure, match='controlled load failure'):
        runtime.wait_ready(1)
    time.sleep(.06)
    assert len(environment.loaders.loads) == 1
    environment.api.rows[pid].alive = False
    wait_for(runtime, lambda state: state['status'] == 'waiting_for_process')
    environment.loaders.failure = None
    add_new(environment, runtime, pid=101)
    assert runtime.wait_ready(2)['pid'] == 101
    assert len(environment.loaders.loads) == 2


def test_pending_loader_cleanup_retains_ownership_until_completion(environment):
    environment.loaders.pending = True
    runtime = arm(environment)
    add_new(environment, runtime)
    wait_for(runtime, lambda state: state['code'] == 'loader_pending')
    with pytest.raises(RuntimeFailure, match='still running'):
        runtime.close()
    assert runtime.describe()['code'] == 'cleanup_pending'
    assert runtime.config_path.exists() and runtime._pending_loader is not None
    assert not runtime._pending_loader.closed
    environment.loaders.complete = True
    runtime.close()
    assert runtime._pending_loader is None
    assert all(loader.closed for loader in environment.loaders.instances)


def test_cancelled_wait_never_loads_and_joins_the_watcher(environment):
    runtime = arm(environment)
    with pytest.raises(InterruptedError):
        runtime.wait_ready(1, cancelled=lambda: True)
    runtime.close()
    assert not runtime._thread.is_alive() and not environment.loaders.loads


def test_foreign_credentials_are_preserved(environment):
    runtime = arm(environment)
    runtime.config_path.write_text('foreign credentials', encoding='utf-8')
    add_new(environment, runtime)
    state = wait_for(runtime, lambda state: state['status'] == 'failed')
    assert state['code'] == 'credential_conflict'
    assert runtime.config_path.read_text(encoding='utf-8') == 'foreign credentials'
    assert not environment.loaders.loads


def test_changed_lease_preserves_its_credential_file_on_cleanup(environment):
    runtime = arm(environment)
    pid = add_new(environment, runtime)
    lease = runtime.wait_ready(2)
    previous = runtime.config_path.read_bytes()
    changed = dict(lease, status='failed')
    runtime.lease_path.write_text(json.dumps(changed), encoding='utf-8')
    environment.api.rows[pid].alive = False
    with pytest.raises(RuntimeFailure, match='Changed PC Frida lease'):
        runtime.close()
    assert runtime.config_path.read_bytes() == previous


def test_default_asset_is_copied_into_writable_runtime_cache(environment, monkeypatch, tmp_path):
    monkeypatch.setattr(module, 'DEFAULT_ASSET', environment.gadget)
    runtime = NativePcFridaRuntime(environment.root, runtime_dir=tmp_path / 'cached-state', api=environment.api,
        loader_factory=environment.loaders, build_check=lambda _: ({'known_build': True}, []), admin_check=lambda: True)
    runtime._port_occupied = lambda: False
    runtime.start()
    try:
        wait_for(runtime, lambda state: state['status'] == 'waiting_for_process')
        assert runtime.gadget_path != environment.gadget
        assert runtime.gadget_path.read_bytes() == environment.gadget.read_bytes()
        assert not environment.gadget.with_suffix('.config').exists()
    finally:
        runtime.close()


def test_preflight_failure_is_stored_for_wait_and_observer_factory(environment):
    runtime = environment.make()
    runtime.admin_check = lambda: False
    runtime.start()
    wait_for(runtime, lambda state: state['status'] == 'failed')
    with pytest.raises(RuntimeFailure, match='管理员'):
        runtime.wait_ready(1)
    observer = runtime.observer_factory(SimpleNamespace(), None)
    with pytest.raises(RuntimeFailure, match='管理员'):
        observer._attach_session(SimpleNamespace())
    assert runtime.describe()['code'] == 'native_permission_denied' and not environment.loaders.loads


def test_explicit_retry_publishes_pending_and_reauth_generation_without_reloading(environment):
    runtime = environment.make()
    runtime.admin_check = lambda: False
    runtime.start()
    wait_for(runtime, lambda state: state['status'] == 'failed')
    assert runtime.is_failed and not runtime.is_pending
    runtime.admin_check = lambda: True
    with runtime._condition:
        assert runtime.retry()
        assert runtime.is_pending and not runtime.is_failed
    wait_for(runtime, lambda state: state['status'] == 'waiting_for_process')
    add_new(environment, runtime)
    runtime.wait_ready(2)
    generation = runtime.describe()['ready_generation']
    armed = runtime.describe()['armed_filetime']
    with runtime._condition:
        assert runtime.retry()
        assert runtime.is_pending and runtime.describe()['ready_generation'] == generation
    runtime.wait_ready(2)
    assert runtime.describe()['ready_generation'] == generation + 1
    assert runtime.describe()['armed_filetime'] == armed
    assert len(environment.loaders.loads) == 1


def test_changed_build_fingerprint_is_rechecked_before_a_new_attempt_mutates_files(environment):
    binary = environment.root / 'GameAssembly.dll'
    binary.write_bytes(b'initial controlled file')
    runtime = environment.make()
    calls = []
    def build_check(_):
        calls.append(binary.read_bytes())
        return {'known_build': binary.read_bytes() == b'initial controlled file'}, []
    runtime.build_check = build_check
    runtime.start()
    wait_for(runtime, lambda state: state['status'] == 'waiting_for_process')
    binary.write_bytes(b'updated unsupported controlled file')
    add_new(environment, runtime)
    state = wait_for(runtime, lambda state: state['status'] == 'failed')
    assert state['code'] == 'native_unsupported_build' and len(calls) == 2
    assert not runtime.config_path.exists() and not runtime.lease_path.exists() and not environment.loaders.loads


def test_changed_gadget_fingerprint_blocks_a_new_attempt_without_writing_credentials(environment):
    runtime = arm(environment)
    environment.gadget.write_bytes(b'changed untrusted component')
    add_new(environment, runtime)
    state = wait_for(runtime, lambda state: state['status'] == 'failed')
    assert state['code'] == 'gadget_hash_mismatch'
    assert not runtime.config_path.exists() and not runtime.lease_path.exists() and not environment.loaders.loads


def test_occupied_endpoint_waits_then_arms_for_a_new_process_without_touching_credentials(environment):
    runtime = environment.make()
    occupied = [True]
    runtime._port_occupied = lambda: occupied[0]
    runtime.start()
    state = wait_for(runtime, lambda state: state['status'] == 'waiting_for_endpoint')
    assert state['code'] == 'endpoint_in_use' and state['armed_filetime'] is None
    assert not runtime.config_path.exists() and not runtime.lease_path.exists() and not environment.loaders.loads
    environment.api.rows[100] = row(environment, time.time_ns() // 100 + module.FILETIME_EPOCH)
    with pytest.raises(TimeoutError):
        runtime.wait_ready(.03)
    occupied[0] = False
    wait_for(runtime, lambda state: state['code'] == 'restart_required')
    assert not environment.loaders.loads and runtime.describe()['armed_filetime'] > environment.api.rows[100].created
    environment.api.rows[100].alive = False
    add_new(environment, runtime, pid=101)
    assert runtime.wait_ready(2)['pid'] == 101
    time.sleep(.06)
    assert environment.loaders.loads == [(101, environment.gadget)]


def test_occupied_endpoint_wait_is_cancellable_and_preserves_foreign_credentials(environment):
    runtime = environment.make()
    runtime.config_path.write_text('foreign credentials', encoding='utf-8')
    runtime._port_occupied = lambda: True
    runtime.start()
    wait_for(runtime, lambda state: state['status'] == 'waiting_for_endpoint')
    runtime.cancel()
    with pytest.raises(InterruptedError):
        runtime.wait_ready(1)
    runtime.close()
    assert not runtime._thread.is_alive() and runtime.describe()['armed_filetime'] is None
    assert not runtime.lease_path.exists() and not environment.loaders.loads
    assert runtime.config_path.read_text(encoding='utf-8') == 'foreign credentials'


def test_changed_configuration_cannot_be_used_for_reconnect(environment):
    first = arm(environment)
    add_new(environment, first)
    first.wait_ready(2)
    first.close()
    first.config_path.write_text('{}', encoding='utf-8')
    second = environment.make().start()
    state = wait_for(second, lambda state: state['status'] == 'failed')
    assert state['code'] == 'credential_conflict' and len(environment.loaders.loads) == 1
    assert first.config_path.read_text(encoding='utf-8') == '{}'


@pytest.mark.parametrize('field,value', [('source_build', 'other'), ('gadget_sha256', '0' * 64),
                                       ('game_root', 'C:/other/game')])
def test_foreign_lease_is_preserved_without_loading(environment, field, value):
    first = arm(environment)
    add_new(environment, first)
    lease = first.wait_ready(2)
    first.close()
    changed = dict(lease, **{field: value})
    first.lease_path.write_text(json.dumps(changed), encoding='utf-8')
    previous = first.config_path.read_bytes()
    second = environment.make().start()
    state = wait_for(second, lambda state: state['status'] == 'failed')
    assert state['code'] == 'lease_conflict' and len(environment.loaders.loads) == 1
    assert json.loads(second.lease_path.read_text(encoding='utf-8')) == changed
    assert second.config_path.read_bytes() == previous


def test_moved_dead_lease_only_removes_copied_credentials(environment):
    first = arm(environment)
    pid = add_new(environment, first)
    lease = first.wait_ready(2)
    first.close()
    copied = environment.gadget.parent / 'old-external-component'
    copied.mkdir()
    old_config = copied / environment.gadget.with_suffix('.config').name
    old_config.write_bytes(first.config_path.read_bytes())
    changed = dict(lease, gadget_path=str(copied / environment.gadget.name))
    first.lease_path.write_text(json.dumps(changed), encoding='utf-8')
    environment.api.rows[pid].alive = False
    second = environment.make().start()
    wait_for(second, lambda state: state['status'] == 'waiting_for_process')
    assert not second.lease_path.exists() and not second.config_path.exists()
    assert old_config.exists() and len(environment.loaders.loads) == 1


def test_authenticated_endpoint_is_pid_bound_and_backend_errors_are_redacted(environment):
    runtime = arm(environment)
    pid = add_new(environment, runtime)
    lease = runtime.wait_ready(2)
    token = runtime._credentials(lease)
    removed = []
    manager = SimpleNamespace(
        add_remote_device=lambda address, **kw: SimpleNamespace(enumerate_processes=lambda: [SimpleNamespace(pid=pid + 1)]),
        remove_remote_device=lambda address: removed.append(address))
    backend = SimpleNamespace(get_device_manager=lambda: manager)
    def refused(address, **kwargs):
        assert address == '127.0.0.1:27382' and kwargs == {'token': token}
        raise RuntimeError('backend accidentally includes ' + token)
    with runtime._condition:
        runtime._state(module.RuntimeState.AUTHENTICATING)
        runtime._release_endpoint()
        with pytest.raises(RuntimeFailure) as mismatch:
            runtime._connect(backend, lease)
        assert mismatch.value.code == 'endpoint_mismatch' and removed == ['127.0.0.1:27382']
        manager.add_remote_device = refused
        with pytest.raises(RuntimeFailure) as unavailable:
            runtime._connect(backend, lease)
        assert unavailable.value.code == 'endpoint_unavailable' and token not in str(unavailable.value)


def test_gadget_observer_reuses_native_event_protocol_and_releases_only_client_resources(environment, monkeypatch):
    runtime = arm(environment)
    pid = add_new(environment, runtime)
    lease = runtime.wait_ready(2)
    calls = []
    class Script:
        is_destroyed = False
        exports_sync = SimpleNamespace(stop=lambda: calls.append('stop'))
        def on(self, name, callback):
            assert name == 'message'
            self.callback = callback
        def load(self):
            self.callback({'type': 'send', 'payload': {'ready': True, 'arch': 'x64'}}, None)
        def post(self, request):
            calls.append(request['operation'])
            reply = {'acknowledged': request['sequence']} if request['operation'] == 'ack' else {
                'native_generation': 1, 'native_frame': 7, 'frame': 7, 'game_time': 2.0,
                'native_fixed_time': str(2 << 32), 'native_process_pid': pid,
                'native_identity': {'controller': '0x1'}, 'native_stage_id': 'test-stage',
                'sequence': 1, 'events': []}
            self.callback({'type': 'send', 'payload': {'kind': 'arkdps-event-response',
                           'id': request['id'], 'result': reply}}, None)
        def unload(self):
            calls.append('unload')
            self.is_destroyed = True
    class Session:
        is_detached = False
        def on(self, name, callback):
            assert name == 'detached'
        def create_script(self, source):
            assert 'arkdps-event-request' in source
            return Script()
        def detach(self):
            calls.append('detach')
            self.is_detached = True
    def attach(target, *, realm):
        assert (target, realm) == (pid, 'native')
        return Session()
    device = SimpleNamespace(enumerate_processes=lambda: [SimpleNamespace(pid=pid)], attach=attach)
    def remote(address, **kwargs):
        assert address == '127.0.0.1:27382' and kwargs['token'] == runtime._credentials(lease)
        return device
    manager = SimpleNamespace(add_remote_device=remote,
        remove_remote_device=lambda address: calls.append(('remove', address)))
    class Cancellable:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def cancel(self):
            pass
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(__version__=module.FRIDA_VERSION,
        get_device_manager=lambda: manager, Cancellable=Cancellable))
    with runtime._condition:
        runtime._state(module.RuntimeState.AUTHENTICATING)
        runtime._release_endpoint()
        runtime._verify_endpoint = lambda record: NativePcFridaRuntime._verify_endpoint(runtime, record)
    runtime.wait_ready(2)
    collector = SimpleNamespace(
        process=SimpleNamespace(pid=pid, process_instance=(pid, lease['created']), process_alive=lambda: True),
        diagnostics=SimpleNamespace(connection_ok=True, version_status=VersionStatus.COMPATIBLE),
        observer_binding=lambda **kw: {'native_session': 'independent-session', 'source_build': module.SOURCE_BUILD})
    observer = runtime.observer_factory(collector, None)
    observer.start()
    assert observer.status == ObserverStatus.READY
    assert observer.mark()['native_session'] == 'independent-session'
    assert observer.read_through(1)['events'] == []
    observer.commit_events(1)
    observer.stop()
    assert calls == ['mark', 'read', 'ack', 'stop', 'unload', 'detach']
    assert runtime.config_path.exists() and runtime.describe()['expected_resident']
    assert len(environment.loaders.loads) == 1
    assert runtime.describe()['endpoint_owned'] and runtime.describe()['active_observers'] == 0
    runtime.close()
    assert calls[-1] == ('remove', '127.0.0.1:27382')
