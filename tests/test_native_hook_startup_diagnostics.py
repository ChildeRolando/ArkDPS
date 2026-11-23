"""Default native startup diagnostics are bounded facts, not config dumps."""
from copy import deepcopy
import json

import pytest

import desktop_audit
import desktop_bridge
from capture_cli import json_value


def facts(event='native_prepare_failed'):
    value = dict(event=event, provider='arkdps-native-hooks', status='process_failed', code='loader_failed',
                utc='2026-11-24T00:00:00.000001+00:00', pid=31672, created_filetime=134399400000000001,
                source_build='win-x64:' + 'a' * 64, source_sha256='b' * 64, dll_sha256='c' * 64,
                preparation_age_seconds=.125, preparation=dict(prepared=False, claimed=False, closed=False,
                    hook_disable_confirmed=True, source_build='win-x64:' + 'a' * 64,
                    source_sha256='b' * 64, dll_sha256='c' * 64,
                    loader=dict(pid=31672, created_filetime=134399400000000001, stage='complete',
                        operation='find_module', check='loaded_component', submitted=True,
                        thread_exit_code=0xffffffff, verification_complete=True, verification_alive=True,
                        process_exit_code=259, module_query_attempted=True, module_query_succeeded=True,
                        module_found=False, module_base_low32=None, return_matches=False,
                        path_utf16_units=220, path_utf16_bytes=440, path_buffer_bytes=442,
                        loader_module_basename='KERNELBASE.dll', loader_export_rva=1234,
                        preparation_retries=0, verification_retries=0, handle_closed=False,
                        timing_seconds=dict(load_started=0., submitted=.1, thread_completed=.2,
                            verification_started=.2, verification_completed=.3, closed=None))))
    value['loader'] = value['preparation'].pop('loader')
    return value


def test_shared_filter_keeps_actual_safe_native_diagnostics():
    source = facts()
    result = desktop_audit.sanitize_native_hook_startup(source)
    assert result == source
    assert result is not source and result['preparation'] is not source['preparation']
    assert result['loader']['thread_exit_code'] == 0xffffffff


@pytest.mark.parametrize('debug', [False, True])
def test_journal_cannot_bypass_deep_filter_even_with_debug(tmp_path, debug):
    value = facts()
    value.update(token='PRIVATE_TOKEN', source='PRIVATE_SOURCE', config_path='C:/PRIVATE_PATH', error='PRIVATE_ERROR')
    value['preparation'].update(cleanup_errors={'loader': 'PRIVATE_ERROR'}, config='PRIVATE_CONFIG')
    value['loader'].update(token='PRIVATE_TOKEN', path='C:/PRIVATE_PATH', exception='PRIVATE_ERROR')
    value['loader']['timing_seconds']['token'] = 'PRIVATE_TOKEN'
    with desktop_audit.DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
            header=lambda *a, **kw: dict(kind='test_header', data={}), debug_mode=debug) as journal:
        journal.write('native_prepare_failed', value)
    path, = tmp_path.glob('wpf-*.jsonl')
    text = path.read_text('utf-8')
    assert 'PRIVATE_' not in text
    record = [json.loads(line) for line in text.splitlines() if json.loads(line)['kind'] == 'native_prepare_failed'][0]
    assert record['data']['loader']['thread_exit_code'] == 0xffffffff
    assert record['data']['utc'] == value['utc']  # Source emit time, not later journal drain time.
    assert desktop_audit.KEY_EVENTS['native_prepare_failed'] is not None


@pytest.mark.parametrize('container,key,value', [
    ('top', 'pid', True), ('top', 'created_filetime', '134399400000000001'),
    ('top', 'source_sha256', 'C:/PRIVATE_PATH'), ('top', 'code', 'PRIVATE_TOKEN'),
    ('top', 'preparation_age_seconds', float('nan')), ('top', 'utc', 'PRIVATE_TOKEN'),
    ('loader', 'submitted', 1), ('loader', 'thread_exit_code', -1),
    ('loader', 'process_exit_code', 2 ** 32), ('loader', 'module_base', 'C:/PRIVATE_PATH'),
    ('loader', 'operation', 'PRIVATE_TOKEN'), ('loader', 'loader_module_basename', 'C:/PRIVATE_PATH'),
    ('loader', 'path_utf16_units', True), ('timing', 'submitted', float('inf')),
    ('timing', 'submitted', 10 ** 1000),
])
def test_wrong_typed_values_are_not_logged(container, key, value):
    source = facts()
    target = source if container == 'top' else source['loader']
    if container == 'timing':
        target = target['timing_seconds']
    target[key] = value
    result = desktop_audit.sanitize_native_hook_startup(source)
    output = result if container == 'top' else result['loader']
    if container == 'timing':
        output = output['timing_seconds']
    assert key not in output
    assert 'PRIVATE_' not in json.dumps(result)


