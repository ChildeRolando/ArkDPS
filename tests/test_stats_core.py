import pytest

from stats_core import Statistics


def test_mid_battle_baseline_and_short_window():
    s = Statistics('battle', 30)
    s.register_source('a', 'operator')
    s.snapshot(30, {'a': {'damage': 1000, 'healing': 500}})
    assert s.result()['rows']['operator']['recent_damage'] is None
    s.snapshot(32.3, {'a': {'damage': 1230, 'healing': 615}})
    row = s.result()['rows']['operator']
    assert row['damage'] == 1230
    assert row['healing'] == 615
    assert row['recent_damage'] == pytest.approx(100)
    assert row['recent_healing'] == pytest.approx(50)
    assert row['engagement_damage'] is None
    assert row['shield_coverage_start'] is None
    assert row['shield_absorbed'] is None


def test_rolling_window_and_pause():
    s = Statistics('battle', 30)
    s.register_source('a', 'a')
    for i in range(8):
        s.snapshot(30 + i, {'a': {'damage': 1000 + 100*i, 'healing': 500 + 50*i}})
    assert s.result()['rows']['a']['recent_damage'] == 100
    s.snapshot(37, {'a': {'damage': 1700, 'healing': 850}})
    assert s.result()['rows']['a']['recent_healing'] == 50


def test_engagement_independent_intervals_and_end_clipping():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    for i, t in enumerate((0, 1, 4)):
        s.advance(t)
        s.event(str(i), t, 'a', 'damage', 100)
    s.event('heal', 4, 'a', 'healing', 50)
    s.advance(4.5)
    row = s.result()['rows']['a']
    assert row['active_damage'] == 3
    assert row['active_healing'] == .5
    assert row['engagement_damage'] == 100
    s.finish(5.5)
    row = s.result()['rows']['a']
    assert row['active_damage'] == 4
    assert row['active_healing'] == 1.5
    with pytest.raises(ValueError):
        s.advance(6)


def test_source_keys_and_event_dedup_keep_host_attribution():
    s = Statistics('battle', 10, event_coverage=True)
    s.register_source('host-key', 'host')
    s.register_source('summon-key', 'host')
    s.snapshot(10, {'host-key': {'damage': 200, 'healing': 0}, 'summon-key': {'damage': 300, 'healing': 0}})
    s.advance(11)
    s.event('shield-1', 11, 'summon-key', 'shield_absorbed', 25)
    s.event('shield-1', 11, 'summon-key', 'shield_absorbed', 25)
    s.event('damage-1', 11, 'summon-key', 'damage', 40)
    assert s.result()['rows']['host']['damage'] == 500
    assert s.result()['rows']['host']['shield_absorbed'] == 25
    assert s.result()['rows']['host']['observed_damage'] == 40
    with pytest.raises(ValueError):
        s.event('shield-1', 11, 'summon-key', 'shield_absorbed', 26)
    with pytest.raises(ValueError):
        s.event('unknown', 11, 'victim', 'shield_absorbed', 4)
    with pytest.raises(ValueError):
        s.event('expire', 11, 'summon-key', 'shield_expired', 4)
    with pytest.raises(ValueError):
        s.register_source('summon-key', 'other')


def test_formula_coverage_checks_each_source_before_aggregating_a_summon():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('host', 'operator')
    s.register_source('summon', 'operator')
    s.register_source('untouched', 'other')
    s.snapshot(0, {key: {'damage': 0, 'healing': 0} for key in s.owners})
    s.snapshot(1, {'host': {'damage': 30, 'healing': 0},
                   'summon': {'damage': 70, 'healing': 0},
                   'untouched': {'damage': 25, 'healing': 0}})
    s.event('a', 1, 'host', 'damage', 20, mitigation_before=25, mitigation_after=20)
    s.event('b', 1, 'summon', 'damage', 80, mitigation_before=100, mitigation_after=80)
    s.event('c', 1, 'untouched', 'damage', 25, mitigation_before=25, mitigation_after=25)
    rows = s.result()['rows']
    assert rows['operator']['mitigation_rate'] is None
    assert rows['other']['mitigation_rate'] == 0


