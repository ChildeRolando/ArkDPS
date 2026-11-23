"""Owned file fixtures only: no DLL loading, game/process access or downloads."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from native_pc import native_hook_source as module


FIXTURE_REQUIRED = (
    'app.py', 'desktop_bridge.py', 'desktop_launcher.py', 'capture_cli.py',
    'collection_session.py', 'collector.py', 'battle_lifecycle.py', 'event_capture.py',
    'event_probe.py', 'event_observer.py', 'event_stream.py', 'snapshot_adapter.py',
    'stats_core.py', 'multiplayer_battle.py', 'autochess_context.py',
    'source_classification.py', 'native_pc_collector.py',
    'native_pc/__init__.py', 'native_pc/event_source.py', 'native_pc/internal_entry.py',
    'native_pc/internal_event_source.py', 'native_pc/native_hook_source.py',
    'native_pc/native_hook_runtime.py', 'native_pc/windows_dll_loader.py',
    'native_pc/probe_native_pc.py', 'native_pc/probe_native_pc_battle.py',
    'native_pc/probe_native_pc_rows.py',
    'event_probe_native_pc.js', 'event_probe_runtime.js', 'event_probe_autochess.js',
)


def hashed(path):
    return sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def portable(tmp_path, monkeypatch):
    bundle = tmp_path / 'owned-package'
    root = bundle / 'collector'
    source_dir = root / 'native_pc/native_event_runtime'
    source_dir.mkdir(parents=True)
    for name in FIXTURE_REQUIRED:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('owned file fixture: ' + name).encode())
    for name in module.RUNTIME_SOURCES:
        (source_dir / name).write_bytes(('owned native source fixture: ' + name).encode())
    binary = source_dir / module.DLL_NAME
    binary.write_bytes(b'controlled-not-a-DLL-never-executed')
    licenses = {}
    for name in ('quickjs-LICENSE', 'minhook-LICENSE.txt', 'nlohmann-json-LICENSE.MIT'):
        path = bundle / 'licenses' / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(('owned license fixture: ' + name).encode())
        licenses[name] = hashed(path)
    monkeypatch.setattr(module, 'NATIVE_HOOK_LICENSES', dict(licenses), raising=False)
    from native_pc.native_event_runtime import build
    dependencies = [dict(file=name, url=url, sha256=digest) for name, url, digest in build.ARCHIVES + build.JSON_FILES]
    build_data = dict(provider=module.PROVIDER, protocol=1, sha256=hashed(binary), dependencies=dependencies,
        sources={name: hashed(source_dir / name) for name in module.RUNTIME_SOURCES}, licenses=licenses)
    build_path = binary.with_suffix('.build.json')
    build_path.write_text(json.dumps(build_data), encoding='utf-8')
    dll_path = binary.relative_to(bundle).as_posix()
    component = dict(provider=module.PROVIDER, protocol=1, scope='official-pc-startup-test',
        path=dll_path, sha256=hashed(binary),
        build_manifest_path=build_path.relative_to(bundle).as_posix(), build_manifest_sha256=hashed(build_path),
        experimental=True, default_platform='native_pc', default_backend='native',
        supported_source_builds=[module.BUILD_ID], performance_verified=False, autochess_handoff_verified=False)
    manifest = dict(format=2, protocol=1, head='a' * 40, frida=None, frida_wheel_sha256=None,
        native_pc_gadget=None, native_pc_native_hooks=component,
        files={p.relative_to(bundle).as_posix(): hashed(p) for p in bundle.rglob('*') if p.is_file()})
    release_path = bundle / 'metadata/release.json'
    release_path.parent.mkdir()
    release_path.write_text(json.dumps(manifest), encoding='utf-8')

    def save_release():
        release_path.write_text(json.dumps(manifest), encoding='utf-8')

    def save_build():
        build_path.write_text(json.dumps(build_data), encoding='utf-8')
        digest = hashed(build_path)
        component['build_manifest_sha256'] = digest
        manifest['files'][component['build_manifest_path']] = digest
        save_release()

    return dict(bundle=bundle, root=root, binary=binary, source_dir=source_dir,
                manifest=manifest, component=component, build_data=build_data, build_path=build_path,
                release_path=release_path, save_release=save_release, save_build=save_build)


def test_explicit_native_hooks_startup_variant_resolves_only_its_packaged_dll(portable):
    assert module.native_hook_binary(portable['root']) == portable['binary']
    # The package does not contain a development runtime fallback.
    assert not (portable['root'] / 'runtime').exists()


def test_relative_collector_root_uses_the_same_fixed_portable_layout(portable, monkeypatch):
    monkeypatch.chdir(portable['bundle'])
    assert module.native_hook_binary(Path('collector')) == portable['binary']


@pytest.mark.parametrize('variant', ['standard', 'cooperative', 'internal'])
def test_other_portable_variants_never_fall_back_to_a_development_dll(portable, variant):
    manifest = portable['manifest']
    manifest.pop('native_pc_native_hooks')
    if variant != 'standard':
        manifest['native_pc_event_backend' if variant == 'cooperative' else 'internal_event_backend'] = dict(provider='internal-test-host')
    portable['save_release']()
    fallback = portable['root'] / 'runtime/native-pc/native-event-runtime' / module.DLL_NAME
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b'must-never-be-selected')
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


@pytest.mark.parametrize('field,value', [
    ('protocol', True), ('protocol', 1.0), ('protocol', '1'), ('protocol', 2),
    ('provider', 'internal-test-host'), ('scope', 'production'),
    ('path', '../escape.dll'), ('path', 'collector\\native_pc\\native_event_runtime\\ArkDPS.NativeEvents.dll'),
    ('build_manifest_path', 'C:/escape.json'), ('experimental', 1), ('experimental', False),
    ('default_platform', 'android'), ('default_backend', 'frida'),
    ('supported_source_builds', []), ('supported_source_builds', [module.BUILD_ID, 'unknown']),
    ('performance_verified', 0), ('performance_verified', True),
    ('autochess_handoff_verified', None), ('autochess_handoff_verified', True),
    ('sha256', 'a' * 63), ('sha256', True), ('build_manifest_sha256', 'A' * 64),
])
def test_component_requires_exact_scope_paths_typed_fields_and_hashes(portable, field, value):
    portable['component'][field] = value
    portable['save_release']()
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


def test_unknown_explanatory_component_fields_do_not_override_required_gate(portable):
    portable['component']['acceptance_note'] = 'startup only, not performance verified'
    portable['save_release']()
    assert module.native_hook_binary(portable['root']) == portable['binary']


@pytest.mark.parametrize('name', FIXTURE_REQUIRED + ('native_pc/optional_extra.py',))
def test_direct_python_entry_validates_all_packaged_code_not_only_wpf_entry(portable, name):
    path = portable['root'] / name
    if name.endswith('optional_extra.py'):
        path.write_bytes(b'original extra module')
        portable['manifest']['files'][path.relative_to(portable['bundle']).as_posix()] = hashed(path)
        portable['save_release']()
    path.write_bytes(b'changed after package was created')
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


@pytest.mark.parametrize('what', ['dll', 'build', 'source', 'license', 'required_py', 'release'])
def test_missing_required_payload_never_uses_developer_files(portable, what):
    path = {'dll': portable['binary'], 'build': portable['build_path'],
            'source': portable['source_dir'] / 'bridge.js',
            'license': portable['bundle'] / 'licenses/quickjs-LICENSE',
            'required_py': portable['root'] / 'event_probe.py', 'release': portable['release_path']}[what]
    path.unlink()
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


@pytest.mark.parametrize('key,value', [('format', True), ('format', 1), ('protocol', True),
    ('head', 'not-a-commit'), ('head', 'a' * 39), ('files', []), ('files', {'../escape': 'a' * 64}),
    ('frida', '17.26.1'), ('frida_wheel_sha256', 'a' * 64), ('native_pc_gadget', {}),
    ('frida_server', {}), ('update_protocol', 1), ('native_pc_event_backend', {})])
def test_standard_manifest_contract_and_no_frida_candidate_are_enforced(portable, key, value):
    portable['manifest'][key] = value
    portable['save_release']()
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


@pytest.mark.parametrize('key', ['../escape.py', '/absolute.py', 'C:/escape.py',
                              'collector/../escape.py', 'collector\\escape.py', 'collector//empty.py'])
def test_even_unused_release_file_entries_cannot_traverse_or_alias_paths(portable, key):
    portable['manifest']['files'][key] = 'a' * 64
    portable['save_release']()
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


@pytest.mark.parametrize('name', ['python/Lib/site-packages/Frida/client.py',
    'python/Lib/site-packages/_frida.pyd', 'collector/vendor/frida/gadget.dll',
    'collector/frida-server/server.xz'])
def test_unlisted_frida_payloads_are_not_hidden_by_no_frida_metadata(portable, name):
    path = portable['bundle'] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'not executed')
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


def test_windows_case_alias_inventory_entries_are_rejected(portable):
    portable['manifest']['files']['collector/Event_Probe.py'] = portable['manifest']['files']['collector/event_probe.py']
    portable['save_release']()
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


@pytest.mark.parametrize('target', ['release', 'component', 'build', 'source_hash', 'licenses'])
def test_duplicate_json_fields_are_never_last_value_wins(portable, target):
    path = portable['release_path'] if target in ('release', 'component') else portable['build_path']
    raw = path.read_text(encoding='utf-8')
    if target == 'release':
        raw = raw.replace('"format": 2', '"format": 1, "format": 2', 1)
    elif target == 'component':
        raw = raw.replace('"experimental": true', '"experimental": false, "experimental": true', 1)
    elif target == 'build':
        raw = raw.replace('"protocol": 1', '"protocol": 2, "protocol": 1', 1)
    else:
        field = 'engine.cpp' if target == 'source_hash' else 'quickjs-LICENSE'
        raw = raw.replace('"' + field + '":', '"' + field + '": "wrong", "' + field + '":', 1)
    path.write_text(raw, encoding='utf-8')
    if path == portable['build_path']:
        digest = hashed(path)
        portable['component']['build_manifest_sha256'] = digest
        portable['manifest']['files'][portable['component']['build_manifest_path']] = digest
        portable['save_release']()
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


@pytest.mark.parametrize('fault', ['protocol_bool', 'provider', 'dll_hash', 'source_missing', 'source_extra',
    'source_hash', 'dependency_pin', 'dependency_url', 'dependency_extra', 'license_pin', 'license_extra'])
def test_build_manifest_source_pins_dependencies_and_licenses_are_checked(portable, fault):
    build = portable['build_data']
    if fault == 'protocol_bool':
        build['protocol'] = True
    elif fault == 'provider':
        build['provider'] = 'another-provider'
    elif fault == 'dll_hash':
        build['sha256'] = '0' * 64
    elif fault == 'source_missing':
        build['sources'].pop('engine.cpp')
    elif fault == 'source_extra':
        build['sources']['unknown.cpp'] = 'a' * 64
    elif fault == 'source_hash':
        build['sources']['engine.cpp'] = '0' * 64
    elif fault == 'dependency_pin':
        build['dependencies'][0]['sha256'] = '0' * 64
    elif fault == 'dependency_url':
        build['dependencies'][0]['url'] = 'https://example.invalid/untrusted'
    elif fault == 'dependency_extra':
        build['dependencies'].append(deepcopy(build['dependencies'][0]))
    elif fault == 'license_pin':
        build['licenses']['quickjs-LICENSE'] = '0' * 64
    else:
        build['licenses']['unknown-LICENSE'] = '0' * 64
    portable['save_build']()
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


def test_loader_dependency_pins_match_the_existing_builder_without_compiling():
    from native_pc.native_event_runtime import build
    expected = tuple(dict(file=name, url=url, sha256=digest) for name, url, digest in build.ARCHIVES + build.JSON_FILES)
    assert module.NATIVE_HOOK_DEPENDENCIES == expected


def test_packager_external_payload_uses_the_same_validator_without_development_runtime(portable):
    result = module.validate_native_hook_payload(portable['binary'], source_dir=portable['source_dir'],
        license_dir=portable['bundle'] / 'licenses')
    assert result == portable['build_data']
    (portable['source_dir'] / 'engine.cpp').write_bytes(b'changed after package input selection')
    with pytest.raises(RuntimeError):
        module.validate_native_hook_payload(portable['binary'], source_dir=portable['source_dir'],
            license_dir=portable['bundle'] / 'licenses')


def test_packager_validation_requires_licenses_and_pinned_dependencies_by_default(portable):
    with pytest.raises(RuntimeError):
        module.validate_native_hook_payload(portable['binary'], source_dir=portable['source_dir'])
    portable['build_data']['dependencies'][0]['sha256'] = '0' * 64
    portable['save_build']()
    with pytest.raises(RuntimeError):
        module.validate_native_hook_payload(portable['binary'], source_dir=portable['source_dir'],
            license_dir=portable['bundle'] / 'licenses')


def test_unlisted_code_file_and_missing_required_inventory_entry_are_rejected(portable):
    (portable['root'] / 'unlisted.py').write_bytes(b'not listed')
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])
    (portable['root'] / 'unlisted.py').unlink()
    portable['manifest']['files'].pop('collector/event_probe_runtime.js')
    portable['save_release']()
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


def test_windows_uppercase_unlisted_python_source_is_also_checked(portable):
    (portable['root'] / 'UNLISTED.PY').write_bytes(b'not listed')
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])


def test_required_source_link_cannot_escape_the_package(portable, tmp_path):
    source = portable['root'] / 'event_probe_runtime.js'
    outside = tmp_path / 'outside.js'
    outside.write_bytes(source.read_bytes())
    source.unlink()
    try:
        source.symlink_to(outside)
    except OSError:
        pytest.skip('local account cannot create symlinks; no privilege change requested')
    with pytest.raises(RuntimeError):
        module.native_hook_binary(portable['root'])
