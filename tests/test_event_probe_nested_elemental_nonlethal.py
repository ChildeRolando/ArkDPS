"""Actual native scalar values in owned callbacks, never rewritten live evidence."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT
from roster import CharacterEntry, RosterSnapshot
from test_event_probe_parity import BoundedObserver


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ROOT / 'tests/fixtures/event_probe_business/20261124-native-elemental-nonlethal-fields.json'
FAULTS = ['parent_callback_not_returned', 'missing_parent_real', 'parent_hp_mismatch',
    'changed_parent_real', 'changed_parent_hp', 'changed_parent_source_uid', 'changed_parent_source_cid',
    'changed_parent_source_data_uid', 'changed_target_uid', 'changed_target_cid', 'changed_target_instance',
    'changed_parent_type', 'parent_cancelled', 'cross_frame', 'cross_time', 'cross_scope', 'second_child_hp_discontinuous',
    'child_delta_mismatch', 'child_hp_mismatch', 'child_cancelled', 'child_shield_unknown',
    'child_incomplete', 'child_settlement_amount', 'child_settlement_uid', 'child_settlement_instance',
    'child_settlement_clock', 'child_certificate_delta', 'changed_child_source', 'reused_modifier']


@pytest.fixture(scope='module')
def fields():
    assert sha256(FIELDS.read_bytes()).hexdigest() == '81838508235e7d9ec8934737c36dc2414d7bc4fad168ebd75d4c884d2dfe0836'
    return json.loads(FIELDS.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def producer(fields):
    result = subprocess.run(['node', 'tests/event_probe_nested_elemental_nonlethal_fixture.js'],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_original_live_partial_records_are_not_relabelled_or_repaired(fields):
    groups = fields['elemental_nonlethal_groups']
    assert len(groups) == 10
    assert [g['original_parent_sequence'] for g in groups] == [281, 314, 327, 335, 542, 673, 691, 822, 1217, 1732]
    for group in groups:
        old = group['original_incomplete_parent_settlement']['event']
        assert old['settlement_complete'] is False and old['amount'] == 0
        assert 'nested_settlement' not in old['proof']
        assert old['proof']['settlement_unknown_reason'] == 'nested modifier changed target settlement scope'
        assert group['raw_child_burst']['event']['killed'] is False
        assert group['raw_child_settlement']['event']['settlement_complete'] is True


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('index', range(10))
def test_ten_actual_nonlethal_elemental_hp_chains_have_one_shared_certificate(producer, fields, platform, index):
    report = producer[platform]['valid'][index]
    group = fields['elemental_nonlethal_groups'][index]
    a, b = group['raw_child_entry']['record'], group['raw_child_return']['record']
    events = report['batch']['events']
    parent = next(e for e in events if e['kind'] == 'damage' and e['source'] == a['parent_source'])
    parent_settlement = next(e for e in events if e['kind'] == 'damage_settlement' and e['source'] == a['parent_source'])
    child = next(e for e in events if e['kind'] == 'elemental_burst')
    child_settlement = next(e for e in events if e['kind'] == 'damage_settlement' and e['source'] is None)
    proof = parent['proof']['nested_settlement']
    assert proof == dict(target_uid=str(a['target_uid']), parent_real_delta=a['parent_real_delta'],
        parent_hp_before=a['parent_target_hit_hp_before'], parent_hp_after=a['target_hp'],
        child_hp_before=a['child_hp_before'], child_hp_after=b['target_hp'], child_real_delta=b['child_real_delta'])
    assert parent_settlement['proof']['nested_settlement'] == proof
    assert parent_settlement['settlement_complete'] is child_settlement['settlement_complete'] is True
    assert parent_settlement['amount'] == -a['parent_real_delta']
    assert child_settlement['amount'] == -b['child_real_delta']
    assert child['hp_before'] == a['child_hp_before'] and child['hp_after'] == b['target_hp'] > 0
    assert parent['hp_before'] == a['parent_target_hit_hp_before'] and parent['hp_after'] == a['target_hp']
    assert parent['real_delta'] == a['parent_real_delta'] and child['real_delta'] == b['child_real_delta']
    assert parent['killed'] is child['killed'] is False
    assert not any(e.get('settled_after_nested_kill') for e in events)
    assert child_settlement['target_uid'] == parent_settlement['target_uid'] == str(a['target_uid'])
    assert parent['frame'] == child['frame'] == a['frame']
    assert parent['game_time'] == child['game_time'] == a['game_time']
    assert parent['target_instance'] == child['target_instance'] == parent_settlement['target_instance']


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('fault', FAULTS)
def test_missing_mutated_or_ambiguous_facts_never_certify_elemental_parent(producer, platform, fault):
    records = producer[platform]['invalid'][fault]['batch']['events']
    assert not any((e.get('proof') or {}).get('nested_settlement') for e in records)
    assert not any((e.get('proof') or {}).get('nested_elemental_chain') for e in records)
    assert not any(e.get('settled_after_nested_kill') for e in records)


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
@pytest.mark.parametrize('index', range(10))
def test_checked_nonlethal_callbacks_enter_existing_stats_once_and_replay_idempotently(producer, platform, index):
    report = producer['android' if platform == 'mumu' else platform]['valid'][index]
    proof = next(e['proof']['nested_settlement'] for e in report['batch']['events'] if e.get('proof', {}).get('nested_settlement'))
    cid = 'char_4247_oxi'

    class Collector:
        frame = 0
        game_time = 0
        session = 'owned-type5-nonlethal'

        def read(self):
            return dict(session='owned-type5-nonlethal', native_session='owned-type5-nonlethal', state=2,
                frame=self.frame, game_time=self.game_time,
                battle_metadata=dict(platform=platform, source_build='synthetic-build'),
                rows=[dict(id=cid, damage=0 if self.frame == 0 else -proof['parent_real_delta'],
                           healing=0, physical=0, arts=0, true=0, elemental=0)])

        def read_roster(self):
            return RosterSnapshot(self.frame, (CharacterEntry(cid, 2147484057, False, False, None, None, 0),),
                                  frozenset((2147484057,)), True, 0)

    collector = Collector()
    observer = BoundedObserver(collector, MUMU_CONTRACT if platform == 'mumu' else NATIVE_PC_CONTRACT, report['batch'])
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time = report['mark']['frame'], report['mark']['game_time']
    observer.records = deepcopy(report['batch']['events'])
    observer.sequence = report['mark']['sequence']
    first = capture.sample()
    expected = -proof['parent_real_delta'] - proof['child_real_delta']
    lane = first['rows'][cid]['settled_damage']
    assert lane['complete'] is True and lane['unverified_hits'] == 0
    assert lane['observed_hits'] == 2 and lane['known_hp_damage'] == expected
    assert first['enemy_damage']['by_uid'][proof['target_uid']]['amount'] == expected
    observer.records = deepcopy(report['batch']['events'])
    assert capture.sample()['rows'] == first['rows']
