from dataclasses import replace
import pytest

from roster import CharacterEntry, RosterSnapshot
from snapshot_adapter import (BattleStatisticsAdapter, MappingUnavailable, FrameMismatch,
                              build_owner_map)


def entry(cid, uid, *, token=False, host_key=None, host_uid=None, predefined=False):
    return CharacterEntry(cid=cid, unique_id=uid, is_token=token,
                          is_predefined=predefined, host_key=host_key,
                          host_uid=host_uid, player_side=0)


def roster(entries, present=(), known=True, frame=1):
    return RosterSnapshot(frame=frame, entries=tuple(entries),
                          present_unique_ids=frozenset(present),
                          present_known=known)


def paired(value, frame):
    return replace(value, frame=frame)


def row(cid, damage=0.0, healing=0.0, *, physical=0.0, arts=0.0,
        true=0.0, elemental=0.0, name=None):
    return {'id': cid, 'name': name or cid, 'damage': damage, 'healing': healing,
            'physical': physical, 'arts': arts, 'true': true,
            'elemental': elemental, 'raw_output': damage}


def sample(rows, game_time, frame, session='battle-1'):
    return {'session': session, 'frame': frame, 'game_time': game_time,
            'rows': list(rows)}


@pytest.mark.parametrize('present', [(), (1,)])
def test_mismatched_frame_rejected_without_mutating_session_or_presence(present):
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100)], 0, 10), roster([CHAR_A], (1,), frame=10))
    before = adapter.result()
    with pytest.raises(FrameMismatch):
        adapter.update(sample([row('char_a', 200)], 1, 11, 'new'),
                       roster([CHAR_A], present, frame=10))
    assert adapter.session == 'battle-1'
    assert adapter.session_switches == 1
    assert adapter.result() == before
    adapter.update(sample([row('char_a', 200)], 1, 11),
                   roster([CHAR_A], present, frame=11))
    assert adapter.result()['rows']['char_a']['present'] == bool(present)


def test_mismatched_frame_cannot_change_summon_host():
    token = entry('token_a', 3, token=True, host_key='char_a', host_uid=1)
    changed = entry('token_a', 3, token=True, host_key='char_b', host_uid=2)
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100), row('token_a', 50)], 0, 1),
                   roster([CHAR_A, CHAR_B, token], frame=1))
    before = adapter.result()
    with pytest.raises(FrameMismatch):
        adapter.update(sample([row('char_a', 100), row('token_a', 500)], 1, 2),
                       roster([CHAR_A, CHAR_B, changed], frame=1))
    assert adapter.result() == before


@pytest.mark.parametrize('frame', [None, 2])
def test_first_snapshot_requires_matching_frame(frame):
    adapter = BattleStatisticsAdapter()
    with pytest.raises(FrameMismatch):
        adapter.update(sample([row('char_a', 100)], 0, frame),
                       roster([CHAR_A], frame=1))
    assert not adapter.active
    assert adapter.session_switches == 0


CHAR_A = entry('char_a', 1)
CHAR_B = entry('char_b', 2)
BASE = roster([CHAR_A, CHAR_B], present=(1,), frame=10)
OTTER = entry('char_242_otter', 11)
SKADI = entry('char_263_skadi', 22)
SUMMON_ROSTER = roster([OTTER, SKADI], present=(11,), frame=10)


def test_shared_prefab_for_player_and_map_character_stays_ambiguous():
    map_a = entry('char_a', 9, predefined=True)
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100), row('char_b', 20)], 0, 1),
                   roster([CHAR_A, CHAR_B, map_a], present=(9, 2)))
    adapter.update(sample([row('char_a', 140), row('char_b', 30)], 1, 2),
                   roster([CHAR_A, CHAR_B], present=(1, 2), frame=2))
    result = adapter.result()
    assert result['mapping_conflicts'] == ['char_a']
    assert 'char_a' not in result['rows']
    assert result['rows']['char_b']['damage'] == 30


def test_mid_battle_baseline_starts_short_window_immediately():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 500, physical=1000)], 30, 1), paired(BASE, 1))
    update = a.update(sample([row('char_a', 1230, 615, physical=1230)], 32.3, 2), paired(BASE, 2))
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
                         elemental=40)], 1, 1), paired(BASE, 1))
    r = a.result(BASE)['rows']['char_a']
    assert r['damage_by_type'] == {'physical': 10, 'arts': 20,
                                   'true': 30, 'elemental': 40}
    assert r['damage'] == 100
    assert r['types_available'] is True
    assert r['types_stale'] is False


