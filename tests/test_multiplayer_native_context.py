"""Native player accounts and phase stamps share the ordinary event core."""
from copy import deepcopy

import pytest

from autochess_context import normalize_native_autochess
from multiplayer_battle import MultiplayerBattleStatistics
from roster import RosterSnapshot


CID = 'char_367_swllow'
ROOT = 'controller:host:manager:center:scope:1'
PLAYERS = [dict(index=i, uid=f'91000000{i}', nick_name=f'玩家{i}', nick_number=f'{i:04}')
           for i in range(1, 5)]
QUALITY = dict(partial_damage_version=7, enemy_damage_ready=True, enemy_damage_unknown=False,
               damage_settlement_ready=True, damage_settlement_unknown=False,
               damage_unknown=False, engagement_unknown=False, shield_unknown=False,
               skill_unknown=False, mitigation_ready=True, mitigation_unknown=False,
               baseline_discarded_skill_events=[], baseline_discarded_skill_events_complete=True)


def member(index=3, uid=None, cid=CID, side=None, **changes):
    result = dict(cid=cid, data_uid=100 * index + 1 if uid is None else uid,
                  player_index=index, player_side=index if side is None else side,
                  is_token=False, is_predefined=False, host_key=None, host_uid=None)
    result.update(changes)
    return result


def context(when, *, phase=4, native_round=4, root=ROOT, members=None, players=None):
    raw = dict(protocol_version=1, status='available', verified_same_frame=True,
               scope_key=root, phase=phase, round=native_round, self_player_index=3,
               frame=round(when * 30), game_time=when,
               players=deepcopy(PLAYERS if players is None else players),
               memberships=[member(i) for i in range(1, 5)] if members is None else members)
    return normalize_native_autochess(raw, expected_frame=raw['frame'],
        expected_game_time=when, expected_scope_key=root)


def stamp(*, phase=4, native_round=4, root=ROOT, when=None, **changes):
    result = dict(protocol_version=1, status='available', verified_same_frame=True,
                  scope_key=root, phase=phase, round=native_round)
    result.update(changes)
    if when is not None:
        result.update(frame=round(when * 30), game_time=when)
    return result


def hit(seq, index, when, amount=100, *, uid=None, cid=CID, event_scope=None, **changes):
    event_scope = stamp() if event_scope is None else deepcopy(event_scope)
    # This fixture models the producer's capture-time clock stamp. Consumers
    # must reject records missing these fields rather than supply defaults.
    event_scope.setdefault('frame', round(when * 30))
    event_scope.setdefault('game_time', when)
    result = dict(sequence=seq, frame=round(when * 30), game_time=when, source=cid,
        source_character_uid=100 * index + 1 if uid is None else uid,
        kind='damage_settlement', amount=amount, nominal_amount=amount,
        target='enemy_1007_slime', target_uid='500', target_instance='target-500',
        damage_type=1, shield_absorbed=0, settlement_complete=True, killed=False,
        native_autochess_scope=event_scope)
    result.update(changes)
    return result


def consume(meter, when, events=(), *, sequence=None, native=None, quality=None, terminal=False, **changes):
    native = context(when) if native is None else native
    sample = dict(session='round-owner:1', native_session='native-controller:1',
        frame=round(when * 30), game_time=when, global_total=0, global_heal=0, rows=[],
        native_counter_scope='events_only', autochess_context=native,
        battle_metadata={'stage_id': 'Activities/ACT3AUTOCHESS/level_act3autochess_m11'})
    sample.update(changes)
    if sequence is None:
        sequence = max((event['sequence'] for event in events), default=meter._sequence or 0)
    declared = dict(QUALITY, **(quality or {}))
    return meter.consume(sample, RosterSnapshot(sample['frame'], local_player_side=0),
        sequence, dict(events=list(events), **declared), mark=declared, terminal=terminal)


def player(result, index):
    return next(p for p in result['players'] if p['player_uid'] == PLAYERS[index - 1]['uid'])


