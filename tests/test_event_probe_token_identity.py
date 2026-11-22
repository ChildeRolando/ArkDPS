"""Feed real owned callback wire events into the shared player ledger."""
import json
from pathlib import Path
import subprocess

from autochess_context import normalize_native_autochess
from multiplayer_battle import MultiplayerBattleStatistics
from test_multiplayer_native_context import QUALITY, consume


def _context(mark):
    raw = mark['native_autochess_context']
    value = normalize_native_autochess(raw, expected_frame=mark['frame'],
        expected_game_time=mark['game_time'], expected_scope_key=raw['scope_key'])
    assert value['status'] == 'available'
    return value


def _quality(batch):
    return {name: batch[name] for name in QUALITY if name in batch}


def test_derived_iron_pincer_callbacks_credit_their_own_white_iron_host_once():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/event_probe_token_identity_fixture.js')],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    packet = json.loads(result.stdout)
    assert packet['status'] == 'verified'
    assert set(packet['platforms']) == {'mumu', 'native_pc'}
    for platform, case in packet['platforms'].items():
        before, mark = case['before'], case['mark']
        baseline, batch = case['baseline'], case['batch']
        identities = case['identities']
        meter = MultiplayerBattleStatistics()
        consume(meter, before['game_time'], sequence=before['sequence'],
                native=_context(before), quality=_quality(baseline))
        result = consume(meter, mark['game_time'], batch['events'],
                         sequence=mark['sequence'], native=_context(mark), quality=_quality(batch))
        assert result['status'] == 'observing'
        players = {p['slot']: p for p in result['players']}
        expected_players = [(1, 'host_uid', 40)]
        if platform == 'native_pc':
            expected_players.append((2, 'peer_host_uid', 10))
        else:
            assert result['collection_mode'] == 'local'
            assert players[2]['observed_damage'] is None
            assert players[2]['statistics_status'] == 'not_collected'
        for slot, key, expected in expected_players:
            player = players[slot]
            actor, = player['actor_ledgers']
            assert actor['cid'] == identities['host_cid']
            assert actor['source_character_uid'] == identities[key]
            assert player['observed_damage'] == expected
            assert actor['ledger']['nominal_hits'] == 1
            assert actor['ledger']['effective_damage'] == expected
        assert all(players[slot]['observed_damage'] is None for slot in (3, 4))
        for record in case['settlements']:
            assert record['source'] == identities['token_cid']
            assert meter._received[record['sequence']] == record
