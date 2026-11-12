"""Best-effort keep-alive for an already provisioned device-side Frida server.

V1 does not ship or install the device server. When the user enables event
observation and the known binary is already present on a rooted device, this
module can re-create the ADB forward and start the server after an emulator
restart. It never downloads or replaces the binary; failure is reported, not
silently converted into a snapshot-only success.
"""
from __future__ import annotations

import shlex
import socket
import subprocess
import time
from uuid import uuid4

DEFAULT_REMOTE = '127.0.0.1:27043'
DEFAULT_DEVICE_PORT = 27864
DEFAULT_SERVER_DIR = '/data/local/tmp/ark_dps'
DEFAULT_SERVER = 'frida-server-17.26.1'


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
                          capture_output=True, text=True, timeout=timeout)


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

    The forward is idempotent. If the endpoint is still down, a known device
    server binary is started once; an absent binary or non-root shell is
    reported as an explicit failure.
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
            return True, '事件端点已恢复'
    except Exception as exc:
        return False, '重建 ADB 转发或检查端点失败：' + str(exc)
    return False, '设备端 frida-server 不可达（请确认 Root、二进制路径与端口）'
