"""Windows acceptance for ARK-11 settings, tray and window-configuration restore.

Phase 1 drives real Tk windows: connection/event settings, a native tray icon,
hiding the GUI while a HUD keeps updating, and a real adb forward create/release.
It then starts phase 2 in a fresh interpreter so "restart" is a genuine process
restart, not a re-instantiation inside one process.

Synthetic statistics fixtures are used; the script never writes to the game and
never starts an event observer. Multi-instance selection uses a documented
synthetic adb payload, while real detection is recorded separately. A physical
tray right-click, physical monitor unplug/DPI change and a real-game explicit
quit cleanup are explicitly recorded as unverified host steps.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from app import App, enable_dpi_awareness  # noqa: E402
from connection import (ConnectionConfig, ConnectionConfigStore,  # noqa: E402
                        ConnectionUnavailable, ForwardRegistry, detect_instances,
                        resolve_configured)
from layout_store import (GuiGeometry, HudPlacement, Layout, LayoutStore,  # noqa: E402
                          monitor_work_areas)
from settings_window import SettingsWindow, event_setting_status  # noqa: E402
from test_gui import sample  # noqa: E402
from tray import TrayIcon, WM_LBUTTONDBLCLK, WM_TRAY_CALLBACK, _user32  # noqa: E402
from verify_gui import capture_window, monitors  # noqa: E402

TRACKED = ('app.py', 'connection.py', 'hud.py', 'layout_store.py',
           'settings_window.py', 'tray.py', 'gui_panel.py', 'panel_model.py',
           'collection_session.py', 'event_observer.py', 'event_service.py',
           'acceptance/verify_settings.py')


def settle(root, rounds=8):
    for _ in range(rounds):
        root.update()
        time.sleep(.03)


def reset_root(root):
    for timer in root.tk.call('after', 'info'):
        try:
            root.after_cancel(timer)
        except tk.TclError:
            pass
    try:
        root.unbind('<Configure>')
        root.deiconify()
    except tk.TclError:
        pass
    for widget in list(root.winfo_children()):
        try:
            widget.destroy()
        except tk.TclError:
            pass
    settle(root, 2)


def detection_payload():
    return dict(
        status='needs_selection', adb_path='C:\\MuMu\\adb.exe', selected_serial=None,
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
    def diagnostics(self):
        return SimpleNamespace(as_dict=detection_payload)


def real_device_checks():
    receipt = {'forward_roundtrip': {'ran': False}}
    try:
        detection = detect_instances()
    except Exception as exc:
        return {'detection': {'error': str(exc)}, 'forward_roundtrip': {'ran': False}}
    diag = detection.diagnostics().as_dict()
    receipt['detection'] = {key: diag.get(key) for key in
                            ('status', 'adb_path', 'selected_serial', 'message',
                             'connection_ok', 'version_status')}
    receipt['detection']['instances'] = [item['primary_serial']
                                         for item in diag.get('instances') or []]
    try:
        target = resolve_configured(ConnectionConfig(auto_detect=True))
        receipt['resolution'] = {'serial': target.serial, 'source': target.source}
    except ConnectionUnavailable as exc:
        receipt['resolution'] = {'error': str(exc),
                                 'status': (exc.diagnostics.as_dict()
                                            if exc.diagnostics else None)}
    adb, serial = diag.get('adb_path'), diag.get('selected_serial')
    if not adb or not serial:
        receipt['forward_roundtrip'] = {'ran': False, 'reason': 'no accessible device'}
        return receipt
    registry = ForwardRegistry()
    local = 27391
    try:
        registry.ensure(adb, serial, local=local, remote=local)
        created = [f'{key[0]}:{key[1]}' for key in registry.owned]
        registry.release(serial, local)
        listing = subprocess.run([adb, 'forward', '--list'], capture_output=True,
                                 text=True, timeout=10)
        still_listed = any(line.split()[:2] == [serial, f'tcp:{local}']
                           for line in listing.stdout.splitlines())
        receipt['forward_roundtrip'] = {
            'ran': True, 'serial': serial, 'local_port': local,
            'created': created, 'owned_after_release': list(registry.owned),
            'listed_after_release': still_listed}
    finally:
        registry.release(serial, local)
    return receipt


def create_root():
    if not enable_dpi_awareness():
        raise RuntimeError('DPI awareness could not be enabled before Tk initialization')
    return tk.Tk()


def run_restore(state: Path, output: Path, head: str):
    """Accept only successful, current-candidate evidence from this child run."""
    child_path = output / 'restore-receipt.json'
    run_id = uuid4().hex
    try:
        child_path.unlink(missing_ok=True)
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), '--phase', 'restore',
             '--state', str(state), '--output', str(output), '--run-id', run_id],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        if child.returncode != 0:
            return {'status': 'FAIL', 'returncode': child.returncode,
                    'error': child.stderr[-2000:] or 'restore process failed'}
        result = json.loads(child_path.read_text(encoding='utf-8'))
        if not isinstance(result, dict):
            raise ValueError('restore receipt is not an object')
        if result.get('run_id') != run_id or result.get('head') != head:
            raise ValueError('restore receipt does not match this run and candidate')
        if result.get('phase') != 'restore' or result.get('status') != 'PASS':
            raise ValueError('restore phase did not pass')
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'status': 'FAIL', 'error': str(exc)}


def phase_main(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    state = output / 'state'
    state.mkdir(exist_ok=True)
    for stale in state.glob('*'):
        stale.unlink()
    root = create_root()
    App.collect = lambda self: None
    layout_store = LayoutStore(state / 'layout.json')
    connection_store = ConnectionConfigStore(state / 'connection.json')
    tray_box = {}

    def tray_factory(app):
        tray = TrayIcon('ArkDPS · 验收', lambda action: app.events.put(('tray', action)),
                        icon_path=ROOT / 'assets' / 'arkdps.ico')
        tray_box['tray'] = tray
        return tray

    app = App(root, SimpleNamespace(adb='', serial='', frida_remote='127.0.0.1:27043'),
              layout_store=layout_store, connection_store=connection_store,
              tray_factory=tray_factory)
    settle(root)
    receipt = dict(
        head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                     text=True).strip(),
        code_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                     for name in TRACKED},
        fixture='Synthetic unified statistics and synthetic multi-instance payload; '
                'no game writes, no event observer',
        native_monitors=monitors(),
        tray={}, settings={}, layout={}, restart={}, device={}, errors=[],
        unverified=[
            'Physical tray right-click menu selection (native callback message is simulated).',
            'Physical monitor removal / Windows DPI change (restore is exercised with injected work areas).',
            'Real-game explicit quit cleanup of a live Frida observer and collection session '
            '(no battle session was started); the cleanup audit itself runs at quit.',
        ])

    tray = tray_box.get('tray')
    receipt['tray']['available'] = bool(tray and tray.available)
    receipt['tray']['error'] = getattr(tray, 'error', None)
    if not receipt['tray']['available']:
        receipt['errors'].append('tray unavailable')

    try:
        # --- panel configuration and HUD placement -------------------------
        first = app.panels[0]
        first.controls['category'][0].set('治疗')
        first.controls['ranking'][0].set('最近 5 秒')
        first.configure_panel()
        second = app.duplicate_panel(first)
        second.controls['category'][0].set('伤害')
        second.configure_panel()
        hud = app.open_hud(first)
        settle(root)
        hud.window.geometry('410x360+120+140')
        hud.set_opacity('75')
        hud.set_locked(True)
        root.geometry('1000x760+60+70')
        settle(root)
        assert hud.locked
        assert capture_window(root, output / 'gui-initial.png'), 'black GUI capture'
        assert capture_window(hud.window, output / 'hud-initial.png'), 'black HUD capture'

        # --- settings: multi-instance choice, adb path, event switch ------
        settings = SettingsWindow(app, detect=lambda adb: FakeDetection())
        settle(root)
        settings.show_detection(detection_payload())
        settle(root)
        receipt['settings']['instances_listed'] = [
            settings.listing.get(index) for index in range(settings.listing.size())]
        settings.listing.selection_set(1)
        settings.select_instance()
        receipt['settings']['selected_address'] = settings.address.get()
        receipt['settings']['auto_detect_after_choice'] = settings.auto.get()
        settings.adb.set('C:\\MuMu\\adb.exe')
        settings.events.set(False)
        assert capture_window(settings, output / 'settings.png'), 'black settings capture'
        settings.apply()
        settle(root)
        receipt['settings']['saved_connection'] = json.loads(
            (state / 'connection.json').read_text(encoding='utf-8'))
        receipt['settings']['event_observation_saved'] = json.loads(
            (state / 'layout.json').read_text(encoding='utf-8'))['event_observation']
        receipt['settings']['agent_state'] = {
            'events_enabled': app.events_enabled, 'frida_remote': app.frida_remote,
            'connection': app.connection_diagnostic_text()}
        assert receipt['settings']['saved_connection']['serial'] == '127.0.0.1:16416'
        assert receipt['settings']['saved_connection']['auto_detect'] is False
        assert receipt['settings']['event_observation_saved'] is False
        assert app.frida_remote is None
        settings.close()

        receipt['event_states'] = {
            state_name: event_setting_status(
                enabled=enabled, event_status=status, event_error=error,
                version_status=version)[0]
            for state_name, (enabled, status, error, version) in {
                'closed': (False, 'recording', None, 'compatible'),
                'dependency': (True, 'failed', "No module named 'frida'", 'compatible'),
                'interrupted': (True, 'gap', 'stream gap', 'compatible'),
                'unsupported': (True, 'recording', None, 'incompatible'),
                'active': (True, 'recording', None, 'compatible')}.items()}
        assert receipt['event_states'] == {
            'closed': 'disabled', 'dependency': 'dependency_missing',
            'interrupted': 'interrupted', 'unsupported': 'unsupported_version',
            'active': 'active'}

        # --- closing the GUI keeps HUD and collection alive ----------------
        app.close()
        settle(root)
        receipt['tray']['gui_state_after_close'] = root.state()
        receipt['tray']['hud_viewable_after_close'] = bool(hud.window.winfo_viewable())
        data = sample()
        app.events.put(('sample', data))
        app.tick()
        settle(root)
        receipt['tray']['hud_value_while_hidden'] = hud.panel.view.entries[0].value
        assert root.state() == 'withdrawn' and hud.window.winfo_viewable()
        assert hud.panel.view == app.panels[0].view
        assert not app.stop.is_set()
        assert capture_window(hud.window, output / 'hud-gui-hidden.png'), 'black hidden-HUD capture'

        # A native tray callback (the message the shell sends on double click)
        # must restore the GUI through the real tray thread.
        import ctypes
        from ctypes import wintypes
        user = _user32()
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
        assert user.PostMessageW(wintypes.HWND(tray.hwnd), WM_TRAY_CALLBACK, 1,
                                 WM_LBUTTONDBLCLK)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and root.state() != 'normal':
            app.tick()
            settle(root, 2)
        receipt['tray']['gui_state_after_native_callback'] = root.state()
        assert root.state() == 'normal'
        assert capture_window(root, output / 'gui-restored.png'), 'black GUI capture'

        # --- real device read-only checks ---------------------------------
        receipt['device'] = real_device_checks()

        # --- persist the layout used by the restart phase -----------------
        app.save_layout_now()
        saved = json.loads((state / 'layout.json').read_text(encoding='utf-8'))
        receipt['layout']['saved'] = saved
        assert saved['version'] == 3 and saved['huds']

        app.quit()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            time.sleep(.05)
            try:
                root.update()
            except tk.TclError:
                break
            if app.shutdown_report is not None:
                break
        receipt['shutdown'] = app.shutdown_report
        assert receipt['shutdown'] and receipt['shutdown']['ok']
    except Exception as exc:  # keep a machine-readable failure receipt
        receipt['errors'].append(repr(exc))
        try:
            app.stop.set()
            root.destroy()
        except tk.TclError:
            pass

    receipt['restart'] = run_restore(state, output, receipt['head'])
    if receipt['restart'].get('status') != 'PASS':
        receipt['errors'].append('restart phase failed')
    receipt['screenshots_sha256'] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.glob('*.png'))}
    receipt['status'] = 'PASS' if not receipt['errors'] else 'FAIL'
    (output / 'receipt.json').write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt['status'] == 'PASS' else 1


def phase_restore(state: Path, output: Path, run_id=None):
    state, output = Path(state), Path(output)
    receipt = {'phase': 'restore', 'run_id': run_id,
               'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                               text=True).strip(),
               'checks': {}, 'screenshots': {}, 'errors': []}
    App.collect = lambda self: None
    root = create_root()
    saved = json.loads((state / 'layout.json').read_text(encoding='utf-8'))
    work_areas = monitor_work_areas()
    checks = receipt['checks']

    def launch(layout_path, areas, events):
        reset_root(root)
        app = App(root, SimpleNamespace(adb='', serial='', frida_remote=events),
                  layout_store=LayoutStore(layout_path),
                  connection_store=ConnectionConfigStore(state / 'connection.json'),
                  work_area_provider=lambda: areas)
        settle(root)
        return app

    try:
        events = '127.0.0.1:27043' if saved['event_observation'] else None
        app = launch(state / 'layout.json', work_areas, events)
        checks['panel_configs'] = [panel.config.to_dict() for panel in app.panels] \
            if hasattr(app.panels[0].config, 'to_dict') else None
        checks['panels_match'] = checks['panel_configs'] == saved['panels']
        checks['events_enabled'] = app.events_enabled
        checks['events_preference_matches'] = app.events_enabled == saved['event_observation']
        restored_huds = {}
        for placement in saved['huds']:
            hud = app.huds.get(placement['panel_id'])
            restored_huds[placement['panel_id']] = None if hud is None else dict(
                x=hud.window.winfo_x(), y=hud.window.winfo_y(),
                width=hud.window.winfo_width(), height=hud.window.winfo_height(),
                opacity=float(hud.window.attributes('-alpha')), locked=hud.locked)
        checks['huds_match'] = all(
            restored_huds.get(item['panel_id']) is not None and
            abs(restored_huds[item['panel_id']]['x'] - item['x']) <= 2 and
            abs(restored_huds[item['panel_id']]['y'] - item['y']) <= 2 and
            abs(restored_huds[item['panel_id']]['width'] - item['width']) <= 2 and
            abs(restored_huds[item['panel_id']]['height'] - item['height']) <= 2 and
            abs(restored_huds[item['panel_id']]['opacity'] - item['opacity']) <= .02 and
            restored_huds[item['panel_id']]['locked'] == item['locked']
            for item in saved['huds'])
        checks['restored_huds'] = restored_huds
        checks['gui'] = dict(x=root.winfo_x(), y=root.winfo_y(),
                             width=root.winfo_width(), height=root.winfo_height())
        saved_gui = saved.get('gui')
        checks['gui_inside_work_area'] = any(
            left <= checks['gui']['x'] and
            checks['gui']['x'] + checks['gui']['width'] <= right and
            top <= checks['gui']['y'] and
            checks['gui']['y'] + checks['gui']['height'] <= bottom
            for left, top, right, bottom in work_areas)
        if saved_gui:
            checks['gui_matches_saved'] = all(
                abs(checks['gui'][key] - saved_gui[key]) <= 2
                for key in ('x', 'y', 'width', 'height'))
        if app.huds:
            hud = next(iter(app.huds.values()))
            receipt['screenshots']['hud-restored.png'] = capture_window(
                hud.window, output / 'hud-restored.png')
        receipt['screenshots']['gui-restored.png'] = capture_window(
            root, output / 'gui-restored.png')

        # Removed display: saved coordinates outside every current monitor.
        removed = state / 'variant-removed.json'
        LayoutStore(removed).save(Layout(
            panels=(app.panels[0].config,),
            huds=(HudPlacement(app.panels[0].config.panel_id, 5000, 4000, 410, 360),),
            gui=GuiGeometry(8000, 6000, 1000, 760), event_observation=True))
        app_removed = launch(removed, work_areas, None)
        hud = next(iter(app_removed.huds.values()))
        checks['removed_display'] = dict(
            gui=dict(x=root.winfo_x(), y=root.winfo_y(),
                     width=root.winfo_width(), height=root.winfo_height()),
            hud=dict(x=hud.window.winfo_x(), y=hud.window.winfo_y(),
                     width=hud.window.winfo_width(), height=hud.window.winfo_height()))
        checks['removed_display_reachable'] = any(
            left <= checks['removed_display']['gui']['x'] and
            checks['removed_display']['gui']['x'] + checks['removed_display']['gui']['width'] <= right and
            top <= checks['removed_display']['gui']['y'] and
            checks['removed_display']['gui']['y'] + checks['removed_display']['gui']['height'] <= bottom
            for left, top, right, bottom in work_areas)
        checks['removed_display_hud_reachable'] = any(
            left <= hud.window.winfo_x() and
            hud.window.winfo_x() + hud.window.winfo_width() <= right and
            top <= hud.window.winfo_y() and
            hud.window.winfo_y() + hud.window.winfo_height() <= bottom
            for left, top, right, bottom in work_areas)

        # DPI increase: saved pixel sizes no longer fit the work area.
        dpi = state / 'variant-dpi.json'
        LayoutStore(dpi).save(Layout(
            panels=(app.panels[0].config,),
            huds=(HudPlacement(app.panels[0].config.panel_id, 0, 0, 2000, 1600),),
            gui=GuiGeometry(0, 0, 3000, 2000), event_observation=True))
        small_area = [(0, 0, 1280, 720)]
        app_dpi = launch(dpi, small_area, None)
        hud = next(iter(app_dpi.huds.values()))
        checks['changed_dpi'] = dict(
            gui=dict(width=root.winfo_width(), height=root.winfo_height()),
            hud=dict(width=hud.window.winfo_width(), height=hud.window.winfo_height()))
        checks['changed_dpi_fits'] = (
            root.winfo_width() <= 1280 and root.winfo_height() <= 720 and
            hud.window.winfo_width() <= 1280 and hud.window.winfo_height() <= 720)
        receipt['screenshots']['gui-dpi-simulated.png'] = capture_window(
            root, output / 'gui-dpi-simulated.png')

        # Damaged file: diagnostic + usable default; original is preserved.
        corrupt = state / 'variant-corrupt.json'
        corrupt.write_text('{not json', encoding='utf-8')
        app_corrupt = launch(corrupt, work_areas, None)
        checks['corrupt'] = dict(
            panels=[panel.config.to_dict() for panel in app_corrupt.panels],
            warnings=list(app_corrupt.warnings),
            loaded_ok=app_corrupt.loaded.ok,
            backups=[path.name for path in state.glob('variant-corrupt.json.corrupt-*')])
        checks['corrupt_default'] = (
            len(app_corrupt.panels) == 1 and
            app_corrupt.panels[0].config.panel_id == 'default' and
            not app_corrupt.loaded.ok and bool(checks['corrupt']['backups']))

        app_corrupt.quit()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            time.sleep(.05)
            try:
                root.update()
            except tk.TclError:
                break
            if app_corrupt.shutdown_report is not None:
                break
        receipt['shutdown'] = app_corrupt.shutdown_report
        checks['shutdown_clean'] = bool(receipt['shutdown'] and receipt['shutdown']['ok'])

        required = ('panels_match', 'huds_match', 'events_preference_matches', 'gui_inside_work_area',
                    'gui_matches_saved', 'removed_display_reachable',
                    'removed_display_hud_reachable', 'changed_dpi_fits',
                    'corrupt_default', 'shutdown_clean')
        receipt['errors'] = [name for name in required if not checks.get(name)]
        receipt['status'] = 'PASS' if not receipt['errors'] else 'FAIL'
    except Exception as exc:
        receipt['errors'].append(repr(exc))
        receipt['status'] = 'FAIL'
        try:
            root.destroy()
        except tk.TclError:
            pass
    receipt['screenshots_sha256'] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.glob('*.png'))}
    (output / 'restore-receipt.json').write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt['status'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/settings-validation')
    parser.add_argument('--phase', choices=('main', 'restore'), default='main')
    parser.add_argument('--state', type=Path)
    parser.add_argument('--run-id', help='Parent-generated restore evidence identity')
    args = parser.parse_args()
    if args.phase == 'restore':
        if not args.state:
            parser.error('--phase restore requires --state')
        return phase_restore(args.state, args.output, args.run_id)
    return phase_main(args.output)


if __name__ == '__main__':
    raise SystemExit(main())