def test_formula_gain_retains_a_signed_mitigation_rate_and_gap_hides_it():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.snapshot(1, {'a': {'damage': 50, 'healing': 0}})
    s.event('a', 1, 'a', 'damage', 50, mitigation_before=40, mitigation_after=50)
    assert s.result()['rows']['a']['mitigation_rate'] == -25
    s.invalidate_events()
    assert s.result()['rows']['a']['mitigation_rate'] is None


def test_missing_source_and_counter_failure_are_not_zero_filled():
    s = Statistics('battle', 0)
    s.register_source('a', 'a')
    s.register_source('b', 'b')
    s.snapshot(0, {k: {'damage': 100, 'healing': 0} for k in ('a', 'b')})
    s.snapshot(1, {'a': {'damage': 150, 'healing': 0}})
    assert s.result()['rows']['a']['recent_damage'] == 50
    assert s.result()['rows']['b']['damage'] == 100
    assert s.result()['rows']['b']['recent_damage'] is None
    with pytest.raises(ValueError):
        s.snapshot(2, {'a': {'damage': 149, 'healing': 0}})
    assert s.result()['game_time'] == 1
    s.snapshot(2, {'a': {'damage': 200, 'healing': 0}, 'b': {'damage': 200, 'healing': 0}})
    assert s.result()['rows']['b']['recent_damage'] is None
    s.snapshot(7, {'a': {'damage': 450, 'healing': 0}, 'b': {'damage': 450, 'healing': 0}})
    assert s.result()['rows']['b']['recent_damage'] == 50


def test_new_source_nonzero_does_not_become_recent_output():
    s = Statistics('battle', 0)
    s.register_source('a', 'host')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    s.register_source('token', 'host')
    s.snapshot(1, {'a': {'damage': 110, 'healing': 0}, 'token': {'damage': 1000, 'healing': 0}})
    row = s.result()['rows']['host']
    assert row['damage'] == 1110
    assert row['recent_damage'] is None


def test_unavailable_events_are_unknown_and_same_time_conflicts_rejected():
    s = Statistics('battle', 0)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 10, 'healing': 0}})
    with pytest.raises(ValueError):
        s.event('e', 0, 'a', 'shield_absorbed', 1)
    with pytest.raises(ValueError):
        s.snapshot(0, {'a': {'damage': 20, 'healing': 0}})
    assert s.result()['rows']['a']['damage'] == 10
    assert s.result()['rows']['a']['shield_absorbed'] is None


def test_same_frame_event_follows_baseline_and_does_not_duplicate_total():
    s = Statistics('battle', 30, event_coverage=True)
    s.register_source('a', 'a')
    with pytest.raises(ValueError):
        s.event('hit', 30, 'a', 'damage', 20)
    s.snapshot(30, {'a': {'damage': 1000, 'healing': 500}})
    s.event('hit', 30, 'a', 'damage', 20)
    assert s.result()['rows']['a']['damage'] == 1000
    s.snapshot(31, {'a': {'damage': 1020, 'healing': 500}})
    row = s.result()['rows']['a']
    assert row['damage'] == 1020
    assert row['observed_damage'] == 20
    assert row['recent_damage'] == 20


def test_explicit_gaps_close_coverage_and_do_not_invent_rates():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    s.event('hit', 0, 'a', 'damage', 10)
    s.advance(1)
    s.invalidate_snapshots()
    s.invalidate_events()
    s.snapshot(2, {'a': {'damage': 200, 'healing': 0}})
    row = s.result()['rows']['a']
    assert row['recent_damage'] is None
    assert row['active_damage'] == 1
    assert row['event_coverage_end'] == 1
    assert row['event_coverage_complete'] is False
    with pytest.raises(ValueError):
        s.event('other', 2, 'a', 'damage', 10)


def test_recent_restarts_immediately_after_gap():
    s = Statistics('battle', 0)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    s.snapshot(1, {'a': {'damage': 110, 'healing': 0}})
    s.invalidate_snapshots()
    s.snapshot(2, {'a': {'damage': 200, 'healing': 0}})
    s.snapshot(2.5, {'a': {'damage': 250, 'healing': 0}})
    assert s.result()['rows']['a']['recent_damage'] == 100


