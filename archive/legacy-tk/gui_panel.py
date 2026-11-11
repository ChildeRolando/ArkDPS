"""Native presentation of PanelView; never computes or owns battle statistics."""
from dataclasses import replace
import json
from math import gcd
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, font

from ui_theme import palette, theme_key

from panel_model import (Category, Ranking, RateBasis, CATEGORY_LABELS,
                         RANKING_LABELS, RATE_LABELS)

BG, SURFACE, TEXT, SECONDARY = '#182127', '#26343d', '#edf2f4', '#b9d2de'


class PortraitAtlas:
    """One offline image source per GUI; unknown identities use a name badge."""
    def __init__(self, root):
        self.cache, self.atlases, self.manifest = {}, {}, {}
        try:
            folder = Path(__file__).resolve().parent / 'assets/portraits'
            self.manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
            self.atlases = {muted: tk.PhotoImage(master=root, file=str(folder / name))
                            for muted, name in ((False, 'normal.png'), (True, 'muted.png'))}
        except (OSError, ValueError, tk.TclError):
            self.manifest = {}

    def get(self, owner, muted, size):
        metadata = self.manifest.get('portraits', {}).get(owner)
        if metadata is None:
            return None
        key = (owner, muted, size)
        if key not in self.cache:
            index = metadata['index']
            cell, columns = self.manifest['cell_size'], self.manifest['columns']
            x, y = index % columns * cell, index // columns * cell
            atlas = self.atlases[muted]
            crop = tk.PhotoImage(master=atlas.tk, width=cell, height=cell)
            crop.tk.call(str(crop), 'copy', str(atlas), '-from', x, y, x + cell, y + cell)
            divisor = gcd(size, cell)
            self.cache[key] = crop.zoom(size // divisor).subsample(cell // divisor)
        return self.cache[key]


def exact(value):
    return '—' if value is None else repr(value)


def empty_text(config, source, *, team_complete=False):
    if not source:
        return '等待可靠战斗数据\n未知不会显示为零。'
    key = {Category.DAMAGE: 'damage', Category.HEALING: 'healing',
           Category.SHIELD: 'shield_absorbed'}[config.category]
    if config.ranking is Ranking.RECENT:
        key = 'recent_' + config.category.value
    if any(row.get(key) is None for row in source.values()):
        return '当前指标尚无可靠数据\n等待可靠采样；未知不记零。'
    completeness = ('event_coverage_complete' if config.category is Category.SHIELD
                    else 'counter_snapshot_complete')
    if not team_complete or not all(row.get(completeness, False) for row in source.values()):
        return '已观察输出为零 · 覆盖不完整\n未覆盖部分仍未知。'
    return '暂无输出（已知为零）\n近期输出归零后隐藏；累计记录保留。'


def detail_text(row):
    if row is None:
        return '点击头像固定详情；方向键选择，回车固定。'
    status = {True: '在场', False: '已离场', None: '在场状态未知'}.get(row.get('present'))
    lines = [f'干员详情 · {row.get("name", "未知干员")} · {status}',
             f'伤害（含过量） {exact(row.get("damage"))}    治疗（含过量） {exact(row.get("healing"))}',
             f'有效护盾（观测区间） {exact(row.get("shield_absorbed"))}',
             f'过量伤害（事件派生） {exact(row.get("overkill_damage"))}',
             f'接敌 DPS {exact(row.get("engagement_damage"))}    HPS {exact(row.get("engagement_healing"))}',
             f'伤害活跃秒 {exact(row.get("active_damage"))}    治疗活跃秒 {exact(row.get("active_healing"))}']
    lines.append(' · '.join(label + ' ' + exact((row.get('damage_by_type') or {}).get(key))
                 for key, label in (('physical', '物理'), ('arts', '法术'),
                                    ('elemental', '元素'), ('true', '真实'))))
    lines.append(f'快照覆盖起点 {exact(row.get("coverage_start"))} · 事件 '
                 f'{exact(row.get("shield_coverage_start"))} → {exact(row.get("event_coverage_end"))}')
    if not row.get('counter_snapshot_complete', False):
        lines.append('累计数据不完整')
    if not row.get('event_coverage_complete', False):
        lines.append('事件未完整覆盖；未知不记零；— 终点表示仍在观测或不可用')
    lines.append('来源 ' + ('、'.join(row.get('sources') or ()) or '—'))
    return chr(10).join(lines)


def monitor_work_area(widget, x, y):
    """Return the pointer monitor's usable rectangle in virtual-screen coordinates."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]

        user = ctypes.WinDLL('user32', use_last_error=True)
        user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user.MonitorFromPoint.restype = wintypes.HMONITOR
        user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
        user.GetMonitorInfoW.restype = wintypes.BOOL
        handle = user.MonitorFromPoint(wintypes.POINT(x, y), 2)  # nearest monitor
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if user.GetMonitorInfoW(handle, ctypes.byref(info)):
            return info.work.left, info.work.top, info.work.right, info.work.bottom
    left, top = widget.winfo_vrootx(), widget.winfo_vrooty()
    return left, top, left + widget.winfo_vrootwidth(), top + widget.winfo_vrootheight()


class Tooltip:
    def __init__(self, widget):
        self.widget, self.window, self.pending = widget, None, None
        self.text = ''
        widget.bind('<Destroy>', lambda e: self.hide(), add='+')

    def show(self, text, x, y):
        if self.text == text:
            return
        self.hide()
        self.text = text
        if text:
            self.pending = self.widget.after(350, lambda: self._open(text, x, y))

    def _open(self, text, x, y):
        self.pending = None
        self.window = tk.Toplevel(self.widget)
        self.window.withdraw()
        self.window.overrideredirect(True)
        ttk.Label(self.window, text=text, padding=8, wraplength=360,
                  style='PanelMuted.TLabel').pack()
        self.window.update_idletasks()
        # The virtual desktop may include space with no physical monitor.
        # Use this monitor's work area, including negative screen coordinates.
        left, top, right, bottom = monitor_work_area(self.widget, x, y)
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        tip_y = y + 18 if y + 18 + height <= bottom else y - 12 - height
        x = max(left, min(x + 12, right - width))
        y = max(top, min(tip_y, bottom - height))
        self.window.geometry(f'+{x}+{y}')
        self.window.deiconify()

    def hide(self):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.text = ''


class StatisticPanel(ttk.Frame):
    def __init__(self, parent, config, *, changed, duplicate, delete, open_hud, portraits,
                 history=False, compact=False):
        super().__init__(parent, padding=12, relief='solid', borderwidth=1, style='Panel.TFrame')
        self.config = config
        self.changed = changed
        self.portraits = portraits
        self.view, self.source = None, {}
        self.team_complete = False
        self.pinned = None
        self.order, self.items = [], {}
        self.hovering = False
        self.unit = max(1, font.nametofont('TkDefaultFont').metrics('linespace') / 17)
        self.name_font = font.Font(self, font=('Microsoft YaHei UI', 10))
        self.row_height = round(56 * self.unit)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self.title = ttk.Label(self, text=config.display_title, style='PanelTitle.TLabel',
                               font=('Microsoft YaHei UI', 11, 'bold'))
        self.title.grid(row=0, column=0, sticky='w')
        actions = ttk.Frame(self, style='PanelInner.TFrame')
        actions.grid(row=1, column=0, sticky='ew', pady=(6, 8))
        for text, command in (('复制', lambda: duplicate(self)),
                              ('删除', lambda: delete(self)),
                              ('打开到 HUD', lambda: open_hud(self))):
            ttk.Button(actions, text=text, command=command).pack(side='left', padx=(0, 6))
        self.delete_button = actions.winfo_children()[1]
        if history:
            actions.grid_remove()
        self.hud_controls = ttk.Frame(self, style='PanelInner.TFrame')
        self.hud_controls.grid(row=7, column=0, sticky='ew', pady=(6, 0))
        settings = ttk.Frame(self, style='PanelInner.TFrame')
        settings.grid(row=2, column=0, sticky='ew')
        self.controls = {}
        for col, (key, label, labels) in enumerate((
                ('category', '类别', CATEGORY_LABELS),
                ('ranking', '排行', RANKING_LABELS),
                ('rate_basis', '速率', RATE_LABELS))):
            settings.columnconfigure(col, weight=1, uniform='setting')
            box = ttk.Frame(settings, style='PanelInner.TFrame')
            box.grid(row=0, column=col, sticky='ew', padx=(0, 6))
            ttk.Label(box, text=label, style='PanelMuted.TLabel').pack(anchor='w')
            variable = tk.StringVar(value=labels[getattr(config, key)])
            combo = ttk.Combobox(box, textvariable=variable, values=list(labels.values()),
                                 state='readonly', width=8)
            combo.pack(fill='x', pady=(3, 0))
            combo.bind('<<ComboboxSelected>>', self.configure_panel)
            self.controls[key] = (variable, combo, labels)
        self.hint = tk.StringVar(value='等待可靠数据；未知不会显示为零。')
        self.hint_label = ttk.Label(self, textvariable=self.hint, style='PanelMuted.TLabel',
                                   wraplength=330)
        self.hint_label.grid(row=3, column=0, sticky='ew', pady=(8, 4))
        body = ttk.Frame(self, style='PanelInner.TFrame')
        body.grid(row=4, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=1,
                                highlightbackground=SURFACE, highlightcolor=SECONDARY,
                                height=round(224 * self.unit), width=1, takefocus=True)
        self.canvas.theme_role = 'plot'
        self.canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(body, command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.empty = self.canvas.create_text(12, 24, anchor='nw', fill=SECONDARY,
                                             font=('Microsoft YaHei UI', 10), text='等待可靠数据')
        self.orbits = [self.canvas.create_oval(0, 0, 1, 1, width=1, tags='orbit') for _ in range(3)]
        self.canvas.tag_lower('orbit')
        self.tooltip = Tooltip(self.canvas)
        self.canvas.bind('<Configure>', lambda e: self.draw())
        self.canvas.bind('<Enter>', lambda e: self.set_hover(True))
        self.canvas.bind('<Leave>', lambda e: self.set_hover(False))
        self.canvas.bind('<Motion>', self.motion)
        self.canvas.bind('<Button-1>', self.click)
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Down>', lambda e: self.navigate(1))
        self.canvas.bind('<Up>', lambda e: self.navigate(-1))
        self.canvas.bind('<Return>', lambda e: self.pin(self.pinned))
        self.detail = tk.StringVar(value=detail_text(None))
        self.detail_label = ttk.Label(self, textvariable=self.detail, wraplength=330,
                                     style='PanelMuted.TLabel', font=('Microsoft YaHei UI', 9))
        self.detail_label.grid(row=5, column=0, sticky='ew', pady=(8, 0))
        ttk.Button(self, text='收起详情', command=lambda: self.pin(None)).grid(
            row=6, column=0, sticky='w', pady=(5, 0))
        self.compact = compact
        self.apply_palette()
        if compact:
            for widget in (actions, settings, self.detail_label, self.hud_controls):
                widget.grid_remove()
            for widget in self.grid_slaves(row=6):
                widget.grid_remove()

    def apply_palette(self):
        p = palette(self)
        self.canvas.configure(bg=p.plot, highlightbackground=p.border, highlightcolor=p.accent)
        self.canvas.itemconfigure(self.empty, fill=p.plot_secondary)
        self.draw()

    def configure_panel(self, _event=None):
        values = {key: next(k for k, label in labels.items() if label == variable.get())
                  for key, (variable, _, labels) in self.controls.items()}
        if values['category'] is Category.SHIELD:
            values['rate_basis'] = RateBasis.RECENT_5S
        self.config = replace(self.config, **values)
        self.order = []
        self.sync_controls()
        self.changed(self)

    def sync_controls(self):
        for key, (variable, combo, labels) in self.controls.items():
            variable.set(labels[getattr(self.config, key)])
            combo.configure(state='disabled' if key == 'rate_basis' and
                            self.config.category is Category.SHIELD else 'readonly')

    def update_view(self, view, source, *, team_complete=False, reset=False):
        self.view, self.source = view, source
        self.team_complete = team_complete
        self.title.configure(text=view.title)
        self.sync_controls()
        if reset:
            self.pinned = None
            self.order = []
            self.canvas.yview_moveto(0)
        desired = [e.owner_id for e in view.entries]
        # Hovering holds row positions while live values keep updating.
        self.order = ([key for key in self.order if key in desired] +
                      [key for key in desired if key not in self.order]
                      if self.hovering and self.order else desired)
        self.refresh_detail()
        self.draw()

    def refresh_detail(self):
        if self.pinned and self.pinned not in self.source:
            self.detail.set('固定干员当前无可靠数据；等待后续采样。')
        else:
            self.detail.set(detail_text(self.source.get(self.pinned)))

    def pin(self, key):
        self.pinned = key
        self.refresh_detail()
        self.draw()

    def set_hover(self, hovering):
        self.hovering = hovering
        if not hovering:
            self.tooltip.hide()
            if self.view:
                self.order = [e.owner_id for e in self.view.entries]
        self.draw()

    def wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), 'units')
        self.tooltip.hide()
        return 'break'

    def navigate(self, delta):
        if self.order:
            index = self.order.index(self.pinned) if self.pinned in self.order else (-1 if delta > 0 else 0)
            index = min(len(self.order) - 1, max(0, index + delta))
            self.pin(self.order[index])
            self.canvas.yview_moveto(index / max(1, len(self.order)))
        return 'break'

    def hit(self, event):
        index = int(self.canvas.canvasy(event.y) // self.row_height)
        return self.order[index] if 0 <= index < len(self.order) else None

    def click(self, event):
        self.canvas.focus_set()
        key = self.hit(event)
        if key:
            self.pin(key)

    def motion(self, event):
        key = self.hit(event)
        if not key or not self.view:
            self.tooltip.hide()
            return
        entry = next(e for e in self.view.entries if e.owner_id == key)
        text = (entry.name + (' · 已离场' if entry.portrait_muted else '')
                if event.x < round(54 * self.unit) else
                f'{entry.name}\n{self.view.metric_label} {entry.exact_value}\n'
                f'{entry.rate_label}速率 {exact(entry.rate)}\n'
                f'占比 {exact(entry.share)}' + ('\n仅覆盖已观察区间' if entry.incomplete else ''))
        self.tooltip.show(text, event.x_root, event.y_root)

    def draw(self):
        p = palette(self)
        width = max(1, self.canvas.winfo_width())
        height = max(100, self.canvas.winfo_height())
        for index, item in enumerate(self.orbits):
            cx, cy = width * .77, height * .65
            rx, ry = (55 + index*30)*self.unit, (35 + index*22)*self.unit
            self.canvas.coords(item, cx-rx, cy-ry, cx+rx, cy+ry)
            self.canvas.itemconfigure(item, outline=p.plot_selection,
                state='normal' if theme_key(self) == 'lone-trail' else 'hidden')
        # Canvas may briefly be unmapped during layout; its width then is 1.
        # Wrap using the containing panel to avoid requesting a tall 100px column.
        wrap_width = max(100, self.winfo_width() - 20)
        self.hint_label.configure(wraplength=wrap_width)
        self.detail_label.configure(wraplength=wrap_width)
        if self.view is None:
            return
        entries = {e.owner_id: e for e in self.view.entries}
        for key in list(self.items):
            if key not in entries:
                for item in self.items.pop(key):
                    self.canvas.delete(item)
        hint = ('头像/姓名 · ' + self.view.metric_label + ' · ' +
                (self.view.rate_label + (' HPS' if self.config.category is Category.HEALING else ' DPS')
                 if self.view.rate_supported else '仅实际吸收') +
                (' · 悬停暂停排序' if self.hovering else ' · 按原始值排序'))
        if self.compact:
            hint = self.view.metric_label + ' · ' + (self.view.rate_label or '有效吸收')
        if not self.compact and self.config.category is Category.DAMAGE:
            hint += chr(10) + '物理（红） / 法术（蓝） / 元素（紫） / 真实（白）；灰条表示分类未完整覆盖'
        if not self.view.share_known:
            hint += '\n' + self.view.share_reason
        if not self.compact and self.view.share_note:
            hint += '\n' + self.view.share_note
        if any(entry.incomplete for entry in self.view.entries):
            hint += '\n* 表示覆盖不完整；悬停可查看未舍入数值。'
        self.hint.set(hint)
        self.canvas.itemconfigure(self.empty, state='hidden' if self.order else 'normal',
                                  width=max(100, width - 24),
                                  text=empty_text(self.config, self.source,
                                                  team_complete=self.team_complete))
        s = self.unit
        for index, key in enumerate(self.order):
            entry = entries[key]
            if key not in self.items:
                self.items[key] = [self.canvas.create_rectangle(0, 0, 1, 1, width=0),
                                   self.canvas.create_rectangle(0, 0, 1, 1, width=0),
                                   self.canvas.create_text(0, 0), self.canvas.create_text(0, 0),
                                   self.canvas.create_text(0, 0), self.canvas.create_text(0, 0)]
                self.items[key] += [self.canvas.create_rectangle(0, 0, 1, 1, width=0) for _ in range(4)]
                self.items[key].append(self.canvas.create_image(0, 0))
            background, avatar, initials, name, amount, rate, *bars, portrait = self.items[key]
            y = index * self.row_height
            self.canvas.coords(background, 0, y, width, y + self.row_height - 1)
            self.canvas.itemconfigure(background, fill=p.plot_selection if key == self.pinned else p.plot)
            self.canvas.coords(avatar, 6*s, y+7*s, 42*s, y+43*s)
            self.canvas.itemconfigure(avatar, fill=p.plot_selection)
            self.canvas.coords(initials, 24*s, y+25*s)
            self.canvas.itemconfigure(initials, text=entry.name[:2], fill=p.plot_secondary if entry.portrait_muted else p.plot_text,
                                      font=('Microsoft YaHei UI', 10))
            image = self.portraits.get(key, entry.portrait_muted, max(4, round(36*s / 4)*4))
            self.canvas.coords(portrait, 24*s, y+25*s)
            self.canvas.itemconfigure(portrait, image=image or '', state='normal' if image else 'hidden')
            self.canvas.itemconfigure(initials, state='hidden' if image else 'normal')
            name_width = max(20, width - 200*s)
            short_name = entry.name
            while len(short_name) > 1 and self.name_font.measure(short_name) > name_width:
                short_name = short_name[:-1]
            if short_name != entry.name:
                short_name = short_name[:-1] + '…'
            self.canvas.coords(name, 52*s, y+16*s)
            self.canvas.itemconfigure(name, text=short_name, anchor='w', fill=p.plot_text,
                                      font=('Microsoft YaHei UI', 10))
            self.canvas.coords(amount, width-10*s, y+16*s)
            self.canvas.itemconfigure(amount, text=entry.display_value + (' *' if entry.incomplete else '') +
                                      '  ' + entry.share_display, anchor='e', fill=p.plot_text,
                                      font=('Microsoft YaHei UI', 10, 'bold'))
            self.canvas.coords(rate, width-10*s, y+38*s)
            self.canvas.itemconfigure(rate, text=entry.rate_display if self.view.rate_supported else '',
                                      anchor='e', fill=p.plot_secondary, font=('Microsoft YaHei UI', 9))
            start, bar_width = 52*s, max(1, width - 140*s) * entry.bar_ratio
            segments = entry.segments if entry.segments_complete else ()
            if not segments:
                segments = ({'ratio': 1, 'color': '#697e8a' if self.config.category is Category.DAMAGE
                             else '#68c5a0' if self.config.category is Category.HEALING else '#e7be72'},)
            for segment_index, item in enumerate(bars):
                if segment_index < len(segments):
                    segment = segments[segment_index]
                    end = start + bar_width * segment['ratio']
                    self.canvas.coords(item, start, y+33*s, end, y+42*s)
                    self.canvas.itemconfigure(item, fill=segment['color'], state='normal')
                    start = end
                else:
                    self.canvas.itemconfigure(item, state='hidden')
        self.canvas.configure(scrollregion=(0, 0, width, max(self.row_height, len(self.order)*self.row_height)))
