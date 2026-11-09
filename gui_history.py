"""Independent history browser. Its selection never changes live GUI/HUD sources."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font

from battle_share import record_labels, record_view, record_complete, json_bytes, export_path, write_export
from gui_panel import StatisticPanel, BG, SECONDARY
from panel_model import PanelConfig


class HistoryWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.history, self.portraits = app.history, app.portraits
        self.records, self.selected = {}, None
        self.title('ArkDPS · 历史与分享')
        self.geometry('960x820')
        unit = max(1, font.nametofont('TkDefaultFont').metrics('linespace') / 17)
        self.minsize(round(440*unit), round(500*unit))
        self.configure(bg=BG)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        top = ttk.Frame(self, padding=12)
        top.grid(row=0, column=0, sticky='ew')
        ttk.Label(top, text='最近 100 局', font=('Microsoft YaHei UI', 12, 'bold')).pack(side='left')
        ttk.Button(top, text='刷新列表', command=self.refresh).pack(side='right')
        self.toggle_button = ttk.Button(top, text='收起列表', command=self.toggle_list)
        self.toggle_button.pack(side='right', padx=8)
        self.summary = tk.StringVar(value='选择一局查看统计；实时面板与 HUD 继续跟随当前战斗。')
        self.summary_label = ttk.Label(self, textvariable=self.summary, padding=(12, 4),
                                       foreground=SECONDARY, wraplength=900)
        self.summary_label.grid(row=1, column=0, sticky='ew')
        listing = ttk.Frame(self, padding=(12, 6))
        self.list_container = listing
        self.list_visible = True
        listing.grid(row=2, column=0, sticky='ew')
        listing.columnconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure('History.Treeview', background=BG, fieldbackground=BG,
                        foreground='#edf2f4', rowheight=round(26*unit))
        style.map('History.Treeview', background=[('selected', '#305569')],
                  foreground=[('selected', '#edf2f4')])
        self.listing = ttk.Treeview(listing, columns=('battle',), show='headings', height=4,
                                    selectmode='browse', style='History.Treeview')
        self.listing.heading('battle', text='关卡 · 模式 · 结果 · 最后可靠游戏时刻 · 日期（可横向滚动）', anchor='w')
        self.listing.column('battle', width=1100, minwidth=1100, stretch=False)
        self.listing.grid(row=0, column=0, sticky='ew')
        vertical = ttk.Scrollbar(listing, command=self.listing.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=self.listing.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.listing.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.listing.bind('<<TreeviewSelect>>', self.select_record)
        body = ttk.Frame(self)
        body.grid(row=3, column=0, sticky='nsew', padx=12, pady=6)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.board = tk.Canvas(body, bg=BG, highlightthickness=0, width=1, height=1)
        self.board.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(body, command=self.board.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.board.configure(yscrollcommand=scroll.set)
        self.panel = StatisticPanel(self.board, PanelConfig(panel_id='history'), changed=lambda p: self.render(),
                                    duplicate=None, delete=None, open_hud=None,
                                    portraits=self.portraits, history=True)
        self.panel_window = self.board.create_window(0, 0, window=self.panel, anchor='nw')
        self.board.bind('<Configure>', self.layout_panel)
        self.panel.bind('<Configure>', lambda e: self.board.configure(scrollregion=self.board.bbox('all')))
        self.board.bind('<MouseWheel>', lambda e: self.board.yview_scroll(-int(e.delta / 120), 'units'))
        bottom = ttk.Frame(self, padding=12)
        bottom.grid(row=4, column=0, sticky='ew')
        self.json_button = ttk.Button(bottom, text='导出 JSON', command=self.export_json, state='disabled')
        self.json_button.pack(side='left')
        self.png_button = ttk.Button(bottom, text='保存统计截图', command=self.export_png, state='disabled')
        self.png_button.pack(side='left', padx=8)
        ttk.Button(bottom, text='返回实时窗口', command=self.return_live).pack(side='right')
        self.notice = tk.StringVar()
        self.notice_label = ttk.Label(self, textvariable=self.notice, padding=(12, 0, 12, 10),
                                      foreground=SECONDARY, wraplength=900)
        self.notice_label.grid(row=5, column=0, sticky='ew')
        self.bind('<Configure>', self.resize)
        self.refresh()

    def resize(self, event):
        if event.widget is self:
            width = max(200, event.width - 24)
            self.summary_label.configure(wraplength=width)
            self.notice_label.configure(wraplength=width)

    def layout_panel(self, _event=None):
        self.board.itemconfigure(self.panel_window, width=max(1, self.board.winfo_width()),
                                 height=max(self.board.winfo_height(), self.panel.winfo_reqheight()))

    def toggle_list(self):
        self.list_visible = not self.list_visible
        if self.list_visible:
            self.list_container.grid()
        else:
            self.list_container.grid_remove()
        self.toggle_button.configure(text='收起列表' if self.list_visible else '展开列表')

    def return_live(self):
        self.master.deiconify()
        self.master.lift()
        self.destroy()

    def refresh(self):
        result = self.history.list_recent()
        self.records = {str(i): record for i, record in enumerate(result.records)}
        self.listing.delete(*self.listing.get_children())
        selected = None
        for key, record in self.records.items():
            self.listing.insert('', 'end', iid=key, values=(' · '.join(record_labels(record)),))
            if self.selected and self.selected.battle_id == record.battle_id:
                selected = key
        if selected is not None:
            self.listing.selection_set(selected)
            self.listing.see(selected)
        elif self.selected is None and self.records:
            selected = next(iter(self.records))
            self.listing.selection_set(selected)
            self.select_record()
        text = f'可读 {len(result.records)} 局。' if result.records else '暂无可读战绩；战斗归档后点击刷新列表。'
        if self.selected and selected is None and result.records:
            text += ' 当前查看的副本保留，选择其他局才切换。'
        if result.diagnostics:
            text += f'\n读取诊断 {len(result.diagnostics)} 条：' + '；'.join(
                f'{d.code}: {d.message[:120]}' for d in result.diagnostics[:3])
        self.notice.set(text)

    def select_record(self, _event=None):
        keys = self.listing.selection()
        if not keys or keys[0] not in self.records:
            return
        record = self.records[keys[0]]
        reset = self.selected is None or self.selected.battle_id != record.battle_id
        self.selected = record
        self.summary.set('历史 · ' + ' · '.join(record_labels(record)) + '\n'
                         '伤害含过量；治疗含过量；护盾仅实际吸收；近期为终点前已观察窗口。\n'
                         '事件：' + record.to_dict()['event_status'] + ' · ' +
                         (record.to_dict()['event_error'] or '无额外诊断'))
        self.render(reset=reset)
        self.json_button.configure(state='normal')
        self.png_button.configure(state='normal')

    def render(self, reset=False):
        if self.selected is not None:
            data = self.selected.to_dict()
            self.panel.update_view(record_view(self.selected, self.panel.config), data['statistics']['rows'],
                                   team_complete=record_complete(self.selected), reset=reset)

    def save(self, extension, produce):
        if self.selected is None:
            return
        # Freeze both record and configuration before the native dialog's nested event loop.
        record, config = self.selected, self.panel.config
        path = filedialog.asksaveasfilename(parent=self, title='保存所选战绩',
                initialfile=f'ArkDPS-历史{extension}', defaultextension=extension,
                filetypes=[('JSON 战绩' if extension == '.json' else 'PNG 统计截图', '*'+extension)],
                confirmoverwrite=False)
        if not path:
            return
        try:
            target = export_path(path, self.history.directory)
            overwrite = target.exists()
            if overwrite and not messagebox.askyesno('确认替换', f'文件已存在，是否替换？\n{target.name}', parent=self):
                return
            payload = produce(record, config)
            write_export(target, payload, history_directory=self.history.directory, overwrite=overwrite)
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror('保存失败', f'{exc}\n请选择可写目录后重试；原战绩保留。', parent=self)
            return
        self.notice.set(f'已保存：{target.name}')

    def export_json(self):
        self.save('.json', lambda record, config: json_bytes(record))

    def export_png(self):
        from statistics_screenshot import statistics_png
        self.save('.png', lambda record, config: statistics_png(self, record, config))