def test_new_source_uses_common_owner_window():
    s = Statistics('battle', 0)
    s.register_source('a', 'host')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    s.snapshot(2, {'a': {'damage': 300, 'healing': 0}})
    s.register_source('b', 'host')
    s.snapshot(2, {'a': {'damage': 300, 'healing': 0}, 'b': {'damage': 1000, 'healing': 0}})
    s.snapshot(2.5, {'a': {'damage': 350, 'healing': 0}, 'b': {'damage': 1050, 'healing': 0}})
    assert s.result()['rows']['host']['recent_damage'] == 200


def test_new_owner_starts_short_window_at_first_baseline():
    s = Statistics('battle', 0)
    s.snapshot(0, {})
    s.register_source('b', 'b')
    s.snapshot(2, {'b': {'damage': 1000, 'healing': 0}})
    s.snapshot(2.5, {'b': {'damage': 1050, 'healing': 0}})
    assert s.result()['rows']['b']['recent_damage'] == 100


def overkill(s, event_id, amount, actual, before, after, *, source='a', killed=None,
             target='enemy', time=0, damage_type=1, **extra):
    if killed is None:
        killed = after == 0
    # A synthetic hit without shields carries an explicit verified zero; a
    # missing absorption context is a different (unknown) case.
    extra.setdefault('shield_absorbed', 0.0)
    s.event(event_id, time, source, 'damage', amount, target=target,
            real_delta=-actual, hp_before=before, hp_after=after, killed=killed,
            target_type=0, damage_type=damage_type, **extra)


def test_overkill_is_derived_only_at_a_lethal_endpoint_without_changing_totals():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 2000, 'healing': 0}})
    overkill(s, 'normal', 280, 280, 1000, 720)
    overkill(s, 'kill', 200, 44, 44, 0)
    overkill(s, 'research', 771.5, 185.5, 185.5, 0)
    # Captured OF-8 ApplyModifier sample: value 195, realDelta -160, HP 160 -> 0.
    overkill(s, 'captured', 195, 160, 160, 0)
    row = s.result()['rows']['a']
    assert row['overkill_damage'] == pytest.approx(777)
    assert row['overkill_coverage_complete'] is True
    assert row['damage'] == 2000          # native total stays inclusive
    assert row['observed_damage'] == pytest.approx(1446.5)
    top = s.result()
    assert top['overkill_damage'] == pytest.approx(777)
    assert top['overkill_coverage_complete'] is True
    assert top['overkill_coverage_start'] == 0


def test_overkill_excludes_shield_absorption_healing_and_never_double_counts():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 500, 'healing': 0}})
    # Captured frame 697: the native value is the incoming damage, the shield
    # absorbed all 200 of it and the target's HP never moved.
    overkill(s, 'absorbed', 200, 0, 800, 800, shield_absorbed=200)
    # 200 incoming, 100 absorbed by the shield, 44 HP actually lost, target dies:
    # overkill is 200 - 100 - 44, not 200 - 44.
    overkill(s, 'partial-kill', 200, 44, 44, 0, shield_absorbed=100)
    s.event('heal', 0, 'a', 'healing', 500)
    row = s.result()['rows']['a']
    assert row['overkill_damage'] == pytest.approx(56)
    assert row['shield_absorbed'] == 0
    assert row['observed_healing'] == 500
    with pytest.raises(ValueError):
        s.event('bad-heal', 0, 'a', 'healing', 10, target='enemy',
                real_delta=0, hp_before=1, hp_after=1, killed=False)


def test_lethal_hit_requires_a_verified_absorption_context():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 500, 'healing': 0}})
    # Non-lethal hits need no absorption context: their overkill is zero.
    overkill(s, 'normal', 280, 280, 1000, 720, shield_absorbed=None)
    overkill(s, 'kill-without-context', 200, 44, 44, 0, shield_absorbed=None)
    row = s.result()['rows']['a']
    assert row['overkill_damage'] is None
    assert row['overkill_coverage_complete'] is False
    assert s.result()['overkill_damage'] is None


@pytest.mark.parametrize('absorbed', [250, 160, -1])
def test_contradictory_absorption_evidence_is_unknown(absorbed):
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 500, 'healing': 0}})
    # 250 > amount; 44 + 160 > amount; a negative absorption is invalid.
    overkill(s, 'kill', 200, 44, 44, 0, shield_absorbed=absorbed)
    assert s.result()['rows']['a']['overkill_damage'] is None


