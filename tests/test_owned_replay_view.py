"""Owned replay reads only worker payload; no live process or VM is started."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_owned_replay_view_contract(tmp_path):
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.skip('owned view test requires g++')
    binary = tmp_path / 'owned-replay-view.exe'
    built = subprocess.run([compiler, '-std=c++17', '-static', '-O2', '-Wall', '-Wextra', '-Werror',
        '-I', str(ROOT / 'native_pc/native_event_runtime'), str(ROOT / 'tests/owned_replay_view_test.cpp'),
        '-o', str(binary)], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert 'owned replay view:' in result.stdout
    print(result.stdout.strip())
