"""Tray integration, hidden-GUI lifecycle and explicit-quit cleanup evidence."""
import queue
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import tkinter as tk

from app import App
from connection import ConnectionConfigStore
from layout_store import LayoutStore
from test_gui import gui, native_root, sample  # noqa: F401
from test_window_restore import reset_root
from tray import TrayIcon, WM_LBUTTONDBLCLK, WM_TRAY_CALLBACK

TRAY_ROOT = Path(__file__).resolve().parents[1]


class FakeTray:
    def __init__(self, available=True, error=None):
        self.available = available
        self.error = error
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return self.available

    def stop(self):
        self.stopped = True
        self.available = False


def launch_tray(native_root, monkeypatch, tmp_path, tray_factory):
    reset_root(native_root)
    monkeypatch.setattr(App, 'collect', lambda self: None)
    app = App(native_root, SimpleNamespace(adb='', serial='', frida_remote=None),
              layout_store=LayoutStore(tmp_path / 'layout.json'),
              connection_store=ConnectionConfigStore(tmp_path / 'connection.json'),
              tray_factory=tray_factory,
              work_area_provider=lambda: [(0, 0, 1920, 1040)])
    native_root.update()
    return app


def test_close_with_tray_hides_gui_and_keeps_hud_and_collection(monkeypatch, tmp_path,
                                                               native_root):
    made = []

    def factory(app):
        made.append(FakeTray())
        return made[-1]

    app = launch_tray(native_root, monkeypatch, tmp_path, factory)
    assert made and made[0].started
    hud = app.open_hud(app.panels[0])
    app.close()
    native_root.update()
    assert native_root.state() == 'withdrawn'
    assert not app.stop.is_set()
    assert hud.window.winfo_viewable()
    # The shared collection session keeps publishing while the GUI is hidden.
    data = sample()
    app.events.put(('sample', data))
    app.tick()
    assert hud.panel.view.entries[0].value == 1230.
    app.events.put(('tray', 'show'))
    app.tick()
    native_root.update()
    assert native_root.state() == 'normal'


def test_explicit_quit_from_tray_stops_collection_and_removes_tray(monkeypatch, tmp_path,
                                                                   native_root):
    made = []

    def factory(app):
        made.append(FakeTray())
        return made[-1]

    app = launch_tray(native_root, monkeypatch, tmp_path, factory)
    finished = []
    monkeypatch.setattr(app, 'finish_close', lambda: finished.append(True))
    app.events.put(('tray', 'quit'))
    app.tick()
    assert app.stop.is_set() and app.quitting
    assert made[0].stopped
    assert app.tray is None
    assert finished == [True]


def test_unavailable_tray_falls_back_to_taskbar_with_diagnostic(monkeypatch, tmp_path,
                                                               native_root):
    def factory(app):
        return FakeTray(available=False, error='no shell')

    app = launch_tray(native_root, monkeypatch, tmp_path, factory)
    assert app.tray is None
    assert any('系统托盘不可用' in warning for warning in app.warnings)
    app.open_hud(app.panels[0])
    app.close()
    native_root.update()
    assert native_root.state() == 'iconic'
    assert not app.stop.is_set()


def test_tray_construction_failure_degrades_and_is_reported(monkeypatch, tmp_path,
                                                            native_root):
    def factory(app):
        raise OSError('user32 unavailable')

    app = launch_tray(native_root, monkeypatch, tmp_path, factory)
    assert app.tray is None
    assert any('系统托盘初始化失败' in warning for warning in app.warnings)
    app.tick()
    assert '系统托盘初始化失败' in app.diagnostics.get()


def test_explicit_quit_reports_observer_session_and_forward_cleanup(gui):
    app = gui
    app.session = SimpleNamespace(
        closed=True,
        observer=SimpleNamespace(script=None, session=None),
        collector=SimpleNamespace(forward_registry=SimpleNamespace(owned={})))
    report = app.verify_shutdown()
    assert report == dict(ok=True, session_closed=True, observer_released=True,
                          forwards_released=True, issues=[])

    app.session = SimpleNamespace(
        closed=False,
        observer=SimpleNamespace(script=object(), session=object()),
        collector=SimpleNamespace(forward_registry=SimpleNamespace(
            owned={('127.0.0.1:16384', 27381): ('adb', 27381)})))
    report = app.verify_shutdown()
    assert not report['ok']
    assert report['session_closed'] is False
    assert report['observer_released'] is False
    assert report['forwards_released'] is False
    assert 'ADB 转发' in ' '.join(report['issues'])


def test_quit_destroys_huds_and_keeps_shutdown_report_available(monkeypatch, tmp_path,
                                                                native_root):
    made = []

    def factory(app):
        made.append(FakeTray())
        return made[-1]

    app = launch_tray(native_root, monkeypatch, tmp_path, factory)
    hud = app.open_hud(app.panels[0])
    finished = []
    monkeypatch.setattr(app, 'finish_close', lambda: finished.append(True))
    app.quit()
    assert not hud.window.winfo_exists()
    assert not app.huds
    assert app.tray is None and made[0].stopped
    assert finished == [True]


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows notification area')
def test_native_tray_icon_starts_emits_command_and_stops():
    messages = queue.Queue()
    icon_path = TRAY_ROOT / 'assets' / 'arkdps.ico'
    tray = TrayIcon('ArkDPS · 测试', messages.put, icon_path=icon_path)
    try:
        if not tray.start():
            pytest.skip('desktop shell unavailable: %s' % tray.error)
        assert tray.available and tray.hwnd
        # Route a native callback exactly as the shell does on double click.
        import ctypes
        from ctypes import wintypes
        from tray import _user32
        user = _user32()
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
        assert user.PostMessageW(wintypes.HWND(tray.hwnd), WM_TRAY_CALLBACK, 1,
                                 WM_LBUTTONDBLCLK)
        assert messages.get(timeout=5) == 'show'
    finally:
        tray.stop()
    assert not tray.available and tray.hwnd is None and tray._nid is None


def test_tray_quit_through_tick_does_not_schedule_after_destroy(monkeypatch, tmp_path,
                                                               native_root):
    made = []

    def factory(app):
        made.append(FakeTray())
        return made[-1]

    app = launch_tray(native_root, monkeypatch, tmp_path, factory)
    destroyed = []
    monkeypatch.setattr(native_root, 'destroy', lambda: destroyed.append(True))
    scheduled = []
    original_after = native_root.after

    def after(milliseconds, function=None, *args):
        if function == app.tick:
            scheduled.append(milliseconds)
        if function is None:
            return original_after(milliseconds)
        return original_after(milliseconds, function, *args)

    monkeypatch.setattr(native_root, 'after', after)
    app.events.put(('tray', 'quit'))
    app.tick()
    assert destroyed == [True]
    assert scheduled == []
    assert app.quitting and app.stop.is_set()
