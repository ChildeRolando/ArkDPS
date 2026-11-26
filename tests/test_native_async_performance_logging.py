from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from metrics import sanitize_native_async_capture
from desktop_audit import _performance_fields


def raw_async():
    return dict(version=1, reserved_ticket=20, processed_ticket=18, closed_ticket=18, published_ticket=16,
        desired_mode_epoch=1, published_mode_epoch=1, publication_ready=True, activity_generation=40,
        active=0, failure_code=0, captured_prefix_lag_records=2, publication_lag_records=2,
        published_event_sequence=10, delivered_event_sequence=8, read_prefix_lag_events=2,
        queue_age_available=True, queue_age_qpc_frequency=10_000_000,
        lane_metrics=[dict(lane=0, captured_records=20, consumed_records=18, captured_bytes=2048, consumed_bytes=1024,
            counter_snapshot_consistent=True, resident_records_approx=2, resident_bytes_approx=1024,
            thread_id=200, thread_birth='133700000000000001', lane_generation=3,
            peak_resident_records=8, peak_resident_bytes=4096, queue_age_available=True,
            age_sample_count=5, last_capture_to_replay_qpc=100, maximum_capture_to_replay_qpc=300,
            observed_pending_age_lower_bound_qpc=None)],
        callback_timing=dict(enabled=True, selection='xorshift_approximately_1_in_128',
            scope='after_sampling_decision_through_capture_enqueue', includes_first_touch=False,
            includes_original_function=False, includes_identity_lookup=True, includes_metrics_recording=False,
            sample_sums_are_not_extrapolated=True, qpc_frequency=10_000_000,
            samples=[dict(lane=0, lane_generation=3, hook_id=2, hook='hp.Entity.SetHpInternal', edge='leave',
                sample_count=5, sample_sum_qpc=500, sample_max_qpc=200, sample_native_reads=70,
                sample_native_read_bytes=1000, sample_enqueued_bytes=3000, sample_drops=0)]))


def native():
    return dict(schema=1, pid=123, process_created_filetime=99, qpc_frequency=10_000_000, rows=[])


def clean(value=None):
    return sanitize_native_async_capture(raw_async() if value is None else value,
        native=native(), identity=(123, 99))


def test_async_summary_is_strict_bounded_and_identity_bound():
    raw = raw_async()
    raw['events'] = ['secret']; raw['lane_metrics'][0]['source'] = 'secret'
    result = clean(raw)
    assert result['available'] and result['pid'] == 123 and result['process_created_filetime'] == 99
    lane = result['lane_metrics'][0]
    assert lane['thread_birth'] == '133700000000000001' and lane['lane_generation'] == 3
    assert lane['observed_pending_age_lower_bound_qpc'] is None
    assert lane['captured_bytes'] == 2048 and lane['consumed_bytes'] == 1024
    assert result['callback_timing']['samples'][0]['thread_birth'] == lane['thread_birth']
    assert 'secret' not in str(result)
    assert sanitize_native_async_capture(raw, native=native(), identity=(124, 99))['reason'] == 'identity_mismatch'
    assert sanitize_native_async_capture(raw)['reason'] == 'identity_unavailable'
    assert clean({})['available'] is False


@pytest.mark.parametrize('change', [
    lambda x: x.update(version=True), lambda x: x.update(active=float('nan')),
    lambda x: x.update(reserved_ticket=2**64), lambda x: x.update(failure_code=10),
    lambda x: x.update(captured_prefix_lag_records=0),
    lambda x: x.update(lane_metrics=x['lane_metrics']*17),
    lambda x: x.update(lane_metrics=x['lane_metrics']*2),
])
def test_invalid_async_top_is_unavailable_without_zero_fill(change):
    raw = raw_async(); change(raw)
    result = clean(raw)
    assert result['available'] is False and 'reserved_ticket' not in result


@pytest.mark.parametrize('change', [
    lambda r: r.update(thread_birth='9'*10000), lambda r: r.update(lane=True),
    lambda r: r.update(captured_records=-1), lambda r: r.update(captured_bytes=0),
    lambda r: r.update(consumed_bytes=True), lambda r: r.update(resident_records_approx=0),
])
def test_invalid_lane_is_explicit_unavailable_and_cannot_bind_sample(change):
    raw = raw_async(); change(raw['lane_metrics'][0]); result = clean(raw)
    assert result['lane_metrics'] == [dict(available=False, reason='invalid_lane_row')]
    assert result['callback_timing']['samples'] == [dict(available=False, reason='invalid_sample_row')]


