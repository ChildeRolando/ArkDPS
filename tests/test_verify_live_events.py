import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'verify_live_events', Path(__file__).resolve().parents[1] / 'acceptance' / 'verify_live_events.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def evidence(directory):
    source, owner = 'token_cathy', 'char_cathy'
    row = {'sources': [source], 'observed_damage': 0, 'observed_healing': 0,
           'shield_absorbed': 2, 'counter_snapshot_complete': False}
    first = {'session': 'battle', 'frame': 1, 'game_time': 0, 'rows': [], 'event_status': 'recording',
             'event_watermark': {'sequence': 0}}
    last = {'session': 'battle', 'frame': 2, 'game_time': 1, 'event_status': 'recording',
            'event_watermark': {'sequence': 2}, 'statistics': {'rows': {owner: row}}, 'rows': []}
    events = [{'sequence': 1, 'frame': 2, 'game_time': 1, 'source': source,
               'kind': 'shield_absorbed', 'amount': 2,
               'proof': {'buff': 'catsld_t_1[core]', 'before': 2, 'after': 0, 'incoming': 5}},
              {'sequence': 2, 'frame': 2, 'game_time': 1, 'source': source,
               'kind': 'shield_absorbed', 'amount': 0,
               'proof': {'buff': 'catsld_t_1[core]', 'before': 0, 'after': 0, 'incoming': 5}}]
    records = [{'kind': 'published', 'data': first},
               {'kind': 'drain', 'data': {'events': events}},
               {'kind': 'published', 'data': last}]
    (directory / 'summary.json').write_text(json.dumps({'error': None, 'head': 'candidate'}))
    path = directory / 'capture.jsonl'
    path.write_text('\n'.join(json.dumps(record) for record in records))
    return records, path


def test_product_event_proofs_reconcile_partial_zero_and_summon_owner(tmp_path):
    evidence(tmp_path)
    result = module.verify(tmp_path, source_owners=['token_cathy=char_cathy'],
                           require_partial=True, require_zero=True)
    assert result['events'] == 2 and result['shield_totals']['char_cathy'] == 2
    assert result['event_counts'] == {'shield_absorbed': 2}
    assert result['partial_break_sequences'] == [1] and result['zero_absorption_sequences'] == [2]


def test_missing_raw_event_fails_reconciliation(tmp_path):
    records, path = evidence(tmp_path)
    records[1]['data']['events'].pop()
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='missing'):
        module.verify(tmp_path)


def test_failed_run_can_only_be_reconciled_as_an_incomplete_prefix(tmp_path):
    evidence(tmp_path)
    (tmp_path / 'summary.json').write_text(json.dumps({'error': 'unstable clock', 'head': 'candidate'}))
    with pytest.raises(ValueError, match='failure'):
        module.verify(tmp_path)
    result = module.verify(tmp_path, allow_incomplete=True)
    assert result['events'] == 2 and result['complete'] is False
    assert result['capture_error'] == 'unstable clock'


def test_event_and_published_totals_cannot_hide_a_native_delta_mismatch(tmp_path):
    records, path = evidence(tmp_path)
    first, last = records[0]['data'], records[2]['data']
    first['rows'] = [{'id': 'token_cathy', 'damage': 100, 'healing': 0}]
    last['rows'] = [{'id': 'token_cathy', 'damage': 120, 'healing': 0}]
    records[1]['data']['events'][0].update(kind='damage', amount=19)
    row = last['statistics']['rows']['char_cathy']
    row.update(observed_damage=19, shield_absorbed=0)
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='amount mismatch'):
        module.verify(tmp_path)


def test_lifecycle_envelope_uses_native_sources_and_preserves_raw_proofs(tmp_path):
    records, path = evidence(tmp_path)
    first, last = records[0]['data'], records[2]['data']
    first['native_rows'] = [{'id': 'token_cathy', 'damage': 100, 'healing': 0}]
    last['native_rows'] = [{'id': 'token_cathy', 'damage': 100, 'healing': 0}]
    # GUI rows are an owner projection and cannot substitute for source totals.
    first['rows'] = last['rows'] = [{'id': 'char_cathy', 'damage': 999}]
    events = records[1]['data']
    records[1] = {'kind': 'lifecycle_observation', 'data': {
        'generation': 1, 'events': {'drain': events}}}
    path.write_text('\n'.join(json.dumps(record) for record in records))
    assert module.verify(tmp_path)['shield_totals']['char_cathy'] == 2
    last['native_rows'][0]['damage'] = 110
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='amount mismatch'):
        module.verify(tmp_path)
