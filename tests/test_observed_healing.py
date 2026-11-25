"""Heal callbacks establish observed amounts and HPS without native counters."""
import pytest

import stats_core
from event_stream import EventStream, StreamGap
from stats_core import Statistics


def setup(*, start=0, observed_from=0):
    stats = Statistics('healing', start, event_coverage=True)
    stats.register_source('source', 'actor')
    stats.register_source('ally', 'ally')
    stats.snapshot(start, {})
    stats.advance(observed_from)
    stats.start_late_event_coverage()
    stream = EventStream(stats, session='healing', baseline_frame=int(observed_from * 100),
                         baseline_sequence=0)
    return stats, stream


def heal(stats, stream, when, amount, source='source', *, sequence=None):
    if when > stats.now:
        stats.advance(when)
    sequence = stream.sequence + 1 if sequence is None else sequence
    stream.accept(session=stats.session, sequence=sequence, frame=int(when * 100), game_time=when,
                  source=source, kind='healing', amount=amount)


def watermark(stats, stream, when):
    if when > stats.now:
        stats.advance(when)
    stream.watermark(session=stats.session, sequence=stream.sequence, frame=int(when * 100),
                     game_time=when)
    return stats.result()['rows']['actor']


def assert_recent(row, rate, interval):
    if rate is None:
        assert row['observed_recent_healing'] is None
        assert row['observed_recent_healing_window'] is None
        assert row['observed_recent_healing_complete'] is False
    else:
        assert row['observed_recent_healing'] == pytest.approx(rate)
        assert row['observed_recent_healing_window'] == interval
        assert row['observed_recent_healing_complete'] is True


def test_short_observation_starts_at_event_baseline_and_explicit_recent_cutoff():
    stats, stream = setup(observed_from=2)
    unobserved_actor = watermark(stats, stream, 2)
    assert unobserved_actor['observed_healing_total'] is None
    assert_recent(unobserved_actor, None, None)
    heal(stats, stream, 2, 10)
    heal(stats, stream, 2.5, 30)
    first = watermark(stats, stream, 3)
    assert first['healing'] is first['recent_healing'] is None
    assert first['observed_healing_total'] == first['observed_healing'] == 40
    assert first['observed_healing_complete'] is True
    assert first['counter_snapshot_complete'] is False and stats.result()['totals_complete'] is False
    assert_recent(first, 40, (2, 3))
    assert_recent(watermark(stats, stream, 7), 8, (2, 7))
    # At the ordinary five-second boundary the old event is excluded.
    idle = watermark(stats, stream, 7.5)
    assert_recent(idle, 0, (2.5, 7.5))
    assert idle['observed_healing_total'] == 40


def test_same_tick_heals_are_coalesced_and_replayed_once():
    stats, stream = setup()
    heal(stats, stream, 0, 10)
    heal(stats, stream, 0, 20)
    heal(stats, stream, 0, 10, sequence=1)
    assert list(stats.engagement[('actor', 'healing')]['recent']) == [(0, 30)]
    frozen = watermark(stats, stream, 0)
    assert frozen['observed_healing_total'] == 30
    assert_recent(frozen, None, None)
    assert_recent(watermark(stats, stream, 1), 30, (0, 1))
    assert_recent(watermark(stats, stream, 1), 30, (0, 1))


@pytest.mark.parametrize('fault', ['event_gap', 'unknown_registered_source', 'unknown_unregistered_source',
                                   'unknown_producer_engagement', 'snapshot_ahead'])
def test_positive_healing_survives_uncertainty_but_current_hps_requires_a_complete_window(fault):
    stats, stream = setup()
    heal(stats, stream, .5, 20)
    heal(stats, stream, .5, 15, 'ally')
    assert_recent(watermark(stats, stream, 1), 20, (0, 1))
    if fault == 'event_gap':
        stream.stop()
    elif fault == 'unknown_registered_source':
        stats.mark_healing_observation_unknown('source')
    elif fault == 'unknown_unregistered_source':
        stats.mark_healing_observation_unknown('unresolved')
    elif fault == 'unknown_producer_engagement':
        stats.mark_all_engagement_unknown()
    else:
        stats.advance(2)
    rows = stats.result()['rows']
    assert rows['actor']['observed_healing_total'] == 20
    assert rows['actor']['observed_healing_complete'] is False
    assert_recent(rows['actor'], None, None)
    if fault == 'unknown_registered_source':
        assert rows['ally']['observed_healing_complete'] is True
        assert_recent(rows['ally'], 15, (0, 1))
        assert rows['actor']['observed_damage'] == 0


def test_exact_terminal_watermark_preserves_final_window_and_rejects_unknown_tail():
    stats, stream = setup()
    heal(stats, stream, 1, 40)
    watermark(stats, stream, 2)
    stream.stop(complete=True)
    stats.finish(2)
    final = stats.result()['rows']['actor']
    assert final['observed_healing_total'] == 40 and final['observed_healing_complete'] is True
    assert_recent(final, 20, (0, 2))

    later, stream = setup()
    heal(later, stream, 1, 40)
    watermark(later, stream, 2)
    stream.stop(complete=True)
    later.finish(3)
    unverified_tail = later.result()['rows']['actor']
    assert unverified_tail['observed_healing_total'] == 40
    assert unverified_tail['observed_healing_complete'] is False
    assert_recent(unverified_tail, None, None)


def test_healing_budget_marks_a_truncated_window_unknown_until_overflow_ages_out(monkeypatch):
    monkeypatch.setattr(stats_core, 'HEALING_RECENT_LIMIT', 2)
    stats, stream = setup()
    for when in (1, 2, 3):
        heal(stats, stream, when, 10)
    truncated = watermark(stats, stream, 3)
    assert len(stats.engagement[('actor', 'healing')]['recent']) == 2
    assert truncated['observed_healing_total'] == 30 and truncated['observed_healing_complete'] is True
    assert_recent(truncated, None, None)
    assert_recent(watermark(stats, stream, 6), 4, (1, 6))


def test_boolean_healing_amount_is_not_counted_as_numeric_output():
    stats, stream = setup()
    with pytest.raises(StreamGap, match='invalid healing amount'):
        heal(stats, stream, 1, True)
    row = stats.result()['rows']['actor']
    assert row['observed_healing_total'] is None
    assert_recent(row, None, None)


@pytest.mark.parametrize('source', ['source', None])
def test_timed_healing_gap_leaves_total_incomplete_and_ages_out_of_recent_window(source):
    stats, stream = setup()
    heal(stats, stream, 1, 30)
    stats.mark_healing_observation_unknown(source, game_time=1)
    heal(stats, stream, 2, 20)
    incomplete = watermark(stats, stream, 5.99)
    assert incomplete['observed_healing_total'] == 50
    assert incomplete['observed_healing_complete'] is False
    assert_recent(incomplete, None, None)
    recent = watermark(stats, stream, 6)
    assert recent['observed_healing_total'] == 50
    assert recent['observed_healing_complete'] is False
    assert_recent(recent, 4, (1, 6))
