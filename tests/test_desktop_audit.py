"""Round-trip desktop journal deltas without changing acceptance journals."""
import json
import os
from pathlib import Path

import pytest

from capture_battles import replay
from capture_cli import json_value
from desktop_audit import DesktopAuditReader, DesktopAuditWriter, DesktopCaptureJournal, ENEMY_STATE_DELTA
from test_lifecycle_events import connected


def _test_header(source, *, debug_mode):
    return {'kind': 'log_header', 'data': {'debug_mode': debug_mode}}


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_capture_selects_key_events_until_debug_enabled_and_respects_live_toggle(tmp_path):
    detailed = False
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               header=_test_header, debug_mode=lambda: detailed) as journal:
        journal.write('published', object())  # Never serialized while debug mode is off.
        journal.write('capture_error', {'error_type': 'OSError', 'reason': 'C:/private/a'})
        journal.write('capture_error', {'error_type': 'OSError', 'reason': 'C:/private/b'})
        detailed = True
        journal.write('sample', {'frame': 7})
        detailed = False
        journal.write('published', object())
        journal.write('stopped', {'cleanup_errors': {'collector': ['C:/private/c']}})
    log, = tmp_path.glob('wpf-*.jsonl')
    header, error, enabled, sample, disabled, stopped = _records(log)
    assert header == _test_header('desktop_capture', debug_mode=False)
    assert error['kind'] == 'capture_error' and error['data']['error_type'] == 'OSError'
    assert 'reason' not in error['data']
    assert enabled['kind'] == disabled['kind'] == 'debug_mode'
    assert enabled['data']['enabled'] is True and disabled['data']['enabled'] is False
    assert sample == {'kind': 'sample', 'data': {'frame': 7}}
    assert stopped['data']['cleanup_ok'] is False and 'cleanup_errors' not in stopped['data']


def test_first_native_pipe_failure_keeps_operation_and_error_code_without_debug(tmp_path):
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               header=_test_header) as journal:
        journal.write('native_hook_request_failed', dict(
            op='read', error_type='OSError', win32_error=233, reason='private pipe details'))
    log, = tmp_path.glob('wpf-*.jsonl')
    _, failure = _records(log)
    assert failure['kind'] == 'native_hook_request_failed'
    assert failure['data'] == dict(op='read', error_type='OSError', win32_error=233,
                                   utc=failure['data']['utc'])


@pytest.mark.parametrize('debug_mode', [False, True])
def test_capture_health_always_keeps_only_bounded_operational_fields(tmp_path, debug_mode):
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               header=_test_header, debug_mode=debug_mode) as journal:
        data = dict(phase='stale', code='callback_busy', age_ms=2400, retry_count=8, attempt_ms=25,
                    platform='native_pc', event_backend='native', observer_status='recording',
                    lifecycle='playing', generation=7)
        journal.write('capture_health', dict(data, reason='C:/private/game.log', target='private_device',
                                             event={'amount': 9}, gap_ms=True, utc='invalid'))
        journal.write('capture_health', dict(data, code='private_person'))
    log, = tmp_path.glob('wpf-*.jsonl')
    _, record = _records(log)
    assert record == {'kind': 'capture_health', 'data': dict(data, utc=record['data']['utc'])}
    assert record['data']['utc'].endswith('+00:00')


def test_desktop_enemy_state_delta_restores_prefix_across_records_and_resets():
    records = []
    writer = DesktopAuditWriter(records.append)
    expected = []

    def write(kind, data):
        expected.append(dict(kind=kind, data=data))
        writer.write(kind, data)

    def statistics(session, frame, *sequences, complete=True):
        return dict(session=session, rows={}, enemy_state=dict(
            start_frame=frame, events=[dict(sequence=value) for value in sequences],
            complete=complete))

    write('published', dict(session='first', statistics=statistics('first', 4, 5)))
    write('lifecycle_observation', {'sample': {'guard': {'start': 4}}})
    write('published', dict(session='first', statistics=statistics('first', 4, 5, 7)))
    write('lifecycle_summary', dict(statistics=statistics('first', 4, 5, 7, complete=False)))
    write('coverage_closed', statistics('first', 4, 5, 7, complete=False))
    write('published', dict(session='second', statistics=statistics('second', 4, 51)))
    write('published', dict(session='second', statistics=statistics('second', 6, 60)))
    write('published', dict(session='second', statistics=statistics('second', 6, 61)))
    write('published', dict(session='second', statistics=statistics('second', 6)))

    assert [record['data']['offset'] for record in records
            if record['kind'] == ENEMY_STATE_DELTA] == [0, 1, 2, 2, 0, 0, 0, 0]
    reader = DesktopAuditReader()
    assert [reader.restore(record) for record in records] == expected
    with pytest.raises(ValueError, match='missing enemy state delta prefix'):
        DesktopAuditReader().restore(records[2])


