"""Owned role lookup ownership/collisions; no game or native hook installation."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_owned_role_index(tmp_path):
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.skip('owned role index requires g++')
    binary = tmp_path / 'owned-role-index.exe'
    built = subprocess.run([compiler, '-std=c++17', '-static', '-O2', '-pthread',
        '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'native_pc/native_event_runtime'),
        str(ROOT / 'tests/native_owned_role_index.cpp'), '-o', str(binary)],
        capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert 'hot zero allocation passed' in result.stdout
