"""Actual QuickJS owned graph-program boundaries; uses an explicit existing cache."""
from pathlib import Path
from hashlib import sha256
import os
import shutil
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]


def test_graph_program_js_provider_contract():
    result=subprocess.run(['node',str(ROOT/'tests/native_pc_read_batch_fixture.js')],cwd=ROOT,
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS owned graph batch facade:' in result.stdout


@pytest.mark.parametrize(('fixture','marker'),[
    ('owned_read_plan_test.cpp','PASS owned graph programs:'),
    ('owned_hp_observation_test.cpp','PASS_RAW_HP_OBSERVATION'),
])
def test_owned_graph_program_native_boundaries(tmp_path,fixture,marker):
    compiler=shutil.which('g++');configured=os.environ.get('ARKDPS_NATIVE_QJS_STAGE')
    if compiler is None or not configured:
        pytest.skip('set ARKDPS_NATIVE_QJS_STAGE to verified existing QuickJS cache; no download')
    stage=Path(configured);objects=[stage/f'object-{i}.o' for i in range(5)];headers=stage/'quickjs-headers'
    if not all(p.is_file() for p in objects):
        objects=[stage/name for name in ('quickjs.o','dtoa.o','libregexp.o','libunicode.o','cutils.o')];headers=stage
    if not all(p.is_file() for p in objects) or not (headers/'quickjs.h').is_file() or not (stage/'json.hpp').is_file():
        pytest.skip('explicit QuickJS stage lacks existing objects/headers')
    assert sha256((stage/'json.hpp').read_bytes()).hexdigest()=='9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'
    binary=tmp_path/'owned-read-plan.exe'
    built=subprocess.run([compiler,'-std=c++17','-static','-O2','-pthread','-Wall','-Wextra','-Werror',
        '-I',str(ROOT/'native_pc/native_event_runtime'),'-isystem',str(headers),'-isystem',str(stage),
        str(ROOT/'tests'/fixture),str(ROOT/'native_pc/native_event_runtime/native_capture.cpp'),*[str(p) for p in objects],'-o',str(binary)],
        capture_output=True,text=True,timeout=60)
    assert built.returncode==0,built.stdout+built.stderr
    run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
    assert run.returncode==0,(run.returncode,run.stdout,run.stderr)
    assert marker in run.stdout


def test_hp_observation_js_provider_contract():
    result=subprocess.run(['node',str(ROOT/'tests/native_pc_hp_observation_fixture.js')],cwd=ROOT,
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS_RAW_HP_JS' in result.stdout


def test_sealed_native_provider_contract():
    result=subprocess.run(['node',str(ROOT/'tests/native_pc_sealed_provider_fixture.js')],cwd=ROOT,
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS_SEALED_PROVIDER' in result.stdout


def test_graph_primitive_provider_contract():
    result=subprocess.run(['node',str(ROOT/'tests/native_pc_graph_primitives_fixture.js')],cwd=ROOT,
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS_GRAPH_PRIMITIVES' in result.stdout


def test_graph_typed_adoption_provider_contract():
    result=subprocess.run(['node',str(ROOT/'tests/native_pc_graph_typed_adoption_fixture.js')],cwd=ROOT,
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS_GRAPH_TYPED_ADOPTION' in result.stdout
