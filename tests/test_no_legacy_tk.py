"""The Tk panel is sealed: no active code may import or revive it."""
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'archive' / 'legacy-tk'
SEALED_MODULES = frozenset({
    'tkinter', 'gui_panel', 'gui_history', 'hud', 'settings_window',
    'tray', 'ui_theme', 'statistics_screenshot', 'release_smoke', 'release_probe',
})
# app.py is the one place allowed to name the removed flag: it must reject it.
FLAG_OWNERS = {'app.py'}


def active_python_files():
    for path in sorted(ROOT.rglob('*.py')):
        parts = set(path.relative_to(ROOT).parts)
        if parts & {'archive', 'vendor', '.git', '__pycache__', 'runtime'}:
            continue
        yield path


def top_level_imports(path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split('.')[0]


def test_no_active_module_imports_sealed_tk_code():
    offenders = {}
    for path in active_python_files():
        sealed = sorted(set(top_level_imports(path)) & SEALED_MODULES)
        if sealed:
            offenders[path.relative_to(ROOT).as_posix()] = sealed
    assert offenders == {}


def test_only_the_entry_point_names_the_removed_flag():
    # Tests may assert the rejection; product code may not offer the flag.
    offenders = []
    for path in active_python_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in FLAG_OWNERS or relative.startswith('tests/'):
            continue
        if '--legacy-tk' in path.read_text(encoding='utf-8'):
            offenders.append(relative)
    assert offenders == []


def test_sealed_code_lives_only_in_the_archive():
    assert ARCHIVE.is_dir()
    for name in ('app_tk.py', 'gui_panel.py', 'gui_history.py', 'hud.py',
                 'settings_window.py', 'tray.py', 'ui_theme.py',
                 'statistics_screenshot.py', 'release_smoke.py', 'release_probe.py'):
        assert (ARCHIVE / name).is_file(), name
        assert not (ROOT / name).exists(), name
    # Nothing archived may be importable from the active tree.
    for module in SEALED_MODULES - {'tkinter'}:
        assert importlib.util.find_spec(module) is None, module


def test_pytest_does_not_collect_archived_legacy_tests():
    collected = subprocess.run(
        [sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider'],
        cwd=ROOT, capture_output=True, text=True, timeout=600)
    combined = collected.stdout + collected.stderr
    assert collected.returncode == 0, combined
    collected_files = {line.split('::', 1)[0] for line in collected.stdout.splitlines()
                       if '::' in line}
    assert not [name for name in collected_files if name.startswith('archive')], collected_files
    assert not [name for name in collected_files if 'legacy-tk' in name], collected_files
    for name in ('test_gui.py', 'test_hud.py', 'test_tray.py', 'test_ui_theme.py',
                 'test_gui_history.py', 'test_window_restore.py', 'test_settings.py',
                 'test_settings_lifecycle_regressions.py'):
        assert not [path for path in collected_files if path.endswith(name)], name


def test_portable_package_never_ships_sealed_modules():
    spec = importlib.util.spec_from_file_location(
        'package_windows', ROOT / 'tools' / 'package_windows.py')
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    shipped = {name.rsplit('.', 1)[0] for name in package.BACKEND}
    assert not (shipped & SEALED_MODULES), sorted(shipped & SEALED_MODULES)
    for name in package.BACKEND:
        assert (ROOT / name).is_file(), name


def test_app_import_never_loads_tkinter():
    result = subprocess.run([sys.executable, '-c',
        "import app, sys; assert 'tkinter' not in sys.modules"],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('flag', ['--legacy-tk', '--acceptance-output', '--self-test'])
def test_removed_flags_exit_nonzero_with_wpf_pointer(flag):
    result = subprocess.run([sys.executable, 'app.py', flag, 'out'], cwd=ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 2
    assert '已封存' in result.stderr and 'verify_wpf' in result.stderr
