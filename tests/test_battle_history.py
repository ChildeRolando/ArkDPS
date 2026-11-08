import json
from unittest.mock import patch

import pytest

from battle_history import BattleRecord, BattleHistory, HistoryError
from battle_lifecycle import BattleRuntime
from test_battle_lifecycle import FakeCollector


def archive():
    collector, runtime = FakeCollector(), BattleRuntime()
    runtime.poll(collector)
    collector.advance(time=12., frame=12, damage=230.)
    runtime.poll(collector)
    collector.lifecycle['battle'] = None
    runtime.poll(collector)
    return runtime.sessions.pop_archives()[0]


def record(index=0, **metadata):
    value = archive()
    value['battle_id'] = value['statistics']['session'] = f'battle:{index}'
    value['statistics']['adapter_session'] = value['battle_id']
    value['last_sample']['session'] = value['battle_id']
    value['last_sample']['observed_at'] = 1700000000 + index
    value['metadata'].update(metadata)
    return BattleRecord.from_archive(value)


def test_101st_record_evicts_only_oldest_and_restart_reads(tmp_path):
    history = BattleHistory(tmp_path)
    for index in range(101):
        assert history.save(record(index)).written
    restarted = BattleHistory(tmp_path)
    result = restarted.list_recent()
    assert not result.diagnostics
    assert [r.battle_id for r in result.records] == [f'battle:{i}' for i in range(100, 0, -1)]
    assert not restarted.get('battle:0').records
    assert restarted.get('battle:60').records[0].statistics == record(60).statistics
    assert len(list(tmp_path.glob('*.json'))) == 100


def test_duplicate_end_across_restart_is_idempotent_and_conflict_protects(tmp_path):
    original = record()
    history = BattleHistory(tmp_path)
    history.save(original)
    path, = tmp_path.glob('*.json')
    before = path.read_bytes()
    assert not BattleHistory(tmp_path).save(original).written
    assert path.read_bytes() == before
    changed = original.to_dict()
    changed['statistics']['rows']['a']['damage'] += 1
    with pytest.raises(HistoryError, match='conflict'):
        history.save(BattleRecord.from_dict(changed))
    assert path.read_bytes() == before


@pytest.mark.parametrize('outcome', ['victory', 'defeat', 'abandoned', 'unknown'])
@pytest.mark.parametrize('practice', [True, False, None])
def test_outcomes_practice_and_unknown_round_trip(tmp_path, outcome, practice):
    original = record(outcome=outcome, practice=practice, stage_id=None)
    history = BattleHistory(tmp_path)
    history.save(original)
    restored = history.get(original.battle_id).records[0]
    assert restored.to_dict() == original.to_dict()
    assert restored.metadata['stage_id'] is None
    row = restored.statistics['rows']['a']
    assert row['shield_absorbed'] is None
    assert row['damage_by_type']['arts'] == 0
    assert restored.to_dict()['duration'] == 12.


def test_event_zero_and_partial_coverage_and_summon_sources_round_trip(tmp_path):
    data = record().to_dict()
    row = data['statistics']['rows']['a']
    row.update(shield_absorbed=0., shield_coverage_start=10.,
               event_coverage_end=11., event_coverage_complete=False,
               sources=['a', 'token_a'], active_damage=1., observed_damage=20.,
               engagement_damage=20.)
    value = BattleRecord.from_dict(data)
    history = BattleHistory(tmp_path)
    history.save(value)
    assert history.get(value.battle_id).records[0].to_dict() == data


