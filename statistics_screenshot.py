"""PNG share image of the entire selected PanelView, using Windows memory bitmaps."""
import ctypes
from ctypes import wintypes
import json
import struct
import sys
import zlib

from battle_share import record_labels, record_view, record_complete
from gui_panel import BG, SECONDARY, empty_text
from panel_model import Category
from ui_theme import PALETTES, theme_key


def display_number(value):
    """User-visible numbers keep at most one decimal in the panel's 0.# form."""
    if value is None:
        return '—'
    return f'{value:.1f}'.rstrip('0').rstrip('.')


class _Bitmap:
    """Own native resources for one image; drawing never touches desktop windows."""
    def __init__(self):
        if sys.platform != 'win32':
            raise OSError('统计截图需要 Windows；仍可导出 JSON。')
        self.user, self.gdi = ctypes.windll.user32, ctypes.windll.gdi32
        gdi, user = self.gdi, self.user
        gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
        gdi.SelectObject.argtypes, gdi.SelectObject.restype = [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE
        gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi.DeleteDC.argtypes = [wintypes.HDC]
        gdi.CreateFontW.argtypes = [ctypes.c_int]*5 + [wintypes.DWORD]*8 + [wintypes.LPCWSTR]
        gdi.CreateFontW.restype = wintypes.HFONT
        gdi.CreateSolidBrush.argtypes, gdi.CreateSolidBrush.restype = [wintypes.DWORD], wintypes.HBRUSH
        gdi.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
        gdi.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        user.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                   ctypes.POINTER(wintypes.RECT), wintypes.UINT]
        user.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
        gdi.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        gdi.CreateDIBSection.restype = wintypes.HBITMAP
        gdi.GdiFlush.argtypes = []
        self.dc = gdi.CreateCompatibleDC(None)
        self.fonts, self.bitmap, self.previous_bitmap, self.previous_font = {}, None, None, None
        if not self.dc:
            raise OSError('无法创建截图内存上下文')
        try:
            for bold, size in ((False, 17), (True, 21)):
                handle = gdi.CreateFontW(-size, 0, 0, 0, 700 if bold else 400,
                                        0, 0, 0, 1, 0, 0, 4, 0, 'Microsoft YaHei UI')
                if not handle:
                    raise OSError('无法创建截图字体')
                self.fonts[bold] = handle
            self.previous_font = gdi.SelectObject(self.dc, self.fonts[False])
            gdi.SetBkMode(self.dc, 1)  # TRANSPARENT
        except Exception:
            self.close()
            raise

    def close(self):
        if self.previous_font:
            self.gdi.SelectObject(self.dc, self.previous_font)
        if self.previous_bitmap:
            self.gdi.SelectObject(self.dc, self.previous_bitmap)
        for handle in self.fonts.values():
            self.gdi.DeleteObject(handle)
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
        if self.dc:
            self.gdi.DeleteDC(self.dc)
            self.dc = None

    @staticmethod
    def color(value):
        red, green, blue = bytes.fromhex(value.lstrip('#'))
        return red | green << 8 | blue << 16

    def text(self, value, x, y, width, *, bold=False, color=SECONDARY, right=False, measure=False):
        self.gdi.SelectObject(self.dc, self.fonts[bold])
        self.gdi.SetTextColor(self.dc, self.color(color))
        rect = wintypes.RECT(x, y, x+width, y+10000)
        flags = 0x800 | 0x10 | (2 if right else 0)  # NOPREFIX | WORDBREAK | RIGHT
        if measure:
            flags |= 0x400  # CALCRECT
        height = self.user.DrawTextW(self.dc, value, len(value), ctypes.byref(rect), flags)
        if not height:
            raise OSError('绘制截图文字失败')
        return height

    def rectangle(self, x, y, width, height, color):
        brush = self.gdi.CreateSolidBrush(self.color(color))
        if not brush:
            raise OSError('无法创建截图画刷')
        try:
            rect = wintypes.RECT(round(x), round(y), round(x+width), round(y+height))
            if not self.user.FillRect(self.dc, ctypes.byref(rect), brush):
                raise OSError('绘制截图色块失败')
        finally:
            self.gdi.DeleteObject(brush)

    def allocate(self, width, height):
        class Header(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                        ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                        ('image_size', wintypes.DWORD), ('xppm', wintypes.LONG), ('yppm', wintypes.LONG),
                        ('colors', wintypes.DWORD), ('important', wintypes.DWORD)]
        # Top-down BGRA buffer, wholly independent of screen size/DPI/occlusion.
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32)
        self.pixels = ctypes.c_void_p()
        self.bitmap = self.gdi.CreateDIBSection(self.dc, ctypes.byref(header), 0,
                                               ctypes.byref(self.pixels), None, 0)
        if not self.bitmap or not self.pixels.value:
            raise OSError('无法分配截图位图')
        self.previous_bitmap = self.gdi.SelectObject(self.dc, self.bitmap)
        self.rectangle(0, 0, width, height, BG)

    def rgb(self, width, height):
        if not self.gdi.GdiFlush():
            raise OSError('截图绘制未完成')
        raw = ctypes.string_at(self.pixels, width*height*4)
        rgb = bytearray(width*height*3)
        rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
        return bytes(rgb)


