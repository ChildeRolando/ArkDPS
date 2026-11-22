"""Known map devices must not downgrade otherwise complete player coverage.

The rc22 round-eight shape was twelve map props and four blizzard props,
all with no host. IDs here are synthetic; actual recordings stay ignored.
"""
from copy import deepcopy

import pytest

from autochess_context import normalize_native_autochess
from multiplayer_battle import MultiplayerBattleStatistics
from test_multiplayer_native_context import PLAYERS, ROOT, consume, hit, member


def native_context(when, unknown):
    raw = dict(protocol_version=1, status='available', verified_same_frame=True,
               scope_key=ROOT, phase=4, round=4, self_player_index=3,
               frame=round(when * 30), game_time=when, players=deepcopy(PLAYERS),
               memberships=[member(i) for i in range(1, 5)],
               unknown_memberships=deepcopy(unknown))
    return normalize_native_autochess(raw, expected_frame=raw['frame'],
        expected_game_time=when, expected_scope_key=ROOT)


def run(unknown, *, quality=None):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=native_context(0, unknown))
    result = consume(meter, 1, [hit(i, i, 1) for i in range(1, 5)],
                     native=native_context(1, unknown), quality=quality)
    return meter, result


@pytest.mark.parametrize('candidate', [None, 3])
def test_sixteen_unowned_stage_props_preserve_diagnostics_without_partial_player_totals(candidate):
    unknown = [dict(cid='trap_1105_accrate' if i < 12 else 'trap_1112_acblzd',
                    data_uid=9001 + i, host_uid=0,
                    reason='token host unavailable or conflicting') for i in range(16)]
    if candidate is not None:
        for row in unknown:
            row['candidate_player_index'] = candidate
    original = deepcopy(unknown)
    _, control = run([])
    meter, result = run(unknown)
    assert [p['coverage_complete'] for p in control['players']] == [True] * 4
    assert [p['coverage_complete'] for p in result['players']] == [True] * 4
    assert [p['observed_damage'] for p in result['players']] == [100] * 4
    assert [p['statistics'] for p in result['players']] == [p['statistics'] for p in control['players']]
    assert result['reasons'] == [] and result['unknown_attribution'] == []
    assert len(result['unknown_memberships']) == 16 and meter._context['status'] == 'partial'
    assert unknown == original


@pytest.mark.parametrize('cid', ['token_10009_weedy_cannon', 'char_unverified', None])
def test_real_summon_character_and_unidentified_member_gaps_still_block_coverage(cid):
    _, result = run([dict(cid=cid, data_uid=9001, reason='native identity unavailable')])
    assert all(p['coverage_complete'] is False for p in result['players'])
    assert '存在无法按玩家隔离的角色归属缺证' in result['reasons']
    assert [p['observed_damage'] for p in result['players']] == [100] * 4


def test_summon_gap_with_proven_candidate_keeps_only_that_players_membership_reason():
    _, result = run([dict(cid='token_10009_weedy_cannon', data_uid=9001,
                         candidate_player_index=3, reason='native host unavailable')])
    assert [p['coverage_complete'] for p in result['players']] == [True, True, False, True]
    assert '该玩家存在未核实的角色归属' in result['players'][2]['reasons']


def test_prop_claim_cannot_hide_a_conflicting_real_actor_uid():
    _, result = run([dict(cid='trap_1105_accrate', data_uid=301,
                         reason='native role identity incomplete')])
    assert [p['coverage_complete'] for p in result['players']] == [True, True, False, True]
    assert '该玩家存在未核实的角色归属' in result['players'][2]['reasons']
    assert result['players'][2]['observed_damage'] is None


def test_stage_props_do_not_clear_real_settlement_quality_gaps():
    _, result = run([dict(cid='trap_1105_accrate', data_uid=9001,
                         reason='native host unavailable')],
                    quality=dict(enemy_damage_unknown=True, damage_settlement_unknown=True))
    assert all(p['coverage_complete'] is False for p in result['players'])
    assert '结算事件质量未完整确认' in result['reasons']
