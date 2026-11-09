"""Native multi-panel statistics GUI; one shared read-only collection session."""
import argparse
import json
import queue
import socket
import sys
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font
from dataclasses import replace
from uuid import uuid4

from panel_model import PanelConfig, render_board, rows_from_statistics
from gui_panel import StatisticPanel, PortraitAtlas, BG, TEXT, SECONDARY

from collector import BattleChanged, DEFAULT_ADB, DEFAULT_SERIAL, ROOT
from metrics import observation_age
from collection_session import CollectionSession, DEFAULT_FRIDA_REMOTE
from battle_lifecycle import BattleRuntime
from battle_history import BattleHistory
from event_capture import CaptureRetry


def timestamp():
    return datetime.now(timezone(timedelta(hours=8)))


def event_label(status, error=None):
    labels = {'recording': '事件观测中 · 护盾仅含观测区间',
              'ready': '事件已就绪 · 等待同帧基线', 'attaching': '正在挂载事件观测',
              'gap': '事件中断 · 仅保留已覆盖区间；伤害/治疗快照继续',
              'failed': '事件不可用 · 使用伤害/治疗快照',
              'unavailable': '事件关闭或不可用 · 使用伤害/治疗快照',
              'finished': '事件观测已结束 · 保留已覆盖区间'}
    return labels.get(status, '等待事件能力诊断') + (f'（{error}）' if error else '')


def enable_dpi_awareness():
    if sys.platform == 'win32':
        import ctypes
        # System-aware fallback for older Windows; per-monitor coordinates on current Windows.
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            ctypes.windll.shcore.SetProcessDpiAwareness(1)


