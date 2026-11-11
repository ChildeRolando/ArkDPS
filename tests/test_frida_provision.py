from types import SimpleNamespace

import frida_provision as fp


def test_ensure_reports_ready_when_forward_makes_the_endpoint_reachable(monkeypatch):
    calls = []
    monkeypatch.setattr(fp, '_adb', lambda *args, **kwargs: calls.append(args) or
                        SimpleNamespace(returncode=0, stdout='', stderr=''))
    monkeypatch.setattr(fp, 'endpoint_ready', lambda *args, **kwargs: True)
    ok, detail = fp.ensure_frida_endpoint('adb', 'serial')
    assert ok and '就绪' in detail
    assert any('forward' in args[2] for args in calls)


def test_ensure_starts_an_existing_server_when_the_endpoint_is_down(monkeypatch):
    calls = []
    ready = iter([False, True])

    def adb(adb_path, serial, arguments, timeout=8):
        calls.append((adb_path, serial, arguments))
        command = ' '.join(arguments)
        if 'ps -A' in command:
            return SimpleNamespace(returncode=1, stdout='', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(fp, '_adb', adb)
    monkeypatch.setattr(fp, 'endpoint_ready', lambda *args, **kwargs: next(ready))
    monkeypatch.setattr(fp, 'time', SimpleNamespace(sleep=lambda _: None))
    ok, detail = fp.ensure_frida_endpoint('adb', 'serial')
    assert ok and '恢复' in detail
    assert any('frida-server-17.26.1' in ' '.join(args[2]) for args in calls)


def test_ensure_reports_start_failure_instead_of_claiming_success(monkeypatch):
    def adb(adb_path, serial, arguments, timeout=8):
        return SimpleNamespace(returncode=1, stdout='', stderr='permission denied')

    monkeypatch.setattr(fp, '_adb', adb)
    monkeypatch.setattr(fp, 'endpoint_ready', lambda *args, **kwargs: False)
    ok, detail = fp.ensure_frida_endpoint('adb', 'serial')
    assert not ok and 'permission denied' in detail


def test_ensure_requires_adb_and_serial(monkeypatch):
    ok, detail = fp.ensure_frida_endpoint('', 'serial')
    assert not ok and 'ADB' in detail
