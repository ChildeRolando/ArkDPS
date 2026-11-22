"""Finite lifecycle traces against fake Windows births and real runtime logic.

The reference rules are ownership and identity rules, not a second copy of the
runtime transition table. Only explicit retry repairs a hard prerequisite or
identity failure; availability waits recover automatically. Cancel/close are
terminal for an owner, and an owned process restart retains early eligibility.
No Windows API, game, socket, or real Frida transport is used here.
"""
from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import threading
import time as wall_time
from types import SimpleNamespace

import pytest

from connection import VersionStatus
from event_observer import ObserverStatus
from native_pc import frida_runtime as module


@dataclass(eq=False)
class Birth:
    pid: int
    created: int
    path: Path
    alive: bool = True
    module: bool = False


@dataclass(eq=False)
class Handle:
    birth: Birth
    closed: bool = False


class Ledger:
    def __init__(self, world):
        self.world = world
        self.acquired = []
        self.released = []

    def take(self, kind, owner):
        token = object()
        self.acquired.append((token, kind, owner))
        return token

    def drop(self, token):
        self.world.require(any(item[0] is token for item in self.acquired), 'unknown resource released')
        self.world.require(token not in self.released, 'resource released twice')
        self.released.append(token)

    def live(self, kind=None):
        return [(token, resource_kind, owner)
                for token, resource_kind, owner in self.acquired
                if token not in self.released and (kind is None or resource_kind == kind)]


class ProcessApi:
    """A handle retains its birth even if the PID registry is replaced."""
    def __init__(self, world):
        self.world = world
        self.current = {}
        self.handles = []

    def named_processes(self):
        result = list(self.current)
        if result:
            self.world.hook('after_scan')
        return result

    def open_process(self, pid, rights):
        birth = self.current.get(pid)
        if birth is None:
            raise module.access.ApiFailure('OpenProcess', 87)
        handle = Handle(birth)
        handle.token = self.world.ledger.take('process', handle)
        self.handles.append(handle)
        return handle

    @staticmethod
    def process_times(handle):
        assert not handle.closed
        return handle.birth.created, 0

    @staticmethod
    def image_path(handle):
        assert not handle.closed
        return handle.birth.path

    @staticmethod
    def exit_code(handle):
        assert not handle.closed
        return 259 if handle.birth.alive else 0

    def close(self, handle):
        self.world.require(not handle.closed, 'process handle closed twice')
        handle.closed = True
        self.world.ledger.drop(handle.token)


class Loader:
    def __init__(self, world, pid):
        self.world = world
        self.handle = world.api.open_process(pid, 0x43a)
        self.birth = self.handle.birth
        self.created_filetime = self.birth.created
        self.pending_thread = None
        self.load_error = None
        self.closed = False
        self.path_token = None
        world.loaders.append(self)
        world.hook('before_load')

    def find_module(self, path):
        return {'base': 0x10000} if self.birth.module else None

    def load(self, path):
        self.world.require(not self.world.runtime._cancel.is_set(), 'load after cancellation')
        assert self.birth.alive
        self.world.loads.append(self.birth)
        self.world.endpoint_birth = self.birth
        config = json.loads(self.world.runtime.config_path.read_text(encoding='utf-8'))
        self.world.endpoint_token = config['interaction']['token']
        if self.world.load_pending:
            self.pending_thread = self.world.ledger.take('loader_thread', self)
            self.path_token = self.world.ledger.take('loader_path', self)
            raise TimeoutError('controlled loader remains pending')
        self.birth.module = True

    def poll(self, **_kwargs):
        if self.pending_thread is not None:
            if self.birth.alive and not self.world.complete_load:
                return False
            self.world.ledger.drop(self.pending_thread)
            self.world.ledger.drop(self.path_token)
            self.pending_thread = self.path_token = None
            self.birth.module = self.birth.alive
            if not self.birth.alive:
                self.load_error = module.RuntimeFailure('loader_failed', 'controlled process exited during load')
        return True

    def close(self):
        if self.closed:
            return
        if not self.poll():
            raise module.RuntimeFailure('cleanup_pending', 'controlled loader still owns resources')
        self.world.api.close(self.handle)
        self.closed = True


