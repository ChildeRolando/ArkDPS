"""Control confirmation and retained ownership across the three real transports."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from event_observer import FridaEventObserver, NativePcFridaObserver, ObserverStatus
from event_probe import ObserverReadRetry, ready_probe_modes
from native_pc.native_hook_source import NativeHookEventProbe


@pytest.fixture(params=(FridaEventObserver, NativePcFridaObserver, NativeHookEventProbe))
def probe(request):
    observer = request.param(SimpleNamespace(), **({'remote': 'unused'} if request.param is FridaEventObserver else {}))
    return observer


def reply(paused=True, revision=1, **fields):
    return dict(capture_pause_version=1, paused=paused, revision=revision,
                capture_generation=1, sequence=7, epoch=12, active=0,
                active_hooks=[], retry=False, error=None, stopped=False, **fields)


def test_pause_resume_confirmed_without_cursor_or_binding_reset(probe):
    probe.probe_modes = {'capture_pause_version': 1, 'native_capture_pause_version': 1}
    probe._committed_sequence, probe._acknowledged_sequence = 7, 5
    lifetime = probe._cursor_lifetime
    calls = []

    def exchange(operation, **arguments):
        calls.append((operation, arguments))
        response = reply(**arguments)
        response['capture_generation'] = arguments['revision']
        return response

    probe._request = exchange
    assert probe.set_capture_paused(True, 1)['paused'] is True
    assert probe.capture_paused
    assert probe.set_capture_paused(False, 2)['paused'] is False
    assert not probe.capture_paused
    assert probe._cursor_lifetime is lifetime
    assert (probe._committed_sequence, probe._acknowledged_sequence) == (7, 5)
    assert calls == [('capture_control', {'paused': True, 'revision': 1}),
                     ('capture_control', {'paused': False, 'revision': 2})]


@pytest.mark.parametrize('fields', [
    {'paused': False}, {'revision': 2}, {'revision': True}, {'active': 1},
    {'active': False}, {'active_hooks': ['OnTakeDamage']}, {'capture_pause_version': True},
    {'capture_generation': True}, {'capture_generation': 0}, {'retry': None}, {'error': 'failed'},
    {'native_paused': False}, {'native_quiet': False}, {'stopped': True},
])
def test_malformed_or_unconfirmed_control_never_claims_paused(probe, fields):
    probe.probe_modes = {'capture_pause_version': 1, 'native_capture_pause_version': 1}
    response = reply()
    response.update(fields)
    probe._request = lambda *args, **kwargs: response
    with pytest.raises(RuntimeError):
        probe.set_capture_paused(True, 1)
    assert not probe.capture_paused


def test_busy_and_lost_reply_are_retried_with_same_revision(probe):
    probe.probe_modes = {'capture_pause_version': 1, 'native_capture_pause_version': 1}
    response = reply()
    response.update(paused=False, retry=True, active=2)
    probe._request = lambda *args, **kwargs: response
    with pytest.raises(ObserverReadRetry):
        probe.set_capture_paused(True, 1)
    assert not probe.capture_paused
    response.update(paused=True, retry=False, active=0)
    assert probe.set_capture_paused(True, 1)['paused']


@pytest.mark.parametrize('version', [None, True, '1', 2])
def test_old_provider_cannot_be_shown_as_supporting_pause(probe, version):
    probe.probe_modes = ready_probe_modes({'capture_pause_version': version})
    probe._request = lambda *args, **kwargs: pytest.fail('unsupported producer called')
    assert not probe.capture_pause_supported
    with pytest.raises(RuntimeError):
        probe.set_capture_paused(True, 1)


def test_actual_production_hooks_pause_resume_and_retain_unacknowledged_queue():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/event_capture_pause_fixture.js')],
                            cwd=root, capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout == 'PASS_OWNED_CAPTURE_PAUSE'


def test_new_js_on_old_native_engine_does_not_advertise_pause():
    probe = NativeHookEventProbe(SimpleNamespace())
    probe.probe_modes = {'capture_pause_version': 1}
    assert not probe.capture_pause_supported


@pytest.mark.parametrize('observer_type', [FridaEventObserver, NativePcFridaObserver])
def test_control_timeout_and_late_frida_reply_do_not_confirm_pause(observer_type):
    probe = observer_type(SimpleNamespace(), **({'remote': 'unused'} if observer_type is FridaEventObserver else {}))
    probe.status = ObserverStatus.READY
    probe.probe_modes = {'capture_pause_version': 1}
    probe.request_timeout = 0
    requests = []
    probe.script = SimpleNamespace(post=lambda request: requests.append(request), is_destroyed=False)
    lifetime = probe._cursor_lifetime
    with pytest.raises(ObserverReadRetry, match='timed out'):
        probe.set_capture_paused(True, 1)
    late = {'type': 'send', 'payload': dict(kind='arkdps-event-response', id=requests[0]['id'], result=reply())}
    probe._message(late, None)
    assert not probe.capture_paused and probe._cursor_lifetime is lifetime

    def post(request):
        requests.append(request)
        probe._message(late, None)  # Old request ID cannot satisfy this retry.
        probe._message({'type': 'send', 'payload': dict(kind='arkdps-event-response',
            id=request['id'], result=reply())}, None)

    probe.script.post = post
    assert probe.set_capture_paused(True, 1)['paused']
    assert requests[0]['revision'] == requests[1]['revision'] == 1
    assert requests[0]['id'] != requests[1]['id']


def test_native_control_transport_failure_keeps_cleanup_owner_without_claiming_pause():
    probe = NativeHookEventProbe(SimpleNamespace(process=SimpleNamespace(process_alive=lambda: True)))
    probe.status = ObserverStatus.READY
    probe.probe_modes = {'capture_pause_version': 1, 'native_capture_pause_version': 1}
    probe.hook_disable_confirmed = False
    owner = object()
    probe._loader = owner

    def timeout(*args, **kwargs):
        raise TimeoutError('control response lost')

    pipe = probe._pipe = SimpleNamespace(request=timeout)
    lifetime = probe._cursor_lifetime
    with pytest.raises(TimeoutError):
        probe.set_capture_paused(True, 1)
    assert probe.status == ObserverStatus.FAILED and not probe.capture_paused
    assert probe._loader is owner and probe._pipe is pipe
    assert not probe.hook_disable_confirmed and probe._cursor_lifetime is lifetime
