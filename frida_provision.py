"""Prepare the selected Android instance's local event endpoint."""
from __future__ import annotations

import hashlib
import lzma
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

DEFAULT_REMOTE = '127.0.0.1:27043'
DEFAULT_DEVICE_PORT = 27864
DEFAULT_SERVER_DIR = '/data/local/tmp/ark_dps'
DEFAULT_SERVER = 'frida-server-17.26.1'
BUNDLED_SERVER_ABI = 'x86_64'
BUNDLED_SERVER_ARCHIVE = (Path(__file__).resolve().with_name('frida-server') /
                          f'{DEFAULT_SERVER}-android-{BUNDLED_SERVER_ABI}.xz')
BUNDLED_SERVER_ARCHIVE_SHA256 = 'b51775e3e8f897800fd9bcd1a57cf27014b29436b08cac1ef2c157c4611fd04e'
BUNDLED_SERVER_SHA256 = 'f6fee988d080b1f83ec9fa01b94cbd1ca4bd70441f17cd6e29956bea75c792b9'


def _remote_parts(remote):
    host, _, port = remote.rpartition(':')
    if not host or not port.isdigit():
        return None
    return host, int(port)


def endpoint_ready(remote=DEFAULT_REMOTE, timeout=0.6):
    parts = _remote_parts(remote)
    if parts is None:
        return False
    try:
        with socket.create_connection(parts, timeout=timeout):
            return True
    except OSError:
        return False


