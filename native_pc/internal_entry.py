"""Resolve packaged event hosts and isolate the controlled replay entrypoint."""

import hashlib
import json
from pathlib import Path
import re


HOST_NAME = 'ArkDPS.NativeEventHost.exe'
HOST_PATH = f'collector/native_pc/internal_event_host/{HOST_NAME}'
NATIVE_HOOK_CANDIDATE_KEY = 'native_pc_native_hooks'


def native_hook_package_profile(root):
    """Validate the packaged backend before deciding its launch policy."""
    manifest = portable_manifest(Path(root))
    if manifest is None or NATIVE_HOOK_CANDIDATE_KEY not in manifest:
        return None
    # The owning loader validates the exact profile, DLL, six source inputs,
    # build manifest and release file hashes. Do not invent a second trust path.
    from native_pc.native_hook_source import native_hook_binary
    native_hook_binary(root)
    profile = manifest[NATIVE_HOOK_CANDIDATE_KEY]
    if isinstance(profile, dict) and profile.get('scope') == 'official-pc':
        # The loader owns payload integrity and the release capability fields.
        # A standard package adds a selectable backend to the existing settings.
        return dict(profile)
    if (not isinstance(profile, dict) or profile.get('provider') != 'arkdps-native-hooks'
            or type(profile.get('protocol')) is not int or profile['protocol'] != 1
            or profile.get('scope') != 'official-pc-startup-test'
            or profile.get('default_platform') != 'native_pc' or profile.get('default_backend') != 'native'
            or profile.get('experimental') is not True or profile.get('performance_verified') is not False
            or profile.get('autochess_handoff_verified') is not False
            or manifest.get('internal_event_backend') or manifest.get('native_pc_event_backend')):
        raise ValueError('ArkPie 启动候选配置与便携包清单不一致。')
    return dict(profile)


def native_hook_candidate_profile(root):
    profile = native_hook_package_profile(root)
    return profile if profile and profile.get('scope') == 'official-pc-startup-test' else None


def validate_native_hook_candidate_connection(profile, config):
    if profile is not None and (config.platform != 'native_pc'
            or config.native_pc_event_backend != 'native' or config.native_pc_event_feed):
        raise ValueError('此启动测试候选仅支持 PC ArkPie 实时采集；不支持 MuMu、Frida 或合作事件文件。')


def native_hook_candidate_defaults(profile, config):
    if profile is None:
        return config
    from dataclasses import replace
    return replace(config, platform='native_pc', native_pc_event_backend='native', native_pc_event_feed=None)


def native_hook_candidate_arguments(profile, args):
    if profile is None:
        return
    if (args.platform not in (None, 'native_pc') or args.native_pc_event_backend not in (None, 'native')
            or args.native_pc_event_feed or args.frida_remote not in (None, '')
            or args.adb is not None or args.serial is not None or args.arkframework_root is not None
            or args.internal_capture is not None):
        raise ValueError('此启动测试候选仅支持 PC ArkPie 实时采集；不支持 MuMu、Frida、合作文件或内部回放。')
    args.platform, args.native_pc_event_backend, args.native_pc_event_feed = 'native_pc', 'native', None


def portable_manifest(root):
    path = root.parent / 'metadata' / 'release.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def internal_host(root):
    return _event_host(root, 'internal_event_backend', 'controlled-replay')


def live_native_host(root):
    return _event_host(root, 'native_pc_event_backend', 'cooperative-live')


def _event_host(root, manifest_key, scope):
    manifest = portable_manifest(root)
    if manifest is None:
        host = root / 'runtime' / 'native-pc' / 'internal-event-host' / HOST_NAME
    else:
        component = manifest.get(manifest_key)
        if not isinstance(component, dict):
            raise ValueError('此便携包没有 ArkPie 事件后端。')
        from native_pc.internal_event_source import PROTOCOL_VERSION, SOURCE_PROVIDER, SOURCE_VERSION

        host = root.parent / HOST_PATH
        files = manifest.get('files')
        expected = files.get(HOST_PATH) if isinstance(files, dict) else None
        if (type(component.get('protocol')) is not int or component['protocol'] != PROTOCOL_VERSION
                or type(component.get('source_version')) is not int or component['source_version'] != SOURCE_VERSION
                or component.get('provider') != SOURCE_PROVIDER or component.get('scope') != scope
                or component.get('path') != HOST_PATH or component.get('sha256') != expected
                or not isinstance(expected, str) or not host.is_file()
                or hashlib.sha256(host.read_bytes()).hexdigest() != expected):
            raise ValueError('ArkPie 事件宿主与便携包清单不一致。')
    if not host.is_file():
        raise ValueError(f'找不到 ArkPie 事件宿主：{host}')
    return host


def internal_state_dir(root, directory, *, desktop=False):
    if directory is None:
        raise ValueError('内部候选必须指定独立的 --state-dir。')
    directory = Path(directory).resolve()
    product_state = (root / 'runtime').resolve()
    if (directory == product_state or directory in product_state.parents
            or product_state in directory.parents):
        raise ValueError('内部候选的状态目录必须与标准运行目录分开。')
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / '.arkdps-internal-capture'
    if not marker.is_file():
        entries = tuple(directory.iterdir())
        if any(not (desktop and entry.is_file() and (
                entry.name == 'wpf-startup.log'
                or re.fullmatch(r'wpf-collector-\d{8}-\d{6}-\d{3}\.log', entry.name)))
                for entry in entries):
            raise ValueError('首次运行内部候选必须使用空状态目录。')
        marker.touch(exist_ok=True)
    return directory