class Cancellable:
    def __init__(self, world):
        self.world = world
        self.cancelled = False
        self.wake = threading.Event()
        world.cancellables.append(self)

    def cancel(self):
        self.cancelled = True
        self.wake.set()

    def __enter__(self):
        self.previous = self.world.rpc
        self.world.rpc = self
        self.check()
        return self

    def __exit__(self, *_args):
        self.world.rpc = self.previous

    def check(self):
        if self.cancelled:
            raise RuntimeError('controlled RPC cancelled')


class EndpointManager:
    def __init__(self, world):
        self.world = world
        self.registrations = []

    def add_remote_device(self, address, *, token):
        self.world.hook('endpoint_add')
        if self.world.rpc is not None:
            self.world.rpc.check()
        if not self.world.endpoint_available:
            raise OSError('controlled endpoint is not listening')
        if token != self.world.endpoint_token:
            raise RuntimeError('controlled authentication rejected')
        assert address == f'{module.ADDRESS}:{module.PORT}'
        device = SimpleNamespace(enumerate_processes=self.enumerate_processes,
                                 attach=lambda pid, **kwargs: self.world.attach(pid, **kwargs))
        resource = self.world.ledger.take('endpoint', device)
        self.registrations.append((address, resource))
        self.world.transport_calls.append('endpoint_add')
        return device

    def enumerate_processes(self):
        self.world.hook('endpoint_enumerate')
        if self.world.rpc is not None:
            self.world.rpc.check()
        pid = self.world.endpoint_pid
        if pid is None:
            pid = self.world.endpoint_birth.pid
        self.world.hook('endpoint_reply')
        return [SimpleNamespace(pid=pid)]

    def remove_remote_device(self, address):
        self.world.transport_calls.append('endpoint_remove')
        if self.world.endpoint_remove_failures:
            self.world.endpoint_remove_failures -= 1
            raise RuntimeError('controlled endpoint removal failed')
        index = next(index for index, item in enumerate(self.registrations) if item[0] == address)
        _, resource = self.registrations.pop(index)
        self.world.ledger.drop(resource)


class InlineThread:
    """Execute the watcher synchronously; scripted Event.wait drives time."""
    def __init__(self, *, target, **_kwargs):
        self.target = target
        self.running = False

    def start(self):
        self.running = True
        try:
            self.target()
        finally:
            self.running = False

    def is_alive(self):
        return self.running

    def join(self, **_kwargs):
        assert not self.running, 'test attempted to join its active inline watcher'


@dataclass
class Action:
    label: str
    perform: object
    remaining: int = 40


