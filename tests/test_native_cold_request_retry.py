"""Owned native capture transaction; no DLL or game process."""
from pathlib import Path
from hashlib import sha256
import os
import shutil
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]

def test_cold_retry_survives_request_deadlines_and_requires_fresh_closed_repair(tmp_path):
    compiler=shutil.which('g++')
    header=Path(os.environ.get('ARKDPS_NATIVE_JSON_HEADER',str(ROOT/'runtime/async-build/stage/json.hpp')))
    if compiler is None or not header.is_file():
        pytest.skip('requires compiler and verified cached JSON header; no download')
    assert sha256(header.read_bytes()).hexdigest()=='9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'
    source=ROOT/'native_pc/native_event_runtime';binary=tmp_path/'cold-retry.exe'
    build=subprocess.run([compiler,'-std=c++17','-static','-O2','-pthread','-Wall','-Wextra','-Werror',
        '-I',str(source),'-I',str(header.parent),str(ROOT/'tests/native_cold_request_retry_owned.cpp'),
        str(source/'native_capture.cpp'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
    assert build.returncode==0,build.stdout+build.stderr
    result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,(result.returncode,result.stdout,result.stderr)
    assert 'PASS actual cold request handoff' in result.stdout
