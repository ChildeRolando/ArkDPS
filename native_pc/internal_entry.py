"""Explicit, device-free entrypoints for the internal PC event candidate."""

import hashlib
import json
from pathlib import Path
import re


HOST_NAME = 'ArkDPS.NativeEventHost.exe'


def portable_manifest(root):
    path = root.parent / 'metadata' / 'release.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def internal_host(root):
    manifest = portable_manifest(root)
    if manifest is None:
        host = root / 'runtime' / 'native-pc' / 'internal-event-host' / HOST_NAME
    else:
        component = manifest.get('internal_event_backend')
        if not isinstance(component, dict) or component.get('protocol') != 1:
            raise ValueError('此便携包没有自研事件后端。')
        relative = f'collector/native_pc/internal_event_host/{HOST_NAME}'
        host = root.parent / relative
        expected = manifest.get('files', {}).get(relative)
        if not host.is_file() or not expected or hashlib.sha256(host.read_bytes()).hexdigest() != expected:
            raise ValueError('自研事件宿主与内部候选清单不一致。')
    if not host.is_file():
        raise ValueError(f'找不到自研事件宿主：{host}')
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
