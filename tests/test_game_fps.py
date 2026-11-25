"""DXGI frame evidence, clock windows and monitor ownership regressions."""

import ctypes as C
import queue
import threading
from types import SimpleNamespace

import pytest

import game_fps as fps


def _frame(windows, ticks, chain=5, *, tid=1, flags=0, result=0):
    windows.start(tid, ticks - 1, chain, flags)
    windows.stop(tid, ticks, result)


def _native(pid=42, birth=111, root='C:/Games/Arknights'):
    return (dict(platform='native_pc', native_game_root=root),
            dict(platform='native_pc', status='ready_unique', connection_ok=True, process_pid=pid,
                 process_created_filetime=birth, native_game_root=root))


def test_exact_windows_count_only_successful_present_and_zero_when_stalled():
    windows = fps._PresentWindows(0, 100)
    _frame(windows, 10)
    windows.stop(99, 23, 0)
    _frame(windows, 101)
    _frame(windows, 110)
    _frame(windows, 120, flags=1)
    _frame(windows, 121, result=0x887A000A)
    _frame(windows, 122, chain=0)
    assert windows.collect(224) == ('warming', None, None, None)
    assert windows.collect(225) == ('warming', None, 0, None)
    assert windows.collect(325) == ('ok', 2, 1, None)
    assert windows.collect(425) == ('ok', 0, 2, None)


def test_zero_windows_keep_dominant_chain_but_sustained_replacement_recovers():
    windows = fps._PresentWindows(0, 100)
    _frame(windows, 10)
    _frame(windows, 110)
    assert windows.collect(225)[0] == 'warming'
    assert windows.collect(325)[:2] == ('ok', 1)
    for second in (2, 3):
        _frame(windows, second * 100 + 20, chain=6)
        assert windows.collect(second * 100 + 225)[:2] == ('ok', 0)
    for second in (4, 5):
        for ticks in range(second * 100 + 10, second * 100 + 20):
            _frame(windows, ticks, chain=7)
        expected = ('warming', None) if second == 4 else ('ok', 10)
        assert windows.collect(second * 100 + 225)[:2] == expected
    assert windows.collect(825)[:2] == ('ok', 0)


def test_loss_and_late_events_produce_unavailable_and_reject_contaminated_windows():
    windows = fps._PresentWindows(0, 100)
    _frame(windows, 10)
    _frame(windows, 110)
    assert windows.collect(225)[0] == 'warming'
    assert windows.collect(325)[:2] == ('ok', 1)
    _frame(windows, 122)
    assert windows.collect(425) == ('lost', None, 1, 'dxgi_etw_late_delivery')
    _frame(windows, 450)
    assert windows.collect(525) == ('warming', None, None, None)
    _frame(windows, 510)
    assert windows.collect(725) == ('ok', 1, 5, None)
    assert windows.collect(825, loss=True) == ('lost', None, None, 'dxgi_etw_lost')
    assert windows.collect(925) == ('warming', None, None, None)


def test_successful_orphan_stop_after_first_window_invalidates_its_bucket():
    windows = fps._PresentWindows(0, 100)
    _frame(windows, 10)
    _frame(windows, 110)
    assert windows.collect(225)[0] == 'warming'
    assert windows.collect(325)[:2] == ('ok', 1)
    windows.stop(1, 220, 0)
    assert windows.collect(425) == ('lost', None, 2, 'dxgi_etw_present_pair')


def _event(windows, event_id, data, *, pid=42, ticks=0, flags=0):
    record = fps._EventRecord()
    record.header.provider = fps._GUID.from_buffer_copy(fps._DXGI)
    record.header.pid, record.header.tid = pid, 55
    record.header.descriptor.id = event_id
    record.header.timestamp, record.header.flags = ticks, flags
    buffer = C.create_string_buffer(data)
    record.data_size = len(data)
    record.data = C.addressof(buffer)
    fps._consume_present(record, 42, windows)


@pytest.mark.parametrize('pointer_size,flags', [(4, 0x20), (8, 0)])
def test_etw_payload_filter_decodes_target_pid_and_rejects_truncated_records(pointer_size, flags):
    windows = fps._PresentWindows(0, 100)
    start = (0x3490).to_bytes(pointer_size, 'little') + bytes(4)
    for ticks in (10, 110):
        _event(windows, 42, start, ticks=ticks, flags=flags)
        _event(windows, 43, bytes(4), ticks=ticks + 1)
        _event(windows, 42, start, pid=41, ticks=ticks + 2, flags=flags)
        _event(windows, 43, bytes(4), pid=41, ticks=ticks + 3)
    assert windows.collect(225)[0] == 'warming'
    assert windows.collect(325) == ('ok', 1, 1, None)
    _event(windows, 42, b'bad', ticks=215, flags=flags)
    assert windows.collect(425) == ('lost', None, 2, 'dxgi_etw_present_format')


