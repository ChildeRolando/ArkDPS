"""Per-side event evidence must never become a fabricated native counter."""
import gzip
import json
from pathlib import Path

import pytest

from multiplayer_battle import MultiplayerBattleStatistics
from roster import CharacterEntry, RosterSnapshot


CID = 'char_367_swllow'
QUALITY = dict(enemy_damage_ready=True, enemy_damage_unknown=False, partial_damage_version=7)


def actor(side, uid=None, cid=CID, **changes):
    return CharacterEntry(cid, uid if uid is not None else 100 * side + 1,
        changes.get('is_token', False), False, changes.get('host_key'),
        changes.get('host_uid'), side)


def sample(when=0, session='round-1', **changes):
    return dict(session=session, game_time=when, frame=round(when * 30),
        battle_metadata={'stage_id': 'Activities/ACT3AUTOCHESS/level_act3autochess_m11'},
        **changes)


def hit(seq, side, when, amount=100, *, uid=None, cid=CID, nominal=None):
    return dict(sequence=seq, frame=round(when * 30), game_time=when, source=cid,
        source_character_uid=100 * side + 1 if uid is None else uid,
        kind='damage_settlement', amount=amount, nominal_amount=amount if nominal is None else nominal,
        target='enemy_1007_slime', target_uid=str(seq), target_instance=f'0x{seq:08x}',
        damage_type=1, shield_absorbed=0, settlement_complete=True, killed=False)


def consume(meter, when, entries, events=(), *, sequence=None, session='round-1', local=3, **changes):
    value = sample(when, session, **changes)
    roster = RosterSnapshot(value['frame'], tuple(entries), local_player_side=local)
    if sequence is None:
        sequence = max([e['sequence'] for e in events], default=meter._sequence or 0)
    return meter.consume(value, roster, sequence, dict(events=list(events), **QUALITY), mark=QUALITY)


def player(value, side):
    return value['players'][side - 1]


def test_four_sides_and_duplicate_cids_use_uid_ledgers_without_native_counters():
    meter = MultiplayerBattleStatistics()
    entries = [actor(side) for side in range(1, 5)] + [actor(3, uid=399)]
    consume(meter, 0, entries)
    value = consume(meter, 10, entries,
        [hit(side, side, side, side * 100) for side in range(1, 5)] + [hit(5, 3, 5, 50, uid=399)])
    assert value['observed_player_sides'] == [1, 2, 3, 4]
    assert value['expected_player_slots'] == 4
    assert [p['observed_damage'] for p in value['players']] == [100, 200, 350, 400]
    assert [p['observed_dps'] for p in value['players']] == [10, 20, 35, 40]
    assert all(p['damage'] is p['dps'] is p['recent_dps'] is None for p in value['players'])
    rows = player(value, 3)['statistics']['rows']
    assert set(rows) == {f'{CID}#301', f'{CID}#399'}
    assert all(row['damage'] is None and not row['counter_snapshot_complete'] for row in rows.values())
    assert all(p['statistics']['totals_complete'] is False for p in value['players'])


def test_unobserved_sides_remain_unknown_and_pause_does_not_consume_game_seconds():
    meter = MultiplayerBattleStatistics()
    entries = [actor(side) for side in range(1, 5)]
    consume(meter, 0, entries)
    value = consume(meter, 2, entries, [hit(1, 4, 1, 100)])
    assert [p['observed_damage'] for p in value['players']] == [None, None, None, 100]
    assert player(value, 4)['observed_dps'] == 50
    paused = consume(meter, 2, entries, paused=True)
    assert player(paused, 4)['observed_dps'] == 50
    assert player(paused, 1)['status'] == 'unknown'
    assert player(paused, 4)['observed_window'] == [0, 2]


def test_recent_rate_uses_shared_settlement_game_window_and_freezes_at_terminal():
    meter = MultiplayerBattleStatistics()
    entries = [actor(3)]
    consume(meter, 0, entries)
    consume(meter, 2, entries, [hit(1, 3, 1, 100)])
    value = consume(meter, 8, entries, [hit(2, 3, 6, 50)])
    assert player(value, 3)['observed_recent_dps'] == 10
    assert player(value, 3)['observed_recent_window'] == [3, 8]
    final = meter.finish(8, complete=True)
    assert final['status'] == 'ended'
    assert player(final, 3)['observed_dps'] == pytest.approx(150 / 8)
    assert player(final, 3)['observed_recent_dps'] is None
    assert player(final, 3)['coverage_complete'] is True
    assert consume(meter, 9, entries, [hit(3, 3, 9, 999)]) == final