def test_desktop_enemy_state_delta_does_not_commit_after_failed_write():
    records = []

    def write(record):
        if not records:
            records.append(None)
            raise OSError('journal write failed')
        records.append(record)

    writer = DesktopAuditWriter(write)
    sample = dict(statistics=dict(session='one', enemy_state=dict(
        start_frame=4, events=[dict(sequence=5)])))
    with pytest.raises(OSError, match='journal write failed'):
        writer.write('published', sample)
    writer.write('published', sample)
    assert records[-1]['data']['offset'] == 0


def test_desktop_delta_replay_checks_enemy_state_in_restored_statistics(tmp_path):
    session, collector, _, captured = connected()
    observer = session.observer
    drain = observer.drain
    observer.drain = lambda: dict(drain(), enemy_state_ready=True,
                                  enemy_entry_ready=True, enemy_phase_ready=True)
    try:
        session.read()
        collector.frame, collector.game_time = 2, 1.0
        observer.sequence = observer.epoch = 1
        observer.events = [dict(sequence=1, frame=2, game_time=1., kind='enemy_enter',
                                source=None, target='enemy_a', target_instance='12',
                                amount=0, hp_after=60_000,
                                enemy_entry_attributes={'max_hp': 60_000})]
        session.read()
    finally:
        session.close()
    records = []
    write = DesktopAuditWriter(records.append).write
    for kind, data in captured:
        write(kind, data)
    path = tmp_path / 'capture.jsonl'

    def save():
        path.write_text('\n'.join(json.dumps(record, default=json_value) for record in records),
                        encoding='utf-8')

    save()
    actual = replay(path)['last_state']['statistics']['enemy_state']
    assert actual['events'][0]['hp_after'] == 60_000
    publication = next(record['data']['value'] for record in records if
                       record['kind'] == ENEMY_STATE_DELTA and
                       record['data']['kind'] == 'published' and
                       record['data']['value']['frame'] == 2)
    publication['statistics']['enemy_state']['events'][0]['hp_after'] = 10
    save()
    with pytest.raises(ValueError, match='published statistics differ'):
        replay(path)


def test_capture_rotation_keeps_newest_and_only_prunes_managed_files(tmp_path):
    oldest = tmp_path / f'wpf-20260101-000000-000000-{"0" * 32}.jsonl'
    recent = tmp_path / f'wpf-20260101-000001-000000-{"0" * 32}.jsonl'
    other = tmp_path / 'capture.jsonl'
    history = tmp_path.parent / 'history.json'
    for path, size, stamp in ((oldest, 270, 1), (recent, 85, 2), (other, 300, 3), (history, 300, 4)):
        path.write_bytes(b'x' * size)
        os.utime(path, (stamp, stamp))

    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               segment_bytes=50, total_bytes=320,
                               header=_test_header, debug_mode=True) as journal:
        assert not oldest.exists() and recent.exists()
        for index in range(5):
            journal.write('sample', {'index': index, 'text': '字' * 14})
            managed = sorted(tmp_path.glob('wpf-*.jsonl'))
            assert sum(path.stat().st_size for path in managed) <= 320
            header, record = _records(managed[-1])
            assert header == _test_header('desktop_capture', debug_mode=True)
            assert record['data']['index'] == index

    assert not recent.exists()
    assert [_records(path)[1]['data']['index'] for path in managed] == [3, 4]
    assert other.stat().st_size == history.stat().st_size == 300