def test_living_target_proves_zero_overkill_without_full_hp_reconstruction():
    s = Statistics('nonlethal', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('hit', .5, 'a', 'damage', 100, target='enemy',
            hp_after=5, killed=False)
    row = s.result()['rows']['A']
    assert row['overkill_damage'] == 0
    assert row['overkill_coverage_complete'] is True
    assert row['effective_damage'] == 0  # snapshot damage is still 0 in this fixture



def test_friendly_self_damage_does_not_enter_engagement_dps():
    s = Statistics('friendly', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(10)
    s.event('self', 1, 'a', 'damage', 25, target='char_a', hp_after=10, killed=False)
    s.event('enemy', 2, 'a', 'damage', 100, target='enemy_1',
            hp_after=10, killed=False)
    row = s.result()['rows']['A']
    assert row['observed_damage'] == 100
    assert row['active_damage'] == pytest.approx(1.5)
    assert row['engagement_damage'] == pytest.approx(100 / 1.5)



def test_incoming_hp_events_alone_do_not_prove_overkill():
    s = Statistics('hp', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 1000, 'healing': 0}})
    s.advance(2)
    s.event('hp1', 1, 'a', 'hp_damage', 150, target='enemy_1',
            hp_before=100, shield_absorbed=0)
    s.event('hp2', 1.5, 'a', 'hp_damage', 50, target='enemy_2',
            hp_before=100, shield_absorbed=0)
    row = s.result()['rows']['A']
    assert row['overkill_damage'] is None
    assert row['overkill_coverage_complete'] is False
    assert row['effective_damage'] is None


def test_hp_damage_missing_hp_or_shield_stays_unknown():
    s = Statistics('hp-unknown', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 1000, 'healing': 0}})
    s.advance(2)
    s.event('hp1', 1, 'a', 'hp_damage', 150, target='enemy_1', hp_before=None)
    row = s.result()['rows']['A']
    assert row['overkill_damage'] is None
    assert row['overkill_coverage_complete'] is False


def test_hp_damage_from_unknown_source_is_ignored_not_fatal():
    s = Statistics('hp-unknown-source', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('hp-evil', .5, 'not-registered', 'hp_damage', 10,
            target='enemy_1', hp_before=5, shield_absorbed=0)
    assert s.result()['rows']['A']['damage'] == 0


@pytest.mark.parametrize('evidence', [
    {},
    {'target': None, 'real_delta': -10, 'hp_before': 10, 'hp_after': 0, 'killed': True},
    {'target': '', 'real_delta': -10, 'hp_before': 10, 'hp_after': 0, 'killed': True},
    {'target': 'enemy', 'real_delta': None, 'hp_before': 10, 'hp_after': 0, 'killed': True},
    {'target': 'enemy', 'real_delta': -10, 'hp_before': 10, 'hp_after': 0},
    {'target': 'enemy', 'real_delta': 10, 'hp_before': 10, 'hp_after': 0, 'killed': True},
    {'target': 'enemy', 'real_delta': -10, 'hp_before': 10, 'hp_after': 5, 'killed': True},
    {'target': 'enemy', 'real_delta': -10, 'hp_before': 10, 'hp_after': 0, 'killed': False},
    {'target': 'enemy', 'real_delta': -2, 'hp_before': 10, 'hp_after': 0, 'killed': True},
])
def test_overkill_incomplete_or_conflicting_evidence_is_unknown_not_zero(evidence):
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    s.event('hit', 0, 'a', 'damage', 10, **evidence)
    row = s.result()['rows']['a']
    assert row['overkill_damage'] is None
    assert row['overkill_coverage_complete'] is False
    assert s.result()['overkill_damage'] is None


def test_overkill_accumulates_summon_sources_to_the_host():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('host-key', 'host')
    s.register_source('summon-key', 'host')
    s.snapshot(0, {'host-key': {'damage': 100, 'healing': 0},
                   'summon-key': {'damage': 100, 'healing': 0}})
    overkill(s, 'host-hit', 300, 50, 50, 0, source='host-key')
    overkill(s, 'summon-hit', 120, 100, 200, 100, source='summon-key', killed=False)
    overkill(s, 'summon-kill', 300, 100, 100, 0, source='summon-key')
    assert s.result()['rows']['host']['overkill_damage'] == pytest.approx(450)


def test_overkill_closes_with_event_coverage_and_keeps_zero_for_healers():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.register_source('healer', 'healer')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0},
                   'healer': {'damage': 0, 'healing': 50}})
    overkill(s, 'kill', 200, 44, 44, 0)
    s.event('heal', 0, 'healer', 'healing', 100)
    assert s.result()['rows']['healer']['overkill_damage'] == 0
    assert s.result()['rows']['healer']['overkill_coverage_complete'] is True
    s.advance(1)
    s.invalidate_events()
    row = s.result()['rows']['a']
    assert row['overkill_damage'] is None
    assert row['overkill_coverage_end'] == 1
    assert s.result()['overkill_coverage_complete'] is False


