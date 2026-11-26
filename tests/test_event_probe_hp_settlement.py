"""Actual native recipes and callbacks feed the existing consumers once."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import pytest
from event_probe import NATIVE_PC_CONTRACT
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