@pytest.mark.parametrize('change', [
    lambda r: r.update(hook='private text'), lambda r: r.update(edge='other'),
    lambda r: r.update(sample_native_read_bytes=float('inf')),
    lambda r: r.update(lane_generation=2), lambda r: r.update(sample_max_qpc=1000),
])
def test_invalid_callback_sample_is_unavailable(change):
    raw = raw_async(); change(raw['callback_timing']['samples'][0])
    assert clean(raw)['callback_timing']['samples'] == [dict(available=False, reason='invalid_sample_row')]


def test_unknown_residency_and_unsampled_age_remain_null():
    raw = raw_async(); lane = raw['lane_metrics'][0]
    lane.update(captured_records=17, counter_snapshot_consistent=False, resident_records_approx=None,
                resident_bytes_approx=None, queue_age_available=False, age_sample_count=0,
                last_capture_to_replay_qpc=None, maximum_capture_to_replay_qpc=None)
    result = clean(raw)['lane_metrics'][0]
    assert result['available'] and result['resident_records_approx'] is None
    assert result['last_capture_to_replay_qpc'] is None


def probe():
    from event_observer import ObserverStatus
    from native_pc.native_hook_source import NativeHookEventProbe
    p = NativeHookEventProbe(SimpleNamespace(process=SimpleNamespace(process_alive=lambda: True)))
    p.status = ObserverStatus.READY; p._owned_process_instance = (123, 99)
    return p


def publication():
    return dict(version=1, token='2:1', closed_ticket='2', activity_begin='5', activity_end='6', sequence=7, epoch=1)


def test_only_one_second_reports_and_stats_face_has_no_async_but_validates_token(monkeypatch):
    from native_pc import native_hook_source as module
    p = probe(); records = []; ticks = iter([0., .2, 1.1])
    monkeypatch.setattr(module.time, 'perf_counter', lambda: next(ticks))
    p.set_performance_diagnostics(lambda *row: records.append(row), enabled=lambda: True)
    response = dict(sequence=7, epoch=1, native_publication=publication(), native_async_capture=raw_async(),
                    native_timing=native(), native_timing_control=dict(version=1, enabled=True))
    p._pipe = SimpleNamespace(request=lambda *_a, **_k: deepcopy(response))
    seen = []
    p.binding = SimpleNamespace(bind=lambda op, raw: (seen.append('native_async_capture' in raw) or dict(raw)))
    for _ in range(3):
        result = p.mark()
        assert 'native_async_capture' not in result and 'native_timing' not in result
    assert seen == [True]*3 and len(records) == 2
    assert records[0][1]['async_capture']['available']
    p._performance_enabled = lambda: False
    monkeypatch.setattr(module.time, 'perf_counter', lambda: pytest.fail('disabled diagnostics read clock'))
    response.pop('native_publication')
    with pytest.raises(RuntimeError, match='异步采集缺少完整'):
        p.mark()  # Presence of async protocol MUST still require a token before stripping.


def test_async_reaches_normal_journal_with_debug_off_and_does_not_override_native(tmp_path):
    from desktop_audit import DesktopCaptureJournal
    from capture_cli import json_value
    data = dict(schema=1, source='native_runtime', available=True, native=native(), async_capture=clean())
    filtered = _performance_fields('native_performance_timing', data)
    assert filtered['async_capture'] == clean()
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
        header=lambda *a, **k: dict(kind='log_header', data=dict(debug_mode=False)), debug_mode=lambda: False) as journal:
        journal.write('native_performance_timing', data)
    rows = [json.loads(s) for s in next(tmp_path.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()]
    assert rows[-1]['data']['async_capture']['callback_timing']['samples'][0]['sample_count'] == 5
    unavailable = _performance_fields('native_performance_timing', dict(data, available=False, native=None))
    assert unavailable['available'] is False and unavailable['async_capture']['available'] is True


def test_terminal_raw_diagnostics_never_enter_terminal_bind_or_saved_raw(monkeypatch):
    from native_pc import native_hook_source as module
    p = probe(); p._raw_request = lambda *a, **k: dict(ok=True, native_async_capture=raw_async())
    p.binding = SimpleNamespace(bind_terminal_mark=lambda raw: dict(raw))
    assert p.read_terminal_mark() == dict(ok=True) and p.last_terminal_response == dict(ok=True)
    def acquire(observer, request, after, **kwargs):
        for op in ('mark', 'terminal_read', 'mark'):
            assert request(op) == dict(ok=True)
        return 'complete'
    monkeypatch.setattr(module, 'acquire_native_terminal_tail', acquire)
    assert p.read_terminal_tail(0) == 'complete'
