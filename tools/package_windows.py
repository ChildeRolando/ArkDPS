"""Build ONE synchronized Windows x64 portable package, with both runtimes.

Run from the repository, not an extracted download. Inputs land in a fresh empty
folder; hashes are pinned here. Nothing installs into the developer's Python.
The full package includes the Frida client, native PC Gadget and Android server. ADB
continues to come from the user's emulator installation.
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
from native_pc.internal_entry import HOST_NAME, HOST_PATH
# The vendored upstream also owns a regular package named tools.
sys.path.insert(0, str(ROOT / 'tools'))
from frida_gadget_asset import stage_gadget, gadget_metadata
from windows_manifest import verify_executable_manifest
DEFAULT_VERSION = '1.0.0-rc.41'
UPDATE_PROTOCOL = 1
RELEASE_VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-rc\.[1-9][0-9]*)?')
PACKAGE_ROOT = {'ArkDPS.Desktop.exe', 'README.txt', 'collector', 'python', 'metadata', 'licenses'}
PYTHON_URL = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
PYTHON_SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
FRIDA_VERSION = '17.26.1'
FRIDA_SHA256 = 'f4f6875afed9f0c493af790b2b2d18fe9bfb379a35ecb708f14c2d4b15df6e1e'
FRIDA_SERVER_NAME = f'frida-server-{FRIDA_VERSION}-android-x86_64.xz'
FRIDA_SERVER_PATH = f'collector/frida-server/{FRIDA_SERVER_NAME}'
FRIDA_SERVER_URL = f'https://github.com/frida/frida/releases/download/{FRIDA_VERSION}/{FRIDA_SERVER_NAME}'
FRIDA_SERVER_SHA256 = 'b51775e3e8f897800fd9bcd1a57cf27014b29436b08cac1ef2c157c4611fd04e'
FRIDA_SERVER_LICENSE_NAME = 'frida-server-COPYING.txt'
FRIDA_SERVER_LICENSE_URL = f'https://raw.githubusercontent.com/frida/frida-core/{FRIDA_VERSION}/COPYING'
FRIDA_SERVER_LICENSE_SHA256 = '5ea1544b51a28bc823b03159190d4108f9fb4f4ef912389f5137c6d295e175b2'
# Nothing Tk-shaped may reach the portable package: the panel is sealed and the
# WPF client renders every surface itself.
TK_MARKERS = ('tkinter', '_tkinter', '/tcl', 'tcl/', 'tk8.', 'tcl8.', 'tk86t', 'tcl86t')
BACKEND = ('desktop_bridge.py', 'battle_history.py', 'battle_lifecycle.py', 'battle_share.py', 'boss_catalog.py',
           'capture_cli.py', 'capture_battles.py', 'collection_session.py', 'collector.py', 'damage_settlement.py',
           'connection.py', 'desktop_audit.py', 'log_context.py', 'event_capture.py', 'event_observer.py', 'event_probe.py', 'event_stream.py',
           'frida_provision.py', 'stack_capture.py', 'stack_capture.wprp', 'game_fps.py', 'game_fps_android.py',
           'character_catalog.py', 'enemy_telemetry.py', 'layout_store.py', 'metrics.py', 'panel_model.py',
           'roster.py', 'snapshot_adapter.py', 'source_classification.py',
           'stage_names.py', 'stats_core.py', 'native_pc_collector.py',
           'event_probe_android2791.js', 'event_probe_native_pc.js', 'event_probe_autochess.js', 'event_probe_runtime.js',
           'multiplayer_battle.py', 'autochess_context.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(path, expected):
    if digest(path) != expected:
        raise ValueError('Package input SHA-256 mismatch: ' + str(path))


def pinned_input(path, *, destination, url, sha256):
    if path is None:
        path = destination
        urllib.request.urlretrieve(url, path)
    check(path, sha256)
    return path


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


def archive_name(version, with_events, *, internal_backend=False, with_native_events=False,
                 with_native_hooks=False):
    if with_native_hooks:
        suffix = '-native-hooks-startup-test'
    elif internal_backend:
        suffix = '-internal-events'
    elif with_native_events:
        suffix = '-native-events'
    else:
        suffix = '' if with_events else '-snapshot'
    return f'ArkDPS-{version}-win-x64{suffix}.zip'


def assert_no_frida_payload(names):
    """Source provisioning helpers are not a client, Gadget or server payload."""
    forbidden = ('python/lib/site-packages/frida', 'python/lib/site-packages/_frida',
                 'collector/vendor/frida/', 'collector/frida-server/')
    bad = sorted(name for name in names if name.lower().startswith(forbidden)
                 or name.lower() == 'collector/native_pc/frida_runtime.py')
    if bad:
        raise RuntimeError('Frida payload in native-hooks startup-test package: ' + ', '.join(bad))


def stage_native_hook_payload(binary, collector, notices):
    """Copy only an already-built, owning-source-validated native payload.

    This never builds or loads a DLL and never looks in ROOT/runtime. Both the
    supplied bytes and the final copied bytes use the loader's validation gate.
    """
    from native_pc.native_hook_source import (RUNTIME_SOURCES, NATIVE_HOOK_LICENSES,
        NATIVE_HOOK_PACKAGE_PATH, NATIVE_HOOK_BUILD_MANIFEST_PATH, validate_native_hook_payload)
    binary = Path(binary)
    source_dir = ROOT / 'native_pc/native_event_runtime'
    validate_native_hook_payload(binary, source_dir=source_dir, license_dir=binary.parent)
    destination = collector.parent / NATIVE_HOOK_PACKAGE_PATH
    build_manifest = collector.parent / NATIVE_HOOK_BUILD_MANIFEST_PATH
    destination.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(binary, destination)
    shutil.copy2(binary.with_suffix('.build.json'), build_manifest)
    for name in RUNTIME_SOURCES:
        shutil.copy2(source_dir / name, destination.parent / name)
    for name in NATIVE_HOOK_LICENSES:
        shutil.copy2(binary.parent / name, notices / name)
    validate_native_hook_payload(destination, source_dir=destination.parent, license_dir=notices,
                                 build_manifest_path=build_manifest)


def native_hook_component(files, *, released=False):
    from native_pc.native_hook_source import NATIVE_HOOK_PACKAGE_PATH, NATIVE_HOOK_BUILD_MANIFEST_PATH
    from native_pc.probe_native_pc import KNOWN_BUILDS
    return dict(provider='arkdps-native-hooks', protocol=1, scope='official-pc' if released else 'official-pc-startup-test',
                path=NATIVE_HOOK_PACKAGE_PATH, build_manifest_path=NATIVE_HOOK_BUILD_MANIFEST_PATH,
                sha256=files[NATIVE_HOOK_PACKAGE_PATH],
                build_manifest_sha256=files[NATIVE_HOOK_BUILD_MANIFEST_PATH],
                default_platform='native_pc', default_backend='native', supported_source_builds=list(KNOWN_BUILDS),
                experimental=not released, performance_verified=released, autochess_handoff_verified=released)


def native_hooks_readme(version):
    return (
        f'ArkDPS {version} · PC ArkPie 事件启动测试包（实验候选）\n\n'
        '本包仅面向已适配构建的 Windows PC，不包含 Frida 客户端、Gadget 或服务端。\n'
        '无需安装 Python 或 .NET；不支持 MuMu、合作事件文件或自动更新。\n'
        '单独解压到新的可写目录，不要覆盖标准版；不要复制或继承旧 runtime、配置或战绩。\n'
        '先启动 ArkDPS.Desktop.exe，允许正常的管理员权限提示，在设置中填写游戏目录。\n'
        '确认界面显示「ArkPie 监听已就绪，请启动游戏」后，再正常启动游戏。\n'
        '此时仅启动监听已建立，尚未预载 DLL 或连接采集；不要等待 DLL ready 才启动游戏。\n'
        '若游戏已开，请先正常退出，再按此顺序重开。\n'
        '退出测试时先正常退出游戏，再退出 ArkDPS；不要强制结束相关进程。\n'
        '这是未签名的实验包，Windows 可能提示未知发布者；不确定时请停止并反馈。\n'
        '不要关闭或修改杀毒、防火墙、反作弊等安全防护，不保证其它机器或游戏构建兼容。\n\n'
        '已知限制：这是启动测试，不是正式上线或完整兼容验收。性能尚未验收；\n'
        '卫戍多阶段交接仍可能出现事件覆盖 gap / partial，不能把显示数值当完整逐笔账。\n'
        '不要据此宣称性能、卫戍完整性或所有战斗机制均已通过。性能体感仅作反馈，不是测量结论。\n\n'
        '手动反馈：Windows 版本、PC 客户端版本/文件校验结果、是否先启动 ArkDPS、\n'
        '监听/初始化状态、是否进入大厅、客户端错误码/崩溃、正常退出结果。\n'
        '启动失败可保留游戏错误截图及 collector/runtime/wpf-startup.log、\n'
        'collector/runtime/wpf-collector*.log，并在脱敏后手动反馈；本包不自动上传。\n'
        '不要发送或打包 native-hook-sessions/*.json 等会含 token 的会话配置，也不要发送认证信息。\n'
    )


def _no_frida_import_guard():
    return (
        'import importlib.abc, importlib.util, importlib.metadata, sys\n'
        'assert importlib.util.find_spec("frida") is None\n'
        'try:\n'
        '    importlib.metadata.version("frida")\n'
        'except importlib.metadata.PackageNotFoundError:\n'
        '    pass\n'
        'else:\n'
        '    raise RuntimeError("Frida distribution found")\n'
        'class NoFrida(importlib.abc.MetaPathFinder):\n'
        '    def find_spec(self, fullname, path=None, target=None):\n'
        '        if fullname.split(".")[0] in ("frida", "_frida") or fullname == "native_pc.frida_runtime":\n'
        '            raise RuntimeError("Frida import attempted")\n'
        'sys.meta_path.insert(0, NoFrida())\n'
    )


def verify_native_hooks_candidate(bundle, inputs, environment):
    """Embedded-Python-only smoke check; no GUI, DLL mapping or game access."""
    python = str(bundle / 'python/python.exe')
    collector = bundle / 'collector'
    guard = _no_frida_import_guard()
    script = (guard + 'import app, desktop_launcher, desktop_bridge, native_pc_collector\n'
              'from pathlib import Path\n'
              'from native_pc.native_hook_source import native_hook_binary\n'
              'import native_pc.native_hook_runtime\n'
              'native_hook_binary(Path.cwd() / "collector")\n'
              'assert "tkinter" not in sys.modules\n')
    subprocess.run([python, '-I', '-B', '-c', script], cwd=bundle, env=environment,
                   check=True, capture_output=True, encoding='utf-8', timeout=30)
    commands = ('hello', 'snapshot', 'quit')
    request = ''.join(json.dumps(dict(version=1, id=index, op=op)) + '\n'
                      for index, op in enumerate(commands, start=1))
    worker_script = (guard + 'import runpy\n'
                     'sys.argv = sys.argv[1:]\n'
                     'runpy.run_path(sys.argv[0], run_name="__main__")\n')
    worker = subprocess.run([
        python, '-I', '-B', '-X', 'utf8', '-c', worker_script, str(collector / 'desktop_bridge.py'),
        '--offline', '--state-dir', str(inputs / 'native-hooks-offline-state'),
    ], cwd=bundle, env=environment, input=request, check=True, capture_output=True, encoding='utf-8', timeout=30)
    replies = [json.loads(line) for line in worker.stdout.splitlines()]
    if (len(replies) != len(commands) or any(not isinstance(reply, dict) or reply.get('ok') is not True
            or reply.get('kind') != kind for reply, kind in zip(replies, ('hello', 'snapshot', 'bye')))):
        raise RuntimeError('Unexpected native-hooks startup-test worker replies')
    hello = replies[0]['result']
    effective = replies[1]['state']['effective_connection_config']
    if ('frida' not in hello or hello['frida'] is not None
            or not isinstance(hello.get('native_hook_candidate'), dict)
            or hello['native_hook_candidate'].get('provider') != 'arkdps-native-hooks'
            or effective.get('platform') != 'native_pc' or effective.get('native_pc_event_backend') != 'native'
            or 'native_pc_event_feed' not in effective or effective['native_pc_event_feed'] not in (None, '')):
        raise RuntimeError('Native-hooks startup-test worker lost its no-Frida package defaults')
    if (collector / 'runtime').exists() or (bundle / '.arkdps-update').exists():
        raise RuntimeError('Offline startup check wrote to product state or update storage')
    return dict(scope='embedded-python-offline-startup-only', imports=True, manifest_validated=True,
                worker_commands=list(commands), frida=None, frida_import_blocked=True,
                effective_platform='native_pc', effective_backend='native', game_connected=False,
                desktop_started=False, performance_verified=False, autochess_handoff_verified=False)


def verify_native_hooks_release(bundle, inputs, environment):
    """Validate both shipped backends without attaching to a process or starting WPF."""
    python = str(bundle / 'python/python.exe')
    collector = bundle / 'collector'
    script = ('import frida, sys\n'
              'from pathlib import Path\n'
              'from native_pc.native_hook_source import native_hook_binary\n'
              'native_hook_binary(Path.cwd() / "collector")\n'
              'assert "tkinter" not in sys.modules\n')
    subprocess.run([python, '-I', '-B', '-c', script], cwd=bundle, env=environment,
                   check=True, capture_output=True, encoding='utf-8', timeout=30)
    commands = ('hello', 'snapshot', 'quit')
    request = ''.join(json.dumps(dict(version=1, id=index, op=op)) + '\n'
                      for index, op in enumerate(commands, start=1))
    worker = subprocess.run([
        python, '-I', '-B', '-X', 'utf8', str(collector / 'desktop_bridge.py'),
        '--offline', '--state-dir', str(inputs / 'native-hooks-release-offline-state'),
        '--platform', 'native_pc', '--native-pc-event-backend', 'native',
    ], cwd=bundle, env=environment, input=request, check=True, capture_output=True, encoding='utf-8', timeout=30)
    replies = [json.loads(line) for line in worker.stdout.splitlines()]
    if (len(replies) != len(commands) or any(not isinstance(reply, dict) or reply.get('ok') is not True
            or reply.get('kind') != kind for reply, kind in zip(replies, ('hello', 'snapshot', 'bye')))):
        raise RuntimeError('Unexpected native backend release worker replies')
    hello = replies[0]['result']
    effective = replies[1]['state']['effective_connection_config']
    if (hello.get('frida') != FRIDA_VERSION or hello.get('native_hook_candidate') is not None
            or effective.get('platform') != 'native_pc' or effective.get('native_pc_event_backend') != 'native'):
        raise RuntimeError('Standard package lost one of its selectable PC event backends')
    if (collector / 'runtime').exists() or (bundle / '.arkdps-update').exists():
        raise RuntimeError('Offline backend check wrote to product state or update storage')
    return dict(scope='embedded-python-offline-startup-only', imports=True, manifest_validated=True,
                worker_commands=list(commands), frida=FRIDA_VERSION,
                effective_platform='native_pc', effective_backend='native', game_connected=False,
                desktop_started=False)


def verify_internal_candidate(bundle, inputs, environment, *, cases=None):
    python = str(bundle / 'python/python.exe')
    collector = bundle / 'collector'
    executable = str(bundle / 'ArkDPS.Desktop.exe')
    if subprocess.run([executable, '--offline', '--verify-internal'], cwd=bundle, env=environment,
                      capture_output=True, timeout=10).returncode != 1:
        raise RuntimeError('Internal desktop startup must require explicit source selection')
    if subprocess.run([python, '-I', '-B', str(collector / 'app.py'), '--capture-seconds', '1'],
                      cwd=bundle, env=environment, capture_output=True, timeout=10).returncode != 2:
        raise RuntimeError('Internal CLI startup must require explicit source selection')
    if cases is None:
        cases = {'normal': ('normal', 1, 0), 'autochess': ('autochess', 1, 4)}
    receipts = {}
    for name, (selector, expected_archives, expected_players) in cases.items():
        cli_dir = inputs / f'internal-cli-{name}'
        cli = subprocess.run([python, '-I', '-B', str(collector / 'app.py'),
            '--internal-capture', str(selector), '--state-dir', str(cli_dir)],
            cwd=bundle, env=environment, check=True, capture_output=True, encoding='utf-8', timeout=30)
        report = json.loads(cli.stdout)
        if (report.get('status') != 'ok' or report.get('archives') != expected_archives
                or report.get('game_connected') is not False):
            raise RuntimeError(f'Unexpected controlled CLI result for {name}')
        (cli_dir / 'cli-receipt.json').write_text(cli.stdout, encoding='utf-8')
        desktop_dir = inputs / f'internal-desktop-{name}'
        subprocess.run([executable, '--offline', '--internal-capture', str(selector),
                        '--state-dir', str(desktop_dir), '--verify-internal'],
                       cwd=bundle, env=environment, check=True, capture_output=True, timeout=45)
        desktop = json.loads((desktop_dir / 'wpf-internal-capture.json').read_text('utf-8'))
        cli_archive = internal_archives(cli_dir, report['battle_ids'])
        desktop_archive = internal_archives(desktop_dir, desktop['battle_ids'])
        if (cli_archive != desktop_archive or desktop.get('scenario') != report.get('scenario')
                or desktop.get('event_status') != 'finished' or desktop.get('history_count') != expected_archives
                or desktop.get('players') != expected_players):
            raise RuntimeError(f'Controlled CLI/WPF archived statistics or projection differ for {name}')
        screenshots = ('wpf-internal-capture.png', 'wpf-internal-history.png', 'wpf-internal-settings.png')
        receipts[name] = dict(
            fixture_sha256=report['fixture_sha256'],
            statistics_sha256=hashlib.sha256('\n'.join(cli_archive).encode('utf-8')).hexdigest(),
            cli=dict(battle_ids=report['battle_ids'], archives=report['archives'], samples=report['samples'],
                     game_connected=report['game_connected']),
            wpf=dict(desktop, screenshots={filename: digest(desktop_dir / filename) for filename in screenshots}),
        )
    if (collector / 'runtime').exists() or (bundle / '.arkdps-update').exists():
        raise RuntimeError('Internal capture touched the standard runtime or update directory')
    return receipts


def internal_archives(state_dir, battle_ids):
    records = [json.loads(path.read_text('utf-8')) for path in (state_dir / 'history').glob('*.json')]
    if (sorted(record['battle_id'] for record in records) != sorted(battle_ids)
            or not any(record['event_status'] == 'finished' for record in records)):
        raise RuntimeError(f'Invalid controlled battle archive in {state_dir}')
    # Each CLI/WPF run deliberately gets independent session identifiers.
    return sorted(json.dumps(
        dict(event_status=record['event_status'], statistics={
            name: value for name, value in record['statistics'].items() if name not in {'session', 'adapter_session'}
        }),
        sort_keys=True, separators=(',', ':'), allow_nan=False,
    ) for record in records)


def verify_cooperative_candidate(bundle, inputs, environment):
    python = str(bundle / 'python/python.exe')
    collector = bundle / 'collector'
    cli = subprocess.run([python, '-I', '-B', str(collector / 'app.py'), '--help'], cwd=bundle,
                         env=environment, check=True, capture_output=True, encoding='utf-8', timeout=10)
    if any(flag not in cli.stdout for flag in ('--native-pc-event-backend', '--native-pc-event-feed')):
        raise RuntimeError('Cooperative candidate CLI is missing its backend options')
    identity_check = (
        'from pathlib import Path; from native_pc.internal_entry import live_native_host; '
        'from native_pc.internal_event_source import InternalEventProbe\n'
        'probe = InternalEventProbe(live_native_host(Path.cwd() / "collector"))\n'
        'try:\n'
        '    probe.start()\n'
        '    import json; print(json.dumps(probe.probe_modes["probe_source"]))\n'
        'finally:\n'
        '    probe.stop()'
    )
    host = subprocess.run([python, '-I', '-B', '-c', identity_check], cwd=bundle, env=environment,
                          check=True, capture_output=True, encoding='utf-8', timeout=20)
    identity = json.loads(host.stdout)
    feed = inputs / 'cooperative-smoke.jsonl'
    mark = dict(sequence=0, frame=0, game_time=0, epoch=0, active=0, native_frame=0,
                native_fixed_time='0', native_generation=1, native_process_pid=42,
                native_stage_id='owned-smoke-stage',
                native_identity=dict(controller='0x10000', native='0x20000', logger='0x30000',
                                     stats='0x40000', level='0x50000'))
    subprocess.run([
        python, '-I', '-B', '-m', 'native_pc.live_event_bridge', '--output', str(feed),
        '--producer', 'owned-packaging-smoke', '--producer-version', '1', '--pid', '42',
        '--creation-filetime', '132245567890001234', '--source-build', 'owned-smoke-build',
    ], cwd=bundle, env=environment, input=json.dumps(dict(mark=mark, events=[], terminal=True)) + '\n',
        check=True, capture_output=True, encoding='utf-8', timeout=10)
    header, closing = [json.loads(line) for line in feed.read_bytes().splitlines()]
    if (header != dict(format='arkdps-pc-events', version=1, producer='owned-packaging-smoke',
                       producer_version=1, process_instance=[42, 132245567890001234], source_build='owned-smoke-build')
            or closing != dict(mark=mark, events=[], terminal_mark=mark)):
        raise RuntimeError('Packaged cooperative bridge did not publish the original fenced record')
    state_dir = inputs / 'cooperative-smoke-state'
    commands = ('hello', 'snapshot', 'quit')
    request = ''.join(json.dumps(dict(version=1, id=index, op=op)) + '\n'
                      for index, op in enumerate(commands, start=1))
    worker = subprocess.run([
        python, '-I', '-B', '-X', 'utf8', str(collector / 'desktop_bridge.py'),
        '--offline', '--platform', 'native_pc', '--state-dir', str(state_dir),
        '--native-pc-event-backend', 'native', '--native-pc-event-feed', str(feed),
    ], cwd=bundle, env=environment, input=request, check=True, capture_output=True, encoding='utf-8', timeout=30)
    replies = [json.loads(line) for line in worker.stdout.splitlines()]
    if [(reply['kind'], reply['ok']) for reply in replies] != [('hello', True), ('snapshot', True), ('bye', True)]:
        raise RuntimeError('Unexpected cooperative candidate worker replies')
    state = replies[1]['state']
    effective = state['effective_connection_config']
    saved = state['connection_config']
    if (replies[0]['result']['frida'] != FRIDA_VERSION or saved['native_pc_event_backend'] != 'frida'
            or effective['native_pc_event_backend'] != 'native' or Path(effective['native_pc_event_feed']) != feed):
        raise RuntimeError('Cooperative candidate lost backend selection or changed the Frida default')
    return dict(runtime_source=identity, frida=FRIDA_VERSION, saved_backend='frida',
                effective_backend='native', packaged_cli_options=True, packaged_cooperative_producer=True,
                game_connected=False)


def build(output, *, version=DEFAULT_VERSION, python_zip=None, frida_wheel=None,
          frida_gadget_archive=None, frida_server=None, frida_server_license=None, with_events=True,
          internal_backend=False, with_native_events=False, internal_event_cache=None,
          with_native_hooks=False, native_hook_dll=None, native_hook_cache=None):
    if not RELEASE_VERSION.fullmatch(version) or any(
            int(part) > 2147483647 for part in re.findall(r'[0-9]+', version)):
        raise ValueError('Use a version such as 1.0.0 or 1.0.0-rc.1')
    head = candidate_head(ROOT)
    if with_native_hooks:
        if with_events or internal_backend or with_native_events:
            raise ValueError('Native-hooks startup-test must be isolated without Frida or another event candidate')
        if native_hook_dll is None:
            raise ValueError('Native-hooks startup-test requires --native-hook-dll (an existing validated DLL)')
        if any(value is not None for value in (frida_wheel, frida_gadget_archive, frida_server, frida_server_license)):
            raise ValueError('Native-hooks startup-test does not accept Frida component inputs')
    elif native_hook_dll is not None and (not with_events or internal_backend or with_native_events):
        raise ValueError('--native-hook-dll requires the standard event package or --with-native-hooks')
    if internal_backend and with_events:
        raise ValueError('Internal event candidate must be built without Frida components')
    if with_native_events and not with_events:
        raise ValueError('The PC backend candidate requires both native and Frida components')
    with_host = internal_backend or with_native_events
    released_hooks = with_events and not with_host
    native_payload = with_native_hooks or released_hooks
    output.mkdir(parents=True, exist_ok=True)
    inputs = Path(tempfile.mkdtemp(prefix='inputs-', dir=output))
    python_zip = pinned_input(python_zip, destination=inputs / 'python.zip',
                              url=PYTHON_URL, sha256=PYTHON_SHA256)
    if released_hooks and native_hook_dll is None:
        from native_pc.native_event_runtime import build as hook_build
        native_hook_dll = hook_build.build(
            inputs / 'native-hooks' / 'ArkDPS.NativeEvents.dll', cache=native_hook_cache)
    if with_host:
        from native_pc.internal_event_host import build as host_build
        from native_pc.internal_event_source import PROTOCOL_VERSION, SOURCE_PROVIDER, SOURCE_VERSION
        internal_event_host = host_build.build(
            inputs / 'host' / HOST_NAME, cache=internal_event_cache,
        )
    if with_events:
        if frida_wheel is None:
            subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                '--no-deps', '--platform', 'win_amd64', '--python-version', '312',
                '--implementation', 'cp', '--abi', 'abi3', '--dest', str(inputs),
                'frida==' + FRIDA_VERSION], cwd=ROOT, check=True)
            frida_wheel, = inputs.glob('frida-*.whl')
        check(frida_wheel, FRIDA_SHA256)
        frida_server = pinned_input(frida_server, destination=inputs / FRIDA_SERVER_NAME,
                                    url=FRIDA_SERVER_URL, sha256=FRIDA_SERVER_SHA256)
        frida_server_license = pinned_input(frida_server_license, destination=inputs / FRIDA_SERVER_LICENSE_NAME,
                                            url=FRIDA_SERVER_LICENSE_URL, sha256=FRIDA_SERVER_LICENSE_SHA256)
    bundle = Path(tempfile.mkdtemp(prefix='ArkDPS-win-x64-', dir=output))
    build_artifacts = inputs / 'dotnet'
    dotnet_executable = os.environ.get('ARKDPS_DOTNET') or shutil.which('dotnet')
    if not dotnet_executable:
        raise RuntimeError('.NET 10 SDK required; set ARKDPS_DOTNET for a task-local SDK')
    desktop_variant = []
    if internal_backend:
        desktop_variant.append('-p:ArkDpsInternalEventCandidate=true')
    elif with_native_events:
        desktop_variant.append('-p:ArkDpsCooperativeEventCandidate=true')
    elif with_native_hooks:
        desktop_variant.append('-p:ArkDpsNativeHookCandidate=true')
    subprocess.run([dotnet_executable, 'publish', str(ROOT / 'desktop/ArkDPS.Desktop/ArkDPS.Desktop.csproj'),
        '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true', '--nologo',
        '-p:ContinuousIntegrationBuild=true', '-p:PublishSingleFile=true',
        '-p:IncludeNativeLibrariesForSelfExtract=true', '-p:PublishTrimmed=false',
        '-p:DebugType=none', *desktop_variant, '--artifacts-path', str(build_artifacts), '-o', str(bundle)],
        cwd=ROOT, check=True)
    verify_executable_manifest(bundle / 'ArkDPS.Desktop.exe', internal_backend=internal_backend)
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
    for name in ('assets', 'vendor', 'native_pc'):
        # The separately built ArkFramework DLL belongs to its host, outside the Python worker.
        ignored = ('__pycache__', '*.pyc')
        if name == 'native_pc':
            ignored += ('arkframework', 'internal_event_host', 'native_event_runtime')
            if not native_payload:
                ignored += ('native_hook_source.py', 'native_hook_runtime.py')
            if with_native_hooks:
                ignored += ('frida_runtime.py',)
            if not internal_backend:
                ignored += ('internal_capture.py',)
            if not with_host and not native_payload:
                ignored += ('internal_event_source.py',)
            if not with_native_events:
                ignored += ('live_event_bridge.py', 'live_event_producer.py')
        if name == 'vendor':
            # Only the full variant receives a verified Gadget. A local source
            # cache must never leak it into a snapshot-only archive.
            ignored += ('frida',)
        shutil.copytree(ROOT / name, collector / name, ignore=shutil.ignore_patterns(*ignored))
    if with_host:
        host_path = bundle / HOST_PATH
        host_path.parent.mkdir()
        shutil.copy2(internal_event_host, host_path)
        shutil.copy2(ROOT / 'app.py', collector / 'app.py')
    if with_native_events or native_payload:
        shutil.copy2(ROOT / 'desktop_launcher.py', collector / 'desktop_launcher.py')
    if native_payload:
        shutil.copy2(ROOT / 'app.py', collector / 'app.py')
        shutil.copy2(ROOT / 'release_identity.py', collector / 'release_identity.py')
    if with_events:
        component_dir = collector / 'vendor' / 'frida'
        stage_gadget(component_dir / FRIDA_VERSION, source_archive=frida_gadget_archive)
        shutil.copy2(ROOT / 'vendor/frida/COPYING', component_dir / 'COPYING')
    notices = bundle / 'licenses'
    notices.mkdir()
    for name in ('LICENSE', 'THIRD_PARTY.md'):
        shutil.copy2(ROOT / name, notices / name)
    if native_payload:
        stage_native_hook_payload(native_hook_dll, collector, notices)
    if with_host:
        license_file = internal_event_host.with_name('nlohmann-json-LICENSE.MIT')
        shutil.copy2(license_file, notices / license_file.name)
    if with_events:
        server_payload = bundle / FRIDA_SERVER_PATH
        server_payload.parent.mkdir()
        shutil.copy2(frida_server, server_payload)
        shutil.copy2(frida_server_license, notices / FRIDA_SERVER_LICENSE_NAME)
    if (bundle / 'Assets').is_dir():
        (bundle / 'Assets').rename(notices / 'Assets')
    dotnet = Path(dotnet_executable).parent
    for name in ('LICENSE.txt', 'ThirdPartyNotices.txt'):
        if not (dotnet/name).is_file():
            raise FileNotFoundError('Required .NET redistribution notice missing: '+name)
        shutil.copy2(dotnet/name, notices/('dotnet-'+name))
    # Python LICENSE.txt and the Frida client's dist-info/licenses/COPYING stay in their archives.
    metadata = bundle / 'metadata'
    metadata.mkdir()
    pc_instructions = (
        'PC 请填写 Arknights.exe 所在目录。逐次事件需要先启动 ArkDPS，再启动游戏。\n'
        'PC 事件组件随游戏保留到正常退出，仅通过本机认证端点连接。关闭观察者可只采累计。\n'
        if with_events else
        '本包仅包含累计快照。PC 请填写 Arknights.exe 所在目录，并关闭「启用观察者」。\n')
    if released_hooks:
        pc_instructions += ('PC 事件后端可选择 Frida 或 ArkPie；若遇“检测到环境存在异常”报错，选择 ArkPie。\n'
                            '切换后端后应用设置，等监听就绪，再正常重启游戏。\n')
    if with_native_events:
        pc_instructions = (
            'PC 请填写 Arknights.exe 所在目录，然后选择「PC 事件后端」。\n'
            'Frida：先启动 ArkDPS，按连接提示启动或重启游戏。\n'
            'ArkPie：指定合作来源写入的 JSONL 文件。事件不可用时继续显示累计快照。\n'
            '合作来源写入工具：.\\python\\python.exe -I -B -m native_pc.live_event_bridge --help\n'
            '无界面采集参数：.\\python\\python.exe -I -B .\\collector\\app.py --help\n'
        )
    update_instructions = (
        '程序启动后会在后台检查并下载更新。也可从设置页手动检查。\n'
        '下载完成后，在设置页选择「重启并更新」。软件会保留设置和战绩。\n\n'
    ) if with_events and not with_native_events else ''
    observer_instructions = (
        '开启观察者时，ArkDPS 会为选中的受支持 MuMu 实例准备并启动包内 Frida 服务。\n'
        if with_events else '此包仅提供伤害和治疗快照。\n'
    )
    readme = (
        f'ArkDPS {version} · Windows x64\n\n'
        '解压到可写目录，双击 ArkDPS.Desktop.exe。无需安装 Python 或 .NET。\n'
        'ArkDPS 始终申请管理员权限，请允许 Windows 提升提示；取消提示不会启动程序。\n'
        '在设置中选择 MuMu 或 PC（官服与 B 服）。MuMu 需要开启 Root，并使用已适配的安卓游戏 2.7.91。\n'
        f'{pc_instructions}'
        f'{observer_instructions}'
        '「卫戍队友DPS」仅支持具有多战场视野的 PC 客户端，适用于官服和 B 服。MuMu 及其它端的防御只统计本家。\n'
        'ADB 由模拟器提供。\n\n'
        f'{update_instructions}'
        '从尚未要求管理员权限的旧版首次升级，请退出旧版并手动下载完整 ZIP，按下面步骤迁移一次。\n'
        '首次从旧版迁移或需要手动升级时，从托盘菜单选「退出 ArkDPS」，将新版 ZIP 解压到新目录。\n'
        '如果旧版有 collector/runtime，将整个 runtime 目录复制到新版的 collector 后再启动。\n'
        '保留旧目录与备份。需要手动回退时，先退出新版，再启动旧目录中的程序。\n')
    if internal_backend:
        readme = (
            f'ArkDPS {version} · ArkPie 事件后端内部候选\n\n'
            '本包使用自有测试数据验证普通关、卫戍和战绩。\n'
            '从包根目录运行以下 PowerShell 命令，可将 normal 换成 autochess。\n\n'
            '桌面\n'
            '.\\ArkDPS.Desktop.exe --offline --internal-capture normal --state-dir "$PWD\\internal-desktop"\n\n'
            '无界面\n'
            '.\\python\\python.exe -I -B .\\collector\\app.py --internal-capture normal '
            '--state-dir .\\internal-cli\n')
    if with_native_hooks:
        readme = native_hooks_readme(version)
    (bundle / 'README.txt').write_text(readme, encoding='utf-8-sig')
    if {entry.name for entry in bundle.iterdir()} != PACKAGE_ROOT:
        raise RuntimeError('Unexpected files in portable package root')
    manifest = dict(format=2, version=version, protocol=1, target_framework='net10.0-windows',
        dotnet_sdk=subprocess.check_output([dotnet_executable,'--version'],cwd=ROOT,text=True).strip(),
        dotnet_runtime=json.loads(runtime_config.read_text('utf-8')), head=head,
        collector_sha256=digest(collector/'desktop_bridge.py'), python='3.12.10',
        python_source=PYTHON_URL, python_archive_sha256=PYTHON_SHA256,
        frida=FRIDA_VERSION if with_events else None, frida_wheel_sha256=FRIDA_SHA256 if with_events else None,
        native_pc_gadget=gadget_metadata() if with_events else None,
        files={str(p.relative_to(bundle)).replace('\\','/'):digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()})
    if with_events:
        if not with_native_events:
            manifest['update_protocol'] = UPDATE_PROTOCOL
        manifest['frida_server'] = dict(path=FRIDA_SERVER_PATH, source=FRIDA_SERVER_URL, sha256=FRIDA_SERVER_SHA256)
    if with_host:
        backend_key = 'internal_event_backend' if internal_backend else 'native_pc_event_backend'
        manifest[backend_key] = dict(
            protocol=PROTOCOL_VERSION, provider=SOURCE_PROVIDER, source_version=SOURCE_VERSION,
            scope='controlled-replay' if internal_backend else 'cooperative-live',
            path=HOST_PATH, sha256=manifest['files'][HOST_PATH],
            cpp_source_sha256=digest(host_build.SOURCE / 'main.cpp'), json_header_sha256=host_build.HEADER[1],
        )
    if native_payload:
        manifest['native_pc_native_hooks'] = native_hook_component(manifest['files'], released=released_hooks)
        if with_native_hooks:
            assert_no_frida_payload(manifest['files'])
    assert_no_tk_files(manifest['files'])
    (metadata/'release.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    # Smoke import under embedded Python with a poisoned system Python environment.
    environment=dict(os.environ, PYTHONHOME='Z:/not-a-python', PYTHONPATH='Z:/not-a-python', DOTNET_MULTILEVEL_LOOKUP='0')
    script=('import desktop_bridge, native_pc_collector, sys; '
            'assert "tkinter" not in sys.modules; print(sys.version); print(sys.executable)')
    if with_native_hooks:
        script = _no_frida_import_guard() + script
    script_args = []
    if with_events:
        script += (f'; import frida, lzma; assert frida.__version__ == {FRIDA_VERSION!r}; print(frida.__version__)'
                   '\nwith lzma.open(sys.argv[1], "rb") as server:\n    assert server.read(4) == b"\\x7fELF"')
        script_args.append(str(server_payload))
    subprocess.run([str(bundle/'python/python.exe'), '-I', '-B', '-c', script, *script_args],
                   cwd=ROOT, env=environment, check=True)
    archive = output / archive_name(version, with_events, internal_backend=internal_backend,
                                   with_native_events=with_native_events, with_native_hooks=with_native_hooks)
    staged_archive = inputs / archive.name
    with zipfile.ZipFile(staged_archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as stream:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                stream.write(path,str(Path('ArkDPS')/path.relative_to(bundle)))
    sha256 = digest(staged_archive)
    if with_host or native_payload:
        extracted = inputs / 'clean-extract'
        unpack(staged_archive, extracted)
        clean_bundle = extracted / 'ArkDPS'
        clean_manifest = clean_bundle / 'metadata/release.json'
        files = {path.relative_to(clean_bundle).as_posix(): digest(path)
                 for path in clean_bundle.rglob('*') if path.is_file() and path != clean_manifest}
        if json.loads(clean_manifest.read_text('utf-8')) != manifest or files != manifest['files']:
            raise RuntimeError('PC event candidate ZIP does not match its release manifest')
        if native_payload:
            backend_key = 'native_pc_native_hooks'
            if with_native_hooks:
                assert_no_frida_payload(files)
                receipts = {'startup': verify_native_hooks_candidate(clean_bundle, inputs, environment)}
            else:
                receipts = {'startup': verify_native_hooks_release(clean_bundle, inputs, environment)}
        else:
            receipts = (verify_internal_candidate(clean_bundle, inputs, environment) if internal_backend else
                        {'cooperative': verify_cooperative_candidate(clean_bundle, inputs, environment)})
        evidence = dict(head=head, archive=archive.name, sha256=sha256,
                        backend=manifest[backend_key], scenarios=receipts,
                        raw_evidence_directory=inputs.name)
        (output / (archive.name + '.acceptance.json')).write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
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
    parser.add_argument('--frida-gadget-archive',type=Path, help='verified PC Gadget XZ input')
    parser.add_argument('--frida-server', type=Path, help='reuse the pinned Android server .xz')
    parser.add_argument('--frida-server-license', type=Path, help='reuse the pinned server COPYING text')
    variants = parser.add_mutually_exclusive_group()
    variants.add_argument('--without-events',action='store_true',help='explicit snapshot-only variant, never silent downgrade')
    variants.add_argument('--internal-events', action='store_true', help='build the separate controlled-data candidate')
    variants.add_argument('--with-native-events', action='store_true', help='build the candidate with both PC event backends')
    variants.add_argument('--with-native-hooks', action='store_true', help='build the isolated official-PC no-Frida startup-test candidate')
    parser.add_argument('--native-hook-dll', type=Path, help='existing native-hooks DLL with its build manifest and redistribution licenses')
    parser.add_argument('--native-hook-cache', type=Path, help='reuse pinned native hook build dependencies')
    parser.add_argument('--internal-event-cache', type=Path, help='reuse pinned nlohmann/json build dependencies')
    args=parser.parse_args()
    build(args.output.resolve(), version=args.version, python_zip=args.python_zip,
          frida_wheel=args.frida_wheel, frida_gadget_archive=args.frida_gadget_archive,
          frida_server=args.frida_server,
          frida_server_license=args.frida_server_license,
          with_events=not args.without_events and not args.internal_events and not args.with_native_hooks,
          internal_backend=args.internal_events, with_native_events=args.with_native_events,
          internal_event_cache=args.internal_event_cache,
          with_native_hooks=args.with_native_hooks, native_hook_dll=args.native_hook_dll,
          native_hook_cache=args.native_hook_cache)


if __name__=='__main__':
    main()
