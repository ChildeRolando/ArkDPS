"""Terminal native phase changes cannot erase the last verified combat ledger."""
from copy import deepcopy

import pytest

from battle_history import BattleRecord
from test_battle_history import record
from test_multiplayer_native_context import (ROOT, PLAYERS, MultiplayerBattleStatistics,
                                             consume, context, hit, player, stamp)


@pytest.mark.parametrize('phase', [4, 7, 8, None])
def test_terminal_uses_last_combat_witness_for_matching_tail_and_receipts_other_scope(phase):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    before = consume(meter, 1, [hit(1, 3, 1, 30)])
    witness = deepcopy(meter._context)
    bucket = meter._players[PLAYERS[2]['uid']]
    current = context(3, phase=phase if phase is not None else 4,
                      root=ROOT if phase == 4 else ROOT + ':terminal')
    if phase is None:
        current.update(status='unknown', verified_same_frame=False, memberships=[])
    tail = [hit(2, 3, 2, 20), hit(3, 3, 3, 999,
             event_scope=stamp(phase=8, root=ROOT + ':terminal'))]
    original = deepcopy(tail)
    result = consume(meter, 3, tail, native=current, state=3, terminal=True)
    frozen = meter.finish(3, complete=True)
    assert result['scope'] == frozen['scope'] == before['scope']
    assert frozen['scope_kind'] == 'defense'
    assert frozen['native_phase'] == 4 and frozen['identity_epoch'] == 0
    assert player(frozen, 3)['observed_damage'] == 50
    assert player(frozen, 3)['actor_ledgers'][0]['source_character_uid'] == 301
    assert meter._players[PLAYERS[2]['uid']] is bucket
    assert meter._context == witness  # Retained evidence was not retimed to frame 90.
    assert meter._context['frame'] == 30 and meter._context['game_time'] == 1
    assert all(p.stream.sequence == 3 for p in meter._players.values())
    assert bucket.stream.received[3] == original[1]
    assert frozen['scope_foreign_events'] == 1
    assert frozen['previous_rounds'] == []
    assert tail == original


def test_terminal_unknown_source_stays_unassigned_and_does_not_impersonate_last_actor():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    consume(meter, 1, [hit(1, 3, 1, 30)])
    missing = hit(2, 3, 2, 999)
    missing.pop('source_character_uid')
    value = consume(meter, 3, [missing, hit(3, 3, 3, 20)],
        native=context(3, phase=8, root=ROOT + ':end'), state=3, terminal=True)
    assert player(value, 3)['observed_damage'] == 50
    assert player(value, 3)['coverage_complete'] is False
    assert value['unknown_attribution'][0]['reason'] == 'source_uid_missing'


def test_terminal_current_account_table_cannot_reassign_old_combat_tail():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    consume(meter, 1, [hit(1, 3, 1, 30)])
    changed = deepcopy(PLAYERS)
    changed[2]['uid'] = '920000003'
    result = consume(meter, 2, [hit(2, 3, 2, 20)], state=3, terminal=True,
        native=context(2, phase=8, root=ROOT + ':end', players=changed))
    assert player(result, 3)['observed_damage'] == 50
    assert all(p['player_uid'] != '920000003' for p in result['players'])
    assert result['self_player_uid'] == PLAYERS[2]['uid']


@pytest.mark.parametrize('changes', [dict(native_session='another-native-session'),
    dict(state=2), dict(game_time=0.5, frame=15)])
def test_untrusted_or_different_native_terminal_preserves_previous_amount_without_new_scope(changes):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    before = consume(meter, 1, [hit(1, 3, 1, 30)])
    value = consume(meter, 3, [hit(2, 3, 2, 999)],
        native=context(3, phase=8, root=ROOT + ':end'), terminal=True,
        **dict(dict(state=3), **changes))
    assert value['scope'] == before['scope']
    assert value['identity_epoch'] == 0
    assert player(value, 3)['observed_damage'] == 30
    assert player(value, 3)['coverage_complete'] is False
    assert value['status'] == 'gap'


def test_confirmed_abandon_is_a_terminal_witness_without_fabricating_state_three():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    consume(meter, 1, [hit(1, 3, 1, 30)])
    metadata = dict(stage_id='Activities/ACT3AUTOCHESS/level_test',
                    outcome='abandoned', evidence='verified native finish transition')
    value = consume(meter, 2, [hit(2, 3, 2, 20)],
        native=context(2, phase=8, root=ROOT + ':end'),
        terminal=True, state=2, battle_metadata=metadata)
    assert value['scope_kind'] == 'defense'
    assert player(value, 3)['observed_damage'] == 50


def test_initial_end_context_does_not_create_a_fake_combat_statistics_bucket():
    meter = MultiplayerBattleStatistics()
    assert consume(meter, 2, native=context(2, phase=8), state=3, terminal=True) is None
    assert meter._players == {}


def test_terminal_frozen_combat_details_still_pass_schema2_archive_validation():
    data = record().to_dict()
    battle = data['battle_id']
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, session=battle)
    consume(meter, 1, [hit(1, 3, 1, 30)], session=battle)
    consume(meter, 12, [hit(2, 3, 2, 20), hit(3, 3, 3, 999,
        event_scope=stamp(phase=8, root=ROOT + ':end'))], session=battle,
        native=context(12, phase=8, root=ROOT + ':end'), state=3, terminal=True)
    data['last_sample']['multiplayer'] = meter.finish(12, complete=True)
    archived = BattleRecord.from_dict(data).last_sample['multiplayer']
    assert archived['scope_kind'] == 'defense'
    assert player(archived, 3)['observed_damage'] == 50
    assert len(player(archived, 3)['actor_ledgers']) == 1


def test_scoped_native_terminal_tail_keeps_combat_rows_and_primary_ack_once():
    from test_native_pc_autochess_acquisition import ScopedNativeBattle

    battle = ScopedNativeBattle()
    battle.poll()
    battle.advance(1)
    battle.advance(1, [battle.scoped_hit(1, 3, 30)])
    battle.poll()
    old_scope = battle.native_scope
    battle.native_phase, battle.autochess_epoch = 8, 2
    battle.advance(2)
    tail = [battle.scoped_hit(2, 3, 20, scope=old_scope, phase=4),
            battle.scoped_hit(3, 3, 999)]
    battle.advance(2, tail, state=3)
    observation = battle.poll()
    result = battle.runtime.display_sample()['multiplayer']
    assert observation.kind == 'terminal'
    assert result['status'] == 'ended'
    assert result['scope_kind'] == 'defense'
    assert result['players'][2]['observed_damage'] == 50
    assert len(result['players'][2]['actor_ledgers']) == 1
    assert result['scope_foreign_events'] == 1
    assert battle.commits == [0, 1, 3]
