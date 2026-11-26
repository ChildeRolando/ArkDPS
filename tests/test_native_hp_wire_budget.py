"""Measure production-generated HP plans using the actual native scope pipeline."""
from pathlib import Path
from hashlib import sha256
import json
import os
import shutil
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]
JSON_SHA='9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'

def test_actual_hp_warm_plan_wire_budget(tmp_path):
    compiler,node=shutil.which('g++'),shutil.which('node')
    if compiler is None or node is None:
        pytest.skip('owned HP plan measurement requires g++ and Node')
    candidates=[Path(os.environ['ARKDPS_NATIVE_JSON_HEADER'])] if 'ARKDPS_NATIVE_JSON_HEADER' in os.environ else [
        ROOT/'runtime/native-pc/native-event-runtime/dependencies/json.hpp',
        ROOT/'runtime/async-build/dependencies/json.hpp',ROOT/'runtime/async-build/stage/json.hpp']
    header=next((p for p in candidates if p.is_file()),None)
    if header is None:
        pytest.skip('requires verified cached json.hpp; set ARKDPS_NATIVE_JSON_HEADER (no download)')
    assert header.name=='json.hpp' and sha256(header.read_bytes()).hexdigest()==JSON_SHA
    cold,plan=tmp_path/'cold.json',tmp_path/'actual-hp.json'
    for command in ([node,'tests/native_pc_cold_plan_fixture.js',str(cold)],
                    [node,'tests/native_hp_wire_budget_export.js',str(plan),str(cold)]):
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stdout+result.stderr
    source=ROOT/'native_pc/native_event_runtime'
    binary=tmp_path/'hp-wire-budget.exe'
    build=subprocess.run([compiler,'-std=c++17','-static','-O2','-pthread','-Wall','-Wextra','-Werror',
        '-I',str(source),'-I',str(header.parent),str(ROOT/'tests/native_hp_wire_budget_owned.cpp'),
        str(source/'native_capture.cpp'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
    assert build.returncode==0,build.stdout+build.stderr
    result=subprocess.run([str(binary),str(plan),'--spill-pressure'],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,(result.returncode,result.stdout,result.stderr)
    receipt=json.loads(result.stdout)
    # Three immutable Character/Unit/Entity parent links now belong to the
    # verified metadata closure; dynamic identity/scope guards stay captured.
    assert receipt['fixture_only'] is True and receipt['publication_guards']==785
    plain,heal,nested=(receipt['branches'][key] for key in ('unchanged','heal','nested_maxhp_dirty_hostclaim'))
    assert plain['entry']['read_count']==12 and plain['leave']['read_count']==3
    assert plain['leave']['scope_revision']==0
    assert heal['first_max_hp']==200 and heal['leave']['scope_revision']==1
    assert nested['fresh_max_hp']==240 and nested['leave']['publication_revision']==1
    assert nested['leave']['compact_bytes']>heal['leave']['compact_bytes']>plain['leave']['compact_bytes']
    small,large=receipt['spill_pressure']
    assert small['failure_code']==4 and small['accepted_edges']<30000
    assert large['failure_code']==0 and large['accepted_edges']==30000
    assert large['peak_serialized_bytes']==15000*(heal['entry']['compact_bytes']+heal['leave']['compact_bytes'])+2*(nested['leave']['compact_bytes']-heal['leave']['compact_bytes'])
    assert large['hot_cpp_allocations']==0
    print(json.dumps(receipt,sort_keys=True))
