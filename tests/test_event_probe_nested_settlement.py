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