def test_snapshots_replace_totals_and_duplicate_frames_are_ignored():
    a = BattleStatisticsAdapter()
    assert a.update(sample([row('char_a', 1000, 0)], 0, 1), paired(BASE, 1)) is not None
    assert a.update(sample([row('char_a', 1000, 0)], 0, 1), paired(BASE, 1)) is None
    a.update(sample([row('char_a', 1230, 0)], 1, 2), paired(BASE, 2))
    a.update(sample([row('char_a', 1260, 0)], 2, 3), paired(BASE, 3))
    assert a.result(BASE)['rows']['char_a']['damage'] == 1260


def test_pause_does_not_fabricate_a_rate_or_change_totals():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 0)], 0, 1), paired(BASE, 1))
    a.update(sample([row('char_a', 1100, 0)], 1, 2), paired(BASE, 2))
    before = a.result(BASE)['rows']['char_a']
    assert a.update(sample([row('char_a', 1100, 0)], 1, 3), paired(BASE, 3)) is None
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
                     row('token_otter', 500, 0)], 0, 1), paired(r, 1))
    result = a.result(r)
    host = result['rows']['char_242_otter']
    assert host['damage'] == 600
    assert host['sources'] == ['char_242_otter', 'token_otter']
    assert 'token_otter' not in result['rows']
    a.update(sample([row('char_242_otter', 100, 0),
                     row('token_otter', 560, 0)], 1, 2), paired(r, 2))
    assert a.result(r)['rows']['char_242_otter']['damage'] == 660


def test_unmapped_token_is_excluded_not_guessed():
    orphan = entry('token_otter', 12, token=True,
                   host_key='char_242_otter', host_uid=999)
    r = roster([OTTER, orphan], present=(11, 12))
    a = BattleStatisticsAdapter()
    update = a.update(sample([row('char_242_otter', 100, 0),
                              row('token_otter', 500, 0)], 0, 1), paired(r, 1))
    result = a.result(r)
    assert result['rows']['char_242_otter']['damage'] == 100
    assert 'token_otter' not in result['rows']
    assert 'token_otter' in update.unmapped_tokens
    assert 'token_otter' in result['unknown_sources']


def test_unknown_source_is_not_guessed_but_does_not_hide_known_rows():
    a = BattleStatisticsAdapter()
    update = a.update(sample([row('char_a', 100, 0), row('mystery', 999, 0)], 0, 1),
                      paired(BASE, 1))
    result = a.result(BASE)
    assert result['rows']['char_a']['damage'] == 100
    assert 'mystery' not in result['rows']
    assert 'mystery' in update.unknown_sources


def test_only_unknown_rows_fail_closed():
    a = BattleStatisticsAdapter()
    with pytest.raises(MappingUnavailable):
        a.update(sample([row('mystery', 999, 0)], 0, 1), paired(BASE, 1))


def test_no_roster_mapping_fails_closed():
    a = BattleStatisticsAdapter()
    with pytest.raises(MappingUnavailable):
        a.update(sample([row('char_a', 100, 0)], 0, 1), paired(roster([]), 1))


def test_missing_fields_are_excluded_and_not_zeroed():
    a = BattleStatisticsAdapter()
    broken = {'id': 'char_a', 'name': 'a', 'healing': 0}
    update = a.update(sample([broken, row('char_b', 50, 0)], 0, 1), paired(BASE, 1))
    assert 'char_a' in update.missing_fields
    result = a.result(BASE)
    assert 'char_a' not in result['rows']
    assert result['rows']['char_b']['damage'] == 50


def test_all_rows_missing_fields_fail_closed():
    a = BattleStatisticsAdapter()
    with pytest.raises(MappingUnavailable):
        a.update(sample([{'id': 'char_a', 'name': 'a'}], 0, 1), paired(BASE, 1))


