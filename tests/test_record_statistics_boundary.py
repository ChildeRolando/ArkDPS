"""Archive interpretation is cached by BattleRecord; raw presence never changes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from threading import Barrier
from unittest.mock import patch
import pytest
from battle_history import BattleHistory, BattleRecord, HistoryError
from battle_share import record_statistics, json_bytes
from test_battle_history import record
from test_battle_share import _complete_record


def legacy_payload():
    data = _complete_record()
    for key in ('totals_complete', 'non_team_sources', 'non_team_output'):
        data['statistics'].pop(key, None)
    data['statistics']['unknown_sources'] = ['trap_321_shnbox']
    for row in data['statistics']['rows'].values():
        row['counter_snapshot_complete'] = False
        for key in ('overkill_damage', 'overkill_coverage_complete', 'burst_count', 'presence_seconds'):
            row.pop(key, None)
    data['policies'].pop('overkill', None)
    data['policies'].pop('overkill_by_type', None)
    data['last_sample'].update(state=3, guard=dict(complete=True, start=10, end=10))
    data['future_record'] = {'opaque': [None, 0, False, {'keep': '原字段'}]}
    data['statistics']['future_statistics'] = {'values': [0, None]}
    return data


@pytest.mark.parametrize('kind', ['missing', 'null', 'zero'])
def test_missing_null_and_zero_keep_raw_presence_while_business_reads_are_validated(kind):
    raw = legacy_payload()
    row = raw['statistics']['rows']['a']
    if kind != 'missing': row['overkill_damage'] = None if kind == 'null' else 0
    expected = deepcopy(raw)
    value = BattleRecord.from_dict(raw)
    raw['future_record']['opaque'].append('caller mutation')
    normalized = value.statistics
    assert normalized['rows']['a']['overkill_damage'] == (0 if kind == 'zero' else None)
    assert normalized['rows']['a']['overkill_coverage_complete'] is False
    assert normalized['rows']['a']['burst_count'] == 0
    assert value.to_dict() == expected
    assert ('overkill_damage' in value.to_dict()['statistics']['rows']['a']) == (kind != 'missing')
    assert 'burst_count' not in value.to_dict()['statistics']['rows']['a']
    assert value.to_dict()['policies'] == expected['policies']


def test_one_record_interprets_once_and_every_public_view_is_independent():
    raw = legacy_payload(); value = BattleRecord.from_dict(raw)
    with patch.object(value, '_normalize_statistics', wraps=value._normalize_statistics) as normalize:
        full = record_statistics(value)
        panel = value.presentation_statistics()
        field = value.statistics_fields(('rows', 'totals_complete', 'future_statistics'))
        assert value.statistics['totals_complete'] is True
        assert record_statistics(value, for_panel=True)['totals_complete'] is True
        assert normalize.call_count == 1
    assert full['unknown_sources'] == [] and full['non_team_sources'] == ['trap_321_shnbox']
    full['rows']['a']['damage_by_type']['physical'] = -1
    panel['rows']['a']['sources'].append('foreign')
    field['future_statistics']['values'].append(99)
    assert value.to_dict() == raw
    assert value.statistics['rows']['a']['damage_by_type']['physical'] >= 0
    assert 'foreign' not in value.presentation_statistics()['rows']['a']['sources']
    assert value.statistics['future_statistics']['values'] == [0, None]


def test_interpretation_is_single_flight_across_readers():
    value = BattleRecord.from_dict(legacy_payload())
    ready = Barrier(2)
    def read(panel):
        ready.wait(timeout=5)
        return value.normalized_statistics(for_panel=panel)
    with patch.object(value, '_normalize_statistics', wraps=value._normalize_statistics) as normalize:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(read, False); second = workers.submit(read, True)
            a, b = first.result(timeout=5), second.result(timeout=5)
        assert normalize.call_count == 1
    a['rows']['a']['damage'] = -1
    assert b['rows']['a']['damage'] == value.statistics['rows']['a']['damage']


def test_export_reimport_and_copy_preserve_original_unknown_fields_and_old_policies():
    raw = legacy_payload(); value = BattleRecord.from_dict(raw)
    value.statistics; value.presentation_statistics()
    exported = json.loads(json_bytes(value))
    exported.pop('display')
    assert exported == raw
    restored = BattleRecord.from_dict(exported)
    assert restored.to_dict() == raw
    assert restored.statistics == value.statistics
    detached = deepcopy(value)
    assert detached.to_dict() == raw and detached.statistics == value.statistics
    assert detached._statistics_lock is not value._statistics_lock


def test_raw_byte_sha_cache_keeps_field_presence_without_revalidation_or_shared_objects(tmp_path, monkeypatch):
    raw = legacy_payload(); history = BattleHistory(tmp_path)
    path = history.path_for(raw['battle_id'])
    original = json.dumps(raw, ensure_ascii=True, indent=1).encode('utf8')
    path.write_bytes(original)
    first = history.get(raw['battle_id']).records[0]
    assert first.presentation_statistics()['totals_complete'] is True
    def forbidden(*_args, **_kwargs): raise AssertionError('unchanged bytes revalidated')
    monkeypatch.setattr(BattleRecord, 'from_dict', forbidden)
    second = history.get(raw['battle_id']).records[0]
    assert second.to_dict() == raw and second.statistics == first.statistics
    assert second is not first and second._raw_data is not first._raw_data
    assert history._validated_reads[path][0] == hashlib.sha256(original).digest()
    assert path.read_bytes() == original
    first.statistics['future_statistics']['values'].append(999)
    assert second.statistics['future_statistics']['values'] == [0, None]


@pytest.mark.parametrize('mutation', ['bad_flag', 'nan', 'bad_policy'])
def test_validation_does_not_hide_invalid_present_fields_behind_defaults(mutation):
    raw = legacy_payload()
    if mutation == 'bad_flag': raw['statistics']['rows']['a']['presence_complete'] = 0
    elif mutation == 'nan': raw['future_record']['opaque'].append(float('nan'))
    else: raw['policies']['overkill'] = 'future-unsupported-formula'
    with pytest.raises(HistoryError): BattleRecord.from_dict(raw)


def test_insufficient_final_evidence_stays_unknown_in_both_views_and_raw():
    raw = legacy_payload(); raw['last_sample']['state'] = 2
    value = BattleRecord.from_dict(raw)
    assert value.statistics['totals_complete'] is False
    assert value.presentation_statistics()['totals_complete'] is False
    assert value.statistics['rows']['a']['counter_snapshot_complete'] is False
    assert value.to_dict() == raw


def test_unverified_native_character_scope_does_not_borrow_full_result_inference():
    from autochess_context import NATIVE_AUTOCHESS_CHARACTER_COUNTERS
    raw = legacy_payload()
    raw['metadata'].update(platform='native_pc', stage_id='ACTIVITY/AUTOCHESS/fixture')
    raw['last_sample']['native_counter_scope'] = NATIVE_AUTOCHESS_CHARACTER_COUNTERS
    value = BattleRecord.from_dict(raw)
    assert value.statistics['totals_complete'] is True  # Existing legacy finished evidence rule.
    panel = value.presentation_statistics()
    assert panel['totals_complete'] is False
    assert panel['rows']['a']['counter_snapshot_complete'] is False
    assert 'non_team_sources' not in panel and panel['unknown_sources'] == ['trap_321_shnbox']
    assert value.to_dict() == raw
