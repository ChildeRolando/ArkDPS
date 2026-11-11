"""Native Tk integration against deterministic unified statistics, no device access."""
import copy
from dataclasses import replace
from pathlib import Path
import sys
import time
import tkinter as tk
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App, event_label
from panel_model import Category, Ranking, RateBasis, render_panel, rows_from_statistics
from gui_panel import detail_text, empty_text
from stats_core import Statistics


@pytest.fixture(scope='session')
def native_root():
    root = tk.Tk()
    yield root
    root.destroy()


@pytest.fixture
def gui(monkeypatch, native_root):
    monkeypatch.setattr(App, 'collect', lambda self: None)
    root = native_root
    app = App(root, SimpleNamespace(adb='', serial='', frida_remote=None))
    root.update()
    yield app
    for timer in root.tk.call('after', 'info'):
        root.after_cancel(timer)
    for widget in root.winfo_children():
        widget.destroy()


def sample():
    row = dict(name='赫默', damage=1230., healing=615., recent_damage=100.,
               recent_healing=50., present=True, shield_absorbed=None,
               engagement_damage=None, engagement_healing=None,
               active_damage=None, active_healing=None, counter_snapshot_complete=True,
               event_coverage_complete=False, damage_by_type={'physical': 1230.},
               coverage_start=20., recent_damage_window=(20., 22.3),
               recent_healing_window=(20., 22.3), sources=['char_a'])
    other = dict(row, name='夜刀', present=False, damage=500., healing=0.,
                 recent_damage=0., recent_healing=0., damage_by_type={'physical': 500.})
    return dict(battle_id='battle-a', session='a', frame=669, game_time=22.3,
                captured_monotonic=time.monotonic(), statistics_complete=True,
                event_status='failed', event_error='observer unavailable',
                # Deliberately contradictory compatibility data must never reach the GUI.
                rows=[dict(id='legacy', damage=999999)], sum_damage=999999,
                statistics=dict(game_time=22.3, rows={'a': row, 'b': other}))


def publish(app, data):
    app.last = data
    app.render(data)
    app.root.update()


def test_default_panel_consumes_unified_result_and_no_hud(gui):
    assert len(gui.panels) == 1
    assert gui.panels[0].config.category is Category.DAMAGE
    assert gui.panels[0].config.ranking is Ranking.TOTAL
    assert gui.panels[0].config.rate_basis is RateBasis.RECENT_5S
    assert not [w for w in gui.root.winfo_children() if isinstance(w, tk.Toplevel)]
    data = sample()
    before = copy.deepcopy(data)
    publish(gui, data)
    view = gui.panels[0].view
    assert [(e.owner_id, e.value, e.rate) for e in view.entries] == [('a', 1230., 100.), ('b', 500., 0.)]
    assert data == before
    assert view.entries[1].portrait_muted
    assert '事件不可用' in gui.event_state


def test_default_panel_short_window_uses_statistics_not_battle_average(gui):
    stats = Statistics('short', 20)
    stats.register_source('s', 'a')
    stats.snapshot(20, {'s': dict(damage=1000, healing=500)})
    stats.snapshot(22.3, {'s': dict(damage=1230, healing=615)})
    data = sample()
    data['statistics'] = stats.result()
    publish(gui, data)
    assert gui.panels[0].view.entries[0].rate == pytest.approx(100)
    assert gui.panels[0].view.entries[0].value == 1230


def test_default_healing_unknown_and_incomplete_shares(gui):
    panel = gui.panels[0]
    panel.config = replace(panel.config, category=Category.HEALING)
    data = sample()
    data['statistics']['rows']['b']['healing'] = None
    publish(gui, data)
    assert [(e.owner_id, e.value, e.rate) for e in panel.view.entries] == [('a', 615., 50.)]
    assert panel.view.entries[0].share is None
    panel.config = replace(panel.config, category=Category.SHIELD)
    gui.render_panels()
    assert panel.view.entries == []
    assert panel.canvas.itemcget(panel.empty, 'state') == 'normal'


def test_multi_panel_reconciles_to_single_model_and_keeps_configuration_independent(gui):
    first = gui.panels[0]
    second = gui.duplicate_panel(first)
    second.config = replace(second.config, category=Category.HEALING, ranking=Ranking.RECENT)
    data = sample()
    before = copy.deepcopy(data)
    publish(gui, data)
    for panel in gui.panels:
        expected = render_panel(panel.config, rows_from_statistics(data['statistics']))
        assert panel.view == expected
        assert panel.source is data['statistics']['rows']
    assert first.config.category is Category.DAMAGE
    assert first.config.panel_id != second.config.panel_id
    assert data == before
    assert str(first.delete_button['state']) == 'normal'
    gui.delete_panel(second)
    assert len(gui.panels) == 1
    gui.delete_panel(first)
    assert len(gui.panels) == 1


def test_hover_stabilizes_order_pinned_detail_survives_hidden_row_and_resets_next_battle(gui):
    data = sample()
    publish(gui, data)
    panel = gui.panels[0]
    panel.pin('b')
    panel.set_hover(True)
    data['statistics']['rows']['b']['damage'] = 9999.
    publish(gui, data)
    assert panel.order == ['a', 'b']
    assert panel.pinned == 'b'
    assert '9999.0' in panel.detail.get()
    panel.set_hover(False)
    assert panel.order == ['b', 'a']
    panel.config = replace(panel.config, ranking=Ranking.RECENT)
    gui.render_panels()
    assert panel.order == ['a']
    assert panel.pinned == 'b'
    data['battle_id'] = 'battle-b'
    publish(gui, data)
    assert panel.pinned is None


