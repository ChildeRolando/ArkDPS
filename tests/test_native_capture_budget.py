"""Queue budget/storage contract only; never load a game or UI."""
from pathlib import Path
import os,shutil,subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('scope',['portable','hints','windows'])
def test_native_capture_budget_contract(tmp_path,scope):
    if scope=='windows'and os.name!='nt':pytest.skip('Windows Lane identity/VirtualFree commitment fixture; portable queue is tested separately')
    compiler=shutil.which('g++')
    if compiler is None:pytest.skip('owned capture budget test requires g++')
    binary=tmp_path/'capture-budget.exe'
    filename={'portable':'native_capture_budget_portable.cpp','hints':'native_capture_budget_hint_test.cpp','windows':'native_capture_budget_test.cpp'}[scope]
    command=[compiler,'-std=c++17','-O2','-static','-pthread','-Wall','-Wextra','-Werror','-I',str(ROOT/'native_pc/native_event_runtime'),str(ROOT/'tests'/filename)]
    if scope=='windows':command+=['-lpsapi']
    if scope=='hints':command+=['-fno-access-control']
    build=subprocess.run([*command,'-o',str(binary)],capture_output=True,text=True,timeout=60)
    assert build.returncode==0,build.stdout+build.stderr
    run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=40)
    assert run.returncode==0,(run.returncode,run.stdout,run.stderr)
    assert 'PASS capture budget:'in run.stdout
