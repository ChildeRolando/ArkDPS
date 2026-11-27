"""Real lane manager with owned payloads and thread lifetimes; no DLL/game."""
from pathlib import Path
import shutil
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]
def test_native_lane_spill_integration(tmp_path):
    compiler=shutil.which('g++')
    if compiler is None: pytest.skip('owned native lane test requires g++')
    binary=tmp_path/'lane-spill.exe'
    build=subprocess.run([compiler,'-std=c++17','-static','-O2','-pthread','-Wall','-Wextra','-Werror',
        '-I',str(ROOT/'native_pc/native_event_runtime'),str(ROOT/'tests/native_lane_spill_integration.cpp'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
    assert build.returncode==0,build.stdout+build.stderr
    result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.returncode,result.stdout,result.stderr)
    assert 'lane spill integration:' in result.stdout
    print(result.stdout.strip())
