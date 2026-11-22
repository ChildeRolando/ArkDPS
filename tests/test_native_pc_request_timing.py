"""Owned request timing is optional evidence, never a new transport owner."""
import inspect
from types import SimpleNamespace

import pytest

from collection_session import CollectionSession
from event_observer import FridaEventObserver, NativePcFridaObserver
from event_probe import ObserverReadRetry
from native_pc import frida_runtime as native


def observer():
    return native._GadgetObserver(SimpleNamespace(), None, runtime=SimpleNamespace())


def test_default_sink_is_none_and_uses_no_clock_or_extra_call(monkeypatch):
    current = observer()
    result = object()
    calls = []

    def request(self, operation, *, timeout=None, **arguments):
        calls.append((self, operation, timeout, arguments))
        return result

    def forbidden():
        raise AssertionError('disabled timing must not read a clock')

    monkeypatch.setattr(native, 'time', SimpleNamespace(monotonic=forbidden, perf_counter=forbidden))
    monkeypatch.setattr(NativePcFridaObserver, '_request', request)
    token = object()
    assert current._request('read', timeout=.125, token=token) is result
    assert calls == [(current, 'read', .125, dict(token=token))]
    assert inspect.signature(native._GadgetObserver._request) == inspect.signature(request)


@pytest.mark.parametrize('operation,timeout', [('mark', None), ('mark', .5), ('read', .125), ('ack', 0), ('private operation', None)])
def test_timing_preserves_inputs_return_identity_and_records_only_bounded_labels(monkeypatch, operation, timeout):
    current = observer()
    result, token = object(), object()
    calls, records = [], []
    clock = [10.0]

    def request(self, received, *, timeout=None, **arguments):
        calls.append((self, received, timeout, arguments))
        clock[0] += .125
        return result

    def sink(kind, value):
        records.append((kind, value))
        clock[0] += 900  # Sink work is excluded from the reported duration.

    monkeypatch.setattr(native, 'time', SimpleNamespace(monotonic=lambda: 10., perf_counter=lambda: clock[0]))
    monkeypatch.setattr(NativePcFridaObserver, '_request', request)
    current.set_request_timing_audit(sink)
    assert current._request(operation, timeout=timeout, token=token, after=17, through=22) is result
    assert calls == [(current, operation, timeout, dict(token=token, after=17, through=22))]
    assert records == [('native_pc_request_timing', dict(
        op=operation if operation in ('mark', 'read', 'ack') else 'other',
        started_monotonic=10.0, started_perf_counter=10.0, elapsed_ms=125.0, outcome='success', error_type=None))]


@pytest.mark.parametrize('failure', [ObserverReadRetry('private timeout details'), RuntimeError('private binding details'), KeyboardInterrupt('private cancel details')])
def test_request_exception_instance_and_type_survive_without_logging_its_message(monkeypatch, failure):
    current = observer()
    ticks = iter((20., 20.25))
    records = []
    calls = []

    def request(self, operation, *, timeout=None, **arguments):
        calls.append(operation)
        raise failure

    monkeypatch.setattr(native, 'time', SimpleNamespace(monotonic=lambda: 20., perf_counter=lambda: next(ticks)))
    monkeypatch.setattr(NativePcFridaObserver, '_request', request)
    current.set_request_timing_audit(lambda kind, value: records.append((kind, value)))
    with pytest.raises(type(failure)) as caught:
        current._request('read')
    assert caught.value is failure and calls == ['read']
    assert records == [('native_pc_request_timing', dict(op='read', started_monotonic=20., started_perf_counter=20.,
        elapsed_ms=250., outcome='error', error_type=type(failure).__name__))]


@pytest.mark.parametrize('sink_failure', [ValueError('audit unavailable'), KeyboardInterrupt('audit interrupted')])
@pytest.mark.parametrize('request_fails', [False, True])
def test_sink_failure_cannot_replace_success_or_the_original_request_exception(monkeypatch, sink_failure, request_fails):
    current = observer()
    result, failure = object(), ObserverReadRetry('original request')
    calls = []

    def request(self, operation, *, timeout=None, **arguments):
        calls.append(operation)
        if request_fails:
            raise failure
        return result

    def sink(*_):
        raise sink_failure

    monkeypatch.setattr(NativePcFridaObserver, '_request', request)
    current.set_request_timing_audit(sink)
    if request_fails:
        with pytest.raises(ObserverReadRetry) as caught:
            current._request('mark')
        assert caught.value is failure
    else:
        assert current._request('mark') is result
    assert calls == ['mark']


