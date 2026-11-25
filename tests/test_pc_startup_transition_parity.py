"""Resource ownership at the shared startup transitions; no native API calls."""
from threading import Condition, Event

import pytest

from native_pc import frida_runtime, native_hook_runtime


@pytest.fixture(params=(frida_runtime, native_hook_runtime))
def startup(request, monkeypatch):
    module = request.param
    kind = module.NativePcFridaRuntime if module is frida_runtime else module.NativeHookRuntime
    owner = object.__new__(kind)
    owner._condition, owner._cancel = Condition(), Event()
    owner.status, owner._thread = module.RuntimeState.STOPPED, None
    owner._rpc_cancellations, owner._observers = set(), set()
    owner._run = lambda: None
    transitions = []

    def state(value, *args, **kwargs):
        transitions.append(value)
        owner.status = value

    monkeypatch.setattr(owner, '_state', state)
    return module, owner, transitions


@pytest.mark.parametrize('alive', (False, True))
def test_failed_thread_start_retains_any_live_watcher_before_retry(startup, monkeypatch, alive):
    module, owner, transitions = startup
    created = []

    class Watcher:
        def __init__(self, **options):
            created.append(self)

        def start(self):
            raise RuntimeError('controlled start result lost')

        def is_alive(self):
            return alive

    monkeypatch.setattr(module, 'Thread', Watcher)
    with pytest.raises(RuntimeError, match='result lost'):
        owner.start()
    if alive:
        assert owner._thread is created[0]
        assert owner.status == module.RuntimeState.STARTING
        assert owner.start() is owner
        assert len(created) == 1  # No second watcher while the first may run.
    else:
        assert owner._thread is None
        assert owner.status == module.RuntimeState.FAILED


def test_cancellation_does_not_erase_pending_cleanup_or_its_owned_resource(startup):
    module, owner, transitions = startup
    resource = object()
    owner._thread = resource
    owner.status = module.RuntimeState.CLEANUP_PENDING
    owner.cancel()
    assert owner._cancel.is_set()
    assert owner.status == module.RuntimeState.CLEANUP_PENDING
    assert owner._thread is resource
    assert not transitions


def test_cancelled_owner_cannot_admit_a_windows_commit(startup):
    module, owner, transitions = startup
    owner.cancel()
    operations = []
    with pytest.raises(InterruptedError):
        owner._commit(lambda: operations.append('Windows mutation'))
    assert not operations
    assert owner.status == module.RuntimeState.STOPPING
