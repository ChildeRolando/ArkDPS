from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from collector import SampleUnavailable
from event_observer import ObserverStatus
from event_probe import NATIVE_PC_LIVE_CONTRACT, ObserverReadRetry
from native_pc import live_event_source
from native_pc_collector import NativePcCollector


INSTANCE = (42, 132245567890001234)
BUILD = 'native-build-sha'
STAGE = 'Obt/Main/level_main_01-07'
ROOTS = dict(controller='0x10000', native='0x20000', logger='0x30000', stats='0x40000', level='0x50000')


def _header(**fields):
    return dict(dict(format=live_event_source.FEED_FORMAT, version=1, producer='Owned event producer',
                     producer_version=3, process_instance=INSTANCE, source_build=BUILD), **fields)


def _encode(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8') + b'\n'


def _append(path, value):
    with path.open('ab') as stream:
        stream.write(value if isinstance(value, bytes) else _encode(value))


def _mark(sequence=0, frame=0, **fields):
    return dict(dict(sequence=sequence, frame=frame, epoch=sequence, active=0, game_time=frame,
                     native_frame=frame, native_fixed_time=str(frame * (1 << 32)), native_identity=ROOTS,
                     native_stage_id=STAGE, native_process_pid=INSTANCE[0], native_generation=7), **fields)


class _Collector:
    observer_binding = NativePcCollector.observer_binding

    def __init__(self):
        self.process = SimpleNamespace(pid=INSTANCE[0], process_instance=INSTANCE,
                                       process_alive=lambda: True, battle_serial_for=lambda *_: 1)
        self.diagnostics = SimpleNamespace(version_actual=BUILD)


class _Host:
    def __init__(self, path):
        self.path = path
        self.mark_value = self.terminal = None
        self.events = []
        self.probe_modes = {'probe_source': {'provider': 'internal-test-host'}}
        self.status = ObserverStatus.STOPPED

    def start(self):
        self.status = ObserverStatus.READY

    def advance(self, mark, events, terminal_mark=None):
        self.mark_value, self.terminal = deepcopy(mark), deepcopy(terminal_mark)
        self.events.extend(deepcopy(events))
        return True

    def mark(self):
        return deepcopy(self.mark_value)

    def read_through(self, sequence):
        return dict(self.mark(), events=[item for item in self.events if item['sequence'] <= sequence])

    def read_baseline(self, sequence):
        return dict(self.mark(), events=[])

    def read_terminal_mark(self):
        return {'terminal_boundary': deepcopy(self.terminal)} if self.terminal else {}

    def commit_events(self, sequence):
        self.events = [item for item in self.events if item['sequence'] > sequence]
        return True

    def stop(self):
        self.status = ObserverStatus.STOPPED


@pytest.fixture
def setup_feed(monkeypatch, tmp_path):
    hosts = []
    probes = []

    def host_factory(path):
        host = _Host(path)
        hosts.append(host)
        return host

    monkeypatch.setattr(live_event_source, 'InternalEventProbe', host_factory)

    def open_feed(**header_fields):
        path = tmp_path / 'cooperative-data.jsonl'
        path.write_bytes(_encode(_header(**header_fields)))
        collector = _Collector()
        probe = live_event_source.LiveNativeEventProbe(collector, path, tmp_path / 'trusted-host.exe')
        probes.append(probe)
        return probe, collector, path, hosts

    yield open_feed
    for probe in probes:
        probe.stop()


def test_live_feed_waits_for_complete_frames_and_holds_the_read_watermark(setup_feed):
    probe, collector, path, hosts = setup_feed()
    probe.start()
    assert probe.contract is NATIVE_PC_LIVE_CONTRACT
    assert hosts[0].path.name == 'trusted-host.exe'
    assert probe.probe_modes['probe_source']['producer'] == 'Owned event producer'
    with pytest.raises(ObserverReadRetry):
        probe.mark()
    first = _encode(dict(mark=_mark(), events=[]))
    _append(path, first[:-5])
    with pytest.raises(ObserverReadRetry):
        probe.mark()
    _append(path, first[-5:])
    mark = probe.mark()
    assert mark == collector.observer_binding(frame=0, game_time=0, native_identity=ROOTS,
                                               native_stage_id=STAGE, native_process_pid=INSTANCE[0],
                                               native_fixed_time='0') | _mark()
    event = dict(sequence=1, kind='healing', amount=2.5, frame=1, game_time=1, detail='审计事件')
    _append(path, dict(mark=_mark(1, 1), events=[event]))
    assert probe.read_through(0) == dict(mark, events=[])
    assert probe.mark()['sequence'] == 1
    assert probe.read_baseline(1)['events'] == []
    assert probe.read_through(1)['events'] == [event]
    assert probe.commit_events(1)
    assert probe.read_through(1)['events'] == []


@pytest.mark.parametrize(('fields', 'error'), [
    ({'version': 2}, 'header'),
    ({'version': True}, 'header'),
    ({'process_instance': (42, INSTANCE[1] + 1)}, 'process'),
    ({'source_build': 'different'}, 'build'),
])
def test_live_feed_validates_header_before_starting_host(setup_feed, fields, error):
    probe, _, _, hosts = setup_feed(**fields)
    with pytest.raises(RuntimeError, match=error):
        probe.start()
    assert probe.status == ObserverStatus.FAILED
    assert not hosts


@pytest.mark.parametrize(('bad_mark', 'events', 'message'), [
    ({'native_generation': 8}, [], 'changed battle generation'),
    ({'source_build': 'other'}, [], 'claims a different battle or build'),
    ({'native_process_created_filetime': INSTANCE[1] + 1}, [], 'different process instance'),
    ({'native_frame': 5}, [], '逻辑时钟不一致'),
    ({}, [{'native_generation': 8}], 'different stream'),
])
def test_live_feed_fails_closed_when_producer_changes_identity(setup_feed, bad_mark, events, message):
    probe, _, path, hosts = setup_feed()
    _append(path, dict(mark=_mark(), events=[]))
    probe.start()
    assert probe.mark()['sequence'] == 0
    _append(path, dict(mark=_mark(1, 1, **bad_mark), events=events))
    with pytest.raises(RuntimeError, match=message):
        probe.mark()
    assert probe.status == ObserverStatus.FAILED
    assert hosts[0].status == ObserverStatus.STOPPED and hosts[0].mark_value['sequence'] == 0


def test_live_feed_waits_on_temporary_native_binding_without_dropping_record(setup_feed):
    probe, collector, path, _ = setup_feed()
    _append(path, dict(mark=_mark(), events=[]))
    original = collector.observer_binding

    def busy(**fields):
        raise SampleUnavailable('controlled frame was busy')

    collector.observer_binding = busy
    probe.start()
    with pytest.raises(ObserverReadRetry, match='controlled frame was busy'):
        probe.mark()
    collector.observer_binding = original
    assert probe.mark()['sequence'] == 0


def test_live_feed_heartbeat_can_repeat_watermark_but_stale_feed_fails(setup_feed, monkeypatch):
    probe, _, path, _ = setup_feed()
    elapsed = [0.]
    monkeypatch.setattr(live_event_source, 'monotonic', lambda: elapsed[0])
    update = dict(mark=_mark(), events=[])
    _append(path, update)
    probe.start()
    assert probe.mark()['sequence'] == 0
    elapsed[0] = 4.
    _append(path, update)
    assert probe.mark()['sequence'] == 0
    elapsed[0] = 8.
    assert probe.mark()['sequence'] == 0
    elapsed[0] = 9.
    with pytest.raises(RuntimeError, match='stopped advancing'):
        probe.mark()
    assert probe.status == ObserverStatus.FAILED


def test_live_feed_bounds_unverified_guarded_frames_even_with_heartbeats(setup_feed, monkeypatch):
    probe, _, path, _ = setup_feed()
    elapsed = [0.]
    monkeypatch.setattr(live_event_source, 'monotonic', lambda: elapsed[0])
    mismatch = ObserverReadRetry('native snapshot crossed a different frame')
    probe.start()
    elapsed[0] = 10.
    with pytest.raises(ObserverReadRetry):
        probe.mark()  # A header in the lobby need not have its first frame yet.
    assert probe.reject_unverified_frame(mismatch) is None
    elapsed[0] = 14.
    probe.accept_verified_frame()
    assert probe.reject_unverified_frame(mismatch) is None
    elapsed[0] = 18.
    _append(path, dict(mark=_mark(), events=[]))
    assert probe.mark()['sequence'] == 0
    elapsed[0] = 19.
    assert 'did not match the current native frame' in probe.reject_unverified_frame(mismatch)
    assert probe.status == ObserverStatus.FAILED


def test_live_feed_retains_a_terminal_proof_and_refuses_later_updates(setup_feed, monkeypatch):
    probe, _, path, _ = setup_feed()
    terminal = _mark()
    _append(path, dict(mark=terminal, events=[], terminal_mark=terminal))
    times = [0.]
    monkeypatch.setattr(live_event_source, 'monotonic', lambda: times[0])
    probe.start()
    assert probe.read_terminal_mark() == {'terminal_boundary': probe.mark()}
    times[0] = 30.
    assert probe.read_terminal_mark() == {'terminal_boundary': probe.mark()}
    _append(path, dict(mark=_mark(1, 1, native_generation=8), events=[]))
    with pytest.raises(RuntimeError, match='continued after a finished battle'):
        probe.mark()


def test_live_feed_rewind_is_a_gap(setup_feed):
    probe, _, path, _ = setup_feed()
    _append(path, dict(mark=_mark(), events=[]))
    probe.start()
    assert probe.mark()['sequence'] == 0
    path.write_bytes(_encode(_header()))
    with pytest.raises(RuntimeError, match='replaced or rewound'):
        probe.mark()


def test_live_feed_bounded_pending_line_and_poll(setup_feed):
    probe, _, path, hosts = setup_feed()
    probe.max_poll_updates = 1
    _append(path, dict(mark=_mark(), events=[]))
    _append(path, dict(mark=_mark(1, 1), events=[]))
    probe.start()
    with pytest.raises(ObserverReadRetry):
        probe.mark()
    assert hosts[0].mark_value['sequence'] == 0
    assert probe.mark()['sequence'] == 1
    probe.max_line_bytes = 100
    _append(path, b'x' * 101)
    with pytest.raises(RuntimeError, match='line limit'):
        probe.mark()
