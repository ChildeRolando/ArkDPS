"""One cursor policy across real observer entry points, with no game attached."""
from types import SimpleNamespace

import pytest

from connection import VersionStatus
from event_observer import FridaEventObserver, NativePcFridaObserver
from event_probe import ObserverReadRetry, reset_probe_cursor
from native_pc.native_hook_source import NativeHookEventProbe


@pytest.fixture(params=(FridaEventObserver, NativePcFridaObserver, NativeHookEventProbe))
def probe(request):
    collector = SimpleNamespace(diagnostics=SimpleNamespace(
        connection_ok=True, version_status=VersionStatus.COMPATIBLE))
    if request.param is FridaEventObserver:
        return request.param(collector, 'unused')
    return request.param(collector)


def test_lost_ack_keeps_consumed_prefix_and_retries_without_rereading(probe):
    requests = []

    def exchange(operation, **arguments):
        requests.append((operation, arguments))
        if operation == 'ack':
            if len(requests) == 1:
                raise ObserverReadRetry('response lost after producer applied ACK')
            return {'acknowledged': arguments['sequence']}
        return {'events': []}

    probe._request = exchange
    assert probe.commit_events(5) is False
    assert probe.read_through(6) == {'events': []}
    assert probe.commit_events(5) is True
    assert probe.commit_events(4) is True
    assert requests == [
        ('ack', {'sequence': 5}), ('read', {'after': 5, 'through': 6}),
        ('ack', {'sequence': 5}),
    ]
    assert probe._committed_sequence == probe._acknowledged_sequence == 5


@pytest.mark.parametrize('reply', [
    {'acknowledged': 4}, {'acknowledged': True}, {'acknowledged': 5.0},
    {'acknowledged': 5, 'error': 'wrong source'}, {}, None,
])
def test_invalid_ack_prevents_further_reads_instead_of_releasing_unconfirmed_data(probe, reply):
    requests = []

    def exchange(operation, **arguments):
        requests.append(operation)
        return reply

    probe._request = exchange
    assert probe.commit_events(5) is False
    assert probe.commit_events(5) is False
    with pytest.raises(RuntimeError):
        probe.read_through(6)
    assert requests == ['ack']
    assert probe._committed_sequence == 5
    assert probe._acknowledged_sequence == 0


@pytest.mark.parametrize('failure', [None, RuntimeError('old source failed')])
def test_reply_from_retired_stream_cannot_commit_or_poison_restarted_observer(probe, failure):
    def retired_exchange(operation, **arguments):
        # Same Python observer, newly loaded producer: no old mark can transfer
        # a cumulative ACK to the new script's sequence space.
        reset_probe_cursor(probe)
        if failure is not None:
            raise failure
        return {'acknowledged': arguments['sequence']}

    probe._request = retired_exchange
    assert probe.commit_events(9) is False
    assert probe._committed_sequence == probe._acknowledged_sequence == 0
    assert probe._ack_error is None
    probe._request = lambda operation, **arguments: {'acknowledged': arguments['sequence']}
    assert probe.commit_events(2) is True
    assert probe._committed_sequence == probe._acknowledged_sequence == 2


@pytest.mark.parametrize('sequence', [True, -1, 2.5, '5', None])
def test_invalid_commit_input_never_changes_cursor_or_sends_request(probe, sequence):
    requests = []
    probe._request = lambda *args, **kwargs: requests.append((args, kwargs))
    with pytest.raises(ValueError, match='nonnegative integer'):
        probe.commit_events(sequence)
    assert not requests
    assert probe._committed_sequence == probe._acknowledged_sequence == 0
