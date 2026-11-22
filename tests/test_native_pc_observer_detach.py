"""Real observer startup/cleanup callbacks against the shared resource ledger."""
from threading import Event
from types import SimpleNamespace

import pytest

from event_observer import ObserverStatus
from test_native_pc_frida_state_sequences import ObservedScript, ObservedSession, World, make_observer


SCRIPT_ERROR = {
    'type': 'error', 'description': 'Error: access violation accessing 0x10',
    'stack': 'Error: access violation accessing 0x10\n    at className (event_probe_native_pc.js:134)',
    'fileName': 'event_probe_native_pc.js', 'lineNumber': 134, 'columnNumber': 10,
    'unrelated': {'ignored': True},
}


class CallbackScript(ObservedScript):
    def __init__(self, session):
        super().__init__(session.world)
        self.session = session
        self.exports_sync = SimpleNamespace(stop=self.stop)

    def load(self):
        if self.session.during_load is not None:
            self.session.during_load()
        if self.session.fail:
            self.callback(dict(SCRIPT_ERROR), None)
        else:
            super().load()

    def stop(self):
        self.world.transport_calls.append('script_stop')
        if self.session.detach_during_stop:
            self.session.disconnect('application-requested')


class CallbackSession(ObservedSession):
    def __init__(self, world, *, fail=False, defer=False, detach_during_stop=False, during_load=None):
        super().__init__(world)
        self.fail, self.defer = fail, defer
        self.detach_during_stop = detach_during_stop
        self.during_load = during_load
        self.delayed = []

    def create_script(self, source):
        assert 'arkdps-event-request' in source
        return CallbackScript(self)

    def detach(self):
        super().detach()
        if self.defer:
            self.delayed.append(self.detached_callback)
        else:
            self.detached_callback('application-requested')

    def disconnect(self, reason):
        assert not self.is_detached
        self.is_detached = True
        self.world.ledger.drop(self.token)
        self.detached_callback(reason)


