"""Versioned layout persistence, migration, corruption and reachability."""
import json

import pytest

import layout_store as ls
from panel_model import Category, PanelConfig, Ranking, RateBasis


def panel(panel_id='p1', **kwargs):
    return PanelConfig(panel_id=panel_id, **kwargs)


def test_first_launch_creates_one_damage_panel_and_no_hud(tmp_path):
    store = ls.LayoutStore(tmp_path / 'layout.json')
    result = store.load()
    assert result.ok and result.warning == ''
    assert result.layout == ls.Layout.default()
    assert result.layout.panels[0].category is Category.DAMAGE
    assert result.layout.panels[0].ranking is Ranking.TOTAL
    assert result.layout.panels[0].rate_basis is RateBasis.RECENT_5S
    assert result.layout.huds == () and result.layout.gui is None
    assert result.layout.event_observation is True
    assert not store.path.exists()


def test_version_two_roundtrip_keeps_panels_hud_gui_and_event_choice(tmp_path):
    store = ls.LayoutStore(tmp_path / 'layout.json')
    layout = ls.Layout(
        panels=(panel('p1', category=Category.HEALING, ranking=Ranking.RECENT),
                panel('p2')),
        huds=(ls.HudPlacement('p1', -500, 40, 410, 360, opacity=0.73, locked=True),),
        gui=ls.GuiGeometry(-1080, 20, 900, 720),
        event_observation=False)
    store.save(layout)
    loaded = store.load()
    assert loaded.ok and loaded.warning == '' and loaded.migrated_from is None
    assert loaded.layout == layout
    raw = json.loads(store.path.read_text(encoding='utf-8'))
    assert raw['version'] == ls.LAYOUT_VERSION
    assert raw['huds'][0]['opacity'] == 0.73
    assert raw['event_observation'] is False


def test_version_one_panel_collection_is_migrated(tmp_path):
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps({'version': 1, 'panels': [
        panel('legacy', category=Category.SHIELD).to_dict()]}), encoding='utf-8')
    store = ls.LayoutStore(path)
    result = store.load()
    assert result.ok and result.migrated_from == 1
    assert '迁移' in result.warning
    assert result.layout.panels[0].panel_id == 'legacy'
    assert result.layout.huds == () and result.layout.gui is None
    assert result.layout.event_observation is True
    store.save(result.layout)
    assert json.loads(path.read_text(encoding='utf-8'))['version'] == ls.LAYOUT_VERSION


def test_bare_panel_document_without_version_is_accepted_as_legacy(tmp_path):
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps({'panels': [panel('a').to_dict()]}), encoding='utf-8')
    result = ls.LayoutStore(path).load()
    assert result.ok and result.migrated_from == 1
    assert result.layout.panels[0].panel_id == 'a'


@pytest.mark.parametrize('payload', ['{not json', '[]', '{}', '{"version": 2}'])
def test_damaged_file_is_backed_up_and_default_layout_is_usable(tmp_path, payload):
    path = tmp_path / 'layout.json'
    path.write_text(payload, encoding='utf-8')
    store = ls.LayoutStore(path)
    result = store.load()
    assert not result.ok and '默认布局' in result.warning
    assert result.layout == ls.Layout.default()
    assert result.backup is not None and result.backup.exists()
    assert result.backup.name.startswith('layout.json.corrupt-')
    assert result.backup.read_text(encoding='utf-8') == payload
    assert not path.exists()
    # The default is savable, so the app continues with a usable configuration.
    store.save(result.layout)
    assert store.load().ok


def test_future_version_is_preserved_and_reported(tmp_path):
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps({'version': 99, 'panels': [panel('x').to_dict()]}),
                    encoding='utf-8')
    result = ls.LayoutStore(path).load()
    assert not result.ok and '不支持的布局版本 99' in result.warning
    assert result.backup is not None and result.backup.exists()
    assert result.layout == ls.Layout.default()


def test_invalid_panels_and_huds_are_skipped_with_diagnostic(tmp_path):
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps({
        'version': 2,
        'event_observation': 'yes',
        'gui': {'x': 'left', 'y': 1, 'width': 10, 'height': 10},
        'panels': [panel('good').to_dict(), {'category': 'nope'},
                   panel('good').to_dict()],
        'huds': [{'panel_id': 'missing', 'x': 1, 'y': 2, 'width': 3, 'height': 4},
                 {'panel_id': 'good', 'x': 1, 'y': 2, 'width': 3, 'height': 4,
                  'opacity': 0.1}],
    }), encoding='utf-8')
    result = ls.LayoutStore(path).load()
    assert result.ok
    assert [p.panel_id for p in result.layout.panels] == ['good']
    assert result.layout.huds == ()
    assert result.layout.event_observation is True
    assert result.layout.gui is None
    assert '跳过' in result.warning and 'HUD' in result.warning
    assert '事件观测开关' in result.warning and 'GUI 窗口几何' in result.warning


def test_fully_invalid_panel_list_is_treated_as_damaged(tmp_path):
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps({'version': 2, 'panels': [{'category': 'nope'}]}),
                    encoding='utf-8')
    result = ls.LayoutStore(path).load()
    assert not result.ok and result.layout == ls.Layout.default()


def test_saved_hud_requires_a_matching_panel(tmp_path):
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps({
        'version': 2, 'panels': [panel('a').to_dict()],
        'huds': [{'panel_id': 'gone', 'x': 0, 'y': 0, 'width': 400, 'height': 300}]}),
        encoding='utf-8')
    result = ls.LayoutStore(path).load()
    assert result.ok and result.layout.huds == ()
    assert 'HUD' in result.warning


def test_clamp_keeps_visible_window_and_rescues_removed_display():
    areas = [(0, 0, 1920, 1040)]
    visible = ls.GuiGeometry(100, 100, 900, 700)
    assert ls.clamp_geometry(visible, areas) == visible
    rescued = ls.clamp_geometry(ls.GuiGeometry(-1000, 200, 800, 600), areas)
    assert (rescued.x, rescued.y) == (0, 200)
    assert rescued.width == 800 and rescued.height == 600


def test_clamp_shrinks_when_dpi_or_screen_cannot_fit_saved_size():
    areas = [(0, 0, 1280, 720)]
    fitted = ls.clamp_geometry(ls.GuiGeometry(200, 100, 3000, 2000), areas,
                               minimum=(440, 500))
    assert (fitted.width, fitted.height) == (1280, 720)
    assert (fitted.x, fitted.y) == (0, 0)


def test_clamp_respects_minimum_but_never_exceeds_the_work_area():
    fitted = ls.clamp_geometry(ls.GuiGeometry(0, 0, 120, 90), [(0, 0, 800, 600)])
    assert (fitted.width, fitted.height) == (350, 230)
    tiny = ls.clamp_geometry(ls.GuiGeometry(0, 0, 120, 90), [(0, 0, 300, 200)])
    assert (tiny.width, tiny.height) == (300, 200)


def test_clamp_chooses_nearest_monitor_for_negative_and_far_coordinates():
    areas = [(-1080, 0, 0, 1800), (0, 0, 1920, 1040)]
    left = ls.clamp_geometry(ls.GuiGeometry(-2000, 300, 800, 600), areas)
    assert left.x == -1080 and left.y == 300
    far = ls.clamp_geometry(ls.GuiGeometry(9000, 5000, 800, 600), areas)
    assert far.x == 1120 and far.y == 440


def test_clamp_without_monitor_information_returns_saved_geometry():
    geometry = ls.GuiGeometry(-500, 100, 700, 500)
    assert ls.clamp_geometry(geometry, []) == geometry