def test_native_same_cid_four_data_uids_belong_to_real_accounts_without_native_totals():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    records = [hit(i, i, i, i * 100) for i in range(1, 5)]
    original = deepcopy(records)
    result = consume(meter, 5, records)
    assert result['schema_version'] == 2
    assert result['scope_kind'] == 'defense' and result['native_round'] == 4
    assert [player(result, i)['observed_damage'] for i in range(1, 5)] == [100, 200, 300, 400]
    assert [p['slot'] for p in result['players']] == [1, 2, 3, 4]
    assert player(result, 3)['is_local'] is True
    assert player(result, 1)['is_local'] is False
    assert player(result, 3)['player_name'] == '玩家3'
    assert all(p['damage'] is p['dps'] is p['recent_dps'] is None for p in result['players'])
    assert len({p['actor_ledgers'][0]['owner_id'] for p in result['players']}) == 4
    assert all(not p['statistics']['totals_complete'] for p in result['players'])
    assert records == original
    assert all(p.stream.received[1] == records[0] for p in meter._players.values())


def test_scoped_receipts_keep_the_original_raw_proof_after_input_is_reused():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    record = hit(1, 3, 1, proof={'settlement': {'actual': 100}})
    original = deepcopy(record)
    consume(meter, 1, [record])
    record['proof']['settlement']['actual'] = 999

    assert meter._received[1] == original
    assert all(p.stream.received[1] == original for p in meter._players.values())
    replayed = consume(meter, 2, [original])
    assert player(replayed, 3)['observed_damage'] == 100
    conflict = consume(meter, 2, [record])
    assert conflict['status'] == 'gap'
    assert player(conflict, 3)['observed_damage'] == 100


def test_help_player_index_three_side_one_stays_the_third_account():
    meter = MultiplayerBattleStatistics()
    members = [member(3, uid=1048780, side=1)]
    consume(meter, 0, native=context(0, phase=5, members=members))
    result = consume(meter, 2, [hit(1, 3, 1, uid=1048780,
        event_scope=stamp(phase=5))], native=context(2, phase=5, members=members))
    assert result['scope_kind'] == 'assist'
    assert player(result, 3)['observed_damage'] == 100
    assert player(result, 1)['observed_damage'] is None
    assert player(result, 3)['is_local'] is True


@pytest.mark.parametrize('changes,scope_kind', [
    ({'phase': 5}, 'assist'), ({'phase': 6}, 'boss'), ({'phase': 3}, 'prepare'),
    ({'native_round': 5}, 'defense'), ({'root': ROOT + ':2'}, 'defense')])
def test_native_scope_changes_clear_ledgers_windows_even_when_clock_keeps_advancing(changes, scope_kind):
    meter = MultiplayerBattleStatistics()
    first = consume(meter, 0)
    consume(meter, 2, [hit(1, 3, 1)])
    switched = consume(meter, 3, sequence=1, native=context(3, **changes))
    assert switched['scope_kind'] == scope_kind
    assert switched['scope'] != first['scope']
    assert switched['identity_epoch'] == 1
    assert player(switched, 3)['observed_damage'] is None
    assert switched['previous_rounds'][0]['players'][2]['observed_damage'] == 100
    assert all(not p.statistics.skill_active and not p.statistics.settlement_bars
               for p in meter._players.values())


def test_account_table_change_creates_new_scope_and_stale_context_cannot_restore_old_accounts():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    consume(meter, 2, [hit(1, 3, 1)])
    changed = deepcopy(PLAYERS)
    changed[2]['uid'] = '920000003'
    switched = consume(meter, 3, sequence=1, native=context(3, players=changed))
    assert switched['identity_epoch'] == 1
    assert next(p for p in switched['players'] if p['slot'] == 3)['player_uid'] == '920000003'
    stale = consume(meter, 4, sequence=1)
    assert stale['scope'] == switched['scope']


@pytest.mark.parametrize('event_scope,reason', [
    (None, 'event_scope_unverified'), ({}, 'event_scope_unverified'),
    (stamp(verified_same_frame=False), 'event_scope_unverified'),
    (stamp(phase=5), 'event_scope_mismatch'),
    (stamp(native_round=5), 'event_scope_mismatch'),
    (stamp(root=ROOT + ':unknown'), 'event_scope_mismatch')])
