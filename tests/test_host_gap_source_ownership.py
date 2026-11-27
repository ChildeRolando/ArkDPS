"""Owned transport only; never attaches to a process."""
from threading import Lock
from types import SimpleNamespace
import json
import pytest
from collection_session import CollectionSession
from event_observer import ObserverStatus
from event_probe import StreamGap
from native_pc.native_hook_source import NativeHookEventProbe, NativePipe


def source():
    process = SimpleNamespace(pid=42, process_instance=(42, 100), process_alive=lambda: True)
    collector = SimpleNamespace(process=process, diagnostics=SimpleNamespace(platform='native_pc'))
    probe = NativeHookEventProbe(collector)
    pipe = NativePipe.__new__(NativePipe)
    pipe.handle, pipe._poisoned = 1, False
    probe._pipe, probe.status, probe._owned_process_instance = pipe, ObserverStatus.READY, (42, 100)
    session = CollectionSession(None, None, collector=collector)
    session.observer = probe
    return session, probe, pipe


def test_host_gap_retains_exact_source_without_ack_or_lifecycle_conversion(monkeypatch):
    session, probe, pipe = source()
    replies = iter([{'bad': True}, {'scope': 'legal'}])
    monkeypatch.setattr(probe, '_raw_request', lambda *a, **k: next(replies))
    def bind(op, raw):
        if 'bad' in raw:
            raise StreamGap('host rejection')
        return raw
    monkeypatch.setattr(probe.binding, 'bind', bind)
    with pytest.raises(StreamGap) as gap:
        probe.mark()
    assert session.preserve_host_stream_gap(gap.value)
    assert session.observer is probe and probe._pipe is pipe
    assert probe.mark() == {'scope': 'legal'}
    assert probe.status is ObserverStatus.READY and not pipe._poisoned


@pytest.mark.parametrize('fault', ['failed', 'poison', 'closed', 'birth', 'pid', 'dead', 'unknown_alive', 'other_collector', 'unknown_pipe', 'not_gap'])
def test_missing_health_evidence_is_not_preserved(fault):
    session, probe, pipe = source()
    error = StreamGap('rejected')
    if fault == 'failed': probe.status = ObserverStatus.FAILED
    elif fault == 'poison': pipe._poisoned = True
    elif fault == 'closed': pipe.handle = None
    elif fault == 'birth': session.collector.process.process_instance = (42, 101)
    elif fault == 'pid': session.collector.process.pid = 43
    elif fault == 'dead': session.collector.process.process_alive = lambda: False
    elif fault == 'unknown_alive': session.collector.process.process_alive = lambda: None
    elif fault == 'other_collector': probe.collector = SimpleNamespace()
    elif fault == 'unknown_pipe': probe._pipe = SimpleNamespace(handle=1, _poisoned=False)
    elif fault == 'not_gap': error = RuntimeError('not gap')
    assert not session.preserve_host_stream_gap(error)


@pytest.mark.parametrize('code', [4, 6])
def test_actual_native_error_envelope_poison_is_never_preserved(code):
    session, probe, pipe = source()
    pipe._lock, pipe._id = Lock(), 0
    pipe._buffer = bytearray((json.dumps({'id': 1, 'error': 'native_replay_gap code=' + str(code)})+'\n').encode())
    pipe._io = lambda operation, buffer, size, deadline: size
    with pytest.raises(RuntimeError):
        probe.mark()
    assert pipe._poisoned and probe.status is ObserverStatus.FAILED
    assert not session.preserve_host_stream_gap(StreamGap('host wrapper'))

def test_collection_poll_gap_stays_strict_same_generation_and_same_source(monkeypatch):
    session, probe, pipe = source()
    session._observer_generation = 7
    calls = []
    monkeypatch.setattr(probe, '_raw_request', lambda operation, **kw: calls.append(operation) or {})
    monkeypatch.setattr(probe.binding, 'bind', lambda *_: (_ for _ in ()).throw(StreamGap('rejected binding')))
    session.runtime = SimpleNamespace(poll=lambda *_a, **_k: probe.mark(),
                                      sessions=SimpleNamespace(pop_archives=lambda: []))
    with pytest.raises(StreamGap) as error:
        session._read_lifecycle()
    assert calls == ['mark']  # No read/ACK is manufactured after rejection.
    assert session.preserve_host_stream_gap(error.value)
    assert session._observer_generation == 7 and session.observer is probe and probe._pipe is pipe
    changed = SimpleNamespace(generation=7, events={})
    assert session._prepare_battle_observer(changed) == (False, probe)
