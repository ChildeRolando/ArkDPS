import json
from pathlib import Path

import pytest

from roster import CharacterEntry, RosterSnapshot
from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch
from stats_core import Statistics


def frozen():
    stats = Statistics('battle', 0, event_coverage=True)
    stats.register_source('a', 'a')
    stats.snapshot(0, {'a': {'damage': 100, 'healing': 50}})
    stats.snapshot(1, {'a': {'damage': 120, 'healing': 60}})
    stats.event('hit', .5, 'a', 'damage', 20)
    stats.finish(1)
    return stats


def test_terminal_totals_do_not_advance_event_time_or_invent_a_sampled_rate():
    stats = frozen()
    before = stats.result()['rows']['a']
    stats.correct_final_totals({'a': {'damage': 150, 'healing': 65}})
    result = stats.result()
    row = result['rows']['a']
    assert result['game_time'] == stats.end_time == 1
    assert row['damage'] == 150 and row['healing'] == 65
    assert row['recent_damage'] is None and row['recent_healing'] is None
    assert row['active_damage'] == before['active_damage']
    assert row['observed_damage'] == before['observed_damage'] == 20
    with pytest.raises(ValueError):
        stats.event('late', 1, 'a', 'damage', 30)
    with pytest.raises(ValueError):
        stats.snapshot(1, {'a': {'damage': 150, 'healing': 65}})


@pytest.mark.parametrize('bad', [
    {'damage': 119, 'healing': 65},
    {'damage': float('nan'), 'healing': 65},
    {'damage': 150},
])
def test_invalid_terminal_totals_are_transactional(bad):
    stats = frozen()
    before = stats.result()
    with pytest.raises(ValueError):
        stats.correct_final_totals({'a': bad})
    assert stats.result() == before


def test_terminal_corrections_require_the_ended_baseline():
    stats = Statistics('battle', 0)
    stats.register_source('a', 'a')
    stats.snapshot(0, {'a': {'damage': 100, 'healing': 50}})
    with pytest.raises(ValueError):
        stats.correct_final_totals({'a': {'damage': 150, 'healing': 65}})
    with pytest.raises(ValueError, match='conflicting counters'):
        stats.snapshot(0, {'a': {'damage': 150, 'healing': 65}})


def test_native_terminal_fixture_updates_totals_without_advancing_time():
    fixture = json.loads((Path(__file__).parent / 'fixtures/lifecycle-terminal-of8.json')
                         .read_text(encoding='utf-8'))
    first, second = fixture['pairs']

    def roster(raw):
        return RosterSnapshot(raw['frame'], tuple(CharacterEntry(**entry) for entry in raw['entries']),
                              frozenset(raw['present_unique_ids']), raw['present_known'])

    adapter = BattleStatisticsAdapter()
    adapter.update(first['sample'], roster(first['roster']))
    assert adapter.result()['rows']['char_017_huang']['damage'] == 28985.8828125
    adapter.update(first['sample'], roster(first['roster']), terminal=True)
    adapter.update(second['sample'], roster(second['roster']), terminal=True)
    result = adapter.result()
    assert result['rows']['char_017_huang']['damage'] == 31899.103515625
    assert result['game_time'] == 98.79990577697754
    assert result['rows']['char_017_huang']['recent_damage'] is None
    assert result['ended']
    # Repeated completion freezes and archives the same corrected result.
    assert adapter.finish() == adapter.finish()
    assert fixture['source']['capture'] == 'of8-aef6051-01/product/capture.jsonl'


def test_adapter_terminal_mode_rejects_foreign_or_advancing_frozen_time():
    fixture = json.loads((Path(__file__).parent / 'fixtures/lifecycle-terminal-of8.json')
                         .read_text(encoding='utf-8'))
    raw = fixture['pairs'][0]
    roster = RosterSnapshot(raw['roster']['frame'],
                            tuple(CharacterEntry(**entry) for entry in raw['roster']['entries']))
    adapter = BattleStatisticsAdapter()
    adapter.update(raw['sample'], roster)
    adapter.update(raw['sample'], roster, terminal=True)
    before = adapter.result()
    for change in ({'session': 'foreign'}, {'state': 2},
                   {'game_time': raw['sample']['game_time'] + 1}):
        with pytest.raises(FrameMismatch):
            adapter.update(dict(raw['sample'], **change), roster, terminal=True)
        assert adapter.result() == before
