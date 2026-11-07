import json
from pathlib import Path

import pytest

import connection as conn


class FakeAdb:
    """Small deterministic adb double; records every command it receives."""

    def __init__(self, devices="", identities=None, versions=None,
                 list_error=None, shell_error=None):
        self.devices_output = devices
        self.identities = identities or {}
        self.versions = versions or {}
        self.list_error = list_error
        self.shell_error = shell_error
        self.calls = []
        self.forwards = {}

    def __call__(self, args, timeout=None):
        args = list(args)
        self.calls.append(args)
        if 'devices' in args:
            if self.list_error:
                return conn.CommandResult(1, '', self.list_error)
            return conn.CommandResult(0, self.devices_output, '')
        if 'connect' in args:
            return conn.CommandResult(0, 'connected', '')
        if 'forward' in args and '--list' in args:
            return conn.CommandResult(0, ''.join(
                f'{serial} tcp:{local} tcp:{remote}\n'
                for (serial, local), remote in self.forwards.items()))
        serial = args[args.index('-s') + 1]
        if 'shell' in args:
            tail = args[args.index('shell') + 1:]
            if tail == ['true']:
                if self.shell_error:
                    return conn.CommandResult(1, '', self.shell_error)
                return conn.CommandResult(0, '', '')
            if tail and tail[0].startswith('getprop'):
                identity = self.identities.get(serial)
                if identity is None:
                    return conn.CommandResult(1, '', 'device not found')
                if isinstance(identity, conn.CommandResult):
                    return identity
                model, product, device, hardware, android_id = identity
                return conn.CommandResult(0, '\n'.join(
                    (model, product, device, hardware, android_id)) + '\n', '')
            if tail[:2] == ['dumpsys', 'package']:
                version = self.versions.get(serial)
                if version is None:
                    return conn.CommandResult(0, '', '')
                if version == 'unparsable':
                    return conn.CommandResult(0, 'Package [x]\n', '')
                return conn.CommandResult(
                    0, f'Packages:\n  Package [com.hypergryph.arknights]\n'
                       f'    versionName={version}\n', '')
        if 'forward' in args:
            if '--remove' in args:
                local = int(args[-1].split(':')[1])
                self.forwards.pop((serial, local), None)
            else:
                local, remote = (int(value.split(':')[1]) for value in args[-2:])
                if '--no-rebind' in args and any(port == local for _, port in self.forwards):
                    return conn.CommandResult(1, '', 'cannot rebind existing socket')
                self.forwards[(serial, local)] = remote
            return conn.CommandResult(0, '', '')
        return conn.CommandResult(0, '', '')


def device_line(serial, state='device'):
    return f'{serial}\t{state} product:HapburnP model:HBP_AL00 device:HapburnP transport_id:1\n'


@pytest.fixture
def adb_path(monkeypatch):
    monkeypatch.setattr(conn, 'resolve_adb_path', lambda explicit=None: 'C:\\MuMu\\adb.exe')
    return 'C:\\MuMu\\adb.exe'


def identity(android_id='mu-1', model='HBP-AL00'):
    return (model, 'HapburnP', 'HapburnP', '', android_id)


def test_zero_instances(adb_path):
    fake = FakeAdb('List of devices attached\n\n')
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.NO_INSTANCE
    assert result.selected_serial is None
    assert not result.candidates


def test_unique_mumu_auto_selected_and_aliases_deduplicated(adb_path):
    fake = FakeAdb(
        'List of devices attached\n' + device_line('emulator-5554') +
        device_line('127.0.0.1:16384'),
        identities={'emulator-5554': identity(), '127.0.0.1:16384': identity()},
    )
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.READY_UNIQUE
    assert result.selected_serial == '127.0.0.1:16384'
    assert len(result.instances) == 1
    assert result.instances[0].serials == ('127.0.0.1:16384', 'emulator-5554')
    assert result.instances[0].certified


def test_multiple_instances_require_selection(adb_path):
    fake = FakeAdb(
        'List of devices attached\n' + device_line('127.0.0.1:16384') +
        device_line('127.0.0.1:16416'),
        identities={'127.0.0.1:16384': identity('a'),
                    '127.0.0.1:16416': identity('b', model='HBP-AL00-2')},
    )
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.NEEDS_SELECTION
    assert result.needs_selection
    assert result.selected_serial is None
    assert len(result.candidates) == 2


def test_inaccessible_instances_are_reported_not_selected(adb_path):
    fake = FakeAdb('List of devices attached\n' +
                   device_line('127.0.0.1:16384', 'offline') +
                   device_line('127.0.0.1:16416', 'unauthorized'))
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.NO_INSTANCE
    assert 'offline' not in result.message and '是否' not in result.message
    assert {d.state for d in result.devices} == {'offline', 'unauthorized'}
    assert all(not d.accessible and d.problem for d in result.devices)


