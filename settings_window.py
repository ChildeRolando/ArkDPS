"""Connection settings, event-observation switch and diagnostics.

The window never owns collection: it edits a :class:`connection.ConnectionConfig`
and an event-observation preference, then asks the app to persist and reconnect.
Detection runs on a worker thread because it shells out to adb; the Tk widgets
are only touched from the main thread.

``event_setting_status`` is a pure helper so the four required distinctions
(closed, dependency/endpoint missing, interrupted, unsupported game version)
are testable without a window or a device.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from connection import ConnectionConfig, detect_instances
from gui_panel import BG, SECONDARY

SNAPSHOT_FALLBACK = ('快照降级：伤害/治疗累计继续发布；护盾与接敌区间仅含已观察区间'
                     '或保持未知，不会记为零。')

EVENT_STATE_LABELS = {
    'active': '事件观测运行中',
    'disabled': '事件观测已关闭',
    'dependency_missing': '事件观测不可用：依赖或端点缺失',
    'interrupted': '事件观测运行中断',
    'unsupported_version': '不支持的游戏版本',
    'waiting': '等待连接与事件诊断',
}

_DEPENDENCY_WORDS = ('frida', 'module', 'import', 'required', '依赖', '未安装')


def event_setting_status(*, enabled: bool, event_status: str | None,
                         event_error: str | None, version_status: str | None,
                         connection_ok: bool | None = None) -> tuple[str, str]:
    """Return ``(state, user_text)`` for the event-observation setting.

    States: ``active``, ``disabled``, ``dependency_missing``, ``interrupted``,
    ``unsupported_version`` or ``waiting``. Only ``active`` claims event data;
    every degraded state states the snapshot fallback explicitly.
    """
    detail = f'（{event_error}）' if event_error else ''
    if version_status == 'not_installed':
        return ('unsupported_version',
                '未找到明日方舟客户端 · 无法连接，也不会发布任何实时数值。')
    if version_status == 'incompatible':
        return ('unsupported_version',
                '不支持的游戏版本 · 已拒绝读取与发布任何实时数值；请更新适配后重试。')
    if not enabled:
        return ('disabled', '已关闭 · 仅使用伤害/治疗快照；护盾与接敌区间保持未知，'
                            '不会记为零。')
    if event_status == 'failed':
        text = (event_error or '').lower()
        if any(word in text for word in _DEPENDENCY_WORDS):
            return ('dependency_missing',
                    'Frida 依赖或观测端点不可用' + detail + '。' + SNAPSHOT_FALLBACK)
        return ('dependency_missing',
                '事件观测无法挂载' + detail + '。' + SNAPSHOT_FALLBACK)
    if event_status in ('gap', 'finished'):
        label = '事件观测已结束' if event_status == 'finished' else '事件观测运行中断'
        return ('interrupted', label + detail + '。' + SNAPSHOT_FALLBACK)
    if event_status in ('ready', 'recording'):
        return ('active', '事件观测运行中 · 护盾与接敌区间来自已观察事件；'
                          '启动前区间仍为未知。')
    if connection_ok is False:
        return ('waiting', '尚未连接 · 事件观测会在连接并通过版本校验后自动挂载。')
    if event_status == 'unavailable':
        return ('dependency_missing',
                '当前采集会话未启用事件观测。' + SNAPSHOT_FALLBACK)
    return ('waiting', '等待连接与事件能力诊断；' + SNAPSHOT_FALLBACK)


class SettingsWindow(tk.Toplevel):
    def __init__(self, app, *, detect=detect_instances):
        super().__init__(app.root)
        self.app = app
        self.detect = detect
        self.detection: queue.Queue = queue.Queue()
        self.instance_rows: list[dict] = []
        self._poll_after = None
        self._status_after = None
        config = app.connection_config
        self.title('ArkDPS · 设置')
        self.geometry('720x680')
        self.configure(bg=BG)
        self.columnconfigure(0, weight=1)
        self.resizable(True, True)
        self.protocol('WM_DELETE_WINDOW', self.close)
        body = ttk.Frame(self, padding=14)
        body.grid(row=0, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)

        ttk.Label(body, text='连接设置', font=('Microsoft YaHei UI', 12, 'bold')).grid(
            row=0, column=0, sticky='w')
        connection = ttk.Frame(body)
        connection.grid(row=1, column=0, sticky='ew', pady=(8, 4))
        connection.columnconfigure(1, weight=1)
        self.auto = tk.BooleanVar(value=config.auto_detect)
        ttk.Checkbutton(connection, text='自动检测 MuMu 实例（唯一已认证实例自动连接）',
                        variable=self.auto, command=self.sync_fields).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
        ttk.Label(connection, text='ADB 路径').grid(row=1, column=0, sticky='w')
        self.adb = tk.StringVar(value=config.adb_path or '')
        self.adb_entry = ttk.Entry(connection, textvariable=self.adb)
        self.adb_entry.grid(row=1, column=1, sticky='ew', padx=8)
        ttk.Button(connection, text='选择…', command=self.choose_adb).grid(row=1, column=2)
        ttk.Label(connection, text='实例地址').grid(row=2, column=0, sticky='w', pady=(6, 0))
        self.address = tk.StringVar(value=config.serial or '')
        self.address_entry = ttk.Entry(connection, textvariable=self.address)
        self.address_entry.grid(row=2, column=1, sticky='ew', padx=8, pady=(6, 0))
        self.address_hint = ttk.Label(connection, text='', foreground=SECONDARY)
        self.address_hint.grid(row=3, column=1, sticky='w', padx=8, pady=(2, 0))
        detect_row = ttk.Frame(connection)
        detect_row.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        self.detect_button = ttk.Button(detect_row, text='检测实例', command=self.start_detection)
        self.detect_button.pack(side='left')
        self.detection_note = tk.StringVar(value='可留空 ADB 路径，按已安装位置与环境变量查找。')
        ttk.Label(detect_row, textvariable=self.detection_note,
                  foreground=SECONDARY).pack(side='left', padx=8)
        self.listing = tk.Listbox(connection, height=4, activestyle='none',
                                  bg='#26343d', fg='#edf2f4', selectbackground='#305569',
                                  highlightthickness=0, exportselection=False)
        self.listing.grid(row=5, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        self.listing.bind('<<ListboxSelect>>', self.select_instance)
        self.connection_status = tk.StringVar(value='')
        ttk.Label(connection, textvariable=self.connection_status, foreground=SECONDARY,
                  wraplength=640).grid(row=6, column=0, columnspan=3, sticky='ew',
                                       pady=(6, 0))

        ttk.Separator(body).grid(row=2, column=0, sticky='ew', pady=10)
        events = ttk.Frame(body)
        events.grid(row=3, column=0, sticky='nsew')
        events.columnconfigure(0, weight=1)
        ttk.Label(events, text='事件观测', font=('Microsoft YaHei UI', 12, 'bold')).grid(
            row=0, column=0, sticky='w')
        # The switch edits the persisted preference, not this run's effective
        # state: a one-shot --no-events/--frida-remote override must not be
        # written back by an unrelated apply.
        self.events = tk.BooleanVar(value=app.event_observation)
        ttk.Checkbutton(events,
                        text='启用事件观测（护盾实际吸收与接敌区间；认证环境默认开启）',
                        variable=self.events, command=self.sync_fields).grid(
            row=1, column=0, sticky='w', pady=(8, 4))
        self.override_note = tk.StringVar(value='')
        ttk.Label(events, textvariable=self.override_note, wraplength=650,
                  foreground='#f1bc82').grid(row=2, column=0, sticky='ew')
        self.event_state_label = tk.StringVar(value='')
        ttk.Label(events, textvariable=self.event_state_label, wraplength=650,
                  foreground=SECONDARY).grid(row=3, column=0, sticky='ew')
        self.error_state = tk.StringVar(value='')
        ttk.Label(events, textvariable=self.error_state, wraplength=650,
                  foreground='#f1bc82').grid(row=4, column=0, sticky='ew', pady=(6, 0))

        actions = ttk.Frame(body)
        actions.grid(row=4, column=0, sticky='ew', pady=(14, 0))
        ttk.Button(actions, text='应用并重新连接', command=self.apply).pack(side='left')
        ttk.Button(actions, text='关闭', command=self.close).pack(side='right')
        self.sync_fields()
        self.show_existing_diagnostics()
        self.refresh_status()
        self._status_after = self.after(500, self._poll_status)
        self._poll_after = self.after(100, self._poll_detection)
        self.lift()

    # -- state -------------------------------------------------------------
    def sync_fields(self):
        auto = self.auto.get()
        self.address_entry.configure(state='normal')
        self.address_hint.configure(
            text='自动检测开启：留空则自动选择唯一实例；多实例时从下方列表选择。'
            if auto else '自动检测关闭：请填写实例地址，例如 127.0.0.1:16384。')

    def show_existing_diagnostics(self):
        diag = self.app.connection_diagnostic or {}
        instances = diag.get('instances') or []
        if instances:
            self.fill_instances(instances)
        self.connection_status.set(self.app.connection_diagnostic_text())

    def refresh_status(self):
        overridden = self.app.events_enabled != self.app.event_observation
        self.override_note.set(
            ('本次启动由命令行%s事件观测；此处开关保存下次启动的偏好，应用后立即生效。'
             % ('启用' if self.app.events_enabled else '关闭')) if overridden else '')
        state, text = event_setting_status(
            enabled=self.events.get(), event_status=self.app.event_status,
            event_error=self.app.event_error,
            version_status=(self.app.connection_diagnostic or {}).get('version_status'),
            connection_ok=(self.app.connection_diagnostic or {}).get('connection_ok'))
        self.event_state_label.set(text)
        self.error_state.set('' if state == 'active' or state == 'disabled' else
                             '设置页仅显示诊断；未覆盖的指标保持未知。')

    def _poll_status(self):
        if not self.winfo_exists():
            return
        self.refresh_status()
        self.connection_status.set(self.app.connection_diagnostic_text())
        self._status_after = self.after(500, self._poll_status)

    # -- detection ---------------------------------------------------------
    def choose_adb(self):
        path = filedialog.askopenfilename(
            parent=self, title='选择 adb.exe',
            filetypes=[('adb.exe', 'adb.exe'), ('可执行文件', '*.exe'), ('所有文件', '*.*')])
        if path:
            self.adb.set(path)

    def start_detection(self):
        adb = self.adb.get().strip() or None
        self.detect_button.configure(state='disabled')
        self.detection_note.set('正在检测 adb 与实例…')
        thread = threading.Thread(target=self._detect_worker, args=(adb,),
                                  name='ArkDPS instance detection', daemon=True)
        thread.start()

    def _detect_worker(self, adb):
        try:
            result = self.detect(adb)
            self.detection.put(('result', result.diagnostics().as_dict()))
        except Exception as exc:  # adb or platform failure is a diagnostic
            self.detection.put(('error', str(exc)))

    def _poll_detection(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, payload = self.detection.get_nowait()
                self.detect_button.configure(state='normal')
                if kind == 'result':
                    self.show_detection(payload)
                else:
                    self.detection_note.set('检测失败：' + payload)
        except queue.Empty:
            pass
        self._poll_after = self.after(100, self._poll_detection)

    def show_detection(self, diag: dict):
        self.app.set_connection_diagnostic(diag)
        self.connection_status.set(self.app.connection_diagnostic_text())
        if diag.get('adb_path') and not self.adb.get().strip():
            self.adb.set(diag['adb_path'])
        instances = diag.get('instances') or []
        self.fill_instances(instances)
        message = diag.get('message') or diag.get('error') or diag.get('status', '')
        self.detection_note.set(message)
        selected = diag.get('selected_serial')
        if selected and not self.address.get().strip():
            self.address.set(selected)

    def fill_instances(self, instances):
        self.instance_rows = [item for item in instances if isinstance(item, dict)]
        self.listing.delete(0, 'end')
        for item in self.instance_rows:
            serial = item.get('primary_serial') or ', '.join(item.get('serials') or [])
            model = item.get('model') or item.get('instance_id') or '未知实例'
            if not item.get('accessible', True):
                state = item.get('problem') or '不可访问'
            elif not item.get('certified', True):
                state = '未认证，手动选择后仍会校验版本'
            else:
                state = '可连接'
            self.listing.insert('end', f'{model} · {serial} · {state}')

    def select_instance(self, _event=None):
        chosen = self.listing.curselection()
        if not chosen or chosen[0] >= len(self.instance_rows):
            return
        item = self.instance_rows[chosen[0]]
        serial = item.get('primary_serial') or (item.get('serials') or [None])[0]
        if not serial:
            return
        self.address.set(serial)
        # An explicit pick is a manual target even though detection found it.
        self.auto.set(False)
        self.sync_fields()
        self.detection_note.set(f'已选择 {serial}；应用后重新连接该实例。')

    # -- apply / close -----------------------------------------------------
    def apply(self):
        adb = self.adb.get().strip() or None
        address = self.address.get().strip() or None
        auto = self.auto.get()
        if not auto and not address:
            messagebox.showerror('连接设置不完整',
                                 '自动检测关闭时必须填写实例地址。', parent=self)
            return
        config = ConnectionConfig(adb_path=adb, serial=address, auto_detect=auto)
        self.app.apply_connection(config, self.events.get())
        self.detection_note.set('已保存连接与事件观测设置，正在重新连接…')
        self.connection_status.set(self.app.connection_diagnostic_text())

    def close(self):
        for name in ('_poll_after', '_status_after'):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    self.after_cancel(timer)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        if self.app.settings_window is self:
            self.app.settings_window = None
        self.destroy()