def test_missing_or_wrong_event_scope_never_counts_into_any_account(event_scope, reason):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    record = hit(1, 3, 1)
    if event_scope is None:
        record.pop('native_autochess_scope')
    else:
        record['native_autochess_scope'] = dict(event_scope, frame=30, game_time=1)
    result = consume(meter, 3, [record, hit(2, 3, 2, 20)])
    assert player(result, 3)['observed_damage'] == 20
    assert all(player(result, i)['observed_damage'] is None for i in (1, 2, 4))
    assert result['unknown_attribution'][0]['reason'] == reason
    assert all(p.stream.sequence == 2 and not p.stream.closed for p in meter._players.values())
    assert all(p.stream.received[1] == record for p in meter._players.values())


def test_delayed_old_scope_does_not_change_new_scope_elemental_witness_or_skill_window():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    # The source actor's first evidence in this new phase is the real effect
    # start below, rather than an already-present actor with missing cold state.
    consume(meter, 1, sequence=0, native=context(1, phase=5,
        members=[member(i) for i in (1, 2, 4)]))
    activation = dict(sequence=1, frame=60, game_time=2, source=CID, source_character_uid=301,
        kind='skill_start', amount=1, skill_key='skchr_swllow_2',
        proof={'method': 'Character.SwitchToSkillState'}, native_autochess_scope=stamp(phase=5, when=2))
    local_bar = dict(sequence=2, frame=90, game_time=3, source=CID, source_character_uid=301,
        kind='elemental_bar', amount=100, target_uid='500', target_instance='target-500',
        native_autochess_scope=stamp(phase=5, when=3))
    old_bar = dict(sequence=3, frame=120, game_time=4, source=CID, source_character_uid=101,
        kind='elemental_bar', amount=100, target_uid='500', target_instance='target-500',
        native_autochess_scope=stamp(when=4))
    explosion = hit(4, 3, 5, source=None, damage_type=5, event_scope=stamp(phase=5))
    explosion.pop('source_character_uid')
    result = consume(meter, 6, [activation, local_bar, old_bar, explosion], native=context(6, phase=5))
    assert player(result, 3)['observed_damage'] == 100
    assert player(result, 1)['observed_damage'] is None
    assert result['scope_foreign_events'] == 1
    assert result['unknown_attribution'] == []
    assert player(result, 3)['actor_ledgers'][0]['ledger']['observed_skill_damage'] == 100


def test_unknown_uid_and_bad_token_keep_verified_actor_skill_classification_intact():
    meter = MultiplayerBattleStatistics()
    members = [member(3), member(2, uid=299, cid='token_bad', is_token=True)]
    consume(meter, 0, native=context(0, members=members))
    unknown = hit(1, 2, 1, uid=299, cid='token_bad')
    result = consume(meter, 4, [unknown, hit(2, 3, 2)], native=context(4, members=members))
    assert player(result, 3)['observed_damage'] == 100
    assert player(result, 3)['coverage_complete'] is True
    lane = player(result, 3)['actor_ledgers'][0]['ledger']
    assert lane['skill_split_complete'] is True
    assert lane['skill_damage_unverified_share'] == 0
    assert player(result, 2)['observed_damage'] is None
    assert player(result, 2)['coverage_complete'] is False


def test_token_merges_only_to_matching_host_account_and_quality_aliases_reach_core_owner():
    meter = MultiplayerBattleStatistics()
    token = member(3, uid=399, cid='token_good', is_token=True, host_key=CID, host_uid=301)
    members = [member(3), token, member(1)]
    consume(meter, 0, native=context(0, members=members))
    result = consume(meter, 3, [hit(1, 3, 1, 20, uid=399, cid='token_good'), hit(2, 1, 2)],
        native=context(3, members=members), quality={'unknown_sources': ['token_good']})
    assert player(result, 3)['observed_damage'] == 20
    assert len(player(result, 3)['actor_ledgers']) == 1
    assert player(result, 3)['actor_ledgers'][0]['source_character_uid'] == 301
    owner = player(result, 3)['actor_ledgers'][0]['owner_id']
    assert owner in meter._players[PLAYERS[2]['uid']].statistics.overkill_incomplete
    assert not meter._players[PLAYERS[0]['uid']].statistics.overkill_incomplete


@pytest.mark.parametrize('baseline', [dict(global_total=1), dict(global_heal=1),
    dict(rows=[dict(id=CID, damage=20, healing=0)]), dict(sequence=5)])
