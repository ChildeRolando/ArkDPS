"""Connection settings, event-observation states and multi-instance choice."""
import json
from types import SimpleNamespace

import pytest

import connection as conn
from app import App, event_label
from connection import ConnectionConfigStore
from layout_store import LayoutStore
from settings_window import SettingsWindow, event_setting_status
from test_connection import FakeAdb, device_line, identity
from test_gui import native_root  # noqa: F401 (reuse the session Tk interpreter)
import settings_window


@pytest.fixture
def root(native_root):
    # Reuse the same real Tk interpreter as the GUI/HUD/restore tests; repeatedly
    # creating interpreters on Windows can fail while sourcing init.tcl.
    def clear():
        for widget in list(native_root.winfo_children()):
            if isinstance(widget, SettingsWindow):
                widget.close()
        for timer in native_root.tk.call('after', 'info'):
            native_root.after_cancel(timer)
        native_root.unbind('<Configure>')
        native_root.deiconify()
        for widget in list(native_root.winfo_children()):
            widget.destroy()
        native_root.update()

    clear()
    yield native_root
    clear()


@pytest.fixture
def app_at(root, monkeypatch, tmp_path):
    monkeypatch.setattr(App, 'collect', lambda self: None)
    layout = LayoutStore(tmp_path / 'layout.json')
    connection = ConnectionConfigStore(tmp_path / 'connection.json')
    app = App(root, SimpleNamespace(adb='', serial='', frida_remote=None),
              layout_store=layout, connection_store=connection,
              work_area_provider=lambda: [(0, 0, 1920, 1040)])
    root.update()
    return app, layout, connection


# -- pure event-observation distinctions ------------------------------------
def test_closed_event_observation_never_publishes_shields_as_zero():
    state, text = event_setting_status(enabled=False, event_status='recording',
                                       event_error=None, version_status='compatible')
    assert state == 'disabled'
    assert '未知' in text and '不会记为零' in text


@pytest.mark.parametrize('error', ["No module named 'frida'",
                                   'Frida 17.26.1 is required',
                                   'Unable to connect to remote frida-server'])
def test_missing_frida_dependency_or_endpoint_is_distinguished(error):
    state, text = event_setting_status(enabled=True, event_status='failed',
                                       event_error=error, version_status='compatible')
    assert state == 'dependency_missing'
    assert '快照降级' in text and '伤害/治疗累计继续' in text


def test_interrupted_observation_keeps_covered_interval_and_falls_back():
    state, text = event_setting_status(enabled=True, event_status='gap',
                                       event_error='stream gap', version_status='compatible')
    assert state == 'interrupted'
    assert '中断' in text and '快照降级' in text


@pytest.mark.parametrize('version', ['incompatible', 'not_installed'])
def test_unsupported_or_missing_game_never_claims_snapshot_results(version):
    state, text = event_setting_status(enabled=False, event_status=None,
                                       event_error=None, version_status=version)
    assert state == 'unsupported_version'
    assert '发布' in text
    assert '快照降级' not in text


def test_active_observation_claims_only_observed_intervals():
    state, text = event_setting_status(enabled=True, event_status='recording',
                                       event_error=None, version_status='compatible')
    assert state == 'active' and '已观察' in text and '启动前' in text


# -- resolution policy ------------------------------------------------------
@pytest.fixture
def adb_path(monkeypatch):
    monkeypatch.setattr(conn, 'resolve_adb_path', lambda explicit=None: 'C:\\MuMu\\adb.exe')


def test_multi_instance_choice_uses_the_saved_serial_only_when_present(adb_path):
    fake = FakeAdb(
        'List of devices attached\n' + device_line('127.0.0.1:16384') +
        device_line('127.0.0.1:16416'),
        identities={'127.0.0.1:16384': identity('a'),
                    '127.0.0.1:16416': identity('b')})
    target = conn.resolve_configured(
        conn.ConnectionConfig(serial='127.0.0.1:16416', auto_detect=True), fake)
    assert target.serial == '127.0.0.1:16416' and target.source == 'selected'


def test_multi_instance_without_choice_asks_the_player_and_lists_instances(adb_path):
    fake = FakeAdb(
        'List of devices attached\n' + device_line('127.0.0.1:16384') +
        device_line('127.0.0.1:16416'),
        identities={'127.0.0.1:16384': identity('a'),
                    '127.0.0.1:16416': identity('b')})
    with pytest.raises(conn.ConnectionUnavailable) as raised:
        conn.resolve_configured(conn.ConnectionConfig(auto_detect=True), fake)
    diagnostics = raised.value.diagnostics
    assert diagnostics.status is conn.DetectionStatus.NEEDS_SELECTION
    assert len(diagnostics.instances) == 2
    assert '选择' in diagnostics.message


