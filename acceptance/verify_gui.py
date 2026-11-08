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
    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                    ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
    user = ctypes.windll.user32
    user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    user.GetMonitorInfoW.restype = wintypes.BOOL
    def visit(handle, dc, rect, param):
        area = rect.contents
        xdpi, ydpi = ctypes.c_uint(), ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(handle, 0, ctypes.byref(xdpi), ctypes.byref(ydpi))
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not user.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise OSError('GetMonitorInfoW failed')
        work = info.work
        found.append(dict(bounds=[area.left, area.top, area.right, area.bottom], dpi=xdpi.value,
                          work_area=[work.left, work.top, work.right, work.bottom]))
        return True
    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(visit), 0)
    return found


def tooltip_bounds(panel, root, point, work_area, screenshot=None):
    """Open real tooltip content and check every edge against the physical work area."""
    text = panel.tooltip.text
    assert text, 'hover did not resolve any content'
    panel.tooltip.hide()
    panel.tooltip._open(text, *point)
    root.update()
    time.sleep(.1)
    root.update()
    window = panel.tooltip.window
    x, y = window.winfo_rootx(), window.winfo_rooty()
    bounds = [x, y, x + window.winfo_width(), y + window.winfo_height()]
    left, top, right, bottom = work_area
    assert left <= bounds[0] and bounds[2] <= right, (bounds, work_area)
    assert top <= bounds[1] and bounds[3] <= bottom, (bounds, work_area)
    if screenshot is not None:
        assert capture_window(window, screenshot), 'black tooltip screenshot'
    panel.tooltip.hide()
    return dict(pointer=point, bounds=bounds, work_area=work_area)