def test_early_time_alone_cannot_certify_full_native_phase_coverage(baseline):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0.5, **baseline)
    record = hit((meter._sequence or 0) + 1, 3, 1)
    result = consume(meter, 2, [record])
    assert player(result, 3)['observed_damage'] == 100
    assert player(result, 3)['coverage_complete'] is False


def test_initial_old_prefix_is_discarded_at_explicit_sequence_baseline_not_relabelled():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0.5, [hit(1, 3, 0.1, 999)], sequence=1)
    result = consume(meter, 2, [hit(2, 3, 1, 10)])
    assert player(result, 3)['observed_damage'] == 10
    assert player(result, 3)['coverage_complete'] is False
    assert meter._baseline == 1
    assert all(1 not in p.stream.received for p in meter._players.values())


def test_native_replay_still_compares_original_scope_metadata_and_never_double_counts():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    record = hit(1, 3, 1)
    consume(meter, 2, [record])
    replayed = consume(meter, 3, [record], sequence=1)
    assert player(replayed, 3)['observed_damage'] == 100
    tampered = deepcopy(record)
    tampered['native_autochess_scope']['phase'] = 5
    failed = consume(meter, 4, [tampered], sequence=1)
    assert failed['status'] == 'gap'
    assert player(failed, 3)['observed_damage'] == 100


def test_missing_player_slot_is_display_unknown_without_a_synthetic_statistics_bucket():
    meter = MultiplayerBattleStatistics()
    native = context(0, players=PLAYERS[:3], members=[member(3)])
    result = consume(meter, 0, native=native)
    absent = next(p for p in result['players'] if p['slot'] == 4)
    assert absent['player_uid'] is absent['statistics'] is absent['observed_damage'] is None
    assert absent['actor_ledgers'] == []
    assert len(meter._players) == 3
    assert set(meter._players) == {p['uid'] for p in PLAYERS[:3]}


def test_transient_unknown_context_receipts_events_without_falling_back_to_side():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    unknown = context(2)
    unknown.update(status='unknown', verified_same_frame=False, memberships=[])
    partial = consume(meter, 2, [hit(1, 3, 1, 999)], native=unknown)
    assert partial['schema_version'] == 2
    assert all(p['observed_damage'] is None for p in partial['players'])
    result = consume(meter, 4, [hit(2, 3, 3, 10)])
    assert player(result, 3)['observed_damage'] == 10
    assert player(result, 3)['coverage_complete'] is False
    assert result['status'] == 'observing'


def test_missing_uid_passive_cast_uses_canonical_ignore_without_lowering_verified_neighbors():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    passive = dict(sequence=1, frame=30, game_time=1, source=CID, kind='skill_end', amount=1,
        skill_key='skchr_swllow_2', skill_wait_for_end=False, skill_reason=1,
        proof={'method': 'BasicSkill.OnCastFinish'}, native_autochess_scope=stamp(when=1))
    result = consume(meter, 6, [passive, *(hit(i + 1, i, i + 1) for i in range(1, 5))])
    assert all(p['coverage_complete'] is True for p in result['players'])
    assert all(not p.statistics.skill_incomplete and not p.statistics.settlement_skill_unassigned
               for p in meter._players.values())


def test_unverified_scope_skill_end_downgrades_only_the_candidate_account():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    start = dict(sequence=1, frame=30, game_time=1, source=CID, source_character_uid=301,
        kind='skill_start', amount=1, skill_key='skchr_swllow_2',
        proof={'method': 'Character.SwitchToSkillState'}, native_autochess_scope=stamp(when=1))
    end = dict(start, sequence=2, frame=60, game_time=2, kind='skill_end',
               native_autochess_scope=stamp(verified_same_frame=False, when=2))
    result = consume(meter, 5, [start, end, hit(3, 3, 3), hit(4, 1, 4)])
    assert player(result, 3)['actor_ledgers'][0]['ledger']['skill_split_complete'] is False
    assert player(result, 1)['actor_ledgers'][0]['ledger']['skill_split_complete'] is True


