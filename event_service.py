"""Prepare the device-side Frida server that event observation depends on.

The desktop build ships a versioned ``frida-server`` asset; this module verifies
it (SHA-256, ELF architecture and on-device version), deploys it to a path this
process owns, starts it on a loopback port, and creates an ``adb forward`` that
belongs to this process. Shutdown removes exactly the forward and process it
created; forwards and frida-server instances owned by MAA or another tool are
never removed or killed.

Only the lifecycle is implemented here; attaching the observer stays in
``event_observer`` so a missing or broken server never changes the snapshot
fallback contract.
"""
from __future__ import annotations

import hashlib
import json
import socket
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from connection import CommandError, ForwardRegistry, SubprocessRunner, _run

FRIDA_VERSION = '17.26.1'
ASSET_DIRECTORY = 'frida-server'
ASSET_MANIFEST = 'manifest.json'
DEFAULT_REMOTE_DIR = '/data/local/tmp/ark_dps'
DEFAULT_PORT = 27043
PORT_ATTEMPTS = 10
_ELF_MACHINES = {'x86_64': 62, 'aarch64': 183}
_ABI_ALIASES = {
    'x86_64': 'x86_64', 'x86-64': 'x86_64', 'amd64': 'x86_64',
    'arm64-v8a': 'aarch64', 'arm64': 'aarch64', 'aarch64': 'aarch64',
}
_LOG_TAIL = 400


class FridaServerError(RuntimeError):
    """A preparation/connection failure with an actionable diagnostic."""

    def __init__(self, code: str, message: str, *, hint: str = '', detail: str = ''):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.detail = detail

    def diagnostic(self) -> 'ServerDiagnostic':
        return ServerDiagnostic(state='failed', code=self.code, message=self.message,
                                hint=self.hint, detail=self.detail)


@dataclass
class ServerDiagnostic:
    """Serializable per-step result shown by the desktop diagnostics panel."""
    state: str = 'idle'
    code: str = ''
    message: str = ''
    hint: str = ''
    detail: str = ''
    server_path: str = ''
    device_path: str = ''
    abi: str = ''
    pid: int | None = None
    local_port: int | None = None
    remote_port: int | None = None
    forward: bool = False
    steps: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(state=self.state, code=self.code, message=self.message,
                    hint=self.hint, detail=self.detail, server_path=self.server_path,
                    device_path=self.device_path, abi=self.abi, pid=self.pid,
                    local_port=self.local_port, remote_port=self.remote_port,
                    forward=self.forward, steps=dict(self.steps))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def elf_machine(path: Path) -> int | None:
    """Return the ELF ``e_machine`` value, or None when the file is not ELF."""
    try:
        with path.open('rb') as stream:
            header = stream.read(20)
    except OSError:
        return None
    if len(header) < 20 or header[:4] != b'\x7fELF':
        return None
    order = '<' if header[5] == 1 else '>'
    return struct.unpack(order + 'H', header[18:20])[0]


def normalize_abi(value: str) -> str:
    return _ABI_ALIASES.get(value.strip().lower(), value.strip().lower())


def asset_path(root: Path, abi: str, version: str = FRIDA_VERSION) -> Path:
    return (Path(root) / ASSET_DIRECTORY /
            f'frida-server-{version}-android-{normalize_abi(abi)}')


def asset_sha256(root: Path, abi: str, version: str = FRIDA_VERSION) -> str | None:
    """Pinned digest for the bundled asset, or None when no manifest exists."""
    manifest = Path(root) / ASSET_DIRECTORY / ASSET_MANIFEST
    try:
        raw = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if str(raw.get('version')) != version:
        return None
    entry = (raw.get('assets') or {}).get(normalize_abi(abi)) or {}
    expected_name = asset_path(root, abi, version).name
    if entry.get('file') and entry['file'] != expected_name:
        return None
    value = entry.get('sha256')
    return str(value).lower() if isinstance(value, str) else None