@pytest.mark.parametrize('damage', ['truncated', 'version', 'content', 'nan', 'wrong_id'])
def test_one_bad_file_diagnosed_other_records_read_and_not_overwritten(tmp_path, damage):
    history = BattleHistory(tmp_path)
    first = record(1)
    history.save(first)
    history.save(record(2))
    bad = history.path_for(first.battle_id)
    data = first.to_dict()
    if damage == 'version':
        data['schema_version'] = 999
    elif damage == 'content':
        del data['statistics']['rows']['a']['damage']
    elif damage == 'nan':
        data['statistics']['rows']['a']['damage'] = float('nan')
    elif damage == 'wrong_id':
        data['battle_id'] = 'different'
    bad.write_text('{' if damage == 'truncated' else json.dumps(data), encoding='utf-8')
    before = bad.read_bytes()
    result = BattleHistory(tmp_path).list_recent()
    assert [r.battle_id for r in result.records] == ['battle:2']
    assert result.diagnostics
    assert history.get(first.battle_id).diagnostics
    with pytest.raises(HistoryError):
        history.save(first)
    assert bad.read_bytes() == before


@pytest.mark.parametrize('failure', ['replace', 'fsync', 'tempfile'])
def test_write_failure_preserves_100_records_without_pruning(tmp_path, failure):
    history = BattleHistory(tmp_path)
    for index in range(100):
        history.save(record(index))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    target = {'replace': 'battle_history.os.replace', 'fsync': 'battle_history.os.fsync',
              'tempfile': 'battle_history.tempfile.NamedTemporaryFile'}[failure]
    with patch(target, side_effect=OSError('disk unavailable')):
        with pytest.raises(HistoryError, match='disk unavailable'):
            history.save(record(101))
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_prune_failure_is_reported_after_successful_commit(tmp_path):
    history = BattleHistory(tmp_path)
    for index in range(100):
        history.save(record(index))
    with patch('battle_history.Path.unlink', side_effect=PermissionError('locked')):
        result = history.save(record(100))
    assert result.written and any(d.code == 'prune_failed' for d in result.diagnostics)
    assert history.get('battle:100').records
    assert len(list(tmp_path.glob('*.json'))) == 101
    assert not history.save(record(100)).written
    assert len(list(tmp_path.glob('*.json'))) == 100


def test_same_directory_atomic_write_and_no_path_traversal(tmp_path):
    history = BattleHistory(tmp_path / 'history')
    value = record().to_dict()
    value['battle_id'] = value['statistics']['session'] = '../unsafe:战局'
    value['statistics']['adapter_session'] = value['last_sample']['session'] = value['battle_id']
    value = BattleRecord.from_dict(value)
    import os
    original = os.replace
    def replace_file(source, target):
        assert source.parent == target.parent == tmp_path / 'history'
        assert json.loads(source.read_text(encoding='utf-8'))['battle_id'] == value.battle_id
        return original(source, target)
    with patch('battle_history.os.replace', side_effect=replace_file):
        history.save(value)
    assert history.get(value.battle_id).records


def test_gap_intervals_survive_recovery_and_archive():
    c, r = FakeCollector(), BattleRuntime()
    r.poll(c)
    c.advance(time=11., frame=11)
    r.poll(c)
    r.disconnected('read_failed')
    c.advance(time=15., frame=15)
    r.poll(c)
    c.advance(time=16., frame=16)
    r.poll(c)
    c.lifecycle['battle'] = None
    r.poll(c)
    data = BattleRecord.from_archive(r.sessions.pop_archives()[0]).to_dict()
    assert data['snapshot_intervals'] == [[10., 11.], [15., 16.]]


def test_collection_entry_persists_archives_and_diagnoses_failure_without_losing_live(tmp_path):
    from collection_session import CollectionSession
    from test_collection_session import Collector
    from event_capture import CaptureRetry
    events = []
    collector = Collector([])
    current = CollectionSession('adb', 'serial', collector=collector, frida_remote=None,
                                history=BattleHistory(tmp_path),
                                audit=lambda kind, data: events.append((kind, data)))
    current.enable_lifecycle()
    current.connect()
    current.read()
    collector.state = 3
    current.read()
    # Explicit departure, not a stable finalizing snapshot, triggers persistence.
    original = collector.read_lifecycle
    collector.read_lifecycle = lambda: dict(original(), battle=None)
    with patch('battle_history.os.replace', side_effect=OSError('disk full')):
        with pytest.raises(CaptureRetry):
            current.read()
    assert any(kind == 'history_error' for kind, _ in events)
    assert current.runtime.sessions.live()['statistics']['rows']['char_a']['damage'] == 100
    with pytest.raises(CaptureRetry):
        current.read()
    assert len(BattleHistory(tmp_path).list_recent().records) == 1
    assert any(kind == 'history_saved' for kind, _ in events)
    current.close()


