"""Two shared producer builds certify only the actual nested HP components."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope='module')
def producer():
    fixture = Path(__file__).with_name('event_probe_nested_elemental_chain_fixture.js')
    completed = subprocess.run(['node', str(fixture)], capture_output=True, encoding='utf-8',
                               timeout=30, check=True)
    return json.loads(completed.stdout)


def parent_record(report):
    parent = report['expected']['parent']
    result, = [event for event in report['batch']['events'] if
               event['kind'] == 'damage_settlement' and event['damage_type'] == parent['damage_type']
               and event['source'] == parent['source']]
    return result


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_elemental_children_and_parent_each_settle_their_own_measured_hp(producer, platform):
    for report in producer[platform]['valid']:
        origin = report['provenance']
        if origin is not None:
            assert origin['raw_parent_modifier_real_delta_available'] is False
            assert {23510: 166, 23647: 198, 25424: 498}[report['recorded']] == origin['original_capture_line']
        parent = parent_record(report)
        expected = report['expected']['parent']
        records = report['batch']['events']
        assert parent['settlement_complete'] is True, report['name']
        assert parent['killed'] is (expected['hp_after'] == 0)
        assert parent['source_character_uid'] == expected['source_character_uid']
        assert parent['amount'] == pytest.approx(expected['amount'], abs=1e-8)
        assert parent['nominal_amount'] == pytest.approx(expected['nominal_amount'], abs=1e-8)
        proof = parent['proof'].get('nested_elemental_chain', parent['proof'].get('nested_settlement'))
        assert proof is not None
        assert proof['parent_hp_before'] == pytest.approx(expected['hp_before'], abs=1e-8)
        assert proof['parent_hp_after'] == pytest.approx(expected['hp_after'], abs=1e-8)
        assert proof['parent_real_delta'] == pytest.approx(-expected['amount'], abs=1e-8)
        children = [event for event in records if event['kind'] == 'damage_settlement' and event['damage_type'] == 5]
        assert len(children) == len(report['expected']['children'])
        for child, wanted in zip(children, report['expected']['children']):
            assert child['settlement_complete'] is True
            assert child['target_uid'] == parent['target_uid']
            assert child['target_instance'] == parent['target_instance']
            assert child['source'] == wanted['source']
            assert child.get('source_character_uid') == wanted['source_character_uid']
            assert child['amount'] == pytest.approx(wanted['amount'], abs=1e-8)
            assert child['killed'] is (wanted['hp_after'] == 0)
            assert child['sequence'] < parent['sequence']
        if len(children) == 1 and report['order'] == 'parent_before_children':
            assert set(proof) == {'target_uid', 'parent_real_delta', 'parent_hp_before', 'parent_hp_after',
                                  'child_hp_before', 'child_hp_after', 'child_real_delta'}
        else:
            assert proof['version'] == 1 and proof['ordering'] == report['order']
            assert [step['settlement_sequence'] for step in proof['children']] == [
                child['sequence'] for child in children]
            entries = {event['sequence']: event for event in records}
            for step, child in zip(proof['children'], children):
                burst = entries[step['burst_sequence']]
                assert child['sequence'] < burst['sequence'] < parent['sequence']
                assert burst['kind'] == 'elemental_burst' and burst['source'] == child['source']
                assert burst['hp_before'] == step['hp_before'] and burst['hp_after'] == step['hp_after']
        assert report['mark']['enemy_damage_unknown'] is False


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_inconsistent_or_unbounded_chain_never_supplies_parent_evidence(producer, platform):
    for fault, report in producer[platform]['invalid'].items():
        parent = parent_record(report)
        assert parent['settlement_complete'] is False, fault
        proof = parent['proof']
        assert 'nested_settlement' not in proof and 'nested_elemental_chain' not in proof, fault
        assert proof['settlement_unknown_reason'] == 'nested modifier changed target settlement scope', fault
        assert proof['nested_settlement_detail']['stage'] in ('child_entry', 'child_close', 'parent_close')
        assert 0 <= proof['nested_settlement_detail']['verified_children'] <= 16
