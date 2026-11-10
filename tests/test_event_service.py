"""Device-side frida-server lifecycle and actionable failure diagnostics."""
import hashlib
import json
import struct

import pytest

from connection import CommandError, CommandResult
from event_service import (FridaServerError, FridaServerManager, asset_path,
                           asset_sha256, elf_machine, listening_ports, normalize_abi)


def elf(machine=62, body=b''):
    """Minimal little-endian ELF64 header with the given e_machine."""
    header = bytearray(b'\x7fELF' + bytes([2, 1, 1]) + bytes(9))
    header += struct.pack('<H', 2) + struct.pack('<H', machine)
    return bytes(header) + body


class Adb:
    """In-memory adb shell/push with only the commands this module issues."""

    def __init__(self, *, abi='x86_64', root=True, files=None, listeners=(),
                 version='17.26.1', start_listens=True, start_pid=True, log=''):
        self.abi, self.root = abi, root
        self.files = dict(files or {})
        self.listeners = set(listeners)
        self.version = version
        self.start_listens, self.start_pid = start_listens, start_pid
        self.log = log
        self.pids = {}
        self.calls = []
        self.pushes = []
        self.uid_calls = 0
        self._next_pid = 4100

    def _shell(self, args):
        script = args[4]
        if script.startswith("su -c '"):
            self.uid_calls += 1 if 'id -u' in script else 0
            script = script[len("su -c '"):-1]
        return script

    def __call__(self, args, timeout=None):
        args = [str(a) for a in args]
        self.calls.append(args)
        if len(args) >= 4 and args[3] == 'push':
            self.pushes.append((args[4], args[5]))
            from pathlib import Path
            self.files[args[5]] = Path(args[4]).read_bytes()
            return CommandResult(0)
        script = self._shell(args)
        if 'getprop ro.product.cpu.abi' in script:
            return CommandResult(0, self.abi + '\n')
        if script == 'id -u':
            if not self.root:
                return CommandResult(1, '', 'permission denied')
            return CommandResult(0, '0\n')
        if script.startswith('mkdir -p'):
            return CommandResult(0)
        if 'wc -c <' in script:
            path = script.split('wc -c <', 1)[1].split(';', 1)[0].split()[0]
            if path not in self.files:
                return CommandResult(1, '', 'No such file')
            data = self.files[path]
            return CommandResult(0, f'{len(data)}\n{hashlib.sha256(data).hexdigest()}  {path}\n')
        if script.endswith('--version'):
            return CommandResult(0, self.version + '\n')
        if script.startswith('cat /proc/net/tcp'):
            rows = ['  sl  local_address rem_address   st']
            for port in sorted(self.listeners):
                rows.append(f'   1: 0100007F:{port:04X} 00000000:0000 0A')
            return CommandResult(0, '\n'.join(rows) + '\n')
        if script.startswith('for d in /proc'):
            port = int(script.split('127.0.0.1:', 1)[1].split('"', 1)[0])
            return CommandResult(0, ''.join(f'{pid}\n' for pid, marker in self.pids.items()
                                            if marker == port))
        if ' -D >' in script:
            port = int(script.split('-l 127.0.0.1:', 1)[1].split(' ', 1)[0])
            if self.start_pid:
                pid = self._next_pid
                self._next_pid += 1
                self.pids[pid] = port
            if self.start_listens:
                self.listeners.add(port)
            return CommandResult(0)
        if script.startswith('chmod 755'):
            return CommandResult(0)
        if script.startswith('kill '):
            for token in script.split()[1:]:
                if token.isdigit():
                    pid = int(token)
                    port = self.pids.pop(pid, None)
                    if port is not None and not any(p == port for p in self.pids.values()):
                        self.listeners.discard(port)
            return CommandResult(0)
        if script.startswith('tail -c'):
            return CommandResult(0, self.log)
        raise AssertionError('unexpected adb shell command: ' + script)


