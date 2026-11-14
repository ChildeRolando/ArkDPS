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


def test_event_only_gap_keeps_presence_for_full_cycle_dps():
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 0)], 0, 1),
                   roster([CHAR_A], present=(1,), frame=1))
    adapter.update(sample([row('char_a', 100)], 3, 3),
                   roster([CHAR_A], present=(1,), frame=3))
    before = adapter.result()['rows']['char_a']
    assert before['presence_complete'] is True and before['presence_seconds'] == 3
    adapter.on_event_gap()
    after = adapter.result()['rows']['char_a']
    assert after['presence_complete'] is True and after['presence_seconds'] == 3


def test_full_cycle_presence_accumulates_only_while_present_and_gap_invalidates():
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 0), row('char_b', 0)], 0, 1),
                   roster([CHAR_A, CHAR_B], present=(1,), frame=1))
    adapter.update(sample([row('char_a', 100), row('char_b', 0)], 3, 3),
                   roster([CHAR_A, CHAR_B], present=(1, 2), frame=3))
    adapter.update(sample([row('char_a', 100), row('char_b', 50)], 5, 5),
                   roster([CHAR_A, CHAR_B], present=(2,), frame=5))
    result = adapter.result(BASE)
    assert result['rows']['char_a']['presence_seconds'] == pytest.approx(5)
    assert result['rows']['char_b']['presence_seconds'] == pytest.approx(2)
    assert result['rows']['char_a']['presence_complete'] is True
    adapter.on_gap()
    result = adapter.result(BASE)
    assert result['rows']['char_a']['presence_seconds'] is None
    assert result['rows']['char_a']['presence_complete'] is False


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


def test_ordered_event_barrier_and_gap_do_not_change_native_totals():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000)], 30, 10), paired(BASE, 10))
    a.start_events(session='battle-1', frame=10, sequence=4)
    a.accept_event(session='battle-1', sequence=5, frame=10, game_time=30,
                   source='char_a', kind='damage', amount=20)
    a.update(sample([row('char_a', 1020)], 31, 11), paired(BASE, 11))
    a.on_gap()
    result = a.result()['rows']['char_a']
    assert result['damage'] == 1020
    assert result['observed_damage'] == 20
    assert result['event_coverage_end'] == 30
    assert not result['event_coverage_complete']


def test_event_stream_cannot_continue_after_mapping_conflict():
    token = entry('token_a', 3, token=True, host_key='char_a', host_uid=1)
    changed = entry('token_a', 3, token=True, host_key='char_b', host_uid=2)
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a'), row('token_a')], 0, 1),
             roster([CHAR_A, CHAR_B, token], frame=1))
    a.start_events(session='battle-1', frame=1, sequence=0)
    a.update(sample([row('char_a'), row('token_a')], 1, 2),
             roster([CHAR_A, CHAR_B, changed], frame=2))
    assert not a.result()['rows']['char_a']['event_coverage_complete']


def test_shield_only_summon_uses_roster_host_without_a_counter_row():
    token = entry('token_a', 3, token=True, host_key='char_a', host_uid=1)
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a')], 0, 1), roster([CHAR_A, token], frame=1))
    a.start_events(session='battle-1', frame=1, sequence=0)
    a.accept_event(session='battle-1', sequence=1, frame=1, game_time=0,
                   source='token_a', kind='shield_absorbed', amount=2)
    assert a.result()['rows']['char_a']['shield_absorbed'] == 2
    assert a.result()['rows']['char_a']['damage'] == 0


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


@pytest.mark.parametrize('reverse', [False, True])
def test_summon_healing_uses_the_hosts_display_name(reverse):
    host = entry('char_108_silent', 11)
    drone = entry('token_10000_silent_healrb', 12, token=True,
                  host_key=host.cid, host_uid=host.unique_id)
    r = roster([host, drone])
    a = BattleStatisticsAdapter()
    rows = [row(host.cid, healing=581, name='助战赫默'),
            row(drone.cid, healing=125, name='医疗无人机')]
    if reverse:
        rows.reverse()
    a.update(sample(rows, 0, 1), paired(r, 1))
    assert a.result()['rows'][host.cid]['name'] == '助战赫默'
    assert a.result()['rows'][host.cid]['healing'] == 706

    drone_only = [row(drone.cid, healing=2500, name='医疗无人机')]
    a.update(sample(drone_only, 1, 2), paired(r, 2))
    assert a.result()['rows'][host.cid]['name'] == '助战赫默'
    assert a.result()['rows'][host.cid]['healing'] == 3081

    a.update(sample(drone_only, 0, 3, session='battle-2'), paired(r, 3))
    result = a.result()['rows']
    assert result[host.cid]['name'] == '赫默'
    assert result[host.cid]['healing'] == 2500
    assert drone.cid not in result


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


