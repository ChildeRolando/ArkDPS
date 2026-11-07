import pytest

from roster import CharacterEntry, RosterSnapshot
from snapshot_adapter import (BattleStatisticsAdapter, MappingUnavailable,
                              build_owner_map)


def entry(cid, uid, *, token=False, host_key=None, host_uid=None, predefined=False):
    return CharacterEntry(cid=cid, unique_id=uid, is_token=token,
                          is_predefined=predefined, host_key=host_key,
                          host_uid=host_uid, player_side=0)


def roster(entries, present=(), known=True, frame=1):
    return RosterSnapshot(frame=frame, entries=tuple(entries),
                          present_unique_ids=frozenset(present),
                          present_known=known)


def row(cid, damage=0.0, healing=0.0, *, physical=0.0, arts=0.0,
        true=0.0, elemental=0.0, name=None):
    return {'id': cid, 'name': name or cid, 'damage': damage, 'healing': healing,
            'physical': physical, 'arts': arts, 'true': true,
            'elemental': elemental, 'raw_output': damage}


def sample(rows, game_time, frame, session='battle-1'):
    return {'session': session, 'frame': frame, 'game_time': game_time,
            'rows': list(rows)}


CHAR_A = entry('char_a', 1)
CHAR_B = entry('char_b', 2)
BASE = roster([CHAR_A, CHAR_B], present=(1,), frame=10)
OTTER = entry('char_242_otter', 11)
SKADI = entry('char_263_skadi', 22)
SUMMON_ROSTER = roster([OTTER, SKADI], present=(11,), frame=10)


def test_mid_battle_baseline_starts_short_window_immediately():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 500, physical=1000)], 30, 1), BASE)
    update = a.update(sample([row('char_a', 1230, 615, physical=1230)], 32.3, 2), BASE)
    r = a.result(BASE)['rows']['char_a']
    assert r['damage'] == 1230
    assert r['healing'] == 615
    assert r['recent_damage'] == pytest.approx(100)
    assert r['recent_healing'] == pytest.approx(50)
    assert r['coverage_start'] == 30
    assert update.game_time == 32.3


def test_four_damage_types_and_overkill_totals_are_kept():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100, 0, physical=10, arts=20, true=30,
                         elemental=40)], 1, 1), BASE)
    r = a.result(BASE)['rows']['char_a']
    assert r['damage_by_type'] == {'physical': 10, 'arts': 20,
                                   'true': 30, 'elemental': 40}
    assert r['damage'] == 100
    assert r['types_available'] is True
    assert r['types_stale'] is False


def test_snapshots_replace_totals_and_duplicate_frames_are_ignored():
    a = BattleStatisticsAdapter()
    assert a.update(sample([row('char_a', 1000, 0)], 0, 1), BASE) is not None
    assert a.update(sample([row('char_a', 1000, 0)], 0, 1), BASE) is None
    a.update(sample([row('char_a', 1230, 0)], 1, 2), BASE)
    a.update(sample([row('char_a', 1260, 0)], 2, 3), BASE)
    assert a.result(BASE)['rows']['char_a']['damage'] == 1260


def test_pause_does_not_fabricate_a_rate_or_change_totals():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 0)], 0, 1), BASE)
    a.update(sample([row('char_a', 1100, 0)], 1, 2), BASE)
    before = a.result(BASE)['rows']['char_a']
    assert a.update(sample([row('char_a', 1100, 0)], 1, 3), BASE) is None
    after = a.result(BASE)['rows']['char_a']
    assert after['damage'] == before['damage'] == 1100
    assert after['recent_damage'] == before['recent_damage']


def test_summon_shared_key_is_counted_once_and_attributed_to_host():
    r = roster([OTTER, SKADI,
                entry('token_otter', 12, token=True,
                      host_key='char_242_otter', host_uid=11),
                entry('token_otter', 12, token=True,
                      host_key='char_242_otter', host_uid=11)],
               present=(11, 12))
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_242_otter', 100, 0),
                     row('token_otter', 500, 0)], 0, 1), r)
    result = a.result(r)
    host = result['rows']['char_242_otter']
    assert host['damage'] == 600
    assert host['sources'] == ['char_242_otter', 'token_otter']
    assert 'token_otter' not in result['rows']
    a.update(sample([row('char_242_otter', 100, 0),
                     row('token_otter', 560, 0)], 1, 2), r)
    assert a.result(r)['rows']['char_242_otter']['damage'] == 660


def test_unmapped_token_is_excluded_not_guessed():
    orphan = entry('token_otter', 12, token=True,
                   host_key='char_242_otter', host_uid=999)
    r = roster([OTTER, orphan], present=(11, 12))
    a = BattleStatisticsAdapter()
    update = a.update(sample([row('char_242_otter', 100, 0),
                              row('token_otter', 500, 0)], 0, 1), r)
    result = a.result(r)
    assert result['rows']['char_242_otter']['damage'] == 100
    assert 'token_otter' not in result['rows']
    assert 'token_otter' in update.unmapped_tokens
    assert 'token_otter' in result['unknown_sources']


