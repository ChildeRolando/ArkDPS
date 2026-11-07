import pytest

from stats_core import Statistics


def test_terminal_revision_changes_totals_without_changing_event_time_or_rates():
    stats = Statistics('battle', 0, event_coverage=True)
    stats.register_source('a', 'host')
    stats.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    with pytest.raises(ValueError):
        stats.settle_snapshot({'a': {'damage': 110, 'healing': 0}})
    stats.snapshot(1, {'a': {'damage': 110, 'healing': 0}})
    stats.event('hit', 0, 'a', 'damage', 20)
    stats.finish(1)
    assert stats.settle_snapshot({'a': {'damage': 120, 'healing': 0}})
    row = stats.result()['rows']['host']
    assert row['damage'] == 120 and row['observed_damage'] == 20
    assert row['recent_damage'] is None
    assert row['active_damage'] == 1 and row['event_coverage_end'] == 1
    assert stats.end_time == stats.now == 1
    assert not stats.settle_snapshot({'a': {'damage': 120, 'healing': 0}})
    before = stats.result()
    with pytest.raises(ValueError):
        stats.settle_snapshot({'a': {'damage': 119, 'healing': 0}})
    assert stats.result() == before
    with pytest.raises(ValueError):
        stats.snapshot(1, {'a': {'damage': 120, 'healing': 0}})
