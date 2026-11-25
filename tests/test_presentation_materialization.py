"""Publication-local reuse preserves current facts and independent caller ownership."""
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
        assert normalize.call_count == 1
        state.snapshot()
        assert normalize.call_count == 2  # Deliberately not a cross-publication cache.
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
