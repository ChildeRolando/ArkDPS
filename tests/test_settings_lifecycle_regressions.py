"""ARK-11: worker recovery and native tray ownership under injected failures."""
import queue
import sys
import threading
from types import SimpleNamespace

import pytest

import app as app_module
import tray as tray_module
from app import App
from layout_store import GuiGeometry, Layout
from tray import TrayIcon


def test_production_gui_rejects_failed_dpi_before_any_tk(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['app.py', '--legacy-tk'])
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    monkeypatch.setattr(app_module, 'enable_dpi_awareness', lambda: False)
    monkeypatch.setattr(app_module.tk, 'Tk', lambda: pytest.fail('Tk created without DPI awareness'))
    with pytest.raises(SystemExit, match='DPI awareness'):
        app_module.main()


def test_first_launch_default_geometry_is_clamped():
    sizes = []
    app = App.__new__(App)
    app.layout = Layout.default()
    app.root = SimpleNamespace(minsize=lambda: (440, 500), geometry=sizes.append)
    app.work_areas = lambda: [(0, 0, 800, 600)]
    app.restore_gui_geometry()
    assert app.last_gui_geometry == GuiGeometry(0, 0, 800, 600)
    assert sizes == ['800x600+0+0']


def test_each_sample_renders_each_hud_once():
    from test_gui import sample
    app = App.__new__(App)
    calls = []
    app.statistics, app.source_rows, app.panels = {}, [], []
    app.current_battle = None
    app.last = None
    app.clock = SimpleNamespace(set=lambda *_: None)
    app.export_button = SimpleNamespace(configure=lambda **_: None)
    app.render_huds = lambda **_: calls.append('hud')
    app.render(sample())
    assert calls == ['hud']


class Function:
    def __init__(self, result=1):
        self.result = result
        self.argtypes = self.restype = None

    def __call__(self, *_):
        return self.result


def test_taskbar_readd_failure_revokes_availability_and_notifies_tk(monkeypatch):
    shell = SimpleNamespace(Shell_NotifyIconW=Function(0))
    monkeypatch.setattr(tray_module, '_shell32', lambda: shell)
    messages = []
    icon = TrayIcon('test', messages.append)
    icon.available = True
    icon._taskbar_created = 77
    icon._nid = tray_module.NOTIFYICONDATAW()
    icon._wndproc(1, 77, 0, 0)
    assert not icon.available
    assert icon.error and messages == ['unavailable']
    shell.Shell_NotifyIconW.result = 1
    icon._wndproc(1, 77, 0, 0)
    assert icon.available


@pytest.mark.parametrize('block_stage', ['window', 'icon'])
def test_start_timeout_cancels_late_initialization_and_keeps_owner(monkeypatch, block_stage):
    entered, release = threading.Event(), threading.Event()
    calls = []
    icon = TrayIcon('test', lambda *_: None)

    def step(stage):
        calls.append(stage)
        if stage == block_stage:
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(icon, '_create_window', lambda: step('window'))
    monkeypatch.setattr(icon, '_add_icon', lambda: step('icon'))
    monkeypatch.setattr(icon, '_remove_icon', lambda: calls.append('remove'))
    monkeypatch.setattr(icon, '_destroy_window', lambda: calls.append('destroy'))
    monkeypatch.setattr(icon, '_message_loop', lambda: calls.append('loop'))
    try:
        assert not icon.start(timeout=.05)
        assert entered.is_set()
        assert icon._thread is not None and icon._thread.is_alive()
    finally:
        release.set()
        worker = icon._thread
        icon.stop(timeout=5)
        if worker is not None:
            worker.join(5)
    assert 'loop' not in calls
    if block_stage == 'window':
        assert 'icon' not in calls
    assert calls[-2:] == ['remove', 'destroy']
    assert icon._thread is None and not icon.available