def test_sparse_sampling_still_publishes_five_second_rate():
    s = Statistics('battle', 0)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    # 2x play with the observed ~1.7s success cadence: no sample at the 5s
    # boundary, but the bracket around it is still inside the window.
    s.snapshot(2, {'a': {'damage': 200, 'healing': 0}})
    s.snapshot(4, {'a': {'damage': 300, 'healing': 0}})
    s.snapshot(6, {'a': {'damage': 400, 'healing': 0}})
    row = s.result()['rows']['a']
    # start=1, interpolated baseline 150, observed 250 over 5s.
    assert row['recent_damage'] == pytest.approx(50)
    assert row['recent_damage_window'] == pytest.approx((1, 6))


def test_rate_still_unknown_when_bracket_is_a_real_gap():
    s = Statistics('battle', 0)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    s.snapshot(10, {'a': {'damage': 200, 'healing': 0}})
    assert s.result()['rows']['a']['recent_damage'] is None


def test_overkill_is_grouped_by_native_damage_type():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 2000, 'healing': 0}})
    overkill(s, 'physical-kill', 200, 44, 44, 0, damage_type=1)
    overkill(s, 'arts-kill', 771.5, 185.5, 185.5, 0, damage_type=2)
    overkill(s, 'normal-hit', 280, 280, 1000, 720, damage_type=3)
    row = s.result()['rows']['a']
    assert row['overkill_damage'] == pytest.approx(742)
    assert row['overkill_type_complete'] is True
    assert row['overkill_damage_by_type'] == {
        'physical': pytest.approx(156), 'arts': pytest.approx(586),
        'elemental': 0.0, 'true': 0.0}


def test_overkill_with_unrecognised_damage_type_keeps_total_but_hides_types():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 500, 'healing': 0}})
    overkill(s, 'unknown-type-kill', 200, 44, 44, 0, damage_type=None)
    row = s.result()['rows']['a']
    assert row['overkill_damage'] == pytest.approx(156)
    assert row['overkill_coverage_complete'] is True
    assert row['overkill_type_complete'] is False
    assert row['overkill_damage_by_type'] is None


def test_settlement_keeps_totals_complete_and_retires_rate_windows():
    s = Statistics('battle', 0)
    s.register_source('a', 'A')
    s.register_source('b', 'B')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0},
                   'b': {'damage': 200, 'healing': 0}})
    s.snapshot(1, {'a': {'damage': 150, 'healing': 0},
                   'b': {'damage': 250, 'healing': 0}})
    assert s.result()['totals_complete'] is True
    s.finish(1)
    s.correct_final_totals({'a': {'damage': 160, 'healing': 0},
                            'b': {'damage': 250, 'healing': 0}})
    result = s.result()
    assert result['ended'] is True and result['totals_complete'] is True
    assert result['rows']['A']['damage'] == 160
    for row in result['rows'].values():
        assert row['counter_snapshot_complete'] is True
        assert row['recent_damage'] is None and row['recent_healing'] is None


def test_collection_gap_requires_a_new_complete_snapshot():
    s = Statistics('battle', 0)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    s.snapshot(1, {'a': {'damage': 150, 'healing': 0}})
    s.invalidate_snapshots()
    result = s.result()
    assert result['totals_complete'] is False
    assert result['rows']['A']['counter_snapshot_complete'] is False
    assert result['rows']['A']['damage'] == 150  # retained, never zero-filled
    s.snapshot(2, {'a': {'damage': 220, 'healing': 0}})
    assert s.result()['totals_complete'] is True
    assert s.result()['rows']['A']['damage'] == 220


