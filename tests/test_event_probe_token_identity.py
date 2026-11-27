"""Feed real owned callback wire events into the shared player ledger."""
import json
from pathlib import Path
import subprocess

import pytest

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


@pytest.fixture(scope='module')
def callback_packets():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/event_probe_token_identity_fixture.js')],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    packet = json.loads(result.stdout)
    assert packet['status'] == 'verified'
    return packet


def test_derived_iron_pincer_callbacks_credit_their_own_white_iron_host_once(callback_packets):
    packet = callback_packets
    assert set(packet['platforms']) == {'mumu', 'native_pc'}
    for platform, case in packet['platforms'].items():
        assert case['owned_lineage_classes'] == 18
        assert {'class_cycle', 'incomplete_ancestry', 'missing_data'} <= packet['rejected'][platform].keys()
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


@pytest.mark.parametrize('platform', ['native_pc', 'mumu'])
def test_transient_callback_credits_host_without_a_sampled_token(callback_packets, platform):
    case = callback_packets['ephemeral_tokens'][platform]
    before, mark = case['before'], case['mark']
    baseline, batch = case['baseline'], case['batch']
    initial, current = _context(before), _context(mark)
    meter = MultiplayerBattleStatistics()
    consume(meter, before['game_time'], sequence=before['sequence'],
            native=initial, quality=_quality(baseline))
    result = consume(meter, mark['game_time'], batch['events'], sequence=mark['sequence'],
                     native=current, quality=_quality(batch))
    own = next(row for row in result['players'] if row['is_local'])
    assert own['observed_damage'] == 40
    actor, = own['actor_ledgers']
    host = next(row for row in current['memberships'] if row['cid'] == 'char_4046_ebnhlz')
    assert actor['source_character_uid'] == host['data_uid']
    assert actor['ledger']['effective_damage'] == 40
    settlement, = [event for event in batch['events'] if event['kind'] == 'damage_settlement']
    assert all(row['data_uid'] != settlement['source_character_uid']
               for frame in (initial, current) for row in frame['memberships'])
    assert meter._received[settlement['sequence']] == settlement
