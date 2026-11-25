"""Run the raw acceptance verifier on shared producer callback fixtures."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from acceptance.verify_live_events import pc_mechanism_evidence


@pytest.fixture(scope='module')
def producer():
    fixture = Path(__file__).with_name('event_probe_nested_elemental_chain_fixture.js')
    data = subprocess.run(['node', str(fixture)], check=True, capture_output=True,
                          encoding='utf-8', timeout=30)
    return json.loads(data.stdout)


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_every_owned_chain_and_sourced_single_passes_the_raw_mechanism_gate(producer, platform):
    for report in producer[platform]['valid']:
        events = report['batch']['events']
        original = deepcopy(events)
        result = pc_mechanism_evidence(events)['nested']
        assert result['passed'] is True, report['name']
        witness, = result['positive_witnesses']
        parent = next(event for event in events if event['sequence'] == witness['parent_sequence'])
        chain = parent['proof'].get('nested_elemental_chain')
        if chain is None:
            assert witness['ordering'] == 'parent_before_child'
            child = next(event for event in events if event['sequence'] == witness['child_sequence'])
            assert child['source'] and child['source_character_uid'] > 0 and child['hp_after'] > 0
        else:
            assert witness['ordering'] == report['order']
            assert witness['children'] == [
                {key: step[key] for key in ('settlement_sequence', 'burst_sequence')}
                for step in chain['children']]
        assert events == original


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_unverified_producer_inputs_do_not_count_as_nested_witnesses(producer, platform):
    for name, report in producer[platform]['invalid'].items():
        result = pc_mechanism_evidence(report['batch']['events'])['nested']
        assert result['passed'] is False and result['positive_witnesses'] == [], name


@pytest.mark.parametrize('fault', [
    'missing_burst', 'missing_settlement', 'missing_parent', 'missing_parent_settlement',
    'duplicate_reference_sequence', 'wrong_reference', 'references_parent', 'wrong_order',
    'child_source', 'child_uid', 'child_target_uid', 'child_target_instance',
    'child_frame', 'child_scope', 'missing_scope', 'child_hp', 'child_real', 'child_incomplete',
    'child_shield', 'parent_nominal', 'parent_actual', 'parent_scope', 'parent_proof_differs',
    'bool_reference', 'bool_version', 'too_many_children',
])
def test_chain_gate_rejects_wrong_loose_or_mutated_raw_references(producer, fault):
    report = producer['native_pc']['valid'][0]
    events = deepcopy(report['batch']['events'])
    parent = next(event for event in events if event['kind'] == 'damage'
                  and 'nested_elemental_chain' in event['proof'])
    parent_settlement = next(event for event in events if event['kind'] == 'damage_settlement'
                             and 'nested_elemental_chain' in event['proof'])
    chain = parent['proof']['nested_elemental_chain']
    last = chain['children'][-1]
    settlement = next(event for event in events if event['sequence'] == last['settlement_sequence'])
    burst = next(event for event in events if event['sequence'] == last['burst_sequence'])
    edit_chain = True
    if fault == 'missing_burst':
        events.remove(burst)
    elif fault == 'missing_settlement':
        events.remove(settlement)
    elif fault == 'missing_parent':
        events.remove(parent)
    elif fault == 'missing_parent_settlement':
        events.remove(parent_settlement)
    elif fault == 'duplicate_reference_sequence':
        events.append(deepcopy(burst))
    elif fault == 'wrong_reference':
        last['settlement_sequence'] -= 1
    elif fault == 'references_parent':
        last['burst_sequence'] = parent['sequence']
    elif fault == 'wrong_order':
        chain['ordering'] = 'children_before_parent'
    elif fault == 'child_source':
        settlement['source'] = burst['source'] = 'char_other'
    elif fault == 'child_uid':
        settlement['source_character_uid'] = burst['source_character_uid'] = 123
    elif fault == 'child_target_uid':
        settlement['target_uid'] = '999'
    elif fault == 'child_target_instance':
        settlement['target_instance'] = burst['target_instance'] = '0x4000'
    elif fault == 'child_frame':
        settlement['frame'] = burst['frame'] = parent['frame'] + 1
    elif fault == 'child_scope':
        settlement['native_autochess_scope']['round'] = burst['native_autochess_scope']['round'] = 99
    elif fault == 'missing_scope':
        burst.pop('native_autochess_scope')
    elif fault == 'child_hp':
        burst['hp_after'] += 1
    elif fault == 'child_real':
        burst['real_delta'] += 1
        settlement['amount'] = -burst['real_delta']
    elif fault == 'child_incomplete':
        settlement['settlement_complete'] = False
    elif fault == 'child_shield':
        settlement['shield_absorbed'] = burst['shield_absorbed'] = 1
    elif fault == 'parent_nominal':
        parent_settlement['nominal_amount'] += 1
    elif fault == 'parent_actual':
        parent_settlement['amount'] += 1
    elif fault == 'parent_scope':
        parent_settlement['native_autochess_scope']['scope_key'] = 'other'
    elif fault == 'parent_proof_differs':
        parent_settlement['proof']['nested_elemental_chain']['children'][-1]['hp_after'] += 1
        edit_chain = False
    elif fault == 'bool_reference':
        last['settlement_sequence'] = True
    elif fault == 'bool_version':
        chain['version'] = True
    elif fault == 'too_many_children':
        chain['children'] *= 10
    if edit_chain:
        parent_settlement['proof']['nested_elemental_chain'] = deepcopy(chain)
    result = pc_mechanism_evidence(events)['nested']
    assert result['passed'] is False and result['positive_witnesses'] == []
    assert result['invalid_sequences']