@pytest.mark.parametrize('has_window', [False, True])
def test_stop_timeout_preserves_thread_until_cleanup_completes(monkeypatch, has_window):
    release = threading.Event()
    icon = TrayIcon('test', lambda *_: None)
    worker = threading.Thread(target=release.wait)
    icon._thread = worker
    icon.hwnd = 12 if has_window else None
    user = SimpleNamespace(PostMessageW=Function())
    monkeypatch.setattr(tray_module, '_user32', lambda: user)
    worker.start()
    try:
        assert icon.stop(timeout=.01) is False
        assert icon._thread is worker
    finally:
        release.set()
        assert icon.stop(timeout=5) is True
    assert icon._thread is None


def test_app_retains_timed_out_tray_and_waits_before_destroy():
    app = App.__new__(App)
    app.tray = None
    app.warnings = []
    stopped = []
    icon = SimpleNamespace(available=False, error='timeout', start=lambda: False,
                           stop=lambda **_: stopped.append(True) or False)
    app.start_tray(lambda _: icon)
    assert app.tray is icon
    app.worker = SimpleNamespace(is_alive=lambda: False)
    scheduled, destroyed = [], []
    app.root = SimpleNamespace(after=lambda *_: scheduled.append(True),
                               destroy=lambda: destroyed.append(True))
    app.finish_close()
    assert scheduled and not destroyed and app.tray is icon
    icon.stop = lambda **_: True
    app.verify_shutdown = lambda: {'ok': True}
    app.finish_close()
    assert destroyed == [True] and app.tray is None


@pytest.mark.parametrize('failure_stage', ['rescan', 'connect'])
def test_cleanup_failure_does_not_kill_reconnect_worker(tmp_path, monkeypatch, failure_stage):
    app = App.__new__(App)
    app.events = queue.Queue()
    app.rescan = threading.Event()
    app.rescan.set()
    app.stop = threading.Event()
    app.collector = app.session = None
    app.retired_sessions = []
    app.collection_cleanup_pending = False
    app.connection_config = object()
    app.frida_remote = None
    app.history = None
    calls = []
    failed = SimpleNamespace(cleanup_errors={})

    def close():
        calls.append('close')
        if calls.count('close') == 1:
            failed.cleanup_errors['observer'] = ['detach refused']
            raise RuntimeError('detach refused')

    failed.close = close
    failed.enable_lifecycle = lambda *_: None
    failed.connect = lambda: (_ for _ in ()).throw(RuntimeError('connect refused'))
    failed.read = lambda: pytest.fail('retiring session still publishes samples')
    good = SimpleNamespace(close=lambda: calls.append('good_close'),
                           enable_lifecycle=lambda runtime: setattr(good, 'runtime', runtime),
                           connect=lambda: calls.append('connected'),
                           event_status='ready', event_error=None,
                           collector=SimpleNamespace(diagnostics=SimpleNamespace(
                               as_dict=lambda: {}, selected_serial='serial', version_actual='2.7.91')))

    def read():
        calls.append('read')
        app.stop.set()
        return None

    good.read = read
    def resolve(_):
        return SimpleNamespace(adb_path='adb', serial='serial',
                               diagnostics=SimpleNamespace(as_dict=lambda: {}))
    sessions = iter([failed, good] if failure_stage == 'connect' else [good])
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    monkeypatch.setattr(app_module, 'resolve_configured', resolve)
    monkeypatch.setattr(app_module, 'CollectionSession', lambda *_a, **_k: next(sessions))
    waits = []
    def wait(_):
        waits.append(True)
        assert len(waits) < 5, 'reconnect did not recover'
        if len(waits) == 1:
            assert not app.stop.is_set()
            assert app.collector is failed
            app.rescan.set()
    app.stop.wait = wait
    if failure_stage == 'rescan':
        app.collector = app.session = failed
    app.collect()
    assert calls.count('close') == 2
    assert 'connected' in calls and 'read' in calls
    assert app.session is good and failed in app.retired_sessions
    assert not App.verify_shutdown(app)['ok']