@pytest.fixture
def case(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    observers = []
    def observer(**session_options):
        world.attach = lambda *_args, **_kwargs: CallbackSession(world, **session_options)
        value = make_observer(world)
        observers.append(value)
        return value
    world.make_callback_observer = observer
    yield world
    for value in observers:
        value.stop()
    for birth in world.births:
        birth.alive = False
    world.close()


def assert_registration_unchanged(world, expected):
    state = world.runtime.describe()
    assert state['status'] == 'ready' and state['ready_generation'] == expected['generation']
    assert world.runtime._endpoint_owner is expected['endpoint']
    assert world.transport_calls.count('endpoint_add') == 1
    assert world.transport_calls.count('endpoint_remove') == 0


def test_probe_initialization_error_cleanup_preserves_error_metadata_and_endpoint_generation(case):
    expected = {}
    def fail_probe():
        expected.update(generation=case.runtime.describe()['ready_generation'], endpoint=case.runtime._endpoint_owner)
        value = case.make_callback_observer(fail=True)
        with pytest.raises(RuntimeError, match='access violation accessing 0x10'):
            value.start()
        assert value.status == ObserverStatus.FAILED and value.error == SCRIPT_ERROR['description']
        assert value.script_error == {key: SCRIPT_ERROR[key] for key in
            ('description', 'stack', 'fileName', 'lineNumber', 'columnNumber')}
        assert value.script is value.session is None
        assert case.runtime.describe()['active_observers'] == 0
        assert not case.ledger.live('script') and not case.ledger.live('session')
        assert_registration_unchanged(case, expected)
    case.drive([case.status('waiting_for_process'), case.do('new birth', case.spawn), case.ready(),
        case.do('probe init error causes owned detach', fail_probe),
        *[case.do('cleanup must not create auth epoch', lambda: assert_registration_unchanged(case, expected))
          for _ in range(3)]])
    assert len(case.loads) == 1


def test_late_cleanup_callback_cannot_wake_a_reused_start_or_pending_request(case):
    expected = {}
    def reuse_after_failure():
        expected.update(generation=case.runtime.describe()['ready_generation'], endpoint=case.runtime._endpoint_owner)
        old = []
        def attach_old(*_args, **_kwargs):
            session = CallbackSession(case, fail=True, defer=True)
            old.append(session)
            return session
        value = case.make_callback_observer()
        case.attach = attach_old
        with pytest.raises(RuntimeError, match='access violation'):
            value.start()
        value.stop()
        assert value.script_error is not None
        def old_event_during_new_load():
            assert value.status == ObserverStatus.ATTACHING and not value.ready.is_set()
            assert value.script_error is None
            pending = SimpleNamespace(done=Event(), response=None)
            value._pending_request = pending
            try:
                old[0].delayed.pop()('application-requested')
                assert not value.ready.is_set() and not pending.done.is_set()
                assert value.error is None and value.script_error is None
                assert_registration_unchanged(case, expected)
            finally:
                value._pending_request = None
        case.attach = lambda *_args, **_kwargs: CallbackSession(case, during_load=old_event_during_new_load)
        value.start()
        assert value.status == ObserverStatus.READY and value.script_error is None
        value.stop()
        assert_registration_unchanged(case, expected)
    case.drive([case.status('waiting_for_process'), case.do('new birth', case.spawn), case.ready(),
                case.do('reuse observer while old detach callback is delayed', reuse_after_failure)])


def test_explicit_stop_marks_intent_before_exports_stop_callback(case):
    def start_then_stop():
        expected = {'generation': case.runtime.describe()['ready_generation'], 'endpoint': case.runtime._endpoint_owner}
        value = case.make_callback_observer(detach_during_stop=True)
        value.start()
        value.stop()
        assert value.status == ObserverStatus.STOPPED and value.error is None
        assert_registration_unchanged(case, expected)
    case.drive([case.status('waiting_for_process'), case.do('new birth', case.spawn), case.ready(),
                case.do('script stop triggers session callback before base finally', start_then_stop)])


def test_runtime_cancel_preserves_cancellation_reason_when_session_later_detaches(case):
    def cancel_then_detach():
        value = case.make_callback_observer()
        value.start()
        generation = case.runtime.describe()['ready_generation']
        case.runtime.cancel()
        error = value.error
        value.session.disconnect('application-requested')
        assert 'cancelled' in error and value.error == error
        assert case.runtime.describe()['ready_generation'] == generation
        assert case.transport_calls.count('endpoint_remove') == 0
        value.stop()
    case.drive([case.status('waiting_for_process'), case.do('new birth', case.spawn), case.ready(),
                case.do('owned cancellation precedes delayed session detach', cancel_then_detach)])


def test_genuine_detached_callback_still_recovers_after_physical_release(case):
    owned = {}
    def start():
        owned['observer'] = case.make_callback_observer()
        owned['observer'].start()
        owned['generation'] = case.runtime.describe()['ready_generation']
    def disconnect():
        owned['observer'].session.disconnect('connection-terminated')
        assert owned['observer'].status == ObserverStatus.FAILED
        assert owned['observer'].error == 'observer detached: connection-terminated'
        assert case.runtime.describe()['status'] == 'authenticating'
        assert case.runtime.describe()['ready_generation'] == owned['generation']
        assert case.runtime.describe()['active_observers'] == 1
        assert case.transport_calls.count('endpoint_remove') == 0
    def recovered():
        assert case.runtime.describe()['ready_generation'] == owned['generation'] + 1
        assert case.transport_calls.count('endpoint_add') == 2
        assert case.transport_calls.count('endpoint_remove') == 1
        assert len(case.loads) == 1
    case.drive([case.status('waiting_for_process'), case.do('new birth', case.spawn), case.ready(),
        case.do('start physical observer', start), case.do('real transport disconnect', disconnect),
        case.do('release failed physical observer', lambda: owned['observer'].stop()), case.ready(),
        *[case.do('only one genuine recovery epoch', recovered) for _ in range(3)]])
