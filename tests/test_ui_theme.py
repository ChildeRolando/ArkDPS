"""ARK-19: state-preserving themes, migration, history filters and PNG identity."""
from dataclasses import replace
import copy
import json
import tkinter as tk

import pytest

from app import App
from battle_history import BattleHistory
from gui_history import HistoryWindow
from layout_store import Layout, LayoutStore, HudPlacement, GuiGeometry
from panel_model import PanelConfig, RateBasis
from statistics_screenshot import statistics_png
from test_gui import gui, native_root, sample, publish
from test_battle_history import record
from ui_theme import PALETTES, palette, theme_key


@pytest.mark.parametrize('version', [1, 2])
def test_old_layouts_default_to_lone_trail_and_migrate(version, tmp_path):
    store = LayoutStore(tmp_path / 'layout.json')
    raw = Layout((PanelConfig(panel_id='a'),),
                 (HudPlacement('a', 100, 100, 410, 360, .73, True),),
                 GuiGeometry(100, 100, 900, 700), False).to_dict()
    raw.update(version=version)
    raw.pop('theme')
    store.path.write_text(json.dumps(raw), encoding='utf-8')
    loaded = store.load()
    assert loaded.ok and loaded.migrated_from == version
    assert loaded.layout.theme == 'lone-trail'
    if version == 2:
        assert loaded.layout.huds[0].locked
        assert loaded.layout.huds[0].opacity == .73
        assert loaded.layout.event_observation is False
        assert loaded.layout.gui.width == 900
    store.save(loaded.layout)
    assert store.load().layout == loaded.layout
    assert json.loads(store.path.read_text('utf-8'))['version'] == 3


@pytest.mark.parametrize('theme', ['original', 'lone-trail'])
def test_theme_roundtrip_and_invalid_theme_recovery(theme, tmp_path):
    store = LayoutStore(tmp_path / 'layout.json')
    layout = replace(Layout.default(), theme=theme)
    store.save(layout)
    assert store.load().layout == layout
    raw = layout.to_dict()
    raw['theme'] = ['invalid']
    store.path.write_text(json.dumps(raw), encoding='utf-8')
    loaded = store.load()
    assert loaded.ok and loaded.layout.theme == 'lone-trail' and '皮肤' in loaded.warning


def test_hot_theme_keeps_models_pins_order_rows_hud_and_history(gui, tmp_path):
    data = sample()
    publish(gui, data)
    p = gui.panels[0]
    p.pin('a')
    p.set_hover(True)
    hud = gui.create_hud(p)
    hud.window.geometry('410x360+120+160')
    hud.set_opacity('73')
    hud.set_locked(True)
    gui.history = BattleHistory(tmp_path / 'history')
    gui.history.save(record(1))
    gui.export()
    browser = gui.history_window
    gui.open_settings()
    gui.root.update()
    before = (copy.deepcopy(data), p.view, p.pinned, list(p.order), copy.deepcopy(p.items),
              hud.placement(), hud.window.winfo_id(), browser.selected.to_dict(),
              gui.current_battle, gui.settings_window.winfo_id())
    for key in ('original', 'lone-trail', 'original'):
        gui.set_theme(key)
        gui.root.update()
        assert theme_key(gui.root) == key
        assert p.canvas['background'] == PALETTES[key].plot
        assert gui.settings_window.listing['background'] == PALETTES[key].surface
        assert (data, p.view, p.pinned, p.order, p.items, hud.placement(),
                hud.window.winfo_id(), browser.selected.to_dict(), gui.current_battle,
                gui.settings_window.winfo_id()) == before
        assert hud.locked and hud.lock_var.get() and not gui.stop.is_set()
    hud.set_locked(False)
    gui.settings_window.close()


def test_preference_is_saved_and_restored(gui, tmp_path):
    gui.layout_store = LayoutStore(tmp_path / 'layout.json')
    gui.set_theme('original')
    gui.root.update()
    assert gui.save_layout_now()
    loaded = gui.layout_store.load()
    assert loaded.layout.theme == 'original'
    assert loaded.layout.panels == tuple(p.config for p in gui.panels)


def test_rate_change_and_theme_do_not_change_ranking_geometry(gui):
    data = sample()
    publish(gui, data)
    p = gui.panels[0]
    before = [(e.owner_id, e.value, e.bar_ratio, e.share) for e in p.view.entries]
    p.controls['rate_basis'][0].set('接敌')
    p.configure_panel()
    gui.set_theme('original')
    assert p.config.rate_basis is RateBasis.ENGAGEMENT
    assert [(e.owner_id, e.value, e.bar_ratio, e.share) for e in p.view.entries] == before
    assert all(e.rate is None for e in p.view.entries)