def test_detached_summon_output_does_not_mark_its_departed_host_present():
    from panel_model import Category, PanelConfig, render_panel, rows_from_statistics
    token = entry('token_heal', 3, token=True, host_key='char_a', host_uid=1)
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a'), row('token_heal', healing=250)], 0, 1),
             roster([CHAR_A, token], present=(1, 3), frame=1))
    assert a.result()['rows']['char_a']['present'] is True
    a.update(sample([row('char_a'), row('token_heal', healing=375)], 1, 2),
             roster([CHAR_A, token], present=(3,), frame=2))
    result = a.result()
    owner = result['rows']['char_a']
    assert owner['healing'] == 375
    assert set(owner['sources']) == {'char_a', 'token_heal'}
    assert owner['present'] is False
    panel = render_panel(PanelConfig(category=Category.HEALING), rows_from_statistics(result))
    assert panel.entries[0].value == 375
    assert panel.entries[0].portrait_muted


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


def test_event_evidence_publishes_overkill_and_a_missing_observer_keeps_it_unknown():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 1000)], 30, 10), paired(BASE, 10))
    a.start_events(session='battle-1', frame=10, sequence=4)
    a.accept_event(session='battle-1', sequence=5, frame=10, game_time=30,
                   source='char_a', kind='damage', amount=200, target='enemy',
                   real_delta=-44, hp_before=44, hp_after=0, killed=True,
                   target_type=0, damage_type=1, shield_absorbed=0)
    a.update(sample([row('char_a', 1200)], 31, 11), paired(BASE, 11))
    result = a.result()
    assert result['rows']['char_a']['overkill_damage'] == 156
    assert result['rows']['char_a']['overkill_coverage_complete'] is True
    assert result['overkill_damage'] == 156
    assert result['rows']['char_a']['damage'] == 1200

    plain = BattleStatisticsAdapter()
    plain.update(sample([row('char_a', 1000)], 30, 10), paired(BASE, 10))
    assert plain.result()['rows']['char_a']['overkill_damage'] is None
    assert plain.result()['rows']['char_a']['overkill_coverage_complete'] is False
    assert plain.result()['overkill_damage'] is None


def test_known_map_trap_is_excluded_from_the_team_instead_of_blocking():
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100), row('trap_321_shnbox', 0)], 0, 1),
                   roster([CHAR_A], frame=1))
    result = adapter.result()
    assert result['unknown_sources'] == []
    assert result['non_team_sources'] == ['trap_321_shnbox']
    assert result['non_team_output'] == {'trap_321_shnbox': {'damage': 0.0, 'healing': 0.0}}
    assert result['totals_complete'] is True
    assert result['rows']['char_a']['counter_snapshot_complete'] is True
    assert 'trap_321_shnbox' not in result['rows']


def test_trap_output_is_named_but_never_attributed_or_zero_filled():
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100), row('trap_321_shnbox', 500)], 0, 1),
                   roster([CHAR_A], frame=1))
    result = adapter.result()
    assert result['rows']['char_a']['damage'] == 100
    assert result['non_team_output']['trap_321_shnbox']['damage'] == 500
    assert result['totals_complete'] is True


def test_unmapped_operator_namespace_still_blocks_team_totals():
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100), row('char_mystery', 50)], 0, 1),
                   roster([CHAR_A], frame=1))
    result = adapter.result()
    assert result['unknown_sources'] == ['char_mystery']
    assert result['non_team_sources'] == []
    assert result['totals_complete'] is False


def test_battle_end_keeps_final_totals_and_retires_recent_rates():
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100)], 0, 1), roster([CHAR_A], frame=1))
    adapter.update(sample([row('char_a', 200)], 1, 2), roster([CHAR_A], frame=2))
    adapter.on_end()
    result = adapter.result()
    assert result['ended'] is False
    assert result['totals_complete'] is True
    assert result['rows']['char_a']['counter_snapshot_complete'] is True
    assert result['rows']['char_a']['recent_damage'] is None
    finished = adapter.finish()
    assert finished['totals_complete'] is True
    assert finished['rows']['char_a']['recent_damage'] is None


def test_ambiguous_trap_key_is_not_reclassified_as_non_team():
    trap = entry('trap_321_shnbox', 7)
    trap_predefined = entry('trap_321_shnbox', 7, predefined=True)
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100), row('trap_321_shnbox', 0)], 0, 1),
                   roster([CHAR_A, trap, trap_predefined], frame=1))
    result = adapter.result()
    assert 'trap_321_shnbox' in result['ambiguous_sources']
    assert result['non_team_sources'] == []
    assert result['unknown_sources'] == ['trap_321_shnbox']
    assert result['totals_complete'] is False


