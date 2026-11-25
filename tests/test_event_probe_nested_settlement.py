"""Measured parent/child checkpoints must close HP without nominal backfill."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT
from roster import CharacterEntry, RosterSnapshot
from test_event_probe_parity import BoundedObserver

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/event_probe_business/2206-native-callback-fields.json'


@pytest.fixture(scope='module')
def producer():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == (
        'da6122420941983fa56037c5e4039a81ea8b8ca2e14c3d53e7f0bab2ec52912f')
    result = subprocess.run(['node', 'tests/event_probe_nested_settlement_fixture.js'], cwd=ROOT,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


class Collector:
    def __init__(self, platform, report):
        self.platform, self.report = platform, report
        self.session, self.frame, self.game_time, self.damage = 'native-checkpoint-replay', 0, 0, 0

    def read(self):
        return dict(session=self.session, native_session=self.session, state=2,
                    frame=self.frame, game_time=self.game_time,
                    battle_metadata=dict(platform=self.platform, source_build='synthetic-build'),
                    rows=[dict(id=self.report['source'], damage=self.damage, healing=0,
                               physical=0, arts=self.damage, true=0, elemental=0)])

    def read_roster(self):
        report = self.report
        return RosterSnapshot(self.frame, (CharacterEntry(report['source'], report['source_uid'],
                              False, False, None, None, 0),), frozenset((report['source_uid'],)), True, 0)


def consume(report, platform):
    collector = Collector(platform, report)
    observer = BoundedObserver(collector, NATIVE_PC_CONTRACT if platform == 'native_pc' else MUMU_CONTRACT,
                               report['batch'])
    live = LiveEventCapture(collector, observer)
    live.sample()
    collector.frame, collector.game_time = report['mark']['frame'], report['mark']['game_time']
    collector.damage = report['measured_parent_hp']
    observer.records = deepcopy(report['batch']['events'])
    observer.sequence = report['mark']['sequence']
    return live.sample()


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
@pytest.mark.parametrize('index', range(5))
def test_five_measured_nested_kills_close_parent_and_child_once(producer, platform, index):
    report = producer['android' if platform == 'mumu' else 'native_pc']['valid'][index]
    settlements = [event for event in report['batch']['events'] if event['kind'] == 'damage_settlement']
    assert len(settlements) == 2
    parent = next(event for event in settlements if event['source'] == report['source'])
    child = next(event for event in settlements if event['source'] is None)
    assert parent['settlement_complete'] is True
    assert child['settlement_complete'] is True
    assert parent['amount'] == report['measured_parent_hp']
    assert child['amount'] == report['measured_child_hp']
    assert parent['killed'] is False and child['killed'] is True
    assert parent['amount'] + child['amount'] == report['measured_before']
    assert report['batch']['enemy_damage_unknown'] is False
    result = consume(report, platform)
    row = result['rows'][report['source']]['settled_damage']
    assert row['complete'] is True and row['observed_hits'] == 2 and row['unverified_hits'] == 0
    assert row['known_hp_damage'] == report['measured_before']
    assert result['enemy_damage']['by_uid'][str(report['target_uid'])]['amount'] == report['measured_before']


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
@pytest.mark.parametrize('fault', ['missing_parent_real', 'changed_parent_real'])
def test_native_parent_real_is_required_and_cannot_be_replaced_by_nominal(producer, platform, fault):
    report = producer['android' if platform == 'mumu' else 'native_pc']['invalid'][fault]
    settlements = [event for event in report['batch']['events'] if event['kind'] == 'damage_settlement']
    parent = next(event for event in settlements if event['source'] == report['source'])
    assert parent['settlement_complete'] is False and parent['amount'] == 0
    result = consume(report, platform)
    row = result['rows'][report['source']]['settled_damage']
    assert row['complete'] is False and row['unverified_hits'] == 1
    assert row['known_hp_damage'] == report['measured_child_hp']


@pytest.fixture(scope='module')
def after_kill_producer():
    result = subprocess.run(['node', 'tests/event_probe_nested_after_kill_fixture.js'], cwd=ROOT,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_child_before_parent_zero_has_one_same_scope_raw_certificate(after_kill_producer, platform):
    records = after_kill_producer[platform]['valid']['batch']['events']
    parent = next(e for e in records if e['kind'] == 'damage_settlement' and e['source'] == 'char_owned_parent')
    parent_settlement = next(e for e in records if e['kind'] == 'damage_settlement' and e['source'] == parent['source'])
    child = next(e for e in records if e['kind'] == 'damage_settlement' and e['damage_type'] == 5)
    child_settlement = next(e for e in records if e['kind'] == 'damage_settlement' and e['source'] is None)
    proof = parent['proof']['nested_settlement']
    assert parent_settlement['proof']['nested_settlement'] == proof
    assert parent['settled_after_nested_kill'] is parent_settlement['settled_after_nested_kill'] is True
    assert parent_settlement['settlement_complete'] is child_settlement['settlement_complete'] is True
    assert parent['hp_before'] == parent['hp_after'] == parent['real_delta'] == parent_settlement['amount'] == 0
    assert proof == dict(target_uid='12', ordering='child_before_parent_zero', parent_entry_hp_before=80,
        parent_real_delta=0, parent_hp_before=0, parent_hp_after=0,
        child_hp_before=80, child_hp_after=0, child_real_delta=-80)
    assert proof['child_hp_before'] == child['hp_before'] == child_settlement['amount']
    assert proof['child_hp_after'] == child['hp_after'] == 0
    assert proof['child_real_delta'] == child['real_delta'] == -child_settlement['amount']
    assert proof['target_uid'] == child_settlement['target_uid'] == parent_settlement['target_uid']
    assert parent['frame'] == parent_settlement['frame'] == child['frame'] == child_settlement['frame']
    assert parent['target_instance'] == parent_settlement['target_instance'] == child['target_instance'] == child_settlement['target_instance']


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('fault', ['child_incomplete', 'child_delta_mismatch', 'parent_nonzero_delta', 'target_uid_changed'])
def test_after_kill_certificate_never_uses_incomplete_or_contradictory_child(after_kill_producer, platform, fault):
    records = after_kill_producer[platform]['invalid'][fault]['batch']['events']
    assert not any(event.get('settled_after_nested_kill') for event in records)
    assert not any((event.get('proof') or {}).get('nested_settlement') for event in records)
