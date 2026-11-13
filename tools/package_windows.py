"""Build ONE synchronized Windows x64 portable package, with both runtimes.

Run from the repository, not an extracted download. Inputs land in a fresh empty
folder; hashes are pinned here. Nothing installs into the developer's Python.
The full package includes the optional Frida client, NOT a server or ADB.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from release_identity import candidate_head
DEFAULT_VERSION = '1.0.0-rc.3'
RELEASE_VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-rc\.[1-9][0-9]*)?')
PACKAGE_ROOT = {'ArkDPS.Desktop.exe', 'README.txt', 'collector', 'python', 'metadata', 'licenses'}
PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
PYTHON_SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
FRIDA_VERSION = '17.26.1'
FRIDA_SHA256 = 'f4f6875afed9f0c493af790b2b2d18fe9bfb379a35ecb708f14c2d4b15df6e1e'
# Nothing Tk-shaped may reach the portable package: the panel is sealed and the
# WPF client renders every surface itself.
TK_MARKERS = ('tkinter', '_tkinter', '/tcl', 'tcl/', 'tk8.', 'tcl8.', 'tk86t', 'tcl86t')
BACKEND = ('desktop_bridge.py', 'battle_history.py', 'battle_lifecycle.py', 'battle_share.py',
           'capture_cli.py', 'capture_battles.py', 'collection_session.py', 'collector.py',
           'connection.py', 'event_capture.py', 'event_observer.py', 'event_stream.py',
           'frida_provision.py',
           'character_catalog.py', 'enemy_telemetry.py', 'layout_store.py', 'metrics.py', 'panel_model.py',
           'roster.py', 'snapshot_adapter.py', 'source_classification.py',
           'stage_names.py', 'stats_core.py',
           'event_probe_android2791.js')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(path, expected):
    if digest(path) != expected:
        raise ValueError('Package input SHA-256 mismatch: ' + str(path))


def unpack(source, destination):
    """Reject traversal, absolute names, symlinks and compressed-file bombs."""
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(source) as archive:
        if sum(i.file_size for i in archive.infolist()) > 750 * 1024 * 1024:
            raise ValueError('Archive expands beyond limit')
        for item in archive.infolist():
            target = (destination / item.filename).resolve()
            if destination.resolve() not in target.parents or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe archive member: ' + item.filename)
        archive.extractall(destination)


def assert_no_tk_files(names):
    """The manifest itself must prove no Tk/Tcl runtime or screenshot code shipped."""
    bad = sorted(name for name in names
                 if any(marker in name.lower() for marker in TK_MARKERS))
    if bad:
        raise RuntimeError('Sealed Tk artifacts in package: ' + ', '.join(bad))


def archive_name(version, with_events):
    suffix = '' if with_events else '-snapshot'
    return f'ArkDPS-{version}-win-x64{suffix}.zip'


def build(output, *, version=DEFAULT_VERSION, python_zip=None, frida_wheel=None, with_events=True):
    if not RELEASE_VERSION.fullmatch(version) or any(
            int(part) > 2147483647 for part in re.findall(r'[0-9]+', version)):
        raise ValueError('Use a version such as 1.0.0 or 1.0.0-rc.1')
    head = candidate_head(ROOT)
    output.mkdir(parents=True, exist_ok=True)
    inputs = Path(tempfile.mkdtemp(prefix='inputs-', dir=output))
    if python_zip is None:
        python_zip = inputs / 'python.zip'
        urllib.request.urlretrieve(PYTHON_URL, python_zip)
    check(python_zip, PYTHON_SHA256)
    if with_events:
        if frida_wheel is None:
            subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                '--no-deps', '--platform', 'win_amd64', '--python-version', '312',
                '--implementation', 'cp', '--abi', 'abi3', '--dest', str(inputs),
                'frida==' + FRIDA_VERSION], cwd=ROOT, check=True)
            frida_wheel, = inputs.glob('frida-*.whl')
        check(frida_wheel, FRIDA_SHA256)
    bundle = Path(tempfile.mkdtemp(prefix='ArkDPS-win-x64-', dir=output))
    build_artifacts = inputs / 'dotnet'
    dotnet_executable = os.environ.get('ARKDPS_DOTNET') or shutil.which('dotnet')
    if not dotnet_executable:
        raise RuntimeError('.NET 10 SDK required; set ARKDPS_DOTNET for a task-local SDK')
    subprocess.run([dotnet_executable, 'publish', str(ROOT / 'desktop/ArkDPS.Desktop/ArkDPS.Desktop.csproj'),
        '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true', '--nologo',
        '-p:ContinuousIntegrationBuild=true', '-p:PublishSingleFile=true',
        '-p:IncludeNativeLibrariesForSelfExtract=true', '-p:PublishTrimmed=false',
        '-p:DebugType=none', '--artifacts-path', str(build_artifacts), '-o', str(bundle)],
        cwd=ROOT, check=True)
    runtime_config, = (build_artifacts / 'bin/ArkDPS.Desktop').glob('*/ArkDPS.Desktop.runtimeconfig.json')
    unpack(python_zip, bundle / 'python')
    # Isolated embedded interpreter: no user site, registry Python, PATH or pip.
    (bundle/'python/python312._pth').write_text('python312.zip\n.\n../collector\nLib/site-packages\n', encoding='ascii')
    if with_events:
        unpack(frida_wheel, bundle / 'python/Lib/site-packages')
    collector = bundle / 'collector'
    collector.mkdir()
    for name in BACKEND:
        shutil.copy2(ROOT / name, collector / name)
    for name in ('assets', 'vendor'):
        shutil.copytree(ROOT / name, collector / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    notices = bundle / 'licenses'
    notices.mkdir()
    for name in ('LICENSE', 'THIRD_PARTY.md'):
        shutil.copy2(ROOT / name, notices / name)
    if (bundle / 'Assets').is_dir():
        (bundle / 'Assets').rename(notices / 'Assets')
    dotnet = Path(dotnet_executable).parent
    for name in ('LICENSE.txt', 'ThirdPartyNotices.txt'):
        if not (dotnet/name).is_file():
            raise FileNotFoundError('Required .NET redistribution notice missing: '+name)
        shutil.copy2(dotnet/name, notices/('dotnet-'+name))
    # Python LICENSE.txt and Frida dist-info/licenses/COPYING stay in their archives.
    metadata = bundle / 'metadata'
    metadata.mkdir()
    (bundle / 'README.txt').write_text(
        f'ArkDPS {version} · Windows x64\n\n'
        '解压到可写目录，双击 ArkDPS.Desktop.exe。无需安装 Python 或 .NET。\n'
        '实时统计需要开启 Root 的 MuMu 12 和已适配的安卓游戏 2.7.91。\n'
        '包内不含 ADB、模拟器或 Frida server。\n\n'
        '更新可在设置页检查。发现新版后点击「查看下载」打开 GitHub Release。\n'
        '升级时，从托盘菜单选「退出 ArkDPS」，将新版 ZIP 解压到新目录。\n'
        '如果旧版有 collector/runtime，将整个 runtime 目录复制到新版的 collector 后再启动。\n'
        '保留旧目录。需要回退时，先退出新版，再启动旧目录中的程序。\n',
        encoding='utf-8-sig')
    if {entry.name for entry in bundle.iterdir()} != PACKAGE_ROOT:
        raise RuntimeError('Unexpected files in portable package root')
    manifest = dict(format=2, version=version, protocol=1, target_framework='net10.0-windows',
        dotnet_sdk=subprocess.check_output([dotnet_executable,'--version'],cwd=ROOT,text=True).strip(),
        dotnet_runtime=json.loads(runtime_config.read_text('utf-8')), head=head,
        collector_sha256=digest(collector/'desktop_bridge.py'), python='3.12.10',
        python_source=PYTHON_URL, python_archive_sha256=PYTHON_SHA256,
        frida=FRIDA_VERSION if with_events else None, frida_wheel_sha256=FRIDA_SHA256 if with_events else None,
        files={str(p.relative_to(bundle)).replace('\\','/'):digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()})
    assert_no_tk_files(manifest['files'])
    (metadata/'release.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    # Smoke import under embedded Python with a poisoned system Python environment.
    environment=dict(os.environ, PYTHONHOME='Z:/not-a-python', PYTHONPATH='Z:/not-a-python', DOTNET_MULTILEVEL_LOOKUP='0')
    script='import desktop_bridge, sys; assert "tkinter" not in sys.modules; print(sys.version); print(sys.executable)'
    if with_events:
        script += '; import frida; assert frida.__version__ == "17.26.1"; print(frida.__version__)'
    subprocess.run([str(bundle/'python/python.exe'), '-I', '-B', '-c', script], cwd=ROOT,env=environment,check=True)
    archive = output / archive_name(version, with_events)
    staged_archive = inputs / archive.name
    with zipfile.ZipFile(staged_archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as stream:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                stream.write(path,str(Path('ArkDPS')/path.relative_to(bundle)))
    sha256 = digest(staged_archive)
    staged_archive.replace(archive)
    (output/(archive.name+'.sha256')).write_text(sha256+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps(dict(bundle=str(bundle), archive=str(archive), sha256=sha256),ensure_ascii=False))
    return bundle


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'runtime'/'packages')
    parser.add_argument('--version', default=DEFAULT_VERSION)
    parser.add_argument('--python-zip',type=Path)
    parser.add_argument('--frida-wheel',type=Path)
    parser.add_argument('--without-events',action='store_true',help='explicit snapshot-only variant, never silent downgrade')
    args=parser.parse_args()
    build(args.output.resolve(), version=args.version, python_zip=args.python_zip,
          frida_wheel=args.frida_wheel, with_events=not args.without_events)


if __name__=='__main__':
    main()
