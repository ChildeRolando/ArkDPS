"""Retention avoids repeated validation without trusting timestamps or mutable DTOs."""
import json
import os

import pytest

import battle_history
from battle_history import BattleHistory, BattleRecord
from test_battle_history import record


def test_identical_history_bytes_reuse_validation_and_readers_are_independent(tmp_path, monkeypatch):
    history = BattleHistory(tmp_path)
    expected = record(1)
    history.save(expected)
    first = history.get(expected.battle_id).records[0]
    first._data['statistics']['rows']['a']['damage'] = 999999
    def no_revalidation(*args):
        raise AssertionError('unchanged validated file was validated again')
    monkeypatch.setattr(BattleRecord, 'from_dict', no_revalidation)
    assert history.get(expected.battle_id).records[0].to_dict() == expected.to_dict()
    assert history.list_recent().records[0].to_dict() == expected.to_dict()


@pytest.mark.parametrize('valid', [True, False])
def test_same_length_same_mtime_changed_bytes_always_revalidate(tmp_path, valid):
    history = BattleHistory(tmp_path)
    expected = record(1, stage_id='stageA')
    history.save(expected)
    path = history.path_for(expected.battle_id)
    old_stat, before = path.stat(), path.read_bytes()
    after = before.replace(b'stageA', b'stageB') if valid else before.replace(b'"generation":1', b'"generation":0')
    assert after != before and len(after) == len(before)
    path.write_bytes(after)
    os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    result = history.get(expected.battle_id)
    if valid:
        assert result.records[0].metadata['stage_id'] == 'stageB'
    else:
        assert not result.records and result.diagnostics[0].code == 'invalid_record'
    assert path.read_bytes() == after


@pytest.mark.parametrize('bad_bytes', [b'{', b'\xff', b'\xef\xbb\xbf{}', b'{"x":1,"x":2}'])
def test_changed_malformed_file_cannot_fall_back_to_cached_record(tmp_path, bad_bytes):
    history = BattleHistory(tmp_path)
    value = record(1)
    history.save(value)
    history.path_for(value.battle_id).write_bytes(bad_bytes)
    result = history.list_recent()
    assert not result.records and result.diagnostics[0].code == 'invalid_record'
    assert history.path_for(value.battle_id).read_bytes() == bad_bytes


def test_validation_cache_is_bound_to_filename_and_detects_repaired_files(tmp_path):
    history = BattleHistory(tmp_path)
    value = record(1)
    history.save(value)
    data = history.path_for(value.battle_id).read_bytes()
    wrong_path = history.path_for('other-id')
    wrong_path.write_bytes(data)
    result = history.list_recent()
    assert len(result.records) == 1 and len(result.diagnostics) == 1
    wrong_path.unlink()
    path = history.path_for(value.battle_id)
    path.write_bytes(b'{')
    assert not history.get(value.battle_id).records
    path.write_bytes(data)
    assert history.get(value.battle_id).records[0].to_dict() == value.to_dict()


def test_cache_preserves_readable_legacy_escaped_unicode_without_encoding_loss(tmp_path):
    history = BattleHistory(tmp_path)
    value = record(1).to_dict()
    value['metadata']['evidence'] = 'legacy-\ud800'
    history.path_for(value['battle_id']).write_text(json.dumps(value), encoding='utf-8')
    assert history.get(value['battle_id']).records[0].to_dict() == value
    assert history.get(value['battle_id']).records[0].to_dict() == value


def test_validation_cache_evicts_under_entry_and_byte_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(battle_history, 'VALIDATED_HISTORY_CACHE_LIMIT', 2)
    monkeypatch.setattr(battle_history, 'VALIDATED_HISTORY_CACHE_BYTES', 100000)
    history = BattleHistory(tmp_path)
    for index in range(3):
        value = record(index)
        history.path_for(value.battle_id).write_text(json.dumps(value.to_dict()), encoding='utf-8')
        assert history.get(value.battle_id).records
    assert len(history._validated_reads) == 2
    monkeypatch.setattr(battle_history, 'VALIDATED_HISTORY_CACHE_BYTES', 1)
    uncached = BattleHistory(tmp_path)
    assert uncached.get('battle:0').records
    assert not uncached._validated_reads and uncached._validated_read_bytes == 0
