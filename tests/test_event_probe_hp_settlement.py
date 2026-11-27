"""Actual native recipes and callbacks feed the existing consumers once."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import pytest
from event_probe import NATIVE_PC_CONTRACT
from event_capture import LiveEventCapture
from roster import CharacterEntry, RosterSnapshot
from test_event_probe_parity import BoundedObserver
from multiplayer_battle import MultiplayerBattleStatistics
from test_multiplayer_native_context import consume, context, member, player, stamp

ROOT = Path(__file__).resolve().parents[1]

def test_independent_hp_writer_closes_existing_settlement_owner_once():
    result = subprocess.run(['node', 'tests/event_probe_hp_settlement_fixture.js'], cwd=ROOT,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = json.loads(result.stdout)
    for name, report in reports.items():
        events = report['batch']['events']
        certs = [event for event in events if event['kind'] == 'damage_settlement']
        assert len(certs) == 1
        cert = certs[0]
        expected = 50 if name == 'overkill' else 40
        assert cert['amount'] == expected and cert['source_character_uid'] == 1010
        assert cert['proof']['evidence_method'] == 'Entity.SetHpInternal+Entity.ApplyModifier'
        quality = {key:value for key,value in NATIVE_PC_CONTRACT.normalize_quality(report['batch']).items() if key != 'events'}
        class Collector:
            session = 'owned-hp-writer'
            frame = 0
            game_time = 0

            def read(self):
                return dict(session=self.session, native_session=self.session, state=2,
                            frame=self.frame, game_time=self.game_time,
                            battle_metadata=dict(platform='native_pc', source_build='synthetic-build'),
                            rows=[dict(id='char_147_shining', damage=cert['nominal_amount'] if self.frame else 0,
                                       healing=0, physical=0, arts=0, true=0, elemental=0)])

            def read_roster(self):
                entry = CharacterEntry('char_147_shining', 1010, False, False, None, None, 0)
                return RosterSnapshot(self.frame, (entry,), frozenset({1010}) if self.frame else frozenset(), True, 0)

        collector = Collector()
        observer = BoundedObserver(collector, NATIVE_PC_CONTRACT, report['batch'])
        capture = LiveEventCapture(collector, observer)
        capture.sample()
        collector.frame, collector.game_time = 30, 1
        observer.records, observer.sequence = deepcopy(events), report['mark']['sequence']
        published = capture.sample()
        solo = published['rows']['char_147_shining']['settled_damage']
        assert solo['observed_hits'] == 1 and solo['unverified_hits'] == 0
        assert solo['known_hp_damage'] == expected
        observer.records = deepcopy(events)
        assert capture.sample()['rows'] == published['rows']

        meter = MultiplayerBattleStatistics()
        members = [member(3, uid=1010, cid='char_147_shining'), member(1, uid=2010, cid='char_147_shining')]
        metadata = dict(platform='native_pc', stage_id='Activities/ACT3AUTOCHESS/level_act3autochess_m04')
        consume(meter, 0, native=context(0, members=members), battle_metadata=metadata)
        records = deepcopy(events)
        for event in records:
            event['native_autochess_scope'] = dict(stamp(when=event['game_time']), frame=event['frame'])
        native = dict(context(1, members=members), frame=30)
        actual = consume(meter, 1, records, native=native, quality=quality, frame=30, battle_metadata=metadata)
        own = player(actual, 3)['actor_ledgers'][0]['ledger']
        assert own['observed_hits'] == 1 and own['unverified_hits'] == 0
        assert own['known_hp_damage'] == pytest.approx(expected)
        assert own['known_shield_damage'] == 0
        assert own['overkill_damage'] == (30 if name == 'overkill' else 0)
        assert all(row['ledger']['observed_hits'] == 0 for row in player(actual, 1)['actor_ledgers'])
        assert consume(meter, 1, records, native=native, quality=quality, frame=30, battle_metadata=metadata) == actual
