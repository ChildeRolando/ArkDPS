"""Small, shareable context for ArkDPS log headers."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile

try:
    import winreg
except ImportError:
    winreg = None


ROOT = Path(__file__).resolve().parent
ENVIRONMENT_FILENAME = 'log-environment.json'
_LABEL = re.compile(r'[\w .,:()+@\-]{1,120}')
_VERSION = re.compile(r'[A-Za-z0-9_.:+\-]{1,100}')
_DESKTOP_RUN_ID = re.compile(r'[a-f0-9]{32}')
_PLATFORMS = ('native_pc', 'mumu')


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _token(value, pattern=_VERSION):
    if isinstance(value, str):
        value = value.strip()
        if pattern.fullmatch(value):
            return value
    return None


def _boolean(mapping, name, default):
    value = mapping.get(name)
    return value if type(value) is bool else default


def _json_file(path):
    try:
        # Headers must stay cheap even when a settings file is damaged.
        with path.open('rb') as source:
            raw = source.read(262145)
        if len(raw) <= 262144:
            data = json.loads(raw.decode('utf-8-sig'))
            if isinstance(data, dict):
                return data, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, RecursionError):
        pass
    return {}, None


def _registry_value(path, name):
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            pass
    return None


def _memory_mb():
    if sys.platform == 'win32':
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong) for name in ('total', 'available', 'total_page_file',
                    'available_page_file', 'total_virtual', 'available_virtual', 'extended_virtual')]

        status = MemoryStatus(length=ctypes.sizeof(MemoryStatus))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total // (1024 * 1024)
    elif hasattr(os, 'sysconf'):
        try:
            pages, size = os.sysconf('SC_PHYS_PAGES'), os.sysconf('SC_PAGE_SIZE')
            if pages > 0 and size > 0:
                return pages * size // (1024 * 1024)
        except (OSError, ValueError):
            pass
    return None


def _gpu_names():
    if sys.platform != 'win32':
        return []

    class DisplayDevice(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('name', wintypes.WCHAR * 32),
                    ('label', wintypes.WCHAR * 128), ('flags', wintypes.DWORD),
                    ('device_id', wintypes.WCHAR * 128), ('device_key', wintypes.WCHAR * 128)]

    names = []
    for index in range(8):
        device = DisplayDevice(size=ctypes.sizeof(DisplayDevice))
        if not ctypes.windll.user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        label = _token(device.label, _LABEL)
        if label and label not in names:
            names.append(label)
    return names


@lru_cache(maxsize=1)
def _system_context():
    windows_key = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    cpu_key = r'HARDWARE\DESCRIPTION\System\CentralProcessor\0'
    vc_runtime_key = r'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64'
    windows = sys.getwindowsversion() if sys.platform == 'win32' else None
    build = None
    if windows is not None:
        revision = _registry_value(windows_key, 'UBR')
        build = f'{windows.build}.{revision}' if type(revision) is int else str(windows.build)
    vc_installed = _registry_value(vc_runtime_key, 'Installed')
    return {
        'os': platform.system(),
        'os_version': _token(_registry_value(windows_key, 'DisplayVersion')) or platform.release(),
        'os_build': build or _token(platform.version(), _LABEL),
        'architecture': _token(os.environ.get('PROCESSOR_ARCHITEW6432') or platform.machine()),
        'cpu': _token(_registry_value(cpu_key, 'ProcessorNameString') or platform.processor(), _LABEL),
        'logical_cores': os.cpu_count(),
        'memory_mb': _memory_mb(),
        'gpus': _gpu_names(),
        'vc_runtime_x64_installed': vc_installed == 1 if type(vc_installed) is int else None,
        'vc_runtime_x64_version': _token(_registry_value(vc_runtime_key, 'Version')),
    }


@lru_cache(maxsize=4)
def _build_context(root):
    manifest, _ = _json_file(root / 'metadata' / 'release.json')
    if not manifest and root.name == 'collector':
        manifest, _ = _json_file(root.parent / 'metadata' / 'release.json')
    if not manifest:
        manifest, _ = _json_file(root / 'build-manifest.json')
    head = _token(manifest.get('head'))
    if not head and (root / '.git').exists():
        try:
            head = _token(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root,
                text=True, stderr=subprocess.DEVNULL, timeout=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        'version': _token(manifest.get('version')),
        'head': head,
        'protocol': manifest.get('protocol') if type(manifest.get('protocol')) is int else None,
        'python': platform.python_version(),
        'frida': _token(manifest.get('frida')),
    }


@lru_cache(maxsize=24)
def _executable_version(path, _mtime_ns):
    # Reading fixed PE metadata does not load or run the executable.
    version = ctypes.windll.version
    size_of = version.GetFileVersionInfoSizeW
    size_of.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    size_of.restype = wintypes.DWORD
    read = version.GetFileVersionInfoW
    read.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
    read.restype = wintypes.BOOL
    query = version.VerQueryValueW
    query.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                      ctypes.POINTER(wintypes.UINT))
    query.restype = wintypes.BOOL
    size = size_of(str(path), None)
    if not 0 < size <= 1024 * 1024:
        return None
    data = ctypes.create_string_buffer(size)
    if not read(str(path), 0, size, data):
        return None
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not query(data, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 24:
        return None
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if words[0] != 0xFEEF04BD:
        return None
    major_minor, build_revision = words[4], words[5]
    if major_minor == build_revision == 0:
        major_minor, build_revision = words[2], words[3]
    if major_minor == build_revision == 0:
        return None
    return '.'.join(str(i) for i in (major_minor >> 16, major_minor & 0xffff,
                                      build_revision >> 16, build_revision & 0xffff))


def _file_version(path):
    if sys.platform == 'win32' and path.is_absolute() and not str(path).startswith('\\\\'):
        try:
            return _executable_version(path, path.stat().st_mtime_ns)
        except (OSError, ValueError):
            pass
    return None


def _mumu_version(adb_path):
    if not isinstance(adb_path, str) or not adb_path:
        return None
    from connection import _mumu_root_for_adb
    root = _mumu_root_for_adb(adb_path)
    if root is not None:
        for executable in ('nx_main/MuMuNxMain.exe', 'shell/MuMuPlayer.exe', 'MuMuPlayer.exe'):
            version = _file_version(root / executable)
            if version:
                return version
    return None


def _settings(connection, layout):
    selected = connection.get('platform')
    theme = layout.get('theme')
    units = layout.get('number_units')
    return {
        'platform': selected if selected in _PLATFORMS else 'mumu',
        'auto_detect': _boolean(connection, 'auto_detect', True),
        'event_observation': _boolean(layout, 'event_observation', True),
        'multiplayer_enabled': _boolean(layout, 'multiplayer_enabled', False),
        'theme': theme if theme in ('lone-trail', 'people', 'babel') else 'lone-trail',
        'number_units': units if units in ('western', 'chinese') else 'western',
        'side_mode': _boolean(layout, 'side_mode', False),
        'adb_configured': bool(connection.get('adb_path')),
        'native_game_root_configured': bool(connection.get('native_game_root')),
        'manual_target_configured': bool(connection.get('serial')),
    }


def _safe_environment(mapping, *, provenance):
    return {
        key: _token(mapping.get(key))
        for key in ('platform', 'game_client_version', 'game_client_expected', 'version_status',
                    'game_executable_version', 'emulator_name', 'emulator_version',
                    'emulator_version_source', 'observed_at_utc')
    } | {'provenance': provenance}


class LogContext:
    def __init__(self, state_dir, *, root=None, connection_override=None, layout_override=None, build_head=None):
        self.state_dir = Path(state_dir)
        self.root = Path(root) if root is not None else ROOT
        self.connection_override = connection_override
        self.layout_override = layout_override
        self.build_head = _token(build_head)
        self._observed = None
        self._observed_hash = None

    def _connection(self):
        saved, sha256 = _json_file(self.state_dir / 'connection.json')
        if self.connection_override is not None:
            return {**saved, **self.connection_override}, None
        return saved, sha256

    def header(self, source, *, debug_mode=None):
        connection, sha256 = self._connection()
        layout, _ = _json_file(self.state_dir / 'layout.json')
        if self.layout_override is not None:
            layout.update(self.layout_override)
        settings = _settings(connection, layout)
        selected = settings['platform']
        environment = self._observed if sha256 == self._observed_hash else None
        if environment is None and sha256 is not None:
            cached, _ = _json_file(self.state_dir / ENVIRONMENT_FILENAME)
            entry = cached.get('environment')
            if cached.get('connection_sha256') == sha256 and isinstance(entry, dict):
                environment = _safe_environment(entry, provenance='last_observed')
        if environment is None:
            game_root = connection.get('native_game_root')
            installed_game = (_file_version(Path(game_root) / 'Arknights.exe')
                              if selected == 'native_pc' and isinstance(game_root, str) and game_root else None)
            mumu_version = _mumu_version(connection.get('adb_path')) if selected == 'mumu' else None
            environment = _safe_environment({'platform': selected, 'game_executable_version': installed_game,
                'emulator_name': 'MuMu' if mumu_version else None, 'emulator_version': mumu_version,
                'emulator_version_source': 'host_executable' if mumu_version else None},
                provenance='local_installation' if installed_game or mumu_version else 'not_observed')
        build = dict(_build_context(self.root))
        if self.build_head is not None:
            build['head'] = self.build_head
        run_id = _token(os.environ.get('ARKDPS_DESKTOP_RUN_ID'), _DESKTOP_RUN_ID)
        return {'kind': 'log_header', 'data': {'schema': 1, 'utc': _utc_now(), 'source': source,
            'desktop_run_id': run_id, 'build': build, 'system': dict(_system_context()),
            'settings': settings, 'environment': environment,
            'debug_mode': _boolean(layout, 'debug_mode', False) if debug_mode is None else bool(debug_mode)}}

    def update_environment(self, diagnostics_mapping, *, cache=False, connection_bytes=None):
        connection, sha256 = self._connection()
        if connection_bytes is not None:
            # Associate late outcomes only with the configuration used to connect.
            # The on-disk preferences can change while a connection is in flight.
            try:
                attempted = json.loads(connection_bytes.decode('utf-8-sig'))
            except (ValueError, AttributeError):
                attempted = None
            if isinstance(attempted, dict):
                connection, sha256 = attempted, hashlib.sha256(connection_bytes).hexdigest()
            else:
                sha256 = None
        diagnosis = diagnostics_mapping
        selected = diagnosis.get('platform') or connection.get('platform', 'mumu')
        if selected not in _PLATFORMS:
            selected = None
        version = diagnosis.get('version_actual')
        game_root = diagnosis.get('native_game_root') or connection.get('native_game_root')
        installed_game = (_file_version(Path(game_root) / 'Arknights.exe')
                          if selected == 'native_pc' and isinstance(game_root, str) and game_root else None)
        mumu_version = _mumu_version(diagnosis.get('adb_path') or connection.get('adb_path')) if selected == 'mumu' else None
        environment = _safe_environment({'platform': selected, 'game_client_version': version,
            'game_client_expected': diagnosis.get('version_expected'), 'game_executable_version': installed_game,
            'version_status': diagnosis.get('version_status'), 'emulator_name': 'MuMu' if mumu_version else None,
            'emulator_version': mumu_version, 'emulator_version_source': 'host_executable' if mumu_version else None,
            'observed_at_utc': _utc_now()}, provenance='observed')
        self._observed, self._observed_hash = environment, sha256
        if (cache and sha256 is not None and environment['game_client_version'] is not None
                and self._connection()[1] == sha256):
            self._save_environment(sha256, environment)
        return environment

    def _save_environment(self, sha256, environment):
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.state_dir, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'connection_sha256': sha256, 'environment': environment}, stream, ensure_ascii=False)
            temporary.replace(self.state_dir / ENVIRONMENT_FILENAME)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
