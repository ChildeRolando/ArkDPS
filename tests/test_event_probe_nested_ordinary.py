"""Separate checked native deltas certify one ordinary child, never nominal sums."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT
from roster import CharacterEntry, RosterSnapshot
from test_event_probe_parity import BoundedObserver

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def producer():
    result = subprocess.run(['node', str(ROOT / 'tests/event_probe_nested_ordinary_fixture.js')],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('case', ['valid', 'different_actor'])
def test_ordinary_nested_callbacks_keep_distinct_parent_and_child_hp(producer, platform, case):
    report = producer[platform][case]
    settlements = [event for event in report['batch']['events'] if event['kind'] == 'damage_settlement']
    assert len(settlements) == 2
    assert [event['amount'] for event in settlements] == [50, 200]
    assert all(event['settlement_complete'] is True for event in settlements)
    assert all(event['shield_absorbed'] == 0 for event in settlements)
    assert report['batch']['enemy_damage_unknown'] is False
    proof = settlements[-1]['proof']['nested_settlement']
    assert proof['parent_hp_before'] == 1000 and proof['parent_hp_after'] == 800
    assert proof['parent_real_delta'] == -200
    assert proof['child_hp_before'] == 800 and proof['child_hp_after'] == 750
    assert proof['child_real_delta'] == -50


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('fault', ['parent_callback_not_returned', 'missing_parent_real',
    'parent_hp_mismatch', 'missing_child_real', 'child_hp_mismatch', 'second_child',
    'changed_parent_real', 'changed_parent_hp', 'changed_source_uid', 'changed_target_uid',
    'changed_source_cid', 'changed_target_cid', 'changed_damage_type', 'cross_frame', 'cross_time',
    'changed_source_data_uid', 'unmatched_shield', 'missing_child_callback',
    'duplicate_child_callback', 'parent_cancelled', 'zero_target_uid', 'child_nominal_underflow'])
def test_incomplete_or_mutated_nested_proof_never_certifies_parent(producer, platform, fault):
    report = producer[platform]['invalid'][fault]
    settlements = [event for event in report['batch']['events'] if event['kind'] == 'damage_settlement']
    assert not any(event['amount'] == 200 and event.get('settlement_complete') is True for event in settlements)
    assert not any(event.get('proof', {}).get('nested_settlement') for event in settlements)


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
@pytest.mark.parametrize('case', ['valid', 'different_actor', 'terminal_child', 'terminal_other_actor'])
def test_checked_producer_passes_existing_capture_statistics_and_replay(producer, platform, case):
    report = producer['android' if platform == 'mumu' else platform][case]

    class Collector:
        frame = 0
        game_time = 0
        session = 'owned-ordinary-nested'

        def read(self):
            return dict(session=self.session, native_session=self.session, state=2,
                frame=self.frame, game_time=self.game_time,
                battle_metadata=dict(platform=platform, source_build='synthetic-build'),
                rows=[dict(id=cid, damage=0 if self.frame == 0 else amount, healing=0,
                           physical=0, arts=0 if self.frame == 0 else amount, true=0, elemental=0)
                      for cid, amount in amounts.items()])

        def read_roster(self):
            return RosterSnapshot(self.frame, tuple(CharacterEntry(cid, uid, False, False, None, None, 0)
                for cid, uid in identities.items()), frozenset(identities.values()), True, 0)

    same_actor = case in ('valid', 'terminal_child')
    terminal = case.startswith('terminal')
    child_nominal, child_hp = (1000, 800) if terminal else (50, 50)
    amounts = {report['parent_source']: 200 + child_nominal if same_actor else 200}
    hp_amounts = {report['parent_source']: 200 + child_hp if same_actor else 200}
    identities = {report['parent_source']: report['parent_uid']}
    if not same_actor:
        amounts[report['child_source']] = child_nominal
        hp_amounts[report['child_source']] = child_hp
        identities[report['child_source']] = report['child_uid']
    collector = Collector()
    observer = BoundedObserver(collector, MUMU_CONTRACT if platform == 'mumu' else NATIVE_PC_CONTRACT,
                               report['batch'])
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time = report['mark']['frame'], report['mark']['game_time']
    observer.records = deepcopy(report['batch']['events'])
    observer.sequence = report['mark']['sequence']
    first = capture.sample()
    for cid, amount in amounts.items():
        lane = first['rows'][cid]['settled_damage']
        assert lane['complete'] is True and lane['unverified_hits'] == 0
        assert lane['known_hp_damage'] == hp_amounts[cid]
        assert first['rows'][cid]['damage'] == amount
        assert lane['known_shield_damage'] == 0
        assert lane['overkill_damage'] == (amount - hp_amounts[cid])
        assert lane['observed_hits'] == (2 if same_actor else 1)
    observer.records = deepcopy(report['batch']['events'])
    replay = capture.sample()
    assert replay['rows'] == first['rows']


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('case', ['terminal_child', 'terminal_other_actor'])
def test_verified_ordinary_child_kill_preserves_independent_parent_debit(producer, platform, case):
    report = producer[platform][case]
    settlements = [event for event in report['batch']['events'] if event['kind'] == 'damage_settlement']
    assert len(settlements) == 2
    assert [event['amount'] for event in settlements] == [800, 200]
    assert [event['nominal_amount'] for event in settlements] == [1000, 200]
    assert all(event['settlement_complete'] is True for event in settlements)
    assert [event['killed'] for event in settlements] == [True, False]
    assert all(event['shield_absorbed'] == 0 for event in settlements)
    assert report['batch']['enemy_damage_unknown'] is False
    proof = settlements[-1]['proof']['nested_settlement']
    assert proof['parent_hp_before'] == 1000 and proof['parent_hp_after'] == 800
    assert proof['parent_real_delta'] == -200
    assert proof['child_hp_before'] == 800 and proof['child_hp_after'] == 0
    assert proof['child_real_delta'] == -800


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('fault', ['parent_callback_not_returned', 'missing_parent_real',
    'parent_hp_mismatch', 'missing_child_real', 'child_hp_mismatch', 'second_child',
    'changed_parent_real', 'changed_parent_hp', 'changed_source_uid', 'changed_target_uid',
    'changed_source_cid', 'changed_target_cid', 'changed_damage_type', 'cross_frame', 'cross_time',
    'changed_source_data_uid', 'unmatched_shield', 'missing_child_callback',
    'duplicate_child_callback', 'parent_cancelled', 'zero_target_uid', 'child_nominal_underflow'])
def test_terminal_child_cannot_repair_mutated_or_missing_parent_or_child_proof(producer, platform, fault):
    settlements = [event for event in producer[platform]['terminal_invalid'][fault]['batch']['events']
                   if event['kind'] == 'damage_settlement']
    assert not any(event['amount'] == 200 and event.get('settlement_complete') is True for event in settlements)
    assert not any(event.get('proof', {}).get('nested_settlement') for event in settlements)