class App:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.rescan = threading.Event()
        self.rescan.set()
        self.last = None
        self.collector = None
        self.history = BattleHistory(ROOT / 'runtime' / 'history')
        self.last_frame_change = 0
        self.frame = None
        self.error = ''
        self.scanning = False
        self.battle_status = 'waiting'
        self.panels = []
        self.source_rows = []
        self.statistics = {}
        self.current_battle = None
        self.connection = '尚未连接'
        self.event_state = '等待事件能力诊断'
        self.hud_request = None  # ARK-10 installs a handler receiving the source panel.
        self.portraits = PortraitAtlas(root)
        root.title('ArkDPS · 实时统计')
        root.geometry('960x700')
        unit = max(1, font.nametofont('TkDefaultFont').metrics('linespace') / 17)
        root.minsize(round(440*unit), round(500*unit))
        root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background=BG, foreground=TEXT)
        style.configure('TButton', padding=(8, 5), background='#33434d', foreground=TEXT)
        style.map('TButton', background=[('active', '#485e6b'), ('disabled', '#26343d')])
        style.map('TCombobox', fieldbackground=[('readonly', '#26343d')],
                  foreground=[('readonly', TEXT)])
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)
        top = ttk.Frame(root, padding=(12, 10))
        top.grid(row=0, column=0, sticky='ew')
        top.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value='准备连接…')
        self.status_label = ttk.Label(top, textvariable=self.status,
                                      font=('Microsoft YaHei UI', 11, 'bold'))
        self.status_label.grid(row=0, column=0, sticky='w')
        self.pin = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='置顶', variable=self.pin,
                        command=lambda: root.attributes('-topmost', self.pin.get())).grid(row=0, column=1)
        self.clock = tk.StringVar(value='等待本局数据')
        ttk.Label(root, textvariable=self.clock, padding=(12, 0)).grid(row=1, column=0, sticky='w')
        self.diagnostics = tk.StringVar(value=self.connection)
        self.diagnostic_label = ttk.Label(root, textvariable=self.diagnostics, foreground=SECONDARY,
                                          padding=(12, 6), wraplength=860)
        self.diagnostic_label.grid(row=2, column=0, sticky='ew')
        container = ttk.Frame(root)
        container.grid(row=3, column=0, sticky='nsew')
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        self.board = tk.Canvas(container, bg=BG, highlightthickness=0, width=1)
        self.board.grid(row=0, column=0, sticky='nsew')
        bar = ttk.Scrollbar(container, command=self.board.yview)
        bar.grid(row=0, column=1, sticky='ns')
        self.board.configure(yscrollcommand=bar.set)
        self.panel_host = ttk.Frame(self.board)
        self.host_window = self.board.create_window(0, 0, window=self.panel_host, anchor='nw')
        self.panel_host.bind('<Configure>', lambda e: self.board.configure(scrollregion=self.board.bbox('all')))
        self.board.bind('<Configure>', self.layout_panels)
        self.board.bind('<MouseWheel>', self.scroll_board)
        bottom = ttk.Frame(root, padding=12)
        bottom.grid(row=4, column=0, sticky='ew')
        ttk.Button(bottom, text='新建面板', command=self.add_panel).pack(side='left')
        self.scan_button = ttk.Button(bottom, text='重新定位', command=self.rescan.set)
        self.scan_button.pack(side='left', padx=8)
        self.export_button = ttk.Button(bottom, text='导出本局', command=self.export, state='disabled')
        self.export_button.pack(side='left')
        self.add_panel(PanelConfig())
        self.worker=threading.Thread(target=self.collect,daemon=True)
        self.worker.start()
        root.after(100,self.tick)

    def collect(self):
        try:
            self._collect()
        except Exception as exc:
            traceback.print_exc()
            self.events.put(('error',str(exc)))
        finally:
            if self.collector:
                self.collector.close()

    def _collect(self):
        runtime = BattleRuntime()
        capture_dir=ROOT/'runtime'/'captures'
        capture_dir.mkdir(parents=True,exist_ok=True)
        capture_path=capture_dir/f'{timestamp():%Y%m%d-%H%M%S}.jsonl'
        with capture_path.open('a',encoding='utf-8') as out:
            from capture_cli import json_value
            def audit(kind, data):
                if out.closed:
                    return
                out.write(json.dumps(dict(kind=kind, data=data), ensure_ascii=False,
                                     default=json_value) + '\n')
                out.flush()
            while not self.stop.is_set():
                if self.rescan.is_set():
                    self.rescan.clear()
                    self.events.put(('scanning',True))
                    if self.collector:
                        self.collector.close()
                    self.collector=CollectionSession(self.args.adb,self.args.serial,
                        frida_remote=self.args.frida_remote, audit=audit, history=self.history,
                        log=lambda x:print(x,flush=True))
                    self.collector.enable_lifecycle(runtime)
                    try:
                        self.collector.connect()
                        diag = self.collector.collector.diagnostics
                        self.events.put(('connection', f'已连接 {diag.selected_serial} · 游戏版本 {diag.version_actual} 已兼容'))
                    except Exception as exc:
                        traceback.print_exc()
                        self.events.put(('error',str(exc)))
                        self.events.put(('connection', '连接或版本校验失败；停止发布实时数值'))
                        self.collector.close()
                        self.collector=None
                    self.events.put(('scanning',False))
                if self.collector:
                    try:
                        sample = self.collector.read()
                        runtime = self.collector.runtime
                        self.events.put(('lifecycle',runtime.sessions.state))
                        if sample:
                            self.events.put(('sample',sample))
                    except CaptureRetry:
                        self.events.put(('lifecycle', self.collector.runtime.sessions.state))
                    except BattleChanged as exc:
                        self.events.put(('error',str(exc)))
                        self.rescan.set()
                    except Exception as exc:
                        self.events.put(('error',str(exc)))
                self.stop.wait(0.2)
            if self.collector:
                self.collector.close()
                self.collector = None
            out.flush()

    def tick(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='sample':
                    self.error=''
                    self.last=value
                    if (value['session'],value['frame'])!=self.frame:
                        self.frame=(value['session'],value['frame'])
                        self.last_frame_change=value['captured_monotonic']
                    self.render(value)
                elif kind=='connection':
                    self.connection=value
                elif kind=='error':
                    self.error=value
                elif kind=='lifecycle':
                    self.battle_status=value
                elif kind=='scanning':
                    self.scanning=value
                    self.scan_button.configure(state='disabled' if value else 'normal')
                    if value:
                        self.error=''
        except queue.Empty:
            pass
        age=observation_age(self.last)
        if self.scanning:
            label='正在定位 · 保留上次记录' if self.last else '正在定位本局…'
        elif self.error:
            label='读取中断 · 显示最后有效记录' if self.last else '等待战斗数据'
        elif not self.last:
            label='等待战斗数据'
        elif self.battle_status=='ended':
            label='历史 · 战斗结束'
        elif self.battle_status=='finalizing':
            label='历史 · 等待结算确认'
        elif self.battle_status=='frozen':
            label='战局冻结 · 保留最后有效记录'
        elif age>2:
            label='数据已过期 · 请重新定位'
        elif time.monotonic()-self.last_frame_change>0.8:
            label='暂停 / 等待战斗推进'
        else:
            label='正在统计'
        self.status.set(label)
        self.diagnostics.set(self.connection + '\n' + self.event_state + ('\n' + self.error if self.error else ''))
        self.status_label.configure(foreground='#f1bc82' if self.error or (self.last and age>2 and not self.scanning) else '#edf2f4')
        self.root.after(100,self.tick)

    def scroll_board(self, event):
        self.board.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'

    def layout_panels(self, _event=None):
        width = max(1, self.board.winfo_width())
        self.board.itemconfigure(self.host_window, width=width)
        self.diagnostic_label.configure(wraplength=max(200, width - 24))
        unit = max(1, font.nametofont('TkDefaultFont').metrics('linespace') / 17)
        columns = max(1, min(len(self.panels), int(width / (390 * unit))))
        for col in range(max(len(self.panels), getattr(self, 'layout_columns', 0))):
            self.panel_host.columnconfigure(col, weight=0, minsize=0, uniform='')
        for col in range(columns):
            self.panel_host.columnconfigure(col, weight=1, uniform='panel')
        for index, panel in enumerate(self.panels):
            panel.grid(row=index // columns, column=index % columns, sticky='nsew', padx=6, pady=6)
        self.layout_columns = columns

    def add_panel(self, config=None):
        if config is None:
            config = PanelConfig(panel_id=uuid4().hex)
        panel = StatisticPanel(self.panel_host, config, changed=lambda p: self.render_panels(),
                               duplicate=self.duplicate_panel, delete=self.delete_panel,
                               open_hud=self.open_hud, portraits=self.portraits)
        self.panels.append(panel)
        self.update_panel_actions()
        self.layout_panels()
        self.render_panels()
        return panel

    def duplicate_panel(self, panel):
        return self.add_panel(replace(panel.config, panel_id=uuid4().hex))

    def delete_panel(self, panel):
        if len(self.panels) == 1:
            return
        # Consumers can close linked displays before the source disappears.
        self.root.event_generate('<<PanelDeleting>>', data=panel.config.panel_id)
        self.panels.remove(panel)
        panel.destroy()
        self.update_panel_actions()
        self.layout_panels()

    def update_panel_actions(self):
        for panel in self.panels:
            panel.delete_button.configure(state='normal' if len(self.panels) > 1 else 'disabled')

    def open_hud(self, panel):
        if self.hud_request:
            self.hud_request(panel)
        else:
            messagebox.showinfo('HUD 尚未接入', 'HUD 窗口由后续版本提供；当前面板继续实时统计。', parent=self.root)

    def render_panels(self, reset=False):
        team_complete = self.last.get('statistics_complete', False) if self.last else False
        views = render_board([p.config for p in self.panels], self.source_rows,
                             team_complete=team_complete)
        source = self.statistics.get('rows') or {}
        for panel, view in zip(self.panels, views):
            panel.update_view(view, source, team_complete=team_complete, reset=reset)

    def render(self, sample):
        # Consume the unified result, never the prototype compatibility rows.
        self.statistics = sample.get('statistics') or {}
        self.source_rows = rows_from_statistics(self.statistics)
        battle = sample.get('battle_id', sample.get('session'))
        reset = battle != self.current_battle
        self.current_battle = battle
        seconds = self.statistics.get('game_time', sample['game_time'])
        self.clock.set(f'游戏时间 {int(seconds)//60:02}:{seconds%60:04.1f} · {len(self.source_rows)} 个统计条目')
        self.event_state = event_label(sample.get('event_status'), sample.get('event_error'))
        self.render_panels(reset=reset)
        self.export_button.configure(state='normal')

    def export(self):
        if not self.last:return
        path=filedialog.asksaveasfilename(parent=self.root,title='导出当前战斗记录',
            initialdir=str(ROOT/'runtime'),initialfile=f'战斗统计-{timestamp():%Y%m%d-%H%M%S}.json',
            defaultextension='.json',filetypes=[('JSON 战斗记录','*.json')])
        if path:
            payload=dict(self.last,exported_at=timestamp().isoformat(),
                data_age_seconds=round(observation_age(self.last),2),
                capture_status='unavailable' if self.error else 'locating' if self.scanning else 'current' if observation_age(self.last)<2 else 'stale')
            try:
                Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('导出失败',str(exc),parent=self.root)

    def close(self):
        self.stop.set()
        self.root.withdraw()
        self.finish_close()

    def finish_close(self):
        if self.worker.is_alive():
            self.root.after(100,self.finish_close)
        else:
            self.root.destroy()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--adb',default=DEFAULT_ADB)
    parser.add_argument('--serial',default=DEFAULT_SERIAL)
    parser.add_argument('--capture-seconds',type=float,default=0)
    events=parser.add_mutually_exclusive_group()
    events.add_argument('--frida-remote', default=DEFAULT_FRIDA_REMOTE,
                        help='Frida 17.26.1 endpoint (default: %(default)s); no server is installed or started')
    events.add_argument('--no-events', dest='frida_remote', action='store_const', const=None,
                        help='disable event observation; retain native snapshots')
    parser.add_argument('--wait-battle-seconds',type=float,default=180)
    parser.add_argument('--baseline-timeout',type=float,default=90)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--stop-file',type=Path)
    parser.add_argument('--expect-head')
    parser.add_argument('--maa-job',type=Path)
    parser.add_argument('--maa-log',type=Path)
    parser.add_argument('--stage')
    parser.add_argument('--require-progress',action='store_true')
    parser.add_argument('--require-events',action='store_true',
                        help='fail acceptance capture if event coverage is unavailable')
    args=parser.parse_args()
    if args.require_events and not args.frida_remote:
        parser.error('--require-events cannot be combined with --no-events')
    if (args.capture_seconds < 0 or args.wait_battle_seconds < 0 or args.baseline_timeout <= 0):
        parser.error('capture/wait durations must be nonnegative and baseline timeout positive')
    (ROOT/'runtime').mkdir(exist_ok=True)
    # One owner for the device channel. A second launch cannot tear it down.
    instance_lock=socket.socket()
    try:
        instance_lock.bind(('127.0.0.1',27380))
        instance_lock.listen(1)
    except OSError:
        instance_lock.close()
        if args.capture_seconds:
            raise SystemExit('统计窗已在运行，请先关闭后进行命令行采集')
        root=tk.Tk();root.withdraw()
        messagebox.showinfo('统计窗已运行','请使用已经打开的“明日方舟 · 局内伤害”窗口。',parent=root)
        root.destroy()
        return
    if args.capture_seconds:
        from capture_cli import run_capture
        run_capture(args)
        return
    log=(ROOT/'runtime'/f'app-{timestamp():%Y%m%d-%H%M%S}.log').open('a',encoding='utf-8',buffering=1)
    sys.stdout=sys.stderr=log
    enable_dpi_awareness()
    root=tk.Tk()
    App(root,args)
    root.mainloop()


if __name__=='__main__':
    main()