def test_real_native_binding_failure_is_inside_the_request_measurement(monkeypatch):
    current = observer()
    failure = ObserverReadRetry('binding remains unknown')
    reply = dict(private_payload='never logged')
    clock = [30.]
    records, calls = [], []

    def transport(self, operation, *, timeout=None, **arguments):
        calls.append(('transport', operation, timeout, arguments))
        clock[0] += .1
        return reply

    def bind(operation, value):
        calls.append(('bind', operation, value))
        clock[0] += .2
        raise failure

    monkeypatch.setattr(FridaEventObserver, '_request', transport)
    monkeypatch.setattr(native, 'time', SimpleNamespace(monotonic=lambda: 30., perf_counter=lambda: clock[0]))
    current.binding = SimpleNamespace(bind=bind)
    current.set_request_timing_audit(lambda kind, value: records.append(value))
    with pytest.raises(ObserverReadRetry) as caught:
        current._request('mark', timeout=.5)
    assert caught.value is failure
    assert calls == [('transport', 'mark', .5, {}), ('bind', 'mark', reply)]
    record, = records
    assert record['elapsed_ms'] == pytest.approx(300) and record['error_type'] == 'ObserverReadRetry'
    assert set(record) == {'op', 'started_monotonic', 'started_perf_counter', 'elapsed_ms', 'outcome', 'error_type'}


def test_ack_timeout_keeps_original_cursor_and_idempotent_request_count(monkeypatch):
    current = observer()
    records, calls = [], []

    def request(self, operation, *, timeout=None, **arguments):
        calls.append((operation, arguments))
        if len(calls) == 1:
            raise ObserverReadRetry('lost acknowledgement')
        return dict(acknowledged=arguments['sequence'], error=None)

    monkeypatch.setattr(NativePcFridaObserver, '_request', request)
    current.set_request_timing_audit(lambda kind, value: records.append(value))
    assert current.commit_events(4) is False
    assert current._committed_sequence == 4 and current._acknowledged_sequence == 0
    assert current.commit_events(4) is True
    assert current.commit_events(4) is True
    assert current._committed_sequence == current._acknowledged_sequence == 4
    assert calls == [('ack', dict(sequence=4)), ('ack', dict(sequence=4))]
    assert [row['outcome'] for row in records] == ['error', 'success']
    assert [(row['ack_sequence'], row['acknowledgement']) for row in records] == [(4, 'unknown'), (4, 'confirmed')]


def test_invalid_ack_reply_is_not_confirmed_even_when_rpc_returns(monkeypatch):
    current = observer()
    records, calls = [], []

    def request(self, operation, *, timeout=None, **arguments):
        calls.append((operation, arguments))
        return dict(acknowledged=True, private='never logged')

    monkeypatch.setattr(NativePcFridaObserver, '_request', request)
    current.set_request_timing_audit(lambda kind, value: records.append(value))
    assert current.commit_events(1) is False
    assert current.commit_events(1) is False
    assert current._committed_sequence == 1 and current._acknowledged_sequence == 0
    assert calls == [('ack', {'sequence': 1})]
    [record] = records
    assert record['outcome'] == 'success' and record['acknowledgement'] == 'rejected'
    assert 'private' not in record


@pytest.mark.parametrize('platform,has_interface', [('native_pc', True), ('native_pc', False), ('mumu', True)])
def test_session_binds_only_native_supported_sink_before_start(platform, has_interface):
    order, records = [], []
    audit = lambda kind, value: records.append((kind, value))
    collector = SimpleNamespace(diagnostics=SimpleNamespace(platform=platform))
    current = SimpleNamespace(probe_modes={})

    def set_sink(value):
        assert value is audit
        order.append('sink')

    if has_interface:
        current.set_request_timing_audit = set_sink
    current.start = lambda: order.append('start')
    current.stop = lambda: order.append('stop')
    session = CollectionSession(None, None, collector=collector, audit=audit,
                                observer_factory=lambda *_: current)
    assert session._attach_observer()
    assert order == (['sink', 'start'] if platform == 'native_pc' and has_interface else ['start'])
    assert session.observer is current and session.event_status == 'ready'
    assert records == [('observer_ready', dict(remote=session.frida_remote))]


def test_sink_can_be_disabled_after_use_without_reading_the_clock(monkeypatch):
    current = observer()
    calls = []
    current.set_request_timing_audit(lambda *_: pytest.fail('disabled sink called'))
    current.set_request_timing_audit()
    monkeypatch.setattr(native, 'time', SimpleNamespace(monotonic=lambda: pytest.fail('disabled clock read'),
                                                     perf_counter=lambda: pytest.fail('disabled precise clock read')))
    monkeypatch.setattr(NativePcFridaObserver, '_request', lambda self, op, **kwargs: calls.append(op))
    current._request('ack')
    assert calls == ['ack']
