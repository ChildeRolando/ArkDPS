"""Offline portable install/upgrade checks for ONE built ZIP.

No MuMu/ADB, no registry or machine-wide installer changes. The Tk panel is
sealed, so the only migration chain proved here is v1 layout -> WPF v3 ->
WPF restart restore.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'acceptance')]
from tools.package_windows import PACKAGE_ROOT, archive_name, digest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('archive',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'runtime'/'package-validation')
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    install=Path(tempfile.mkdtemp(prefix='clean-install-',dir=output))
    with zipfile.ZipFile(args.archive) as archive:
        for name in archive.namelist():
            if install not in (install/name).resolve().parents:
                raise ValueError('unsafe archive path')
        archive.extractall(install)
    bundle=install/'ArkDPS'
    assert {entry.name for entry in bundle.iterdir()} == PACKAGE_ROOT
    manifest_path = bundle / 'metadata/release.json'
    manifest=json.loads(manifest_path.read_text('utf-8'))
    assert manifest['format'] == 2
    assert args.archive.name == archive_name(manifest['version'], manifest['frida'] is not None)
    assert manifest['target_framework']=='net10.0-windows'
    files = {path.relative_to(bundle).as_posix(): digest(path) for path in bundle.rglob('*')
             if path.is_file() and path != manifest_path}
    assert files == manifest['files']
    assert files['collector/desktop_bridge.py'] == manifest['collector_sha256']
    # Child WPF/worker must not rely on system Python or machine-wide .NET.
    environment=dict(os.environ,PYTHONHOME='Z:/missing-python',PYTHONPATH='Z:/missing-python',
                     DOTNET_ROOT='Z:/missing-dotnet',DOTNET_ROOT_X64='Z:/missing-dotnet',
                     DOTNET_BUNDLE_EXTRACT_BASE_DIR=str(install/'dotnet-extraction'),
                     DOTNET_MULTILEVEL_LOOKUP='0',ARKDPS_GIT=shutil.which('git'),
                     PATH=str(Path(os.environ['WINDIR'])/'System32'))
    executable = bundle/'ArkDPS.Desktop.exe'
    worker = bundle/'collector/desktop_bridge.py'
    original = worker.read_bytes()
    rejection = output/'wrong-worker'
    try:
        worker.write_bytes(original + b'\n# intentionally mismatched acceptance fixture\n')
        rejected = subprocess.run([str(executable), '--offline', '--state-dir', str(rejection/'state'),
            '--verify', str(rejection)], cwd=ROOT, env=environment, timeout=30)
        assert rejected.returncode != 0
        assert '安装包两端不匹配' in (rejection/'failure.txt').read_text('utf-8')
    finally:
        worker.write_bytes(original)
    validation=output/'upgrade'
    subprocess.run([sys.executable,'-I',str(ROOT/'acceptance/verify_wpf.py'),
        '--bundle',str(bundle),'--migrate-v1','--output',str(validation)],
        cwd=ROOT,env=environment,check=True,timeout=300)
    state_dir = bundle/'collector/runtime'
    saved=json.loads((state_dir/'layout.json').read_text('utf-8'))
    assert saved['version']==3
    # Restart the bundled WPF client on exactly the layout it wrote itself.
    forward=output/'forward-restart'
    subprocess.run([str(executable),'--offline','--state-dir',str(state_dir),
        '--fixture',str(state_dir/'fixture.json'),'--verify',str(forward)],
        cwd=ROOT,env=environment,check=True,timeout=150)
    after=json.loads((forward/'receipt.json').read_text('utf-8'))
    expected=json.loads((validation/'restart/receipt.json').read_text('utf-8'))['restored_layout']
    assert after['restored_layout']['panels']==saved['panels']
    # Compare persisted settings, not cross-process GUI pixels: the window
    # manager may place a fresh process differently (including minimized
    # rectangles), which is not an upgrade regression.
    for key in ('version','theme','event_observation','panels','huds'):
        assert after['restored_layout'][key]==expected[key],key
    result=dict(status='PASS_OFFLINE_PACKAGE',head=manifest['head'],archive_sha256=hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        version=manifest['version'],framework=manifest['target_framework'],sdk=manifest['dotnet_sdk'],
        checks=['clean ZIP extraction, exact root and complete payload hashes',
            'packaged startup rejects a mismatched worker',
            'self-contained UI with invalid system DOTNET_ROOT, no dotnet/Python on PATH',
            'isolated bundled Python 3.12.10 + Frida 17.26.1',
            'old v1 layout -> native WPF v3 -> WPF restart restores the same layout',
            'copied legacy runtime retains history and connection settings',
            'panel settings, theme, locked HUD and 83% opacity retained'],
        limitations=['portable ZIP only, not MSI or registry installation',
            'synthetic data only; device remains reserved to ARK-14',
            'physical tray/game input/DPI require current-candidate manual acceptance'])
    (output/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS_OFFLINE_PACKAGE',output)


if __name__=='__main__':
    main()