def test_gap_keeps_totals_but_drops_rates():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 0)], 0, 1), paired(BASE, 1))
    a.update(sample([row('char_a', 1100, 0)], 1, 2), paired(BASE, 2))
    assert a.result(BASE)['rows']['char_a']['recent_damage'] is not None
    a.on_gap()
    a.update(sample([row('char_a', 1200, 0)], 2, 3), paired(BASE, 3))
    r = a.result(BASE)['rows']['char_a']
    assert r['damage'] == 1200
    assert r['recent_damage'] is None


def test_presence_is_reported_per_owner_and_retired_rows_keep_totals():
    r = roster([CHAR_A, CHAR_B], present=(1,))
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100, 0),
                     row('char_b', 200, 0)], 0, 1), paired(r, 1))
    result = a.result(r)
    assert result['rows']['char_a']['present'] is True
    assert result['rows']['char_b']['present'] is False
    assert result['rows']['char_b']['damage'] == 200
    assert result['present_known'] is True


def test_unknown_presence_ids_are_reported():
    r = roster([CHAR_A], present=(1, 777))
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100, 0)], 0, 1), paired(r, 1))
    assert a.result(r)['unknown_present_unique_ids'] == [777]


def test_presence_unknown_when_live_list_unavailable():
    r = roster([CHAR_A], present=(), known=False)
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100, 0)], 0, 1), paired(r, 1))
    result = a.result(r)
    assert result['rows']['char_a']['present'] is None
    assert result['present_known'] is False


def test_new_session_resets_state_and_counts_switch():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 0)], 0, 1, session='b1'), paired(BASE, 1))
    a.update(sample([row('char_a', 50, 0)], 0, 1, session='b2'), paired(BASE, 1))
    result = a.result(BASE)
    assert a.session_switches == 2
    assert result['rows']['char_a']['damage'] == 50
    assert result['rows']['char_a']['coverage_start'] == 0


def test_backwards_clock_starts_a_new_observation_not_a_mixed_series():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000, 0)], 10, 1), paired(BASE, 1))
    a.update(sample([row('char_a', 500, 0)], 5, 2), paired(BASE, 2))
    result = a.result(BASE)
    assert result['rows']['char_a']['damage'] == 500
    assert a.session_switches == 2


def test_clock_reset_is_detected_even_when_frame_number_is_reused():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000)], 10, 1), paired(BASE, 1))
    a.update(sample([row('char_a', 50)], 1, 1), paired(BASE, 1))
    assert a.session_switches == 2
    assert a.result()['rows']['char_a']['damage'] == 50


def test_existing_mapping_cannot_be_silently_reassigned():
    token = entry('token_otter', 12, token=True,
                  host_key='char_242_otter', host_uid=11)
    first = roster([OTTER, SKADI, token], present=(11,))
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_242_otter', 100, 0),
                     row('token_otter', 500, 0)], 0, 1), paired(first, 1))
    assert a.result(first)['rows']['char_242_otter']['damage'] == 600
    # Later evidence cannot move an already registered key.
    changed = roster([OTTER, SKADI,
                      entry('token_otter', 12, token=True,
                            host_key='char_263_skadi', host_uid=22)], present=(11,))
    a.update(sample([row('char_242_otter', 100, 0),
                     row('token_otter', 500, 0)], 1, 2), paired(changed, 2))
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
                     row('char_180_amgoat', 70, 0)], 0, 1), paired(r, 1))
    result = a.result(r)
    assert result['rows']['enemy_npc']['damage'] == 40
    assert result['rows']['char_180_amgoat']['damage'] == 70


@pytest.mark.parametrize('reverse', [False, True])
def test_summon_types_sum_each_key_and_mark_partial_stale(reverse):
    token = entry('token_otter', 12, token=True, host_key=OTTER.cid, host_uid=11)
    r = roster([OTTER, token])
    a = BattleStatisticsAdapter()
    rows = [row(OTTER.cid, 10, physical=10), row(token.cid, 20, arts=20)]
    if reverse:
        rows.reverse()
    a.update(sample(rows, 0, 1), paired(r, 1))
    host = a.result()['rows'][OTTER.cid]
    assert host['damage_by_type'] == {'physical': 10, 'arts': 20, 'true': 0, 'elemental': 0}
    assert host['types_stale'] is False
    partial = row(token.cid, 30, arts=30)
    del partial['arts']
    a.update(sample([row(OTTER.cid, 15, physical=15), partial], 1, 2), paired(r, 2))
    host = a.result()['rows'][OTTER.cid]
    assert host['damage_by_type'] == {'physical': 15, 'arts': 20, 'true': 0, 'elemental': 0}
    assert host['types_stale'] is True