def test_end_seals_the_retained_total_of_a_source_that_left():
    s = Statistics('battle', 0)
    s.register_source('host', 'H')
    s.register_source('token', 'H')
    s.snapshot(0, {'host': {'damage': 100, 'healing': 0},
                   'token': {'damage': 300, 'healing': 0}})
    s.snapshot(1, {'host': {'damage': 140, 'healing': 0}})
    row = s.result()['rows']['H']
    assert row['damage'] == 440 and row['counter_snapshot_complete'] is False
    s.finish(1)
    s.seal_totals()
    row = s.result()['rows']['H']
    assert row['damage'] == 440
    assert row['counter_snapshot_complete'] is True
    assert row['recent_damage'] is None


def test_unrecovered_gap_is_not_sealed_by_the_battle_end():
    s = Statistics('battle', 0)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    s.snapshot(1, {'a': {'damage': 150, 'healing': 0}})
    s.invalidate_snapshots()
    s.finish(1)
    s.seal_totals()
    result = s.result()
    assert result['totals_complete'] is False
    assert result['rows']['A']['counter_snapshot_complete'] is False


def test_overkill_unknown_reason_names_missing_absorption_context():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 500, 'healing': 0}})
    overkill(s, 'kill-without-context', 200, 44, 44, 0, shield_absorbed=None)
    row = s.result()['rows']['a']
    assert row['overkill_damage'] is None
    assert '护盾/抵挡吸收上下文' in row['overkill_unknown_reason']


def test_elemental_burst_is_attributed_to_the_last_elemental_bar_source():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 1000, 'healing': 0}})
    s.advance(1)
    s.event('bar', 0.5, 'a', 'elemental_bar', 308.88, target_instance='target-1')
    s.event('burst', 0.9, None, 'elemental_burst', 6000,
            target='enemy_1046_agent', real_delta=-784, hp_before=784,
            hp_after=0, killed=True, target_instance='target-1')
    s.advance(2)
    row = s.result()['rows']['a']
    assert row['burst_damage'] == pytest.approx(784)
    assert row['burst_overkill'] == pytest.approx(5216)
    assert row['burst_settlement'] == pytest.approx(6000)
    assert row['burst_complete'] is True
    assert row['damage'] == pytest.approx(7000)
    assert row['overkill_damage'] == pytest.approx(5216)
    assert row['effective_damage'] == pytest.approx(1784)


def test_unattributed_elemental_burst_is_unknown_not_guessed():
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 1000, 'healing': 0}})
    s.advance(1)
    s.event('burst', 0.9, None, 'elemental_burst', 6000,
            target='enemy_1046_agent', real_delta=-784, hp_before=784,
            hp_after=0, killed=True, target_instance='target-unknown')
    s.advance(2)
    row = s.result()['rows']['a']
    assert row['burst_damage'] is None
    assert row['burst_complete'] is False
    assert '元素爆条' in row['burst_unknown_reason']
    assert row['damage'] == pytest.approx(1000)


@pytest.mark.parametrize('unresolved', [False, True])
def test_sourced_burst_is_already_in_native_and_sourceless_overkill_is_added_once(unresolved):
    s = Statistics('battle', 0, event_coverage=True)
    s.register_source('a', 'a')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.snapshot(1, {'a': {'damage': 1500, 'healing': 0}})
    s.event('direct-hp', .5, 'a', 'hp_damage', 500, target='enemy_1',
            hp_before=300, shield_absorbed=0, damage_type=5)
    s.event('direct-burst', .5, 'a', 'elemental_burst', 500, target='enemy_1',
            hp_before=300, hp_after=0, real_delta=-300, target_instance='first')
    s.event('bar', .6, 'a', 'elemental_bar', 100, target_instance='second')
    s.event('sourceless-burst', .7, None, 'elemental_burst', 300, target='enemy_1',
            hp_before=100, hp_after=0, real_delta=-100, target_instance='second')
    if unresolved:
        s.event('unowned-burst', .9, None, 'elemental_burst', 900, target='enemy_1',
                hp_before=50, hp_after=0, real_delta=-50, target_instance='third')
    row = s.result()['rows']['a']
    assert row['burst_complete'] is (not unresolved)
    if unresolved:
        assert row['burst_unknown_reason']
        assert row['burst_damage'] is None
        assert row['burst_overkill'] is None
        assert row['burst_settlement'] is None
    else:
        assert row['burst_damage'] == 400
        assert row['burst_overkill'] == 400
        assert row['burst_settlement'] == 800
    assert row['burst_added_settlement'] == 300
    assert row['damage'] == 1800
    assert row['overkill_damage'] == 400
    assert row['overkill_damage_by_type'] == {
        'physical': 0, 'arts': 0, 'elemental': 400, 'true': 0,
    }
    assert row['effective_damage'] == 1400


