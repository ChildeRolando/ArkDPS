"""Startup-test packaging is isolated, offline-checked and never a release claim."""
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from native_pc import native_hook_source as hooks

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_hooks_packager', ROOT / 'tools/package_windows.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
HEAD = 'a' * 40


def test_canonical_event_store_is_in_every_backend_package_inventory():
    assert package.BACKEND.count('event_probe_store.js') == 1
    assert (ROOT / 'event_probe_store.js').is_file()


def test_archive_variant_preserves_existing_names():
    assert package.archive_name('1.2.3', False, with_native_hooks=True) == 'ArkDPS-1.2.3-win-x64-native-hooks-startup-test.zip'
    assert package.archive_name('1.2.3', True) == 'ArkDPS-1.2.3-win-x64.zip'
    assert package.archive_name('1.2.3', False) == 'ArkDPS-1.2.3-win-x64-snapshot.zip'
    assert package.archive_name('1.2.3', True, with_native_events=True).endswith('-native-events.zip')
    assert package.archive_name('1.2.3', False, internal_backend=True).endswith('-internal-events.zip')


@pytest.mark.parametrize('options', [
    dict(with_native_hooks=True),
    dict(with_native_hooks=True, with_events=False),
    dict(with_native_hooks=True, with_events=False, internal_backend=True, native_hook_dll=Path('x.dll')),
    dict(with_native_hooks=True, with_native_events=True, native_hook_dll=Path('x.dll')),
    dict(with_events=False, native_hook_dll=Path('x.dll')),
    dict(with_native_hooks=True, with_events=False, native_hook_dll=Path('x.dll'), frida_wheel=Path('x.whl')),
    dict(with_native_hooks=True, with_events=False, native_hook_dll=Path('x.dll'), frida_gadget_archive=Path('x.xz')),
    dict(with_native_hooks=True, with_events=False, native_hook_dll=Path('x.dll'), frida_server=Path('x.xz')),
    dict(with_native_hooks=True, with_events=False, native_hook_dll=Path('x.dll'), frida_server_license=Path('COPYING')),
])
def test_invalid_variants_fail_before_any_inputs(tmp_path, monkeypatch, options):
    monkeypatch.setattr(package, 'candidate_head', lambda root: HEAD)
    monkeypatch.setattr(package, 'pinned_input', lambda *a, **kw: pytest.fail('input/network reached'))
    with pytest.raises(ValueError):
        package.build(tmp_path / 'output', **options)
    assert not (tmp_path / 'output').exists()


def test_dirty_candidate_gate_still_precedes_native_inputs(tmp_path, monkeypatch):
    def dirty(root):
        raise RuntimeError('clean committed candidate required')
    monkeypatch.setattr(package, 'candidate_head', dirty)
    monkeypatch.setattr(package, 'pinned_input', lambda *a, **kw: pytest.fail('input/network reached'))
    with pytest.raises(RuntimeError, match='clean committed'):
        package.build(tmp_path / 'output', with_events=False, with_native_hooks=True, native_hook_dll=Path('x.dll'))


