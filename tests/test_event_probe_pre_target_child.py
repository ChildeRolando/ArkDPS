"""A completed prior child does not belong to its parent's later HP interval."""
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope='module')
def reports():
    fixture = Path(__file__).with_name('event_probe_pre_target_child_fixture.js')
    result = subprocess.run(['node', str(fixture)], encoding='utf-8', capture_output=True,
                            check=True, timeout=30)
    return json.loads(result.stdout)


def settlements(report):
    return [event for event in report['batch']['events'] if event['kind'] == 'damage_settlement'
            and event['source'] == report['parent_source']]


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_complete_prior_child_and_later_lethal_parent_settle_separately(reports, platform):
    for report in reports[platform]['valid']:
        child, parent = settlements(report)
        expected = report['expected']
        assert child['settlement_complete'] is True and child['killed'] is False
        assert child['amount'] == pytest.approx(expected['child_amount'])
        assert parent['settlement_complete'] is True and parent['killed'] is True
        assert parent['amount'] == pytest.approx(expected['amount'])
        assert parent['nominal_amount'] == pytest.approx(expected['nominal'])
        assert parent['proof']['evidence_method'] == 'Unit.OnTakeDamage+Entity.ApplyModifier'
        assert 'nested_settlement' not in parent['proof'] and 'nested_elemental_chain' not in parent['proof']
        assert child['target_uid'] == parent['target_uid']
        assert child['sequence'] < parent['sequence']
        assert report['mark']['enemy_damage_unknown'] is False


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_other_target_activity_does_not_block_the_parent_hp_interval(reports, platform):
    report = reports[platform]['different_target_parallel']
    parent, = [event for event in settlements(report) if event.get('killed') is True]
    assert parent['settlement_complete'] is True
    assert parent['amount'] == pytest.approx(report['expected']['amount'])


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_active_child_or_missing_parent_evidence_still_prevents_certification(reports, platform):
    for fault, report in reports[platform]['invalid'].items():
        amount = report['expected']['amount']
        assert not any(event.get('settlement_complete') is True and event['amount'] == pytest.approx(amount)
                       for event in settlements(report)), fault
        assert report['mark']['active'] == 0, fault
