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
    manifest=json.loads((bundle/'release.json').read_text('utf-8'))
    assert manifest['target_framework']=='net10.0-windows'
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((bundle/name).read_bytes()).hexdigest()==digest,name
    # Child WPF/worker must not rely on system Python or machine-wide .NET.
    environment=dict(os.environ,PYTHONHOME='Z:/missing-python',PYTHONPATH='Z:/missing-python',
                     DOTNET_ROOT='Z:/missing-dotnet',DOTNET_ROOT_X64='Z:/missing-dotnet',
                     DOTNET_MULTILEVEL_LOOKUP='0',ARKDPS_GIT=shutil.which('git'),
                     PATH=str(Path(os.environ['WINDIR'])/'System32'))
    validation=output/'upgrade'
    subprocess.run([sys.executable,'-I',str(ROOT/'acceptance/verify_wpf.py'),
        '--bundle',str(bundle),'--migrate-v1','--output',str(validation)],
        cwd=ROOT,env=environment,check=True,timeout=300)
    state_dir,=validation.glob('state-*')
    saved=json.loads((state_dir/'layout.json').read_text('utf-8'))
    assert saved['version']==3
    # Restart the bundled WPF client on exactly the layout it wrote itself.
    forward=output/'forward-restart'
    subprocess.run([str(bundle/'ArkDPS.Desktop.exe'),'--offline','--state-dir',str(state_dir),
        '--fixture',str(state_dir/'fixture.json'),'--verify',str(forward)],
        cwd=ROOT,env=environment,check=True,timeout=150)
    after=json.loads((forward/'receipt.json').read_text('utf-8'))
    assert after['restored_layout']['panels']==saved['panels']
    assert after['restored_layout']==json.loads((validation/'restart/receipt.json').read_text('utf-8'))['restored_layout']
    result=dict(status='PASS_OFFLINE_PACKAGE',head=manifest['head'],archive_sha256=hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        framework=manifest['target_framework'],sdk=manifest['dotnet_sdk'],
        checks=['clean ZIP extraction and every manifest file digest',
            'self-contained UI with invalid system DOTNET_ROOT, no dotnet/Python on PATH',
            'isolated bundled Python 3.12.10 + Frida 17.26.1',
            'old v1 layout -> native WPF v3 -> WPF restart restores the same layout',
            'panel settings, theme, locked HUD and 83% opacity retained'],
        limitations=['portable ZIP only, not MSI or registry installation',
            'synthetic data only; device remains reserved to ARK-14',
            'physical tray/game input/DPI require current-candidate manual acceptance'])
    (output/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS_OFFLINE_PACKAGE',output)


if __name__=='__main__':
    main()