@pytest.mark.parametrize('value', [None, [], {'event': 'unknown'}, {'event': 'native_prepare_failed', 'provider': 'frida'}])
def test_invalid_native_event_envelope_is_rejected(value):
    assert desktop_audit.sanitize_native_hook_startup(value) is None


def test_event_kind_cannot_disagree_with_record():
    assert desktop_audit.sanitize_native_hook_startup(facts(), event='native_prepare_completed') is None


@pytest.mark.parametrize('event', ['native_prepare_started', 'native_prepare_failed', 'native_prepare_completed', 'native_prepare_closed'])
def test_native_trace_is_default_key_log_and_audited_when_status_unchanged(tmp_path, monkeypatch, event):
    worker = desktop_bridge.CollectionWorker(desktop_bridge.DesktopState(tmp_path))
    logged, audited = [], []
    monkeypatch.setattr(desktop_bridge, '_write_diagnostic_event', lambda kind, data: logged.append((kind, data)))
    worker._bootstrap_audit = lambda kind, data: audited.append((kind, data))
    worker._last_pc_bootstrap = dict(status='process_failed', code='loader_failed', ready_generation=0)
    value = facts(event)
    value['token'] = 'PRIVATE_TOKEN'
    worker._trace_pc_startup(value)
    # The runtime callback must not write the journal concurrently. The worker
    # drains the event before state deduplication, including an unchanged state.
    assert not logged and not audited
    worker._emit_pc_bootstrap(dict(worker._last_pc_bootstrap))
    assert len(logged) == 1 and logged[0][0] == 'pc-startup'
    assert logged[0][1]['event'] == event and 'PRIVATE_TOKEN' not in json.dumps(logged)
    assert len(audited) == 1 and audited[0][0] == event
    worker._emit_pc_bootstrap(dict(worker._last_pc_bootstrap))
    assert len(audited) == 1


def test_trace_and_journal_failure_do_not_change_worker_result(tmp_path, monkeypatch):
    worker = desktop_bridge.CollectionWorker(desktop_bridge.DesktopState(tmp_path))
    before = (worker.pc_source, worker.session, worker._last_pc_bootstrap)
    def fail(*args, **kwargs):
        raise OSError('PRIVATE_TOKEN')
    monkeypatch.setattr(desktop_bridge, '_write_diagnostic_event', fail)
    worker._bootstrap_audit = fail
    worker._trace_pc_startup(facts())
    worker._emit_pc_bootstrap(None)
    assert (worker.pc_source, worker.session, worker._last_pc_bootstrap) == before


def test_legacy_frida_trace_selection_is_unchanged(tmp_path, monkeypatch):
    worker = desktop_bridge.CollectionWorker(desktop_bridge.DesktopState(tmp_path))
    logged = []
    monkeypatch.setattr(desktop_bridge, '_write_diagnostic_event', lambda kind, data: logged.append(data))
    legacy = dict(event='loader_completed', status='loading', loader_phase='complete', loader_verified=True)
    worker._trace_pc_startup(legacy)
    assert logged == [legacy]
    worker._trace_pc_startup(dict(event='state', status='authenticating'))
    assert logged == [legacy]


@pytest.mark.parametrize('event', [None, [], {}, 'native_prepare_unknown'])
def test_debug_cannot_turn_unknown_native_event_into_a_raw_dump(tmp_path, monkeypatch, event):
    worker = desktop_bridge.CollectionWorker(desktop_bridge.DesktopState(tmp_path))
    worker.set_debug_mode(True)
    logged = []
    monkeypatch.setattr(desktop_bridge, '_write_diagnostic_event', lambda *args: logged.append(args))
    worker._trace_pc_startup(dict(event=event, provider='arkdps-native-hooks', token='PRIVATE_TOKEN'))
    assert not logged


