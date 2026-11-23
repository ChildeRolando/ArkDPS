"""Foreground native-only acceptance, with Frida imports explicitly forbidden."""
import argparse
import ctypes
from ctypes import wintypes
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class BlockFridaImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'native_pc.frida_runtime' or fullname == 'frida' or fullname.startswith('frida.'):
            raise RuntimeError('native-only acceptance forbids Frida imports: ' + fullname)
        return None


def audit_native_wire(pipe, audit):
    """Keep bounded ACK/stop reply evidence, never startup/authentication data."""
    if getattr(pipe, '_native_wire_audited', False):
        return
    request = pipe.request

    def observed(operation, **arguments):
        tracked = operation in ('ack', 'stop')
        evidence = {'operation': operation}
        if operation == 'ack' and type(arguments.get('sequence')) is int:
            evidence['requested_sequence'] = arguments['sequence']
        try:
            reply = request(operation, **arguments)
        except Exception as error:
            if tracked:
                audit('native_only_wire_evidence', dict(evidence, outcome='error', error_type=type(error).__name__))
            raise
        if tracked:
            evidence.update(outcome='reply', error_present=bool(reply.get('error')))
            for key in ('acknowledged', 'active'):
                if type(reply.get(key)) is int:
                    evidence[key] = reply[key]
            if type(reply.get('native_quiet')) is bool:
                evidence['native_quiet'] = reply['native_quiet']
            audit('native_only_wire_evidence', evidence)
        return reply

    pipe.request = observed
    pipe._native_wire_audited = True


def module_inventory(process):
    """Inspect one owned handle; an incomplete scan cannot certify absence."""
    result = dict(complete=False, process_instance=list(process.process_instance), frida_modules=[])
    try:
        if not process.process_alive():
            return dict(result, reason='process_exited')
        api, handle = process.api, process.handle
        modules = (wintypes.HMODULE * 4096)()
        needed = wintypes.DWORD()
        if not api.psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3):
            return dict(result, api='EnumProcessModulesEx', win32_error=ctypes.get_last_error())
        if needed.value > ctypes.sizeof(modules) or needed.value % ctypes.sizeof(wintypes.HMODULE):
            return dict(result, reason='incomplete_module_list')
        total = needed.value // ctypes.sizeof(wintypes.HMODULE)
        for module in modules[:total]:
            path = ctypes.create_unicode_buffer(32768)
            count = api.psapi.GetModuleFileNameExW(handle, module, path, len(path))
            if not count or count >= len(path):
                return dict(result, api='GetModuleFileNameExW', win32_error=ctypes.get_last_error())
            name = Path(path.value).name
            if 'frida' in name.casefold():
                result['frida_modules'].append(name)
        if not process.process_alive():
            return dict(result, reason='process_changed_during_inventory')
        return dict(result, complete=True, module_count=total)
    except Exception as exc:
        return dict(result, error_type=type(exc).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=900)
    parser.add_argument('--wait-seconds', type=float, default=600)
    args = parser.parse_args(argv)
    if not (0 < args.seconds <= 3600 and 0 < args.wait_seconds <= 600):
        parser.error('capture seconds must be positive and bounded')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    try:
        installed = version('frida')
    except PackageNotFoundError:
        installed = None
    if installed is not None:
        parser.error('use the dedicated interpreter without a Frida distribution')
    guard = BlockFridaImports()
    sys.meta_path.insert(0, guard)
    from app import parser_for
    from acceptance.capture_pc_probe_pair import candidate_identity
    from capture_cli import run_capture
    from acceptance.source_prefix_capture import PrefixAuditSession

    (output / 'candidate.json').write_text(json.dumps(dict(candidate_identity(),
        acceptance_mode='native-only', frida_imports_blocked=True,
        frida_distribution=installed, real_client_acceptance='pending'),
        ensure_ascii=False, indent=2), encoding='utf-8')

    inventories = []

    class NativeOnlySession(PrefixAuditSession):
        def _attach_observer(self):
            ready = super()._attach_observer()
            if ready:
                audit_native_wire(self.observer._pipe, self.audit)
            return ready

        def inventory(self, phase):
            if self.collector.process is None:
                return
            record = dict(module_inventory(self.collector.process), phase=phase)
            inventories.append(record)
            self.audit('native_only_module_inventory', record)
            if record['frida_modules']:
                raise RuntimeError('Frida module found in native-only game process')

        def wait_for_game_init(self, **options):
            super().wait_for_game_init(**options)
            if not any(row['phase'] == 'ready' for row in inventories):
                self.inventory('ready')

        def close(self):
            try:
                if self.collector.process is not None:
                    self.inventory('before_stop')
            finally:
                super().close()

    capture_args = parser_for().parse_args([
        '--platform', 'native_pc', '--native-game-root', str(args.game_root),
        '--native-pc-event-backend', 'native', '--capture-seconds', str(args.seconds),
        '--wait-battle-seconds', str(args.wait_seconds), '--baseline-timeout', str(args.wait_seconds),
        '--output-dir', str(output), '--stop-file', str(output / 'STOP'), '--require-progress', '--require-events',
    ])
    capture_args.collect_native_actions = True
    failure = None
    summary = {}
    try:
        summary = run_capture(capture_args, session_factory=NativeOnlySession, state_dir=output / 'state',
                              baseline_poll_seconds=.01)
    except BaseException as exc:
        failure = f'{type(exc).__name__}: {exc}'
    finally:
        imported = sorted(name for name in sys.modules
                          if name == 'frida' or name.startswith('frida.') or name == 'native_pc.frida_runtime')
        ready_scans = [row for row in inventories if row['phase'] == 'ready']
        stop_scans = [row for row in inventories if row['phase'] == 'before_stop']
        receipt = dict(status='failed' if failure else 'captured', error=failure,
            evidence_class='live_native_only_candidate', frida_distribution=installed,
            frida_imports_blocked=True, frida_modules_imported=imported,
            game_module_inventories=inventories,
            complete_boundary_inventories_without_frida=bool(ready_scans and stop_scans
                and all(row['complete'] and not row['frida_modules'] for row in inventories)),
            samples=summary.get('samples'), live_acceptance_passed=False,
            limitation='Two boundary inventories do not certify every instant or untested mechanics.')
        (output / 'native-only-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    if failure:
        print(failure, file=sys.stderr)
        return 2
    print('NATIVE_ONLY_CAPTURED', output, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
