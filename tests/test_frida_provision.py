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
        return SimpleNamespace(returncode=int(arguments[0] == 'shell'),
                               stdout='', stderr='permission denied')

    monkeypatch.setattr(fp, '_adb', adb)
    monkeypatch.setattr(fp, 'endpoint_ready', lambda *args, **kwargs: False)
    ok, detail = fp.ensure_frida_endpoint('adb', 'serial')
    assert not ok and 'permission denied' in detail


def test_ensure_requires_adb_and_serial(monkeypatch):
    ok, detail = fp.ensure_frida_endpoint('', 'serial')
    assert not ok and 'ADB' in detail


def test_first_binding_cannot_trust_an_open_endpoint_on_failed_forward(monkeypatch):
    checked = []
    monkeypatch.setattr(fp, '_adb', lambda *_: SimpleNamespace(
        returncode=1, stdout='', stderr='device offline'))
    monkeypatch.setattr(fp, 'endpoint_ready', lambda *args: checked.append(args) or True)
    assert fp.ensure_frida_endpoint('adb', 'serial') == (False, '建立 ADB 转发失败：device offline')
    assert checked == []


def test_periodic_health_skips_adb_when_previously_bound_endpoint_is_reachable(monkeypatch):
    checked, forwards = [], []
    monkeypatch.setattr(fp, 'endpoint_ready', lambda remote: checked.append(remote) or True)
    monkeypatch.setattr(fp, '_adb', lambda *args: forwards.append(args))
    assert fp.keep_frida_endpoint('adb', 'serial', remote='127.0.0.1:27044')[0]
    assert checked == ['127.0.0.1:27044']
    assert forwards == []


def test_periodic_health_rebinds_the_selected_serial_on_failure(monkeypatch):
    calls = []
    reachable = iter([False, True])
    monkeypatch.setattr(fp, 'endpoint_ready', lambda *_: next(reachable))
    monkeypatch.setattr(fp, '_adb', lambda *args: calls.append(args) or
                        SimpleNamespace(returncode=0, stdout='', stderr=''))
    assert fp.keep_frida_endpoint('adb', 'selected', remote='127.0.0.1:27044')[0]
    assert calls == [('adb', 'selected', ['forward', 'tcp:27044', 'tcp:27864'])]