def test_unique_instance_wins_even_if_a_stale_address_is_saved(adb_path):
    fake = FakeAdb('List of devices attached\n' + device_line('127.0.0.1:16384'),
                   identities={'127.0.0.1:16384': identity('a')})
    target = conn.resolve_configured(
        conn.ConnectionConfig(serial='127.0.0.1:16800', auto_detect=True), fake)
    assert target.serial == '127.0.0.1:16384' and target.source == 'auto'


def test_manual_mode_requires_an_address():
    with pytest.raises(conn.ConnectionUnavailable) as raised:
        conn.resolve_configured(conn.ConnectionConfig(auto_detect=False))
    assert raised.value.diagnostics.status is conn.DetectionStatus.NO_INSTANCE
    assert '自动检测已关闭' in raised.value.diagnostics.message


def test_manual_mode_probes_the_saved_address(adb_path):
    fake = FakeAdb('List of devices attached\n', versions={'127.0.0.1:16500': '2.7.91'})
    target = conn.resolve_configured(
        conn.ConnectionConfig(serial='127.0.0.1:16500', auto_detect=False), fake)
    assert target.serial == '127.0.0.1:16500' and target.source == 'manual'


# -- settings window --------------------------------------------------------
def detection_payload():
    return dict(
        status='needs_selection', adb_path='C:\\MuMu\\adb.exe',
        selected_serial=None,
        instances=[
            dict(instance_id='a', serials=['127.0.0.1:16384'], model='HBP-AL00',
                 accessible=True, certified=True, primary_serial='127.0.0.1:16384',
                 problem=''),
            dict(instance_id='b', serials=['127.0.0.1:16416'], model='HBP-AL00-2',
                 accessible=True, certified=True, primary_serial='127.0.0.1:16416',
                 problem='')],
        devices=[], message='发现多个 MuMu 实例，请选择要连接的实例。', error='',
        connection_ok=False, version_status='unknown',
        version_expected='2.7.91', version_actual=None)


class FakeDetection:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    def diagnostics(self):
        return SimpleNamespace(as_dict=lambda: self.payload)


def test_settings_apply_persists_manual_address_and_event_choice(app_at, root):
    app, layout, connection = app_at
    window = SettingsWindow(app, detect=lambda adb: FakeDetection(detection_payload()))
    window.auto.set(False)
    window.adb.set('C:\\MuMu\\adb.exe')
    window.address.set('127.0.0.1:16416')
    window.events.set(False)
    window.apply()
    root.update()
    saved = connection.load().config
    assert saved.adb_path == 'C:\\MuMu\\adb.exe'
    assert saved.serial == '127.0.0.1:16416' and saved.auto_detect is False
    assert app.events_enabled is False and app.frida_remote is None
    assert app.connection_config.serial == '127.0.0.1:16416'
    assert app.rescan.is_set() and '重新连接' in app.connection
    stored = json.loads((layout.path).read_text(encoding='utf-8'))
    assert stored['event_observation'] is False
    window.close()


def test_settings_apply_rejects_empty_address_with_auto_detect_off(app_at, monkeypatch):
    app, _layout, _connection = app_at
    errors = []
    monkeypatch.setattr(settings_window.messagebox, 'showerror',
                        lambda *args, **kwargs: errors.append(args))
    window = SettingsWindow(app, detect=lambda adb: FakeDetection(detection_payload()))
    app.rescan.clear()
    window.auto.set(False)
    window.address.set('')
    window.apply()
    assert errors and '实例地址' in errors[0][1]
    assert not app.rescan.is_set()
    window.close()


def test_detection_lists_instances_and_selection_becomes_manual(app_at, root):
    app, _layout, _connection = app_at
    window = SettingsWindow(app, detect=lambda adb: FakeDetection(detection_payload()))
    window.start_detection()
    window._poll_detection()
    root.update()
    assert window.listing.size() == 2
    assert '127.0.0.1:16384' in window.listing.get(0)
    assert '多个' in window.detection_note.get()
    window.listing.selection_set(1)
    window.select_instance()
    assert window.address.get() == '127.0.0.1:16416'
    assert window.auto.get() is False
    assert '已选择' in window.detection_note.get()
    assert app.connection_diagnostic['status'] == 'needs_selection'
    window.close()


def test_detection_failure_is_reported_without_closing_the_window(app_at, root):
    app, _layout, _connection = app_at

    def fail(_adb):
        raise OSError('adb missing')

    window = SettingsWindow(app, detect=fail)
    window.start_detection()
    window._poll_detection()
    root.update()
    assert '检测失败' in window.detection_note.get()
    assert str(window.detect_button['state']) == 'normal'
    window.close()


def test_event_status_text_in_settings_distinguishes_dependency_failure(app_at, root):
    app, _layout, _connection = app_at
    app.connection_diagnostic = {'version_status': 'compatible', 'connection_ok': True}
    app.event_status, app.event_error = 'failed', "No module named 'frida'"
    window = SettingsWindow(app, detect=lambda adb: FakeDetection(detection_payload()))
    window.events.set(True)
    window.refresh_status()
    text = window.event_state_label.get()
    assert '依赖' in text and '快照降级' in text
    window.events.set(False)
    window.refresh_status()
    assert '已关闭' in window.event_state_label.get()


