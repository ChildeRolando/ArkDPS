"""Release provenance must work without Git and must reject stale/dirty candidates."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

import release_identity
from release_identity import candidate_head, load_manifest
import capture_cli
from test_capture_cli import Session, options, stub_git_and_clock

ROOT = Path(__file__).resolve().parents[1]
HEAD = 'a' * 40
spec = importlib.util.spec_from_file_location('package_windows', ROOT / 'tools/package_windows.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def manifest(tmp_path):
    (tmp_path / 'resource.txt').write_text('resource')
    value = dict(schema_version=1, head=HEAD, files={
        'resource.txt': hashlib.sha256(b'resource').hexdigest()})
    (tmp_path / 'build-manifest.json').write_text(json.dumps(value))
    return value


def test_manifest_hashes_reject_modified_resource(tmp_path):
    manifest(tmp_path)
    assert load_manifest(tmp_path, verify=True)['head'] == HEAD
    (tmp_path / 'resource.txt').write_text('modified')
    with pytest.raises(ValueError, match='checksum mismatch'):
        load_manifest(tmp_path, verify=True)


@pytest.mark.parametrize('field,value', [('head', 'dirty'), ('schema_version', 2),
                                        ('files', {}), ('files', {'../outside': 'a'*64}),
                                        ('files', {'C:/outside': 'a'*64}),
                                        ('files', {r'folder\outside': 'a'*64}),
                                        ('files', {'/absolute': 'a'*64})])
def test_invalid_manifests_rejected(tmp_path, field, value):
    data = manifest(tmp_path)
    data[field] = value
    (tmp_path / 'build-manifest.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_manifest(tmp_path, verify=True)


def test_candidate_head_refuses_dirty_tree_and_wrong_head(monkeypatch):
    def git(command, **kwargs):
        assert command[0] == 'git' and kwargs['cwd'] == ROOT
        return HEAD + '\n' if command[1] == 'rev-parse' else ' M app.py\n'
    monkeypatch.setattr(release_identity.subprocess, 'check_output', git)
    with pytest.raises(RuntimeError, match='clean committed'):
        candidate_head(ROOT)
    monkeypatch.setattr(release_identity.subprocess, 'check_output',
                        lambda command, **kwargs: HEAD + '\n' if command[1] == 'rev-parse' else '')
    with pytest.raises(RuntimeError, match='candidate mismatch'):
        candidate_head(ROOT, 'b' * 40)
    assert candidate_head(ROOT, HEAD) == HEAD


def test_package_rejects_any_tk_runtime_file():
    package.assert_no_tk_files(['ArkDPS.Desktop.exe', 'collector/desktop_bridge.py',
                                'python/python312.zip'])
    for name in ('python/tcl/tcl8.6/init.tcl', 'python/Lib/tkinter/__init__.py',
                 'python/DLLs/_tkinter.pyd', 'licenses/Tcl.txt'):
        with pytest.raises(RuntimeError, match='Sealed Tk'):
            package.assert_no_tk_files(['ArkDPS.Desktop.exe', name])


def test_package_backend_is_worker_import_closure_without_tk():
    assert 'gui_panel.py' not in package.BACKEND and 'ui_theme.py' not in package.BACKEND
    assert not any('tk' in name.lower() for name in package.BACKEND)
    for name in package.BACKEND:
        assert (ROOT / name).is_file(), name


def test_frozen_capture_uses_manifest_not_git(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    monkeypatch.setattr(capture_cli.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(capture_cli, '__file__', str(tmp_path / 'capture_cli.py'))
    manifest(tmp_path)
    def no_git(*args, **kwargs):
        raise AssertionError('portable artifact must not require Git')
    monkeypatch.setattr(capture_cli.subprocess, 'check_output', no_git)
    args = options(tmp_path)
    args.expect_head = HEAD
    result = capture_cli.run_capture(args, session_factory=Session)
    assert result['error'] is None
    assert json.loads((args.output_dir / 'READY.json').read_text())['head'] == HEAD


def test_frozen_capture_refuses_wrong_candidate_before_connect(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    monkeypatch.setattr(capture_cli.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(capture_cli, '__file__', str(tmp_path / 'capture_cli.py'))
    manifest(tmp_path)
    args = options(tmp_path)
    args.expect_head = 'b'*40
    with pytest.raises(RuntimeError, match='candidate mismatch'):
        capture_cli.run_capture(args, session_factory=Session)
    assert not args.output_dir.exists()


def test_ci_desktop_preparation_never_changes_user_desktop(monkeypatch):
    spec = importlib.util.spec_from_file_location('prepare_ci', ROOT / 'tools/prepare_windows_ci.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    class NoDisplayAccess:
        def __getattr__(self, name):
            raise AssertionError('user display must not be touched')
    with pytest.raises(RuntimeError, match='restricted'):
        module.prepare(NoDisplayAccess())


def test_ci_desktop_preparation_requires_verified_resolution(monkeypatch):
    spec = importlib.util.spec_from_file_location('prepare_ci', ROOT / 'tools/prepare_windows_ci.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('RUNNER_OS', 'Windows')
    class Display:
        calls = []
        dimensions = (1024, 768)
        def EnumDisplaySettingsW(self, name, index, pointer):
            pointer._obj.width, pointer._obj.height = self.dimensions
            return True
        def ChangeDisplaySettingsW(self, pointer, flag):
            self.calls.append(flag)
            if flag == 0:
                self.dimensions = (pointer._obj.width, pointer._obj.height)
            return 0
    display = Display()
    assert module.prepare(display) == dict(before=[1024, 768], after=[1920, 1080])
    assert display.calls == [2, 0]
    monkeypatch.setattr(display, 'ChangeDisplaySettingsW', lambda *args: -2)
    with pytest.raises(OSError, match='CI display change failed'):
        module.prepare(display)


@pytest.mark.parametrize('expected', [None, HEAD])
@pytest.mark.parametrize('damage', ['modified', 'missing'])
def test_frozen_capture_rejects_damaged_bundle_before_evidence_or_connect(
        tmp_path, monkeypatch, expected, damage):
    stub_git_and_clock(monkeypatch)
    monkeypatch.setattr(capture_cli.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(capture_cli, '__file__', str(tmp_path / 'capture_cli.py'))
    manifest(tmp_path)
    if damage == 'modified':
        (tmp_path / 'resource.txt').write_text('modified')
    else:
        (tmp_path / 'resource.txt').unlink()
    def no_git(*args, **kwargs):
        raise AssertionError('portable artifact must not require Git')
    monkeypatch.setattr(capture_cli.subprocess, 'check_output', no_git)
    args = options(tmp_path)
    args.expect_head = expected
    before = len(Session.instances)
    with pytest.raises((ValueError, FileNotFoundError)):
        capture_cli.run_capture(args, session_factory=Session)
    assert len(Session.instances) == before
    assert not args.output_dir.exists()


def test_clean_ci_installs_declared_png_test_decoder():
    def pins(name):
        return dict(line.split('==') for line in (ROOT/name).read_text('utf-8').splitlines()
                    if line.strip() and not line.startswith('#'))
    testing = pins('requirements-test.txt')
    assert all(part.isdigit() for part in testing['Pillow'].split('.'))
    assert 'pytest' in testing
    assert 'Pillow' not in pins('requirements-windows.txt')
    workflow = (ROOT/'.github/workflows/windows.yml').read_text('utf-8')
    assert '-r requirements-windows.txt -r requirements-test.txt' in workflow
