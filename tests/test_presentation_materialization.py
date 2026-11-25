"""Publication revision reuse preserves current facts and independent caller ownership."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
import desktop_bridge
from desktop_bridge import DesktopState
from panel_model import Ranking
from sample_data import sample


def test_one_publication_normalizes_scope_once_and_crops_only_wire(tmp_path):
    state = DesktopState(tmp_path)
    data = sample()
    data['statistics']['rows']['a']['private_proof'] = {'raw': [1, 2]}
    state.accept('sample', data)
    config = state.layout.panels[0]
    state.layout = replace(state.layout, panels=(config, replace(config, panel_id='second'),
        replace(config, panel_id='recent', ranking=Ranking.RECENT)))
    with patch('desktop_bridge.rows_from_statistics', wraps=desktop_bridge.rows_from_statistics) as normalize:
        snapshot = state.snapshot()
        assert normalize.call_count == 0  # Prepared by accept/update_live_analysis.
        state.snapshot()
        assert normalize.call_count == 0  # Stable revision reuses work across polls.
    for view in snapshot['panels']:
        assert all('detail' not in entry for entry in view['entries'])
        assert 'private_proof' not in view['source']['a']
        assert 'readings' in view['details_by_owner']['a']
    assert data['statistics']['rows']['a']['private_proof'] == {'raw': [1, 2]}


def test_same_frame_revision_and_returned_dto_mutation_never_reuse_old_values(tmp_path):
    state = DesktopState(tmp_path)
    data = sample()
    state.accept('sample', data)
    first = state.snapshot()
    first['panels'][0]['details_by_owner']['a']['damage'] = -1
    first['panels'][0]['source']['a']['damage_by_type']['physical'] = -1
    assert data['statistics']['rows']['a']['damage_by_type']['physical'] == 1230
    next_data = deepcopy(data)
    next_data['statistics']['rows']['a']['damage'] = 2000
    next_data['statistics']['rows']['a']['event_coverage_complete'] = True
    state.accept('sample', next_data)
    view = state.snapshot()['panels'][0]
    assert view['details_by_owner']['a']['damage'] == 2000
    assert view['details_by_owner']['a']['event_coverage_complete'] is True
    assert data['statistics']['rows']['a']['damage'] == 1230


def test_identical_views_share_work_but_not_mutable_dtos(tmp_path):
    state = DesktopState(tmp_path)
    config = state.layout.panels[0]
    statistics = sample()['statistics']
    def projection():
        first = state.view(config, statistics)
        first['details_by_owner']['a']['damage'] = -1
        second = state.view(config, statistics)
        assert second['details_by_owner']['a']['damage'] == 1230
        return second
    with patch.object(state, '_snapshot', side_effect=projection), patch(
            'desktop_bridge.render_panel', wraps=desktop_bridge.render_panel) as render:
        state.snapshot()
        assert render.call_count == 1


def test_same_frame_quality_event_and_config_revision_invalidate_cached_views(tmp_path):
    state = DesktopState(tmp_path)
    data = sample()
    state.accept('sample', data)
    with patch('desktop_bridge.rows_from_statistics', wraps=desktop_bridge.rows_from_statistics) as normalize:
        state.snapshot(); state.snapshot()
        assert normalize.call_count == 0
        changed = deepcopy(data)
        changed['statistics']['rows']['a']['overkill_unknown_reason'] = 'new same-frame quality'
        state.accept('sample', changed)
        view = state.snapshot()['panels'][0]
        assert normalize.call_count == 3  # Main plus two target-scope certificates prepared once.
        assert view['details_by_owner']['a']['overkill_unknown_reason'] == 'new same-frame quality'
        state.persist(replace(state.layout, panels=(replace(state.layout.panels[0], ranking=Ranking.RECENT),)))
        state.snapshot()
        assert normalize.call_count == 4


def test_freshness_overlay_keeps_aging_without_rebuilding_statistics(tmp_path, monkeypatch):
    state = DesktopState(tmp_path)
    data = sample(); data['captured_monotonic'] = 100
    monkeypatch.setattr(desktop_bridge.time, 'monotonic', lambda: 101)
    state.accept('sample', data)
    with patch('desktop_bridge.rows_from_statistics', wraps=desktop_bridge.rows_from_statistics) as normalize:
        first = state.snapshot()
        monkeypatch.setattr(desktop_bridge.time, 'monotonic', lambda: 110)
        second = state.snapshot()
        assert first['status'] != second['status']
        assert normalize.call_count == 0



def test_chart_and_detail_use_canonical_row_rates_with_separate_full_window_admission():
    from panel_model import (Category, RateBasis, EventAvailability, rows_from_statistics,
        live_damage_rates, detail_rates, settled_boss_damage_rates)
    raw = dict(name='A', damage=100, recent_damage=20, recent_damage_window=(1, 6),
        counter_snapshot_complete=True, event_coverage_complete=True,
        overkill_coverage_complete=True, recent_overkill_damage=25,
        observed_damage=80, overkill_damage=10, active_damage=2)
    statistics = {'game_time': 6, 'rows': {'a': raw}}
    row, = rows_from_statistics(statistics)
    team, operators = live_damage_rates(statistics, 6, effective=True)
    assert team == operators[0]['recent_dps'] == row.rate(Category.DAMAGE, RateBasis.RECENT_5S, effective=True) == 15
    detail = detail_rates([row], EventAvailability('ready'), effective=True)
    assert detail['a']['damage']['value'] == row.rate(Category.DAMAGE, RateBasis.ENGAGEMENT, effective=True) == 35
    assert live_damage_rates(statistics, 7, effective=True)[0] is None
    lane = dict(observed_boss_recent_damage=5, observed_boss_recent_window=(1, 6),
        observed_boss_recent_damage_complete=False, observed_boss_damage=25,
        recent_effective_damage=4, recent_effective_damage_window=(1, 6),
        recent_effective_damage_complete=False, effective_damage=20)
    scoped = {'game_time': 6, 'rows': {'a': {'settled_damage': lane}}}
    canonical, = rows_from_statistics(scoped)
    team, operators = settled_boss_damage_rates(scoped, 6, effective=False)
    assert team is None and operators[0]['recent_dps'] is None
    assert operators[0]['observed_recent_dps'] == canonical.rate(Category.DAMAGE, RateBasis.RECENT_5S) == 5
    assert canonical.recent_window(Category.DAMAGE) == (1, 6)
