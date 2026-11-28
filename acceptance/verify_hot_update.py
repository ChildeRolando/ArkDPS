"""Exercise native Windows updates and rollback with portable archives and offline state."""

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
from package_windows import (FRIDA_SERVER_LICENSE_NAME, UPDATE_PROTOCOL, archive_name, digest, unpack,
                             update_archive_name)
from test_battle_history import record
from windows_manifest import require_admin_test_host, verify_executable_manifest

FULL_ASSET_ID = 987654321
LAYERED_ASSET_ID = 987654322
LEGACY_VERSION = '1.0.0-rc.48'
LEGACY_HEAD = '1701cb40f6aa9c25323468d9f7c9d40511ab52c9'


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


def verify_scenario(source, target, old, new, primary_digest, output, case, scenario, *,
                    layered=None, expected_downloads=None, damaged_donor=None, check_relaunch_recovery=False):
    install = Path(tempfile.mkdtemp(prefix=case + '-', dir=output))
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
    capture = state / 'captures' / 'wpf-20261121-000000-000001-0123456789abcdef0123456789abcdef-part-000001.jsonl'
    capture.parent.mkdir()
    capture_record = b'{"kind":"connected","data":{}}\n'
    capture.write_bytes(capture_record)

    executable = bundle / 'ArkDPS.Desktop.exe'
    verify_executable_manifest(executable)
    damaged_content = b'Donor file modified by the offline update verifier.\n'
    if damaged_donor is not None:
        original = bundle / damaged_donor
        assert digest(original) == old['files'][damaged_donor] == new['files'][damaged_donor]
        original.write_bytes(damaged_content)

    versions = bundle / '.arkdps-update' / 'versions'
    staged = versions / f"{new['version']}-{primary_digest[:16]}"
    environment = dict(os.environ, PYTHONHOME='Z:/missing-python', PYTHONPATH='Z:/missing-python',
        DOTNET_ROOT='Z:/missing-dotnet', DOTNET_ROOT_X64='Z:/missing-dotnet',
        DOTNET_BUNDLE_EXTRACT_BASE_DIR=str(install / 'dotnet-extraction'),
        DOTNET_MULTILEVEL_LOOKUP='0', PATH=str(Path(os.environ['WINDIR']) / 'System32'))

    def launch(mode, *, label=None):
        label = mode if label is None else label
        receipt_path = install / f'process-{label}.json'
        request = install / f'request-{label}.json'
        payload = dict(archive=str(target), version=new['version'],
            receipt=str(receipt_path), scenario=mode)
        if layered is not None:
            payload['layered_archive'] = str(layered)
        request.write_text(json.dumps(payload), encoding='utf-8')
        process = subprocess.run([str(executable), '--offline', '--state-dir', str(state),
                                  '--verify-update', str(request)], cwd=install, env=environment,
                                 timeout=180)
        allowed_exit_codes = (0, 1) if mode == 'old_exit_failure' else (0,)
        assert process.returncode in allowed_exit_codes, (case, mode, process.returncode, receipt_path)
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
        start_receipt = launch(scenario)
        marker = Path(str(start_receipt) + '.interrupted')
        interrupted = read_receipt(marker)
        wait_for_exit(interrupted['helper_pid'])
        wait_for_exit(interrupted['candidate_pid'])
        assert not (versions.parent / 'selection.json').exists()
        receipts = {case: interrupted,
                    case + '_relaunch': require_result(launch('relaunch'), 'selected', old)}
    else:
        outcome = {'success': 'updated', 'failure': 'rolled_back', 'tampered': 'rejected',
                   'old_exit_failure': 'rolled_back'}[scenario]
        expected = new if scenario == 'success' else old
        start_receipt = launch(scenario)
        receipts = {case: require_result(start_receipt, outcome, expected)}
        if scenario == 'success':
            receipts[case + '_relaunch'] = require_result(launch('relaunch'), 'selected', new)
    if expected_downloads is not None:
        transfers = read_receipt(start_receipt.with_suffix('.downloads.json'))
        assert transfers == expected_downloads, (case, transfers, expected_downloads)
        receipts[case]['downloads'] = transfers
    assert LayoutStore(state / 'layout.json').load().layout.theme == 'babel'
    assert ConnectionConfigStore(state / 'connection.json').load().config == connection
    assert {path.name: path.read_bytes() for path in (state / 'history').glob('*.json')} == history
    assert capture.read_bytes() == capture_record
    assert json.loads((bundle / 'metadata/release.json').read_text(encoding='utf-8')) == old
    for name, expected_digest in old['files'].items():
        if name == damaged_donor:
            assert (bundle / name).read_bytes() == damaged_content, name
        else:
            assert digest(bundle / name) == expected_digest, name

    if scenario == 'success':
        metadata = staged / 'metadata/release.json'
        assert json.loads(metadata.read_text(encoding='utf-8')) == new
        files = {path.relative_to(staged).as_posix(): digest(path)
                 for path in staged.rglob('*') if path.is_file() and path != metadata}
        assert files == new['files'], sorted(files.keys() ^ new['files'].keys())
        if check_relaunch_recovery:
            runtime_version = new['dotnet_runtime_version']
            hostfxr = staged / 'collector/dotnet/host/fxr' / runtime_version / 'hostfxr.dll'
            original_hostfxr = hostfxr.read_bytes()
            try:
                hostfxr.write_bytes(b'Damaged private .NET host for restart recovery test.\n')
                recovery = launch('relaunch', label='relaunch-damaged-runtime')
                receipts['relaunch_damaged_runtime'] = require_result(recovery, 'selected', old)
            finally:
                hostfxr.write_bytes(original_hostfxr)
    elif scenario == 'tampered':
        assert not any(versions.glob('*/ArkDPS.Desktop.exe'))
    elif scenario == 'old_exit_failure':
        # Assert what the actual handoff observed rather than the launcher's return code.
        assert '旧版采集器未确认正常退出' in (state / 'wpf-update.log').read_text(encoding='utf-8')
        selection = json.loads((versions.parent / 'selection.json').read_text(encoding='utf-8'))
        assert selection['Current'] is None
    return receipts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--layered-target', type=Path,
                        help='incremental asset for target, with measured download and fallback checks')
    parser.add_argument('--legacy-source', type=Path,
                        help='published rc.48 archive for upgrade and original-entry relaunch')
    args = parser.parse_args()
    require_admin_test_host()
    source, target, output = args.source.resolve(), args.target.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    old, new = release_manifest(source), release_manifest(target)
    assert old['version'] != new['version']
    target_digest = digest(target)
    full_transfer = dict(asset_id=FULL_ASSET_ID, bytes=target.stat().st_size)
    layered = args.layered_target.resolve() if args.layered_target else None
    layered_digest = None
    if layered is not None:
        assert layered.name == update_archive_name(target.name)
        with zipfile.ZipFile(layered) as package:
            assert json.loads(package.read('ArkDPS/metadata/release.json')) == new
        layered_digest = digest(layered)
        assert 0 < layered.stat().st_size < full_transfer['bytes'], 'layered asset is not smaller than the full asset'
    primary_transfers = [full_transfer]
    if layered is not None:
        primary_transfers = [dict(asset_id=LAYERED_ASSET_ID, bytes=layered.stat().st_size)]

    scenarios = {}

    def run(case, scenario, *, archive=source, source_manifest=old, use_layered=False, transfers=None,
            damaged_donor=None, check_relaunch_recovery=False):
        print(f'Verifying hot update scenario {case}', flush=True)
        scenarios.update(verify_scenario(
            archive, target, source_manifest, new, layered_digest if use_layered else target_digest,
            output, case, scenario, layered=layered if use_layered else None, expected_downloads=transfers,
            damaged_donor=damaged_donor, check_relaunch_recovery=check_relaunch_recovery,
        ))

    run('full_only', 'success', transfers=[full_transfer], check_relaunch_recovery=True)
    if layered is not None:
        run('layered', 'success', use_layered=True, transfers=primary_transfers)
        damaged_donor = 'licenses/' + FRIDA_SERVER_LICENSE_NAME
        assert damaged_donor in new['update_shared_paths']
        run('layered_fallback', 'success', use_layered=True,
            transfers=[*primary_transfers, full_transfer], damaged_donor=damaged_donor)
    for scenario in ('failure', 'tampered', 'interrupted', 'old_exit_failure'):
        run(scenario, scenario, use_layered=layered is not None, transfers=primary_transfers)

    legacy = args.legacy_source.resolve() if args.legacy_source else None
    legacy_evidence = None
    if legacy is not None:
        manifest = release_manifest(legacy)
        assert (manifest['version'], manifest['head']) == (LEGACY_VERSION, LEGACY_HEAD), manifest['head']
        run('legacy_rc48', 'success', archive=legacy, source_manifest=manifest)
        legacy_evidence = dict(version=manifest['version'], head=manifest['head'], archive_sha256=digest(legacy))

    result = dict(status='PASS_OFFLINE_HOT_UPDATE', source_version=old['version'], source_head=old['head'],
        source_archive_sha256=digest(source), target_version=new['version'], target_head=new['head'],
        target_archive_sha256=target_digest, layered_archive_sha256=layered_digest, legacy_source=legacy_evidence,
        shared_capture_log_verified=True, scenarios=scenarios,
        limitations=['Synthetic data and simulated GitHub responses. No game device is contacted.'])
    (output / 'receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS_OFFLINE_HOT_UPDATE', output)


if __name__ == '__main__':
    main()