def test_capture_rotation_replays_complete_chain_and_inspects_retained_tail(tmp_path):
    session, collector, _, captured = connected()
    observer = session.observer
    drain = observer.drain
    observer.drain = lambda: dict(drain(), enemy_state_ready=True,
                                  enemy_entry_ready=True, enemy_phase_ready=True)
    try:
        session.read()
        collector.frame, collector.game_time = 2, 1.
        observer.sequence = observer.epoch = 1
        observer.events = [dict(sequence=1, frame=2, game_time=1., kind='enemy_enter',
                                source=None, target='enemy_a', target_instance='12',
                                amount=0, hp_after=60_000,
                                enemy_entry_attributes={'max_hp': 60_000})]
        session.read()
    finally:
        session.close()

    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               segment_bytes=1, debug_mode=True) as journal:
        for kind, data in captured:
            journal.write(kind, data)

    parts = sorted(tmp_path.glob('wpf-*.jsonl'))
    assert len(parts) == len(captured)
    replayed = replay(parts[-1])
    assert replayed['samples'] == 2
    assert replayed['last_state']['statistics']['enemy_state']['events'][0]['hp_after'] == 60_000

    parts[0].unlink()
    retained = replay(parts[-1])
    assert retained['coverage'] == 'retained_only'
    assert retained['segments'][0] == 2
    assert retained['last_publication']['statistics']['enemy_state']['events'][0]['hp_after'] == 60_000


def test_capture_retention_retries_after_locked_file_is_released(tmp_path, monkeypatch):
    old = tmp_path / f'wpf-20260101-000000-000000-{"0" * 32}.jsonl'
    old.write_bytes(b'x' * 100)
    unlink = Path.unlink
    locked = True
    now = 0.
    warnings = []

    def unlink_if_available(path, **kwargs):
        if path == old and locked:
            raise PermissionError('log is open')
        unlink(path, **kwargs)

    monkeypatch.setattr(Path, 'unlink', unlink_if_available)
    monkeypatch.setattr('desktop_audit.time.monotonic', lambda: now)
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=warnings.append,
                               segment_bytes=50, total_bytes=100,
                               header=_test_header, debug_mode=True) as journal:
        journal.write('sample', {'frame': 1})
        journal.write('sample', {'frame': 2})
        assert old.exists() and len(warnings) == 1
        locked, now = False, 11.
        journal.write('sample', {'frame': 3})

    log, = tmp_path.glob('wpf-*.jsonl')
    assert not old.exists()
    assert _records(log)[1]['data'] == {'frame': 3}
    assert replay(log)['coverage'] == 'retained_only'


def test_native_replay_gap_numeric_context_survives_non_debug_journal(tmp_path):
    fields = dict(op='mark', error_type='RuntimeError', code='native_replay_gap',
                  replay_hook_id=2, replay_edge=1, replay_ticket=2**64-1,
                  replay_role_id=3, replay_checkpoint=0, replay_field_id=-1,
                  replay_gap_count=1, metadata_read_size=8,
                  replay_reason_codes='1', replay_script_lines='1:123.2:456')
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               header=_test_header) as journal:
        journal.write('native_hook_request_failed', dict(fields, reason='private pointer'))
    log, = tmp_path.glob('wpf-*.jsonl')
    _, failure = _records(log)
    data = failure['data']
    assert {k: data[k] for k in fields} == fields
    assert 'reason' not in data


@pytest.mark.parametrize('debug', [False, True])
def test_cursor_failure_context_is_bounded_in_normal_and_debug_journal(tmp_path, debug):
    detail = dict(version=1, reason='cursor_lifetime_changed', phase='pending_prefix',
                  owner_matches=True, observer_present=True, expected_present=True, actual_present=True)
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               header=_test_header, debug_mode=debug) as journal:
        journal.write('capture_error', dict(error_type='StreamGap', cursor_failure=detail))
    log, = tmp_path.glob('wpf-*.jsonl')
    row = next(row for row in _records(log) if row['kind'] == 'capture_error')
    assert row['data']['cursor_failure'] == detail