class ScriptedEvent:
    def __init__(self, world):
        self.world = world
        self.flag = False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def wait(self, seconds=None):
        self.world.check(require_current=True)
        self.world.now += max(seconds or 0, .1)
        if not self.world.actions:
            self.world.runtime.cancel()
            return True
        action = self.world.actions[0]
        self.world.history.append(action.label)
        action.remaining -= 1
        if action.remaining < 0:
            # pytest's outcome exception escapes the watcher's Exception
            # handler instead of recursively becoming another runtime error.
            pytest.fail(self.world.diagnostic('bounded trace did not converge'))
        if action.perform():
            self.world.actions.popleft()
        self.world.check()
        return self.flag


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.now = 100.0
        self.state_dir = tmp_path / 'state'
        self.root = tmp_path / 'controlled game'
        self.root.mkdir()
        self.asset = tmp_path / module.GADGET_NAME
        self.asset_bytes = b'synthetic lifecycle asset, never executed'
        self.asset.write_bytes(self.asset_bytes)
        self.violations = []
        self.ledger = Ledger(self)
        self.api = ProcessApi(self)
        self.loaders, self.loads, self.cancellables = [], [], []
        self.actions, self.history = deque(), []
        self.ready_publications = []
        self.births = []
        self.port_busy = False
        self.endpoint_available = True
        self.endpoint_birth = self.endpoint_token = self.endpoint_pid = None
        self.endpoint_remove_failures = 0
        self.session_detach_failures = 0
        self.transport_calls = []
        self.attach = None
        self.load_pending = self.complete_load = False
        self.build_known = self.admin = True
        self.cancel_at = None
        self._rpc_state = threading.local()
        self.rpc = None
        self.manager = EndpointManager(self)
        monkeypatch.setattr(module, 'GADGET_SHA256', hashlib.sha256(self.asset_bytes).hexdigest())
        monkeypatch.setattr(module, 'Thread', InlineThread)
        monkeypatch.setattr(module, 'time', SimpleNamespace(
            monotonic=lambda: self.now,
            time_ns=lambda: int(self.now * 1_000_000_000),
            sleep=lambda seconds: setattr(self, 'now', self.now + seconds)))
        monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(
            __version__=module.FRIDA_VERSION,
            Cancellable=lambda: Cancellable(self),
            get_device_manager=lambda: self.manager))
        self.new_owner()

    def new_owner(self):
        if hasattr(self, 'runtime'):
            assert self.runtime.describe()['status'] == 'closed'
        self.runtime = module.NativePcFridaRuntime(self.root, runtime_dir=self.state_dir,
            gadget_path=self.asset, api=self.api,
            loader_factory=lambda pid, **_kwargs: Loader(self, pid),
            build_check=lambda _root: ({'known_build': self.build_known, 'source_build': module.access.BUILD_ID}, []),
            admin_check=lambda: self.admin)
        self.runtime._cancel = ScriptedEvent(self)
        self.runtime._port_occupied = lambda: self.port_busy
        original_state = self.runtime._state

        def publish(status, *args, **kwargs):
            if status == 'ready':
                self.require(not self.runtime._cancel.is_set(), 'ready published after cancellation')
                self.ready_publications.append(self.runtime.describe()['lease'])
            result = original_state(status, *args, **kwargs)
            self.check(require_current=True)
            return result

        self.runtime._state = publish

    def diagnostic(self, message):
        return f'{message}; history={self.history!r}; state={self.runtime.describe()!r}'

    @property
    def rpc(self):
        return getattr(self._rpc_state, 'current', None)

    @rpc.setter
    def rpc(self, value):
        self._rpc_state.current = value

    def require(self, condition, message):
        # The production watcher catches ordinary Exception. Persist violations
        # so that swallowing an AssertionError cannot make a trace pass.
        if not condition:
            self.violations.append(self.diagnostic(message))
            raise AssertionError(self.violations[-1])

    def hook(self, boundary):
        if self.cancel_at == boundary:
            self.cancel_at = None
            self.runtime.cancel()

    def spawn(self, pid=101, *, correct=True):
        self.now += .01
        created = (module.FILETIME_EPOCH + int(self.now * 10_000_000)
                   if isinstance(self.runtime._cancel, ScriptedEvent)
                   else module.FILETIME_EPOCH + wall_time.time_ns() // 100)
        birth = Birth(pid, created,
                      self.root / ('Arknights.exe' if correct else 'Other.exe'))
        previous = self.api.current.get(pid)
        assert previous is None or not previous.alive, 'PID reused before its previous birth exited'
        self.api.current[pid] = birth
        self.births.append(birth)
        return birth

    def check(self, *, require_current=False):
        assert not self.violations, '\n'.join(self.violations)
        counts = Counter(self.loads)
        self.require(all(count <= 1 for count in counts.values()), 'multiple loads for a birth')
        state = self.runtime.describe()
        if state['status'] == 'ready':
            lease = state['lease']
            self.require(lease is not None, 'ready without lease')
            matching = [birth for birth in self.births
                        if (birth.pid, birth.created) == (lease['pid'], lease['created'])]
            self.require(len(matching) == 1, 'ready lease has no unique birth')
            if require_current:
                self.require(matching[0].alive and matching[0].module, 'ready for a dead/unloaded birth')
                self.require(self.endpoint_birth is matching[0], 'ready endpoint belongs to another birth')
                self.require(self.endpoint_pid in (None, matching[0].pid), 'ready endpoint authenticates another PID')
                persisted = json.loads(self.runtime.lease_path.read_text(encoding='utf-8'))
                self.require(persisted == lease, 'published lease differs from persisted lease')
                self.require(hashlib.sha256(self.runtime.config_path.read_bytes()).hexdigest()
                             == lease['config_sha256'], 'ready lease authenticates different credentials')
        for token, kind, owner in self.ledger.live():
            if kind in ('loader_thread', 'loader_path'):
                self.require(not owner.closed, 'pending resource belongs to a closed loader')
                self.require(self.runtime._pending_loader is owner, 'pending resource lost its owner')

    def do(self, label, callback):
        def perform():
            callback()
            return True
        return Action(label, perform)

    def until(self, label, predicate):
        return Action(label, predicate)

    def status(self, status):
        return self.until(f'wait {status}', lambda: self.runtime.describe()['status'] == status)

    def ready(self, birth=None):
        def predicate():
            state = self.runtime.describe()
            return state['status'] == 'ready' and (birth is None or (
                state['lease']['pid'], state['lease']['created']) == (birth.pid, birth.created))
        return self.until('wait matching ready', predicate)

    def drive(self, actions, *, retry=False):
        self.actions.extend(actions)
        (self.runtime.retry if retry else self.runtime.start)()
        assert not self.actions, self.diagnostic('watcher stopped before trace completion')
        self.check()

    def close(self):
        self.runtime.close()
        assert not self.ledger.live(), self.diagnostic('resources remain after completed cleanup')


@pytest.fixture
def world(tmp_path, monkeypatch):
    value = World(tmp_path, monkeypatch)
    yield value
    # Never let teardown turn a resource leak into an apparent successful test.
    for birth in value.births:
        birth.alive = False
    value.endpoint_remove_failures = 0
    value.complete_load = True
    # drive()/check() already surface these persistent oracle failures in the
    # test body. Let final resource cleanup run without repeating that failure.
    value.violations.clear()
    value.runtime.close()
    assert not value.ledger.live(), value.diagnostic('teardown retained resources')


@pytest.mark.parametrize('busy_ticks', [0, 2])
@pytest.mark.parametrize('endpoint_delay', [0, 3])
def test_availability_sequences_recover_without_another_load(world, busy_ticks, endpoint_delay):
    world.port_busy = bool(busy_ticks)
    world.endpoint_available = not endpoint_delay
    actions = []
    if busy_ticks:
        actions.append(world.status('waiting_for_endpoint'))
        actions.extend(world.do('busy endpoint tick', lambda: None) for _ in range(busy_ticks))
        actions.append(world.do('release foreign endpoint', lambda: setattr(world, 'port_busy', False)))
    actions.extend([world.status('waiting_for_process'), world.do('new birth', world.spawn)])
    if endpoint_delay:
        actions.append(world.until('wait authentication retry', lambda: bool(world.loads)))
        actions.extend(world.do('endpoint startup tick', lambda: None) for _ in range(endpoint_delay))
        actions.append(world.do('endpoint starts listening', lambda: setattr(world, 'endpoint_available', True)))
    actions.extend([world.ready(), world.do('assert one committed load', lambda: pytest_assert(len(world.loads) == 1))])
    world.drive(actions)
    world.close()


@pytest.mark.parametrize('reuse_pid', [False, True])
@pytest.mark.parametrize('restart_gap', [False, True])
def test_owned_restart_preserves_new_birth_eligibility(world, reuse_pid, restart_gap):
    births = {}
    def first():
        births['first'] = world.spawn()
    def second():
        births['second'] = world.spawn(101 if reuse_pid else 202)
    def exit_first():
        births['first'].alive = False
    actions = [world.status('waiting_for_process'), world.do('first birth', first), world.ready()]
    if restart_gap:
        actions.extend([world.do('first birth exits', exit_first), world.status('waiting_for_process'),
                        world.do('second birth starts', second)])
    else:
        actions.append(world.do('exit and immediate restart', lambda: (exit_first(), second())))
    actions.append(world.until('new birth is ready', lambda: 'second' in births and
        world.runtime.describe()['status'] == 'ready' and
        world.runtime.describe()['lease']['created'] == births['second'].created))
    world.drive(actions)
    assert world.loads == [births['first'], births['second']]
    world.close()


def test_ambiguity_and_wrong_path_never_claim_an_unselected_birth(world):
    births = {}
    def several():
        births['selected'] = world.spawn(101)
        births['extra'] = world.spawn(202)
        births['wrong_path'] = world.spawn(303, correct=False)
    world.drive([world.status('waiting_for_process'), world.do('several processes', several),
        world.until('ambiguous exact paths', lambda: world.runtime.describe()['code'] == 'ambiguous_process'),
        world.do('extra exact-path process exits', lambda: setattr(births['extra'], 'alive', False)),
        world.ready()])
    assert world.loads == [births['selected']]
    world.close()


@pytest.mark.parametrize('boundary', ['after_scan', 'before_load', 'endpoint_add', 'endpoint_enumerate',
                                     'endpoint_reply'])
def test_cancellation_at_resource_boundaries_never_publishes_ready(world, boundary):
    def spawn_and_cancel_at_boundary():
        world.cancel_at = boundary
        world.spawn()
    world.drive([world.status('waiting_for_process'), world.do('arm cancellation boundary', spawn_and_cancel_at_boundary)])
    assert not world.ready_publications
    if boundary in ('after_scan', 'before_load'):
        assert not world.loads
    world.close()


def test_pending_loader_cleanup_failure_retains_the_owner(world):
    world.load_pending = True
    world.drive([world.status('waiting_for_process'), world.do('new birth', world.spawn),
        world.until('owned pending loader', lambda: world.runtime._pending_loader is not None and
                    world.runtime._pending_loader.pending_thread is not None)])
    owner = world.runtime._pending_loader
    with pytest.raises(module.RuntimeFailure) as failure:
        world.runtime.close()
    assert failure.value.code == 'cleanup_pending'
    assert world.runtime._pending_loader is owner
    assert len(world.ledger.live('loader_thread')) == len(world.ledger.live('loader_path')) == 1
    world.check()
    world.complete_load = True
    world.close()
    assert not world.ready_publications


@pytest.mark.parametrize('prerequisite', ['missing_asset', 'unsupported_build', 'asset_hash'])
def test_explicit_retry_rechecks_a_repaired_preflight(world, prerequisite):
    if prerequisite == 'missing_asset':
        world.asset.unlink()
    elif prerequisite == 'unsupported_build':
        world.build_known = False
    else:
        world.asset.write_bytes(b'corrupted synthetic asset')
    def verify_failure():
        assert not world.loads and not world.runtime.lease_path.exists()
        assert not world.ledger.live()

    def repair_and_retry():
        world.asset.write_bytes(world.asset_bytes)
        world.build_known = True
        world.runtime.retry()

    world.drive([world.status('failed'), world.do('failed preflight has no side effects', verify_failure),
                 world.do('operator repairs and retries', repair_and_retry),
                 world.status('waiting_for_process'), world.do('new birth after repair', world.spawn),
                 world.ready()])
    assert len(world.loads) == 1
    world.close()


def test_authentication_identity_failure_requires_explicit_retry_without_reloading(world):
    world.endpoint_pid = 999
    def identity_failure():
        return world.runtime.describe()['code'] == 'endpoint_mismatch'
    def repair():
        world.endpoint_pid = None
    def explicitly_retry():
        assert world.runtime.describe()['status'] != 'ready'
        world.runtime.retry()
    world.drive([world.status('waiting_for_process'), world.do('new birth', world.spawn),
        world.until('identity failure', identity_failure), world.do('repair endpoint identity', repair),
        world.do('ordinary retry tick', lambda: None),
        world.do('operator retries identity check', explicitly_retry), world.ready()])
    assert len(world.loads) == 1
    world.close()


def test_endpoint_cleanup_failure_retains_registration_until_retry(world):
    world.drive([world.status('waiting_for_process'), world.do('new birth', world.spawn), world.ready()])
    assert len(world.ledger.live('endpoint')) == 1
    world.endpoint_remove_failures = 1
    with pytest.raises(module.RuntimeFailure) as failure:
        world.runtime.close()
    assert failure.value.code == 'endpoint_cleanup_pending'
    assert len(world.ledger.live('endpoint')) == 1
    assert world.runtime.describe()['endpoint_owned']
    world.close()


@pytest.mark.parametrize('terminal_operation', ['cancel', 'close'])
@pytest.mark.parametrize('started', [False, True])
def test_start_after_terminal_operation_is_rejected(world, terminal_operation, started):
    if started:
        world.drive([world.status('waiting_for_process')])
    getattr(world.runtime, terminal_operation)()
    with pytest.raises((RuntimeError, InterruptedError)):
        world.runtime.start()
    assert not world.loads


@pytest.mark.parametrize('cancel_owner', [False, True])
def test_cancellation_is_checked_even_when_already_ready(world, cancel_owner):
    def cancelled_ready_wait():
        if cancel_owner:
            world.runtime.cancel()
        with pytest.raises(InterruptedError):
            world.runtime.wait_ready(0, cancelled=None if cancel_owner else lambda: True)
    world.drive([world.status('waiting_for_process'), world.do('new birth', world.spawn), world.ready(),
                 world.do('cancel already-ready waiter', cancelled_ready_wait)])
    world.close()


def test_fake_handles_retain_birth_identity_when_the_pid_is_reused(world):
    first = world.spawn()
    handle = world.api.open_process(first.pid, 0x1000)
    first.alive = False
    second = world.spawn(first.pid)
    assert world.api.process_times(handle)[0] == first.created != second.created
    assert world.api.exit_code(handle) == 0
    world.api.close(handle)


def test_a_pending_old_birth_cannot_transfer_its_loader_to_a_reused_pid(world):
    world.load_pending = True
    births = {}

    def first():
        births['old'] = world.spawn()

    def replace_pending_birth():
        births['old'].alive = False
        world.load_pending = False
        births['new'] = world.spawn(births['old'].pid)

    world.drive([world.status('waiting_for_process'), world.do('birth with pending loader', first),
        world.until('old loader pending', lambda: bool(world.ledger.live('loader_thread'))),
        world.do('old birth exits and PID is reused', replace_pending_birth),
        world.until('new birth ready', lambda: 'new' in births and
            world.runtime.describe()['status'] == 'ready' and
            world.runtime.describe()['lease']['created'] == births['new'].created)])
    assert world.loads == [births['old'], births['new']]
    assert not births['old'].module and births['new'].module
    assert not world.ledger.live('loader_thread') and not world.ledger.live('loader_path')
    world.close()


@pytest.mark.parametrize('delayed_endpoint', [False, True])
def test_a_new_owner_reconnects_the_live_lease_without_reloading(world, delayed_endpoint):
    world.drive([world.status('waiting_for_process'), world.do('new birth', world.spawn), world.ready()])
    lease = world.runtime.describe()['lease']
    world.close()
    assert world.runtime.lease_path.exists() and world.runtime.config_path.exists()
    world.new_owner()
    world.endpoint_available = not delayed_endpoint
    actions = []
    if delayed_endpoint:
        actions.extend([world.until('existing lease authentication waits', lambda:
                            world.runtime.describe()['code'] == 'endpoint_unavailable'),
                        world.do('existing endpoint returns', lambda:
                            setattr(world, 'endpoint_available', True))])
    world.drive([*actions, world.ready()])
    assert world.runtime.describe()['lease'] == lease
    assert len(world.loads) == 1
    world.close()


def test_foreign_startup_files_are_preserved_until_explicit_repair(world):
    lease_bytes = b'{"version":1,"foreign_owner":"preserve"}\n'
    config_bytes = b'foreign credentials, never parse or replace'
    world.runtime.lease_path.parent.mkdir(parents=True, exist_ok=True)
    world.runtime.lease_path.write_bytes(lease_bytes)
    world.runtime.config_path.write_bytes(config_bytes)
    def verify_foreign_files():
        assert world.runtime.lease_path.read_bytes() == lease_bytes
        assert world.runtime.config_path.read_bytes() == config_bytes
        assert not world.loads and not world.ledger.live()

    def repair_and_retry():
        # Simulate the operator resolving a conflict in this synthetic directory.
        world.runtime.lease_path.unlink()
        world.runtime.config_path.unlink()
        world.runtime.retry()

    world.drive([world.until('foreign lease blocked', lambda: world.runtime.describe()['code'] == 'lease_conflict'),
                 world.do('foreign files preserved', verify_foreign_files),
                 world.do('operator resolves conflict and retries', repair_and_retry),
                 world.status('waiting_for_process'), world.do('birth after explicit repair', world.spawn),
                 world.ready()])
    assert len(world.loads) == 1
    world.close()


def test_stale_lease_cannot_authorize_an_already_running_reused_pid(world):
    world.drive([world.status('waiting_for_process'), world.do('original birth', world.spawn), world.ready()])
    old = world.loads[0]
    world.close()
    old.alive = False
    already_running = world.spawn(old.pid)
    world.new_owner()

    def restart():
        already_running.alive = False
        world.spawn(old.pid)

    def confirm_no_authority():
        assert len(world.loads) == 1
        assert world.runtime.describe()['lease'] is None
        assert not world.runtime.config_path.exists()

    world.drive([world.until('reused PID needs a new start', lambda:
                    world.runtime.describe()['code'] == 'restart_required'),
                 world.do('stale lease did not authorize PID reuse', confirm_no_authority),
                 world.do('operator normally restarts process', restart), world.ready()])
    assert len(world.loads) == 2
    assert already_running not in world.loads
    world.close()


def pytest_assert(condition):
    assert condition


class ObservedScript:
    def __init__(self, world):
        self.world = world
        self.is_destroyed = False
        self.token = world.ledger.take('script', self)
        self.exports_sync = SimpleNamespace(stop=lambda: world.transport_calls.append('script_stop'))

    def on(self, event, callback):
        assert event == 'message'
        self.callback = callback

    def load(self):
        self.world.hook('script_load')
        if self.world.rpc is not None:
            self.world.rpc.check()
        self.callback({'type': 'send', 'payload': {'ready': True, 'arch': 'x64'}}, None)

    def unload(self):
        self.world.transport_calls.append('script_unload')
        self.is_destroyed = True
        self.world.ledger.drop(self.token)


class ObservedSession:
    def __init__(self, world):
        self.world = world
        self.is_detached = False
        self.token = world.ledger.take('session', self)

    def on(self, event, callback):
        assert event == 'detached'
        self.detached_callback = callback

    def create_script(self, source):
        assert 'arkdps-event-request' in source
        return ObservedScript(self.world)

    def detach(self):
        if self.world.session_detach_failures:
            self.world.session_detach_failures -= 1
            self.world.transport_calls.append('session_detach_failed')
            raise RuntimeError('controlled session detach failed')
        self.world.transport_calls.append('session_detach')
        self.is_detached = True
        self.world.ledger.drop(self.token)


def make_observer(world):
    birth = world.loads[-1]
    collector = SimpleNamespace(
        process=SimpleNamespace(pid=birth.pid, process_instance=(birth.pid, birth.created),
                                process_alive=lambda: birth.alive),
        diagnostics=SimpleNamespace(connection_ok=True, version_status=VersionStatus.COMPATIBLE,
                                    version_actual=module.access.BUILD_ID))
    return world.runtime.observer_factory(collector, None)


def wait_thread_state(world, predicate):
    deadline = wall_time.monotonic() + 3
    pause = threading.Event()
    while wall_time.monotonic() < deadline:
        if predicate():
            return
        pause.wait(.005)
    pytest.fail(world.diagnostic('real-thread barrier did not converge'))


@pytest.mark.parametrize('blocked_operation', ['endpoint_enumerate', 'script_load'])
def test_real_thread_cancel_unblocks_rpc_and_releases_all_owned_resources(world, monkeypatch, blocked_operation):
    world.runtime._cancel = threading.Event()
    monkeypatch.setattr(module, 'Thread', threading.Thread)
    monkeypatch.setattr(module, 'time', wall_time)
    entered, returned = threading.Event(), threading.Event()
    observer = startup_thread = None
    startup_errors = []

    def barrier(boundary):
        if boundary != blocked_operation:
            return
        cancellation = world.rpc
        world.require(cancellation is not None, 'blocked RPC lacks a current Cancellable')
        entered.set()
        world.require(cancellation.wake.wait(3), 'runtime cancellation did not wake blocked RPC')
        returned.set()
        cancellation.check()

    world.hook = barrier
    try:
        world.runtime.start()
        wait_thread_state(world, lambda: world.runtime.describe()['status'] == 'waiting_for_process')
        world.spawn()
        if blocked_operation == 'script_load':
            world.runtime.wait_ready(3)
            world.attach = lambda *_args, **_kwargs: ObservedSession(world)
            observer = make_observer(world)

            def start_observer():
                try:
                    observer.start()
                except BaseException as exc:
                    startup_errors.append(exc)

            startup_thread = threading.Thread(target=start_observer, name='controlled observer startup')
            startup_thread.start()
        assert entered.wait(3), world.diagnostic('RPC barrier was not entered')
        assert world.runtime.describe()['thread_alive']
        assert len(world.ledger.live('endpoint')) == 1
        world.runtime.cancel()
        assert returned.wait(3), 'cancellation did not release fake RPC'
        if startup_thread is not None:
            startup_thread.join(3)
            assert not startup_thread.is_alive()
            assert len(startup_errors) == 1 and 'cancel' in str(startup_errors[0]).lower()
            assert observer.session is None and observer.script is None
        world.close()
        state = world.runtime.describe()
        assert state['status'] == 'closed' and not state['thread_alive']
        assert state['active_observers'] == 0 and not state['endpoint_owned']
        assert any(cancellation.cancelled for cancellation in world.cancellables)
    finally:
        world.runtime.cancel()
        if startup_thread is not None:
            startup_thread.join(3)
        if observer is not None:
            observer.stop()
        world.runtime.close()


def test_cancel_after_attach_returns_retains_and_detaches_the_session(world):
    sessions = []

    def attach_and_cancel(pid, *, realm):
        assert realm == 'native' and pid == world.loads[-1].pid
        session = ObservedSession(world)
        sessions.append(session)
        world.runtime.cancel()
        return session

    def cancelled_attachment():
        world.attach = attach_and_cancel
        observer = make_observer(world)
        with pytest.raises(InterruptedError):
            observer.start()
        assert observer.status == ObserverStatus.FAILED
        assert observer.session is None and observer.script is None
        assert sessions[0].is_detached
        assert not world.ledger.live('session')
        assert world.runtime.describe()['active_observers'] == 0

    world.drive([world.status('waiting_for_process'), world.do('new birth', world.spawn), world.ready(),
                 world.do('cancel while returning physical attachment', cancelled_attachment)])
    assert len(sessions) == 1
    world.close()


def test_transport_recovery_waits_for_physical_observer_release_and_advances_once(world):
    owned = {}

    def start_observer():
        world.attach = lambda *_args, **_kwargs: ObservedSession(world)
        owned['observer'] = make_observer(world)
        owned['observer'].start()
        assert owned['observer'].status == ObserverStatus.READY
        owned['generation'] = world.runtime.describe()['ready_generation']
        assert world.runtime.describe()['active_observers'] == 1

    def assert_original_registration():
        state = world.runtime.describe()
        assert state['status'] == 'authenticating' and state['active_observers'] == 1
        assert state['ready_generation'] == owned['generation']
        assert world.transport_calls.count('endpoint_add') == 1
        assert world.transport_calls.count('endpoint_remove') == 0

    def partially_release_observer():
        world.session_detach_failures = 1
        with pytest.raises(RuntimeError, match='controlled session detach failed'):
            owned['observer'].stop()
        assert owned['observer'].script is None
        assert owned['observer'].session is not None
        assert_original_registration()

    def assert_one_reauthentication():
        assert world.runtime.describe()['ready_generation'] == owned['generation'] + 1
        assert world.runtime.describe()['active_observers'] == 0
        assert world.transport_calls.count('endpoint_add') == 2
        assert world.transport_calls.count('endpoint_remove') == 1
        calls = world.transport_calls
        assert calls.index('script_unload') < calls.index('session_detach') < calls.index('endpoint_remove')
        assert len(world.loads) == 1

    world.drive([world.status('waiting_for_process'), world.do('new birth', world.spawn), world.ready(),
        world.do('start physical observer', start_observer),
        world.do('report observer transport failure', world.runtime.report_transport_failure),
        *[world.do('active observer blocks endpoint replacement', assert_original_registration) for _ in range(3)],
        world.do('observer cleanup partly fails', partially_release_observer),
        *[world.do('retained session blocks endpoint replacement', assert_original_registration) for _ in range(3)],
        world.do('release retained physical session', lambda: owned['observer'].stop()), world.ready(),
        *[world.do('single recovery generation', assert_one_reauthentication) for _ in range(3)]])
    world.close()
