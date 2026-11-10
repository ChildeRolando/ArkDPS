"""Build ONE synchronized Windows x64 portable package, with both runtimes.

Run from the repository, not an extracted download. Inputs land in a fresh empty
folder; hashes are pinned here. Nothing installs into the developer's Python.
The full package includes both the Frida client and the device-side server for
the certified MuMu x86_64 ABI; ADB itself is never distributed or installed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
PYTHON_SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
FRIDA_VERSION = '17.26.1'
FRIDA_SERVER_VERSION = FRIDA_VERSION
FRIDA_SHA256 = 'f4f6875afed9f0c493af790b2b2d18fe9bfb379a35ecb708f14c2d4b15df6e1e'
FRIDA_SERVER_ABI = 'x86_64'
FRIDA_SERVER_URL = ('https://github.com/frida/frida/releases/download/'
                    + FRIDA_VERSION + '/frida-server-' + FRIDA_VERSION
                    + '-android-' + FRIDA_SERVER_ABI + '.xz')
FRIDA_SERVER_XZ_SHA256 = 'b51775e3e8f897800fd9bcd1a57cf27014b29436b08cac1ef2c157c4611fd04e'
FRIDA_SERVER_SHA256 = 'f6fee988d080b1f83ec9fa01b94cbd1ca4bd70441f17cd6e29956bea75c792b9'
BACKEND = ('desktop_bridge.py', 'battle_history.py', 'battle_lifecycle.py', 'battle_share.py',
           'capture_cli.py', 'capture_battles.py', 'collection_session.py', 'collector.py',
           'connection.py', 'event_capture.py', 'event_observer.py', 'event_service.py',
           'event_stream.py',
           'character_catalog.py', 'layout_store.py', 'metrics.py', 'panel_model.py',
           'roster.py', 'snapshot_adapter.py', 'stats_core.py', 'event_probe_android2791.js')


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


def unpack_xz(source, destination):
    """Decompress one pinned frida-server archive into a new file."""
    if destination.exists():
        raise FileExistsError('Refusing to overwrite existing input: ' + str(destination))
    with lzma.open(source, 'rb') as compressed, destination.open('xb') as stream:
        while block := compressed.read(1024 * 1024):
            stream.write(block)
    return destination


def build(output, *, python_zip=None, frida_wheel=None, frida_server=None, with_events=True):
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise RuntimeError('Release packages require a clean committed source tree')
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
        if frida_server is None:
            frida_server = inputs / ('frida-server-' + FRIDA_VERSION + '-android-'
                                     + FRIDA_SERVER_ABI + '.xz')
            urllib.request.urlretrieve(FRIDA_SERVER_URL, frida_server)
        check(frida_server, FRIDA_SERVER_XZ_SHA256)
    bundle = Path(tempfile.mkdtemp(prefix='ArkDPS-win-x64-', dir=output))
    dotnet_executable = os.environ.get('ARKDPS_DOTNET') or shutil.which('dotnet')
    if not dotnet_executable:
        raise RuntimeError('.NET 10 SDK required; set ARKDPS_DOTNET for a task-local SDK')
    subprocess.run([dotnet_executable, 'publish', str(ROOT / 'desktop/ArkDPS.Desktop/ArkDPS.Desktop.csproj'),
        '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true', '--nologo',
        '-p:ContinuousIntegrationBuild=true', '-o', str(bundle)], cwd=ROOT, check=True)
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
    if with_events:
        server_dir = collector / 'frida-server'
        server_dir.mkdir()
        server_name = ('frida-server-' + FRIDA_VERSION + '-android-' + FRIDA_SERVER_ABI)
        unpack_xz(frida_server, server_dir / server_name)
        check(server_dir / server_name, FRIDA_SERVER_SHA256)
        (server_dir / 'manifest.json').write_text(json.dumps(dict(
            version=FRIDA_VERSION,
            assets={FRIDA_SERVER_ABI: dict(file=server_name, sha256=FRIDA_SERVER_SHA256)}),
            indent=2) + '\n', encoding='utf-8')
    for name in ('LICENSE', 'THIRD_PARTY.md'):
        shutil.copy2(ROOT / name, bundle / name)
    notices = bundle / 'licenses'
    notices.mkdir()
    dotnet = Path(dotnet_executable).parent
    for name in ('LICENSE.txt', 'ThirdPartyNotices.txt'):
        if not (dotnet/name).is_file():
            raise FileNotFoundError('Required .NET redistribution notice missing: '+name)
        shutil.copy2(dotnet/name, notices/('dotnet-'+name))
    # Python LICENSE.txt and Frida dist-info/licenses/COPYING stay in their archives.
    manifest = dict(format=1, protocol=1, target_framework='net10.0-windows',
        dotnet_sdk=subprocess.check_output([dotnet_executable,'--version'],cwd=ROOT,text=True).strip(),
        dotnet_runtime=json.loads((bundle/'ArkDPS.Desktop.runtimeconfig.json').read_text('utf-8')), head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        collector_sha256=digest(collector/'desktop_bridge.py'), python='3.12.10',
        python_source=PYTHON_URL, python_archive_sha256=PYTHON_SHA256,
        frida=FRIDA_VERSION if with_events else None, frida_wheel_sha256=FRIDA_SHA256 if with_events else None,
        frida_server=FRIDA_VERSION if with_events else None,
        frida_server_abi=FRIDA_SERVER_ABI if with_events else None,
        frida_server_sha256=FRIDA_SERVER_SHA256 if with_events else None,
        frida_server_source=FRIDA_SERVER_URL if with_events else None,
        frida_server_archive_sha256=FRIDA_SERVER_XZ_SHA256 if with_events else None,
        files={str(p.relative_to(bundle)).replace('\\','/'):digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()})
    (bundle/'release.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (bundle/'使用说明.txt').write_text('双击 ArkDPS.Desktop.exe。两端和运行时随同一包发布，无需安装 Python 或 .NET。\n'
        '请解压到可写目录；配置和历史在 collector/runtime。整个目录一起替换，不混用旧 worker。\n'
        '含与 MuMu x86_64 匹配的 Frida 设备端服务；连接后由 ArkDPS 校验、推送到设备并启动，退出时只停止/移除自己创建的进程与转发。\n'
        '不含 ADB 或模拟器。\n'
        '明确“退出”会等待采集清理；关闭主窗仅收起到托盘，HUD 和采集继续。\n',encoding='utf-8')
    # Smoke import under embedded Python with a poisoned system Python environment.
    environment=dict(os.environ, PYTHONHOME='Z:/not-a-python', PYTHONPATH='Z:/not-a-python', DOTNET_MULTILEVEL_LOOKUP='0')
    script='import desktop_bridge, sys; assert "tkinter" not in sys.modules; print(sys.version); print(sys.executable)'
    if with_events:
        script += '; import frida; assert frida.__version__ == "17.26.1"; print(frida.__version__)'
    subprocess.run([str(bundle/'python/python.exe'), '-I', '-c', script], cwd=ROOT,env=environment,check=True)
    archive = output / (bundle.name + '.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as stream:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                stream.write(path,str(Path('ArkDPS')/path.relative_to(bundle)))
    (output/(archive.name+'.sha256')).write_text(digest(archive)+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps(dict(bundle=str(bundle), archive=str(archive), sha256=digest(archive)),ensure_ascii=False))
    return bundle


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'runtime'/'packages')
    parser.add_argument('--python-zip',type=Path)
    parser.add_argument('--frida-wheel',type=Path)
    parser.add_argument('--frida-server',type=Path,help='pinned frida-server .xz for the certified ABI')
    parser.add_argument('--without-events',action='store_true',help='explicit snapshot-only variant, never silent downgrade')
    args=parser.parse_args()
    build(args.output.resolve(),python_zip=args.python_zip,frida_wheel=args.frida_wheel,
          frida_server=args.frida_server,with_events=not args.without_events)


if __name__=='__main__':
    main()
