import json
import os
from unittest.mock import patch

import pytest

from battle_history import BattleHistory, BattleRecord
from battle_share import (json_bytes, record_labels, record_statistics, record_view,
                          write_export)
from panel_model import PanelConfig, Category
from test_battle_history import record


def exported_record(payload):
    """Split the export into its display-only block and the versioned record."""
    data = json.loads(payload)
    return data.pop('display'), data


def test_export_is_exact_versioned_record_with_unknown_zero_precision_and_coverage():
    data = record(practice=None, outcome='unknown').to_dict()
    row = data['statistics']['rows']['a']
    row.update(damage=230.1234567890123, healing=0., shield_absorbed=None,
               shield_coverage_start=10., event_coverage_end=11.,
               event_coverage_complete=False, sources=['a', 'token_a'])
    data['event_error'] = 'sequence gap'
    original = BattleRecord.from_dict(data)
    display, exported = exported_record(json_bytes(original))
    assert exported == data
    assert display['stage']['stage_name'] == record_labels(original)[0]
    assert original.to_dict() == data
    assert '结果未知' in record_labels(original)
    view = record_view(original, PanelConfig(category=Category.SHIELD))
    assert not view.entries
    assert not view.share_known


@pytest.mark.parametrize('practice', [True, False, None])
@pytest.mark.parametrize('outcome', ['victory', 'defeat', 'abandoned', 'unknown'])
def test_all_metadata_states_remain_browsable(practice, outcome):
    original = record(practice=practice, outcome=outcome)
    assert record_view(original, PanelConfig()).entries[0].value == 230.
    if practice is None:
        assert '演习状态未知' in record_labels(original)


def test_export_exclusive_then_confirmed_replacement(tmp_path):
    history = tmp_path / 'history'
    target = tmp_path / 'share.json'
    write_export(target, b'first', history_directory=history)
    with pytest.raises(FileExistsError):
        write_export(target, b'second', history_directory=history)
    assert target.read_bytes() == b'first'
    write_export(target, b'second', history_directory=history, overwrite=True)
    assert target.read_bytes() == b'second'
    assert not list(tmp_path.glob('.arkdps-share-*'))


@pytest.mark.parametrize('operation', ['fsync', 'replace', 'publish'])
def test_atomic_failures_preserve_existing_export_and_archive(tmp_path, operation):
    history = BattleHistory(tmp_path / 'history')
    original = record()
    history.save(original)
    archived = history.path_for(original.battle_id).read_bytes()
    target = tmp_path / 'share.json'
    target.write_bytes(b'previous complete file')
    api = ('rename' if os.name == 'nt' else 'link') if operation == 'publish' else operation
    with patch('battle_share.os.' + api, side_effect=OSError('disk unavailable')):
        with pytest.raises(OSError):
            write_export(target, json_bytes(original), history_directory=history.directory,
                         overwrite=operation != 'publish')
    assert target.read_bytes() == b'previous complete file'
    assert history.path_for(original.battle_id).read_bytes() == archived
    assert not list(tmp_path.glob('.arkdps-share-*'))


def test_archive_directory_and_ancestor_aliases_protected(tmp_path):
    history = BattleHistory(tmp_path / 'history')
    original = record()
    history.save(original)
    archived = history.path_for(original.battle_id)
    before = archived.read_bytes()
    for target in (archived, history.directory / 'other.png',
                   history.directory / 'nested' / '..' / archived.name):
        with pytest.raises(ValueError, match='历史目录'):
            write_export(target, b'bad', history_directory=history.directory, overwrite=True)
    assert archived.read_bytes() == before


def test_export_to_missing_or_non_directory_parent_preserves_record(tmp_path):
    original = record()
    target = tmp_path / 'missing' / 'result.json'
    with pytest.raises(OSError):
        write_export(target, json_bytes(original), history_directory=tmp_path / 'history')
    assert not target.exists()
    display, exported = exported_record(json_bytes(original))
    assert exported == original.to_dict()
    assert display['stage']['stage_id'] == original.metadata['stage_id']


def test_historical_shares_use_frozen_coverage_not_optional_compatibility_flag():
    data = record().to_dict()
    assert 'statistics_complete' not in data['last_sample']
    for key in ('unknown_sources', 'unmapped_tokens', 'ambiguous_sources', 'mapping_conflicts', 'missing_fields'):
        data['statistics'][key] = []
    data['statistics']['rows']['a']['counter_snapshot_complete'] = True
    view = record_view(BattleRecord.from_dict(data), PanelConfig())
    assert view.share_known
    assert view.entries[0].share == 1.
    data['statistics']['unknown_sources'] = ['unmapped']
    assert not record_view(BattleRecord.from_dict(data), PanelConfig()).share_known


def _complete_record():
    data = record().to_dict()
    stats = data['statistics']
    for key in ('unknown_sources', 'unmapped_tokens', 'ambiguous_sources',
                'mapping_conflicts', 'missing_fields'):
        stats[key] = []
    stats['rows']['a']['counter_snapshot_complete'] = True
    return data


def test_classified_non_team_source_is_named_without_blocking_shares():
    data = _complete_record()
    stats = data['statistics']
    stats['non_team_sources'] = ['trap_321_shnbox']
    stats['non_team_output'] = {'trap_321_shnbox': {'damage': 0., 'healing': 0.}}
    stats['totals_complete'] = True
    view = record_view(BattleRecord.from_dict(data), PanelConfig())
    assert view.share_known and view.entries[0].share == 1.
    assert 'trap_321_shnbox' in view.share_note
    assert '已排除非队伍来源' in view.share_note


def test_real_unknown_source_still_blocks_shares_and_is_named():
    data = _complete_record()
    stats = data['statistics']
    stats['totals_complete'] = True
    stats['unknown_sources'] = ['char_mystery']
    view = record_view(BattleRecord.from_dict(data), PanelConfig())
    assert not view.share_known
    assert 'char_mystery' in view.share_reason
    assert '未归属统计来源' in view.share_reason


def test_legacy_record_is_recomputed_from_finished_native_evidence():
    data = _complete_record()
    stats = data['statistics']
    stats.pop('totals_complete')
    stats.pop('non_team_sources')
    stats.pop('non_team_output')
    stats['unknown_sources'] = ['trap_321_shnbox']
    for row in stats['rows'].values():
        row['counter_snapshot_complete'] = False
    data['last_sample']['state'] = 3
    data['last_sample']['guard'] = {'complete': True, 'start': 10, 'end': 10}
    normalized = record_statistics(BattleRecord.from_dict(data))
    assert normalized['totals_complete'] is True
    assert normalized['unknown_sources'] == []
    assert normalized['non_team_sources'] == ['trap_321_shnbox']
    view = record_view(BattleRecord.from_dict(data), PanelConfig())
    assert view.share_known
    assert 'trap_321_shnbox' in view.share_note


def test_legacy_record_without_finished_evidence_stays_incomplete():
    data = _complete_record()
    stats = data['statistics']
    stats.pop('totals_complete')
    stats.pop('non_team_sources')
    stats.pop('non_team_output')
    for row in stats['rows'].values():
        row['counter_snapshot_complete'] = False
    data['last_sample']['state'] = 2
    data['last_sample']['guard'] = {'complete': True, 'start': 10, 'end': 10}
    view = record_view(BattleRecord.from_dict(data), PanelConfig())
    assert not view.share_known
    assert '累计快照不完整' in view.share_reason