def test_unknown_source_is_not_guessed_but_does_not_hide_known_rows():
    a = BattleStatisticsAdapter()
    update = a.update(sample([row('char_a', 100, 0), row('mystery', 999, 0)], 0, 1),
                      BASE)
    result = a.result(BASE)
    assert result['rows']['char_a']['damage'] == 100
    assert 'mystery' not in result['rows']
    assert 'mystery' in update.unknown_sources


def test_only_unknown_rows_fail_closed():
    a = BattleStatisticsAdapter()
    with pytest.raises(MappingUnavailable):
        a.update(sample([row('mystery', 999, 0)], 0, 1), BASE)


def test_no_roster_mapping_fails_closed():
    a = BattleStatisticsAdapter()
    with pytest.raises(MappingUnavailable):
        a.update(sample([row('char_a', 100, 0)], 0, 1), roster([]))


def test_missing_fields_are_excluded_and_not_zeroed():
    a = BattleStatisticsAdapter()
    broken = {'id': 'char_a', 'name': 'a', 'healing': 0}
    update = a.update(sample([broken, row('char_b', 50, 0)], 0, 1), BASE)
    assert 'char_a' in update.missing_fields
    result = a.result(BASE)
    assert 'char_a' not in result['rows']
    assert result['rows']['char_b']['damage'] == 50


def test_all_rows_missing_fields_fail_closed():
    a = BattleStatisticsAdapter()
    with pytest.raises(MappingUnavailable):
        a.update(sample([{'id': 'char_a', 'name': 'a'}], 0, 1), BASE)


def test_gap_keeps_totals_but_drops_rates():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 0)], 0, 1), BASE)
    a.update(sample([row('char_a', 1100, 0)], 1, 2), BASE)
    assert a.result(BASE)['rows']['char_a']['recent_damage'] is not None
    a.on_gap()
    a.update(sample([row('char_a', 1200, 0)], 2, 3), BASE)
    r = a.result(BASE)['rows']['char_a']
    assert r['damage'] == 1200
    assert r['recent_damage'] is None


def test_presence_is_reported_per_owner_and_retired_rows_keep_totals():
    r = roster([CHAR_A, CHAR_B], present=(1,))
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100, 0),
                     row('char_b', 200, 0)], 0, 1), r)
    result = a.result(r)
    assert result['rows']['char_a']['present'] is True
    assert result['rows']['char_b']['present'] is False
    assert result['rows']['char_b']['damage'] == 200
    assert result['present_known'] is True


def test_unknown_presence_ids_are_reported():
    r = roster([CHAR_A], present=(1, 777))
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100, 0)], 0, 1), r)
    assert a.result(r)['unknown_present_unique_ids'] == [777]


def test_presence_unknown_when_live_list_unavailable():
    r = roster([CHAR_A], present=(), known=False)
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100, 0)], 0, 1), r)
    result = a.result(r)
    assert result['rows']['char_a']['present'] is None
    assert result['present_known'] is False


def test_new_session_resets_state_and_counts_switch():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 0)], 0, 1, session='b1'), BASE)
    a.update(sample([row('char_a', 50, 0)], 0, 1, session='b2'), BASE)
    result = a.result(BASE)
    assert a.session_switches == 2
    assert result['rows']['char_a']['damage'] == 50
    assert result['rows']['char_a']['coverage_start'] == 0


def test_backwards_clock_starts_a_new_observation_not_a_mixed_series():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 0)], 10, 1), BASE)
    a.update(sample([row('char_a', 500, 0)], 5, 2), BASE)
    result = a.result(BASE)
    assert result['rows']['char_a']['damage'] == 500
    assert a.session_switches == 2


def test_existing_mapping_cannot_be_silently_reassigned():
    token = entry('token_otter', 12, token=True,
                  host_key='char_242_otter', host_uid=11)
    first = roster([OTTER, SKADI, token], present=(11,))
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_242_otter', 100, 0),
                     row('token_otter', 500, 0)], 0, 1), first)
    assert a.result(first)['rows']['char_242_otter']['damage'] == 600
    # Later evidence cannot move an already registered key.
    changed = roster([OTTER, SKADI,
                      entry('token_otter', 12, token=True,
                            host_key='char_263_skadi', host_uid=22)], present=(11,))
    a.update(sample([row('char_242_otter', 100, 0),
                     row('token_otter', 500, 0)], 1, 2), changed)
    result = a.result(changed)
    assert result['rows']['char_242_otter']['damage'] == 600
    assert 'token_otter' in result['mapping_conflicts']


def test_owner_map_requires_host_key_and_uid_to_agree():
    wrong_key = entry('token_otter', 12, token=True,
                      host_key='char_263_skadi', host_uid=11)
    mapping = build_owner_map(roster([OTTER, SKADI, wrong_key]))
    assert 'token_otter' not in mapping.owners
    assert 'token_otter' in mapping.unknown_token_cids


def test_predefined_and_assisted_units_keep_their_own_identity():
    npc = entry('enemy_npc', 99, predefined=True)
    assisted = entry('char_180_amgoat', 55)
    r = roster([CHAR_A, npc, assisted], present=(1, 99, 55))
    a = BattleStatisticsAdapter()
    a.update(sample([row('enemy_npc', 40, 0),
                     row('char_180_amgoat', 70, 0)], 0, 1), r)
    result = a.result(r)
    assert result['rows']['enemy_npc']['damage'] == 40
    assert result['rows']['char_180_amgoat']['damage'] == 70
