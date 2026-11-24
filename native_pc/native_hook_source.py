"""Experimental, process-bound PC hooks without the Frida client or Gadget.

The established JS settlement observer runs on QuickJS, with a small native
memory/metadata/MinHook adapter. Native snapshots remain the authority for
totals. This module never elevates privileges or changes a protection service.
"""
import ctypes
from copy import deepcopy
from ctypes import wintypes
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
from threading import Lock
import time

from connection import VersionStatus
from event_observer import ObserverStatus
from event_probe import (NATIVE_PC_CONTRACT, NativePcBinding, ObserverReadRetry, AssembledProbeSource,
                         assemble_probe_source, ready_probe_modes, acquire_native_terminal_tail)
from native_pc.internal_event_source import _finite_number, _reject_constant, _unique_fields
from native_pc.probe_native_pc import BUILD_ID
from native_pc import probe_native_pc as access
from native_pc.windows_dll_loader import WindowsDllLoader


DLL_NAME = 'ArkDPS.NativeEvents.dll'
PROVIDER = 'arkdps-native-hooks'
PROTOCOL = 1
RUNTIME_SOURCES = ('engine.cpp', 'engine.h', 'server.cpp', 'hooks.S', 'bridge.js', 'build.py')
NATIVE_HOOK_LICENSES = {
    'quickjs-LICENSE': '598fd7fc928e4350abce36e337ba5a1346923c5c692f5be92c3d8e29ddd7c18d',
    'minhook-LICENSE.txt': 'd5c2224982b0b95a16b098a561335fa93f3eaf4d0aa964b7843edb986df78dc8',
    'nlohmann-json-LICENSE.MIT': '86b998c792894ccb911a1cb7994f7a9652894e7a094c0b5e45be2f553f45cf14',
}
NATIVE_HOOK_DEPENDENCIES = tuple(dict(file=name, url=url, sha256=digest) for name, url, digest in (
    ('quickjs-2025-09-13.tar.xz', 'https://bellard.org/quickjs/quickjs-2025-09-13.tar.xz',
     '6f1f322aea3bb3a90858db85c9fe717013fde4df7dfcafe2f57e78f5bb4b4a0c'),
    ('minhook-1.3.4.tar.gz', 'https://codeload.github.com/TsudaKageyu/minhook/tar.gz/refs/tags/v1.3.4',
     '1aebeae4ca898330c507860acc2fca2eb335fe446a3a2b8444c3bf8b2660a14e'),
    ('json.hpp', 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp',
     '9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'),
    ('nlohmann-json-LICENSE.MIT', 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/LICENSE.MIT',
     '86b998c792894ccb911a1cb7994f7a9652894e7a094c0b5e45be2f553f45cf14'),
))
NATIVE_HOOK_PACKAGE_PATH = 'collector/native_pc/native_event_runtime/' + DLL_NAME
NATIVE_HOOK_BUILD_MANIFEST_PATH = 'collector/native_pc/native_event_runtime/ArkDPS.NativeEvents.build.json'
NATIVE_HOOK_REQUIRED_SOURCES = (
    'app.py', 'desktop_bridge.py', 'desktop_launcher.py', 'capture_cli.py',
    'collection_session.py', 'collector.py', 'battle_lifecycle.py', 'event_capture.py',
    'event_probe.py', 'event_observer.py', 'event_stream.py', 'snapshot_adapter.py',
    'stats_core.py', 'multiplayer_battle.py', 'autochess_context.py',
    'source_classification.py', 'native_pc_collector.py',
    'native_pc/__init__.py', 'native_pc/event_source.py', 'native_pc/internal_entry.py',
    'native_pc/internal_event_source.py', 'native_pc/native_hook_source.py',
    'native_pc/native_hook_runtime.py', 'native_pc/windows_dll_loader.py',
    'native_pc/probe_native_pc.py', 'native_pc/probe_native_pc_battle.py',
    'native_pc/probe_native_pc_rows.py',
    'event_probe_native_pc.js', 'event_probe_runtime.js', 'event_probe_autochess.js',
)
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _portable_bytes(package, name, *, limit=64 * 1024 * 1024):
    path = package.joinpath(*PurePosixPath(name).parts)
    if not path.resolve().is_relative_to(package) or not path.is_file():
        raise RuntimeError('自研启动测试便携包缺件或路径越界。')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('自研启动测试便携包文件超出大小限制。')
    return data


def _portable_json(data):
    value = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_fields,
                       parse_float=_finite_number, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError('package JSON must be an object')
    return value