def test_default_result_uses_latest_presence_and_resets_on_new_session():
    a = BattleStatisticsAdapter()
    for i, (present, known, expected) in enumerate([((1,), True, True), ((), True, False), ((1,), True, True), ((), False, None)]):
        a.update(sample([row('char_a', 100)], i, i+1), paired(roster([CHAR_A], present, known), i+1))
        assert a.result()['rows']['char_a']['present'] is expected
    a.update(sample([row('char_a', 2)], 0, 1, 'new'), paired(roster([CHAR_A], (), True), 1))
    assert a.result()['rows']['char_a']['present'] is False


def test_gap_immediately_invalidates_rates_even_on_paused_frame():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100)], 0, 1), paired(BASE, 1))
    a.update(sample([row('char_a', 200)], 1, 2), paired(BASE, 2))
    a.on_gap()
    for _ in range(2):
        r = a.result()['rows']['char_a']
        assert r['damage'] == 200
        assert r['recent_damage'] is None
        assert r['counter_snapshot_complete'] is False
        assert a.update(sample([row('char_a', 200)], 1, 2), paired(BASE, 2)) is None


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('cached', [False, True])
def test_partial_unknown_shared_token_key_is_not_updated(reverse, cached):
    valid = entry('token_a', 2, token=True, host_key='char_a', host_uid=1)
    invalid = entry('token_a', 3, token=True, host_key='char_a', host_uid=999)
    a = BattleStatisticsAdapter()
    if cached:
        a.update(sample([row('char_a', 100), row('token_a', 50)], 0, 1), paired(roster([CHAR_A, valid]), 1))
    tokens = [valid, invalid]
    if reverse:
        tokens.reverse()
    r = roster([CHAR_A] + tokens)
    assert 'token_a' not in build_owner_map(r).owners
    a.update(sample([row('char_a', 100), row('token_a', 500)], 1 if cached else 0, 2), paired(r, 2))
    result = a.result()
    assert result['rows']['char_a']['damage'] == (150 if cached else 100)
    assert 'token_a' in result['unmapped_tokens']
    if cached:
        assert result['rows']['char_a']['counter_snapshot_complete'] is False


def test_recorded_summon_fixture_preserves_both_damage_types():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).parents[1] / 'acceptance/recovery/summon-roster-live.json').read_text(encoding='utf-8'))
    player = data['players'][0]
    entries = [entry(e['cid'], e['unique_id'], token=e['is_token'],
                     host_key=e['token_or_host_key'], host_uid=e['token_or_host_uid'])
               for e in player['characters'] + player['tokens']]
    a = BattleStatisticsAdapter()
    a.update(data['before'], paired(roster(entries), data['before']['frame']))
    result = a.result()['rows']['char_242_otter']
    assert result['damage_by_type']['physical'] == 1295
    assert result['damage_by_type']['arts'] == pytest.approx(374.3999938964844)
    assert result['damage'] == pytest.approx(1669.3999938964844)


def test_missing_initial_source_type_is_partial():
    token = entry('token_a', 2, token=True, host_key='char_a', host_uid=1)
    missing = row('token_a', 20)
    del missing['arts']
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 10, physical=10), missing], 0, 1), paired(roster([CHAR_A, token]), 1))
    r = a.result()['rows']['char_a']
    assert r['damage_by_type']['physical'] == 10
    assert r['types_available'] is True
    assert r['types_stale'] is True


def test_changed_owner_evidence_does_not_increase_cached_source():
    valid = entry('token_a', 3, token=True, host_key='char_a', host_uid=1)
    changed = entry('token_a', 3, token=True, host_key='char_b', host_uid=2)
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100), row('token_a', 50)], 0, 1), paired(roster([CHAR_A, CHAR_B, valid]), 1))
    a.update(sample([row('char_a', 100), row('token_a', 500)], 1, 2), paired(roster([CHAR_A, CHAR_B, changed]), 2))
    r = a.result()
    assert r['rows']['char_a']['damage'] == 150
    assert r['rows']['char_a']['counter_snapshot_complete'] is False
    assert 'token_a' in r['mapping_conflicts']