def test_filters_keep_frozen_selection_when_hidden_removed_and_new_live_battle(gui, tmp_path):
    gui.history = BattleHistory(tmp_path / 'history')
    for i, result in enumerate(('victory', 'defeat', 'abandoned')):
        gui.history.save(record(i, stage_id=f'OF-{i}', outcome=result, practice=i == 1))
    gui.export()
    w = gui.history_window
    key = next(k for k, r in w.records.items() if r.battle_id == 'battle:1')
    w.listing.selection_set(key)
    w.select_record()
    selected = w.selected.to_dict()
    w.query.set('of-0')
    assert len(w.listing.get_children()) == 1
    assert w.selected.to_dict() == selected
    w.outcome_filter.set('失败')
    assert not w.listing.get_children()
    assert w.selected.to_dict() == selected
    w.query.set('')
    w.mode_filter.set('演习')
    assert len(w.listing.get_children()) == 1
    gui.history.path_for('battle:1').unlink()
    w.refresh()
    gui.root.update()
    assert w.selected.to_dict() == selected
    w.mode_filter.set('全部模式')
    w.outcome_filter.set('全部结果')
    hud = gui.create_hud(gui.panels[0])
    data = sample()
    data['battle_id'] = 'next'
    publish(gui, data)
    assert gui.current_battle == 'next' and hud.panel.view == gui.panels[0].view
    assert w.selected.to_dict() == selected
    assert str(w.png_button['state']) == 'normal'


def test_statistics_png_uses_selected_theme_without_changing_record(gui):
    from PIL import Image
    import io
    r = record(1)
    before = r.to_dict()
    for theme in PALETTES:
        gui.set_theme(theme)
        raw = statistics_png(gui.root, r, gui.panels[0].config)
        image = Image.open(io.BytesIO(raw))
        selection = json.loads(image.info['ArkDPS.selection'])
        assert selection['theme'] == theme and selection['battle_id'] == r.battle_id
        assert image.getpixel((0, 0)) == tuple(bytes.fromhex(PALETTES[theme].background[1:]))
        assert selection['owners'] == ['a']
    assert r.to_dict() == before


def test_text_palette_contrast():
    def luminance(hex):
        rgb = [v / 255 for v in bytes.fromhex(hex[1:])]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055)**2.4 for v in rgb]
        return sum(x*w for x, w in zip(linear, (.2126, .7152, .0722)))
    for p in PALETTES.values():
        for fg, bg in ((p.text, p.background), (p.secondary, p.background),
                       (p.warning, p.background), (p.accent, p.background),
                       (p.plot_text, p.plot), (p.plot_secondary, p.plot),
                       (p.plot_text, p.plot_selection), (p.text, p.selection)):
            light, dark = sorted((luminance(fg), luminance(bg)), reverse=True)
            assert (light+.05)/(dark+.05) >= 4.5, (fg, bg)


@pytest.mark.parametrize('theme', ['lone-trail', 'original'])
def test_wait_running_pause_stale_degraded_disconnect_remain_distinct(gui, theme):
    import time
    gui.set_theme(theme)
    gui.scanning = False
    gui.tick()
    assert '等待战斗数据' in gui.status.get()
    data = sample()
    gui.last = data
    gui.last_frame_change = time.monotonic()
    gui.render(data)
    gui.tick()
    assert gui.status.get() == '正在统计'
    assert '事件不可用' in gui.diagnostics.get()
    gui.last_frame_change = time.monotonic() - 1
    gui.tick()
    assert '暂停' in gui.status.get()
    data['captured_monotonic'] -= 10
    gui.tick()
    assert '过期' in gui.status.get()
    gui.error = 'transport disconnected'
    gui.tick()
    assert '连接断开' in gui.status.get()
    assert gui.panels[0].view.entries[0].value == 1230.


def test_png_save_freezes_skin_before_nested_dialog(gui, tmp_path, monkeypatch):
    from PIL import Image
    gui.history = BattleHistory(tmp_path / 'history')
    gui.history.save(record(1))
    gui.export()
    w = gui.history_window
    gui.set_theme('lone-trail')
    target = tmp_path / 'share.png'
    def dialog(**kwargs):
        gui.set_theme('original')
        return str(target)
    monkeypatch.setattr('gui_history.filedialog.asksaveasfilename', dialog)
    w.export_png()
    assert json.loads(Image.open(target).info['ArkDPS.selection'])['theme'] == 'lone-trail'