def test_token_requires_uid_and_verified_host_then_merges_only_into_that_actor():
    meter = MultiplayerBattleStatistics()
    host = actor(2, cid='char_4016_kazema')
    token = actor(2, uid=299, cid='token_10022_kazema_shadow', is_token=True,
                  host_key=host.cid, host_uid=host.unique_id)
    entries = [host, token, actor(3, cid=host.cid)]
    consume(meter, 0, entries)
    good = hit(1, 2, 1, 25, uid=299, cid=token.cid)
    missing = hit(2, 2, 2, 500, uid=299, cid=token.cid)
    missing.pop('source_character_uid')
    value = consume(meter, 3, entries, [good, missing])
    assert player(value, 2)['observed_damage'] == 25
    assert player(value, 3)['observed_damage'] is None
    assert value['unknown_attribution'][0]['reason'] == 'source_uid_missing'
    assert player(value, 2)['coverage_complete'] is False
    assert list(player(value, 2)['statistics']['rows']) == [f'{host.cid}#201']


@pytest.mark.parametrize('problem', ['cid_mismatch', 'uid_conflict', 'host_conflict'])
def test_conflicting_or_mismatched_uid_is_not_guessed_by_character_name(problem):
    meter = MultiplayerBattleStatistics()
    entries = [actor(2), actor(3)]
    event = hit(1, 2, 1, 500)
    if problem == 'cid_mismatch':
        event['source'] = 'char_140_whitew'
    elif problem == 'uid_conflict':
        entries.append(actor(3, uid=201))
    else:
        token = actor(2, uid=299, cid='token_10022_kazema_shadow', is_token=True,
                      host_key=CID, host_uid=301)
        entries.append(token)
        event.update(source=token.cid, source_character_uid=299)
    consume(meter, 0, entries)
    value = consume(meter, 2, entries, [event])
    assert all(p['observed_damage'] is None for p in value['players'])
    assert len(value['unknown_attribution']) == 1


@pytest.mark.parametrize('kind', ['skill_start', 'skill_end'])
def test_missing_skill_actor_uid_preserves_uncertainty_without_charging_a_cid_peer(kind):
    meter = MultiplayerBattleStatistics()
    other = 'char_140_whitew'
    entries = [actor(2), actor(3), actor(4, cid=other)]
    consume(meter, 0, entries)
    uncertain = dict(hit(1, 2, 1, 0), kind=kind, skill_key='skill_test')
    uncertain.pop('source_character_uid')
    value = consume(meter, 4, entries, [uncertain,
        hit(2, 2, 2), hit(3, 3, 3), hit(4, 4, 4, cid=other)])
    assert value['status'] == 'observing'
    assert [player(value, side)['observed_damage'] for side in (2, 3, 4)] == [100, 100, 100]
    for side in (2, 3):
        lane = player(value, side)['actor_ledgers'][0]['ledger']
        assert lane['skill_split_complete'] is False
        assert lane['skill_damage_unverified_share'] == 1
        assert lane['observed_non_skill_damage'] is None
    assert player(value, 4)['actor_ledgers'][0]['ledger']['skill_split_complete'] is True


def test_deferred_result_matches_eager_output_through_gap_identity_reset_and_finish():
    eager, deferred = MultiplayerBattleStatistics(), MultiplayerBattleStatistics()
    entries = [actor(2), actor(3)]
    cases = [(sample(0), entries, 0, []),
             (sample(2), entries, 1, [hit(1, 2, 1)]),
             (sample(3), entries, 3, [hit(3, 3, 3)]),  # Missing seq2 retains the prefix.
             (sample(0, session='round-2'), [actor(2, uid=222)], 3, []),
             (sample(3, session='round-2'), [actor(2, uid=222)], 4,
              [hit(4, 2, 2, 50, uid=222)])]
    for value, actors, sequence, events in cases:
        roster = RosterSnapshot(value['frame'], tuple(actors), local_player_side=3)
        batch = dict(events=events, **QUALITY)
        expected = eager.consume(value, roster, sequence, batch, mark=QUALITY)
        assert deferred.consume(value, roster, sequence, batch, mark=QUALITY, return_result=False) is None
        assert deferred.result() == expected
    assert deferred.finish(3, complete=True) == eager.finish(3, complete=True)


