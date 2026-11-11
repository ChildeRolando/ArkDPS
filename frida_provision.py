"""Best-effort keep-alive for an already provisioned device-side Frida server.

V1 does not ship or install the device server. When the user enables event
observation and the known binary is already present on a rooted device, this
module can re-create the ADB forward and start the server after an emulator
restart. It never downloads or replaces the binary; failure is reported, not
silently converted into a snapshot-only success.
"""
from __future__ import annotations

import socket
import subprocess
import time

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
    try:
        _adb(adb_path, serial, forward)
    except Exception as exc:
        return False, '建立 ADB 转发失败：' + str(exc)
    if endpoint_ready(remote):
        return True, '事件端点已就绪'
    probe = (f'ps -A 2>/dev/null | grep -q "[{server[:1]}]{server[1:]}" '
             f'|| netstat -ln 2>/dev/null | grep -q ":{device_port}"')
    try:
        running = _adb(adb_path, serial, ['shell', probe]).returncode == 0
    except Exception:
        running = False
    if not running:
        start = (f'cd {server_dir} && (./{server} -l 127.0.0.1:{device_port} '
                 f'> arkdps-frida-server.log 2>&1 &)')
        try:
            result = _adb(adb_path, serial, ['shell', start])
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or '').strip()
                return False, '启动设备端 frida-server 失败：' + (detail or 'adb shell 返回非零')
        except Exception as exc:
            return False, '启动设备端 frida-server 失败：' + str(exc)
        time.sleep(2)
    try:
        _adb(adb_path, serial, forward)
    except Exception as exc:
        return False, '重建 ADB 转发失败：' + str(exc)
    if endpoint_ready(remote, timeout=2):
        return True, '事件端点已恢复'
    return False, '设备端 frida-server 不可达（请确认 Root、二进制路径与端口）'
