"""Connection configuration and MuMu instance detection.

The product only auto-selects the certified MuMu baseline. Detection is
read-only apart from starting the ordinary adb server and creating forwards
owned by this tool; it never reboots an emulator, kills an adb server or
touches forwards owned by another program (for example MAA).

GUI code consumes :class:`ConnectionDiagnostics`; the JSON config store keeps
user choices across launches. Detection and statistics compatibility are
reported separately: an accessible device does not imply a supported game
version, and a version mismatch never publishes fabricated counters.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parent
DEFAULT_ADB = r'C:\Program Files\Netease\MuMu Player 12\nx_main\adb.exe'
PACKAGE = 'com.hypergryph.arknights'
PROJECT_FORWARD_PORT = 27381
CONFIG_VERSION = 1

MUMU_ADB_CANDIDATES = (
    DEFAULT_ADB,
    r'C:\Program Files\Netease\MuMu Player 12\shell\adb.exe',
    r'C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe',
    r'C:\Program Files\Netease\MuMu Player 12\nx_device\12.0\shell\adb.exe',
)
# MuMu 12 exposes each instance through a loopback port; multi-instance ports
# are spaced by 32. The emulator-* alias is deduplicated by instance id.
_MUMU_PORT = re.compile(r'^127\.0\.0\.1:(\d+)$')


def _manifest_version() -> str | None:
    path = ROOT / 'vendor' / 'ark-source' / 'game_data' / 'manifest.json'
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('android_game_version')
    except (OSError, ValueError):
        return None


EXPECTED_GAME_VERSION = _manifest_version()


class DetectionStatus(str, Enum):
    READY_UNIQUE = 'ready_unique'
    NEEDS_SELECTION = 'needs_selection'
    NO_INSTANCE = 'no_instance'
    NO_CERTIFIED_INSTANCE = 'no_certified_instance'
    ADB_MISSING = 'adb_missing'
    ADB_INACCESSIBLE = 'adb_inaccessible'
    ERROR = 'error'


class VersionStatus(str, Enum):
    COMPATIBLE = 'compatible'
    INCOMPATIBLE = 'incompatible'
    NOT_INSTALLED = 'not_installed'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ''
    stderr: str = ''


class CommandError(RuntimeError):
    pass


class UnsafeCommandError(CommandError):
    """A command would disturb the user's emulator or another tool's adb."""


def _is_unsafe(args: Sequence[str]) -> bool:
    """Reject commands that restart targets or tear down the shared adb server."""
    words = [str(a).lower() for a in args]
    if 'kill-server' in words or 'reboot' in words:
        return True
    if 'emu' in words and 'kill' in words:
        return True
    # Removing every forward also removes forwards owned by MAA.
    return '--remove-all' in words


class SubprocessRunner:
    """Run adb commands without a shell and without touching destructive paths."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def run(self, args: Sequence[str], timeout: float | None = None) -> CommandResult:
        args = [str(a) for a in args]
        if _is_unsafe(args):
            raise UnsafeCommandError('refusing to run a command that affects other tools: %s'
                                     % ' '.join(args))
        flags = 0
        if os.name == 'nt':
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace',
                                  timeout=timeout or self.timeout, creationflags=flags)
        except FileNotFoundError as exc:
            raise CommandError('adb 不存在：%s' % args[0]) from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandError('adb 命令超时：%s' % ' '.join(args)) from exc
        return CommandResult(proc.returncode, proc.stdout or '', proc.stderr or '')


Runner = Callable[[Sequence[str], float | None], CommandResult]


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    product: str = ''
    model: str = ''
    device: str = ''
    transport_id: str = ''
    instance_id: str = ''
    accessible: bool = False
    certified_mumu: bool = False
    problem: str = ''

    @property
    def display_name(self) -> str:
        name = self.model or self.product or self.serial
        return f'{name} ({self.serial})'


@dataclass(frozen=True)
class MuMuInstance:
    instance_id: str
    serials: tuple[str, ...]
    model: str
    accessible: bool
    certified: bool
    problem: str = ''

    @property
    def primary_serial(self) -> str:
        return sorted(self.serials, key=_serial_preference)[0]


def _serial_preference(serial: str) -> tuple[int, str]:
    if _MUMU_PORT.match(serial):
        return (0, serial)
    if serial.startswith('emulator-'):
        return (1, serial)
    return (2, serial)