@pytest.mark.parametrize('failed_rescans', [0, 2])
def test_failed_archive_survives_session_replacement_and_saves_once(tmp_path, failed_rescans):
    from collection_session import CollectionSession
    from test_collection_session import Collector
    from event_capture import CaptureRetry
    history, events = BattleHistory(tmp_path), []
    runtime = BattleRuntime()

    def connect():
        session = CollectionSession('adb', 'serial', collector=Collector([]),
                                    frida_remote=None, history=history,
                                    audit=lambda kind, data: events.append((kind, data)))
        session.enable_lifecycle(runtime)
        session.connect()
        return session

    current = connect()
    current.read()
    battle_id = runtime.sessions.battle_id
    lifecycle = current.collector.read_lifecycle
    current.collector.read_lifecycle = lambda: dict(lifecycle(), battle=None)
    with patch('battle_history.os.replace', side_effect=OSError('disk unavailable')):
        with pytest.raises(CaptureRetry):
            current.read()
        current.close()
        for _ in range(failed_rescans):
            current = connect()
            current.read()
            current.close()
    assert not history.list_recent().records
    # app.py replaces the connection but retains the runtime and history.
    current = connect()
    current.read()
    current.close()
    result = BattleHistory(tmp_path).get(battle_id)
    assert len(result.records) == 1 and not result.diagnostics
    path = history.path_for(battle_id)
    before = path.read_bytes()
    current = connect()
    current.read()
    current.close()
    assert path.read_bytes() == before
    saved = [data for kind, data in events if kind == 'history_saved'
             and data['battle_id'] == battle_id]
    assert len(saved) == 1 and saved[0]['written']
    assert len([data for kind, data in events if kind == 'archive_ready'
                and data['battle_id'] == battle_id]) == 1


def test_corrupt_pending_archive_does_not_block_healthy_battle_or_hide_error(tmp_path):
    import os
    from collection_session import CollectionSession
    from test_collection_session import Collector
    from event_capture import CaptureRetry
    history, events = BattleHistory(tmp_path), []
    collector = Collector([])
    current = CollectionSession('adb', 'serial', collector=collector,
                                frida_remote=None, history=history,
                                audit=lambda kind, data: events.append((kind, data)))
    current.enable_lifecycle()
    current.connect()
    current.read()
    first_id = current.runtime.sessions.battle_id
    lifecycle = collector.read_lifecycle
    collector.read_lifecycle = lambda: dict(lifecycle(), battle=None)
    replace_file = os.replace

    def corrupt_commit(source, target):
        replace_file(source, target)
        target.write_text('{', encoding='utf-8')

    with patch('battle_history.os.replace', side_effect=corrupt_commit):
        with pytest.raises(CaptureRetry):
            current.read()
    corrupt_path = history.path_for(first_id)
    assert history.get(first_id).diagnostics
    collector.read_lifecycle = lifecycle
    current.read()
    second_id = current.runtime.sessions.battle_id
    assert second_id != first_id
    collector.read_lifecycle = lambda: dict(lifecycle(), battle=None)
    events.clear()
    with pytest.raises(CaptureRetry):
        current.read()
    assert history.get(second_id).records
    assert corrupt_path.read_bytes() == b'{'
    assert current.history_error
    errors = [data for kind, data in events if kind == 'history_error']
    assert len(errors) == 1 and errors[0]['battle_id'] == first_id
    assert any(kind == 'history_saved' and data['battle_id'] == second_id
               for kind, data in events)
    current.close()
    # Once the corrupt file is explicitly removed, the retained handoff recovers.
    corrupt_path.unlink()
    replacement = CollectionSession('adb', 'serial', collector=Collector([]),
                                    frida_remote=None, history=history)
    replacement.enable_lifecycle(current.runtime)
    replacement.connect()
    replacement.read()
    replacement.close()
    assert len(history.list_recent().records) == 2
    assert replacement.history_error is None