def capture_window(root, path):
    """Ask our own Tk window to paint into a bitmap; no desktop capture needed."""
    from PIL import Image
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
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
    hwnd = user.GetAncestor(root.winfo_id(), 2)  # GA_ROOT, including override-redirect tooltips
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
        if image.getextrema() == ((0, 0), (0, 0), (0, 0)):
            # Some Tk override-redirect wrappers do not forward PrintWindow.
            # Request the Tk client and its children to paint into the same DC.
            user.SendMessageW(root.winfo_id(), 0x0317, memory, 0x001E)  # WM_PRINT
            if not gdi.GetDIBits(memory, bitmap, 0, height, raw, ctypes.byref(header), 0):
                raise OSError('GetDIBits failed after WM_PRINT')
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
    parser.add_argument('--native-scale', type=int, choices=(100, 125, 150, 200),
                        help='Require this actual Windows display scale; do not override Tk scaling')
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/gui-validation')
    args = parser.parse_args()
    enable_dpi_awareness()
    displays = monitors()
    if args.native_scale is not None and not any(
            m['dpi'] == round(96 * args.native_scale / 100) for m in displays):
        parser.error(f'Windows {args.native_scale}% display required; observed monitors: {displays}. '
                     'Set the Windows display scale on the host, then rerun. '
                     '--scale only tests Tk text sizing and cannot satisfy this check.')
    root = tk.Tk()
    if args.native_scale is None:
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
    # The fixture calls panel.motion directly. Ignore live pointer input that
    # could replace or dismiss a tooltip while Tk processes its paint events.
    for panel in app.panels:
        for event in ('<Enter>', '<Leave>', '<Motion>', '<MouseWheel>'):
            panel.canvas.unbind(event)
    args.output.mkdir(parents=True, exist_ok=True)
    receipt = dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   code_sha256={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                                for p in ('app.py', 'gui_panel.py')},
                   scale_kind=('Native Windows display DPI; Tk scaling not overridden'
                               if args.native_scale else 'Tk text scaling, not a Windows display setting change'),
                   requested_scale=args.native_scale or args.scale, native_monitors=displays, cases=[])
    try:
        for monitor_index, monitor in enumerate(displays):
            left, top, right, bottom = monitor['bounds']
            work_left, work_top, work_right, work_bottom = monitor['work_area']
            scale = (monitor['dpi'] / 96 if args.native_scale else args.scale / 100)
            for mode, requested_width in (('wide', 1600), ('narrow', round(480*scale))):
                width, height = min(requested_width, right-left-48), min(round(780*scale), bottom-top-80)
                root.geometry(f'{width}x{height}')
                root.update()
                hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                ctypes.windll.user32.SetWindowPos(hwnd, 0, left+20, top+20, 0, 0, 0x0001 | 0x0040)
                root.lift()
                root.update()
                time.sleep(.25)
                root.update()
                panel = app.panels[0]
                window_dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                if args.native_scale:
                    assert window_dpi == monitor['dpi'], 'window DPI does not match this monitor'
                assert panel.canvas.winfo_width() >= 300 * scale, 'panel clipped'
                for control in panel.controls.values():
                    combo = control[1]
                    assert combo.winfo_width() >= 50, 'configuration control clipped'
                prefix = f'native-{args.native_scale}' if args.native_scale else f'scale-{args.scale}'
                filename = f'{prefix}-monitor-{monitor_index}-{mode}.png'
                bbox = (root.winfo_rootx(), root.winfo_rooty(),
                        root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height())
                capture_valid = capture_window(root, args.output / filename)
                # Avatar hover and full-number hover both use the live source row.
                panel.motion(SimpleNamespace(x=12, y=16, x_root=bbox[0]+20, y_root=bbox[1]+200))
                assert names[keys[0]] in panel.tooltip.text
                tooltips = [tooltip_bounds(panel, root, (bbox[0]+20, bbox[1]+200), monitor['work_area'])]
                panel.motion(SimpleNamespace(x=panel.canvas.winfo_width()-20, y=16,
                                             x_root=bbox[0]+200, y_root=bbox[1]+200))
                assert '12300.123456789' in panel.tooltip.text
                # Bottom/right edges of monitors with unequal heights, including
                # negative coordinates; use the numeric hover's full content.
                for point in ((work_right-8, work_bottom-8), (work_left+8, work_top+8)):
                    panel.motion(SimpleNamespace(x=panel.canvas.winfo_width()-20, y=16,
                                                 x_root=point[0], y_root=point[1]))
                    tooltip_file = args.output / f'{prefix}-monitor-{monitor_index}-{mode}-tooltip.png'
                    tooltips.append(tooltip_bounds(panel, root, point, monitor['work_area'],
                                                   tooltip_file if point[0] == work_right-8 else None))
                panel.pin(keys[0])
                assert '12300.123456789' in panel.detail.get()
                panel.pin(None)
                # Also hover a genuinely visible numeric row in the second
                # stacked panel, after scrolling it into the outer viewport.
                app.board.yview_moveto(1)
                root.update()
                lower = app.panels[-1]
                cx, cy = lower.canvas.winfo_rootx(), lower.canvas.winfo_rooty()
                px = min(cx + lower.canvas.winfo_width()-20, work_right-8)
                py = min(cy + lower.canvas.winfo_height()-4,
                         app.board.winfo_rooty()+app.board.winfo_height()-4, work_bottom-8)
                event = SimpleNamespace(x=px-cx, y=py-cy, x_root=px, y_root=py)
                assert 0 <= event.y < lower.canvas.winfo_height(), 'second panel is not visible'
                assert lower.hit(event), 'no visible data row at bottom of second panel'
                lower.motion(event)
                tooltips.append(tooltip_bounds(lower, root, (px, py), monitor['work_area']))
                app.board.yview_moveto(0)
                root.update()
                receipt['cases'].append(dict(file=filename, geometry=root.geometry(),
                    columns=app.layout_columns, monitor=monitor_index, canvas_width=panel.canvas.winfo_width(),
                    window_dpi=window_dpi, tk_scaling=float(root.tk.call('tk', 'scaling')),
                    bbox=bbox, tooltips=tooltips, tooltip_and_pin='pass', screenshot_valid=capture_valid))
        (args.output / f'{prefix}.json').write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(receipt, ensure_ascii=False))
        if not all(case['screenshot_valid'] for case in receipt['cases']):
            raise RuntimeError('Black screenshots cannot satisfy visual acceptance; see receipt')
    finally:
        app.stop.set()
        # Cancel widget-owned callbacks on their owner before the root timers.
        for panel in app.panels:
            panel.tooltip.hide()
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        root.destroy()


if __name__ == '__main__':
    main()
