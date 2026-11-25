"""The event-only healing ranking never needs fabricated native counters."""
from copy import deepcopy
from dataclasses import replace

import pytest

from desktop_bridge import DesktopState, _display_statistics, _native_display_source
from panel_model import Category, PanelConfig, Ranking
from test_multiplayer_healing_scope import heal
from test_multiplayer_native_context import MultiplayerBattleStatistics, consume, context, stamp


HEALING = PanelConfig(category=Category.HEALING)


def event_only(**rows):
    return dict(native_counter_scope='unavailable', event_only_projection=True,
                game_time=12, totals_complete=False, rows=rows)


def healer(amount, **fields):
    return dict({'healing': None, 'recent_healing': None, 'observed_healing': amount}, **fields)


def test_old_defense_record_recovers_observed_healing_total_without_recent_window(tmp_path):
    state = DesktopState(tmp_path)
    # Minimal actor rows from rc.2026112509 generation 53, archive 2699da3b…
    sample = event_only(blades=healer(17064), taraxa=healer(32198.30669300817))
    original = deepcopy(sample)
    view = state.view(HEALING, sample)
    assert [row['value'] for row in view['entries']] == [32198.30669300817, 17064]
    assert view['title'] == '已观察治疗' and view['observed_healing_only']
    assert view['share_scope'] == 'known' and not view['footer_total_complete']
    assert view['footer_total'] == pytest.approx(49262.30669300817)
    assert view['footer_rate'] is None and all(row['rate'] is None for row in view['entries'])
    details = view['details_by_owner']['taraxa']
    assert details['healing_is_observed'] and details['healing'] == 32198.30669300817
    assert details['coverage']['observed_healing']['basis_kind'] == 'scope'
    assert sample == original and view['source']['taraxa']['healing'] is None
    recent = state.view(replace(HEALING, ranking=Ranking.DPS), sample)
    assert not recent['entries'] and recent['empty_text'] == '近 5 秒治疗尚无可靠观察窗口'


@pytest.mark.parametrize('ranking', (Ranking.TOTAL, Ranking.RECENT, Ranking.DPS))
def test_current_observed_window_ranks_and_aggregates_without_whole_team_claim(tmp_path, ranking):
    state = DesktopState(tmp_path)
    window = dict(observed_recent_healing_window=(7., 12.), observed_recent_healing_complete=True)
    sample = event_only(a=healer(200, observed_healing_total=240, observed_healing_complete=True,
                                 observed_recent_healing=40, **window),
                        b=healer(None, observed_healing_total=80, observed_healing_complete=False,
                                 observed_recent_healing=10, **window))
    view = state.view(replace(HEALING, ranking=ranking, boss_only=True), sample)
    expected = [240, 80] if ranking is Ranking.TOTAL else [40, 10]
    assert [row['value'] for row in view['entries']] == expected
    assert [row['rate'] for row in view['entries']] == [40, 10]
    assert 'boss_damage_available' not in view and view['observed_healing_only']
    assert view['footer_rate'] == 50 and not view['footer_rate_complete']
    assert view['share_scope'] == 'known' and view['denominator'] == sum(expected)
    assert view['footer_total'] == (50 if ranking is Ranking.RECENT else 320)
    assert not view['footer_total_complete']
    assert all(row['healing'] is None for row in view['source'].values())


@pytest.mark.parametrize('window,certificate', ((None, True), ((0, 5), True), ((6, 12), True),
                                                ((7, 12), False), ((7, 12), 1)))
def test_unverified_or_stale_recent_healing_stays_unknown(tmp_path, window, certificate):
    view = DesktopState(tmp_path).view(HEALING, event_only(
        a=healer(100, observed_recent_healing=50, observed_recent_healing_window=window,
                 observed_recent_healing_complete=certificate)))
    assert view['entries'][0]['value'] == 100 and view['entries'][0]['rate'] is None
    assert view['footer_rate'] is None


def test_observed_totals_do_not_substitute_unavailable_or_snapshot_values(tmp_path):
    state = DesktopState(tmp_path)
    view = state.view(HEALING, event_only(a=healer(100, observed_healing_total=None), b=healer(0)))
    assert not view['entries'] and view['footer_total'] is None
    snapshot = dict(game_time=12, rows={'a': healer(100, healing=50, recent_healing=10,
        recent_healing_window=(7, 12), counter_snapshot_complete=True)})
    view = state.view(HEALING, snapshot)
    assert not view['observed_healing_only'] and view['entries'][0]['value'] == 50
    assert view['entries'][0]['rate'] == 10 and view['footer_total_complete']


def test_observed_healing_metrics_do_not_enter_the_boss_damage_detail(tmp_path):
    state = DesktopState(tmp_path)
    view = state.view(PanelConfig(boss_only=True), event_only(a=healer(100)))
    assert view['details_by_owner']['a']['healing'] is None
    assert not view['details_by_owner']['a']['healing_is_observed']


@pytest.mark.parametrize('phase,mode,expected_uids', ((4, 'local', {301}), (4, 'all', {301}),
                                                     (5, 'all', {201, 301})))
def test_accepted_scope_healing_reaches_default_recent_hps_column(tmp_path, phase, mode, expected_uids):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=dict(context(0, phase=phase), collection_mode=mode))
    payload = consume(meter, 1, [heal(1, .25, 200, index=2,
                                    scope=stamp(when=.25, phase=phase, collection_mode=mode)),
                                 heal(2, .5, 100, index=3,
                                    scope=stamp(when=.5, phase=phase, collection_mode=mode))],
                      native=dict(context(1, phase=phase), collection_mode=mode))
    sample = dict(native_counter_scope='events_only', multiplayer=payload, game_time=1)
    player = _native_display_source(sample)
    state = DesktopState(tmp_path)
    view = state._actor_view(HEALING, _display_statistics(sample, source=player), player['actor_ledgers'])
    assert {row['actor_uid'] for row in view['entries']} == expected_uids
    assert {row['actor_uid']: (row['value'], row['rate']) for row in view['entries']} == {
        uid: (value, value) for uid, value in ((201, 200), (301, 100)) if uid in expected_uids}
    assert not view['footer_total_complete'] and not view['footer_rate_complete']