def test_resolution_needs_explicit_current_process_instance_or_android_endpoint():
    connection, diagnostic = _native()
    assert fps._resolve_target(connection, diagnostic).birth == 111
    assert fps._resolve_target(connection, None).reason == 'awaiting_game_process'
    assert fps._resolve_target(connection, diagnostic | {'process_created_filetime': None}).pid is None
    assert fps._resolve_target(connection, diagnostic | {'status': 'error'}).pid is None
    assert fps._resolve_target(connection, diagnostic | {'native_game_root': 'C:/Other'}).pid is None
    assert fps._resolve_target(connection, diagnostic | {'native_game_root': r'c:\games\arknights'}).pid == 42
    assert fps._resolve_target(None, None).reason == 'awaiting_android_device'
    android = dict(connection_ok=True, adb_path='adb.exe', selected_serial='127.0.0.1:16384')
    assert fps._resolve_target({'platform': 'mumu'}, android).serial == android['selected_serial']
    assert fps._resolve_target({'platform': 'mumu', 'serial': 'other'}, android).serial is None


def test_failed_stop_keeps_owned_session_for_retry():
    trace = object.__new__(fps._DxgiTrace)
    trace.session = fps._U64(123)
    trace.consumer, trace.thread, trace.process = None, None, 99
    trace.name = 'owned_test'
    outcomes = iter((5, 0))
    closed = []
    trace.api = SimpleNamespace(ControlTraceW=lambda *_: next(outcomes), CloseHandle=closed.append)
    with pytest.raises(fps._FpsError, match='dxgi_etw_stop'):
        trace.close()
    assert trace.session.value == 123 and trace.process == 99
    trace.close()
    assert trace.session.value == 0 and trace.process is None and closed == [99]


def test_startup_cleanup_failure_carries_owner_until_stop(monkeypatch):
    outcomes = iter((5, 0))
    closed = []
    api = SimpleNamespace(open_instance=lambda *_: 99, ControlTraceW=lambda *_: next(outcomes),
                          CloseHandle=closed.append)
    monkeypatch.setattr(fps, '_WindowsEtw', lambda: api)

    def fail_start(trace):
        trace.session.value = 123
        raise fps._FpsError('dxgi_etw_open', 6)

    monkeypatch.setattr(fps._DxgiTrace, '_start', fail_start)
    with pytest.raises(fps._FpsError, match='dxgi_etw_stop') as failure:
        fps._DxgiTrace(fps._Target(source='dxgi_present', pid=42, birth=111))
    retained = failure.value.cleanup_trace
    assert retained.session.value == 123 and closed == []
    retained.close()
    assert retained.session.value == 0 and closed == [99]


def test_monitor_owns_close_retry_when_connection_instance_changes():
    samples = queue.Queue()
    instances = []

    class Trace:
        def __init__(self, target):
            self.target, self.close_attempts = target, 0
            instances.append(self)

        def poll(self):
            return 'ok', self.target.pid, 100, 101, None

        def close(self):
            self.close_attempts += 1
            if self.target.pid == 42 and self.close_attempts == 1:
                raise fps._FpsError('dxgi_etw_stop', 5)

    monitor = fps.GameFpsMonitor(emit=samples.put, _trace_factory=Trace)
    try:
        monitor.configure(*_native())
        first = samples.get(timeout=3)
        assert (first['pid'], first['fps'], first['duration_ms']) == (42, 42, 1000)
        first['fps'] = -1
        assert monitor.snapshot()['fps'] == 42
        monitor.configure(*_native(77, 222))
        assert monitor.snapshot()['fps'] is None
        error = samples.get(timeout=3)
        assert (error['pid'], error['status'], error['error_code'], error['winerror']) == (
            77, 'error', 'dxgi_etw_stop', 5)
        recovered = samples.get(timeout=3)
        assert (recovered['pid'], recovered['fps']) == (77, 77)
        assert first['sample_seq'] < error['sample_seq'] < recovered['sample_seq']
    finally:
        monitor.close()
    assert [trace.close_attempts for trace in instances] == [2, 1]


def test_android_window_has_guest_clock_and_no_invented_host_anchor():
    emitted = threading.Event()

    class Android:
        def __init__(self, adb, serial):
            assert (adb, serial) == ('adb.exe', 'serial')

        def poll(self):
            return SimpleNamespace(fps=0, frames=0, bucket_end_ns=5_000_000_000,
                                   window_seconds=1.0, reason='', pid=77)

    monitor = fps.GameFpsMonitor(emit=lambda _: emitted.set(), _android_factory=Android)
    try:
        monitor.configure({'platform': 'mumu'},
                          {'connection_ok': True, 'adb_path': 'adb.exe', 'selected_serial': 'serial'})
        assert emitted.wait(timeout=3)
        result = monitor.snapshot()
        assert result['source'] == 'surfaceflinger'
        assert result['clock_source'] == 'android_monotonic'
        assert result['fps'] == 0 and result['duration_ms'] == 1000 and result['guest_pid'] == 77
        assert (result['bucket_start_ns'], result['bucket_end_ns']) == (4_000_000_000, 5_000_000_000)
        assert result['window_start_utc'] is None and result['window_end_utc'] is None
        assert result['observed_at_utc'].endswith('+00:00')
    finally:
        monitor.close()