def payload(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    source = root / 'native_pc/native_event_runtime'
    source.mkdir(parents=True)
    for name in hooks.RUNTIME_SOURCES:
        (source / name).write_text('owned-source-' + name)
    # Unrelated caches must never become part of the distributable runtime.
    (source / 'cache-secret.txt').write_text('not a payload')
    external = tmp_path / 'external'
    external.mkdir()
    dll = external / hooks.DLL_NAME
    dll.write_bytes(b'owned-synthetic-dll-not-executed')
    licenses = {name: hashlib.sha256(('owned-' + name).encode()).hexdigest() for name in hooks.NATIVE_HOOK_LICENSES}
    monkeypatch.setattr(hooks, 'NATIVE_HOOK_LICENSES', licenses)
    for name in licenses:
        (external / name).write_text('owned-' + name)
    manifest = dict(provider=hooks.PROVIDER, protocol=1, sha256=package.digest(dll),
                    sources={name: package.digest(source / name) for name in hooks.RUNTIME_SOURCES},
                    dependencies=list(hooks.NATIVE_HOOK_DEPENDENCIES), licenses=licenses)
    dll.with_suffix('.build.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(package, 'ROOT', root)
    return root, dll, manifest


def test_payload_uses_owner_validator_and_exact_layout(tmp_path, monkeypatch):
    root, dll, manifest = payload(tmp_path, monkeypatch)
    calls = []
    original = hooks.validate_native_hook_payload
    def checked(binary, **kwargs):
        calls.append((binary, kwargs))
        return original(binary, **kwargs)
    monkeypatch.setattr(hooks, 'validate_native_hook_payload', checked)
    collector, notices = tmp_path / 'bundle/collector', tmp_path / 'bundle/licenses'
    collector.mkdir(parents=True)
    notices.mkdir()
    package.stage_native_hook_payload(dll, collector, notices)
    native = collector / 'native_pc/native_event_runtime'
    assert {p.name for p in native.iterdir()} == set(hooks.RUNTIME_SOURCES) | {hooks.DLL_NAME, 'ArkDPS.NativeEvents.build.json'}
    assert (native / hooks.DLL_NAME).read_bytes() == dll.read_bytes()
    assert json.loads((native / 'ArkDPS.NativeEvents.build.json').read_text()) == manifest
    assert {p.name for p in notices.iterdir()} == set(hooks.NATIVE_HOOK_LICENSES)
    assert calls[0][0] == dll
    assert calls[0][1]['source_dir'] == root / 'native_pc/native_event_runtime'
    assert calls[0][1]['license_dir'] == dll.parent
    assert len(calls) == 2  # Validate the copied bytes too, before any manifest is published.


@pytest.mark.parametrize('fault', ['source', 'dll', 'manifest', 'license', 'dependency', 'missing-license'])
def test_payload_rejects_stale_or_unpinned_inputs(tmp_path, monkeypatch, fault):
    root, dll, manifest = payload(tmp_path, monkeypatch)
    if fault == 'source':
        (root / 'native_pc/native_event_runtime/engine.cpp').write_text('changed')
    elif fault == 'dll':
        dll.write_bytes(b'changed')
    elif fault == 'manifest':
        manifest['protocol'] = True
        dll.with_suffix('.build.json').write_text(json.dumps(manifest))
    elif fault == 'dependency':
        manifest['dependencies'] = []
        dll.with_suffix('.build.json').write_text(json.dumps(manifest))
    elif fault == 'missing-license':
        (dll.parent / next(iter(hooks.NATIVE_HOOK_LICENSES))).unlink()
    else:
        (dll.parent / next(iter(hooks.NATIVE_HOOK_LICENSES))).write_text('changed')
    collector, notices = tmp_path / 'collector', tmp_path / 'licenses'
    collector.mkdir()
    notices.mkdir()
    with pytest.raises(RuntimeError):
        package.stage_native_hook_payload(dll, collector, notices)
    assert not (collector / 'native_pc/native_event_runtime').exists()


@pytest.mark.parametrize('name', [
    'python/Lib/site-packages/frida/__init__.py', 'python/Lib/site-packages/_frida.pyd',
    'python/Lib/site-packages/frida-17.dist-info/METADATA',
    'collector/vendor/frida/17/FridaGadget.dll', 'collector/frida-server/frida-server.xz',
    'collector/native_pc/frida_runtime.py',
])
def test_no_frida_payload_assertion(name):
    with pytest.raises(RuntimeError, match='Frida'):
        package.assert_no_frida_payload([name])
    package.assert_no_frida_payload(['collector/frida_provision.py', hooks.NATIVE_HOOK_PACKAGE_PATH])


def test_metadata_binds_only_startup_test_component():
    files = {hooks.NATIVE_HOOK_PACKAGE_PATH: 'a' * 64, hooks.NATIVE_HOOK_BUILD_MANIFEST_PATH: 'b' * 64}
    value = package.native_hook_component(files)
    from native_pc.probe_native_pc import KNOWN_BUILDS
    assert value == dict(provider='arkdps-native-hooks', protocol=1, scope='official-pc-startup-test',
                         path=hooks.NATIVE_HOOK_PACKAGE_PATH, build_manifest_path=hooks.NATIVE_HOOK_BUILD_MANIFEST_PATH,
                         sha256='a' * 64, build_manifest_sha256='b' * 64,
                         default_platform='native_pc', default_backend='native', supported_source_builds=list(KNOWN_BUILDS),
                         experimental=True, performance_verified=False, autochess_handoff_verified=False)


def replies():
    return [dict(kind='hello', ok=True, result=dict(frida=None, native_hook_candidate=dict(provider='arkdps-native-hooks'))),
            dict(kind='snapshot', ok=True, state=dict(effective_connection_config=dict(
                platform='native_pc', native_pc_event_backend='native', native_pc_event_feed=None))),
            dict(kind='bye', ok=True)]


def test_safe_candidate_check_runs_only_embedded_python_offline(tmp_path, monkeypatch):
    bundle, inputs = tmp_path / 'bundle', tmp_path / 'inputs'
    bundle.mkdir()
    inputs.mkdir()
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[0] == str(bundle / 'python/python.exe')
        return SimpleNamespace(stdout='\n'.join(json.dumps(v) for v in replies()), returncode=0)
    monkeypatch.setattr(package.subprocess, 'run', run)
    result = package.verify_native_hooks_candidate(bundle, inputs, {})
    assert len(calls) == 2  # imports/manifest, then worker hello/snapshot/quit
    assert '--offline' in calls[1][0]
    assert 'runpy.run_path' in calls[1][0][calls[1][0].index('-c') + 1]
    for command, kwargs in calls:
        assert 'import frida' not in command
        assert kwargs['timeout'] <= 30
        script = command[command.index('-c') + 1]
        assert 'MetaPathFinder' in script and 'native_pc.frida_runtime' in script
    assert [json.loads(line)['op'] for line in calls[1][1]['input'].splitlines()] == ['hello', 'snapshot', 'quit']
    assert result['game_connected'] is False and result['desktop_started'] is False
    assert result['performance_verified'] is False and result['autochess_handoff_verified'] is False


@pytest.mark.parametrize('fault', ['frida', 'platform', 'backend', 'feed', 'failed', 'extra', 'missing-frida', 'missing-feed', 'integer-ok'])
def test_safe_candidate_check_rejects_wrong_worker_profile(tmp_path, monkeypatch, fault):
    values = replies()
    if fault == 'frida':
        values[0]['result']['frida'] = '17.26.1'
    elif fault in ('platform', 'backend', 'feed'):
        key = dict(platform='platform', backend='native_pc_event_backend', feed='native_pc_event_feed')[fault]
        values[1]['state']['effective_connection_config'][key] = dict(platform='mumu', backend='frida', feed='file.jsonl')[fault]
    elif fault == 'extra':
        values.append(dict(kind='snapshot', ok=True))
    elif fault == 'missing-frida':
        del values[0]['result']['frida']
    elif fault == 'missing-feed':
        del values[1]['state']['effective_connection_config']['native_pc_event_feed']
    elif fault == 'integer-ok':
        values[1]['ok'] = 1
    else:
        values[1]['ok'] = False
    monkeypatch.setattr(package.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        stdout='\n'.join(json.dumps(v) for v in values), returncode=0))
    with pytest.raises(RuntimeError):
        package.verify_native_hooks_candidate(tmp_path / 'bundle', tmp_path / 'inputs', {})


def test_cli_selects_native_hooks_without_frida(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(package, 'build', lambda output, **kw: calls.append((output, kw)))
    monkeypatch.setattr(package.sys, 'argv', ['package_windows.py', '--with-native-hooks', '--native-hook-dll', 'owned.dll', '--output', str(tmp_path)])
    package.main()
    assert calls[0][1]['with_events'] is False
    assert calls[0][1]['with_native_hooks'] is True
    assert calls[0][1]['native_hook_dll'] == Path('owned.dll')


def test_native_hooks_readme_does_not_migrate_old_state():
    text = package.native_hooks_readme('1.2.3')
    for phrase in ('启动测试', '先启动 ArkDPS', 'ArkPie 监听已就绪，请启动游戏', '尚未预载 DLL',
                   '再正常启动游戏', '不包含 Frida', '单独', '不要复制',
                   '性能尚未验收', '卫戍', 'partial', '不保证', '未签名', '不要关闭', '不自动上传'):
        assert phrase in text
    assert '将整个 runtime 目录复制' not in text
    assert 'wpf-startup.log' in text and 'wpf-collector' in text and 'token' in text


def fake_build_environment(tmp_path, monkeypatch):
    """Exercise the real copy/manifest/ZIP path, never dotnet or embedded code."""
    root, dll, _ = payload(tmp_path, monkeypatch)
    for name in (*package.BACKEND, 'app.py', 'desktop_launcher.py', 'release_identity.py', 'LICENSE', 'THIRD_PARTY.md'):
        (root / name).write_text('# owned-test-source ' + name)
    for directory in ('assets', 'vendor'):
        (root / directory).mkdir()
    # Copytree must not leak local caches, the alternative backend or old host.
    for name in ('native_hook_source.py', 'native_hook_runtime.py', 'internal_event_source.py',
                 'internal_capture.py', 'frida_runtime.py', 'live_event_bridge.py', 'live_event_producer.py', '__init__.py'):
        (root / 'native_pc' / name).write_text('# owned-test ' + name)
    for directory in ('native_pc/arkframework', 'native_pc/internal_event_host', 'vendor/frida'):
        (root / directory).mkdir(parents=True)
        (root / directory / 'cached.dll').write_bytes(b'forbidden-cache')
    python_zip = tmp_path / 'python.zip'
    with zipfile.ZipFile(python_zip, 'w') as stream:
        stream.writestr('python.exe', 'not executed')
        stream.writestr('python312.zip', 'not executed')
        stream.writestr('LICENSE.txt', 'owned python notice')
    dotnet = tmp_path / 'dotnet'
    dotnet.mkdir()
    for name in ('dotnet.exe', 'LICENSE.txt', 'ThirdPartyNotices.txt'):
        (dotnet / name).write_text('owned dotnet ' + name)
    monkeypatch.setenv('ARKDPS_DOTNET', str(dotnet / 'dotnet.exe'))
    gates, inputs, runs, verifications = [], [], [], []
    monkeypatch.setattr(package, 'candidate_head', lambda source: gates.append(source) or HEAD)
    def pinned(path, **kwargs):
        inputs.append(kwargs)
        assert kwargs['url'] == package.PYTHON_URL  # Never even request Frida inputs.
        return python_zip
    monkeypatch.setattr(package, 'pinned_input', pinned)
    monkeypatch.setattr(package.subprocess, 'check_output', lambda command, **kw: '10.0.100\n')
    def run(command, **kwargs):
        runs.append((command, kwargs))
        if command[0] == str(dotnet / 'dotnet.exe'):
            assert command[1] == 'publish'
            bundle = Path(command[command.index('-o') + 1])
            (bundle / 'ArkDPS.Desktop.exe').write_bytes(b'owned-publish-stub')
            artifacts = Path(command[command.index('--artifacts-path') + 1]) / 'bin/ArkDPS.Desktop/release'
            artifacts.mkdir(parents=True)
            (artifacts / 'ArkDPS.Desktop.runtimeconfig.json').write_text('{}')
        else:
            assert Path(command[0]).name == 'python.exe'
        return SimpleNamespace(stdout='', returncode=0)
    monkeypatch.setattr(package.subprocess, 'run', run)
    monkeypatch.setattr(package, 'verify_executable_manifest', lambda exe, **kw: verifications.append((exe, kw)))
    def safe_verify(bundle, directory, environment):
        release = json.loads((bundle / 'metadata/release.json').read_text())
        assert package.native_hook_component(release['files']) == release['native_pc_native_hooks']
        return dict(scope='owned-test-double-offline-not-live', game_connected=False, desktop_started=False)
    monkeypatch.setattr(package, 'verify_native_hooks_candidate', safe_verify)
    monkeypatch.setattr(package, 'verify_internal_candidate', lambda *a, **kw: pytest.fail('WPF/internal check reached'))
    monkeypatch.setattr(package, 'verify_cooperative_candidate', lambda *a, **kw: pytest.fail('cooperative check reached'))
    monkeypatch.setattr(package, 'stage_gadget', lambda *a, **kw: pytest.fail('Gadget staging reached'))
    return SimpleNamespace(root=root, dll=dll, python_zip=python_zip, gates=gates, inputs=inputs,
                           runs=runs, verifications=verifications)


def test_native_hooks_build_integrates_payload_metadata_and_safe_clean_extract(tmp_path, monkeypatch):
    env = fake_build_environment(tmp_path, monkeypatch)
    output = tmp_path / 'output'
    bundle = package.build(output, version='1.0.0-rc.2026112401', python_zip=env.python_zip,
                           with_events=False, with_native_hooks=True, native_hook_dll=env.dll)
    release = json.loads((bundle / 'metadata/release.json').read_text())
    files = release['files']
    assert env.gates == [env.root]
    assert len(env.inputs) == 1
    assert '-p:ArkDpsNativeHookCandidate=true' in env.runs[0][0]
    assert env.verifications[0][1] == dict(internal_backend=False)
    assert release['frida'] is None and release['frida_wheel_sha256'] is None and release['native_pc_gadget'] is None
    assert not any(key in release for key in ('frida_server', 'update_protocol', 'native_pc_event_backend', 'internal_event_backend'))
    for name in ('app.py', 'desktop_launcher.py', 'release_identity.py',
                 'native_pc/native_hook_source.py', 'native_pc/native_hook_runtime.py', 'native_pc/internal_event_source.py'):
        assert 'collector/' + name in files
    assert all('cache-secret' not in name and 'cached.dll' not in name for name in files)
    for name in ('internal_capture.py', 'frida_runtime.py', 'live_event_bridge.py', 'live_event_producer.py'):
        assert 'collector/native_pc/' + name not in files
    package.assert_no_frida_payload(files)
    assert all('MetaPathFinder' in cmd[cmd.index('-c') + 1] for cmd, _ in env.runs[1:])
    archive = output / package.archive_name('1.0.0-rc.2026112401', False, with_native_hooks=True)
    assert archive.is_file()
    assert (output / (archive.name + '.sha256')).read_text().startswith(package.digest(archive))
    evidence = json.loads((output / (archive.name + '.acceptance.json')).read_text())
    assert evidence['backend'] == release['native_pc_native_hooks']
    assert evidence['scenarios']['startup']['game_connected'] is False
    assert {p.name for p in bundle.iterdir()} == package.PACKAGE_ROOT


def test_standard_snapshot_build_does_not_gain_candidate_payload_or_flag(tmp_path, monkeypatch):
    env = fake_build_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(package, 'stage_native_hook_payload', lambda *a, **kw: pytest.fail('native hook staging reached'))
    monkeypatch.setattr(package, 'verify_native_hooks_candidate', lambda *a, **kw: pytest.fail('candidate check reached'))
    bundle = package.build(tmp_path / 'output', python_zip=env.python_zip, with_events=False)
    release = json.loads((bundle / 'metadata/release.json').read_text())
    assert 'native_pc_native_hooks' not in release
    assert '-p:ArkDpsNativeHookCandidate=true' not in env.runs[0][0]
    assert 'collector/app.py' not in release['files']
    assert 'collector/desktop_launcher.py' not in release['files']
    assert 'collector/native_pc/native_hook_source.py' not in release['files']
    assert 'collector/native_pc/native_hook_runtime.py' not in release['files']
    assert not (bundle / 'collector/native_pc/native_event_runtime').exists()
    assert '将整个 runtime 目录复制' in (bundle / 'README.txt').read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('other', ['--without-events', '--internal-events', '--with-native-events'])
def test_cli_native_hooks_is_mutually_exclusive(monkeypatch, other):
    monkeypatch.setattr(package.sys, 'argv', ['package_windows.py', '--with-native-hooks', other])
    with pytest.raises(SystemExit) as error:
        package.main()
    assert error.value.code == 2


def test_standard_release_ships_both_backends_and_update_protocol(tmp_path, monkeypatch):
    env = fake_build_environment(tmp_path, monkeypatch)
    wheel = tmp_path / 'frida.whl'
    with zipfile.ZipFile(wheel, 'w') as stream:
        stream.writestr('frida/__init__.py', '# owned fixture')
    server, license_file = tmp_path / 'server.xz', tmp_path / 'COPYING'
    server.write_bytes(b'owned-server')
    license_file.write_bytes(b'owned-license')
    (env.root / 'vendor/frida/COPYING').write_text('owned Gadget license')
    monkeypatch.setattr(package, 'pinned_input', lambda path, **kw: path)
    monkeypatch.setattr(package, 'check', lambda *a: None)
    def gadget(destination, **kwargs):
        destination.mkdir(parents=True)
        (destination / 'FridaGadget.dll').write_bytes(b'owned-gadget')
    monkeypatch.setattr(package, 'stage_gadget', gadget)
    monkeypatch.setattr(package, 'verify_native_hooks_release', lambda *a: {'verified': True})
    output = tmp_path / 'output'
    bundle = package.build(output, python_zip=env.python_zip, frida_wheel=wheel,
        frida_server=server, frida_server_license=license_file, native_hook_dll=env.dll)
    release = json.loads((bundle / 'metadata/release.json').read_text())
    component = release['native_pc_native_hooks']
    assert component['scope'] == 'official-pc' and component['experimental'] is False
    assert component['performance_verified'] is False and component['autochess_handoff_verified'] is False
    assert release['update_protocol'] == 1 and release['frida'] == package.FRIDA_VERSION
    for name in ('frida_runtime.py', 'native_hook_runtime.py', 'native_hook_source.py', 'internal_event_source.py'):
        assert 'collector/native_pc/' + name in release['files']
    assert 'python/Lib/site-packages/frida/__init__.py' in release['files']
    assert not any('ArkDpsNativeHookCandidate' in part for part in env.runs[0][0])
    readme = (bundle / 'README.txt').read_text('utf-8-sig')
    assert '检测到环境存在异常' in readme
    assert '启动测试包' not in readme and '性能尚未验收' not in readme


@pytest.mark.parametrize('released', [True, False])
def test_distribution_profile_never_implies_live_acceptance(released):
    from native_pc.native_hook_source import NATIVE_HOOK_PACKAGE_PATH, NATIVE_HOOK_BUILD_MANIFEST_PATH
    component = package.native_hook_component({NATIVE_HOOK_PACKAGE_PATH: 'a' * 64,
        NATIVE_HOOK_BUILD_MANIFEST_PATH: 'b' * 64}, released=released)
    assert component['performance_verified'] is False
    assert component['autochess_handoff_verified'] is False