def test_diagnostic_queue_is_bounded_without_starting_or_blocking_worker(tmp_path, monkeypatch):
    worker = desktop_bridge.CollectionWorker(desktop_bridge.DesktopState(tmp_path))
    logged, audited = [], []
    monkeypatch.setattr(desktop_bridge, '_write_diagnostic_event', lambda kind, data: logged.append(data))
    worker._bootstrap_audit = lambda kind, data: audited.append(data)
    for _ in range(100):
        worker._trace_pc_startup(facts())
    assert worker._pc_startup_audit_queue.qsize() == 64
    assert not logged and not audited
    worker._flush_pc_startup_audit()
    assert len(logged) == len(audited) == 65
    overflow = logged[-1]
    assert overflow['event'] == 'native_prepare_trace_overflow'
    assert overflow['dropped_records'] == overflow['total_dropped_records'] == 36
    assert overflow['diagnostic_records_complete'] is False
    worker._flush_pc_startup_audit()
    assert len(logged) == len(audited) == 65
    assert not worker.thread.is_alive() and worker.pc_runtime is None


@pytest.mark.parametrize('failed_sink', ['stderr', 'journal'])
def test_one_sink_failure_does_not_skip_the_other_sink(tmp_path, monkeypatch, failed_sink):
    worker = desktop_bridge.CollectionWorker(desktop_bridge.DesktopState(tmp_path))
    written = []
    def stderr(kind, data):
        if failed_sink == 'stderr':
            raise OSError('PRIVATE_TOKEN')
        written.append('stderr')
    def audit(kind, data):
        if failed_sink == 'journal':
            raise OSError('PRIVATE_TOKEN')
        written.append('journal')
    monkeypatch.setattr(desktop_bridge, '_write_diagnostic_event', stderr)
    worker._bootstrap_audit = audit
    worker._trace_pc_startup(facts())
    assert not written
    worker._flush_pc_startup_audit()
    assert written == ['journal' if failed_sink == 'stderr' else 'stderr']


@pytest.mark.parametrize('debug', [False, True])
def test_overflow_counter_is_also_deep_filtered_in_default_and_debug_journal(tmp_path, debug):
    value = dict(event='native_prepare_trace_overflow', provider='arkdps-native-hooks',
                 dropped_records=2, total_dropped_records=3, diagnostic_records_complete=False,
                 token='PRIVATE_TOKEN', source='PRIVATE_SOURCE')
    with desktop_audit.DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
            header=lambda *a, **kw: dict(kind='test_header', data={}), debug_mode=debug) as journal:
        journal.write('native_prepare_trace_overflow', value)
    path, = tmp_path.glob('wpf-*.jsonl')
    assert 'PRIVATE_' not in path.read_text('utf-8')
    value['dropped_records'] = True
    assert desktop_audit.sanitize_native_hook_startup(value) is None


def test_real_native_runtime_record_roundtrips_through_shared_filter(tmp_path):
    from native_pc.native_hook_runtime import NativeHookRuntime, RuntimeState
    runtime = NativeHookRuntime(tmp_path / 'game', runtime_dir=tmp_path / 'test-runtime')
    runtime._lease = dict(pid=31672, created=134399400000000001, source_build='win-x64:' + 'a' * 64)
    runtime.status = RuntimeState.PROCESS_FAILED
    raw = facts()
    preparation = dict(raw['preparation'], process_instance=[raw['pid'], raw['created_filetime']],
        prepared_at=None, prepared_wall_time=None, expires_at=None, valid_seconds=120, loader=raw['loader'])
    received = []
    runtime.startup_trace = received.append
    runtime._trace_startup('native_prepare_failed', preparation=preparation, snapshot_phase='after_prepare',
                           error=RuntimeError('PRIVATE_TOKEN'))
    record, = received
    assert desktop_audit.sanitize_native_hook_startup(record) == record
    assert 'PRIVATE_TOKEN' not in json.dumps(record)
    assert not runtime.runtime_dir.exists()  # Constructor+trace only, not start/prepare.