def test_settings_prevent_shield_engagement_and_hud_receives_source(gui):
    panel = gui.panels[0]
    panel.controls['rate_basis'][0].set('接敌')
    panel.controls['category'][0].set('有效护盾')
    panel.configure_panel()
    assert panel.config.rate_basis is RateBasis.RECENT_5S
    assert str(panel.controls['rate_basis'][1]['state']) == 'disabled'
    received = []
    gui.hud_request = received.append
    gui.open_hud(panel)
    assert received == [panel]


def test_connection_error_not_erased_by_lifecycle_and_stale_history_are_distinct(gui):
    gui.events.put(('error', 'connection lost'))
    gui.events.put(('lifecycle', 'frozen'))
    gui.tick()
    assert gui.error == 'connection lost'
    assert 'connection lost' in gui.diagnostics.get()
    data = sample()
    data['captured_monotonic'] -= 10
    publish(gui, data)
    gui.error = ''
    gui.battle_status = 'active'
    gui.tick()
    assert '过期' in gui.status.get()
    gui.battle_status = 'ended'
    gui.tick()
    assert '历史' in gui.status.get()
    assert event_label('gap') != event_label('failed')
    assert '—' in detail_text(dict(name='未知'))


def test_responsive_wide_narrow_and_keyboard_selection(gui):
    gui.add_panel()
    publish(gui, sample())
    gui.root.geometry('1400x850')
    gui.root.update()
    assert gui.layout_columns == 2
    gui.root.geometry('500x650')
    gui.root.update()
    assert gui.layout_columns == 1
    panel = gui.panels[0]
    panel.navigate(1)
    assert panel.pinned == 'a'
    assert panel.canvas.winfo_width() > 300


def test_offline_portraits_known_gray_and_unknown_fallback(gui):
    normal = gui.portraits.get('char_002_amiya', False, 36)
    muted = gui.portraits.get('char_002_amiya', True, 36)
    assert normal.width() == muted.width() == 36
    assert normal.get(18, 18) != muted.get(18, 18)
    assert len(set(muted.get(18, 18))) == 1
    assert gui.portraits.get('unknown', False, 36) is None


def test_reliable_zero_unknown_and_waiting_empty_states(gui):
    config = gui.panels[0].config
    assert '等待可靠战斗数据' in empty_text(config, {})
    assert '尚无可靠数据' in empty_text(config, {'a': dict(damage=None)})
    assert '已知为零' in empty_text(config, {'a': dict(damage=0, counter_snapshot_complete=True)},
                                  team_complete=True)
    assert '覆盖不完整' in empty_text(config, {'a': dict(damage=0, counter_snapshot_complete=False)})


@pytest.mark.parametrize('category', [Category.DAMAGE, Category.HEALING])
def test_unmapped_output_never_claims_team_is_reliably_zero(gui, category):
    from battle_lifecycle import BattleRuntime
    from test_battle_lifecycle import FakeCollector

    collector, runtime = FakeCollector(), BattleRuntime()
    collector.sample['rows'][0].update(damage=0., healing=0., physical=0.)
    collector.sample['rows'].append(dict(collector.sample['rows'][0], id='mystery',
                                        damage=500., healing=200., physical=500.))
    runtime.poll(collector)
    data = runtime.display_sample()
    assert data['statistics']['unknown_sources'] == ['mystery']
    assert not data['statistics_complete']
    assert data['statistics']['rows']['a']['counter_snapshot_complete']
    panel = gui.panels[0]
    panel.config = replace(panel.config, category=category)
    publish(gui, data)
    assert not panel.view.entries
    text = panel.canvas.itemcget(panel.empty, 'text')
    assert '已知为零' not in text
    assert '已观察输出为零' in text and '未知' in text

    # A complete battle with the same known zero may show reliable zero.
    # Unknown sources remain unknown for their battle even after disappearing.
    collector.sample['rows'].pop()
    runtime = BattleRuntime()
    runtime.poll(collector)
    publish(gui, runtime.display_sample())
    assert '已知为零' in panel.canvas.itemcget(panel.empty, 'text')


@pytest.mark.parametrize('bounds,point', [
    ((0, 0, 800, 560), (790, 550)),
    ((-1080, 0, 0, 1880), (-10, 1870)),
    ((0, -900, 1200, 0), (1190, -10)),
])
def test_tooltip_all_edges_stay_in_pointer_monitor_work_area(gui, monkeypatch, bounds, point):
    import gui_panel

    panel = gui.panels[0]
    # A tall, offset neighbour must not turn the desktop's unoccupied area
    # or the taskbar into usable space for the pointer's monitor.
    monkeypatch.setattr(panel.canvas, 'winfo_vrootx', lambda: -1080)
    monkeypatch.setattr(panel.canvas, 'winfo_vrooty', lambda: -900)
    monkeypatch.setattr(panel.canvas, 'winfo_vrootwidth', lambda: 3000)
    monkeypatch.setattr(panel.canvas, 'winfo_vrootheight', lambda: 2820)
    monkeypatch.setattr(gui_panel, 'monitor_work_area', lambda *args: bounds, raising=False)
    panel.tooltip._open('完整数值 12300.123456789\n近 5 秒速率 100.0\n占比 0.123456789', *point)
    gui.root.update()
    window = panel.tooltip.window
    x, y = window.winfo_rootx(), window.winfo_rooty()
    left, top, right, bottom = bounds
    assert left <= x and x + window.winfo_width() <= right
    assert top <= y and y + window.winfo_height() <= bottom
    assert y + window.winfo_height() <= point[1], 'bottom edge should open above pointer'
    panel.tooltip.hide()