class Forwards:
    def __init__(self, fail=False):
        self.fail = fail
        self.owned = {}
        self.calls = []

    def ensure(self, adb, serial, local, remote):
        self.calls.append(('ensure', serial, local, remote))
        if self.fail:
            raise CommandError('cannot bind local port')
        self.owned[(serial, local)] = (adb, remote)

    def release(self, serial, local):
        self.calls.append(('release', serial, local))
        self.owned.pop((serial, local), None)


def asset(tmp_path, machine=62, sha=None, name_abi='x86_64', version='17.26.1'):
    root = tmp_path / 'app'
    directory = root / 'frida-server'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'frida-server-{version}-android-{name_abi}'
    path.write_bytes(elf(machine, b'server-binary'))
    digest = sha if sha is not None else hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / 'manifest.json').write_text(json.dumps(dict(
        version=version, assets={name_abi: dict(file=path.name, sha256=digest)})),
        encoding='utf-8')
    return root


def manager(tmp_path, adb=None, *, forwards=None, connect=None, local_free=None,
            start_port=28100, **kwargs):
    adb = adb or Adb()
    return FridaServerManager(
        'adb.exe', '127.0.0.1:16384', asset_root=tmp_path / 'app', runner=adb,
        forwards=forwards or Forwards(), connect=connect or (lambda _port: True),
        local_free=local_free or (lambda _port: True), start_port=start_port,
        sleep=lambda _seconds: None, **kwargs), adb


def test_manifest_pins_sha_and_archive_layout(tmp_path):
    root = asset(tmp_path)
    path = asset_path(root, 'x86_64')
    assert path.is_file()
    assert asset_sha256(root, 'x86_64') == hashlib.sha256(path.read_bytes()).hexdigest()
    assert elf_machine(path) == 62
    assert normalize_abi('arm64-v8a') == 'aarch64'


def test_listener_parser_accepts_tcp_and_tcp6_rows():
    text = ('  sl local_address rem_address st\n'
            '   0: 0100007F:6AF5 00000000:0000 0A\n'
            '   1: 0000000000000000FFFF00000100007F:69A3 0000000000000000:0000 0A\n'
            '   2: 0100007F:6AF6 00000000:0001 01\n')
    assert set(listening_ports(text)) == {0x6AF5, 0x69A3}


def test_missing_bundled_server_reports_expected_path(tmp_path):
    (tmp_path / 'app').mkdir()
    current, _adb = manager(tmp_path)
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'missing_server'
    assert str(asset_path(tmp_path / 'app', 'x86_64')) in caught.value.message
    assert '发行包' in caught.value.hint
    assert current.diagnostic().steps.get('resolve') == 'failed'


def test_sha_mismatch_rejected_before_push(tmp_path):
    root = asset(tmp_path, sha='0' * 64)
    current, adb = manager(tmp_path)
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'sha_mismatch'
    assert adb.pushes == []
    assert current.diagnostic().steps.get('verify') == 'failed'


def test_abi_mismatch_names_device_architecture(tmp_path):
    root = asset(tmp_path, machine=183, name_abi='x86_64')
    current, adb = manager(tmp_path)
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'abi_mismatch'
    assert 'x86_64' in caught.value.message
    assert adb.pushes == []


def test_device_without_root_gets_actionable_diagnostic(tmp_path):
    asset(tmp_path)
    current, adb = manager(tmp_path, Adb(root=False))
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'no_root'
    assert 'root' in caught.value.hint
    assert current.diagnostic().steps.get('root') == 'failed'


def test_unreachable_device_is_not_reported_as_event_degradation(tmp_path):
    asset(tmp_path)

    def broken(_args, _timeout=None):
        raise CommandError('device offline')

    current = FridaServerManager('adb.exe', 'serial', asset_root=tmp_path / 'app',
                                 runner=broken, forwards=Forwards(), sleep=lambda _s: None)
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'device_unreachable'
    assert 'MuMu' in caught.value.hint


