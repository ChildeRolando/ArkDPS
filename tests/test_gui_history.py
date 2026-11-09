from dataclasses import replace
import json
import struct
from unittest.mock import patch
import zlib

import pytest

from battle_history import BattleHistory
from gui_history import HistoryWindow
from panel_model import Category, Ranking, RateBasis
from statistics_screenshot import statistics_png
from test_battle_history import record
from test_gui import gui, native_root, sample, publish


@pytest.fixture
def browser(gui, tmp_path):
    gui.history = BattleHistory(tmp_path / 'history')
    for i, outcome in enumerate(('victory', 'defeat', 'abandoned', 'unknown')):
        gui.history.save(record(i, stage_id=f'测试关卡-{i}', outcome=outcome, practice=i == 0))
    gui.export()
    gui.root.update()
    return gui.history_window


def choose(browser, battle_id):
    key = next(key for key, value in browser.records.items() if value.battle_id == battle_id)
    browser.listing.selection_set(key)
    browser.select_record()


def test_history_available_without_connection_and_all_outcomes_open(browser, gui):
    assert gui.last is None
    assert str(gui.export_button['state']) == 'normal'
    for i in range(4):
        choose(browser, f'battle:{i}')
        assert browser.selected.metadata['outcome'] == ('victory', 'defeat', 'abandoned', 'unknown')[i]
        assert browser.panel.view.entries[0].value == 230.
        assert '历史' in browser.summary.get()
    gui.export()
    assert gui.history_window is browser


def test_history_stays_selected_across_live_updates_new_battle_and_refresh(browser, gui):
    choose(browser, 'battle:1')
    before = browser.selected.to_dict()
    history_view = browser.panel.view
    # HUD's documented hook consumes this live source panel, never the history panel.
    consumers = []
    gui.hud_request = consumers.append
    gui.open_hud(gui.panels[0])
    first = sample()
    gui.events.put(('sample', first))
    gui.tick()
    second = sample()
    second['battle_id'] = 'next-battle'
    second['statistics']['rows']['a']['damage'] = 9876.
    gui.events.put(('sample', second))
    gui.tick()
    assert consumers[0].view.entries[0].value == 9876.
    assert gui.current_battle == 'next-battle'
    gui.history.save(record(20, outcome='defeat'))
    browser.refresh()
    gui.root.update()
    assert browser.selected.to_dict() == before
    assert browser.panel.view == history_view
    assert len(browser.records) == 5
    browser.destroy()
    assert not gui.stop.is_set()
    assert gui.panels[0].view.entries[0].value == 9876.


def test_cancel_and_decline_collision_do_not_produce_or_modify(browser, tmp_path):
    target = tmp_path / 'existing.json'
    target.write_bytes(b'original')
    original = browser.selected.to_dict()
    with patch('gui_history.filedialog.asksaveasfilename', return_value=''), \
         patch('gui_history.json_bytes', side_effect=AssertionError('must not serialize')):
        browser.export_json()
    with patch('gui_history.filedialog.asksaveasfilename', return_value=str(target)), \
         patch('gui_history.messagebox.askyesno', return_value=False), \
         patch('gui_history.json_bytes', side_effect=AssertionError('must not serialize')):
        browser.export_json()
    assert target.read_bytes() == b'original'
    assert browser.selected.to_dict() == original


def test_json_dialog_freezes_selection_and_reports_write_failure(browser, tmp_path):
    target = tmp_path / 'share.json'
    choose(browser, 'battle:0')
    original = browser.selected.to_dict()
    def dialog(**kwargs):
        choose(browser, 'battle:1')
        return str(target)
    with patch('gui_history.filedialog.asksaveasfilename', side_effect=dialog):
        browser.export_json()
    assert json.loads(target.read_bytes()) == original
    with patch('gui_history.filedialog.asksaveasfilename', return_value=str(target)), \
         patch('gui_history.messagebox.askyesno', return_value=True), \
         patch('gui_history.write_export', side_effect=OSError('disk full')), \
         patch('gui_history.messagebox.showerror') as error:
        browser.export_json()
    assert 'disk full' in error.call_args.args[1]
    assert json.loads(target.read_bytes()) == original
    assert len(browser.history.list_recent().records) == 4


