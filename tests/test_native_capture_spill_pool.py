"""Standalone owned spill banks; never inject a DLL or access a game."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_capture_spill_pool_owned_contract(tmp_path):
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.skip('owned spill pool test requires g++')
    binary = tmp_path / 'capture-spill.exe'
    built = subprocess.run([compiler, '-std=c++17', '-static', '-O2', '-pthread',
        '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'native_pc/native_event_runtime'),
        str(ROOT / 'tests/native_capture_spill_pool_test.cpp'), '-o', str(binary)],
        capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert 'spill pool:' in result.stdout
    print(result.stdout.strip())
