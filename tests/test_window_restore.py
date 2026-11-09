"""Restart restores panels/HUDs; damaged or unreachable layout falls back safely."""
from types import SimpleNamespace

import pytest
import tkinter as tk

from app import App
from connection import ConnectionConfig, ConnectionConfigStore
from layout_store import GuiGeometry, HudPlacement, Layout, LayoutStore
from panel_model import Category, PanelConfig, Ranking, RateBasis
from test_gui import native_root  # noqa: F401  (one session Tk root)


def reset_root(root):
    """Drop a previous App instance while keeping this process's Tk root."""
    for timer in root.tk.call('after', 'info'):
        try:
            root.after_cancel(timer)
        except tk.TclError:
            pass
    try:
        root.unbind('<Configure>')
        root.deiconify()
    except tk.TclError:
        pass
    for widget in list(root.winfo_children()):
        try:
            widget.destroy()
        except tk.TclError:
            pass
    root.update()


def launch(native_root, monkeypatch, layout_path, *, work_areas=None, events='remote',
           args=None):
    reset_root(native_root)
    monkeypatch.setattr(App, 'collect', lambda self: None)
    app = App(native_root, args or SimpleNamespace(adb='', serial='', frida_remote=events),
              layout_store=LayoutStore(layout_path),
              connection_store=ConnectionConfigStore(layout_path.parent / 'connection.json'),
              work_area_provider=lambda: work_areas or [(0, 0, 1920, 1040)])
    native_root.update()
    return app


def test_restart_restores_panels_hud_geometry_opacity_and_gui(tmp_path, monkeypatch,
                                                             native_root):
    path = tmp_path / 'layout.json'
    app = launch(native_root, monkeypatch, path)
    first = app.panels[0]
    first.controls['category'][0].set('治疗')
    first.controls['ranking'][0].set('最近 5 秒')
    first.configure_panel()
    second = app.duplicate_panel(first)
    second.controls['category'][0].set('伤害')
    second.configure_panel()
    hud = app.open_hud(first)
    native_root.update()
    hud.window.geometry('410x360+120+140')
    hud.set_opacity('75')
    native_root.geometry('1000x760+50+60')
    native_root.update()
    assert app.save_layout_now()
    panel_configs = [panel.config for panel in app.panels]
    first_id = first.config.panel_id

    app2 = launch(native_root, monkeypatch, path)
    assert [panel.config for panel in app2.panels] == panel_configs
    assert app2.panels[0].config.category is Category.HEALING
    assert app2.panels[0].config.ranking is Ranking.RECENT
    assert len(app2.huds) == 1
    restored = app2.huds[first_id]
    assert (restored.window.winfo_width(), restored.window.winfo_height()) == (410, 360)
    assert (restored.window.winfo_x(), restored.window.winfo_y()) == (120, 140)
    assert float(restored.window.attributes('-alpha')) == pytest.approx(.75)
    assert not restored.locked
    assert (native_root.winfo_width(), native_root.winfo_height()) == (1000, 760)
    assert (native_root.winfo_x(), native_root.winfo_y()) == (50, 60)
    assert app2.panels[1].config.category is Category.DAMAGE


def test_saved_hud_lock_is_restored(tmp_path, monkeypatch, native_root):
    path = tmp_path / 'layout.json'
    LayoutStore(path).save(Layout(panels=(PanelConfig(),), huds=(
        HudPlacement('default', 300, 200, 410, 360, opacity=0.8, locked=True),)))
    app = launch(native_root, monkeypatch, path)
    hud = app.huds['default']
    assert hud.locked
    assert hud.lock_var.get()
    assert hud.handle.cget('text').endswith('已锁定（在 GUI 解锁）')


def test_removed_display_returns_gui_and_hud_to_a_reachable_area(tmp_path, monkeypatch,
                                                                 native_root):
    path = tmp_path / 'layout.json'
    LayoutStore(path).save(Layout(
        panels=(PanelConfig(),),
        huds=(HudPlacement('default', 5000, 4000, 410, 360),),
        gui=GuiGeometry(8000, 6000, 1000, 760)))
    app = launch(native_root, monkeypatch, path, work_areas=[(0, 0, 1920, 1040)])
    assert 0 <= native_root.winfo_x() <= 1920 - native_root.winfo_width()
    assert 0 <= native_root.winfo_y() <= 1040 - native_root.winfo_height()
    hud = app.huds['default']
    assert 0 <= hud.window.winfo_x() <= 1920 - hud.window.winfo_width()
    assert 0 <= hud.window.winfo_y() <= 1040 - hud.window.winfo_height()


