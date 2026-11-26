"""Separate checked native deltas certify one ordinary child, never nominal sums."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT, bind_native_autochess_pair
from multiplayer_battle import MultiplayerBattleStatistics
from roster import CharacterEntry, RosterSnapshot
from test_event_probe_parity import BoundedObserver, CanonicalCollector

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def producer():
    result = subprocess.run(['node', str(ROOT / 'tests/event_probe_nested_ordinary_fixture.js')],
                            cwd=ROOT, encoding='utf-8', capture_output=True, timeout=30)
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


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('case', ['nonlethal', 'parent_kills', 'child_kills'])
def test_true_child_inside_physical_target_callback_preserves_both_hits(producer, platform, case):
    report = producer[platform]['child_before_parent'][case]
    child_kills = case == 'child_kills'
    child, parent = [event for event in report['batch']['events']
                     if event['kind'] == 'damage_settlement']
    expected = report['expected']
    child_hp_after, parent_amount = expected['childHpAfter'], expected['parentHpSpent']
    parent_hp_after = child_hp_after - parent_amount
    assert [child['damage_type'], parent['damage_type']] == [3, 1]
    assert [child['amount'], parent['amount']] == [expected['childAmount'], parent_amount]
    assert parent['nominal_amount'] == expected['parentAmount']
    assert all(event['source'] == report['parent_source'] for event in (child, parent))
    assert all(event['source_character_uid'] == report['parent_uid'] for event in (child, parent))
    assert all(event['settlement_complete'] is True for event in (child, parent))
    assert all(event['shield_absorbed'] == 0 for event in (child, parent))
    assert child['target_uid'] == parent['target_uid'] and child['sequence'] < parent['sequence']
    assert (child['hp_before'], child['hp_after'], child['killed']) == (1000, child_hp_after, child_kills)
    assert (parent['hp_before'], parent['hp_after'], parent['killed']) == (
        child_hp_after, parent_hp_after, parent_hp_after == 0)
    assert (parent.get('settled_after_nested_kill') is True) is child_kills
    proof = parent['proof']['nested_settlement']
    assert proof['ordering'] == ('child_before_parent_zero' if child_kills else 'child_before_parent')
    assert proof['parent_entry_hp_before'] == proof['child_hp_before'] == 1000
    assert proof['parent_hp_before'] == proof['child_hp_after'] == child_hp_after
    assert proof['parent_hp_after'] == parent_hp_after
    assert proof['parent_real_delta'] == -parent_amount
    assert proof['child_real_delta'] == -expected['childAmount']
    assert report['mark']['enemy_damage_unknown'] is False


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('fault', ['child_kills_parent_delta', 'missing_child_callback', 'invalid_child_delta',
                                    'child_starts_after_unexplained_loss', 'missing_parent_return',
                                    'missing_parent_delta', 'unclocked_shield'])
def test_in_progress_parent_requires_independent_child_and_parent_debits(producer, platform, fault):
    report = producer[platform]['child_before_parent'][fault]
    parent, = [event for event in report['batch']['events']
               if event['kind'] == 'damage_settlement' and event['damage_type'] == 1]
    assert parent['settlement_complete'] is False
    assert 'nested_settlement' not in parent['proof']
    assert not parent.get('settled_after_nested_kill')
    if fault == 'unclocked_shield':
        assert report['mark']['shield_unknown'] is True


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
@pytest.mark.parametrize('case', ['nonlethal', 'parent_kills', 'child_kills'])
def test_child_before_parent_hits_keep_their_types_through_production_capture(producer, platform, case):
    report = producer['android' if platform == 'mumu' else platform]['child_before_parent'][case]
    expected = report['expected']

    class Collector(CanonicalCollector):
        def read(self):
            sample = super().read()
            if self.frame:
                sample['rows'][0].update(physical=expected['parentAmount'], true=expected['childAmount'])
            return sample

    collector = Collector(platform)
    collector.cid, collector.uid = report['parent_source'], report['parent_uid']
    observer = BoundedObserver(collector, MUMU_CONTRACT if platform == 'mumu' else NATIVE_PC_CONTRACT,
                               report['batch'])
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time = report['mark']['frame'], report['mark']['game_time']
    collector.damage = expected['parentAmount'] + expected['childAmount']
    observer.records = deepcopy(report['batch']['events'])
    observer.sequence = report['mark']['sequence']

    result = capture.sample()
    row = result['rows'][collector.cid]
    lane = row['settled_damage']
    assert lane['complete'] is True and lane['observed_hits'] == 2
    assert lane['unverified_hits'] == 0
    effective = {'true': expected['childAmount']}
    if expected['parentHpSpent']:
        effective['physical'] = expected['parentHpSpent']
    assert lane['effective_damage_by_type'] == effective
    assert lane['observed_damage'] == expected['parentAmount'] + expected['childAmount']
    overkill = expected['parentAmount'] - expected['parentHpSpent']
    assert lane['overkill_damage'] == overkill
    assert lane['overkill_damage_by_type'] == ({'physical': overkill} if overkill else {})
    observer.records = deepcopy(report['batch']['events'])
    assert capture.sample()['rows'] == result['rows']


def test_logged_mlynar_hp_intervals_reach_native_autochess_ledger(producer):
    report = producer['autochess']['valid']
    child, parent = report['batch']['events']
    assert [event['damage_type'] for event in (child, parent)] == [3, 1]
    assert child['native_autochess_scope'] == parent['native_autochess_scope']
    assert parent['native_autochess_scope']['round'] == 11
    assert child['hp_before'] == pytest.approx(159760.09925796394, abs=1e-8)
    assert child['hp_after'] == parent['hp_before'] == pytest.approx(159414.07125438424, abs=1e-8)
    assert parent['hp_after'] == pytest.approx(156326.4257972762, abs=1e-8)

    meter = MultiplayerBattleStatistics()
    for mark, batch in ((report['initialMark'], report['initialBatch']), (report['mark'], report['batch'])):
        sample = dict(session='owned-mlynar:1', native_session='owned-native-mlynar:1',
                      frame=mark['frame'], game_time=mark['game_time'], global_total=0, global_heal=0, rows=[],
                      battle_metadata=dict(platform='native_pc', source_build='owned-mlynar-fixture',
                                           stage_id=mark['native_stage_id']))
        bind_native_autochess_pair(sample, mark, deepcopy(mark))
        result = meter.consume(sample, RosterSnapshot(sample['frame']), mark['sequence'], batch, mark=mark)

    player = next(player for player in result['players'] if player['player_uid'] == '1')
    actor, = [actor for actor in player['actor_ledgers'] if actor['cid'] == report['parent_source']]
    assert actor['source_character_uid'] == report['parent_uid']
    assert actor['ledger']['observed_hits'] == 2
    assert actor['ledger']['unverified_hits'] == 0
    assert actor['ledger']['effective_damage_by_type'] == pytest.approx({
        'physical': 3087.6454571080394, 'true': 346.0280035797041}, abs=1e-8)
    assert actor['ledger']['observed_damage'] == pytest.approx(3433.6734606877435, abs=1e-8)


def test_native_autochess_does_not_certify_parent_across_child_scope_change(producer):
    child, parent = producer['autochess']['wrong_scope']['batch']['events']
    assert child['native_autochess_scope']['scope_key'] != parent['native_autochess_scope']['scope_key']
    assert child['native_autochess_scope']['phase'] == 5
    assert parent['native_autochess_scope']['phase'] == 4
    assert child['settlement_complete'] is True
    assert parent['settlement_complete'] is False
    assert 'nested_settlement' not in parent['proof']


def test_native_autochess_does_not_move_open_parent_into_child_scope(producer):
    report = producer['autochess']['phase_changes_before_child']
    child, parent = report['batch']['events']
    opened = report['initialMark']['native_autochess_context']
    assert opened['phase'] == 4
    assert child['native_autochess_scope'] == parent['native_autochess_scope']
    assert child['native_autochess_scope']['phase'] == 5
    assert child['native_autochess_scope']['scope_key'] != opened['scope_key']
    assert child['settlement_complete'] is True
    assert parent['settlement_complete'] is False
    assert 'nested_settlement' not in parent['proof']
