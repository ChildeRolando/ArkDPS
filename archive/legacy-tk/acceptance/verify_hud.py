"""Windows HUD acceptance with synthetic live data and actual native mouse input.

No game/device access. Screenshots paint only our own windows with PrintWindow.
Requires Pillow for capture; does not change Windows display settings.
"""
import argparse
import ctypes
from ctypes import wintypes
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from app import App, enable_dpi_awareness
from test_gui import sample
from verify_gui import capture_window, monitors
from hud import window_handle
from panel_model import Category, Ranking, RateBasis, rows_from_statistics


def settle(root):
    for _ in range(8):
        root.update()
        time.sleep(.025)


def native_click(root, x, y):
    class MouseInput(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                    ('data', wintypes.DWORD), ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class InputUnion(ctypes.Union):
        _fields_ = [('mouse', MouseInput)]
    class Input(ctypes.Structure):
        _fields_ = [('kind', wintypes.DWORD), ('data', InputUnion)]
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user.SendInput.restype = wintypes.UINT
    assert user.SetCursorPos(x, y), 'SetCursorPos failed'
    settle(root)
    inputs = (Input * 2)(Input(0, InputUnion(mouse=MouseInput(flags=0x2))),
                         Input(0, InputUnion(mouse=MouseInput(flags=0x4))))
    count = user.SendInput(2, inputs, ctypes.sizeof(Input))
    assert count == 2, f'SendInput failed: {ctypes.get_last_error()}'
    settle(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/hud-validation')
    parser.add_argument('--hit-test-only', action='store_true',
                        help='validate native point routing when input-desktop access is denied; '
                             'does not certify physical clicks or streaming capture')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    enable_dpi_awareness()
    root = tk.Tk()
    App.collect = lambda self: None
    app = App(root, SimpleNamespace(adb='', serial='', frida_remote=None))
    errors = []
    root.report_callback_exception = lambda *exc: errors.append(repr(exc))
    user = ctypes.WinDLL('user32', use_last_error=True)
    pointer = wintypes.POINT()
    pointer_available = user.GetCursorPos(ctypes.byref(pointer))
    user.WindowFromPoint.argtypes = [wintypes.POINT]
    user.WindowFromPoint.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    receipt = dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                               text=True).strip(),
                   code_sha256={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                                for p in ('app.py', 'gui_panel.py', 'hud.py')},
                   fixture=('Synthetic unified snapshots; no game connection; ' +
                            ('Win32 point routing only' if args.hit_test_only else 'actual Win32 input')),
                   native_monitors=monitors(), cases=[])
    try:
        data = sample()
        for key, row in data['statistics']['rows'].items():
            row['engagement_damage'] = 82.3456789 if key == 'a' else None
            row['engagement_healing'] = 41.1728394 if key == 'a' else None
            row['shield_absorbed'] = 100.5 if key == 'a' else None
        first = app.panels[0]
        second = app.duplicate_panel(first)
        second.config = replace(second.config, category=Category.HEALING,
                                ranking=Ranking.RECENT, rate_basis=RateBasis.ENGAGEMENT)
        app.last = data
        app.render(data)
        a, b = app.open_hud(first), app.open_hud(second)
        root.update()
        assert a.panel.view == first.view and b.panel.view == second.view
        # A private test backdrop represents the single-monitor streaming scene.
        backdrop = tk.Toplevel(root)
        backdrop.title('ArkDPS · 离线单屏场景验收（合成数据）')
        backdrop.geometry('1100x780+40+40')
        canvas = tk.Canvas(backdrop, bg='#26343d', highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        canvas.create_text(24, 28, text='离线单屏场景 · HUD 应保持置顶，锁定后此窗口可接收点击',
                           anchor='nw', fill='#edf2f4', font=('Microsoft YaHei UI', 14))
        hits = []
        canvas.bind('<Button-1>', lambda e: hits.append('underlay'))
        a.panel.canvas.bind('<Button-1>', lambda e: hits.append('hud'), add='+')
        a.window.geometry('420x400+100+150')
        b.window.geometry('420x400+570+150')
        app.close()
        settle(root)
        assert root.state() == 'iconic' and not app.stop.is_set()
        assert a.window.winfo_viewable() and b.window.winfo_viewable()
        a.window.lift()
        settle(root)
        point = (a.panel.canvas.winfo_rootx()+100, a.panel.canvas.winfo_rooty()+20)
        routes = []
        def check_route(expected):
            target = user.WindowFromPoint(wintypes.POINT(*point))
            owner = user.GetAncestor(target, 2)
            assert owner == window_handle(expected), (target, owner, window_handle(expected))
            routes.append('hud' if expected is a.window else 'underlay')
        check_route(a.window)
        if not args.hit_test_only:
            native_click(root, *point)
            assert hits == ['hud'], hits
        a.set_locked(True)
        check_route(backdrop)
        if not args.hit_test_only:
            native_click(root, *point)
            assert hits == ['hud', 'underlay'], hits
        a.set_locked(False)
        check_route(a.window)
        if not args.hit_test_only:
            native_click(root, *point)
            assert hits == ['hud', 'underlay', 'hud'], hits
        receipt['native_clicks'] = hits
        receipt['native_point_routes'] = routes
        # Source configuration changes while GUI is hidden.
        for category in (Category.DAMAGE, Category.HEALING, Category.SHIELD):
            first.config = replace(first.config, category=category)
            app.render_panels()
            settle(root)
            assert a.panel.view == first.view
            a.panel.pin(None)
            filename = f'{category.value}-hud.png'
            assert capture_window(a.window, args.output / filename), 'black HUD capture'
            receipt['cases'].append(dict(category=category.value, screenshot=filename,
                                         order=a.panel.order, topmost=a.window.attributes('-topmost'),
                                         geometry=a.window.geometry(), opacity=a.window.attributes('-alpha')))
        # A historical GUI row must not redirect the overlay's current result.
        history = dict(rows={'history': dict(data['statistics']['rows']['a'], damage=999999.)})
        app.statistics, app.source_rows = history, rows_from_statistics(history)
        first.config = replace(first.config, category=Category.DAMAGE)
        app.render_panels()
        assert first.view.entries[0].value == 999999.
        assert a.panel.view.entries[0].value == 1230.
        a.panel.pin('a')
        data['battle_id'] = 'battle-next'
        data['statistics']['rows']['a']['damage'] = 27.
        app.events.put(('sample', data))
        app.tick()
        settle(root)
        assert a.panel.view.entries[-1].value == 27. and a.panel.pinned is None
        assert not app.stop.is_set()
        # Independent opacity/resize and GUI return remain usable after unlock.
        b_geometry = b.window.geometry()
        a.set_opacity(70)
        a.window.geometry('500x460+100+150')
        settle(root)
        assert b.window.geometry() == b_geometry
        assert float(a.window.attributes('-alpha')) == .7
        app.show_gui()
        root.geometry('1100x850+40+40')
        settle(root)
        assert capture_window(root, args.output / 'gui-controls.png'), 'black GUI capture'
        app.delete_panel(second)
        assert not b.window.winfo_exists() and a.window.winfo_exists()
        a.close()
        assert not app.huds and not app.stop.is_set()
        receipt.update(history_isolated=True, hidden_gui_live_switch=True,
                       source_delete_closes_hud=True, hud_close_keeps_session=True,
                       callback_errors=errors,
                       status='PARTIAL_INPUT_DESKTOP_UNVERIFIED' if args.hit_test_only else 'PASS',
                       streaming_capture_verified=False)
        assert not errors, errors
        for path in args.output.glob('*.png'):
            receipt.setdefault('screenshots_sha256', {})[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (args.output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    finally:
        app.stop.set()
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        root.destroy()
        if pointer_available:
            user.SetCursorPos(pointer.x, pointer.y)


if __name__ == '__main__':
    main()
