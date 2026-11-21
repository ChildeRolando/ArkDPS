"""Exercise native update and rollback with two actual portable ZIPs and offline state."""

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(ROOT / 'tools')]

from battle_history import BattleHistory
from connection import ConnectionConfig, ConnectionConfigStore
from layout_store import Layout, LayoutStore
from package_windows import UPDATE_PROTOCOL, archive_name, digest, unpack
from test_battle_history import record


def release_manifest(archive):
    with zipfile.ZipFile(archive) as package:
        manifest = json.loads(package.read('ArkDPS/metadata/release.json'))
    assert manifest['format'] == 2 and manifest['update_protocol'] == UPDATE_PROTOCOL
    assert manifest['frida'] is not None and archive.name == archive_name(manifest['version'], True)
    return manifest


def read_receipt(path):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(.25)
    raise TimeoutError(f'Hot update did not produce a valid receipt: {path}')


def wait_for_exit(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    synchronize = 0x00100000
    invalid_parameter = 87
    handle = kernel.OpenProcess(synchronize, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == invalid_parameter:
            return  # The detached process already exited.
        raise ctypes.WinError(error)
    try:
        if kernel.WaitForSingleObject(handle, 30_000) != 0:
            raise TimeoutError(f'Hot update fixture process {pid} did not exit')
    finally:
        kernel.CloseHandle(handle)


def verify_scenario(source, target, old, new, new_digest, output, scenario):
    install = Path(tempfile.mkdtemp(prefix=scenario + '-', dir=output))
    unpack(source, install / 'package')
    bundle = install / 'package' / 'ArkDPS'
    state = bundle / 'collector/runtime'
    state.mkdir()
    LayoutStore(state / 'layout.json').save(replace(Layout.default(), theme='babel'))
    connection = ConnectionConfig(serial='127.0.0.1:16384', auto_detect=False)
    ConnectionConfigStore(state / 'connection.json').save(connection)
    BattleHistory(state / 'history').save(record())
    history = {path.name: path.read_bytes() for path in (state / 'history').glob('*.json')}
    assert len(history) == 1

    executable = bundle / 'ArkDPS.Desktop.exe'
    versions = bundle / '.arkdps-update' / 'versions'
    staged = versions / f"{new['version']}-{new_digest[:16]}"
    environment = dict(os.environ, PYTHONHOME='Z:/missing-python', PYTHONPATH='Z:/missing-python',
        DOTNET_ROOT='Z:/missing-dotnet', DOTNET_ROOT_X64='Z:/missing-dotnet',
        DOTNET_BUNDLE_EXTRACT_BASE_DIR=str(install / 'dotnet-extraction'),
        DOTNET_MULTILEVEL_LOOKUP='0', PATH=str(Path(os.environ['WINDIR']) / 'System32'))

    def launch(mode, expected_exit=0):
        receipt_path = install / f'process-{mode}.json'
        request = install / f'request-{mode}.json'
        request.write_text(json.dumps(dict(archive=str(target), version=new['version'],
            receipt=str(receipt_path), scenario=mode)), encoding='utf-8')
        process = subprocess.run([str(executable), '--offline', '--state-dir', str(state),
                                  '--verify-update', str(request)], cwd=install, env=environment,
                                 timeout=180)
        assert process.returncode == expected_exit, (mode, process.returncode, receipt_path)
        return receipt_path

    def require_result(receipt_path, outcome, expected):
        receipt = read_receipt(receipt_path)
        assert receipt['outcome'] == outcome, receipt
        assert receipt['version'] == expected['version'] and receipt['protocol'] == expected['protocol'], receipt
        assert Path(receipt['state_directory']).resolve() == state.resolve()
        expected_executable = staged / executable.name if expected is new else executable
        assert Path(receipt['executable']).resolve() == expected_executable.resolve()
        wait_for_exit(receipt['pid'])
        return receipt

    if scenario == 'interrupted':
        marker = Path(str(launch(scenario)) + '.interrupted')
        interrupted = read_receipt(marker)
        wait_for_exit(interrupted['helper_pid'])
        wait_for_exit(interrupted['candidate_pid'])
        assert not (versions.parent / 'selection.json').exists()
        receipts = {scenario: interrupted,
                    'interrupted_relaunch': require_result(launch('relaunch'), 'selected', old)}
    else:
        outcome = {'success': 'updated', 'failure': 'rolled_back', 'tampered': 'rejected',
                   'old_exit_failure': 'rolled_back'}[scenario]
        expected = new if scenario == 'success' else old
        receipts = {scenario: require_result(launch(scenario, int(scenario == 'old_exit_failure')), outcome, expected)}
        if scenario == 'success':
            receipts['relaunch'] = require_result(launch('relaunch'), 'selected', new)
    assert LayoutStore(state / 'layout.json').load().layout.theme == 'babel'
    assert ConnectionConfigStore(state / 'connection.json').load().config == connection
    assert {path.name: path.read_bytes() for path in (state / 'history').glob('*.json')} == history
    assert json.loads((bundle / 'metadata/release.json').read_text(encoding='utf-8')) == old
    for name, expected_digest in old['files'].items():
        assert digest(bundle / name) == expected_digest, name

    if scenario == 'success':
        metadata = staged / 'metadata/release.json'
        assert json.loads(metadata.read_text(encoding='utf-8')) == new
        files = {path.relative_to(staged).as_posix(): digest(path)
                 for path in staged.rglob('*') if path.is_file() and path != metadata}
        assert files == new['files'], sorted(files.keys() ^ new['files'].keys())
    elif scenario == 'tampered':
        assert not any(versions.glob('*/ArkDPS.Desktop.exe'))
    return receipts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, target, output = args.source.resolve(), args.target.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    old, new = release_manifest(source), release_manifest(target)
    assert old['version'] != new['version']
    target_digest = digest(target)
    scenarios = {}
    for scenario in ('success', 'failure', 'tampered', 'interrupted', 'old_exit_failure'):
        print(f'Verifying hot update scenario {scenario}', flush=True)
        scenarios.update(verify_scenario(source, target, old, new, target_digest, output, scenario))
    result = dict(status='PASS_OFFLINE_HOT_UPDATE', source_version=old['version'], source_head=old['head'],
        source_archive_sha256=digest(source), target_version=new['version'], target_head=new['head'],
        target_archive_sha256=target_digest, scenarios=scenarios,
        limitations=['Synthetic data and simulated GitHub responses. No game device is contacted.'])
    (output / 'receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS_OFFLINE_HOT_UPDATE', output)


if __name__ == '__main__':
    main()