def test_cursor_failure_normal_journal_refuses_unknown_text(tmp_path):
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               header=_test_header, debug_mode=False) as journal:
        journal.write('capture_error', dict(error_type='StreamGap', cursor_failure={
            'version':1, 'reason':'cursor_lifetime_changed', 'phase':'C:/private/token'}))
    log, = tmp_path.glob('wpf-*.jsonl')
    row = next(row for row in _records(log) if row['kind'] == 'capture_error')
    assert 'cursor_failure' not in row['data']
    assert 'private' not in repr(row)


@pytest.mark.parametrize('debug', [False, True])
@pytest.mark.parametrize('reason,code,expected', [
    ('publication_certificate_mismatch', None, dict(sampling_failure_reason='publication_certificate_mismatch')),
    ('structural_capture_error', 10, dict(sampling_failure_reason='structural_capture_error', sampling_capture_error_code=10)),
    ('structural_capture_error', True, dict(sampling_failure_reason='structural_capture_error')),
    ('structural_capture_error', 11, dict(sampling_failure_reason='structural_capture_error')),
    ('publication_failed', 4, dict(sampling_failure_reason='publication_failed')),
    ('PRIVATE', 0, {}),
    (['PRIVATE'], 0, {}),
])
def test_sampling_failure_journal_requires_finite_schema(tmp_path, debug, reason, code, expected):
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               header=_test_header, debug_mode=debug) as journal:
        journal.write('native_hook_request_failed', dict(op='mark', error_type='RuntimeError',
            sampling_failure_reason=reason, sampling_capture_error_code=code))
    log, = tmp_path.glob('wpf-*.jsonl')
    row = next(row for row in _records(log) if row['kind'] == 'native_hook_request_failed')
    assert {key:value for key,value in row['data'].items() if key.startswith('sampling_')} == expected
    assert 'PRIVATE' not in repr(row)


def test_sampling_failure_journal_schema_covers_only_wire_reason_vocabulary():
    from native_pc.native_hook_source import SAMPLING_FAILURE_REASONS
    import desktop_audit
    assert desktop_audit.SAMPLING_FAILURE_REASONS == frozenset(SAMPLING_FAILURE_REASONS.values()) | {'structural_capture_error', 'native_stage_failure'}


@pytest.mark.parametrize('debug', [False, True])
@pytest.mark.parametrize('fault', [None, 'stage', 'lane', 'json', 'ticket', 'role', 'field', 'count', 'bytes', 'reasons', 'lines'])
def test_numeric_sampling_context_journal_has_bounded_schema(tmp_path, debug, fault):
    fields = dict(sampling_failure_reason='native_stage_failure', sampling_stage=7,
        sampling_lane_fault=32, sampling_json_exception_id=599, code='native_sampling_gap',
        replay_ticket=2**64-1, replay_role_id=65535, replay_checkpoint=65535, replay_field_id=-1,
        replay_gap_count=16, metadata_read_size=1048576, replay_reason_codes='0.31', replay_script_lines='1:123.2:456')
    changes = dict(stage=('sampling_stage', True), lane=('sampling_lane_fault', 33),
        json=('sampling_json_exception_id', 99), ticket=('replay_ticket', 2**64),
        role=('replay_role_id', 65536), field=('replay_field_id', -2), count=('replay_gap_count', 17),
        bytes=('metadata_read_size', 1048577), reasons=('replay_reason_codes', 'PRIVATE'),
        lines=('replay_script_lines', '1:123.PRIVATE'))
    if fault is not None:
        key, value = changes[fault]
        fields[key] = value
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
                               header=_test_header, debug_mode=debug) as journal:
        journal.write('native_hook_request_failed', fields)
    log, = tmp_path.glob('wpf-*.jsonl')
    row = next(row for row in _records(log) if row['kind'] == 'native_hook_request_failed')['data']
    if fault is None:
        assert all(row[key] == value for key, value in fields.items())
    elif fault in ('stage', 'lane', 'json'):
        assert 'sampling_failure_reason' not in row and 'sampling_stage' not in row
    else:
        assert 'code' not in row and 'replay_ticket' not in row and 'replay_reason_codes' not in row
    assert 'PRIVATE' not in repr(row)