def validate_native_hook_payload(binary, *, source_dir, license_dir=None, build_manifest_path=None,
                                 require_distribution_pins=True, read_file=None):
    """Validate existing bytes for packager, development or portable admission.

    Never compiles, imports supplied build code, downloads or loads a DLL.
    The portable reader additionally binds every read to release.files. The
    legacy development layout may omit redistribution pins/licenses only.
    """
    binary, source_dir = Path(binary), Path(source_dir)
    manifest_path = Path(build_manifest_path) if build_manifest_path else binary.with_suffix('.build.json')

    def read(path):
        if read_file is not None:
            return read_file(path)
        with path.open('rb') as stream:
            data = stream.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError('native hook payload exceeds limit')
        return data

    try:
        manifest_bytes = read(manifest_path)
        if len(manifest_bytes) > 4 * 1024 * 1024:
            raise ValueError('native hook build manifest exceeds limit')
        manifest = _portable_json(manifest_bytes)
        if (not isinstance(manifest.get('sha256'), str) or _SHA256.fullmatch(manifest['sha256']) is None
                or manifest['sha256'] != sha256(read(binary)).hexdigest()):
            raise RuntimeError('自研实时 DLL 与构建清单摘要不一致，请重新构建。')
        sources = manifest.get('sources')
        if (manifest.get('provider') != PROVIDER or type(manifest.get('protocol')) is not int
                or manifest['protocol'] != PROTOCOL or not isinstance(sources, dict)
                or set(sources) != set(RUNTIME_SOURCES)
                or any(not isinstance(sources[name], str) or _SHA256.fullmatch(sources[name]) is None
                       or sha256(read(source_dir / name)).hexdigest() != sources[name]
                       for name in RUNTIME_SOURCES)):
            raise RuntimeError('自研实时组件源码与构建清单不一致，请重新构建。')
        if require_distribution_pins:
            if (manifest.get('dependencies') != list(NATIVE_HOOK_DEPENDENCIES)
                    or manifest.get('licenses') != NATIVE_HOOK_LICENSES or license_dir is None):
                raise ValueError('native hook dependency/license pins mismatch')
            for name, expected in NATIVE_HOOK_LICENSES.items():
                if sha256(read(Path(license_dir) / name)).hexdigest() != expected:
                    raise ValueError('native hook license bytes mismatch')
        return manifest
    except RuntimeError:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise RuntimeError('自研实时组件载荷、构建清单或许可不一致，拒绝加载。') from None


def _portable_native_hook_binary(root, *, _read_capture=None):
    """Admit only an explicit startup-test payload, not a release certificate.

    Hashes bind this bundle's files to its manifest, not to a digital signature
    or an adversary-proof trust root. No file from a developer runtime is used.
    This gate also runs when Python is invoked without the WPF entrypoint.
    """
    root = root.resolve()
    package = root.parent
    try:
        if root.resolve() != package / 'collector':
            raise ValueError('fixed collector directory required')
        release_bytes = _portable_bytes(package, 'metadata/release.json', limit=16 * 1024 * 1024)
        release = _portable_json(release_bytes)
        if _read_capture is not None:
            _capture_material_bytes(_read_capture, package / 'metadata/release.json', release_bytes)
        if (type(release.get('format')) is not int or release['format'] != 2
                or type(release.get('protocol')) is not int or release['protocol'] != PROTOCOL
                or not isinstance(release.get('head'), str) or re.fullmatch(r'[0-9a-f]{40}', release['head']) is None):
            raise ValueError('invalid release identity')
        component = release.get('native_pc_native_hooks')
        if not isinstance(component, dict):
            raise RuntimeError('此便携包没有明示自研启动测试变体，不是完整实机验收版本。')
        if (type(component.get('protocol')) is not int or component['protocol'] != PROTOCOL
                or component.get('provider') != PROVIDER or component.get('scope') != 'official-pc-startup-test'
                or component.get('path') != NATIVE_HOOK_PACKAGE_PATH
                or component.get('build_manifest_path') != NATIVE_HOOK_BUILD_MANIFEST_PATH
                or component.get('experimental') is not True
                or component.get('default_platform') != 'native_pc' or component.get('default_backend') != 'native'
                or component.get('supported_source_builds') != [BUILD_ID]
                or component.get('performance_verified') is not False
                or component.get('autochess_handoff_verified') is not False):
            raise ValueError('invalid startup-test component')
        if (any(release.get(key) is not None for key in ('frida', 'frida_wheel_sha256', 'native_pc_gadget'))
                or any(key in release for key in ('frida_server', 'update_protocol',
                                                  'native_pc_event_backend', 'internal_event_backend'))):
            raise ValueError('not an isolated no-Frida startup-test variant')
        files = release.get('files')
        if not isinstance(files, dict) or not files or len(files) > 100000:
            raise ValueError('invalid file inventory')
        canonical = set()
        for name, digest in files.items():
            if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                    or PurePosixPath(name).is_absolute() or PurePosixPath(name).as_posix() != name
                    or any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in name.split('/'))
                    or name.casefold() in canonical or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None):
                raise ValueError('invalid or aliased inventory path/hash')
            canonical.add(name.casefold())
            if name.casefold().startswith(('python/lib/site-packages/frida', 'python/lib/site-packages/_frida',
                                           'collector/vendor/frida/', 'collector/frida-server/')):
                raise ValueError('Frida payload in startup-test inventory')
        site_packages = package / 'python/Lib/site-packages'
        if (any(path.exists() for path in (root / 'vendor/frida', root / 'frida-server'))
                or (site_packages.is_dir() and any(path.name.casefold().startswith(('frida', '_frida'))
                                                  for path in site_packages.iterdir()))):
            raise ValueError('unlisted Frida payload in startup-test bundle')

        def verified(name):
            expected = files.get(name)
            if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
                raise ValueError('required file missing from inventory')
            data = _portable_bytes(package, name)
            if sha256(data).hexdigest() != expected:
                raise ValueError('packaged file hash mismatch')
            if _read_capture is not None:
                _capture_material_bytes(_read_capture, package / name, data)
            return data

        for field, name in (('sha256', NATIVE_HOOK_PACKAGE_PATH),
                            ('build_manifest_sha256', NATIVE_HOOK_BUILD_MANIFEST_PATH)):
            if (not isinstance(component.get(field), str) or _SHA256.fullmatch(component[field]) is None
                    or component[field] != files.get(name)):
                raise ValueError('component hash differs from release inventory')
        build = validate_native_hook_payload(package / NATIVE_HOOK_PACKAGE_PATH,
            source_dir=package / 'collector/native_pc/native_event_runtime', license_dir=package / 'licenses',
            read_file=lambda path: verified(path.relative_to(package).as_posix()))
        if build['sha256'] != component['sha256']:
            raise ValueError('component/build digest mismatch')
        for name in NATIVE_HOOK_REQUIRED_SOURCES:
            verified('collector/' + name)
        # New optional collector modules are not a way around the fixed core
        # list. Runtime data is not imported source and is intentionally local.
        code_paths = {path.relative_to(package).as_posix() for path in root.rglob('*')
                      if path.is_file() and path.suffix.casefold() in ('.py', '.js')
                      and path.relative_to(root).parts[0] != 'runtime'
                      and '__pycache__' not in path.parts}
        code_paths.update(name for name in files if name.startswith('collector/')
                          and PurePosixPath(name).suffix.casefold() in ('.py', '.js')
                          and not name.startswith('collector/runtime/'))
        for name in code_paths:
            verified(name)
        return package / NATIVE_HOOK_PACKAGE_PATH
    except RuntimeError:
        raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        raise RuntimeError('自研启动测试便携包与清单不一致，拒绝加载；不是完整实机验收版本。') from None


