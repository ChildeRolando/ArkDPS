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