def test_no_output_consumption_does_not_materialize_a_player_snapshot(monkeypatch):
    meter = MultiplayerBattleStatistics()
    entries = (actor(2), actor(3))

    def unwanted_snapshot():
        raise AssertionError('no-output event consumption must not build a result')

    with monkeypatch.context() as guarded:
        guarded.setattr(meter, 'result', unwanted_snapshot)
        for when, seq, events in ((0, 0, []), (2, 1, [hit(1, 2, 1)])):
            value = sample(when)
            assert meter.consume(value, RosterSnapshot(value['frame'], entries), seq,
                dict(events=events, **QUALITY), mark=QUALITY, return_result=False) is None
    assert player(meter.result(), 2)['observed_damage'] == 100


def test_exact_replay_is_ignored_conflict_or_missing_tail_closes_coverage():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2)]
    consume(meter, 0, entries)
    event = hit(1, 2, 1)
    value = consume(meter, 2, entries, [event, event])
    assert player(value, 2)['observed_damage'] == 100
    assert player(consume(meter, 2, entries, [event]), 2)['observed_damage'] == 100
    conflict = dict(event, amount=900)
    value = consume(meter, 3, entries, [conflict])
    assert value['status'] == 'gap'
    assert player(value, 2)['observed_damage'] == 100
    assert player(value, 2)['coverage_complete'] is False
    assert player(consume(meter, 5, entries, [hit(2, 2, 4)]), 2)['observed_damage'] == 100


@pytest.mark.parametrize('events, through', [([hit(2, 2, 2)], 2),
    ([hit(2, 2, 2), hit(1, 2, 1)], 2), ([hit(1, 2, 1)], 2),
    ([hit(1, 2, 3), hit(2, 2, 2)], 2)])
def test_invalid_batch_keeps_real_failure_and_does_not_partially_add(events, through):
    meter = MultiplayerBattleStatistics()
    entries = [actor(2)]
    consume(meter, 0, entries)
    value = consume(meter, 4, entries, events, sequence=through)
    assert value['status'] == 'gap'
    assert player(value, 2)['observed_damage'] is None


def test_short_read_gap_and_uncertain_terminal_keep_the_prefix():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2)]
    consume(meter, 0, entries)
    consume(meter, 2, entries, [hit(1, 2, 1, 100)])
    value = meter.consume(sample(3), RosterSnapshot(999, tuple(entries)), 1, {'events': []})
    assert value['status'] == 'gap'
    assert player(value, 2)['observed_damage'] == 100
    final = meter.finish(9, complete=True)
    assert final['game_time'] == 2
    assert player(final, 2)['observed_dps'] == 50
    assert player(final, 2)['coverage_complete'] is False


def test_new_session_clock_reset_and_full_roster_replacement_never_mix_rounds():
    meter = MultiplayerBattleStatistics()
    old = [actor(2), actor(3)]
    consume(meter, 0, old)
    consume(meter, 10, old, [hit(1, 3, 3, 100)])
    # Same generation, new roster UIDs and side numbers at game second 115.
    new = [actor(1, 111), actor(2, 222)]
    value = consume(meter, 115, new, sequence=1, local=2)
    assert value['identity_epoch'] == 1
    assert len(value['previous_rounds']) == 1
    assert player(value, 3)['observed_damage'] is None
    value = consume(meter, 120, new, [hit(2, 1, 117, 50, uid=111)], local=2)
    assert player(value, 1)['observed_window'] == [115, 120]
    assert player(value, 1)['observed_dps'] == 10
    assert player(value, 1)['coverage_complete'] is False
    assert player(value['previous_rounds'][0], 3)['observed_damage'] == 100
    reset = consume(meter, 0, new, sequence=2)
    assert reset['identity_epoch'] == 2
    assert player(reset, 1)['observed_damage'] is None
    next_round = consume(meter, 0, old, session='round-2', sequence=0)
    assert next_round['identity_epoch'] == 3
    consume(meter, 2, old, [hit(1, 2, 1, 20)], session='round-2')
    stale = consume(meter, 121, new, [hit(3, 1, 121, 999, uid=111)])
    assert stale['source_round_id'] == 'round-2'
    assert player(stale, 2)['observed_damage'] == 20