def test_missing_directory_is_empty_but_unreadable_directory_is_diagnosed(tmp_path):
    history = BattleHistory(tmp_path / 'missing')
    assert not history.list_recent().records and not history.list_recent().diagnostics
    history.directory.write_text('not a directory', encoding='utf-8')
    assert history.list_recent().diagnostics


@pytest.mark.parametrize('legacy', [True, False])
def test_journal_replay_preserves_old_evidence_and_verifies_new_intervals(tmp_path, legacy):
    from dataclasses import asdict
    from capture_cli import json_value
    from capture_battles import replay
    collector, runtime = FakeCollector(), BattleRuntime()
    records = [dict(kind='lifecycle_header', data=dict(feed=runtime.feed.id))]
    runtime.record = lambda observation: records.append(
        dict(kind='lifecycle_observation', data=asdict(observation)))
    runtime.poll(collector)
    collector.lifecycle['battle'] = None
    runtime.poll(collector)
    value, = runtime.sessions.pop_archives()
    if legacy:
        value.pop('snapshot_intervals')
    records.append(dict(kind='archive_ready', data=value))
    records.append(dict(kind='lifecycle_summary', data=runtime.sessions.live()))
    path = tmp_path / 'capture.jsonl'
    def write():
        path.write_text('\n'.join(json.dumps(r, default=json_value) for r in records), encoding='utf-8')
    write()
    assert replay(path)['archives'] == 1
    if not legacy:
        value['snapshot_intervals'] = []
        write()
        with pytest.raises(ValueError, match='archive differs'):
            replay(path)


def test_reads_are_independent_and_duplicate_json_keys_are_diagnosed(tmp_path):
    history = BattleHistory(tmp_path)
    original = record()
    history.save(original)
    value = history.get(original.battle_id).records[0].statistics
    value['rows']['a']['damage'] = -1
    assert history.get(original.battle_id).records[0].statistics == original.statistics
    path = history.path_for(original.battle_id)
    text = path.read_text(encoding='utf-8')
    path.write_text('{"schema_version":1,' + text[1:], encoding='utf-8')
    assert history.list_recent().diagnostics


def test_retention_uses_observation_time_not_write_or_filename_order(tmp_path):
    history = BattleHistory(tmp_path)
    for index in range(100, 0, -1):
        history.save(record(index))
    history.save(record(0))
    assert not history.get('battle:0').records
    assert len(history.list_recent().records) == 100


def test_directory_permission_failure_is_diagnosed_and_no_files_lost(tmp_path):
    history = BattleHistory(tmp_path)
    history.save(record())
    with patch('battle_history.Path.iterdir', side_effect=PermissionError('denied')):
        assert history.list_recent().diagnostics[0].code == 'read_failed'
    assert history.get('battle:0').records


def test_committed_record_readback_failure_does_not_prune_and_retry_recovers(tmp_path):
    history = BattleHistory(tmp_path)
    for index in range(100):
        history.save(record(index))
    candidate = record(100)
    original = history._read
    def read(path):
        if path == history.path_for(candidate.battle_id):
            raise OSError('readback failed')
        return original(path)
    with patch.object(history, '_read', side_effect=read):
        with pytest.raises(HistoryError, match='readback failed'):
            history.save(candidate)
    assert len(list(tmp_path.glob('*.json'))) == 101
    assert history.get('battle:0').records
    assert not history.save(candidate).written
    assert len(list(tmp_path.glob('*.json'))) == 100