def test_unverified_scope_bar_prevents_inheriting_stale_owner_until_a_new_verified_bar():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    def bar(seq, index, when, scope):
        return dict(sequence=seq, frame=when * 30, game_time=when, source=CID,
            source_character_uid=100 * index + 1, kind='elemental_bar', amount=100,
            target_uid='500', target_instance='target-500',
            native_autochess_scope=dict(scope, frame=when * 30, game_time=when))
    explosion = hit(3, 3, 3, source=None, damage_type=5)
    explosion.pop('source_character_uid')
    consume(meter, 4, [bar(1, 3, 1, stamp()),
        bar(2, 1, 2, stamp(verified_same_frame=False)), explosion])
    assert all(p['observed_damage'] is None for p in meter.result()['players'])
    next_hit = dict(explosion, sequence=5, frame=180, game_time=6,
                    native_autochess_scope=stamp(when=6))
    result = consume(meter, 7, [bar(4, 1, 5, stamp()), next_hit])
    assert player(result, 1)['observed_damage'] == 100
    assert player(result, 3)['observed_damage'] is None


@pytest.mark.parametrize('changes', [dict(frame=None), dict(frame=31),
    dict(game_time=None), dict(game_time=1.1), dict(frame=True), dict(game_time=True)])
def test_event_scope_clock_must_match_the_original_event_without_consumer_defaults(changes):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    record = hit(1, 3, 1)
    for key, value in changes.items():
        if value is None:
            record['native_autochess_scope'].pop(key)
        else:
            record['native_autochess_scope'][key] = value
    result = consume(meter, 3, [record, hit(2, 3, 2, 20)])
    assert player(result, 3)['observed_damage'] == 20
    assert result['unknown_attribution'][0]['reason'] == 'event_scope_unverified'


def test_unknown_context_retains_known_ledger_receipts_all_sequences_and_recovers_same_bucket():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    known = consume(meter, 1, [hit(1, 3, 1, 30)])
    bucket = meter._players[PLAYERS[2]['uid']]
    unknown = context(2)
    unknown.update(status='unknown', verified_same_frame=False, memberships=[])
    unclassified = hit(2, 3, 2, 50, event_scope=stamp(phase=5, root=ROOT + ':unverified'))
    uncertain = consume(meter, 2, [unclassified], native=unknown)
    assert uncertain['available'] is False
    assert uncertain['scope_kind'] == 'unknown'
    assert player(uncertain, 3)['observed_damage'] == 30
    assert all(p.stream.sequence == 2 and p.stream.frame == 60 and p.stream.game_time == 2
               for p in meter._players.values())
    assert bucket.stream.received[2] == unclassified
    recovered = consume(meter, 3, [hit(3, 3, 3, 60)])
    assert recovered['available'] is True and recovered['scope_kind'] == 'defense'
    assert recovered['scope'] == known['scope'] and recovered['identity_epoch'] == known['identity_epoch']
    assert meter._players[PLAYERS[2]['uid']] is bucket
    assert player(recovered, 3)['observed_damage'] == 90
    assert player(recovered, 3)['coverage_complete'] is False
    assert recovered['status'] == 'observing'


def test_scoped_native_battle_unknown_context_keeps_single_ack_and_ninety_verified_damage():
    from test_native_pc_autochess_acquisition import ScopedNativeBattle

    class Drop(ScopedNativeBattle):
        unknown = False

        def binding(self):
            result = super().binding()
            if self.unknown:
                result['native_autochess_context'] = dict(protocol_version=1, status='unknown',
                    verified_same_frame=False, frame=self.frame, game_time=self.when,
                    reason='synthetic short metadata read')
            return result

    battle = Drop()
    battle.poll()
    battle.advance(1, [battle.scoped_hit(1, 3, 30)])
    battle.poll()
    original = battle.runtime.display_sample()['multiplayer']
    battle.unknown = True
    battle.advance(2, [battle.scoped_hit(2, 3, 50)])
    battle.poll()
    during = battle.runtime.display_sample()['multiplayer']
    assert during['available'] is False and during['scope_kind'] == 'unknown'
    battle.unknown = False
    battle.advance(3, [battle.scoped_hit(3, 3, 60)])
    battle.poll()
    result = battle.runtime.display_sample()['multiplayer']
    assert result['scope'] == original['scope']
    assert result['identity_epoch'] == original['identity_epoch']
    assert result['players'][2]['observed_damage'] == 90
    assert result['players'][2]['coverage_complete'] is False
    assert result['status'] == 'observing'
    assert battle.commits == [0, 1, 2, 3]
