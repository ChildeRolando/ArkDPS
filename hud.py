"""Compact native displays of live PanelViews; no collection or statistics owner."""
import ctypes
from ctypes import wintypes
import sys
import tkinter as tk
from tkinter import ttk, messagebox

from gui_panel import StatisticPanel, BG, SECONDARY


def window_handle(window):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    return user.GetAncestor(window.winfo_id(), 2)  # Tk's outer wrapper, GA_ROOT


def set_clickthrough(window, locked):
    """Layered Windows overlays pass input to applications underneath when locked."""
    if sys.platform != 'win32':
        if locked:
            raise OSError('HUD 鼠标穿透需要 Windows')
        return
    user = ctypes.WinDLL('user32', use_last_error=True)
    getter = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
    setter = user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.SetWindowLongW
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    getter.restype = ctypes.c_ssize_t
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    setter.restype = ctypes.c_ssize_t
    hwnd = window_handle(window)
    style = getter(hwnd, -20)  # GWL_EXSTYLE
    mask = 0x20 | 0x08000000  # WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    style = (style | mask | 0x80000) if locked else (style & ~mask)
    ctypes.set_last_error(0)
    if not setter(hwnd, -20, style) and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())


class HudWindow:
    def __init__(self, app, source, placement=None):
        self.app, self.source = app, source
        self.locked = False
        self.drag_origin = None
        self.ready = False
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.title('ArkDPS HUD · ' + source.config.display_title)
        self.window.overrideredirect(True)
        self.window.configure(bg=BG, highlightbackground=SECONDARY, highlightthickness=1)
        self.window.attributes('-topmost', True)
        unit = source.unit
        self.window.minsize(round(350*unit), round(230*unit))
        self.window.attributes('-alpha', placement.opacity if placement else .9)
        if placement:
            width, height = (max(round(350*unit), placement.width),
                             max(round(230*unit), placement.height))
            self.window.geometry(f'{width}x{height}+{placement.x}+{placement.y}')
        else:
            self.window.geometry(f'{round(380*unit)}x{round(360*unit)}')
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        header = ttk.Frame(self.window, padding=(8, 4))
        header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(0, weight=1)
        self.handle = ttk.Label(header, text='HUD · 拖动此处移动', foreground=SECONDARY)
        self.handle.grid(row=0, column=0, sticky='ew')
        ttk.Button(header, text='GUI', command=app.show_gui).grid(row=0, column=1, padx=4)
        ttk.Button(header, text='关闭', command=self.close).grid(row=0, column=2)
        self.handle.bind('<ButtonPress-1>', self.begin_drag)
        self.handle.bind('<B1-Motion>', self.drag)
        self.panel = StatisticPanel(self.window, source.config, changed=lambda p: None,
                                    duplicate=lambda p: None, delete=lambda p: None,
                                    open_hud=lambda p: None, portraits=app.portraits, compact=True)
        self.panel.grid(row=1, column=0, sticky='nsew')
        footer = ttk.Frame(self.window, padding=(8, 3))
        footer.grid(row=2, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value='等待可靠战斗数据')
        self.status_label = ttk.Label(footer, textvariable=self.status, foreground=SECONDARY)
        self.status_label.grid(row=0, column=0, sticky='ew')
        grip = ttk.Sizegrip(footer)
        grip.grid(row=0, column=1, sticky='se')
        # Override-redirect windows have no native sizing frame.
        grip.bind('<ButtonPress-1>', self.begin_resize)
        grip.bind('<B1-Motion>', self.resize)
        self.window.bind('<Configure>', self.wrap_status)
        self.window.deiconify()
        self.window.update_idletasks()
        self.install_controls()
        self.ready = True
        if placement and placement.locked:
            try:
                self.set_locked(True)
            except OSError as exc:
                app.record_warning('HUD 恢复锁定失败，已保持可交互：' + str(exc))

    def install_controls(self):
        host = self.source.hud_controls
        self.lock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(host, text='锁定 HUD（鼠标穿透）', variable=self.lock_var,
                        command=self.toggle_lock).pack(anchor='w')
        row = ttk.Frame(host)
        row.pack(fill='x', pady=(4, 0))
        ttk.Label(row, text='HUD 透明度', foreground=SECONDARY).pack(side='left')
        current = round(float(self.window.attributes('-alpha')) * 100)
        self.opacity = tk.DoubleVar(value=current)
        ttk.Scale(row, from_=70, to=100, variable=self.opacity,
                  command=self.set_opacity).pack(side='left', fill='x', expand=True, padx=6)
        self.opacity_label = ttk.Label(row, text=f'{current}%', width=5)
        self.opacity_label.pack(side='left')

    def wrap_status(self, _event=None):
        self.status_label.configure(wraplength=max(100, self.window.winfo_width() - 40))

    def set_opacity(self, value):
        value = max(70., min(100., float(value)))
        self.window.attributes('-alpha', value / 100)
        self.opacity_label.configure(text=f'{value:.0f}%')
        self.layout_changed()

    def layout_changed(self):
        if self.ready:
            self.app.hud_layout_changed(self)

    def toggle_lock(self):
        try:
            self.set_locked(self.lock_var.get())
        except OSError as exc:
            self.lock_var.set(self.locked)
            messagebox.showerror('HUD 锁定失败', str(exc), parent=self.app.root)

    def set_locked(self, locked):
        # Apply native state first so a failed transition cannot claim success.
        set_clickthrough(self.window, locked)
        self.locked = locked
        self.lock_var.set(locked)
        self.panel.tooltip.hide()
        self.panel.set_hover(False)
        self.handle.configure(text='HUD · 已锁定（在 GUI 解锁）' if locked else 'HUD · 拖动此处移动')
        self.layout_changed()

    def begin_drag(self, event):
        if not self.locked:
            self.drag_origin = (event.x_root, event.y_root,
                                self.window.winfo_x(), self.window.winfo_y())

    def drag(self, event):
        if not self.locked and self.drag_origin:
            x, y, left, top = self.drag_origin
            # A leading '-' in Tk geometry means distance from the right edge.
            # '+-500' instead denotes absolute virtual-desktop x=-500.
            self.window.geometry(f'+{left+event.x_root-x}+{top+event.y_root-y}')
            self.layout_changed()

    def begin_resize(self, event):
        if not self.locked:
            self.resize_origin = (event.x_root, event.y_root,
                                  self.window.winfo_width(), self.window.winfo_height())

    def resize(self, event):
        if not self.locked and hasattr(self, 'resize_origin'):
            x, y, width, height = self.resize_origin
            minimum = self.window.minsize()
            self.window.geometry(f'{max(minimum[0], width+event.x_root-x)}x'
                                 f'{max(minimum[1], height+event.y_root-y)}')
            self.layout_changed()

    def update_view(self, view, source, *, team_complete=False, reset=False):
        self.panel.config = self.source.config
        self.window.title('ArkDPS HUD · ' + view.title)
        self.panel.update_view(view, source, team_complete=team_complete, reset=reset)

    def placement(self):
        """Current geometry and presentation state for layout persistence."""
        return dict(
            panel_id=self.source.config.panel_id,
            x=self.window.winfo_x(), y=self.window.winfo_y(),
            width=self.window.winfo_width(), height=self.window.winfo_height(),
            opacity=float(self.window.attributes('-alpha')), locked=bool(self.locked))

    def _destroy(self):
        self.ready = False
        self.panel.tooltip.hide()
        for widget in self.source.hud_controls.winfo_children():
            widget.destroy()
        self.window.destroy()
        self.app.huds.pop(self.source.config.panel_id, None)

    def close(self):
        self._destroy()
        self.app.hud_closed(self)

    def destroy_for_quit(self):
        """Explicit quit: never restore the GUI while the app is shutting down."""
        self._destroy()
