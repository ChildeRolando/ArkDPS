"""Device-free routing tests; dummy payloads are not native/game acceptance."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys

import pytest

import app
import desktop_bridge
from connection import ConnectionConfig
from native_pc import internal_entry as entry


PROFILE = dict(provider='arkdps-native-hooks', protocol=1, scope='official-pc-startup-test',
    experimental=True, default_platform='native_pc', default_backend='native',
    performance_verified=False, autochess_handoff_verified=False)


@pytest.fixture
def candidate(monkeypatch, tmp_path):
    root = tmp_path / 'collector'
    root.mkdir()
    meta = tmp_path / 'metadata'
    meta.mkdir()
    manifest = meta / 'release.json'
    manifest.write_text(json.dumps(dict(native_pc_native_hooks=PROFILE)), encoding='utf-8')
    from native_pc import native_hook_source
    verified = []
    monkeypatch.setattr(native_hook_source, 'native_hook_binary', lambda path: verified.append(path) or root / 'owned-test.dll')
    for module in (app, desktop_bridge):
        monkeypatch.setattr(module, 'ROOT', root)
    return root, manifest, verified


def test_profile_uses_owning_binary_validator_and_never_changes_global_connection_default(candidate):
    root, _, verified = candidate
    saved = ConnectionConfig()
    profile = entry.native_hook_candidate_profile(root)
    effective = entry.native_hook_candidate_defaults(profile, saved)
    assert verified == [root]
    assert saved.platform == 'mumu' and saved.native_pc_event_backend == 'frida'
    assert effective.platform == 'native_pc' and effective.native_pc_event_backend == 'native'
    assert effective.native_pc_event_feed is None
    assert entry.native_hook_candidate_defaults(None, saved) is saved


@pytest.mark.parametrize('field,value', [('provider', 'frida'), ('protocol', True), ('protocol', 2),
    ('scope', 'cooperative-live'), ('experimental', False), ('default_platform', 'mumu'),
    ('default_backend', 'frida'), ('performance_verified', True), ('performance_verified', 0),
    ('autochess_handoff_verified', True), ('autochess_handoff_verified', None)])
def test_profile_cannot_promote_the_startup_candidate_or_lose_its_defaults(candidate, field, value):
    root, manifest, _ = candidate
    manifest.write_text(json.dumps(dict(native_pc_native_hooks=dict(PROFILE, **{field: value}))))
    with pytest.raises(ValueError, match='清单不一致'):
        entry.native_hook_candidate_profile(root)


@pytest.mark.parametrize('key', ['internal_event_backend', 'native_pc_event_backend'])
def test_candidate_never_combines_legacy_variants(candidate, key):
    root, manifest, _ = candidate
    manifest.write_text(json.dumps(dict(native_pc_native_hooks=PROFILE, **{key: dict(protocol=1)})))
    with pytest.raises(ValueError):
        entry.native_hook_candidate_profile(root)


def test_standard_and_cooperative_profiles_do_not_acquire_new_defaults(tmp_path):
    root = tmp_path / 'collector'
    assert entry.native_hook_candidate_profile(root) is None
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    (metadata / 'release.json').write_text('{"native_pc_event_backend":{"protocol":1}}')
    assert entry.native_hook_candidate_profile(root) is None


def test_desktop_effective_defaults_leave_saved_config_bytes_unchanged(candidate, tmp_path):
    _, _, _ = candidate
    state_dir = tmp_path / 'independent-state'
    state_dir.mkdir()
    saved = ConnectionConfig(platform='mumu', native_pc_event_backend='frida')
    path = state_dir / 'connection.json'
    path.write_text(json.dumps(saved.to_dict()))
    original = path.read_bytes()
    state = desktop_bridge.DesktopState(state_dir)
    assert state.connection.platform == 'mumu'
    assert state.effective_connection.platform == 'native_pc'
    assert state.effective_connection.native_pc_event_backend == 'native'
    assert path.read_bytes() == original
    view = state.snapshot()
    assert view['native_hook_candidate'] == PROFILE
    assert any('固定使用 ArkPie PC' in warning for warning in view['warnings'])
    assert any('此目录已有连接设置' in warning for warning in view['warnings'])


@pytest.mark.parametrize('change', [dict(platform='mumu'), dict(native_pc_event_backend='frida'),
    dict(native_pc_event_feed='C:/owned-fixture/events.jsonl')])
def test_connection_apply_rejects_unavailable_route_before_persisting(candidate, tmp_path, change):
    state = desktop_bridge.DesktopState(tmp_path / 'state')
    config = dict(state.effective_connection.to_dict(), **change)
    with pytest.raises(ValueError, match='仅支持官服 PC'):
        state.command(dict(version=1, op='connection_apply', config=config, events=True))
    assert not (state.directory / 'connection.json').exists()
    assert not (state.directory / 'layout.json').exists()


def test_connection_apply_can_save_the_pc_path_and_explicit_snapshot_mode(candidate, tmp_path):
    state = desktop_bridge.DesktopState(tmp_path / 'state')
    config = dict(state.effective_connection.to_dict(), native_game_root='C:/owned-game')
    state.command(dict(version=1, op='connection_apply', config=config, events=False))
    assert state.effective_connection.platform == 'native_pc'
    assert state.effective_connection.native_pc_event_backend == 'native'
    assert state.layout.event_observation is False


@pytest.mark.parametrize('module', [app, desktop_bridge])
@pytest.mark.parametrize('options', [['--platform', 'mumu'], ['--native-pc-event-backend', 'frida'],
    ['--native-pc-event-feed', 'C:/events.jsonl'], ['--frida-remote', '127.0.0.1:1234'],
    ['--adb', 'unused.exe'], ['--serial', 'unused'], ['--arkframework-root', 'unused'],
    ['--internal-capture', 'normal']])
def test_entry_rejects_unsupported_options_before_socket_or_launch(candidate, monkeypatch, module, options):
    monkeypatch.setattr(sys, 'argv', [module.__file__, *options])
    monkeypatch.setattr(module.socket, 'socket', lambda *_: pytest.fail('candidate rejection must precede startup'))
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


def test_app_launch_supplies_candidate_defaults_without_saving(candidate, monkeypatch):
    import desktop_launcher
    observed = []
    monkeypatch.setattr(sys, 'argv', ['app.py', '--native-game-root', 'C:/owned-game'])
    monkeypatch.setattr(desktop_launcher, 'launch', lambda args: observed.append(deepcopy(vars(args))) or 73)
    assert app.main() == 73
    assert observed[0]['platform'] == 'native_pc' and observed[0]['native_pc_event_backend'] == 'native'
    assert observed[0]['native_pc_event_feed'] is None


def test_candidate_offline_handshake_is_device_free_and_has_no_silent_fallback(candidate, tmp_path, monkeypatch):
    state_dir = tmp_path / 'offline-state'
    commands = ''.join(json.dumps(dict(op=op, id=i, version=1)) + '\n'
        for i, op in enumerate(('hello', 'snapshot', 'quit'), 1))
    output = io.StringIO()
    monkeypatch.setattr(sys, 'argv', ['desktop_bridge.py', '--offline', '--state-dir', str(state_dir)])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(commands))
    monkeypatch.setattr(sys, 'stdout', output)
    monkeypatch.setattr(desktop_bridge.socket, 'socket', lambda *_: pytest.fail('offline must not listen'))
    monkeypatch.setattr(desktop_bridge, 'CollectionWorker', lambda *_a, **_k: pytest.fail('offline must not collect'))
    desktop_bridge.main()
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [reply['kind'] for reply in replies] == ['hello', 'snapshot', 'bye']
    assert replies[0]['result']['native_hook_candidate'] == PROFILE
    effective = replies[1]['state']['effective_connection_config']
    assert effective['platform'] == 'native_pc' and effective['native_pc_event_backend'] == 'native'
    assert not effective['native_pc_event_feed']


def test_candidate_mumu_detection_is_rejected_without_device_calls(candidate, monkeypatch, tmp_path):
    state = desktop_bridge.DesktopState(tmp_path / 'state')
    monkeypatch.setattr(desktop_bridge, 'detect_instances', lambda *_a, **_k: pytest.fail('must not detect MuMu'))
    stream = io.StringIO('{"op":"hello","id":1,"version":1}\n'
        '{"op":"detect","id":2,"version":1,"platform":"mumu"}\n'
        '{"op":"quit","id":3,"version":1}\n')
    output = io.StringIO()
    desktop_bridge.serve(stream, output, state)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[1]['kind'] == 'error' and 'MuMu' in replies[1]['error']


def test_candidate_ui_is_compile_gated_and_never_enables_updates_or_frida():
    root = Path(__file__).resolve().parents[1] / 'desktop/ArkDPS.Desktop'
    app_source = (root / 'App.xaml.cs').read_text(encoding='utf-8')
    window = (root / 'MainWindow.xaml.cs').read_text(encoding='utf-8')
    xaml = (root / 'MainWindow.xaml').read_text(encoding='utf-8')
    project = (root / 'ArkDPS.Desktop.csproj').read_text(encoding='utf-8')
    assert 'ArkDpsNativeHookCandidate' in project and 'ARKDPS_NATIVE_HOOK_CANDIDATE' in app_source
    assert 'VerifyNativeHookPackage(manifest.RootElement)' in app_source
    assert 'CooperativeEventPackage || NativeHookCandidate' in app_source
    assert '!App.CooperativeEventPackage && !App.NativeHookCandidate' in window
    assert 'App.NativeHookCandidate ? "effective_connection_config"' in window
    assert 'ConnectionPlatform.IsEnabled = NativeEventBackend.IsEnabled = false' in window
    assert 'Content="ArkPie" Tag="native"' in xaml
    assert '遇到“检测到环境存在异常”报错时，选择 ArkPie。' in xaml
    assert '自研 PC 启动测试候选' not in xaml and '性能、稳定性和卫戍完整交接尚未验证' not in xaml
    assert 'bool nativeHooks =' in app_source and 'if (nativeHooks) VerifyNativeHookPackage' in app_source
    assert 'official-pc' in app_source


def test_native_startup_candidate_missing_game_root_reports_configuration_not_old_package_denial(candidate, tmp_path):
    from native_pc.event_source import PcEventSource
    root, _, verified = candidate
    source = PcEventSource(tmp_path / 'state', collector_root=root)
    factory = source.select(enabled=True, backend='native')
    with pytest.raises(RuntimeError, match='需要游戏目录.*客户端启动前准备'):
        factory(None, None)
    assert verified == [root] and source.runtime is None


@pytest.mark.parametrize('manifest', [{}, {'native_pc_event_backend': {'scope': 'cooperative-live'}}])
def test_other_portable_variants_keep_denial_without_importing_absent_hook_module(tmp_path, monkeypatch, manifest):
    import builtins
    from native_pc.event_source import PcEventSource
    root = tmp_path / 'collector'
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    (metadata / 'release.json').write_text(json.dumps(manifest))
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == 'native_pc.native_hook_source':
            pytest.fail('standard/cooperative packages do not contain this module')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    source = PcEventSource(tmp_path / 'state', collector_root=root)
    factory = source.select(enabled=True, backend='native')
    with pytest.raises(RuntimeError, match='不从便携包加载'):
        factory(None, None)
    assert source.runtime is None


def test_release_backend_keeps_saved_platform_and_backend_selectable(candidate, tmp_path):
    root, manifest, verified = candidate
    manifest.write_text(json.dumps(dict(native_pc_native_hooks=dict(PROFILE,
        scope='official-pc', experimental=False, performance_verified=True,
        autochess_handoff_verified=True))), encoding='utf-8')
    assert entry.native_hook_package_profile(root)['scope'] == 'official-pc'
    assert entry.native_hook_candidate_profile(root) is None
    state = desktop_bridge.DesktopState(tmp_path / 'release-state')
    assert state.effective_connection == state.connection
    assert state.native_hook_profile is None
    assert not any('测试' in warning or '尚未验证' in warning for warning in state.warnings)
    for platform, backend in [('native_pc', 'native'), ('native_pc', 'frida'), ('mumu', 'frida')]:
        config = dict(state.connection.to_dict(), platform=platform, native_pc_event_backend=backend)
        state.command(dict(version=1, op='connection_apply', config=config, events=True))
        assert state.effective_connection.platform == platform
        assert state.effective_connection.native_pc_event_backend == backend
    assert verified


def test_release_no_game_root_reports_configuration(candidate, tmp_path):
    from native_pc.event_source import PcEventSource
    root, manifest, verified = candidate
    manifest.write_text(json.dumps(dict(native_pc_native_hooks=dict(PROFILE,
        scope='official-pc', experimental=False, performance_verified=True,
        autochess_handoff_verified=True))), encoding='utf-8')
    source = PcEventSource(tmp_path / 'release-state', collector_root=root)
    factory = source.select(enabled=True, backend='native')
    with pytest.raises(RuntimeError, match='需要游戏目录'):
        factory(None, None)
    assert verified == [root]