def test_changed_dpi_shrinks_saved_windows_into_the_current_work_area(
        tmp_path, monkeypatch, native_root):
    path = tmp_path / 'layout.json'
    LayoutStore(path).save(Layout(
        panels=(PanelConfig(),),
        huds=(HudPlacement('default', 0, 0, 2000, 1600),),
        gui=GuiGeometry(0, 0, 3000, 2000)))
    app = launch(native_root, monkeypatch, path, work_areas=[(0, 0, 1280, 720)])
    assert (native_root.winfo_width(), native_root.winfo_height()) == (1280, 720)
    hud = app.huds['default']
    assert (hud.window.winfo_width(), hud.window.winfo_height()) == (1280, 720)


def test_damaged_layout_reports_diagnostic_and_starts_with_default_panel(
        tmp_path, monkeypatch, native_root):
    path = tmp_path / 'layout.json'
    path.write_text('{broken', encoding='utf-8')
    app = launch(native_root, monkeypatch, path)
    assert len(app.panels) == 1 and app.panels[0].config == PanelConfig()
    assert any('默认布局' in warning for warning in app.warnings)
    backups = list(tmp_path.glob('layout.json.corrupt-*'))
    assert len(backups) == 1
    app.tick()
    assert '默认布局' in app.diagnostics.get()
    # The usable default can be saved and restored normally afterwards.
    app.add_panel()
    assert app.save_layout_now()
    assert LayoutStore(path).load().ok


def test_panel_configuration_and_hud_close_are_persisted(tmp_path, monkeypatch,
                                                         native_root):
    path = tmp_path / 'layout.json'
    app = launch(native_root, monkeypatch, path)
    panel = app.panels[0]
    panel.controls['category'][0].set('有效护盾')
    panel.configure_panel()
    hud = app.open_hud(panel)
    native_root.update()
    assert LayoutStore(path).load().layout.panels[0].category is Category.SHIELD
    assert len(LayoutStore(path).load().layout.huds) == 1
    hud.close()
    native_root.update()
    assert LayoutStore(path).load().layout.huds == ()


def test_event_observation_preference_is_saved_with_the_layout(
        tmp_path, monkeypatch, native_root):
    path = tmp_path / 'layout.json'
    app = launch(native_root, monkeypatch, path)
    assert app.events_enabled is True and app.frida_remote == 'remote'
    app.apply_connection(ConnectionConfig(auto_detect=True), False)
    native_root.update()
    assert app.events_enabled is False and app.frida_remote is None
    assert LayoutStore(path).load().layout.event_observation is False
    app.apply_connection(ConnectionConfig(auto_detect=True), True)
    assert LayoutStore(path).load().layout.event_observation is True


def test_first_launch_creates_no_hud_and_one_damage_panel(tmp_path, monkeypatch,
                                                          native_root):
    app = launch(native_root, monkeypatch, tmp_path / 'layout.json')
    assert len(app.panels) == 1 and not app.huds
    config = app.panels[0].config
    assert (config.category, config.ranking, config.rate_basis) == (
        Category.DAMAGE, Ranking.TOTAL, RateBasis.RECENT_5S)


def test_preloaded_damaged_layout_diagnostic_reaches_the_app(tmp_path, monkeypatch,
                                                            native_root):
    from app import App

    path = tmp_path / 'layout.json'
    path.write_text('{bad', encoding='utf-8')
    store = LayoutStore(path)
    loaded = store.load()
    assert not loaded.ok
    reset_root(native_root)
    monkeypatch.setattr(App, 'collect', lambda self: None)
    app = App(native_root, SimpleNamespace(adb='', serial='', frida_remote=None),
              layout_store=store, layout_load=loaded,
              work_area_provider=lambda: [(0, 0, 1920, 1040)])
    native_root.update()
    assert any('默认布局' in warning for warning in app.warnings)
    assert app.panels[0].config == PanelConfig()


def test_single_launch_no_events_override_keeps_saved_preference(
        tmp_path, monkeypatch, native_root):
    """A one-shot --no-events launch must not clear the saved event preference."""
    path = tmp_path / 'layout.json'
    first = launch(native_root, monkeypatch, path)
    assert first.events_enabled is True and first.event_observation is True
    assert first.save_layout_now()
    assert LayoutStore(path).load().layout.event_observation is True

    # Same as `--no-events`: events are off for this run only.
    off = launch(native_root, monkeypatch, path, events=None)
    assert off.events_enabled is False and off.event_observation is True
    monkeypatch.setattr(off, 'finish_close', lambda: None)
    off.close()
    native_root.update()
    assert LayoutStore(path).load().layout.event_observation is True

    # An ordinary restart derives event observation from the kept preference.
    saved = LayoutStore(path).load().layout
    again = launch(native_root, monkeypatch, path,
                   events='remote' if saved.event_observation else None)
    assert again.events_enabled is True and again.event_observation is True
