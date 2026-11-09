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
from tkinter import ttk, messagebox, font
from dataclasses import replace
from uuid import uuid4

from panel_model import PanelConfig, render_board, rows_from_statistics
from gui_panel import StatisticPanel, PortraitAtlas, BG, TEXT, SECONDARY
from hud import HudWindow

from collector import BattleChanged, DEFAULT_ADB, DEFAULT_SERIAL, ROOT
from metrics import observation_age
from collection_session import CollectionSession, DEFAULT_FRIDA_REMOTE
from battle_lifecycle import BattleRuntime
from battle_history import BattleHistory
from event_capture import CaptureRetry
from connection import (ConnectionConfig, ConnectionConfigStore,
                        ConnectionUnavailable, resolve_configured)
import layout_store as layout_module
from layout_store import (GuiGeometry, HudPlacement, Layout, LayoutStore,
                          clamp_geometry)
from tray import TrayIcon


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
    def __init__(self, root, args, *, layout_store=None, connection_store=None,
                 tray_factory=None, work_area_provider=None, layout_load=None):
        self.root, self.args = root, args
        self.events = queue.Queue()
        self.layout_store = layout_store
        self.connection_store = connection_store
        self.work_area_provider = work_area_provider or layout_module.monitor_work_areas
        self.loading = True
        self.quitting = False
        self.tray = None
        self.settings_window = None
        self.save_after = None
        self.warnings = []
        self.loaded = (layout_load if layout_load is not None else
                       (layout_store.load() if layout_store is not None
                        else layout_module.LayoutLoadResult(Layout.default(), True)))
        self.layout = self.loaded.layout
        # Long-lived event-observation preference from the layout file. It is
        # updated only by an explicit settings apply; one-shot command-line
        # overrides change ``events_enabled`` for this run without rewriting it.
        self.event_observation = self.layout.event_observation
        if self.loaded.warning:
            self.warnings.append(self.loaded.warning)
        adb_arg = getattr(args, 'adb', '') or None
        serial_arg = getattr(args, 'serial', '') or None
        self.connection_preference = ConnectionConfig()
        if self.connection_store is not None:
            loaded_connection = self.connection_store.load()
            self.connection_preference = loaded_connection.config
            if not loaded_connection.ok and loaded_connection.warning:
                self.warnings.append(loaded_connection.warning)
        self.connection_config = self.connection_preference
        self.apply_command_line_connection(adb_arg, serial_arg)
        self.events_enabled = getattr(args, 'frida_remote', None) is not None
        # Retain the launch endpoint even while observation is switched off;
        # applying unrelated settings must not replace a custom Frida bridge.
        self.event_endpoint = getattr(args, 'frida_remote', None) or DEFAULT_FRIDA_REMOTE
        self.frida_remote = self.event_endpoint if self.events_enabled else None
        self.connection_diagnostic = None
        self.event_status = None
        self.event_error = None
        self.session = None
        self.shutdown_report = None
        self.last_gui_geometry = self.layout.gui
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
        self.huds = {}
        self.live_statistics, self.live_source_rows = {}, []
        self.live_complete = False
        self.hud_request = self.create_hud
        self.portraits = PortraitAtlas(root)
        root.title('ArkDPS · 实时统计')
        unit = max(1, font.nametofont('TkDefaultFont').metrics('linespace') / 17)
        root.minsize(round(440*unit), round(500*unit))
        self.restore_gui_geometry()
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
        ttk.Button(bottom, text='设置', command=self.open_settings).pack(side='left', padx=(8, 0))
        self.scan_button = ttk.Button(bottom, text='重新定位', command=self.rescan.set)
        self.scan_button.pack(side='left', padx=8)
        self.export_button = ttk.Button(bottom, text='历史与分享', command=self.export)
        self.export_button.pack(side='left')
        ttk.Button(bottom, text='退出应用', command=self.quit).pack(side='right')
        for config in self.layout.panels:
            self.add_panel(config)
        self.restore_huds()
        self.start_tray(tray_factory)
        self.loading = False
        root.bind('<Configure>', self.root_configured, add='+')
        self.worker=threading.Thread(target=self.collect,daemon=True)
        self.worker.start()
        root.after(100,self.tick)

    # -- connection settings, layout persistence and tray ------------------
    def apply_command_line_connection(self, adb, serial):
        """A CLI value overrides the stored setting for this launch only."""
        config = self.connection_config
        if adb:
            config = ConnectionConfig(adb, config.serial, config.auto_detect)
        if serial:
            config = ConnectionConfig(config.adb_path, serial, False)
        self.connection_config = config

    def work_areas(self):
        try:
            return list(self.work_area_provider())
        except Exception as exc:  # a display API failure never blocks startup
            self.record_warning('无法读取显示器布局：' + str(exc))
            return []

    def restore_gui_geometry(self):
        geometry = self.layout.gui
        if geometry is None:
            self.root.geometry('%dx%d' % layout_module.DEFAULT_GUI)
            return
        minimum = tuple(self.root.minsize())
        fitted = clamp_geometry(geometry, self.work_areas(),
                                minimum=(max(200, minimum[0]), max(200, minimum[1])))
        self.last_gui_geometry = fitted
        self.root.geometry(f'{fitted.width}x{fitted.height}+{fitted.x}+{fitted.y}')

    def hud_minimum(self, panel):
        return (round(350*panel.unit), round(230*panel.unit))

    def restore_huds(self):
        by_panel = {panel.config.panel_id: panel for panel in self.panels}
        for placement in self.layout.huds:
            panel = by_panel.get(placement.panel_id)
            if panel is None:
                continue
            try:
                self.create_hud(panel, placement=placement)
            except Exception as exc:
                self.record_warning('HUD 恢复失败：' + str(exc))

    def start_tray(self, tray_factory=None):
        if tray_factory is None:
            return
        try:
            tray = tray_factory(self)
            self.tray = tray
            if not tray.start():
                reason = getattr(tray, 'error', None) or '未知原因'
                self.record_warning('系统托盘不可用：' + str(reason) +
                                    '；关闭 GUI 时保留任务栏恢复入口。')
                self.tray = None
        except Exception as exc:
            self.record_warning('系统托盘初始化失败：' + str(exc))
            self.tray = None

    def open_settings(self):
        from settings_window import SettingsWindow
        window = self.settings_window
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return window
        self.settings_window = SettingsWindow(self)
        return self.settings_window

    def apply_connection(self, config, events_enabled):
        """Persist connection/event choices and ask the worker to reconnect."""
        self.connection_preference = config
        self.connection_config = config
        self.event_observation = bool(events_enabled)
        self.events_enabled = bool(events_enabled)
        self.frida_remote = self.event_endpoint if self.events_enabled else None
        if self.connection_store is not None:
            try:
                self.connection_store.save(config)
            except OSError as exc:
                self.record_warning('连接配置保存失败：' + str(exc))
        self.connection_diagnostic = None
        self.event_status, self.event_error = None, None
        self.event_state = event_label(None)
        self.connection = '正在按新设置重新连接…'
        self.save_layout_now()
        self.rescan.set()
        return True

    def set_connection_diagnostic(self, diagnostic):
        self.connection_diagnostic = diagnostic or None

    def connection_diagnostic_text(self):
        parts = [self.connection]
        diag = self.connection_diagnostic or {}
        version = {'compatible': '游戏版本已兼容', 'incompatible': '游戏版本不兼容',
                   'not_installed': '未安装明日方舟客户端',
                   'unknown': '游戏版本未知'}.get(diag.get('version_status'))
        if version:
            parts.append(version)
        instances = diag.get('instances') or []
        if diag.get('status') == 'needs_selection' and len(instances) > 1:
            parts.append(f'检测到 {len(instances)} 个实例，请在设置中选择')
        if diag.get('message') and diag.get('status') not in (None, 'ready_unique'):
            parts.append(diag['message'])
        if not self.events_enabled:
            parts.append('事件观测已关闭 · 仅使用快照')
        return ' · '.join(part for part in parts if part)

    def record_warning(self, text):
        if text and text not in self.warnings:
            self.warnings.append(text)
            print('警告：' + text, flush=True)

    def hud_layout_changed(self, _hud):
        self.schedule_layout_save()

    def hud_closed(self, _hud):
        self.schedule_layout_save(delay=0)
        if not self.huds and self.root.state() in ('withdrawn', 'iconic'):
            self.show_gui()

    def root_configured(self, event):
        if event.widget is not self.root or self.root.state() != 'normal':
            return
        self.last_gui_geometry = GuiGeometry(
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height())
        self.schedule_layout_save()

    def schedule_layout_save(self, delay=600):
        if self.loading or self.layout_store is None:
            return
        if self.save_after is not None:
            try:
                self.root.after_cancel(self.save_after)
            except tk.TclError:
                pass
            self.save_after = None
        self.save_after = self.root.after(max(0, int(delay)), self.save_layout_now)

    def snapshot_layout(self):
        huds = []
        for hud in self.huds.values():
            try:
                huds.append(HudPlacement(**hud.placement()))
            except (tk.TclError, TypeError, ValueError, AttributeError):
                continue
        gui = self.last_gui_geometry
        if self.root.state() == 'normal':
            gui = GuiGeometry(self.root.winfo_x(), self.root.winfo_y(),
                              self.root.winfo_width(), self.root.winfo_height())
        return Layout(panels=tuple(panel.config for panel in self.panels),
                      huds=tuple(huds), gui=gui,
                      event_observation=self.event_observation)

    def save_layout_now(self):
        if self.save_after is not None:
            try:
                self.root.after_cancel(self.save_after)
            except tk.TclError:
                pass
            self.save_after = None
        if self.layout_store is None:
            return False
        try:
            self.layout_store.save(self.snapshot_layout())
        except (OSError, ValueError) as exc:
            self.record_warning('布局保存失败：' + str(exc))
            return False
        return True

    def verify_shutdown(self):
        """Check that this app's own observer, session and forwards are gone."""
        session = self.session
        issues = []
        cleanup_errors = getattr(session, 'cleanup_errors', {}) if session is not None else {}
        session_closed = ((session is None or bool(getattr(session, 'closed', False)))
                          and not cleanup_errors)
        if not session_closed:
            issues.append('采集会话未关闭')
        observer_released = not cleanup_errors.get('observer')
        if session is not None:
            observer = getattr(session, 'observer', None)
            if observer is not None:
                observer_released = (observer_released and
                                     not getattr(observer, 'cleanup_errors', {}) and
                                     getattr(observer, 'script', None) is None and
                                     getattr(observer, 'session', None) is None)
        if not observer_released:
            issues.append('事件观察器释放未确认')
        for resource, errors in cleanup_errors.items():
            issues.extend(f'{resource} 清理失败：{error}' for error in errors)
        forwards_released = True
        collector = getattr(session, 'collector', None) if session is not None else None
        registry = getattr(collector, 'forward_registry', None)
        owned = getattr(registry, 'owned', {}) if registry is not None else {}
        if owned:
            forwards_released = False
            issues.append(f'仍有 {len(owned)} 个 ADB 转发未释放')
        report = dict(ok=not issues, session_closed=session_closed,
                      observer_released=observer_released,
                      forwards_released=forwards_released, issues=issues)
        print('退出清理核对：' + json.dumps(report, ensure_ascii=False), flush=True)
        return report

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
                        self.collector = None
                    try:
                        target = resolve_configured(self.connection_config)
                    except ConnectionUnavailable as exc:
                        diagnostic = exc.diagnostics.as_dict() if exc.diagnostics else None
                        self.events.put(('connection_diagnostic', diagnostic))
                        self.events.put(('connection', str(exc)))
                        self.events.put(('event', (None, None)))
                        self.events.put(('scanning', False))
                    except Exception as exc:
                        traceback.print_exc()
                        self.events.put(('error', str(exc)))
                        self.events.put(('connection', '连接设置解析失败；停止发布实时数值'))
                        self.events.put(('scanning', False))
                    else:
                        self.events.put(('connection_diagnostic',
                                         target.diagnostics.as_dict()))
                        remote = self.frida_remote
                        try:
                            self.collector=CollectionSession(
                                target.adb_path, target.serial, frida_remote=remote,
                                audit=audit, history=self.history,
                                log=lambda x:print(x,flush=True))
                            self.session = self.collector
                            self.collector.enable_lifecycle(runtime)
                            self.collector.connect()
                            diag = self.collector.collector.diagnostics
                            self.events.put(('connection_diagnostic', diag.as_dict()))
                            self.events.put(('event', (self.collector.event_status,
                                                       self.collector.event_error)))
                            self.events.put(('connection', f'已连接 {diag.selected_serial} · 游戏版本 {diag.version_actual} 已兼容'))
                        except Exception as exc:
                            traceback.print_exc()
                            self.events.put(('error',str(exc)))
                            diagnostic = getattr(exc, 'diagnostics', None)
                            if diagnostic is not None:
                                self.events.put(('connection_diagnostic',
                                                 diagnostic.as_dict()))
                            self.events.put(('connection', '连接或版本校验失败；停止发布实时数值'))
                            if self.collector is not None:
                                try:
                                    self.collector.close()
                                finally:
                                    self.collector=None
                        self.events.put(('scanning', False))
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
                elif kind=='connection_diagnostic':
                    self.connection_diagnostic=value
                elif kind=='event':
                    self.event_status,self.event_error=value
                    self.event_state=event_label(value[0], value[1])
                elif kind=='tray':
                    if value=='show':
                        self.show_gui()
                    elif value=='quit':
                        self.quit()
                        return
        except queue.Empty:
            pass
        if self.quitting:
            return
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
        for hud in self.huds.values():
            hud.status.set(label + ' · ' + self.event_state)
        warning = '\n'.join(self.warnings)
        self.diagnostics.set(self.connection + '\n' + self.event_state +
                             ('\n' + warning if warning else '') +
                             ('\n' + self.error if self.error else ''))
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
        panel = StatisticPanel(self.panel_host, config, changed=self.panel_changed,
                               duplicate=self.duplicate_panel, delete=self.delete_panel,
                               open_hud=self.open_hud, portraits=self.portraits)
        self.panels.append(panel)
        self.update_panel_actions()
        self.layout_panels()
        self.render_panels()
        self.schedule_layout_save()
        return panel

    def panel_changed(self, _panel=None):
        self.render_panels()
        self.schedule_layout_save(delay=0)

    def duplicate_panel(self, panel):
        return self.add_panel(replace(panel.config, panel_id=uuid4().hex))

    def delete_panel(self, panel):
        if len(self.panels) == 1:
            return
        hud = self.huds.get(panel.config.panel_id)
        if hud:
            hud.close()
        # Consumers can close linked displays before the source disappears.
        self.root.event_generate('<<PanelDeleting>>', data=panel.config.panel_id)
        self.panels.remove(panel)
        panel.destroy()
        self.update_panel_actions()
        self.layout_panels()
        self.schedule_layout_save(delay=0)

    def update_panel_actions(self):
        for panel in self.panels:
            panel.delete_button.configure(state='normal' if len(self.panels) > 1 else 'disabled')

    def open_hud(self, panel):
        if self.hud_request:
            return self.hud_request(panel)

    def create_hud(self, panel, placement=None):
        hud = self.huds.get(panel.config.panel_id)
        if hud is None:
            if placement is not None:
                fitted = clamp_geometry(placement.geometry, self.work_areas(),
                                        minimum=self.hud_minimum(panel))
                placement = HudPlacement(placement.panel_id, fitted.x, fitted.y,
                                         fitted.width, fitted.height,
                                         placement.opacity, placement.locked)
            hud = HudWindow(self, panel, placement)
            self.huds[panel.config.panel_id] = hud
        hud.window.lift()
        self.render_huds()
        self.schedule_layout_save(delay=0)
        return hud

    def render_huds(self, reset=False):
        # Always current battle, even when the GUI is displaying a saved record.
        huds = list(self.huds.values())
        views = render_board([h.source.config for h in huds], self.live_source_rows,
                             team_complete=self.live_complete)
        for hud, view in zip(huds, views):
            hud.update_view(view, self.live_statistics.get('rows') or {},
                            team_complete=self.live_complete, reset=reset)

    def show_gui(self):
        self.root.deiconify()
        self.root.lift()

    def render_panels(self, reset=False):
        team_complete = self.last.get('statistics_complete', False) if self.last else False
        views = render_board([p.config for p in self.panels], self.source_rows,
                             team_complete=team_complete)
        source = self.statistics.get('rows') or {}
        for panel, view in zip(self.panels, views):
            panel.update_view(view, source, team_complete=team_complete, reset=reset)
        self.render_huds()

    def render(self, sample):
        # Consume the unified result, never the prototype compatibility rows.
        self.statistics = sample.get('statistics') or {}
        self.source_rows = rows_from_statistics(self.statistics)
        self.live_statistics = self.statistics
        self.live_source_rows = self.source_rows
        self.live_complete = sample.get('statistics_complete', False)
        battle = sample.get('battle_id', sample.get('session'))
        reset = battle != self.current_battle
        self.current_battle = battle
        seconds = self.statistics.get('game_time', sample['game_time'])
        self.clock.set(f'游戏时间 {int(seconds)//60:02}:{seconds%60:04.1f} · {len(self.source_rows)} 个统计条目')
        self.event_status = sample.get('event_status')
        self.event_error = sample.get('event_error')
        self.event_state = event_label(self.event_status, self.event_error)
        self.render_panels(reset=reset)
        self.render_huds(reset=reset)
        self.export_button.configure(state='normal')

    def export(self):
        from gui_history import HistoryWindow
        window = getattr(self, 'history_window', None)
        if window is None or not window.winfo_exists():
            self.history_window = HistoryWindow(self)
        else:
            window.deiconify()
            window.lift()

    def close(self):
        """Window close hides the GUI; only explicit quit stops collection."""
        self.save_layout_now()
        if self.tray is not None:
            # HUDs and collection keep running; the tray restores this window.
            self.root.withdraw()
        elif self.huds:
            # Without a tray keep a taskbar recovery route for locked HUDs.
            self.root.iconify()
        else:
            self.quit()

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.save_layout_now()
        self.stop.set()
        for hud in list(self.huds.values()):
            hud.destroy_for_quit()
        window = self.settings_window
        if window is not None:
            try:
                window.close()
            except tk.TclError:
                pass
        self.settings_window = None
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception as exc:
                self.record_warning('系统托盘清理失败：' + str(exc))
            self.tray = None
        self.root.withdraw()
        self.finish_close()

    def finish_close(self):
        if self.worker.is_alive():
            self.root.after(100,self.finish_close)
        else:
            self.shutdown_report = self.verify_shutdown()
            self.root.destroy()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--adb', default=None,
                        help='override the saved adb path for this launch')
    parser.add_argument('--serial', default=None,
                        help='override the saved instance address for this launch')
    parser.add_argument('--capture-seconds',type=float,default=0)
    events=parser.add_mutually_exclusive_group()
    events.add_argument('--frida-remote', default=None,
                        help='Frida 17.26.1 endpoint; default comes from the saved '
                             'event-observation setting (initially %s); no server is installed or started'
                             % DEFAULT_FRIDA_REMOTE)
    events.add_argument('--no-events', dest='frida_remote', action='store_const', const='',
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
    # Headless capture must not migrate/quarantine GUI-only layout files or
    # consume diagnostics that can only be displayed by the next GUI launch.
    layout_store = loaded_layout = None
    if not args.capture_seconds:
        layout_store = LayoutStore()
        loaded_layout = layout_store.load()
    if args.frida_remote is None:
        if args.capture_seconds:
            # Headless capture has no settings window; keep the old default.
            args.frida_remote = DEFAULT_FRIDA_REMOTE
        else:
            args.frida_remote = (DEFAULT_FRIDA_REMOTE
                                 if loaded_layout.layout.event_observation else None)
    elif args.frida_remote == '':
        args.frida_remote = None
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
        # Headless capture has no settings window; keep the documented defaults.
        args.adb = args.adb or DEFAULT_ADB
        args.serial = args.serial or DEFAULT_SERIAL
        from capture_cli import run_capture
        run_capture(args)
        return
    log=(ROOT/'runtime'/f'app-{timestamp():%Y%m%d-%H%M%S}.log').open('a',encoding='utf-8',buffering=1)
    sys.stdout=sys.stderr=log
    enable_dpi_awareness()
    root=tk.Tk()

    def tray_factory(app):
        return TrayIcon('ArkDPS · 实时统计',
                        lambda action: app.events.put(('tray', action)),
                        icon_path=ROOT / 'assets' / 'arkdps.ico')

    App(root, args, layout_store=layout_store,
        connection_store=ConnectionConfigStore(), tray_factory=tray_factory,
        layout_load=loaded_layout)
    root.mainloop()


if __name__=='__main__':
    main()