def test_identity_failure_marks_device_inaccessible(adb_path):
    fake = FakeAdb('List of devices attached\n' + device_line('127.0.0.1:16384'),
                   identities={'127.0.0.1:16384': conn.CommandResult(1, '', 'no root')})
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.NO_INSTANCE
    assert result.devices[0].accessible is False
    assert 'no root' in result.devices[0].problem


def test_generic_android_device_is_not_auto_selected(monkeypatch):
    monkeypatch.setattr(conn, 'resolve_adb_path', lambda explicit=None: 'C:\\platform-tools\\adb.exe')
    fake = FakeAdb('List of devices attached\n' + device_line('ZY22'),
                   identities={'ZY22': identity('phone')})
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.NO_CERTIFIED_INSTANCE
    assert result.instances[0].certified is False


def test_adb_missing(monkeypatch):
    monkeypatch.setattr(conn, 'resolve_adb_path', lambda explicit=None: None)
    result = conn.detect_instances(runner=FakeAdb())
    assert result.status is conn.DetectionStatus.ADB_MISSING


def test_adb_inaccessible(adb_path):
    fake = FakeAdb(list_error='cannot connect to daemon')
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.ADB_INACCESSIBLE
    assert 'cannot connect to daemon' in result.error


def test_version_status_is_separate_from_connection(adb_path):
    fake = FakeAdb(versions={'127.0.0.1:16384': '2.7.98'})
    report = conn.check_version('adb', '127.0.0.1:16384', fake, expected='2.7.91')
    assert report.connection_ok is True
    assert report.status is conn.VersionStatus.INCOMPATIBLE
    assert report.actual == '2.7.98'
    diag = report.diagnostics()
    assert diag.connection_ok is True
    assert diag.version_status is conn.VersionStatus.INCOMPATIBLE


def test_version_compatible_and_not_installed(adb_path):
    fake = FakeAdb(versions={'s': '2.7.91'})
    assert conn.check_version('adb', 's', fake, expected='2.7.91').status is conn.VersionStatus.COMPATIBLE
    missing = conn.check_version('adb', 's', FakeAdb(), expected='2.7.91')
    assert missing.connection_ok is True
    assert missing.status is conn.VersionStatus.NOT_INSTALLED


def test_connection_probe_failure(adb_path):
    fake = FakeAdb(shell_error='device offline')
    report = conn.check_version('adb', 's', fake, expected='2.7.91')
    assert report.connection_ok is False
    assert report.status is conn.VersionStatus.UNKNOWN


def test_manual_override_resolution(adb_path):
    fake = FakeAdb(versions={'manual:1': '2.7.91'})
    target = conn.resolve_target(conn.ConnectionConfig(serial='manual:1'), fake)
    assert target.serial == 'manual:1'
    assert target.source == 'manual'
    assert target.diagnostics.version_status is conn.VersionStatus.COMPATIBLE


def test_manual_override_reports_version_mismatch_without_publishing(adb_path):
    fake = FakeAdb(versions={'manual:1': '2.7.80'})
    target = conn.resolve_target(conn.ConnectionConfig(serial='manual:1'), fake)
    assert target.diagnostics.connection_ok
    assert target.diagnostics.version_status is conn.VersionStatus.INCOMPATIBLE


def test_manual_override_unreachable(adb_path):
    fake = FakeAdb(shell_error='device not found')
    with pytest.raises(conn.ConnectionUnavailable) as exc:
        conn.resolve_target(conn.ConnectionConfig(serial='nope'), fake)
    assert exc.value.diagnostics.connection_ok is False


def test_auto_resolution_selects_unique_instance(adb_path):
    fake = FakeAdb('List of devices attached\n' + device_line('127.0.0.1:16384'),
                   identities={'127.0.0.1:16384': identity()})
    target = conn.resolve_target(conn.ConnectionConfig(), fake)
    assert target.serial == '127.0.0.1:16384'
    assert target.source == 'auto'


def test_auto_resolution_raises_for_multiple(adb_path):
    fake = FakeAdb('List of devices attached\n' + device_line('127.0.0.1:16384') +
                   device_line('127.0.0.1:16416'),
                   identities={'127.0.0.1:16384': identity('a'),
                               '127.0.0.1:16416': identity('b')})
    with pytest.raises(conn.ConnectionUnavailable) as exc:
        conn.resolve_target(conn.ConnectionConfig(), fake)
    assert '选择' in str(exc.value)
    assert exc.value.diagnostics.status is conn.DetectionStatus.NEEDS_SELECTION