def test_cold_start_pushes_starts_own_forward_and_is_ready(tmp_path):
    asset(tmp_path)
    forwards = Forwards()
    current, adb = manager(tmp_path, forwards=forwards)
    diagnostic = current.ensure()
    assert diagnostic.state == 'ready' and diagnostic.forward
    assert diagnostic.local_port == diagnostic.remote_port == 28100
    assert adb.pushes and adb.pushes[0][1].endswith('arkdps-frida-server-17.26.1-x86_64')
    assert ('ensure', '127.0.0.1:16384', 28100, 28100) in forwards.calls
    assert diagnostic.steps == dict(device='ok', root='ok', resolve='ok', verify='ok',
                                    push='pushed', version='ok', start='ok',
                                    forward='ok', ready='ok')
    assert current.address == '127.0.0.1:28100'


def test_second_ensure_reuses_own_instance_without_push_or_restart(tmp_path):
    asset(tmp_path)
    current, adb = manager(tmp_path)
    current.ensure()
    starts = [args for args in adb.calls if len(args) >= 5 and ' -D >' in args[4]]
    pushes = list(adb.pushes)
    diagnostic = current.ensure()
    assert diagnostic.state == 'ready'
    assert len([args for args in adb.calls if len(args) >= 5 and ' -D >' in args[4]]) == len(starts)
    assert adb.pushes == pushes
    assert diagnostic.steps['start'] == 'reused'


def test_port_conflict_uses_next_port_and_leaves_foreign_forward_alone(tmp_path):
    asset(tmp_path)
    adb = Adb(listeners={28100})
    forwards = Forwards()
    current, _ = manager(tmp_path, adb, forwards=forwards)
    diagnostic = current.ensure()
    assert diagnostic.local_port == 28101
    assert ('ensure', '127.0.0.1:16384', 28101, 28101) in forwards.calls


def test_all_ports_occupied_reports_port_in_use_without_killing(tmp_path):
    asset(tmp_path)
    adb = Adb(listeners=set(range(28100, 28110)))
    current, _ = manager(tmp_path, adb)
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'port_in_use'
    assert '不会终止' in caught.value.hint
    assert not any(len(args) >= 5 and args[4].startswith('kill') for args in adb.calls)


def test_stop_kills_only_the_pid_it_started_and_releases_its_forward(tmp_path):
    asset(tmp_path)
    forwards = Forwards()
    adb = Adb()
    current, _ = manager(tmp_path, adb, forwards=forwards)
    diagnostic = current.ensure()
    pid = diagnostic.pid
    current.stop()
    kills = [args[4] for args in adb.calls if len(args) >= 5 and args[4].startswith('kill')]
    assert kills == [f'kill {pid}']
    assert ('release', '127.0.0.1:16384', 28100) in forwards.calls
    assert not adb.listeners and not adb.pids
    assert not any('kill-server' in ' '.join(args) or '--remove-all' in args for args in adb.calls)


def test_stop_never_kills_a_preexisting_matching_process(tmp_path):
    asset(tmp_path)
    forwards = Forwards()
    adb = Adb()
    adb.pids[900] = 28100  # already running for this port but not listening yet
    current, _ = manager(tmp_path, adb, forwards=forwards)
    diagnostic = current.ensure()
    assert diagnostic.pid != 900 and diagnostic.pid in adb.pids
    current.stop()
    assert 900 in adb.pids
    kills = [args[4] for args in adb.calls if len(args) >= 5 and args[4].startswith('kill')]
    assert kills == [f'kill {diagnostic.pid}']


def test_start_failure_surfaces_log_and_removes_the_process_it_started(tmp_path):
    asset(tmp_path)
    adb = Adb(start_listens=False, start_pid=True, log='error: Address already in use')
    current, _ = manager(tmp_path, adb)
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'port_in_use'
    assert 'Address already in use' in caught.value.detail
    assert not adb.pids


def test_forward_failure_reports_forward_step(tmp_path):
    asset(tmp_path)
    current, _ = manager(tmp_path, forwards=Forwards(fail=True))
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'forward_failed'
    assert current.diagnostic().steps.get('forward') == 'failed'


def test_unresponsive_forward_is_reported_not_silently_degraded(tmp_path):
    asset(tmp_path)
    current, _ = manager(tmp_path, connect=lambda _port: False)
    with pytest.raises(FridaServerError) as caught:
        current.ensure()
    assert caught.value.code == 'server_unresponsive'