def test_stage_trap_roster_token_is_non_team_and_does_not_block_events():
    trap = entry('trap_016_peon', 99, token=True)
    mapping = build_owner_map(roster([CHAR_A, trap], frame=1))
    assert 'trap_016_peon' not in mapping.unknown_token_cids
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100), row('trap_016_peon', 0)], 0, 1),
                   roster([CHAR_A, trap], frame=1))
    result = adapter.result()
    assert result['unknown_sources'] == []
    assert result['non_team_sources'] == ['trap_016_peon']
    assert result['non_team_output'] == {'trap_016_peon': {'damage': 0.0, 'healing': 0.0}}
    assert result['totals_complete'] is True
    adapter.start_events(session='battle-1', frame=1, sequence=0)


def test_supplemental_hp_evidence_from_unmapped_source_keeps_events_open():
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a')], 0, 1), roster([CHAR_A], frame=1))
    a.start_events(session='battle-1', frame=1, sequence=0)
    a.accept_event(session='battle-1', sequence=1, frame=1, game_time=0,
                   source='enemy_1', kind='hp_damage', amount=10,
                   target='char_a', hp_before=5, shield_absorbed=0)
    a.accept_event(session='battle-1', sequence=2, frame=1, game_time=0,
                   source='char_a', kind='damage', amount=5)
    result = a.result()['rows']['char_a']
    assert result['event_coverage_complete'] is True
    assert result['observed_damage'] == 5


def test_verified_character_uid_maps_alternate_counter_key_to_roster_form():
    form = entry('char_form', 7)
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a')], 0, 1), roster([CHAR_A, form]), source_attribution=True)
    a.start_events(session='battle-1', frame=1, sequence=0)
    event = dict(session='battle-1', sequence=1, frame=2, game_time=1, source='char_original',
                 source_character_uid=7, kind='damage', amount=40,
                 target='enemy_a', hp_before=100, hp_after=60, killed=False,
                 real_delta=-40, target_type=0, damage_type=1, cancel_reason=0, shield_absorbed=0,
                 mitigation_before=50, mitigation_after=40)
    a.update(sample([row('char_a'), row('char_original', 40, physical=40)], 1, 2),
             roster([CHAR_A, form], frame=2), source_events=[event])
    a.accept_event(**event)
    a.event_stream.watermark(session='battle-1', sequence=1, frame=2, game_time=1)
    result = a.result()
    assert result['totals_complete']
    assert result['rows']['char_form']['effective_damage'] == 40
    assert result['rows']['char_form']['observed_damage'] == 40
    assert result['rows']['char_form']['mitigation_matched_hits'] == 1
    assert 'char_original' not in result['rows']


@pytest.mark.parametrize('uid', [None, 999])
def test_unowned_or_colliding_source_keeps_the_rest_of_the_event_watermark(uid):
    a = BattleStatisticsAdapter()
    native = 'char_b' if uid is not None else 'char_unmapped'
    roster_entries = [CHAR_A, CHAR_B]
    a.update(sample([row('char_a'), row('char_b')], 0, 1),
             roster(roster_entries), source_attribution=True)
    a.start_events(session='battle-1', frame=1, sequence=0)
    unknown = dict(session='battle-1', sequence=1, frame=2, game_time=1, source=native,
                   source_character_uid=uid, kind='damage', amount=50)
    known = dict(unknown, sequence=2, source='char_a', source_character_uid=CHAR_A.unique_id, amount=25,
                 target='enemy_a', hp_before=100, hp_after=75, killed=False,
                 real_delta=-25, target_type=0, damage_type=1, cancel_reason=0, shield_absorbed=0)
    a.update(sample([row('char_a', 25), row(native, 50)], 1, 2),
             roster(roster_entries, frame=2), source_events=[unknown, known])
    a.accept_event(**unknown)
    a.accept_event(**known)
    a.event_stream.watermark(session='battle-1', sequence=2, frame=2, game_time=1)
    result = a.result()
    assert result['rows']['char_a']['effective_damage'] == 25
    assert result['rows']['char_a']['event_coverage_complete']
    assert not result['totals_complete']
    assert native in result['unknown_sources']
    assert not a.event_stream.closed


def test_supplemental_hp_evidence_after_coverage_closed_is_dropped_quietly():
    unknown_token = entry('token_otter', 9, token=True)
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a', 100)], 0, 1), roster([CHAR_A], frame=1))
    a.start_events(session='battle-1', frame=1, sequence=0)
    a.update(sample([row('char_a', 100)], 1, 2), roster([CHAR_A, unknown_token], frame=2))
    assert a.event_stream.closed
    a.accept_event(session='battle-1', sequence=1, frame=2, game_time=1,
                   source='token_otter', kind='hp_damage', amount=10,
                   target='enemy_1', hp_before=5, shield_absorbed=0)
    result = a.result()['rows']['char_a']
    assert result['event_coverage_complete'] is False
    assert result['damage'] == 100


