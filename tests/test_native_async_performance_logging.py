from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from metrics import sanitize_native_async_capture
from desktop_audit import _performance_fields


def untimed_async():
    raw = raw_async()
    raw['queue_age_available'] = False
    raw['callback_timing'].update(enabled=False, samples=[])
    for lane in raw['lane_metrics']:
        lane.update(queue_age_available=False, age_sample_count=0,
                    last_capture_to_replay_qpc=None, maximum_capture_to_replay_qpc=None,
                    observed_pending_age_lower_bound_qpc=None)
    return raw


def test_pressure_counts_survive_without_debug_or_callback_timing(monkeypatch, tmp_path):
    from native_pc import native_hook_source as module
    from event_observer import ObserverStatus
    from desktop_audit import DesktopCaptureJournal
    from capture_cli import json_value
    probe = module.NativeHookEventProbe(SimpleNamespace(process=SimpleNamespace(process_alive=lambda: True)))
    probe.status = ObserverStatus.READY
    probe._owned_process_instance = (123, 99)
    state, calls, records = [0.0], [], []
    result = dict(retry=True, mark_retry_reason='native_fresh_publication_not_ready',
                  native_async_capture=untimed_async())
    def request(operation, **arguments):
        calls.append((operation, arguments))
        return result
    probe._pipe = SimpleNamespace(request=request)
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    monkeypatch.setattr(module.time, 'perf_counter', lambda: state[0])
    for now in (0., 1.99, 2., 2.01, 2.99, 3.):
        state[0] = now
        assert probe._raw_request('mark') is result
    assert [kind for kind, _ in records] == ['native_capture_pressure'] * 2
    assert all('native_timing' not in args for _, args in calls)
    result['retry'] = False
    state[0] = 3.1
    probe._raw_request('mark')
    probe._raw_request('mark')
    assert [data['phase'] for _, data in records] == ['pending', 'pending', 'recovered']
    assert all(data['async_capture']['available'] for _, data in records)
    assert all(data['async_capture']['queue_age_available'] is False for _, data in records)
    assert all(data['async_capture']['lane_metrics'][0]['maximum_capture_to_replay_qpc'] is None
               for _, data in records)
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
        header=lambda *a, **k: dict(kind='log_header', data=dict(debug_mode=False))) as journal:
        for kind, data in records:
            journal.write(kind, dict(data, private='hidden'))
    rows = [json.loads(s) for s in next(tmp_path.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()]
    assert [r['data']['phase'] for r in rows[1:]] == ['pending', 'pending', 'recovered']
    assert all('private' not in r['data'] and 'callback_timing' not in r['data']['async_capture'] for r in rows[1:])
    assert all(r['data']['async_capture']['captured_prefix_lag_records'] == 2 for r in rows[1:])


def test_pressure_reason_and_identity_are_not_taken_from_arbitrary_text():
    data = dict(schema=1, source='native_runtime', phase='pending', reason='C:/private', async_capture=clean())
    assert _performance_fields('native_capture_pressure', data) is None
    data['reason'] = 'native_fresh_publication_not_ready'
    data['async_capture']['process_created_filetime'] = -1
    assert _performance_fields('native_capture_pressure', data)['async_capture']['available'] is False


def test_healthy_native_counters_are_sparse_and_do_not_enable_callback_timing(monkeypatch):
    from native_pc import native_hook_source as module
    from event_observer import ObserverStatus
    probe = module.NativeHookEventProbe(SimpleNamespace(process=SimpleNamespace(process_alive=lambda: True)))
    probe.status = ObserverStatus.READY
    probe._owned_process_instance = (123, 99)
    result = dict(native_async_capture=untimed_async())
    state, records, calls = [0.0], [], []
    def request(operation, **arguments):
        calls.append((operation, arguments))
        return result
    probe._pipe = SimpleNamespace(request=request)
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    monkeypatch.setattr(module.time, 'perf_counter', lambda: state[0])
    for now in (0., .1, 1., 4.99, 5., 5.1):
        state[0] = now
        assert probe._raw_request('mark') is result
    assert [data['phase'] for _, data in records] == ['steady', 'steady']
    assert all('native_timing' not in arguments for _, arguments in calls)


def raw_async():
    return dict(version=1, reserved_ticket=20, processed_ticket=18, closed_ticket=18, published_ticket=16,
        desired_mode_epoch=1, published_mode_epoch=1, publication_ready=True, activity_generation=40,
        active=0, failure_code=0, captured_prefix_lag_records=2, publication_lag_records=2,
        published_event_sequence=10, delivered_event_sequence=8, read_prefix_lag_events=2,
        queue_age_available=True, queue_age_qpc_frequency=10_000_000,
        lane_metrics=[dict(lane=0, captured_records=20, consumed_records=18, capture_uncertain_records=0, captured_bytes=2048, consumed_bytes=1024,
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
    assert lane['capture_uncertain_records'] == 0
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


def test_uncertain_capture_counter_missing_is_unknown_and_not_zero():
    raw = raw_async(); raw['lane_metrics'][0].pop('capture_uncertain_records')
    row = clean(raw)['lane_metrics'][0]
    assert row == dict(available=False, reason='invalid_lane_row')
    for value in (True, -1, 2**64):
        raw = raw_async(); raw['lane_metrics'][0]['capture_uncertain_records'] = value
        assert clean(raw)['lane_metrics'][0] == row


def test_uncertain_counter_allows_crossed_atomic_snapshot_and_preserves_generation_in_journal(tmp_path):
    from desktop_audit import DesktopCaptureJournal
    from capture_cli import json_value
    records = []
    for generation, count in ((3, 21), (4, 2)):
        raw = raw_async()
        raw['lane_metrics'][0].update(lane_generation=generation, capture_uncertain_records=count)
        raw['callback_timing']['samples'][0]['lane_generation'] = generation
        data = dict(schema=1, source='native_runtime', available=True, native=native(), async_capture=clean(raw))
        row = _performance_fields('native_performance_timing', data)['async_capture']['lane_metrics'][0]
        assert row['available'] and row['capture_uncertain_records'] == count
        records.append(data)
    # 21 > captured_records20 is a permitted split-atomic observation, not a
    # reason to clamp it or declare a successful zero-uncertainty measurement.
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
        header=lambda *a, **k: dict(kind='log_header', data=dict(debug_mode=False)), debug_mode=lambda: False) as journal:
        for data in records:
            journal.write('native_performance_timing', data)
    saved = [json.loads(s) for s in next(tmp_path.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()]
    rows = [r['data']['async_capture']['lane_metrics'][0] for r in saved if r['kind']=='native_performance_timing']
    assert [(r['lane_generation'], r['capture_uncertain_records']) for r in rows] == [(3, 21), (4, 2)]



def test_sampling_diagnostic_keeps_request_and_finite_rejection_without_game_values():
    raw=raw_async()
    raw['sampling']=dict(version=1, plan_available=True, attempts=10, serial='7', request='8',
                         completed_request='6', retry_reason='sampling_native_activity_changed', secret='private')
    value=clean(raw)
    assert value['available'] is True
    assert value['sampling']['request']=='8'
    assert value['sampling']['retry_reason']=='sampling_native_activity_changed'
    assert 'private' not in repr(value)
    raw['sampling']['retry_reason']='C:/private/config'
    assert clean(raw)['available'] is False