def test_config_roundtrip_and_corrupt_file_is_kept(tmp_path):
    store = conn.ConnectionConfigStore(tmp_path / 'connection.json')
    assert store.load().ok
    store.save(conn.ConnectionConfig(adb_path='C:\\adb.exe', serial='127.0.0.1:16384',
                                     auto_detect=False))
    loaded = store.load()
    assert loaded.ok and loaded.config.serial == '127.0.0.1:16384'
    assert loaded.config.adb_path == 'C:\\adb.exe'
    assert loaded.config.auto_detect is False
    assert json.loads(store.path.read_text(encoding='utf-8'))['version'] == conn.CONFIG_VERSION
    store.path.write_text('{broken', encoding='utf-8')
    broken = store.load()
    assert broken.ok is False and broken.config == conn.ConnectionConfig()
    assert '{broken' in store.path.read_text(encoding='utf-8')
    assert not list(tmp_path.glob('.connection-*.json'))


def test_config_rejects_invalid_values():
    with pytest.raises(ValueError):
        conn.ConnectionConfig.from_dict({'serial': 4})
    with pytest.raises(ValueError):
        conn.ConnectionConfig.from_dict({'auto_detect': 'yes'})
    with pytest.raises(ValueError):
        conn.ConnectionConfig.from_dict([])


def test_unsafe_adb_commands_are_refused():
    for args in (['adb', 'kill-server'], ['adb', '-s', 'x', 'reboot'],
                 ['adb', 'emu', 'kill'], ['adb', 'forward', '--remove-all']):
        assert conn._is_unsafe(args)
    assert not conn._is_unsafe(['adb', 'devices', '-l'])
    runner = conn.SubprocessRunner()
    with pytest.raises(conn.CommandError):
        runner.run(['adb', 'kill-server'])


def test_forward_registry_only_releases_owned_forwards():
    fake = FakeAdb()
    registry = conn.ForwardRegistry(fake)
    registry.release('not-owned')
    assert not [c for c in fake.calls if 'forward' in c]
    registry.ensure('adb', '127.0.0.1:16384')
    registry.release('127.0.0.1:16384')
    forward_calls = [c for c in fake.calls if 'forward' in c]
    assert any('--remove' in c for c in forward_calls)
    assert not any('--remove-all' in c for c in forward_calls)


def test_mumu_adb_does_not_certify_a_usb_phone(adb_path):
    fake = FakeAdb('List of devices attached\n' + device_line('ZY22'),
                   identities={'ZY22': identity('phone')})
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.NO_CERTIFIED_INSTANCE


def test_missing_android_id_does_not_hide_an_accessible_instance(adb_path):
    fake = FakeAdb('List of devices attached\n' + device_line('127.0.0.1:16384'),
                   identities={'127.0.0.1:16384': ('HBP-AL00', 'HapburnP', 'HapburnP', 'hw-1', '')})
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.READY_UNIQUE
    assert result.devices[0].accessible
    assert result.devices[0].instance_id == 'hw-1'


def test_collectable_gate_refuses_incompatible_version(adb_path):
    fake = FakeAdb(versions={'manual:1': '2.7.80'})
    target = conn.resolve_target(conn.ConnectionConfig(serial='manual:1'), fake)
    assert target.collectable is False
    with pytest.raises(conn.ConnectionUnavailable) as exc:
        target.require_collectable()
    assert exc.value.diagnostics.version_status is conn.VersionStatus.INCOMPATIBLE


def test_foreign_forward_conflict_and_replacement_are_preserved():
    fake = FakeAdb()
    fake.forwards[('other', 27381)] = 1717
    registry = conn.ForwardRegistry(fake)
    with pytest.raises(conn.CommandError):
        registry.ensure('adb', 's')
    registry.release('s')
    assert fake.forwards == {('other', 27381): 1717}
    fake.forwards.clear()
    registry.ensure('adb', 's')
    fake.forwards[('s', 27381)] = 9999  # replaced by another client
    registry.release('s')
    assert fake.forwards == {('s', 27381): 9999}


def test_manual_unregistered_tcp_address_connects_once(adb_path):
    class Unregistered(FakeAdb):
        registered = False

        def __call__(self, args, timeout=None):
            if 'connect' in args:
                self.registered = True
            if args[-2:] == ['shell', 'true'] and not self.registered:
                self.calls.append(list(args))
                return conn.CommandResult(1, '', "device 'localhost:16384' not found")
            return super().__call__(args, timeout)

    fake = Unregistered(versions={'localhost:16384': conn.EXPECTED_GAME_VERSION})
    target = conn.resolve_target(conn.ConnectionConfig(serial='localhost:16384'), fake)
    assert target.collectable
    assert sum('connect' in call for call in fake.calls) == 1


