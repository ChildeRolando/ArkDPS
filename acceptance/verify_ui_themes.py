"""ARK-19 native UI evidence with labelled synthetic data, not the game.

Real Tk windows and Win32 mouse input. The underlay is a private test window;
this does NOT certify a click received by MuMu/the game. Tray restore uses the
native callback message, not a physical icon double-click. Restart runs in a
new Python process. No collector, device or game process is accessed.
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
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from app import App, enable_dpi_awareness
from battle_history import BattleHistory
from layout_store import LayoutStore, Layout, LayoutLoadResult
from panel_model import Category
from statistics_screenshot import statistics_png
from test_battle_history import record
from test_gui import sample
from tray import TrayIcon, WM_LBUTTONDBLCLK, WM_TRAY_CALLBACK, _user32
from verify_gui import capture_window, monitors
from verify_hud import native_click
from hud import window_handle


def settle(root):
    for _ in range(8):
        root.update()
        time.sleep(.025)


def capture(widget, output, name):
    settle(widget._root())
    assert capture_window(widget, output / name), name


def finish(root, app):
    if app.settings_window is not None:
        app.settings_window.close()
    app.stop.set()
    app.stop_tray()
    for timer in root.tk.call('after', 'info'):
        root.after_cancel(timer)
    root.destroy()


def run(output, restore):
    output.mkdir(parents=True, exist_ok=True)
    enable_dpi_awareness()
    pointer = wintypes.POINT()
    user = ctypes.WinDLL('user32', use_last_error=True)
    restore_pointer = user.GetCursorPos(ctypes.byref(pointer))
    root = tk.Tk()
    App.collect = lambda self: None  # explicit offline fixture boundary
    store = LayoutStore(output / 'layout.json')
    errors = []
    root.report_callback_exception = lambda *exc: errors.append(repr(exc))
    app = App(root, SimpleNamespace(adb='', serial='', frida_remote=None), layout_store=store,
              layout_load=None if restore else LayoutLoadResult(Layout.default(), True),
              tray_factory=lambda app: TrayIcon('ArkDPS · ARK-19 离线验收',
                                                lambda action: app.events.put(('tray', action))))
    try:
        settle(root)
        if restore:
            assert app.theme == 'original'
            assert len(app.panels) == 2 and len(app.huds) == 1
            hud = next(iter(app.huds.values()))
            expected = json.loads((output / 'expected.json').read_text('utf-8'))
            assert hud.placement() == expected['hud'], (hud.placement(), expected)
            assert [p.config.to_dict() for p in app.panels] == expected['panels']
            capture(root, output, 'restart-original.png')
            return {'status': 'PASS', 'fresh_process': True, 'theme': app.theme,
                    'hud': hud.placement(), 'callback_errors': errors}
        receipt = {'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                   'native_monitors': monitors(), 'fixture': 'Synthetic data; no game connection',
                   'manual_gaps': ['Actual click received by MuMu/game window',
                                   'Physical shell tray icon double-click (native callback tested)'],
                   'native_clicks': {}, 'callback_errors': errors}
        app.history = BattleHistory(output / 'history')
        for i, outcome in enumerate(('victory', 'defeat', 'abandoned')):
            app.history.save(record(i, stage_id=f'离线验收 OF-{i}', outcome=outcome, practice=i == 0))
        data = sample()
        app.connection = '离线验收 · 合成统计；未连接游戏'
        data['event_status'], data['event_error'] = 'failed', '离线样本：事件不可用，盾与接敌未知'
        app.events.put(('sample', data))
        app.tick()
        first = app.panels[0]
        second = app.duplicate_panel(first)
        second.config = replace(second.config, category=Category.HEALING)
        app.render_panels()
        first.pin('a')
        root.geometry('1080x900+30+30')
        hud = app.create_hud(first)
        hud.window.geometry('420x400+1140+140')
        hud.set_opacity(83)
        app.export()
        browser = app.history_window
        frozen = browser.selected.to_dict()
        browser.withdraw()
        for theme in ('lone-trail', 'original'):
            app.set_theme(theme)
            settle(root)
            capture(root, output, theme + '-main.png')
            capture(hud.window, output, theme + '-hud.png')
            browser.deiconify()
            capture(browser, output, theme + '-history.png')
            (output / (theme + '-share.png')).write_bytes(
                statistics_png(browser, browser.selected, browser.panel.config))
            browser.withdraw()
            app.open_settings()
            capture(app.settings_window, output, theme + '-settings.png')
            app.settings_window.close()
        # Keep GUI normal: minimizing it also minimizes its owned test underlay,
        # which would accidentally test an unrelated desktop application.
        underlay = tk.Toplevel(root)
        underlay.title('ARK-19 · 离线单屏点击验收（非游戏）')
        underlay.geometry('800x650+700+50')
        canvas = tk.Canvas(underlay, bg='#26343d')
        canvas.pack(fill='both', expand=True)
        canvas.create_text(24, 24, anchor='nw', fill='white', text='离线测试窗口 · 非游戏')
        hits = []
        canvas.bind('<Button-1>', lambda e: hits.append('underlay'))
        hud.panel.canvas.bind('<Button-1>', lambda e: hits.append('hud'), add='+')
        hud.window.geometry('420x400+800+150')
        settle(root)
        user = _user32()
        user.WindowFromPoint.argtypes = [wintypes.POINT]
        user.WindowFromPoint.restype = wintypes.HWND
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        for theme in ('lone-trail', 'original'):
            app.set_theme(theme)
            underlay.lift()
            underlay.focus_force()
            hud.window.lift()
            settle(root)
            point = (hud.panel.canvas.winfo_rootx()+100, hud.panel.canvas.winfo_rooty()+20)
            start = len(hits)
            for locked in (False, True, False):
                hud.set_locked(locked)
                # An unlocked HUD click can activate its GUI owner, raising
                # it above the underlay. Restore the test scene each time,
                # as a player would foreground the game before using a HUD.
                underlay.lift()
                underlay.focus_force()
                hud.window.lift()
                settle(root)
                actual = user.GetAncestor(user.WindowFromPoint(wintypes.POINT(*point)), 2)
                assert actual == window_handle(underlay if locked else hud.window), (theme, locked, actual, point, underlay.geometry(), underlay.winfo_viewable(), hud.window.geometry(), hud.panel.canvas.winfo_geometry())
                native_click(root, *point)
            assert hits[start:] == ['hud', 'underlay', 'hud'], hits
            receipt['native_clicks'][theme] = hits[start:]
        receipt['input_scene'] = {'underlay': underlay.geometry(), 'hud': hud.window.geometry()}
        underlay.destroy()
        browser.query.set('不存在的关卡')
        assert not browser.listing.get_children()
        data['battle_id'] = 'next-battle'
        data['statistics']['rows']['a']['damage'] = 9876.
        app.events.put(('sample', data))
        app.tick()
        browser.refresh()
        assert browser.selected.to_dict() == frozen
        assert hud.panel.view.entries[0].value == 9876.
        receipt['history_rollover_preserved'] = True
        assert app.tray and app.tray.available, getattr(app.tray, 'error', '')
        app.close()
        settle(root)
        assert root.state() == 'withdrawn' and not app.stop.is_set()
        data['statistics']['rows']['a']['damage'] = 9988.
        app.events.put(('sample', data))
        app.tick()
        assert hud.panel.view.entries[0].value == 9988.
        capture(hud.window, output, 'hud-gui-hidden.png')
        assert user.PostMessageW(wintypes.HWND(app.tray.hwnd), WM_TRAY_CALLBACK, 1, WM_LBUTTONDBLCLK)
        settle(root)
        assert root.state() == 'normal'
        hud.set_locked(True)
        before = hud.placement()
        app.set_theme('lone-trail')
        app.set_theme('original')
        settle(root)
        assert hud.locked and hud.placement() == before
        hud.lock_var.set(False)
        hud.toggle_lock()
        assert not hud.locked
        receipt['tray_restore_and_unlock'] = True
        capture(root, output, 'tray-restored.png')
        hud.set_locked(True)
        app.save_layout_now()
        expected = {'hud': hud.placement(), 'panels': [p.config.to_dict() for p in app.panels]}
        (output / 'expected.json').write_text(json.dumps(expected), 'utf-8')
        receipt['status'] = 'PASS_OFFLINE_NATIVE'
        assert not errors, errors
        return receipt
    finally:
        finish(root, app)
        if restore_pointer:
            user.SetCursorPos(pointer.x, pointer.y)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/ui-theme-validation')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.restore:
        # Invalidate old success evidence before touching native windows.
        (args.output / 'receipt.json').write_text(json.dumps({'status': 'RUNNING'}), 'utf-8')
    result = run(args.output, args.restore)
    if not args.restore:
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--restore',
                                '--output', str(args.output)], capture_output=True, text=True, timeout=120)
        assert child.returncode == 0, child.stderr
        result['restart'] = json.loads(child.stdout)
        result['files_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in [*ROOT.glob('*.py'), Path(__file__).resolve()]}
        result['screenshots_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in args.output.glob('*.png')}
        (args.output / 'receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
