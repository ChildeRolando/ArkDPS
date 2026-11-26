"""Compile and exercise only owned memory; never load a hook DLL or touch a game."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_native_capture_owned_memory_contract(tmp_path):
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.skip('owned C++ capture test requires g++')
    binary = tmp_path / 'owned-capture.exe'
    source = ROOT / 'native_pc/native_event_runtime'
    build = subprocess.run([compiler, '-std=c++20', '-static', '-O2', '-Wall', '-Wextra', '-Werror',
        '-I', str(source), str(ROOT / 'tests/native_capture_owned.cpp'),
        str(source / 'native_capture.cpp'), '-o', str(binary)], capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stdout + build.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert 'owned capture:' in result.stdout
