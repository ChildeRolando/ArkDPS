from dataclasses import replace
from types import SimpleNamespace
from threading import Thread

import pytest

from desktop_audit import _performance_fields
from layout_store import Layout, LayoutStore
from metrics import TimingWindow, timed, timing_batch


def test_disabled_batch_never_reads_clock_or_emits():
    def forbidden(*_):
        raise AssertionError('disabled diagnostics performed work')
    owner = SimpleNamespace(_performance_timings=TimingWindow(lambda: False, forbidden,
                            source='collector', clock=forbidden))
    @timing_batch
    @timed('child')
    def operation(self):
        return 17
    assert operation(owner) == 17


def test_nested_wall_time_is_not_double_counted_and_context_is_restored():
    clock = [1]
    window = TimingWindow(lambda: True, lambda *_: None, source='collector', clock=lambda: clock[0])
    @timed('child')
    def child():
        clock[0] += 3
    @timed('parent')
    def parent():
        clock[0] += 5
        child()
        clock[0] += 2
    with window.batch():
        parent()
        thread = Thread(target=child)
        thread.start(); thread.join()
    assert window.rows['parent'] == [1, 10, 7, 10]
    assert window.rows['child'] == [1, 3, 3, 3]
    child()
    assert window.rows['child'][0] == 1


def test_failure_is_timed_and_failed_sink_cannot_break_collection():
    clock = [1]
    def reject(*_):
        raise OSError('disk unavailable')
    window = TimingWindow(lambda: True, reject, source='collector', clock=lambda: clock[0])
    with pytest.raises(ValueError), window.batch(), window.phase('failed'):
        clock[0] += 1_000_000_001
        raise ValueError('original failure')
    assert window.failures == 1 and not window.stack


def test_phase_capacity_and_one_second_emission_bound():
    clock, records = [1], []
    window = TimingWindow(lambda: True, lambda *item: records.append(item), source='collector', clock=lambda: clock[0])
    for _ in range(100):
        with window.batch():
            for i in range(100):
                window.add(str(i), 1)
    assert len(window.rows) == 33 and not records
    clock[0] += 1_000_000_000
    with window.batch():
        pass
    assert len(records) == 1
    assert sum(row['calls'] for row in records[0][1]['phases'].values()) == 10_000


def test_independent_setting_roundtrip_and_default_off(tmp_path):
    store = LayoutStore(tmp_path/'layout.json')
    assert not store.load().layout.performance_diagnostics
    layout = replace(Layout.default(), performance_diagnostics=True, debug_mode=False)
    store.save(layout)
    assert store.load().layout == layout


def test_normal_mode_native_summary_is_bounded_and_has_no_raw_events():
    source = dict(schema=1, source='native_runtime', available=True, native=dict(schema=1,
        qpc_frequency=10_000_000, rows=[dict(calls=3, samples=1, hook='x'*200,
        events=['secret'], read_ticks=20)]*200, events=['secret']))
    clean = _performance_fields('native_performance_timing', source)
    assert len(clean['native']['rows']) == 128
    assert len(clean['native']['rows'][0]['hook']) == 96
    assert 'secret' not in str(clean)
    assert _performance_fields('native_performance_timing', dict(schema=1, source='native_runtime', available=False))['available'] is False


def test_native_disable_requires_ack_and_old_runtime_is_unknown(monkeypatch):
    from event_observer import ObserverStatus
    from native_pc import native_hook_source as module
    probe = module.NativeHookEventProbe(SimpleNamespace(process=SimpleNamespace(process_alive=lambda: True)))
    calls, records = [], []
    enabled = [True]
    def request(operation, **arguments):
        calls.append(arguments)
        return {}  # Older runtime ignores the optional diagnostic capability.
    probe._pipe = SimpleNamespace(request=request)
    probe.status = ObserverStatus.READY
    probe.set_performance_diagnostics(lambda *row: records.append(row), enabled=lambda: enabled[0])
    probe._raw_request('mark')
    assert calls[-1]['native_timing'] == dict(version=1, enabled=True, snapshot=True)
    assert records[-1][1]['available'] is False and records[-1][1]['native'] is None
    enabled[0] = False
    probe._raw_request('mark')
    assert calls[-1]['native_timing']['enabled'] is False
    assert probe._native_timing_may_be_enabled  # A missing ACK is not an ACK.
    probe._pipe.request = lambda *_args, **kwargs: dict(native_timing_control=dict(version=1,enabled=False))
    probe._raw_request('mark')
    assert not probe._native_timing_may_be_enabled
    probe._pipe.request = request
    monkeypatch.setattr(module.time, 'perf_counter', lambda: pytest.fail('disabled timing read clock'))
    probe._raw_request('mark')
    assert 'native_timing' not in calls[-1]


def test_summary_is_saved_with_raw_debug_off(tmp_path):
    import json
    from desktop_audit import DesktopCaptureJournal
    from capture_cli import json_value
    with DesktopCaptureJournal(tmp_path,encoder=json_value,warn=pytest.fail,
        header=lambda *args,**kwargs: dict(kind='log_header',data=dict(debug_mode=False)),
        debug_mode=lambda: False) as journal:
        journal.write('performance_timing',dict(schema=1,source='collector',
            phases=dict(capture_acquire=dict(calls=1,inclusive_ns=100,exclusive_ns=90,max_ns=100)),
            events=['must not appear']))
    data=[json.loads(line) for line in next(tmp_path.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()]
    assert data[-1]['kind']=='performance_timing' and 'events' not in data[-1]['data']


def test_native_control_is_not_resent_per_mark_and_metadata_never_enters_binding():
    from event_observer import ObserverStatus
    from native_pc.native_hook_source import NativeHookEventProbe
    probe=NativeHookEventProbe(SimpleNamespace(process=SimpleNamespace(process_alive=lambda: True)))
    requests=[]
    def request(operation,**arguments):
        requests.append(arguments)
        return dict(sequence=7,native_timing_control=dict(version=1,enabled=True),native_timing=dict(schema=1))
    probe._pipe=SimpleNamespace(request=request)
    probe.status=ObserverStatus.READY
    probe.set_performance_diagnostics(lambda *_: None,enabled=lambda: True)
    assert probe._raw_request('mark')==dict(sequence=7)
    assert probe._raw_request('mark')==dict(sequence=7)
    assert 'native_timing' in requests[0] and 'native_timing' not in requests[1]