def test_skill_windows_split_observed_damage_and_close_at_finish():
    s = Statistics('skill-split', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('start', .5, 'a', 'skill_start', 1,
            target_instance='Character.OnSkillStart', skill_key='skill_a', skill_name='技能A')
    s.event('skill-hit', .6, 'a', 'damage', 100, target='enemy_1')
    s.event('end', .7, 'a', 'skill_end', 1,
            target_instance='Character.OnSkillFinish', skill_key='skill_a', skill_reason=0)
    s.event('normal-hit', .8, 'a', 'damage', 50, target='enemy_1')
    s.finish(2)
    row = s.result()['rows']['A']
    assert row['skill_damage'] == 100
    assert row['non_skill_damage'] == 50
    assert row['skill_split_complete'] is True
    assert row['skill_window_count'] == 1
    assert row['skill_seen'] is True
    assert row['observed_damage'] == 150


def test_duplicate_skill_start_and_end_markers_stay_one_window():
    s = Statistics('skill-dedupe', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('s1', .1, 'a', 'skill_start', 1, skill_key='k')
    s.event('s2', .2, 'a', 'skill_start', 1, skill_key='k')
    s.event('d', .3, 'a', 'damage', 10, target='enemy_1')
    s.event('e1', .4, 'a', 'skill_end', 1, skill_key='k')
    s.event('e2', .5, 'a', 'skill_end', 1, skill_key='k')
    row = s.result()['rows']['A']
    assert row['skill_damage'] == 10
    assert row['non_skill_damage'] == 0
    assert row['skill_window_count'] == 1


def test_skill_split_is_unknown_after_event_coverage_ends():
    s = Statistics('skill-gap', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('s', .1, 'a', 'skill_start', 1, skill_key='k')
    s.event('d', .2, 'a', 'damage', 10, target='enemy_1')
    s.invalidate_events(1)
    row = s.result()['rows']['A']
    assert row['skill_damage'] is None and row['non_skill_damage'] is None
    assert row['skill_split_complete'] is False
    assert row['skill_split_reason'] == '事件覆盖已结束'


def test_clean_event_coverage_close_keeps_final_effective_and_skill_metrics():
    s = Statistics('skill-clean-end', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('start', .1, 'a', 'skill_start', 1, skill_key='k')
    s.event('hp', .2, 'a', 'hp_damage', 200, target='enemy_1',
            hp_before=10, shield_absorbed=0, damage_type=1)
    s.event('hit', .2, 'a', 'damage', 50, target='enemy_1', hp_before=10,
            hp_after=0, real_delta=-10, killed=True, shield_absorbed=0, damage_type=1)
    s.event('end', .3, 'a', 'skill_end', 1, skill_key='k')
    s.advance(1)
    s.snapshot(1, {'a': {'damage': 50, 'healing': 0}})
    s.invalidate_events(1, complete=True)
    row = s.result()['rows']['A']
    assert row['event_coverage_end'] == 1
    assert row['event_coverage_complete'] is True
    assert row['overkill_damage'] == 40
    assert row['effective_damage'] == 10
    assert row['skill_damage'] == 50
    assert row['skill_damage_effective'] == 10
    assert row['skill_split_effective_complete'] is True
    assert row['skill_coverage_complete'] is True


def test_skill_event_from_unknown_source_marks_split_unknown_not_fatal():
    s = Statistics('skill-unknown', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('s', .1, 'mystery', 'skill_start', 1, skill_key='k')
    s.event('d', .2, 'a', 'damage', 10, target='enemy_1')
    row = s.result()['rows']['A']
    assert row['skill_damage'] is None
    assert row['skill_split_complete'] is False


def test_skill_coverage_and_effective_split_use_per_event_overkill():
    s = Statistics('skill-effective', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('start', .5, 'a', 'skill_start', 1, skill_key='k')
    s.event('skill-kill', .6, 'a', 'damage', 50, target='enemy_1',
            real_delta=-10, hp_before=10, hp_after=0, killed=True,
            target_type=0, damage_type=1, shield_absorbed=0)
    s.event('end', .7, 'a', 'skill_end', 1, skill_key='k')
    s.event('normal-hit', .8, 'a', 'damage', 30, target='enemy_1',
            real_delta=-30, hp_before=40, hp_after=10, killed=False,
            target_type=0, damage_type=1)
    s.advance(2)
    row = s.result()['rows']['A']
    assert row['skill_damage'] == 50
    assert row['non_skill_damage'] == 30
    assert row['skill_damage_effective'] == 10
    assert row['non_skill_damage_effective'] == 30
    assert row['skill_split_effective_complete'] is True
    assert row['skill_active_seconds'] == pytest.approx(.2)
    assert row['skill_coverage_complete'] is True


def test_settled_overkill_evidence_splits_effective_skill_damage():
    s = Statistics('skill-hp-effective', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.advance(1)
    s.event('start', .1, 'a', 'skill_start', 1, skill_key='k')
    s.event('hp', .2, 'a', 'hp_damage', 200, target='enemy_1',
            hp_before=10, shield_absorbed=0, damage_type=1)
    s.event('hit', .2, 'a', 'damage', 50, target='enemy_1', hp_before=10,
            hp_after=0, real_delta=-10, killed=True, shield_absorbed=0, damage_type=1)
    s.event('end', .3, 'a', 'skill_end', 1, skill_key='k')
    s.advance(1)
    row = s.result()['rows']['A']
    assert row['skill_damage'] == 50
    assert row['skill_damage_effective'] == 10
    assert row['skill_split_effective_complete'] is True
    assert row['overkill_damage_by_type'] == {
        'physical': 40, 'arts': 0, 'elemental': 0, 'true': 0,
    }
    assert row['overkill_type_complete'] is True



def test_stage_reduction_uses_post_settlement_without_subtracting_incoming_excess():
    s = Statistics('stage-reduction', 0, event_coverage=True)
    s.register_source('a', 'A')
    s.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    s.snapshot(1, {'a': {'damage': 2_750, 'healing': 0}})
    s.event('skill', .1, 'a', 'skill_start', 1)
    s.event('incoming1', .2, 'a', 'hp_damage', 6_400, target='enemy_1',
            target_instance='one', hp_before=1_800, shield_absorbed=0, damage_type=1)
    s.event('actual1', .2, 'a', 'damage', 1_600, target='enemy_1', target_instance='one',
            hp_before=1_800, hp_after=200, real_delta=-1_600, killed=False,
            shield_absorbed=0, damage_type=1)
    s.event('incoming2', .3, 'a', 'hp_damage', 4_600, target='enemy_1',
            target_instance='one', hp_before=200, shield_absorbed=0, damage_type=1)
    s.event('actual2', .3, 'a', 'damage', 1_150, target='enemy_1', target_instance='one',
            hp_before=200, hp_after=0, real_delta=-200, killed=True,
            shield_absorbed=0, damage_type=1)
    s.event('dead', .3, 'a', 'damage', 500, target='enemy_1', target_instance='one',
            hp_before=0, hp_after=0, real_delta=0, killed=True, shield_absorbed=0)
    s.event('immune', .4, 'a', 'damage', 700, target='enemy_2', target_instance='two',
            hp_before=300, hp_after=300, real_delta=0, killed=False, cancel_reason=1)

    row = s.result()['rows']['A']
    assert row['damage'] == row['observed_damage'] == row['skill_damage'] == 2_750
    assert row['overkill_damage'] == 950
    assert row['effective_damage'] == row['skill_damage_effective'] == 1_800
    assert row['overkill_damage_by_type']['physical'] == 950