def test_settings_reopens_a_single_window(app_at):
    app, _layout, _connection = app_at
    first = app.open_settings()
    assert app.open_settings() is first
    first.close()
    second = app.open_settings()
    assert second is not first
    second.close()


def test_event_label_still_covers_degraded_states():
    assert event_label('gap') != event_label('failed')
    assert '快照' in event_label('failed')


def test_settings_apply_keeps_saved_preference_under_command_line_override(app_at, root):
    app, layout, _connection = app_at
    # This run has events off while the persisted preference stays enabled,
    # exactly like a `--no-events` launch over an enabled layout.
    assert app.events_enabled is False and app.event_observation is True
    window = SettingsWindow(app, detect=lambda adb: FakeDetection(detection_payload()))
    assert window.events.get() is True
    assert '命令行' in window.override_note.get()
    window.apply()
    root.update()
    stored = json.loads((layout.path).read_text(encoding='utf-8'))
    assert stored['event_observation'] is True
    assert app.events_enabled is True and app.frida_remote is not None
    window.refresh_status()
    assert window.override_note.get() == ''
    window.close()


@pytest.fixture
def configured_app(root, monkeypatch, tmp_path):
    """A launch whose one-shot connection values differ from saved preferences."""
    monkeypatch.setattr(App, 'collect', lambda self: None)
    connection = ConnectionConfigStore(tmp_path / 'connection.json')
    preference = conn.ConnectionConfig(adb_path=r'C:\Saved\adb.exe',
                                       serial='127.0.0.1:16416', auto_detect=True)
    connection.save(preference)
    app = App(root, SimpleNamespace(adb=r'C:\Launch\adb.exe',
                                    serial='127.0.0.1:16500',
                                    frida_remote='127.0.0.1:55927'),
              layout_store=LayoutStore(tmp_path / 'layout.json'),
              connection_store=connection,
              work_area_provider=lambda: [(0, 0, 1920, 1040)])
    root.update()
    return app, connection, preference


@pytest.mark.parametrize('toggle_events', [False, True])
def test_settings_apply_preserves_custom_frida_endpoint(configured_app, toggle_events):
    app, _connection, _preference = configured_app
    endpoint = app.frida_remote
    window = SettingsWindow(app)
    if toggle_events:
        window.events.set(False)
        window.apply()
        assert app.frida_remote is None
        window.events.set(True)
    window.apply()
    assert app.events_enabled is True
    assert app.frida_remote == endpoint == '127.0.0.1:55927'
    window.close()


@pytest.mark.parametrize('edit', ['none', 'events', 'adb', 'address', 'auto'])
def test_settings_apply_only_persists_edited_connection_preferences(configured_app, edit):
    from dataclasses import replace

    app, connection, preference = configured_app
    assert app.connection_config.adb_path == r'C:\Launch\adb.exe'
    assert app.connection_config.serial == '127.0.0.1:16500'
    assert app.connection_config.auto_detect is False
    window = SettingsWindow(app)
    assert window.adb.get() == preference.adb_path
    assert window.address.get() == preference.serial
    assert window.auto.get() == preference.auto_detect
    assert '命令行覆盖' in window.detection_note.get()
    expected = preference
    if edit == 'events':
        window.events.set(False)
    elif edit == 'adb':
        window.adb.set(r'C:\Edited\adb.exe')
        expected = replace(preference, adb_path=r'C:\Edited\adb.exe')
    elif edit == 'address':
        window.address.set('127.0.0.1:16600')
        expected = replace(preference, serial='127.0.0.1:16600')
    elif edit == 'auto':
        window.auto.set(False)
        expected = replace(preference, auto_detect=False)
    window.apply()
    assert connection.load().config == expected
    assert app.connection_config == expected
    # Repeated apply/reopen must not resurrect the one-shot launch overrides.
    window.close()
    reopened = SettingsWindow(app)
    assert reopened.adb.get() == expected.adb_path
    assert reopened.address.get() == expected.serial
    assert reopened.auto.get() == expected.auto_detect
    reopened.apply()
    assert connection.load().config == expected
    reopened.close()


def test_connection_failure_clears_previous_event_status_in_gui_and_hud(app_at):
    app, _, _ = app_at
    hud = app.open_hud(app.panels[0])
    app.events.put(('event', ('recording', None)))
    app.tick()
    assert '事件观测中' in app.diagnostics.get()
    assert '事件观测中' in hud.status.get()
    app.events.put(('event', (None, None)))
    app.tick()
    assert app.event_status is app.event_error is None
    assert app.event_state == event_label(None, None)
    assert '事件观测中' not in app.diagnostics.get()
    assert '事件观测中' not in hud.status.get()
