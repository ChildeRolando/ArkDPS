import hashlib
import json
from types import SimpleNamespace

import desktop_launcher
from log_context import ENVIRONMENT_FILENAME, LogContext


def test_header_uses_safe_settings_and_actual_package_root(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    package = tmp_path / 'bundle'
    (package / 'metadata').mkdir(parents=True)
    state.mkdir()
    (package / 'metadata' / 'release.json').write_text(json.dumps({
        'version': '1.0.0-rc.34', 'head': 'a' * 40, 'protocol': 1, 'frida': '17.26.1',
        'files': {'C:/private/user-account/file': 'secret'},
    }), encoding='utf-8')
    (state / 'connection.json').write_text(json.dumps({
        'platform': 'native_pc', 'adb_path': 'C:/private/adb.exe', 'serial': 'secret-serial',
        'native_game_root': 'C:/private/Game', 'auto_detect': False, 'token': 'secret-token',
    }), encoding='utf-8')
    (state / 'layout.json').write_text(json.dumps({
        'event_observation': False, 'theme': 'C:private-theme', 'debug_mode': True,
    }), encoding='utf-8')
    monkeypatch.setattr('log_context._file_version', lambda _: '2.7.91.0')
    monkeypatch.setenv('ARKDPS_DESKTOP_RUN_ID', 'f' * 32)

    header = LogContext(state, root=package / 'collector').header('worker')

    assert header['kind'] == 'log_header'
    data = header['data']
    assert data['schema'] == 1 and data['source'] == 'worker' and data['debug_mode'] is True
    assert data['desktop_run_id'] == 'f' * 32
    assert data['build']['version'] == '1.0.0-rc.34' and data['build']['head'] == 'a' * 40
    assert data['system']['os'] and isinstance(data['system']['logical_cores'], int)
    assert data['settings']['platform'] == 'native_pc' and data['settings']['event_observation'] is False
    assert data['settings']['theme'] == 'lone-trail' and data['settings']['manual_target_configured'] is True
    assert data['environment']['game_executable_version'] == '2.7.91.0'
    assert data['environment']['provenance'] == 'local_installation'
    assert 'private' not in json.dumps(header) and 'secret' not in json.dumps(header)


def test_environment_cache_tracks_only_the_configuration_used_to_connect(tmp_path, monkeypatch):
    path = tmp_path / 'connection.json'
    first = json.dumps({'platform': 'mumu', 'serial': 'first'}).encode()
    second = json.dumps({'platform': 'mumu', 'serial': 'second'}).encode()
    monkeypatch.setattr('log_context._mumu_version', lambda path: '12.1.0.0' if path else None)
    path.write_bytes(first)
    live = LogContext(tmp_path)
    diagnostics = {'version_actual': '2.7.91', 'version_expected': '2.7.91',
                   'version_status': 'compatible', 'adb_path': 'C:/private/MuMu/adb.exe'}
    observed = live.update_environment(diagnostics, cache=True, connection_bytes=first)
    assert observed['provenance'] == 'observed' and observed['emulator_version'] == '12.1.0.0'
    cached_path = tmp_path / ENVIRONMENT_FILENAME
    assert json.loads(cached_path.read_bytes())['connection_sha256'] == hashlib.sha256(first).hexdigest()
    assert live.header('worker')['data']['environment']['provenance'] == 'observed'
    cached = LogContext(tmp_path).header('worker')['data']['environment']
    assert cached['provenance'] == 'last_observed' and cached['game_client_version'] == '2.7.91'
    path.write_bytes(second)
    assert live.header('worker')['data']['environment']['provenance'] == 'not_observed'
    live.update_environment({**diagnostics, 'version_actual': 'old-run'}, cache=True, connection_bytes=first)
    assert json.loads(cached_path.read_bytes())['environment']['game_client_version'] == '2.7.91'
    assert LogContext(tmp_path).header('worker')['data']['environment']['provenance'] == 'not_observed'


def test_source_build_log_is_identifiable_without_launching_desktop(tmp_path, monkeypatch):
    project = tmp_path / 'desktop' / 'dummy.csproj'
    project.parent.mkdir()
    project.touch()
    monkeypatch.setattr(desktop_launcher, 'ROOT', tmp_path)
    monkeypatch.setattr(desktop_launcher, 'PROJECT', project)
    monkeypatch.setattr(desktop_launcher, 'EXECUTABLE', project.with_name('app.exe'))
    monkeypatch.setenv('ARKDPS_DOTNET', 'test-dotnet')
    monkeypatch.setattr(desktop_launcher, 'run_desktop', lambda *_args: 0)

    def build(command, *, stdout, **_kwargs):
        assert command[0] == 'test-dotnet'
        stdout.write('test build output\n')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(desktop_launcher.subprocess, 'run', build)
    assert desktop_launcher.launch() == 0
    first, output = (tmp_path / 'runtime' / 'wpf-build.log').read_text(encoding='utf-8').splitlines()
    assert output == 'test build output'
    assert json.loads(first)['data']['source'] == 'wpf_build'
