"""Offline Windows history/share acceptance. Pillow is used only for visual QA.

Does not connect to or operate the game. --scale adjusts Tk text scaling only.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from app import App, enable_dpi_awareness
from battle_history import BattleHistory, BattleRecord
from battle_share import json_bytes, record_labels, write_export
from panel_model import Category, Ranking, RateBasis
from statistics_screenshot import statistics_png
from test_battle_history import record
from test_gui import sample
from verify_gui import capture_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/history-validation')
    parser.add_argument('--scale', type=int, choices=(100, 125, 150, 200), default=100)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    history = BattleHistory(output / 'history')
    stage_ids = ('Obt/Main/level_main_01-07', 'Activities/ACT3D0/level_act3d0_08',
                 'Obt/Roguelike/RO6/level_rogue6_1-3', 'Obt/Unknown/level_offline_9-9')
    for index in range(101):
        data = record(index, stage_id=stage_ids[index % 4], practice=index % 2 == 0,
                      outcome=('victory', 'defeat', 'abandoned', 'unknown')[index % 4]).to_dict()
        base = data['statistics']['rows']['a']
        rows = {}
        for row_index, name in enumerate(('赫默', '夜刀', '阿米娅', '闪灵', '山', '凯瑟琳', '梅尔',
                                           '歌蕾蒂娅', '普罗旺斯', '天火', '测试长姓名用于检查换行不覆盖数值')):
            row = dict(base, name=name, damage=12300.123456789/(row_index+1),
                       healing=6150.987654321/(row_index+1), present=row_index % 3 != 1,
                       recent_damage=100.123456/(row_index+1), recent_healing=50.123456/(row_index+1),
                       engagement_damage=None, engagement_healing=80.123456/(row_index+1),
                       active_damage=None, active_healing=1.,
                       recent_damage_window=[10., 12.], recent_healing_window=[10., 12.],
                       damage_by_type={'physical': 8000/(row_index+1), 'arts': 4300.123456789/(row_index+1)})
            rows[f'fixture-{row_index}'] = row
        data['statistics']['rows'] = rows
        data['event_status'], data['event_error'] = 'failed', '离线样本：未观测事件；盾未知'
        history.save(BattleRecord.from_dict(data))
    result = history.list_recent()
    assert len(result.records) == 100 and result.records[0].battle_id == 'battle:100'
    labels = {record_labels(item)[0] for item in result.records}
    assert {'1-7', 'OF-8 汐斯塔狂想曲', '急不可耐', 'offline_9-9'} <= labels
    archive_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in history.directory.glob('*.json')}
    enable_dpi_awareness()
    root = tk.Tk()
    root.tk.call('tk', 'scaling', args.scale / 100 * 96 / 72)
    App.collect = lambda self: None
    app = App(root, SimpleNamespace(adb='', serial='', frida_remote=None))
    app.history = history
    app.connection = '离线界面验收样本 · 未连接游戏'
    app.last = sample()
    app.render(app.last)
    app.export()
    browser = app.history_window
    try:
        root.update()
        selected = browser.selected
        assert selected.battle_id == 'battle:100'
        captures = []
        for name, geometry in (('wide', '1200x980+10+10'), ('narrow', '600x900+10+10')):
            browser.geometry(geometry)
            if name == 'narrow' and args.scale >= 150:
                browser.toggle_list()
            root.update()
            assert capture_window(browser, output / f'{name}.png')
            assert browser.panel.canvas.winfo_height() >= round(150 * args.scale / 100)
            assert browser.panel.canvas.winfo_width() > 300
            assert browser.png_button.winfo_rooty() + browser.png_button.winfo_height() <= (
                browser.winfo_rooty()+browser.winfo_height())
            captures.append(dict(name=name, width=browser.winfo_width(), height=browser.winfo_height()))
        browser.board.yview_moveto(1)
        root.update()
        assert capture_window(browser, output / 'details-scroll.png')
        from PIL import Image
        exports = []
        for category, ranking, rate in ((Category.DAMAGE, Ranking.TOTAL, RateBasis.RECENT_5S),
                (Category.HEALING, Ranking.RECENT, RateBasis.ENGAGEMENT),
                (Category.SHIELD, Ranking.TOTAL, RateBasis.RECENT_5S)):
            config = replace(browser.panel.config, category=category, ranking=ranking, rate_basis=rate)
            browser.panel.config = config
            browser.render()
            path = output / f'share-{category.value}.png'
            write_export(path, statistics_png(browser, selected, config),
                         history_directory=history.directory, overwrite=True)
            image = Image.open(path)
            selection = json.loads(image.info['ArkDPS.selection'])
            assert selection['battle_id'] == selected.battle_id
            assert selection['panel'] == config.to_dict()
            assert selection['owners'] == [e.owner_id for e in browser.panel.view.entries]
            assert len(image.getcolors(image.width*image.height)) > 20
            if category is not Category.SHIELD:
                assert image.height > 1000, 'all rows must survive beyond viewport height'
                band = image.crop((0, image.height-150, image.width, image.height))
                assert len(band.getcolors(band.width*band.height)) > 20, 'last rows/footer must be painted'
            exports.append(dict(path=path.name, dimensions=image.size, selection=selection,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        write_export(output / 'selected.json', json_bytes(selected),
                     history_directory=history.directory, overwrite=True)
        shared = json.loads((output / 'selected.json').read_bytes())
        display = shared.pop('display')
        assert display['stage']['stage_id'] == selected.metadata['stage_id']
        assert display['stage']['stage_name'] == record_labels(selected)[0]
        assert shared == selected.to_dict()
        # A live boundary while the history window is open must leave its selection frozen.
        next_sample = sample()
        next_sample['battle_id'] = 'offline-next-battle'
        next_sample['statistics']['rows']['a']['damage'] = 98765.
        app.events.put(('sample', next_sample))
        app.tick()
        root.update()
        assert browser.selected.to_dict() == selected.to_dict()
        assert app.panels[0].view.entries[0].value == 98765.
        assert archive_hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in history.directory.glob('*.json')}
        receipt = dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                       worktree=subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True),
                       scale=args.scale, scale_scope='Tk text scaling; not native Windows DPI acceptance',
                       source='deterministic offline fixtures; no game connection', count=len(browser.records),
                       screenshots=captures, exports=exports,
                       live_continued=True, history_unchanged=True,
                       code_sha256={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                                    ('app.py', 'gui_history.py', 'battle_share.py', 'statistics_screenshot.py')})
        (output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(result='PASS', head=receipt['head'], output=str(output), scale=args.scale)))
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