def test_partial_roster_change_summons_and_retreat_do_not_reset_identity():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2), actor(3)]
    consume(meter, 0, entries)
    consume(meter, 2, entries, [hit(1, 2, 1)])
    added = entries + [actor(2, uid=299, cid='char_140_whitew')]
    consume(meter, 3, added)
    consume(meter, 4, [added[-1], entries[0]])
    value = consume(meter, 5, [], [hit(2, 2, 5)])
    assert value['identity_epoch'] == 0
    assert player(value, 2)['observed_damage'] == 200
    assert value['previous_rounds'] == []


def test_side_reassignment_is_a_new_epoch_and_later_uid_conflict_marks_evidence_partial():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2), actor(3)]
    consume(meter, 0, entries)
    consume(meter, 2, entries, [hit(1, 2, 1)])
    reassigned = [actor(4, uid=201), actor(3)]
    value = consume(meter, 3, reassigned, sequence=1)
    assert value['identity_epoch'] == 1
    assert player(value, 2)['observed_damage'] is None
    value = consume(meter, 5, reassigned, [hit(2, 4, 4, 20, uid=201)])
    assert player(value, 4)['observed_damage'] == 20
    conflict = reassigned + [actor(4, uid=201, cid='char_140_whitew')]
    value = consume(meter, 6, conflict)
    assert '编队UID存在身份冲突' in value['reasons']
    assert player(value, 4)['coverage_complete'] is False
    meter.finish(6, complete=True)
    final = meter.result()
    meter.gap('later disconnect')
    assert meter.result() == final


def test_non_autochess_does_not_initialize_player_statistics():
    meter = MultiplayerBattleStatistics()
    assert meter.consume(dict(sample(0), battle_metadata={'stage_id': 'main_01-07'}),
                         RosterSnapshot(0), 0, {'events': []}) is None
    assert meter.result() is None


def test_missing_or_unsupported_version_does_not_enable_a_fictitious_v7_ledger():
    entries = [actor(2)]
    for version in (None, 2, 99, True):
        meter = MultiplayerBattleStatistics()
        quality = dict(QUALITY, partial_damage_version=version)
        roster = RosterSnapshot(0, tuple(entries), local_player_side=2)
        value = meter.consume(sample(0), roster, 0, dict(events=[], **quality), mark=quality)
        assert player(value, 2)['observed_damage'] is None
        assert meter._players[2].statistics.settlement_enabled is False
        assert '未取得支持的结算事件版本' in value['reasons']
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, entries)
    value = meter.consume(sample(2), RosterSnapshot(60, tuple(entries)), 1,
        dict(events=[hit(1, 2, 1)], partial_damage_version=6, **{
            k: v for k, v in QUALITY.items() if k != 'partial_damage_version'}), mark=QUALITY)
    assert value['status'] == 'gap'
    assert player(value, 2)['observed_damage'] is None


def test_absent_quality_fields_stay_partial_and_do_not_claim_coverage():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2)]
    quality = {'partial_damage_version': 7}
    meter.consume(sample(0), RosterSnapshot(0, tuple(entries)), 0, dict(events=[], **quality))
    value = meter.consume(sample(2), RosterSnapshot(60, tuple(entries)), 1,
                          dict(events=[hit(1, 2, 1)], **quality))
    assert player(value, 2)['observed_damage'] == 100
    assert player(value, 2)['coverage_complete'] is False
    assert '结算事件质量未完整确认' in value['reasons']


def test_retired_identity_summaries_are_bounded_and_omit_core_journals():
    meter = MultiplayerBattleStatistics()
    for index in range(12):
        entries = [actor(2, uid=200 + index)]
        consume(meter, index * 10, entries, sequence=0)
    value = meter.result()
    assert len(value['previous_rounds']) == 8
    assert value['previous_rounds_pruned'] == 3
    assert all('statistics' not in p and 'actor_ledgers' not in p
               for old in value['previous_rounds'] for p in old['players'])