def local_port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def tcp_ready(port: int, host: str = '127.0.0.1', timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def listening_ports(text: str) -> dict[int, str]:
    """Parse ``/proc/net/tcp`` and ``/proc/net/tcp6`` listener rows."""
    found = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[0].rstrip(':').isdigit():
            continue
        local, state = fields[1], fields[3]
        if state != '0A' or ':' not in local:
            continue
        try:
            found[int(local.rsplit(':', 1)[1], 16)] = fields[1]
        except ValueError:
            continue
    return found


class FridaServerManager:
    """Own the lifecycle of one device-side frida-server and its adb forward."""

    def __init__(self, adb: str, serial: str, *, server_path: str | Path | None = None,
                 asset_root: str | Path | None = None, runner=None,
                 forwards: ForwardRegistry | None = None, version: str = FRIDA_VERSION,
                 remote_dir: str = DEFAULT_REMOTE_DIR, start_port: int = DEFAULT_PORT,
                 log: Callable[[str], None] | None = None,
                 connect: Callable[[int], bool] | None = None,
                 local_free: Callable[[int], bool] = local_port_free,
                 sleep: Callable[[float], None] = time.sleep):
        self.adb, self.serial = adb, serial
        self.server_path = Path(server_path) if server_path else None
        self.asset_root = Path(asset_root) if asset_root is not None else None
        self.run = runner or SubprocessRunner()
        self.version = version
        self.remote_dir = remote_dir.rstrip('/')
        self.start_port = start_port
        self.forwards = forwards or ForwardRegistry(self.run)
        self.log = log or (lambda _text: None)
        self.connect = connect or tcp_ready
        self.local_free = local_free
        self.sleep = sleep
        self.abi = ''
        self.device_path = ''
        self.pid: int | None = None
        self.local_port: int | None = None
        self.remote_port: int | None = None
        self.forward = False
        self._root_prefix = ''
        self._local_sha = ''
        self._steps: dict = {}
        self._diagnostic = ServerDiagnostic()

    # -- public surface ---------------------------------------------------
    @property
    def address(self) -> str:
        return f'127.0.0.1:{self.local_port or self.start_port}'

    def diagnostic(self) -> ServerDiagnostic:
        prepared = self._diagnostic.as_dict()
        prepared.update(server_path=str(self.server_path or ''), device_path=self.device_path,
                        abi=self.abi, pid=self.pid, local_port=self.local_port,
                        remote_port=self.remote_port, forward=self.forward,
                        steps=dict(self._diagnostic.steps or self._steps))
        return ServerDiagnostic(**prepared)

    def ensure(self) -> ServerDiagnostic:
        """Bring the owned server+forward up; reuse this manager's live instance."""
        self._steps = {}
        try:
            self._probe_device()
            if self._reuse():
                return self._ready()
            self._resolve_asset()
            self._verify_local()
            self._deploy()
            self._verify_device_version()
            self._start()
            self._open_forward()
            self._wait_ready()
        except FridaServerError as exc:
            self._diagnostic = exc.diagnostic()
            self._diagnostic.server_path = str(self.server_path or '')
            self._diagnostic.device_path = self.device_path
            self._diagnostic.abi = self.abi
            self._diagnostic.pid = self.pid
            self._diagnostic.local_port = self.local_port
            self._diagnostic.remote_port = self.remote_port
            self._diagnostic.forward = self.forward
            self._diagnostic.steps = dict(self._steps)
            self.log(f'frida-server preparation failed: {exc.code}: {exc.message}')
            raise
        self._diagnostic = self._ready()
        return self._diagnostic

    def stop(self) -> None:
        """Remove only this process's forward and the server process it started."""
        errors = []
        if self.forward or self.local_port is not None:
            try:
                self.forwards.release(self.serial, self.local_port or self.start_port)
            except Exception as exc:  # the registry normally swallows CommandError
                errors.append(f'转发释放失败：{exc}')
            self.forward = False
        if self.pid is not None:
            pids, self.pid = [self.pid], None
            try:
                self._kill_exact(pids)
            except Exception as exc:
                errors.append(f'终止本工具的 frida-server 失败：{exc}')
        self.local_port = self.remote_port = None
        self._diagnostic = ServerDiagnostic(state='stopped', code='stopped',
                                            message='设备端事件服务已停止。')
        if errors:
            raise RuntimeError('；'.join(errors))

    # -- device probing ---------------------------------------------------
    def _shell(self, script: str, timeout: float = 15.0) -> str:
        command = f"{self._root_prefix} '{script}'" if self._root_prefix else script
        result = _run(self.run, [self.adb, '-s', self.serial, 'shell', command], timeout)
        if result.returncode != 0:
            raise CommandError((result.stderr or result.stdout).strip() or 'adb shell 失败')
        return result.stdout.replace('\r', '')

    def _probe_device(self) -> None:
        try:
            abi = self._shell('getprop ro.product.cpu.abi', 8.0).strip()
        except CommandError as exc:
            self._steps['device'] = 'failed'
            raise FridaServerError(
                'device_unreachable', f'无法连接设备：{exc}',
                hint='确认 MuMu 已启动、ADB 路径与实例地址正确，然后点击「重试事件观测」。',
                detail=str(exc)) from exc
        self.abi = normalize_abi(abi)
        self._steps['device'] = 'ok'
        try:
            uid = self._shell('id -u', 8.0).strip()
            if uid != '0':
                self._root_prefix = 'su -c'
                uid = self._shell('id -u', 8.0).strip()
        except CommandError as exc:
            self._steps['root'] = 'failed'
            raise FridaServerError(
                'no_root', '设备未授予 root，无法启动终端 frida-server。',
                hint='在 MuMu 中开启 root 并重启模拟器后，点击「重试事件观测」。',
                detail=str(exc)) from exc
        if uid != '0':
            self._steps['root'] = 'failed'
            raise FridaServerError(
                'no_root', '设备未授予 root，无法启动终端 frida-server。',
                hint='在 MuMu 中开启 root 并重启模拟器后，点击「重试事件观测」。')
        self._steps['root'] = 'ok'

    # -- local asset ------------------------------------------------------
    def _resolve_asset(self) -> None:
        if self.server_path is None:
            if self.asset_root is None:
                self._steps['resolve'] = 'failed'
                raise FridaServerError('missing_server', '未配置设备端 frida-server 资源目录。',
                                       hint='请使用包含 frida-server 资源的完整发行包。')
            self.server_path = asset_path(self.asset_root, self.abi or 'x86_64', self.version)
        if not self.server_path.is_file():
            self._steps['resolve'] = 'failed'
            raise FridaServerError(
                'missing_server',
                f'缺少 Frida {self.version} / {self.abi or "未知架构"} 的设备端服务：'
                f'{self.server_path}',
                hint='请使用包含 frida-server 资源的完整发行包，或把匹配版本与架构的'
                     '文件放到该路径后重试。')
        self._steps['resolve'] = 'ok'

    def _verify_local(self) -> None:
        machine = elf_machine(self.server_path)
        expected = _ELF_MACHINES.get(self.abi)
        if machine is None or expected is None or machine != expected:
            self._steps['verify'] = 'failed'
            actual = f'ELF machine {machine}' if machine else '不是 ELF 文件'
            raise FridaServerError(
                'abi_mismatch',
                f'设备端 frida-server 架构不匹配：文件为 {actual}，设备为 {self.abi or "未知"}。',
                hint='请使用与设备架构匹配的 frida-server（MuMu 12 通常为 x86_64）。',
                detail=str(self.server_path))
        digest = None
        if self.asset_root is not None and self.server_path.parent == (self.asset_root / ASSET_DIRECTORY):
            digest = asset_sha256(self.asset_root, self.abi, self.version)
        self._local_sha = _sha256(self.server_path)
        if digest and self._local_sha != digest:
            self._steps['verify'] = 'failed'
            raise FridaServerError(
                'sha_mismatch', '设备端 frida-server 文件校验失败（SHA-256 不符）。',
                hint='文件已损坏或被替换；请恢复发行包内的原始文件后重试。',
                detail=f'{self.server_path}: {self._local_sha}')
        self._steps['verify'] = 'ok'

    def _deploy(self) -> None:
        self.device_path = (f'{self.remote_dir}/'
                            f'arkdps-frida-server-{self.version}-{self.abi}')
        size = str(self.server_path.stat().st_size)
        try:
            self._shell(f'mkdir -p {self.remote_dir}', 10.0)
            current = self._remote_fingerprint()
            if current != (size, self._local_sha):
                result = _run(self.run, [self.adb, '-s', self.serial, 'push',
                                         str(self.server_path), self.device_path], 180.0)
                if result.returncode != 0:
                    raise CommandError((result.stderr or result.stdout).strip() or 'adb push 失败')
                self._shell(f'chmod 755 {self.device_path}', 10.0)
                if self._remote_fingerprint() != (size, self._local_sha):
                    self._steps['push'] = 'failed'
                    raise FridaServerError(
                        'push_failed', '推送设备端 frida-server 后校验不一致。',
                        hint='检查设备存储空间与 root 写权限后重试。',
                        detail=self.device_path)
                self._steps['push'] = 'pushed'
            else:
                self._steps['push'] = 'reused'
        except CommandError as exc:
            self._steps['push'] = 'failed'
            raise FridaServerError(
                'push_failed', f'无法推送设备端 frida-server：{exc}',
                hint='检查 ADB 连接、设备存储空间与 root 权限后重试。',
                detail=str(exc)) from exc

    def _remote_fingerprint(self) -> tuple[str, str] | None:
        script = (f'wc -c < {self.device_path} 2>/dev/null; '
                  f'sha256sum {self.device_path} 2>/dev/null')
        command = f"{self._root_prefix} '{script}'" if self._root_prefix else script
        result = _run(self.run, [self.adb, '-s', self.serial, 'shell', command], 60.0)
        if result.returncode != 0:
            return None
        lines = [line.strip() for line in result.stdout.replace('\r', '').splitlines()
                 if line.strip()]
        if len(lines) < 2 or not lines[0].isdigit():
            return None
        return lines[0], lines[1].split()[0].lower()

    def _verify_device_version(self) -> None:
        try:
            actual = self._shell(f'{self.device_path} --version', 15.0).strip()
        except CommandError as exc:
            self._steps['version'] = 'failed'
            raise FridaServerError(
                'start_failed', f'无法在设备上执行 frida-server：{exc}',
                hint='确认设备 root 与文件执行权限后重试。', detail=str(exc)) from exc
        if actual != self.version:
            self._steps['version'] = 'failed'
            raise FridaServerError(
                'version_mismatch',
                f'设备端 frida-server 版本不符：期望 {self.version}，实际 {actual or "无法读取"}。',
                hint='请使用与设备端一致的 frida-server 版本后重试。',
                detail=actual)
        self._steps['version'] = 'ok'

    # -- start / forward --------------------------------------------------
    def _remote_ports(self) -> dict[int, str]:
        try:
            return listening_ports(self._shell(
                'cat /proc/net/tcp /proc/net/tcp6 2>/dev/null', 10.0))
        except CommandError:
            return {}

    def _matching_pids(self, port: int) -> set[int]:
        marker = f'{self.device_path} -l 127.0.0.1:{port}'
        comm = Path(self.device_path).name[:15]
        script = ('for d in /proc/[0-9]*; do '
                  'c=$(cat "$d/comm" 2>/dev/null) || continue; '
                  f'[ "$c" = "{comm}" ] || continue; '
                  f'tr "\\0" " " < "$d/cmdline" 2>/dev/null | grep -q -- "{marker}" '
                  '&& echo "${d#/proc/}"; done')
        try:
            output = self._shell(script, 15.0)
        except CommandError:
            return set()
        return {int(line) for line in output.split() if line.strip().isdigit()}

    def _port_pair(self) -> tuple[int, int]:
        remote = self._remote_ports()
        for port in range(self.start_port, self.start_port + PORT_ATTEMPTS):
            if port in remote or not self.local_free(port):
                continue
            return port, port
        raise FridaServerError(
            'port_in_use',
            f'本地/设备端端口 {self.start_port}-{self.start_port + PORT_ATTEMPTS - 1} '
            '均被占用，无法启动本工具的 frida-server。',
            hint='关闭占用这些端口的其他 Frida 工具后重试；'
                 '本工具不会终止或移除其他程序的进程与转发。')

    def _start(self) -> None:
        self.local_port, self.remote_port = self._port_pair()
        baseline = self._matching_pids(self.remote_port)
        log_path = f'{self.remote_dir}/arkdps-frida-server.log'
        command = (f'{self.device_path} -l 127.0.0.1:{self.remote_port} -D '
                   f'>{log_path} 2>&1 </dev/null')
        try:
            self._shell(command, 20.0)
        except CommandError as exc:
            self._steps['start'] = 'failed'
            raise FridaServerError(
                'start_failed', f'设备端 frida-server 启动失败：{exc}',
                hint='查看设备日志 /data/local/tmp/ark_dps/arkdps-frida-server.log 后重试。',
                detail=str(exc)) from exc
        for _ in range(40):
            self.sleep(.25)
            if self.remote_port in self._remote_ports():
                break
        else:
            detail = self._log_tail(log_path)
            started = self._matching_pids(self.remote_port) - baseline
            if started:
                try:
                    self._kill_exact(sorted(started))
                except Exception:
                    pass
            self._steps['start'] = 'failed'
            code = 'port_in_use' if 'Address already in use' in detail else 'start_failed'
            raise FridaServerError(
                code, '设备端 frida-server 未能在所选端口开始监听。',
                hint='关闭占用端口的程序后重试；本工具不会终止其他程序的进程。',
                detail=detail)
        started = self._matching_pids(self.remote_port) - baseline
        if len(started) > 1:
            try:
                self._kill_exact(sorted(started))
            except Exception:
                pass
            self._steps['start'] = 'failed'
            raise FridaServerError(
                'start_failed', '设备端 frida-server 启动结果不唯一，已停止。',
                hint='重试事件观测；如持续出现请检查设备上的 frida 进程。')
        if started:
            self.pid = sorted(started)[0]
        self._steps['start'] = 'ok'

    def _open_forward(self) -> None:
        try:
            self.forwards.ensure(self.adb, self.serial, self.local_port, self.remote_port)
            self.forward = True
        except Exception as exc:
            self._steps['forward'] = 'failed'
            raise FridaServerError(
                'forward_failed', f'adb 端口转发失败：{exc}',
                hint='检查本地端口是否已被其他程序转发占用；本工具不会移除其他程序的转发。',
                detail=str(exc)) from exc
        self._steps['forward'] = 'ok'

    def _wait_ready(self) -> None:
        for _ in range(32):
            if self.connect(self.local_port):
                self._steps['ready'] = 'ok'
                return
            self.sleep(.25)
        self._steps['ready'] = 'failed'
        raise FridaServerError('server_unresponsive', '设备端 frida-server 未响应端口转发。',
                               hint='重试事件观测；若仍失败，请检查 MuMu 的 ADB 转发状态。')

    # -- reuse / cleanup --------------------------------------------------
    def _reuse(self) -> bool:
        if self.pid is None or self.local_port is None or self.remote_port is None:
            return False
        if self.pid not in self._matching_pids(self.remote_port):
            self.pid = None
            return False
        if self.remote_port not in self._remote_ports():
            return False
        try:
            self.forwards.ensure(self.adb, self.serial, self.local_port, self.remote_port)
        except Exception:
            return False
        self.forward = True
        if not self.connect(self.local_port):
            return False
        self._steps = dict(device='ok', root='ok', resolve='reused', verify='ok',
                           push='reused', version='ok', start='reused', forward='reused',
                           ready='ok')
        return True

    def _kill_exact(self, pids: list[int]) -> None:
        if not pids:
            return
        own = set(pids)
        self._shell('kill ' + ' '.join(str(pid) for pid in pids), 10.0)
        if self.remote_port is None:
            return
        for _ in range(20):
            if not (self._matching_pids(self.remote_port) & own):
                return
            self.sleep(.1)
        if self._matching_pids(self.remote_port) & own:
            self._shell('kill -9 ' + ' '.join(str(pid) for pid in pids), 10.0)
            self.sleep(.2)

    def _log_tail(self, path: str) -> str:
        try:
            return self._shell(f'tail -c {_LOG_TAIL} {path} 2>/dev/null', 10.0).strip()
        except CommandError:
            return ''

    def _ready(self) -> ServerDiagnostic:
        return ServerDiagnostic(
            state='ready', code='', message='设备端事件服务已就绪。',
            server_path=str(self.server_path or ''), device_path=self.device_path,
            abi=self.abi, pid=self.pid, local_port=self.local_port,
            remote_port=self.remote_port, forward=self.forward,
            steps=dict(self._steps, ready='ok'))