class _Overlapped(ctypes.Structure):
    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]


class NativePipe:
    """One serial newline channel, bounded I/O, no reuse after a timed-out RPC."""

    max_response_bytes = 9 * 1024 * 1024
    max_request_bytes = 2 * 1024 * 1024

    def __init__(self, name, pid, *, timeout=5.0):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = (
            ('CreateFileW', [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
            ('WaitNamedPipeW', [wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL),
            ('GetNamedPipeServerProcessId', [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            ('CreateEventW', [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('ReadFile', [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                           ctypes.POINTER(_Overlapped)], wintypes.BOOL),
            ('WriteFile', [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                            ctypes.POINTER(_Overlapped)], wintypes.BOOL),
            ('GetOverlappedResult', [wintypes.HANDLE, ctypes.POINTER(_Overlapped),
                                     ctypes.POINTER(wintypes.DWORD), wintypes.BOOL], wintypes.BOOL),
            ('CancelIoEx', [wintypes.HANDLE, ctypes.POINTER(_Overlapped)], wintypes.BOOL),
            ('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        )
        for key, arguments, result in signatures:
            function = getattr(self.kernel, key)
            function.argtypes, function.restype = arguments, result
        self.handle = None
        self._pending = None
        self._lock = Lock()
        self._id = 0
        self._poisoned = False
        self._buffer = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            handle = self.kernel.CreateFileW(name, 0xC0000000, 0, None, 3, 0x40000000, None)
            if handle not in (None, ctypes.c_void_p(-1).value):
                self.handle = handle
                break
            error = ctypes.get_last_error()
            if error not in (2, 231) or time.monotonic() >= deadline:
                raise OSError(error, 'native observer pipe is unavailable')
            self.kernel.WaitNamedPipeW(name, 50)
            time.sleep(.01)
        owner = wintypes.DWORD()
        if not self.kernel.GetNamedPipeServerProcessId(self.handle, ctypes.byref(owner)) or owner.value != pid:
            self.close()
            raise RuntimeError('native observer pipe belongs to a different process')

    def _io(self, operation, buffer, size, deadline):
        event = self.kernel.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        overlap = _Overlapped(hEvent=event)
        transferred = wintypes.DWORD()
        try:
            done = getattr(self.kernel, operation)(self.handle, buffer, size, ctypes.byref(transferred),
                                                   ctypes.byref(overlap))
            if not done:
                error = ctypes.get_last_error()
                if error != 997:
                    raise OSError(error, 'native observer pipe I/O failed')
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                status = self.kernel.WaitForSingleObject(event, remaining)
                if status != 0:
                    self.kernel.CancelIoEx(self.handle, ctypes.byref(overlap))
                    if self.kernel.WaitForSingleObject(event, 1000) != 0:
                        # Never free an OVERLAPPED or buffer still owned by Windows.
                        self._pending = (buffer, overlap, event)
                        event = None
                    self._poisoned = True
                    raise ObserverReadRetry('native observer pipe I/O timed out; connection cannot be reused')
                if not self.kernel.GetOverlappedResult(self.handle, ctypes.byref(overlap),
                                                       ctypes.byref(transferred), False):
                    raise OSError(ctypes.get_last_error(), 'native observer pipe completion failed')
            return transferred.value
        finally:
            if event is not None:
                self.kernel.CloseHandle(event)

    def request(self, operation, *, timeout=5.0, **arguments):
        with self._lock:
            if self.handle is None or self._poisoned:
                raise RuntimeError('native observer pipe is closed or has an incomplete request')
            self._id += 1
            raw = json.dumps(dict(id=self._id, operation=operation, **arguments),
                             ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
            if len(raw) > self.max_request_bytes:
                raise ValueError('native observer request exceeds its limit')
            deadline = time.monotonic() + timeout
            try:
                written = 0
                while written < len(raw):
                    chunk = ctypes.create_string_buffer(raw[written:])
                    count = self._io('WriteFile', chunk, len(raw) - written, deadline)
                    if not count:
                        raise RuntimeError('native observer pipe stopped accepting requests')
                    written += count
                while b'\n' not in self._buffer:
                    if len(self._buffer) > self.max_response_bytes:
                        raise RuntimeError('native observer response exceeds its limit')
                    chunk = ctypes.create_string_buffer(65536)
                    count = self._io('ReadFile', chunk, len(chunk), deadline)
                    if not count:
                        raise RuntimeError('native observer pipe closed before replying')
                    self._buffer.extend(chunk.raw[:count])
                line, _, tail = self._buffer.partition(b'\n')
                self._buffer = bytearray(tail)
                if len(line) > self.max_response_bytes or tail:
                    raise RuntimeError('native observer returned oversized or unsolicited data')
                reply = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_fields,
                                   parse_float=_finite_number, parse_constant=_reject_constant)
                if (not isinstance(reply, dict) or type(reply.get('id')) is not int
                        or reply['id'] != self._id or set(reply) - {'id', 'result', 'error'}):
                    raise RuntimeError('native observer response ID or envelope mismatch')
                if reply.get('error'):
                    raise RuntimeError(str(reply['error']))
                if not isinstance(reply.get('result'), dict):
                    raise RuntimeError('native observer returned a non-object result')
                return reply['result']
            except BaseException:
                self._poisoned = True
                raise

    def close(self):
        if self.handle is None:
            return
        if self._pending is not None:
            _buffer, overlap, event = self._pending
            self.kernel.CancelIoEx(self.handle, ctypes.byref(overlap))
            if self.kernel.WaitForSingleObject(event, 1000) != 0:
                raise RuntimeError('native observer pipe cancellation is still pending')
            self.kernel.CloseHandle(event)
            self._pending = None
        self.kernel.CloseHandle(self.handle)
        self.handle = None


def native_hook_binary(root, *, _read_capture=None):
    """Resolve development bytes or the explicitly scoped portable candidate."""
    root = Path(root)
    if root.name.casefold() == 'collector' or (root.parent / 'metadata').exists():
        return _portable_native_hook_binary(root, _read_capture=_read_capture)
    binary = root / 'runtime/native-pc/native-event-runtime' / DLL_NAME
    manifest_path = binary.with_suffix('.build.json')
    if not binary.is_file() or not manifest_path.is_file():
        raise RuntimeError('请先构建自研实时组件：python native_pc/native_event_runtime/build.py')
    def read(path):
        with Path(path).open('rb') as stream:
            data = stream.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError('native hook payload exceeds limit')
        _capture_material_bytes(_read_capture, path, data)
        return data
    validate_native_hook_payload(binary, source_dir=root / 'native_pc/native_event_runtime',
                                 require_distribution_pins=False, read_file=read if _read_capture is not None else None)
    return binary


def _effective_native_environment():
    hp = os.environ.get('ARKDPS_TRACE_HP_CONTEXT') == '1'
    return hp, hp and os.environ.get('ARKDPS_TRACE_NESTED_PARENT') == '1', os.environ.get('ARKDPS_COALESCE_ENEMY_HP') != '0'


def _native_source(root, autochess_enabled=None, *, read_file=None, effective_env=None):
    assembled = assemble_probe_source('native_pc', directory=root, read_file=read_file)
    source = assembled.source
    if autochess_enabled is not None:
        source = 'globalThis.arkdpsAutoChessMode = ' + json.dumps(
            'all' if autochess_enabled() else 'local') + ';\n' + source
    hp, nested, coalesce = _effective_native_environment() if effective_env is None else effective_env
    if hp:
        source = 'globalThis.arkdpsTraceHpContext = true;\n' + source
        if nested:
            source = 'globalThis.arkdpsTraceNestedParent = true;\n' + source
    if not coalesce:
        source = 'globalThis.arkdpsCoalesceEnemyHp = false;\n' + source
    return assembled, source


def _capture_material_bytes(capture, path, data):
    if capture is None:
        return
    if type(data) is not bytes:
        raise TypeError('native material requires immutable file bytes')
    path = Path(path).resolve()
    if path in capture and capture[path] != data:
        raise RuntimeError('原生载荷在同次完整校验期间改变')
    capture[path] = data


@dataclass(frozen=True)
class NativeHookMaterial:
    """Private per-owner validated bytes, not a path/Boolean cache or credential.

    Critical prepare code is already imported at this module's top level. No
    supplied Python/build source is imported or executed by material creation.
    Hashes prove byte consistency, not an adversary-proof package signature.
    """
    root: Path
    image: bytes = field(repr=False)
    assembled: AssembledProbeSource = field(repr=False)
    source: str = field(repr=False)
    effective_env: tuple
    image_sha256: str
    build_manifest_sha256: str
    release_sha256: str | None
    provenance: tuple = field(repr=False)


def prepare_native_hook_material(root):
    """Run every existing hash gate once, assemble from those exact JS reads.

    A new call always revalidates. Callers may keep this immutable result only
    for their current root/environment lifecycle, before arming their watch.
    """
    root = Path(root).resolve()
    environment = _effective_native_environment()
    captured = {}
    binary = native_hook_binary(root, _read_capture=captured)
    binary_key, manifest_key = binary.resolve(), binary.with_suffix('.build.json').resolve()
    image, manifest_bytes = captured[binary_key], captured[manifest_key]
    portable = root.name.casefold() == 'collector' or (root.parent / 'metadata').exists()
    def source_read(path):
        key = Path(path).resolve()
        if key not in captured:
            if portable:
                raise RuntimeError('原生便携包缺少同次已校验的业务源码')
            _capture_material_bytes(captured, path, Path(path).read_bytes())
        return captured[key]
    assembled, source = _native_source(root, None, read_file=source_read, effective_env=environment)
    if _effective_native_environment() != environment:
        raise RuntimeError('原生探针环境参数在完整校验期间改变')
    image_hash = sha256(image).hexdigest()
    if _portable_json(manifest_bytes).get('sha256') != image_hash:
        raise RuntimeError('原生已验证字节与构建摘要不一致')
    base = root.parent if portable else root
    provenance = []
    for path, data in captured.items():
        try:
            name = path.relative_to(base).as_posix()
        except ValueError:
            raise RuntimeError('原生已验证载荷路径越界') from None
        provenance.append((name, sha256(data).hexdigest()))
    release = captured.get((root.parent / 'metadata/release.json').resolve()) if portable else None
    return NativeHookMaterial(root, image, assembled, source, environment, image_hash,
        sha256(manifest_bytes).hexdigest(), sha256(release).hexdigest() if release is not None else None,
        tuple(sorted(provenance)))


class PreparedNativeHooks:
    """Own an ordinary early DLL load, without connecting or evaluating JS.

    The native server waits at most 120 seconds for its first connection. A
    preparation belongs to exactly one process birth and is never resubmitted.
    """

    valid_seconds = 120.0

    def __init__(self, game_root, *, root=None, runtime_dir=None, loader_factory=WindowsDllLoader, material=None):
        self.game_root = Path(game_root).expanduser().resolve()
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parents[1]
        self.runtime_dir = Path(runtime_dir) if runtime_dir else self.root / 'runtime/native-pc/native-hook-sessions'
        self.loader_factory = loader_factory
        self.material = material
        self.process_instance = None
        self.prepared_at = self.expires_at = self.prepared_wall_time = None
        self.claimed = False
        self.hook_disable_confirmed = True  # No authentication or JS has run.
        self.cleanup_errors = {}
        self.cancelled = lambda: False
        self.commit = lambda operation: operation()
        self._lock = Lock()
        self._attempted = self._prepared = self._closed = False
        self._loader = self._config = self._staged = None
        self._assembled = self._source = self._pipe_name = self._token = None
        self._image_sha256 = self._config_sha256 = None

    def _identity(self):
        loader = self._loader
        if (loader.created_filetime != self.process_instance[1]
                or loader.api.process_times(loader.handle)[0] != self.process_instance[1]
                or loader.api.exit_code(loader.handle) != 259
                or not access.same_path(loader.api.image_path(loader.handle), self.game_root / 'Arknights.exe')):
            raise RuntimeError('预加载目标进程实例或游戏路径已变化')

    def prepare(self, process_instance):
        with self._lock:
            if self._attempted or self._closed:
                raise RuntimeError('原生组件准备对象不能重复加载')
            self._attempted = True
            if (not isinstance(process_instance, (tuple, list)) or len(process_instance) != 2
                    or any(type(part) is not int or part <= 0 for part in process_instance)):
                raise ValueError('预加载需要有效的进程实例')
            self.process_instance = tuple(process_instance)
            if self.cancelled():
                raise InterruptedError('原生组件预加载已取消')
            build, _ = access.verify_build(self.game_root)
            if (not build.get('known_build') or not build.get('amd64') or build.get('source_build') != BUILD_ID):
                raise RuntimeError('预加载需要指纹匹配的官服 Windows x64 构建')
            material = self.material
            if material is None:
                material = prepare_native_hook_material(self.root)
            if (type(material) is not NativeHookMaterial or material.root != self.root
                    or type(material.effective_env) is not tuple or len(material.effective_env) != 3
                    or any(type(value) is not bool for value in material.effective_env)
                    or material.effective_env != _effective_native_environment()
                    or type(material.image) is not bytes or type(material.source) is not str
                    or type(material.assembled) is not AssembledProbeSource
                    or sha256(material.image).hexdigest() != material.image_sha256):
                raise RuntimeError('原生已校验载荷类型、源码目录、环境或字节摘要不匹配')
            self.material = material
            image, self._image_sha256 = material.image, material.image_sha256
            self._assembled, self._source = material.assembled, material.source
            self._loader = self.loader_factory(self.process_instance[0])
            self._identity()
            self._loader.cancelled = lambda: self.cancelled()
            self._loader.commit = lambda operation: self.commit(operation)
            self._loader.before_mutation = self._identity
            nonce, self._token = secrets.token_hex(16), secrets.token_hex(32)
            folder = self.runtime_dir / nonce
            folder.mkdir(parents=True, exist_ok=False)
            self._staged = folder / DLL_NAME
            self._staged.write_bytes(image)
            self._pipe_name = rf'\\.\pipe\ArkDPS.NativeEvents.{self.process_instance[0]}-{nonce}'
            self._config = self._staged.with_suffix('.json')
            raw = json.dumps(dict(pipe=self._pipe_name, token=self._token,
                                 process_instance=list(self.process_instance), source=self._source),
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
            with self._config.open('xb') as stream:
                stream.write(raw)
            self._config_sha256 = sha256(raw).hexdigest()
            try:
                self._loader.load(self._staged)
            except TimeoutError:
                deadline = time.monotonic() + 5
                while not self._loader.poll(wait_ms=25):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Windows 预加载仍未返回，保留加载器所有权等待清理')
                if self._loader.load_error:
                    raise self._loader.load_error
            self._identity()
            self.prepared_at, self.prepared_wall_time = time.monotonic(), time.time()
            self.expires_at = self.prepared_at + self.valid_seconds
            self._prepared = True
            return self

    def _claim(self, probe):
        with self._lock:
            if not self._prepared or self._closed or self.claimed:
                raise RuntimeError('原生预加载尚未就绪、已关闭或已移交')
            if time.monotonic() >= self.expires_at:
                raise RuntimeError('原生预加载已超过 120 秒有效期，请正常重启游戏；不会重复加载')
            process = probe.collector.process
            selected_root = getattr(probe.collector, 'game_root', None)
            if selected_root is None:
                selected_root = getattr(probe.collector.diagnostics, 'native_game_root', None)
            if (tuple(process.process_instance) != self.process_instance or process.pid != self.process_instance[0]
                    or selected_root is None or not access.same_path(selected_root, self.game_root)
                    or probe.root.resolve() != self.root):
                raise RuntimeError('原生预加载与采集器进程实例、游戏路径或源码目录不匹配')
            self._identity()
            # The immutable prepared program contains no UI preference prefix.
            # Preferences use the existing typed mark control, not a weaker
            # source comparison or a rewrite of already consumed config bytes.
            _, requested_source = _native_source(probe.root, None)
            if requested_source != self._source:
                raise RuntimeError('原生预加载的探针源码或采集参数已变化，不能重新初始化或加载')
            if (sha256(self._config.read_bytes()).hexdigest() != self._config_sha256
                    or sha256(self._staged.read_bytes()).hexdigest() != self._image_sha256):
                raise RuntimeError('原生预加载的配置或 DLL 已变化')
            probe._loader, probe._config = self._loader, self._config
            self._loader = self._config = None
            self.claimed = True
            return self._assembled, self._source, self._image_sha256, self._pipe_name, self._token

    def diagnostics(self):
        with self._lock:
            loader = self._loader
            return dict(prepared=self._prepared, claimed=self.claimed, closed=self._closed,
                        process_instance=self.process_instance, prepared_at=self.prepared_at,
                        prepared_wall_time=self.prepared_wall_time, expires_at=self.expires_at,
                        valid_seconds=self.valid_seconds, source_build=BUILD_ID,
                        source_sha256=sha256(self._source.encode('utf-8')).hexdigest() if self._source else None,
                        dll_sha256=self._image_sha256, hook_disable_confirmed=self.hook_disable_confirmed,
                        cleanup_errors=dict(self.cleanup_errors),
                        loader=loader.diagnostics() if loader and hasattr(loader, 'diagnostics') else None)

    def close(self):
        with self._lock:
            if self.claimed:
                return  # The observer exclusively owns transferred resources.
            errors = {}
            if self._loader is not None:
                try:
                    self._loader.close()
                except Exception as exc:
                    errors['loader'] = str(exc)
                else:
                    self._loader = None
            if self._config is not None and self._loader is None:
                try:
                    self._config.unlink(missing_ok=True)
                except Exception as exc:
                    errors['config'] = str(exc)
                else:
                    self._config = None
            self.cleanup_errors = errors
            if errors:
                raise RuntimeError('; '.join(f'{key}: {value}' for key, value in errors.items()))
            self._closed = True


class NativeHookEventProbe:
    """The existing mark/read/ACK contract over native hooks, not a file feed."""

    contract = NATIVE_PC_CONTRACT
    transfers_event_records = True
    provides_autochess_context = True
    request_timeout = 5.0
    terminal_mark_timeout = .5

    def __init__(self, collector, remote=None, *, root=None, runtime_dir=None,
                 loader_factory=WindowsDllLoader, pipe_factory=NativePipe, prepared=None):
        if remote is not None:
            raise ValueError('自研 PC 实时组件只支持本机进程')
        self.collector = collector
        self.root = Path(root) if root else Path(__file__).resolve().parents[1]
        self.runtime_dir = Path(runtime_dir) if runtime_dir else self.root / 'runtime/native-pc/native-hook-sessions'
        self.loader_factory, self.pipe_factory = loader_factory, pipe_factory
        self.prepared = prepared
        self.binding = NativePcBinding(collector)
        self.status = ObserverStatus.STOPPED
        self.error = None
        self.probe_modes = {}
        self.cleanup_errors = {}
        self._loader = self._pipe = self._config = None
        self._started = self._attempted = False
        # A chained outer observer may only detach after this native layer
        # has explicitly disabled and drained its hooks (or never submitted).
        self.hook_disable_confirmed = True
        self._committed_sequence = self._acknowledged_sequence = 0
        self._ack_error = None
        self._autochess_enabled = None

    def set_autochess_setting(self, enabled):
        self._autochess_enabled = enabled

    def start(self):
        if self.status != ObserverStatus.STOPPED or self._attempted:
            raise RuntimeError('native hook observer cannot be started twice')
        self._attempted = True
        diagnostics, process = self.collector.diagnostics, self.collector.process
        if (not diagnostics.connection_ok or diagnostics.version_status != VersionStatus.COMPATIBLE
                or diagnostics.version_actual != BUILD_ID):
            self.status = ObserverStatus.INCOMPATIBLE
            raise RuntimeError('自研实时采集需要指纹匹配的官服 Windows x64 构建')
        self.status = ObserverStatus.ATTACHING
        try:
            if not process.process_alive() or not process.observer_boot_ready():
                raise RuntimeError('游戏 IL2CPP 尚未完成初始化')
            if self.prepared is not None:
                assembled, source, image_sha256, pipe_name, token = self.prepared._claim(self)
            else:
                binary = native_hook_binary(self.root)
                image = binary.read_bytes()
                build_manifest = json.loads(binary.with_suffix('.build.json').read_text(encoding='utf-8'),
                                            object_pairs_hook=_unique_fields)
                image_sha256 = sha256(image).hexdigest()
                if not isinstance(build_manifest, dict) or build_manifest.get('sha256') != image_sha256:
                    raise RuntimeError('自研实时 DLL 在准备期间改变，请重新构建后重试。')
                assembled, source = _native_source(self.root, self._autochess_enabled)
                nonce, token = secrets.token_hex(16), secrets.token_hex(32)
                folder = self.runtime_dir / nonce
                folder.mkdir(parents=True, exist_ok=False)
                staged = folder / DLL_NAME
                # Stage precisely the single read whose digest was verified, not
                # a second read racing a concurrent successful rebuild.
                staged.write_bytes(image)
                pipe_name = rf'\\.\pipe\ArkDPS.NativeEvents.{process.pid}-{nonce}'
                self._config = staged.with_suffix('.json')
                with self._config.open('x', encoding='utf-8') as stream:
                    json.dump(dict(pipe=pipe_name, token=token, process_instance=list(process.process_instance),
                                   source=source), stream, ensure_ascii=False, allow_nan=False)
                self._loader = self.loader_factory(process.pid)
                if self._loader.created_filetime != process.process_instance[1]:
                    raise RuntimeError('游戏进程实例在加载前发生变化')
                def validate_mutation():
                    if not process.process_alive() or not process.observer_boot_ready():
                        raise RuntimeError('游戏进程或 IL2CPP 初始化状态在加载前发生变化')
                self._loader.before_mutation = validate_mutation
                try:
                    self._loader.load(staged)
                except TimeoutError:
                    deadline = time.monotonic() + 5
                    while not self._loader.poll(wait_ms=25):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Windows 加载仍未返回，保留加载器所有权等待清理')
                    if self._loader.load_error:
                        raise self._loader.load_error
            self._pipe = self.pipe_factory(pipe_name, process.pid)
            self.hook_disable_confirmed = False
            hello = self._pipe.request('initialize', token=token, timeout=20)
            if (hello.get('provider') != PROVIDER or type(hello.get('protocol')) is not int
                    or hello['protocol'] != PROTOCOL or not isinstance(hello.get('ready'), dict)
                    or hello['ready'].get('ready') is not True or hello['ready'].get('arch') != 'x64'):
                raise RuntimeError('自研实时组件握手或架构不匹配')
            if not process.process_alive():
                raise RuntimeError('游戏进程在原生组件初始化期间退出')
            if self._autochess_enabled is not None:
                options = self._mark_options()
                if type(options['autochess_enabled']) is not bool:
                    raise ValueError('autochess_enabled must be boolean')
                # The bridge applies the preference before trying to acquire a
                # watermark. A retry (e.g. no battle in the lobby) is harmless:
                # this is only a control request, never a baseline, read or ACK.
                initial = self._pipe.request('mark', timeout=self.request_timeout, **options)
                if initial.get('error') or initial.get('stopped'):
                    raise RuntimeError(initial.get('error') or 'native observer stopped before applying preferences')
                if not process.process_alive():
                    raise RuntimeError('游戏进程在原生组件偏好初始化期间退出')
            self.probe_modes = ready_probe_modes(hello['ready'])
            self.probe_modes['probe_source'] = dict(assembled.audit(), provider=PROVIDER, protocol=PROTOCOL,
                submitted_sha256=sha256(source.encode('utf-8')).hexdigest(),
                dll_sha256=image_sha256, frida_required=False,
                experimental=True, live_acceptance='pending')
            self.binding.reset()
            self._started = True
            self.status = ObserverStatus.READY
            # Server has consumed the immutable startup configuration.
            self._config.unlink(missing_ok=True)
        except BaseException as exc:
            self.error = str(exc)
            self.status = ObserverStatus.FAILED
            try:
                self._release()
            except Exception:
                pass  # Keep failed resources and cleanup_errors for retry.
            raise

    def _raw_request(self, operation, *, timeout=None, **arguments):
        if self.status != ObserverStatus.READY or self._pipe is None:
            raise RuntimeError(self.error or 'native hook observer is not ready')
        if not self.collector.process.process_alive():
            raise RuntimeError('游戏进程已退出')
        try:
            result = self._pipe.request(operation,
                timeout=self.request_timeout if timeout is None else timeout, **arguments)
        except (OSError, RuntimeError) as exc:
            self.error = str(exc)
            self.status = ObserverStatus.FAILED
            raise
        return result

    def _request(self, operation, *, timeout=None, **arguments):
        return self.binding.bind(operation, self._raw_request(operation, timeout=timeout, **arguments))

    def remember_verified_mark(self, sample, mark):
        return self.binding.remember_verified_mark(sample, mark)

    def _mark_options(self):
        return {} if self._autochess_enabled is None else dict(autochess_enabled=self._autochess_enabled())

    def mark(self):
        return self._request('mark', **self._mark_options())

    def read_terminal_mark(self):
        self.last_terminal_response = None
        raw = self._raw_request('mark', timeout=self.terminal_mark_timeout, **self._mark_options())
        self.last_terminal_response = deepcopy(raw)
        return self.binding.bind_terminal_mark(raw)

    def read_terminal_tail(self, after, *, deadline=None):
        return acquire_native_terminal_tail(self, self._raw_request, after, deadline=deadline)

    def read_through(self, sequence):
        return self._read_range(self._committed_sequence, sequence)

    def read_baseline(self, sequence):
        return self._read_range(sequence, sequence)

    def _read_range(self, after, through):
        if self._ack_error:
            raise RuntimeError(self._ack_error)
        return self._request('read', after=after, through=through)

    def commit_events(self, sequence):
        self._committed_sequence = max(sequence, self._committed_sequence)
        if self._committed_sequence <= self._acknowledged_sequence:
            return True
        if self._ack_error:
            return False
        try:
            reply = self._request('ack', sequence=self._committed_sequence)
        except ObserverReadRetry:
            return False
        except RuntimeError as exc:
            self._ack_error = str(exc)
            return False
        acknowledged = reply.get('acknowledged')
        if reply.get('error') or type(acknowledged) is not int or acknowledged != self._committed_sequence:
            self._ack_error = reply.get('error') or 'native observer acknowledgement mismatch'
            return False
        self._acknowledged_sequence = acknowledged
        return True

    def _release(self):
        errors = {}
        if self._pipe is not None:
            try:
                # Disconnect also asks the native server to detach; it never
                # unloads a DLL containing in-flight callback return addresses.
                self._pipe.close()
            except Exception as exc:
                errors['pipe'] = str(exc)
            else:
                self._pipe = None
        if self._loader is not None:
            try:
                self._loader.close()
            except Exception as exc:
                errors['loader'] = str(exc)
            else:
                self._loader = None
        if self._config is not None and self._loader is None:
            self._config.unlink(missing_ok=True)
        self.cleanup_errors = errors
        if errors:
            raise RuntimeError('; '.join(f'{key}: {value}' for key, value in errors.items()))

    def stop(self):
        self.binding.reset()
        self.last_terminal_response = None
        stop_error = None
        if self._started and self._pipe is not None and self.status == ObserverStatus.READY:
            try:
                reply = self._pipe.request('stop', timeout=2)
                if (reply.get('error') or type(reply.get('active')) is not int or reply['active'] != 0
                        or reply.get('native_quiet') is not True):
                    raise RuntimeError('native hooks stopped with in-flight callbacks; DLL remains mapped')
                self.hook_disable_confirmed = True
            except Exception as exc:
                stop_error = exc
        self.status = ObserverStatus.STOPPED
        self._release()
        if stop_error:
            self.cleanup_errors['hooks'] = str(stop_error)
            raise stop_error
