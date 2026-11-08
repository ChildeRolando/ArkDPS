"""Render native GUI fixtures on real Windows monitors, without touching the game.

Pillow is required only for screenshots. --scale changes Tk text scaling; it does
not change the user's Windows display settings. Native monitor DPI is recorded.
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
from character_catalog import load_character_names
from panel_model import Category, Ranking, RateBasis


def monitors():
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR,
                                       wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def visit(handle, dc, rect, param):
        area = rect.contents
        xdpi, ydpi = ctypes.c_uint(), ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(handle, 0, ctypes.byref(xdpi), ctypes.byref(ydpi))
        found.append(dict(bounds=[area.left, area.top, area.right, area.bottom], dpi=xdpi.value))
        return True
    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(visit), 0)
    return found


def capture_window(root, path):
    """Ask our own Tk window to paint into a bitmap; no desktop capture needed."""
    from PIL import Image
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    class BitmapHeader(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                    ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                    ('image_size', wintypes.DWORD), ('xppm', wintypes.LONG), ('yppm', wintypes.LONG),
                    ('colors', wintypes.DWORD), ('important', wintypes.DWORD)]
    hwnd = user.GetParent(root.winfo_id())
    width, height = root.winfo_width(), root.winfo_height()
    dc = user.GetDC(hwnd)
    if not dc:
        raise OSError('GetDC failed for the GUI window')
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, 3):
            raise OSError('PrintWindow failed for the GUI window')
        header = BitmapHeader(ctypes.sizeof(BitmapHeader), width, -height, 1, 32)
        raw = ctypes.create_string_buffer(width * height * 4)
        if not gdi.GetDIBits(memory, bitmap, 0, height, raw, ctypes.byref(header), 0):
            raise OSError('GetDIBits failed for the GUI window')
        image = Image.frombuffer('RGB', (width, height), raw, 'raw', 'BGRX', 0, 1)
        image.save(path)
        return image.getextrema() != ((0, 0), (0, 0), (0, 0))
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale', type=int, default=100, choices=(100, 125, 150, 200))
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/gui-validation')
    args = parser.parse_args()
    enable_dpi_awareness()
    displays = monitors()
    root = tk.Tk()
    root.tk.call('tk', 'scaling', args.scale / 100 * 96 / 72)
    App.collect = lambda self: None  # Deliberate offline fixture; no device access.
    app = App(root, SimpleNamespace(adb='', serial='', frida_remote=None))
    data = sample()
    names = load_character_names()
    keys = ['char_002_amiya', 'char_109_fmout', 'char_120_hibisc', 'char_147_shining',
            'char_103_angel', 'char_126_shotst', 'char_107_liskam', 'char_150_snakek',
            'char_240_wyvern', 'char_151_myrtle', 'char_215_mantic', 'char_102_texas']
    rows = {}
    for index, key in enumerate(keys):
        row = dict(data['statistics']['rows']['a'])
        row.update(name=names.get(key, key), damage=12300.123456789 / (index+1),
                   healing=6150.987654321 / (index+1),
                   present=index % 3 != 1, sources=[key],
                   damage_by_type=dict(physical=8000/(index+1), arts=4300.123456789/(index+1)))
        rows[key] = row
    data['statistics']['rows'] = rows
    app.connection = '离线界面验收样本 · 未连接游戏'
    app.last = data
    app.render(data)
    second = app.duplicate_panel(app.panels[0])
    second.config = replace(second.config, category=Category.HEALING, ranking=Ranking.RECENT,
                            rate_basis=RateBasis.ENGAGEMENT)
    app.render_panels()
    args.output.mkdir(parents=True, exist_ok=True)
    receipt = dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   code_sha256={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                                for p in ('app.py', 'gui_panel.py')},
                   scale_kind='Tk text scaling, not a Windows display setting change',
                   requested_scale=args.scale, native_monitors=displays, cases=[])
    try:
        for monitor_index, monitor in enumerate(displays):
            left, top, right, bottom = monitor['bounds']
            for mode, requested_width in (('wide', 1600), ('narrow', round(480*args.scale/100))):
                width, height = min(requested_width, right-left-48), min(round(780*args.scale/100), bottom-top-80)
                root.geometry(f'{width}x{height}')
                root.update()
                hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                ctypes.windll.user32.SetWindowPos(hwnd, 0, left+20, top+20, 0, 0, 0x0001 | 0x0040)
                root.lift()
                root.update()
                time.sleep(.25)
                root.update()
                panel = app.panels[0]
                assert panel.canvas.winfo_width() >= 300 * args.scale / 100, 'panel clipped'
                for control in panel.controls.values():
                    combo = control[1]
                    assert combo.winfo_width() >= 50, 'configuration control clipped'
                filename = f'scale-{args.scale}-monitor-{monitor_index}-{mode}.png'
                bbox = (root.winfo_rootx(), root.winfo_rooty(),
                        root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height())
                capture_valid = capture_window(root, args.output / filename)
                # Avatar hover and full-number hover both use the live source row.
                panel.motion(SimpleNamespace(x=12, y=16, x_root=bbox[0]+20, y_root=bbox[1]+200))
                assert names[keys[0]] in panel.tooltip.text
                panel.tooltip.hide()
                panel.tooltip._open(names[keys[0]], bbox[0]+20, bbox[1]+200)
                root.update()
                assert left <= panel.tooltip.window.winfo_rootx() < right, 'tooltip on wrong monitor'
                panel.tooltip.hide()
                panel.motion(SimpleNamespace(x=panel.canvas.winfo_width()-20, y=16,
                                             x_root=bbox[0]+200, y_root=bbox[1]+200))
                assert '12300.123456789' in panel.tooltip.text
                panel.tooltip.hide()
                panel.pin(keys[0])
                assert '12300.123456789' in panel.detail.get()
                panel.pin(None)
                receipt['cases'].append(dict(file=filename, geometry=root.geometry(),
                    columns=app.layout_columns, monitor=monitor_index, canvas_width=panel.canvas.winfo_width(),
                    bbox=bbox, tooltip_and_pin='pass', screenshot_valid=capture_valid))
        (args.output / f'scale-{args.scale}.json').write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(receipt, ensure_ascii=False))
        if not all(case['screenshot_valid'] for case in receipt['cases']):
            raise RuntimeError('Black screenshots cannot satisfy visual acceptance; see receipt')
    finally:
        app.stop.set()
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        root.destroy()


if __name__ == '__main__':
    main()