@dataclass
class ConnectionDiagnostics:
    """Serializable status for the GUI and logs; diagnostics never carry data."""

    status: DetectionStatus = DetectionStatus.NO_INSTANCE
    adb_path: str | None = None
    selected_serial: str | None = None
    devices: list[dict] = field(default_factory=list)
    instances: list[dict] = field(default_factory=list)
    message: str = ''
    error: str = ''
    connection_ok: bool = False
    version_status: VersionStatus = VersionStatus.UNKNOWN
    version_expected: str | None = EXPECTED_GAME_VERSION
    version_actual: str | None = None

    def as_dict(self) -> dict:
        return {
            'status': self.status.value,
            'adb_path': self.adb_path,
            'selected_serial': self.selected_serial,
            'devices': self.devices,
            'instances': self.instances,
            'message': self.message,
            'error': self.error,
            'connection_ok': self.connection_ok,
            'version_status': self.version_status.value,
            'version_expected': self.version_expected,
            'version_actual': self.version_actual,
        }


@dataclass
class DetectionResult:
    status: DetectionStatus
    adb_path: str | None
    devices: list[AdbDevice] = field(default_factory=list)
    instances: list[MuMuInstance] = field(default_factory=list)
    selected_serial: str | None = None
    message: str = ''
    error: str = ''

    @property
    def candidates(self) -> list[MuMuInstance]:
        return [i for i in self.instances if i.accessible and i.certified]

    @property
    def needs_selection(self) -> bool:
        return self.status is DetectionStatus.NEEDS_SELECTION

    def diagnostics(self) -> ConnectionDiagnostics:
        return ConnectionDiagnostics(
            status=self.status, adb_path=self.adb_path,
            selected_serial=self.selected_serial,
            devices=[{
                'serial': d.serial, 'state': d.state, 'model': d.model,
                'accessible': d.accessible, 'certified_mumu': d.certified_mumu,
                'instance_id': d.instance_id, 'problem': d.problem,
            } for d in self.devices],
            instances=[{
                'instance_id': i.instance_id, 'serials': list(i.serials),
                'model': i.model, 'accessible': i.accessible,
                'certified': i.certified, 'primary_serial': i.primary_serial,
                'problem': i.problem,
            } for i in self.instances],
            message=self.message, error=self.error,
        )


@dataclass
class VersionReport:
    status: VersionStatus
    expected: str | None
    actual: str | None
    package: str = PACKAGE
    message: str = ''
    connection_ok: bool = False

    @property
    def compatible(self) -> bool:
        return self.status is VersionStatus.COMPATIBLE

    def diagnostics(self) -> ConnectionDiagnostics:
        return ConnectionDiagnostics(
            status=DetectionStatus.READY_UNIQUE,
            connection_ok=self.connection_ok,
            version_status=self.status,
            version_expected=self.expected,
            version_actual=self.actual,
            message=self.message,
        )


@dataclass
class ConnectionConfig:
    adb_path: str | None = None
    serial: str | None = None
    auto_detect: bool = True

    def to_dict(self) -> dict:
        return {'version': CONFIG_VERSION, 'adb_path': self.adb_path,
                'serial': self.serial, 'auto_detect': bool(self.auto_detect)}

    @classmethod
    def from_dict(cls, raw: object) -> 'ConnectionConfig':
        if not isinstance(raw, dict):
            raise ValueError('连接配置必须是 JSON 对象')
        adb = raw.get('adb_path')
        serial = raw.get('serial')
        auto = raw.get('auto_detect', True)
        if adb is not None and not isinstance(adb, str):
            raise ValueError('adb_path 必须是字符串或 null')
        if serial is not None and not isinstance(serial, str):
            raise ValueError('serial 必须是字符串或 null')
        if not isinstance(auto, bool):
            raise ValueError('auto_detect 必须是布尔值')
        return cls(adb_path=adb or None, serial=serial or None, auto_detect=auto)


@dataclass
class ConfigLoadResult:
    config: ConnectionConfig
    ok: bool
    warning: str = ''