def test_export_ui_cannot_overwrite_history(browser):
    path = browser.history.path_for(browser.selected.battle_id)
    before = path.read_bytes()
    with patch('gui_history.filedialog.asksaveasfilename', return_value=str(path)), \
         patch('gui_history.messagebox.askyesno') as confirm, \
         patch('gui_history.messagebox.showerror') as error:
        browser.export_json()
    confirm.assert_not_called()
    assert '历史目录' in error.call_args.args[1]
    assert path.read_bytes() == before


def test_confirmed_ui_replacement_preserves_record_precision(browser, tmp_path):
    target = tmp_path / 'existing.json'
    target.write_bytes(b'previous export')
    with patch('gui_history.filedialog.asksaveasfilename', return_value=str(target)), \
         patch('gui_history.messagebox.askyesno', return_value=True) as confirm:
        browser.export_json()
    confirm.assert_called_once()
    assert json.loads(target.read_bytes()) == browser.selected.to_dict()


def decode_png(payload):
    assert payload[:8] == b'\x89PNG\r\n\x1a\n'
    offset, chunks = 8, {}
    while offset < len(payload):
        size = struct.unpack('!I', payload[offset:offset+4])[0]
        kind, data = payload[offset+4:offset+8], payload[offset+8:offset+8+size]
        assert zlib.crc32(kind+data) == struct.unpack('!I', payload[offset+8+size:offset+12+size])[0]
        chunks[kind] = chunks.get(kind, b'') + data
        offset += size+12
    width, height = struct.unpack('!II', chunks[b'IHDR'][:8])
    return width, height, zlib.decompress(chunks[b'IDAT']), json.loads(chunks[b'tEXt'].split(b'\0', 1)[1])


@pytest.mark.parametrize('category,ranking,rate', [
    (Category.DAMAGE, Ranking.TOTAL, RateBasis.RECENT_5S),
    (Category.HEALING, Ranking.RECENT, RateBasis.ENGAGEMENT),
    (Category.SHIELD, Ranking.TOTAL, RateBasis.RECENT_5S),
])
def test_native_png_uses_selected_record_and_current_config(browser, category, ranking, rate):
    choose(browser, 'battle:2')
    config = replace(browser.panel.config, category=category, ranking=ranking, rate_basis=rate)
    browser.panel.config = config
    browser.render()
    width, height, pixels, selection = decode_png(statistics_png(browser, browser.selected, config))
    assert width == 960 and height > 150
    assert len(pixels) == (width*3+1)*height
    assert len(set(pixels)) > 20, 'native image must include text, not a blank/solid capture'
    assert selection['battle_id'] == 'battle:2'
    assert selection['panel'] == config.to_dict()
    assert selection['owners'] == [e.owner_id for e in browser.panel.view.entries]


def test_read_diagnostics_and_empty_history_are_visible(gui, tmp_path):
    gui.history = BattleHistory(tmp_path / 'history')
    gui.export()
    browser = gui.history_window
    assert '暂无可读战绩' in browser.notice.get()
    assert str(browser.json_button['state']) == 'disabled'
    gui.history.directory.mkdir()
    (gui.history.directory / 'broken.json').write_text('{')
    browser.refresh()
    assert 'invalid_record' in browser.notice.get()
    assert str(browser.png_button['state']) == 'disabled'


def test_list_can_collapse_and_details_remain_reachable_at_large_text(browser, gui):
    selected = browser.selected.to_dict()
    browser.toggle_list()
    gui.root.update()
    assert not browser.list_container.winfo_ismapped()
    browser.board.yview_moveto(1)
    gui.root.update()
    assert browser.panel.detail_label.winfo_ismapped()
    assert browser.selected.to_dict() == selected
    browser.toggle_list()
    gui.root.update()
    assert browser.list_container.winfo_ismapped()
