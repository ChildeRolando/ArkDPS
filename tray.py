"""Windows notification-area icon using only the standard library.

The icon owns a hidden message window on its own thread. Menu commands are
reported through a caller-supplied ``emit`` callable; this module never touches
Tk widgets, so ``emit`` must be thread-safe (the app passes a queue put).

When the shell is unavailable the icon reports ``start() is False``; the app
keeps running with its taskbar fallback and shows the diagnostic instead of
pretending a tray entry exists.
"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from pathlib import Path

WM_APP = 0x8000
WM_TRAY_CALLBACK = WM_APP + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_NULL = 0x0000

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
MF_STRING = 0x0000
IDI_APPLICATION = 32512

_DEFAULT_COMMANDS = (('show', '打开主窗口'), ('quit', '退出 ArkDPS'))


class _GUID(ctypes.Structure):
    _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD),
                ('data3', wintypes.WORD), ('data4', ctypes.c_ubyte * 8)]


class _InfoUnion(ctypes.Union):
    _fields_ = [('uTimeout', wintypes.UINT), ('uVersion', wintypes.UINT)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND), ('uID', wintypes.UINT),
        ('uFlags', wintypes.UINT), ('uCallbackMessage', wintypes.UINT),
        ('hIcon', wintypes.HICON), ('szTip', wintypes.WCHAR * 128),
        ('dwState', wintypes.DWORD), ('dwStateMask', wintypes.DWORD),
        ('szInfo', wintypes.WCHAR * 256), ('info', _InfoUnion),
        ('szInfoTitle', wintypes.WCHAR * 64), ('dwInfoFlags', wintypes.DWORD),
        ('guidItem', _GUID), ('hBalloonIcon', wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', ctypes.c_void_p),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HBRUSH),
                ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]


class TrayIcon:
    """A Windows notification-area icon with a two-item command menu."""

    def __init__(self, title: str, emit, *, icon_path: Path | str | None = None,
                 commands=_DEFAULT_COMMANDS):
        self.title = title
        self.emit = emit
        self.icon_path = Path(icon_path) if icon_path else None
        self.commands = tuple(commands)
        self.available = False
        self.error: str | None = None
        self.hwnd = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._wndproc_ref = None
        self._class_name = f'ArkDPSTray{id(self):x}'
        self._icon = None
        self._icon_owned = False
        self._taskbar_created = 0
        self._nid = None

    # -- lifecycle ---------------------------------------------------------
    def start(self, timeout: float = 5.0) -> bool:
        if sys.platform != 'win32':
            self.error = '系统托盘需要 Windows'
            return False
        if self._thread is not None:
            return self.available
        self._thread = threading.Thread(target=self._run, name='ArkDPS tray', daemon=True)
        self._thread.start()
        self._started.wait(timeout)
        return self.available

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is not None and self.hwnd:
            user = _user32()
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
            user.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self._thread.join(timeout)
        self._thread = None
        self.available = False

    # -- internals ---------------------------------------------------------
    def _run(self):
        try:
            self._create_window()
            self._add_icon()
            self.available = True
            self._started.set()
            self._message_loop()
        except Exception as exc:  # the app degrades instead of crashing
            self.error = str(exc)
        finally:
            try:
                self._remove_icon()
            finally:
                self._destroy_window()
                self.available = False
                self._started.set()
                self._stopped.set()

    def _create_window(self):
        global wndproc_type
        user = _user32()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        self._wndproc_ref = wndproc_type(self._wndproc)
        wndclass = WNDCLASSW()
        wndclass.style = 0
        wndclass.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
        wndclass.hInstance = kernel.GetModuleHandleW(None)
        wndclass.lpszClassName = self._class_name
        if not user.RegisterClassW(ctypes.byref(wndclass)):
            raise ctypes.WinError(ctypes.get_last_error())
        user.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user.CreateWindowExW.restype = wintypes.HWND
        self.hwnd = user.CreateWindowExW(0, self._class_name, 'ArkDPS', 0,
                                         0, 0, 0, 0, None, None,
                                         wndclass.hInstance, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        user.RegisterWindowMessageW.restype = wintypes.UINT
        self._taskbar_created = user.RegisterWindowMessageW('TaskbarCreated')

    def _load_icon(self):
        user = _user32()
        if self.icon_path is not None and self.icon_path.is_file():
            user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                        wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                        wintypes.UINT]
            user.LoadImageW.restype = wintypes.HANDLE
            handle = user.LoadImageW(None, str(self.icon_path), IMAGE_ICON, 0, 0,
                                     LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if handle:
                self._icon_owned = True
                return handle
        user.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        user.LoadIconW.restype = wintypes.HICON
        return user.LoadIconW(None, ctypes.cast(IDI_APPLICATION, wintypes.LPCWSTR))

    def _notify_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY_CALLBACK
        data.hIcon = self._icon
        data.szTip = self.title[:127]
        return data

    def _add_icon(self):
        shell = _shell32()
        shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell.Shell_NotifyIconW.restype = wintypes.BOOL
        self._icon = self._load_icon()
        self._nid = self._notify_data()
        if not shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid)):
            raise OSError(ctypes.get_last_error(), '无法创建系统托盘图标')

    def _remove_icon(self):
        if self._nid is None:
            return
        try:
            shell = _shell32()
            shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                                ctypes.POINTER(NOTIFYICONDATAW)]
            shell.Shell_NotifyIconW.restype = wintypes.BOOL
            shell.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        except OSError:
            pass
        finally:
            self._nid = None
            if self._icon_owned and self._icon:
                try:
                    _user32().DestroyIcon(wintypes.HICON(self._icon))
                except OSError:
                    pass
            self._icon = None
            self._icon_owned = False

    def _destroy_window(self):
        if self.hwnd:
            try:
                _user32().DestroyWindow(wintypes.HWND(self.hwnd))
            except OSError:
                pass
            self.hwnd = None

    def _message_loop(self):
        user = _user32()
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                     wintypes.UINT, wintypes.UINT]
        user.GetMessageW.restype = ctypes.c_int
        message = wintypes.MSG()
        while True:
            result = user.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            if result == 0:
                return
            user.TranslateMessage(ctypes.byref(message))
            user.DispatchMessageW(ctypes.byref(message))

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY_CALLBACK:
            event = int(lparam) & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._emit('show')
                return 0
            if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
                return 0
        elif message == WM_CLOSE:
            # Remove the icon before its owning window disappears; a vanished
            # window cannot reliably delete the shell entry.
            self._remove_icon()
            _user32().DestroyWindow(wintypes.HWND(hwnd))
            return 0
        elif message == WM_DESTROY:
            _user32().PostQuitMessage(0)
            return 0
        elif self._taskbar_created and message == self._taskbar_created:
            # Explorer restarted; the previous icon no longer exists.
            try:
                if self._nid is not None:
                    shell = _shell32()
                    shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid))
            except OSError:
                pass
            return 0
        return _user32().DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self):
        user = _user32()
        user.CreatePopupMenu.restype = wintypes.HMENU
        user.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                                     ctypes.c_size_t, wintypes.LPCWSTR]
        user.AppendMenuW.restype = wintypes.BOOL
        user.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                        wintypes.LPVOID]
        user.TrackPopupMenu.restype = ctypes.c_int
        user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user.GetCursorPos.restype = wintypes.BOOL
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        menu = user.CreatePopupMenu()
        if not menu:
            return
        try:
            for index, (_key, label) in enumerate(self.commands, start=1):
                user.AppendMenuW(menu, MF_STRING, index, label)
            point = wintypes.POINT()
            if not user.GetCursorPos(ctypes.byref(point)):
                return
            user.SetForegroundWindow(wintypes.HWND(self.hwnd))
            chosen = user.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                         point.x, point.y, 0,
                                         wintypes.HWND(self.hwnd), None)
            user.PostMessageW(wintypes.HWND(self.hwnd), WM_NULL, 0, 0)
            if 1 <= chosen <= len(self.commands):
                self._emit(self.commands[chosen - 1][0])
        finally:
            user.DestroyMenu(menu)

    def _emit(self, action: str):
        try:
            self.emit(action)
        except Exception:
            # A listener failure must not tear down the native message loop.
            pass


wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM)


_DLLS: dict[str, ctypes.WinDLL] = {}


def _dll(name: str) -> ctypes.WinDLL:
    """Cache DLL objects: argtypes/prototypes must survive across calls."""
    if name not in _DLLS:
        _DLLS[name] = ctypes.WinDLL(name, use_last_error=True)
    return _DLLS[name]


def _user32():
    return _dll('user32')


def _shell32():
    return _dll('shell32')