class ConnectionConfigStore:
    """Atomic JSON persistence; corrupt files never overwrite themselves."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else ROOT / 'runtime' / 'connection.json'

    def load(self) -> ConfigLoadResult:
        if not self.path.exists():
            return ConfigLoadResult(ConnectionConfig(), True)
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            return ConfigLoadResult(ConnectionConfig.from_dict(raw), True)
        except (OSError, ValueError) as exc:
            return ConfigLoadResult(ConnectionConfig(), False,
                                    f'连接配置无法读取，已使用默认值：{exc}')

    def save(self, config: ConnectionConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + '\n'
        fd, tmp = tempfile.mkstemp(prefix='.connection-', suffix='.json',
                                   dir=str(self.path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _run(runner: Runner | SubprocessRunner, args: Sequence[str],
         timeout: float | None = None) -> CommandResult:
    invoke = runner if callable(runner) else runner.run
    try:
        return invoke(args, timeout)
    except CommandError:
        raise
    except TypeError:
        # A callable taking only args is convenient in tests.
        return invoke(args)  # type: ignore[misc]
    except Exception as exc:  # subprocess and platform failures both surface here
        raise CommandError(str(exc)) from exc


def adb_candidates(explicit: str | None = None) -> list[str]:
    found: list[str] = []
    for item in (explicit, os.environ.get('MULTICA_ADB'), os.environ.get('ADB_PATH'),
                 *MUMU_ADB_CANDIDATES):
        if item and item not in found:
            found.append(item)
    which = shutil.which('adb')
    if which and which not in found:
        found.append(which)
    return found


def resolve_adb_path(explicit: str | None = None) -> str | None:
    for candidate in adb_candidates(explicit):
        if os.path.isfile(candidate):
            return candidate
    return None


def _parse_devices(stdout: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith('List of devices') or line.startswith('*'):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        fields = {}
        for token in parts[2:]:
            if ':' in token:
                key, value = token.split(':', 1)
                fields[key] = value
        devices.append(AdbDevice(
            serial=serial, state=state,
            product=fields.get('product', ''), model=fields.get('model', ''),
            device=fields.get('device', ''), transport_id=fields.get('transport_id', ''),
            accessible=state == 'device'))
    return devices


def _is_mumu_serial(serial: str) -> bool:
    match = _MUMU_PORT.match(serial)
    if match:
        port = int(match.group(1))
        return port >= 16384 and (port - 16384) % 32 == 0
    return False


def _adb_is_mumu(adb_path: str | None) -> bool:
    if not adb_path:
        return False
    normalized = adb_path.lower().replace('/', '\\')
    return 'mumu' in normalized or 'netease' in normalized


def _query_identity(run: Runner, adb: str, serial: str) -> tuple[dict, str]:
    # `settings` can be restricted on some builds; it must not turn a
    # reachable device into an inaccessible one.
    script = ('getprop ro.product.model; getprop ro.product.name; '
              'getprop ro.product.device; getprop ro.serialno; '
              'settings get secure android_id 2>/dev/null; true')
    try:
        result = _run(run, [adb, '-s', serial, 'shell', script], 10.0)
    except CommandError as exc:
        return {}, str(exc)
    if result.returncode != 0:
        return {}, (result.stderr or result.stdout or '无法读取设备身份').strip()
    lines = [line.strip() for line in result.stdout.replace('\r', '').splitlines()]
    lines += [''] * (5 - len(lines))
    model, product, device, hardware_serial, android_id = lines[:5]
    # Android's settings command prints "null" for an unset value.
    if android_id.lower() in ('null', 'unknown'):
        android_id = ''
    if hardware_serial.lower() in ('null', 'unknown'):
        hardware_serial = ''
    identity = {'model': model, 'product': product, 'device': device,
                'hardware_serial': hardware_serial, 'android_id': android_id}
    # Two serials that alias the same emulator return the same android id,
    # which is exactly the key needed to avoid offering one instance twice.
    instance_id = android_id or hardware_serial
    return identity, ('' if instance_id or model or product else '无法取得设备标识')


def _merge_devices(devices: Iterable[AdbDevice]) -> list[MuMuInstance]:
    grouped: dict[str, list[AdbDevice]] = {}
    for device in devices:
        key = device.instance_id or f'serial:{device.serial}'
        grouped.setdefault(key, []).append(device)
    instances: list[MuMuInstance] = []
    for instance_id, members in grouped.items():
        serials = tuple(sorted((m.serial for m in members), key=_serial_preference))
        accessible = any(m.accessible for m in members)
        certified = any(m.certified_mumu for m in members)
        problem = next((m.problem for m in members if m.problem), '')
        model = next((m.model for m in members if m.model), '')
        instances.append(MuMuInstance(instance_id=instance_id, serials=serials,
                                      model=model, accessible=accessible,
                                      certified=certified, problem=problem))
    instances.sort(key=lambda i: _serial_preference(i.primary_serial))
    return instances


def detect_instances(adb_path: str | None = None,
                     runner: Runner | None = None) -> DetectionResult:
    """Enumerate accessible devices and return certified MuMu instances.

    The function is deterministic and side-effect free beyond adb's normal
    server startup; no emulator or other tool is restarted.
    """
    run = runner or SubprocessRunner()
    adb = resolve_adb_path(adb_path)
    if not adb:
        return DetectionResult(DetectionStatus.ADB_MISSING, None,
                               message='未找到 adb。请安装 MuMu 或在连接设置中手动选择 adb.exe。')
    try:
        listing = _run(run, [adb, 'devices', '-l'], 20.0)
    except CommandError as exc:
        return DetectionResult(DetectionStatus.ADB_INACCESSIBLE, adb, error=str(exc),
                               message='无法访问 adb 服务，请检查模拟器与 adb。')
    if listing.returncode != 0:
        detail = (listing.stderr or listing.stdout).strip()
        return DetectionResult(DetectionStatus.ADB_INACCESSIBLE, adb, error=detail,
                               message='adb devices 失败：' + (detail or '未知错误'))
    devices = _parse_devices(listing.stdout)
    if not devices:
        return DetectionResult(DetectionStatus.NO_INSTANCE, adb,
                               message='未发现任何安卓设备。请先启动 MuMu 并开启 ADB 调试。')
    enriched: list[AdbDevice] = []
    for device in devices:
        if not device.accessible:
            problem = {'offline': '设备离线', 'unauthorized': '设备未授权',
                       'no permissions': '没有设备访问权限'}.get(device.state, '设备不可访问')
            enriched.append(AdbDevice(**{**device.__dict__, 'problem': problem}))
            continue
        identity, problem = _query_identity(run, adb, device.serial)
        emulator_serial = bool(_MUMU_PORT.match(device.serial)) or             device.serial.startswith('emulator-')
        certified = (_is_mumu_serial(device.serial) or
                     (_adb_is_mumu(adb) and emulator_serial) or
                     any('mumu' in identity.get(k, '').lower() or
                         'netease' in identity.get(k, '').lower()
                         for k in ('model', 'product', 'device')))
        enriched.append(AdbDevice(
            serial=device.serial, state=device.state,
            product=identity.get('product') or device.product,
            model=identity.get('model') or device.model,
            device=identity.get('device') or device.device,
            transport_id=device.transport_id,
            instance_id=identity.get('android_id') or identity.get('hardware_serial') or '',
            accessible=not problem, certified_mumu=certified, problem=problem))
    instances = _merge_devices(enriched)
    candidates = [i for i in instances if i.accessible and i.certified]
    accessible_other = [i for i in instances if i.accessible and not i.certified]
    if not candidates:
        if accessible_other:
            return DetectionResult(
                DetectionStatus.NO_CERTIFIED_INSTANCE, adb, enriched, instances,
                message='发现安卓设备，但没有已认证的 MuMu 实例；可在连接设置中手动选择。')
        problems = '；'.join(d.problem for d in enriched if d.problem) or '设备不可访问'
        return DetectionResult(DetectionStatus.NO_INSTANCE, adb, enriched, instances,
                               message='未发现可用实例：' + problems)
    if len(candidates) == 1:
        instance = candidates[0]
        return DetectionResult(DetectionStatus.READY_UNIQUE, adb, enriched, instances,
                               selected_serial=instance.primary_serial,
                               message=f'已自动选择 {instance.model or instance.primary_serial}')
    return DetectionResult(DetectionStatus.NEEDS_SELECTION, adb, enriched, instances,
                           message='发现多个 MuMu 实例，请选择要连接的实例。')


@dataclass
class ConnectionTarget:
    adb_path: str
    serial: str
    source: str
    diagnostics: ConnectionDiagnostics

    @property
    def collectable(self) -> bool:
        return (self.diagnostics.connection_ok and
                self.diagnostics.version_status is VersionStatus.COMPATIBLE)

    def require_collectable(self) -> None:
        """Raise before any counter is read when the version is unsupported."""
        if not self.diagnostics.connection_ok:
            raise ConnectionUnavailable('连接不可用：' + self.diagnostics.message,
                                        self.diagnostics)
        if self.diagnostics.version_status is not VersionStatus.COMPATIBLE:
            raise ConnectionUnavailable(self.diagnostics.message or '游戏版本不受支持',
                                        self.diagnostics)


class ConnectionUnavailable(RuntimeError):
    def __init__(self, message: str,
                 diagnostics: ConnectionDiagnostics | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics


def resolve_target(config: ConnectionConfig,
                   runner: Runner | None = None) -> ConnectionTarget:
    """Resolve a concrete adb path and serial from config and auto-detection."""
    run = runner or SubprocessRunner()
    if config.serial:
        adb = resolve_adb_path(config.adb_path)
        if not adb:
            diag = ConnectionDiagnostics(status=DetectionStatus.ADB_MISSING,
                                         message='手动配置了实例，但未找到 adb。')
            raise ConnectionUnavailable(diag.message, diag)
        report = check_version(adb, config.serial, run)
        # A TCP endpoint may be reachable yet absent from this adb server.
        # Do not reconnect registered/offline or USB devices.
        if (not report.connection_ok and
                re.fullmatch(r'(?:\[[^\]]+\]|[^:\s]+):\d+', config.serial) and
                'not found' in report.message.lower()):
            try:
                _run(run, [adb, 'connect', config.serial], 10.0)
                report = check_version(adb, config.serial, run)
            except CommandError as exc:
                report.message = '无法连接设备：' + str(exc)
        diag = report.diagnostics()
        diag.adb_path, diag.selected_serial = adb, config.serial
        if not report.connection_ok:
            raise ConnectionUnavailable('无法连接手动指定的实例：' + report.message, diag)
        return ConnectionTarget(adb, config.serial, 'manual', diag)
    result = detect_instances(config.adb_path, run)
    diag = result.diagnostics()
    if result.status is DetectionStatus.READY_UNIQUE and result.selected_serial:
        return ConnectionTarget(result.adb_path, result.selected_serial, 'auto', diag)
    if result.status is DetectionStatus.NEEDS_SELECTION:
        raise ConnectionUnavailable('发现多个 MuMu 实例，请选择要连接的实例。', diag)
    raise ConnectionUnavailable(result.message or '没有可用连接', diag)


def resolve_configured(config: ConnectionConfig,
                       runner: Runner | None = None) -> ConnectionTarget:
    """Resolve a connection setting, honoring auto-detection and a preference.

    ``auto_detect`` off uses the stored address as a manual target. With it on,
    a unique certified instance is selected automatically; with several, the
    stored address is used only when it still names one of the candidates,
    otherwise the caller must ask the player to choose.
    """
    if not config.auto_detect:
        if not config.serial:
            adb = resolve_adb_path(config.adb_path)
            diag = ConnectionDiagnostics(
                status=DetectionStatus.NO_INSTANCE, adb_path=adb,
                message='自动检测已关闭；请在连接设置中填写实例地址或重新开启自动检测。')
            raise ConnectionUnavailable(diag.message, diag)
        return resolve_target(ConnectionConfig(config.adb_path, config.serial, False),
                              runner)
    result = detect_instances(config.adb_path, runner)
    diag = result.diagnostics()
    if result.status is DetectionStatus.READY_UNIQUE and result.selected_serial:
        return ConnectionTarget(result.adb_path, result.selected_serial, 'auto', diag)
    if result.status is DetectionStatus.NEEDS_SELECTION:
        if config.serial and any(config.serial in instance.serials
                                 for instance in result.candidates):
            return ConnectionTarget(result.adb_path, config.serial, 'selected', diag)
        raise ConnectionUnavailable('发现多个 MuMu 实例，请选择要连接的实例。', diag)
    raise ConnectionUnavailable(result.message or '没有可用连接', diag)


def _parse_version(output: str) -> str | None:
    match = re.search(r'versionName=([^\s]+)', output)
    return match.group(1) if match else None


def check_version(adb: str, serial: str, runner: Runner | None = None,
                  expected: str | None = None) -> VersionReport:
    """Probe connection and game version independently.

    connection_ok reflects adb reachability and game installation. The version
    status is separate so the GUI can say "connected but unsupported" while
    refusing to display counters.
    """
    run = runner or SubprocessRunner()
    expected = expected or EXPECTED_GAME_VERSION
    try:
        probe = _run(run, [adb, '-s', serial, 'shell', 'true'], 8.0)
    except CommandError as exc:
        return VersionReport(VersionStatus.UNKNOWN, expected, None,
                             message='无法连接设备：' + str(exc), connection_ok=False)
    if probe.returncode != 0:
        return VersionReport(VersionStatus.UNKNOWN, expected, None,
                             message='无法连接设备：' +
                                     (probe.stderr or probe.stdout).strip(),
                             connection_ok=False)
    try:
        package = _run(run, [adb, '-s', serial, 'shell', 'dumpsys', 'package', PACKAGE], 20.0)
    except CommandError as exc:
        return VersionReport(VersionStatus.UNKNOWN, expected, None,
                             message='无法读取游戏版本：' + str(exc), connection_ok=True)
    if package.returncode != 0 or not package.stdout.strip():
        return VersionReport(VersionStatus.NOT_INSTALLED, expected, None,
                             message='设备已连接，但未找到明日方舟客户端。', connection_ok=True)
    actual = _parse_version(package.stdout)
    if not actual:
        return VersionReport(VersionStatus.UNKNOWN, expected, None,
                             message='设备已连接，但无法解析游戏版本。', connection_ok=True)
    if expected and actual != expected:
        return VersionReport(VersionStatus.INCOMPATIBLE, expected, actual,
                             message=f'当前仅验证 Android {expected}，检测到 {actual}；'
                                     '拒绝读取统计，需更新适配。', connection_ok=True)
    return VersionReport(VersionStatus.COMPATIBLE, expected, actual,
                         message='连接与游戏版本检查通过。', connection_ok=True)


class ForwardRegistry:
    """Track only forwards this process created and remove exactly those."""

    def __init__(self, runner: Runner | None = None):
        self.run = runner or SubprocessRunner()
        self.owned: dict[tuple[str, int], tuple[str, int]] = {}

    def ensure(self, adb: str, serial: str, local: int = PROJECT_FORWARD_PORT,
               remote: int = PROJECT_FORWARD_PORT) -> None:
        key = (serial, local)
        if key in self.owned:
            old_adb, old_remote = self.owned[key]
            listing = _run(self.run, [old_adb, 'forward', '--list'], 10.0)
            if listing.returncode != 0:
                raise CommandError((listing.stderr or listing.stdout).strip() or '无法检查 adb 转发')
            expected = [serial, f'tcp:{local}', f'tcp:{old_remote}']
            if any(line.split() == expected for line in listing.stdout.splitlines()):
                if (old_adb, old_remote) == (adb, remote):
                    return
                raise CommandError('本地端口已有本工具的其他转发')
            self.owned.pop(key)
        result = _run(self.run, [adb, '-s', serial, 'forward', '--no-rebind',
                                 f'tcp:{local}', f'tcp:{remote}'], 10.0)
        if result.returncode != 0:
            raise CommandError((result.stderr or result.stdout).strip() or 'adb forward 失败')
        self.owned[key] = (adb, remote)

    def release(self, serial: str, local: int = PROJECT_FORWARD_PORT) -> None:
        owned = self.owned.get((serial, local))
        if not owned:
            return
        adb, remote = owned
        try:
            listing = _run(self.run, [adb, 'forward', '--list'], 10.0)
            if listing.returncode != 0:
                return
            expected = [serial, f'tcp:{local}', f'tcp:{remote}']
            if any(line.split() == expected for line in listing.stdout.splitlines()):
                result = _run(self.run, [adb, '-s', serial, 'forward', '--remove',
                                         f'tcp:{local}'], 10.0)
                if result.returncode != 0:
                    return
            self.owned.pop((serial, local), None)
        except CommandError:
            pass