def _adb(adb_path, serial, arguments, timeout=8):
    return subprocess.run([adb_path, '-s', serial, *arguments],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def _require_success(result, failure):
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(f'{failure}：{detail}' if detail else failure)


def _prepare_bundled_server(adb_path, serial, server_dir, server):
    probe = _adb(adb_path, serial, ['shell', 'id -u && getprop ro.product.cpu.abi'])
    _require_success(probe, '无法检查 MuMu Root 和 Android ABI')
    identity, _, abi = probe.stdout.strip().partition('\n')
    if identity.strip() != '0':
        raise RuntimeError('需要在所选 MuMu 实例开启 Root')
    if abi.strip() != BUNDLED_SERVER_ABI:
        raise RuntimeError(f'所选实例的 Android ABI 为 {abi.strip() or "未知"}，'
                           f'随包服务端仅支持 {BUNDLED_SERVER_ABI}')

    directory = shlex.quote(server_dir)
    destination = shlex.quote(f'{server_dir}/{server}')
    verify = f'test -x {destination} && sha256sum {destination}'
    existing = _adb(adb_path, serial, ['shell', verify])
    if existing.returncode == 0 and existing.stdout.split()[:1] == [BUNDLED_SERVER_SHA256]:
        return False

    staging_path = f'{server_dir}/.{server}-{uuid4().hex}.stage'
    staging = shlex.quote(staging_path)
    with TemporaryDirectory(prefix='arkdps-frida-') as scratch:
        binary = Path(scratch) / server
        with BUNDLED_SERVER_ARCHIVE.open('rb') as compressed:
            archive_hash = hashlib.sha256()
            for chunk in iter(lambda: compressed.read(1024 * 1024), b''):
                archive_hash.update(chunk)
            if archive_hash.hexdigest() != BUNDLED_SERVER_ARCHIVE_SHA256:
                raise RuntimeError('随包服务端文件校验失败，请重新解压完整发行包')
            compressed.seek(0)
            with lzma.LZMAFile(compressed) as unpacked, binary.open('wb') as extracted:
                shutil.copyfileobj(unpacked, extracted)
        _require_success(_adb(adb_path, serial, ['shell', f'mkdir -p {directory}']),
                         '无法创建设备端服务目录')
        try:
            _require_success(_adb(adb_path, serial, ['push', str(binary), staging_path], timeout=25),
                             '无法将服务端传到所选实例')
            received = f'$(sha256sum < {staging} | cut -d " " -f 1)'
            promote = (f'test "{received}" = {BUNDLED_SERVER_SHA256} && chmod 700 {staging} && '
                       f'test ! -d {destination} && mv -f {staging} {destination}')
            _require_success(_adb(adb_path, serial, ['shell', promote]),
                             '设备端服务文件校验或安装失败')
        except Exception:
            # The prior executable is intact until the same-directory rename succeeds.
            try:
                _adb(adb_path, serial, ['shell', f'rm -f {staging}'])
            except (OSError, subprocess.SubprocessError):
                pass
            raise
    return True


def keep_frida_endpoint(adb_path, serial, *, remote=DEFAULT_REMOTE):
    """Check an endpoint already bound to this connection, repairing it if needed."""
    if adb_path and serial and endpoint_ready(remote):
        return True, '事件端点已就绪'
    return ensure_frida_endpoint(adb_path, serial, remote=remote)


def ensure_frida_endpoint(adb_path, serial, *, remote=DEFAULT_REMOTE,
                          device_port=DEFAULT_DEVICE_PORT,
                          server_dir=DEFAULT_SERVER_DIR,
                          server=DEFAULT_SERVER):
    """Return ``(ok, detail)`` after trying to make ``remote`` reachable.

    A portable bundle supplies the binary for the default endpoint. Source
    runs and endpoint overrides can keep using an existing device binary.
    """
    if not adb_path or not serial:
        return False, '缺少 ADB 路径或实例地址'
    parts = _remote_parts(remote)
    if parts is None:
        return False, '事件端点格式无效：' + str(remote)
    forward = ['forward', f'tcp:{parts[1]}', f'tcp:{device_port}']

    def bind_forward():
        result = _adb(adb_path, serial, forward)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or '').strip()
                               or 'adb forward 返回非零')

    try:
        bind_forward()
    except Exception as exc:
        return False, '建立 ADB 转发失败：' + str(exc)
    listener = (f"netstat -lnt 2>/dev/null | grep -Eq "
                f"'[:.]{device_port}[[:space:]].*[[:space:]]LISTEN([[:space:]]|$)'")

    def reachable(timeout=0.6):
        # ADB can accept a local TCP connection even when the device has no
        # listener for the forwarded port.
        return (endpoint_ready(remote, timeout=timeout)
                and _adb(adb_path, serial, ['shell', listener]).returncode == 0)

    try:
        if reachable():
            return True, '事件端点已就绪'
    except Exception as exc:
        return False, '检查设备端 frida-server 失败：' + str(exc)

    installed = False
    if (remote == DEFAULT_REMOTE and server_dir == DEFAULT_SERVER_DIR and server == DEFAULT_SERVER
            and BUNDLED_SERVER_ARCHIVE.is_file()):
        try:
            installed = _prepare_bundled_server(adb_path, serial, server_dir, server)
        except Exception as exc:
            return False, '准备随包 frida-server 失败：' + str(exc)

    # Frida clears its temporary directory during startup, even if it later
    # loses the bind race. An independent directory protects the ARM64 agent
    # extracted by a server that is already running.
    directory = f'arkdps-frida-{uuid4().hex}'
    start = (
        f'cd {shlex.quote(server_dir)} || exit 1; '
        '( flock -n 9 || exit 75; '
        f'if {listener}; then exit 0; fi; '
        f'{shlex.quote("./" + server)} --directory {directory} '
        f'-l 127.0.0.1:{device_port} </dev/null >arkdps-frida-server.log 2>&1 9>&- & '
        'server_pid=$!; '
        'for tick in 1 2 3 4 5 6 7 8 9 10; do '
        f'if {listener}; then exit 0; fi; '
        'if ! kill -0 "$server_pid" 2>/dev/null; then '
        'tail -n 3 arkdps-frida-server.log >&2; exit 1; fi; '
        'sleep 0.2; done '
        ') 9>.frida-start.lock'
    )
    try:
        result = _adb(adb_path, serial, ['shell', start])
        if result.returncode not in (0, 75):
            detail = (result.stderr or result.stdout or '').strip()
            return False, '启动设备端 frida-server 失败：' + (detail or 'adb shell 返回非零')
    except Exception as exc:
        return False, '启动设备端 frida-server 失败：' + str(exc)
    if result.returncode == 75:
        # Another copy is still launching it. Wait for that listener without
        # starting another process.
        time.sleep(2)
    try:
        bind_forward()
        if reachable(timeout=2):
            return True, '已部署随包 frida-server，事件端点已恢复' if installed else '事件端点已恢复'
    except Exception as exc:
        return False, '重建 ADB 转发或检查端点失败：' + str(exc)
    return False, '设备端 frida-server 不可达（请确认 Root、二进制路径与端口）'