def test_native_session_change_and_stale_old_native_batch_cannot_reopen_old_scope():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2)]
    consume(meter, 0, entries, native_session='native-a')
    consume(meter, 3, entries, [hit(1, 2, 1)], native_session='native-a')
    value = consume(meter, 3, entries, sequence=1, native_session='native-b')
    assert value['identity_epoch'] == 1
    value = consume(meter, 4, entries, [hit(2, 2, 4, 50)], native_session='native-b')
    assert player(value, 2)['observed_damage'] == 50
    stale = consume(meter, 5, entries, [hit(3, 2, 5, 999)], native_session='native-a')
    assert stale['identity_epoch'] == 1
    assert player(stale, 2)['observed_damage'] == 50


def elemental_bar(seq, side, when, target_uid='500'):
    return dict(sequence=seq, frame=round(when * 30), game_time=when, kind='elemental_bar',
        source=CID, source_character_uid=100 * side + 1, amount=100,
        target_uid=target_uid, target_instance=f'0x{target_uid}')


def elemental_hit(seq, when, target_uid='500'):
    event = hit(seq, 2, when, amount=100)
    event.update(source=None, damage_type=5, target_uid=target_uid, target_instance=f'0x{target_uid}')
    event.pop('source_character_uid')
    return event


def test_sourceless_elemental_settlement_reuses_verified_bar_player_and_actor():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2), actor(3)]
    consume(meter, 0, entries)
    value = consume(meter, 3, entries, [elemental_bar(1, 2, 1), elemental_hit(2, 2)])
    assert player(value, 2)['observed_damage'] == 100
    assert player(value, 3)['observed_damage'] is None
    assert value['unknown_attribution'] == []
    assert player(value, 2)['coverage_complete'] is True


def test_foreign_elemental_bar_overwrites_old_local_witness_in_each_context():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2), actor(3)]
    consume(meter, 0, entries)
    value = consume(meter, 4, entries,
        [elemental_bar(1, 2, 1), elemental_bar(2, 3, 2), elemental_hit(3, 3)])
    assert player(value, 2)['observed_damage'] is None
    assert player(value, 3)['observed_damage'] == 100
    assert value['unknown_attribution'] == []


def test_unwitnessed_sourceless_elemental_damage_is_not_invented_or_carried_across_epoch():
    meter = MultiplayerBattleStatistics()
    entries = [actor(2), actor(3)]
    consume(meter, 0, entries)
    consume(meter, 2, entries, [elemental_bar(1, 2, 1)])
    new_entries = [actor(2, 222), actor(3, 333)]
    consume(meter, 10, new_entries, sequence=1)
    value = consume(meter, 12, new_entries, [elemental_hit(2, 11)])
    assert all(p['observed_damage'] is None for p in value['players'])
    assert value['unknown_attribution'][0]['reason'] == 'elemental_owner_unverified'


def test_real_frozen_generation12_keeps_two_grey_throats_and_unobserved_slot_unknown():
    fixture_path = Path(__file__).parent / 'fixtures' / 'autochess_generation12.json.gz'
    fixture = json.loads(gzip.decompress(fixture_path.read_bytes()))
    meter = MultiplayerBattleStatistics()
    for frame in fixture['frames']:
        raw = frame['roster']
        roster = RosterSnapshot(raw['frame'], tuple(CharacterEntry(**e) for e in raw['entries']),
            frozenset(raw['present_unique_ids']), raw['present_known'], raw['local_player_side'])
        value = meter.consume(frame['sample'], roster, frame['sequence'],
                              dict(events=frame['events'], **frame['quality']), mark=frame['quality'])
        assert value['status'] == 'observing', value['reasons']
    assert value['observed_player_sides'] == [2, 3, 4]
    assert player(value, 1)['observed_damage'] is None
    assert [player(value, side)['observed_damage'] for side in (2, 3, 4)] == pytest.approx(
        [97707.40503488178, 74063.3021427358, 101973.14395464887])
    expected = {3: (3145802, 72, 17488.800107479095), 4: (4194395, 100, 29497.00003683567)}
    for side, (uid, hits, nominal) in expected.items():
        grey, = [a for a in player(value, side)['actor_ledgers'] if a['cid'] == CID]
        assert grey['source_character_uid'] == uid
        assert grey['ledger']['observed_hits'] == hits
        assert grey['ledger']['observed_damage'] == pytest.approx(nominal)
    assert value['identity_epoch'] == 0
