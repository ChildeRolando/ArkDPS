"""Offline native Windows smoke for the exact extracted artifact, not game acceptance."""
import json
from pathlib import Path
import platform
import sys
import time
import traceback
from types import SimpleNamespace


def run_smoke(output):
    # Never reuse receipts or write into existing runtime/config/history.
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipt = dict(status='FAIL', kind='offline-native-artifact-smoke',
                   platform=platform.platform(), frozen=bool(getattr(sys, 'frozen', False)),
                   checks={}, errors=[], unverified=['live game', '1x/2x performance',
                   'physical display changes', 'physical tray menu', 'clean Windows installation'])
    root = app = None
    try:
        from app import App, enable_dpi_awareness
        from collector import ROOT, Collector
        from character_catalog import load_character_names
        from connection import ConnectionConfigStore, ConnectionConfig
        from layout_store import LayoutStore
        from battle_history import BattleHistory, BattleRecord
        from stats_core import Statistics
        from gui_history import HistoryWindow
        from statistics_screenshot import statistics_png
        from battle_share import json_bytes
        from release_identity import load_manifest
        import tkinter as tk
        import frida
        from tray import TrayIcon

        if receipt['frozen']:
            manifest = load_manifest(ROOT, verify=True)
            receipt['head'] = manifest['head']
            receipt['release'] = manifest['release']
            assert ROOT == Path(sys.executable).resolve().parent, 'portable runtime location changed'
            receipt['checks']['bundle_hashes'] = len(manifest['files'])
        receipt['checks']['frida'] = frida.__version__
        assert frida.__version__ == '17.26.1'
        from tools.enemy_health import memcore
        from tools.game_data import bundle_root
        binary = Path(memcore.__file__).parent / 'bin/memsrv'
        assert binary.is_file() and binary.read_bytes().startswith(b'ELF'), 'missing device reader binary'
        assert bundle_root() is not None, 'missing versioned game data'
        receipt['checks']['device_reader_resources'] = True
        collector = Collector()
        assert collector.names and load_character_names()
        collector.close()  # Unconnected: no device commands.
        assert enable_dpi_awareness(), 'DPI initialization failed'
        root = tk.Tk()
        root.report_callback_exception = lambda *exc: receipt['errors'].append(
            ''.join(traceback.format_exception(*exc)))

        class OfflineApp(App):
            def collect(self):
                pass  # Explicitly synthetic: never claim live game coverage.

        store = LayoutStore(output / 'layout.json')
        connection = ConnectionConfigStore(output / 'connection.json')
        connection.save(ConnectionConfig(auto_detect=True))
        app = OfflineApp(root, SimpleNamespace(adb=None, serial=None, frida_remote=None),
                         layout_store=store, connection_store=connection,
                         tray_factory=lambda app: TrayIcon('ArkDPS offline verification',
                             lambda action: app.events.put(('tray', action)),
                             icon_path=ROOT / 'assets/arkdps.ico'))
        app.history = BattleHistory(output / 'history')
        assert app.portraits.manifest and len(app.portraits.atlases) == 2, 'missing portraits'
        stats = Statistics('release-smoke', 0)
        stats.register_source('char_108_silent', 'char_108_silent')
        stats.snapshot(0, {'char_108_silent': dict(damage=0, healing=0)})
        stats.snapshot(1, {'char_108_silent': dict(damage=100, healing=50)})
        result = stats.result()
        row = result['rows']['char_108_silent']
        row.update(name='赫默', present=True, sources=['char_108_silent'],
                   types_available=True, types_stale=False, damage_by_type={'physical': 100})
        result.update(adapter_session='release-smoke', unknown_sources=[], unmapped_tokens=[],
                      ambiguous_sources=[], mapping_conflicts=[], missing_fields=[])
        sample = dict(session='release-smoke', frame=30, game_time=1.,
                      captured_monotonic=time.monotonic(), statistics=result,
                      event_status='failed', event_error='offline smoke', statistics_complete=True)
        app.last = sample
        app.render(sample)
        app.duplicate_panel(app.panels[0])
        hud = app.create_hud(app.panels[0])
        hud.set_locked(True)
        for _ in range(8):
            root.update()
            time.sleep(.03)
        assert len(app.panels) == 2 and len(app.huds) == 1
        assert app.panels[0].view.entries[0].value == 100
        assert hud.panel.view == app.panels[0].view
        app.save_layout_now()
        restored = LayoutStore(output / 'layout.json').load()
        assert len(restored.layout.panels) == 2 and len(restored.layout.huds) == 1
        receipt['checks']['layout_roundtrip'] = True
        receipt['checks']['panels_hud'] = True
        result['ended'] = True
        record = BattleRecord.from_archive(dict(battle_id='release-smoke', generation=1,
            reason='offline_fixture', metadata=dict(stage_id=None, practice=None,
            outcome='unknown', evidence='synthetic release smoke'), statistics=result,
            last_sample=dict(session='release-smoke'), event_status='failed',
            event_error='offline smoke', snapshot_intervals=[[0, 1]]))
        app.history.save(record)
        assert not app.history.save(record).written
        saved = BattleHistory(output / 'history').get(record.battle_id).records[0]
        (output / 'statistics.json').write_bytes(json_bytes(saved))
        (output / 'statistics.png').write_bytes(statistics_png(root, saved, app.panels[0].config))
        history = HistoryWindow(app)
        root.update()
        assert len(history.records) == 1
        history.destroy()
        receipt['checks']['history_json_png'] = True
        receipt['checks']['tray'] = bool(app.tray and app.tray.available)
        assert receipt['checks']['tray'], 'native tray did not start'
        app.quit()
        deadline = time.monotonic() + 15
        while app.shutdown_report is None and time.monotonic() < deadline:
            root.update()
            time.sleep(.03)
        receipt['shutdown'] = app.shutdown_report
        assert app.shutdown_report and app.shutdown_report['ok'], 'shutdown not clean'
        assert not receipt['errors'], 'Tk callback errors'
        receipt['status'] = 'PASS'
    except Exception:
        receipt['errors'].append(traceback.format_exc())
    finally:
        if app is not None and not app.quitting:
            app.quit()
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        (output / 'receipt.json').write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if receipt['status'] == 'PASS' else 1