@pytest.mark.parametrize('kind', ['skill_start', 'skill_end'])
def test_skill_window_from_unmapped_source_marks_split_unknown_without_closing_events(kind):
    a = BattleStatisticsAdapter()
    a.update(sample([row('char_a')], 0, 1), roster([CHAR_A], frame=1))
    a.start_events(session='battle-1', frame=1, sequence=0)
    a.accept_event(session='battle-1', sequence=1, frame=1, game_time=0,
                   source='enemy_1', kind=kind, amount=1, skill_key='k')
    a.accept_event(session='battle-1', sequence=2, frame=1, game_time=0,
                   source='char_a', kind='damage', amount=5)
    result = a.result()['rows']['char_a']
    assert result['event_coverage_complete'] is True
    assert result['skill_damage'] is None
    assert result['skill_split_complete'] is False


def test_stage_altar_skill_events_leave_squad_skill_split_intact():
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a')], 0, 1), roster([CHAR_A], frame=1))
    adapter.start_events(session='battle-1', frame=1, sequence=0)
    events = [
        ('trap_011_ore', 'skill_start', 1),
        ('char_a', 'damage', 8),
        ('char_a', 'skill_start', 1),
        ('trap_011_ore', 'skill_end', 1),
        ('char_a', 'damage', 12),
        ('char_a', 'skill_end', 1),
    ]
    for sequence, (source, kind, amount) in enumerate(events, 1):
        adapter.accept_event(session='battle-1', sequence=sequence, frame=1,
                             game_time=0, source=source, kind=kind, amount=amount)
    result = adapter.result()['rows']['char_a']
    assert result['skill_damage'] == 12
    assert result['non_skill_damage'] == 8
    assert result['skill_window_count'] == 1
    assert result['skill_split_complete'] is True
    assert result['event_coverage_complete'] is True



def test_undeployed_roster_character_finish_preserves_order_without_creating_counter_row():
    aroma = entry('char_446_aroma', 2147483655)
    team = roster([CHAR_A, aroma], present=(1,), frame=32)
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100)], 1, 32), team)
    adapter.start_events(session='battle-1', frame=32, sequence=1272)
    adapter.update(sample([row('char_a', 125)], 120, 3617), paired(team, 3617))
    finish = dict(session='battle-1', sequence=1273, frame=3534, game_time=117,
                  source=aroma.cid, kind='skill_end', amount=1, skill_reason=7)
    adapter.accept_event(**finish)
    adapter.accept_event(session='battle-1', sequence=1274, frame=3617, game_time=120,
                         source='char_a', kind='damage', amount=25)
    adapter.event_stream.watermark(session='battle-1', sequence=1274,
                                   frame=3617, game_time=120)

    result = adapter.result()
    assert adapter.owner_map.lookup(aroma.cid) == aroma.cid
    assert aroma.cid not in adapter.statistics.owners
    assert aroma.cid not in result['rows']
    assert result['totals_complete']
    assert result['rows']['char_a']['observed_damage'] == 25
    assert result['rows']['char_a']['skill_coverage_complete']

    deployed = roster([CHAR_A, aroma], present=(1, aroma.unique_id), frame=3618)
    adapter.update(sample([row('char_a', 125), row(aroma.cid, 0)], 121, 3618), deployed)
    # A later registration cannot turn a replay of the same raw event into a conflict.
    adapter.accept_event(**finish)
    assert adapter.result()['rows'][aroma.cid]['counter_snapshot_complete']


def test_registered_character_finish_closes_skill_without_hiding_a_missing_counter():
    adapter = BattleStatisticsAdapter()
    adapter.update(sample([row('char_a', 100), row('char_b', 50)], 0, 1), paired(BASE, 1))
    adapter.start_events(session='battle-1', frame=1, sequence=0)
    adapter.accept_event(session='battle-1', sequence=1, frame=1, game_time=0,
                         source='char_b', kind='skill_start', amount=1)
    adapter.update(sample([row('char_a', 100)], 1, 2), paired(BASE, 2))
    adapter.accept_event(session='battle-1', sequence=2, frame=2, game_time=1,
                         source='char_b', kind='skill_end', amount=1, skill_reason=7)
    adapter.update(sample([row('char_a', 100)], 2, 3), paired(BASE, 3))

    result = adapter.result()
    assert not result['totals_complete']
    assert not result['rows']['char_b']['counter_snapshot_complete']
    assert result['rows']['char_b']['event_coverage_complete']
    assert result['rows']['char_b']['skill_window_count'] == 1
    assert result['rows']['char_b']['skill_active_seconds'] == 1
    assert adapter.statistics.skill_windows['char_b'][0]['reason'] == 7
