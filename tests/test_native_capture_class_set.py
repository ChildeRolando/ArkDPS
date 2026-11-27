"""Only owned C++ memory; no DLL loading or game attachment."""
from pathlib import Path
from hashlib import sha256
import os
import shutil
import subprocess
import pytest
ROOT = Path(__file__).resolve().parents[1]

def test_native_class_set(tmp_path):
    compiler = shutil.which('g++')
    header = Path(os.environ.get('ARKDPS_NATIVE_JSON_HEADER', str(ROOT/'runtime/async-build/stage/json.hpp')))
    if compiler is None or not header.is_file():
        pytest.skip('requires compiler and verified cached ARKDPS_NATIVE_JSON_HEADER; no download')
    assert sha256(header.read_bytes()).hexdigest() == '9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'
    source=ROOT/'native_pc/native_event_runtime'
    binary=tmp_path/'class-set.exe'
    build=subprocess.run([compiler,'-std=c++17','-static','-O2','-Wall','-Wextra','-Werror','-I',str(source),'-I',str(header.parent),str(ROOT/'tests/native_capture_class_set.cpp'),str(source/'native_capture.cpp'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
    assert build.returncode==0,build.stdout+build.stderr
    run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
    assert run.returncode==0,run.stdout+run.stderr
    assert 'PASS class_set' in run.stdout