def encode_png(width, height, rgb, selection):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    stride = width * 3
    scanlines = b''.join(b'\0' + rgb[y*stride:(y+1)*stride] for y in range(height))
    provenance = b'ArkDPS.selection\0' + json.dumps(selection, ensure_ascii=True).encode('ascii')
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'tEXt', provenance) + chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))


def statistics_png(parent, record, config, *, theme=None):
    """Render all rows from a frozen record; no scroll position or live sample is read."""
    key = theme if theme is not None else theme_key(parent)
    p = PALETTES[key]
    view = record_view(record, config)
    data = record.to_dict()
    image = _Bitmap()
    try:
        elements, y = [], 18
        def text(value, *, bold=False):
            nonlocal y
            height = image.text(value, 20, y, 920, bold=bold, measure=True)
            elements.append((value, y, bold))
            y += height + 10
        text('ArkDPS · 历史统计 · ' + view.title, bold=True)
        text(' · '.join(record_labels(record)))
        text('总伤害含过量；治疗含过量；过量伤害为事件派生且不改变排行；护盾仅实际吸收。未知显示 —，* 表示覆盖不完整。')
        text(f'{view.metric_label} · 速率：{view.rate_label or "不可用"} · '
             '近期统计仅含最后可靠时刻前的已观察窗口。')
        if config.category is Category.DAMAGE:
            text('物理（红） / 法术（蓝） / 元素（紫） / 真实（白）；灰条表示分类未完整覆盖。')
        if not view.share_known:
            text(view.share_reason)
        text('快照覆盖（游戏秒）：' + repr(data['snapshot_intervals']) +
             '\n事件状态：' + data['event_status'] + '；原因：' + (data['event_error'] or '—'))
        if not view.entries:
            text(empty_text(config, data['statistics']['rows'],
                            team_complete=record_complete(record)))
        # Wrap long names and show each row's observed interval without clipping.
        rows = []
        for entry in view.entries:
            name = entry.name + (' · 已离场' if entry.portrait_muted else '')
            name_height = image.text(name, 20, y, 430, measure=True)
            row = data['statistics']['rows'][entry.owner_id]
            coverage = (f'快照起点 {display_number(row.get("coverage_start"))} 秒 · 事件 '
                        f'{display_number(row.get("shield_coverage_start"))} → '
                        f'{display_number(row.get("event_coverage_end"))} 秒 · '
                        f'过量 {display_number(row.get("overkill_damage"))}')
            rows.append((entry, name, y, name_height, coverage))
            y += max(68, name_height + 40) + 26
        text('JSON 导出保留原值、来源与逐指标覆盖起止；此图采用当前面板的类别、排行与速率。')
        height = y + 10
        image.allocate(960, height)
        image.rectangle(0, 0, 960, height, p.background)
        for value, top, bold in elements:
            image.text(value, 20, top, 920, bold=bold, color=p.accent if bold else p.secondary)
        for entry, name, top, name_height, coverage in rows:
            image.rectangle(12, top-4, 936, max(54, name_height+23)+2, p.plot)
            image.text(name, 20, top, 430, color=p.plot_secondary if entry.portrait_muted else p.plot_text)
            image.text(entry.display_value + (' *' if entry.incomplete else '') + '  ' + entry.share_display,
                       480, top, 460, bold=True, color=p.plot_text, right=True)
            image.text(entry.rate_display if view.rate_supported else '速率 —',
                       800, top+32, 140, color=p.plot_secondary, right=True)
            start, width = 20, 760 * entry.bar_ratio
            segments = entry.segments if entry.segments_complete else ()
            if not segments:
                segments = ({'ratio': 1, 'color': '#697e8a' if config.category is Category.DAMAGE
                             else '#68c5a0' if config.category is Category.HEALING else '#e7be72'},)
            for segment in segments:
                segment_width = width * segment['ratio']
                if segment_width >= 1:
                    image.rectangle(start, top+name_height+10, segment_width, 9, segment['color'])
                start += segment_width
            image.text(coverage, 20, top+max(54, name_height+23), 920, color=p.secondary)
        return encode_png(960, height, image.rgb(960, height),
                          dict(battle_id=record.battle_id, panel=config.to_dict(), theme=key,
                               owners=[e.owner_id for e in view.entries]))
    finally:
        image.close()
