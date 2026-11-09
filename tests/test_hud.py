"""HUD integration: one result owner, independent windows and native input state."""
import copy
import ctypes
from ctypes import wintypes
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_gui import gui, native_root, sample, publish
from panel_model import Category, Ranking, RateBasis, render_panel, rows_from_statistics
from hud import window_handle


def style(hud):
    user = ctypes.WinDLL('user32', use_last_error=True)
    getter = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    getter.restype = ctypes.c_ssize_t
    return getter(window_handle(hud.window), -20)


@pytest.mark.parametrize('category,ranking,rate', [
    (Category.DAMAGE, Ranking.TOTAL, RateBasis.RECENT_5S),
    (Category.DAMAGE, Ranking.RECENT, RateBasis.ENGAGEMENT),
    (Category.HEALING, Ranking.TOTAL, RateBasis.ENGAGEMENT),
    (Category.HEALING, Ranking.RECENT, RateBasis.RECENT_5S),
    (Category.SHIELD, Ranking.TOTAL, RateBasis.RECENT_5S),
])
def test_hud_matches_gui_model_shares_and_unknowns(gui, category, ranking, rate):
    data = sample()
    data['statistics']['rows']['a'].update(shield_absorbed=15., event_coverage_complete=False)
    data['statistics']['rows']['b']['healing'] = None
    before = copy.deepcopy(data)
    panel = gui.panels[0]
    panel.config = replace(panel.config, category=category, ranking=ranking, rate_basis=rate)
    hud = gui.open_hud(panel)
    assert gui.open_hud(panel) is hud
    publish(gui, data)
    assert hud.panel.view == panel.view
    assert hud.panel.source is panel.source is data['statistics']['rows']
    assert hud.panel.config is panel.config
    assert data == before
    assert hud.window.attributes('-topmost')
    assert len(gui.huds) == 1


def test_source_configuration_changes_and_window_settings_are_independent(gui):
    first = gui.panels[0]
    second = gui.duplicate_panel(first)
    a, b = gui.open_hud(first), gui.open_hud(second)
    publish(gui, sample())
    a.window.geometry('500x450+80+100')
    b.window.geometry('420x320+700+150')
    gui.root.update()
    geometry = b.window.geometry()
    a.set_opacity('73')
    first.controls['category'][0].set('治疗')
    first.controls['ranking'][0].set('最近 5 秒')
    first.configure_panel()
    assert a.panel.view == first.view
    assert a.panel.view != b.panel.view
    assert b.panel.config.category is Category.DAMAGE
    assert b.window.geometry() == geometry
    assert float(a.window.attributes('-alpha')) == pytest.approx(.73)
    assert float(b.window.attributes('-alpha')) == pytest.approx(.9)


def test_history_display_does_not_redirect_hud_and_next_battle_resets_selection(gui):
    data = sample()
    panel = gui.panels[0]
    hud = gui.open_hud(panel)
    publish(gui, data)
    hud.panel.pin('a')
    # GUI history code is free to replace its presentation rows, never live rows.
    history = copy.deepcopy(data['statistics'])
    history['rows']['a']['damage'] = 777777.
    gui.statistics = history
    gui.source_rows = rows_from_statistics(history)
    gui.render_panels()
    assert panel.view.entries[0].value == 777777.
    assert hud.panel.view.entries[0].value == 1230.
    assert hud.panel.source is data['statistics']['rows']
    data['battle_id'] = 'battle-b'
    data['statistics']['rows']['a']['damage'] = 50.
    publish(gui, data)
    assert hud.panel.pinned is None
    assert hud.panel.view == render_panel(panel.config, rows_from_statistics(data['statistics']))


def test_close_hud_and_delete_source_preserve_collection_and_records(gui):
    data = sample()
    publish(gui, data)
    collector, history = gui.collector, gui.history
    first = gui.panels[0]
    hud = gui.open_hud(first)
    hud.close()
    assert first in gui.panels
    assert not gui.stop.is_set()
    assert gui.last is data and gui.history is history and gui.collector is collector
    second = gui.duplicate_panel(first)
    a, b = gui.open_hud(first), gui.open_hud(second)
    gui.delete_panel(second)
    assert not b.window.winfo_exists()
    assert a.window.winfo_exists()
    assert set(gui.huds) == {first.config.panel_id}
    assert not gui.stop.is_set()
    assert gui.last is data and gui.history is history and gui.collector is collector


def test_close_gui_keeps_visible_hud_and_live_event_loop(gui):
    hud = gui.open_hud(gui.panels[0])
    hud.set_locked(True)
    gui.close()
    gui.root.update()
    assert gui.root.state() == 'iconic'
    assert hud.window.winfo_viewable()
    assert hud.locked
    assert not gui.stop.is_set()
    data = sample()
    gui.events.put(('sample', data))
    gui.tick()
    assert hud.panel.view.entries[0].value == 1230.
    assert '事件不可用' in hud.status.get()
    hud.close()
    gui.root.update()
    assert gui.root.state() == 'normal'
    assert not gui.stop.is_set()


def test_native_lock_unlock_and_drag_resize(gui):
    hud = gui.open_hud(gui.panels[0])
    gui.root.update()
    origin = hud.window.geometry()
    hud.set_locked(True)
    assert style(hud) & 0x20 and style(hud) & 0x80000 and style(hud) & 0x08000000
    assert hud.lock_var.get()
    hud.begin_drag(SimpleNamespace(x_root=1, y_root=1))
    hud.drag(SimpleNamespace(x_root=100, y_root=100))
    gui.root.update()
    assert hud.window.geometry() == origin
    hud.set_opacity(80)
    assert style(hud) & 0x20
    hud.set_locked(False)
    assert not (style(hud) & (0x20 | 0x08000000))
    width, height = hud.window.winfo_width(), hud.window.winfo_height()
    left, top = hud.window.winfo_x(), hud.window.winfo_y()
    hud.begin_drag(SimpleNamespace(x_root=1, y_root=1))
    hud.drag(SimpleNamespace(x_root=41, y_root=31))
    gui.root.update()
    assert (hud.window.winfo_x(), hud.window.winfo_y()) == (left+40, top+30)
    hud.begin_resize(SimpleNamespace(x_root=0, y_root=0))
    hud.resize(SimpleNamespace(x_root=80, y_root=60))
    gui.root.update()
    assert (hud.window.winfo_width(), hud.window.winfo_height()) == (width+80, height+60)


def test_failed_native_lock_does_not_claim_locked(gui, monkeypatch):
    hud = gui.open_hud(gui.panels[0])
    def fail(*args):
        raise OSError('native style denied')
    monkeypatch.setattr('hud.set_clickthrough', fail)
    with pytest.raises(OSError, match='native style denied'):
        hud.set_locked(True)
    assert not hud.locked and not hud.lock_var.get()


def test_drag_across_negative_virtual_desktop_coordinates(gui):
    hud = gui.open_hud(gui.panels[0])
    hud.window.geometry('400x350+100+100')
    gui.root.update()
    hud.begin_drag(SimpleNamespace(x_root=100, y_root=100))
    hud.drag(SimpleNamespace(x_root=-500, y_root=150))
    gui.root.update()
    assert (hud.window.winfo_x(), hud.window.winfo_y()) == (-500, 150)


def test_explicit_quit_requests_shared_collector_shutdown(gui, monkeypatch):
    gui.open_hud(gui.panels[0])
    finished = []
    monkeypatch.setattr(gui, 'finish_close', lambda: finished.append(True))
    gui.quit()
    assert gui.stop.is_set() and finished == [True]