def test_manual_tcp_failed_connect_and_usb_do_not_fake_success(adb_path):
    fake = FakeAdb(shell_error='device not found')
    with pytest.raises(conn.ConnectionUnavailable) as exc:
        conn.resolve_target(conn.ConnectionConfig(serial='localhost:16384'), fake)
    assert not exc.value.diagnostics.connection_ok
    assert sum('connect' in call for call in fake.calls) == 1
    fake = FakeAdb(shell_error='device not found')
    with pytest.raises(conn.ConnectionUnavailable):
        conn.resolve_target(conn.ConnectionConfig(serial='usb-serial'), fake)
    assert not any('connect' in call for call in fake.calls)


def test_forward_list_failure_preserves_mapping_for_retry():
    class ListFailure(FakeAdb):
        def __call__(self, args, timeout=None):
            if '--list' in args:
                return conn.CommandResult(1, '', 'adb unavailable')
            return super().__call__(args, timeout)

    fake = ListFailure()
    registry = conn.ForwardRegistry(fake)
    registry.ensure('adb', 's')
    registry.release('s')
    assert fake.forwards == {('s', 27381): 27381}
    assert ('s', 27381) in registry.owned


@pytest.mark.parametrize('missing', ['null', ' NULL ', ''])
def test_missing_android_id_keeps_distinct_instances(adb_path, missing):
    serials = ('127.0.0.1:16384', '127.0.0.1:16416')
    fake = FakeAdb('List of devices attached\n' + ''.join(map(device_line, serials)),
                   identities={s: ('model', 'product', 'device', f'hw-{i}', missing)
                               for i, s in enumerate(serials)})
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.NEEDS_SELECTION
    assert result.selected_serial is None
    assert len(result.instances) == 2


def test_both_identifiers_missing_keeps_distinct_instances(adb_path):
    serials = ('127.0.0.1:16384', '127.0.0.1:16416')
    fake = FakeAdb('List of devices attached\n' + ''.join(map(device_line, serials)),
                   identities={s: ('model', 'product', 'device', 'unknown', 'null')
                               for s in serials})
    result = conn.detect_instances(runner=fake)
    assert result.status is conn.DetectionStatus.NEEDS_SELECTION
    assert len(result.instances) == 2


def collector_with_fake_adb(monkeypatch):
    import collector
    fake = FakeAdb()
    instance = collector.Collector(adb='adb', serial='s', log=lambda *_: None)
    if not hasattr(instance, 'forward_registry'):
        instance.forward_registry = conn.ForwardRegistry(fake)
        instance.core.mc.forward_registry = instance.forward_registry
    instance.forward_registry.run = fake
    monkeypatch.setattr(instance.core.mc, 'adb',
                        lambda *args, **kwargs: fake(['adb', '-s', 's', *args]))
    return instance, fake


def test_collector_channel_forward_conflict_and_owned_close(monkeypatch):
    import collector

    instance, fake = collector_with_fake_adb(monkeypatch)
    channel = collector.TcpChannel(instance.core.mc, port=collector.PORT)
    instance.core.mc._chan = channel
    monkeypatch.setattr(channel, '_push_memsrv', lambda: True)
    monkeypatch.setattr(channel, '_server_up', lambda: True)
    fake.forwards[('other', collector.PORT)] = 1717
    with pytest.raises(conn.CommandError):
        channel._ensure_server()
    instance.close()
    assert fake.forwards == {('other', collector.PORT): 1717}
    assert not any('--remove' in c for c in fake.calls)

    fake.forwards.clear()
    channel._ensure_server()
    channel._ensure_server()  # reopening the same owned mapping is safe
    assert fake.forwards == {('s', collector.PORT): collector.PORT}
    instance.close()
    assert fake.forwards == {}
    assert any('--no-rebind' in c for c in fake.calls)


def test_collector_close_preserves_replaced_forward(monkeypatch):
    import collector

    instance, fake = collector_with_fake_adb(monkeypatch)
    channel = collector.TcpChannel(instance.core.mc, port=collector.PORT)
    instance.core.mc._chan = channel
    monkeypatch.setattr(channel, '_kill_own_nc', lambda: None)
    monkeypatch.setattr(channel, '_push_memsrv', lambda: True)
    monkeypatch.setattr(channel, '_server_up', lambda: True)
    channel._ensure_server()
    fake.forwards[('s', collector.PORT)] = 1717
    instance.close()
    assert fake.forwards == {('s', collector.PORT): 1717}
    assert not any('--remove' in c for c in fake.calls)


def test_collector_reopen_rejects_replaced_forward(monkeypatch):
    import collector

    instance, fake = collector_with_fake_adb(monkeypatch)
    channel = collector.TcpChannel(instance.core.mc, port=collector.PORT)
    monkeypatch.setattr(channel, '_push_memsrv', lambda: True)
    monkeypatch.setattr(channel, '_server_up', lambda: True)
    channel._ensure_server()
    fake.forwards[('s', collector.PORT)] = 1717
    with pytest.raises(conn.CommandError):
        channel._ensure_server()
    instance.close()
    assert fake.forwards == {('s', collector.PORT): 1717}
