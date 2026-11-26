"""Run the staged production HP consumer experiment without loading any DLL.

Requires an existing verified native build stage; never downloads/builds a DLL.
A failure is evidence against this workload, not an in-game FPS measurement.
"""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import shutil
import subprocess
from production_hp_quickjs_bundle import ROOT, bundle

def run(stage, output, count=10000, source_root=ROOT, native_root=ROOT, bridge_root=None):
    if not 2 <= count <= 100000:
        raise ValueError('bounded count must be 2..100000')
    compiler=shutil.which('g++')
    if not compiler:
        raise RuntimeError('requires existing g++')
    dependencies=[stage/f'object-{i}.o' for i in range(5)]
    if not all(p.is_file() for p in dependencies):
        raise RuntimeError('requires cached QuickJS objects 0..4 (no download)')
    if sha256((stage/'json.hpp').read_bytes()).hexdigest()!='9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6':
        raise RuntimeError('cached JSON header differs from pinned upstream')
    output.mkdir(parents=True,exist_ok=True)
    script=output/'production-hp.js'; binary=output/'production-hp.exe'
    script.write_text(bundle(count,True,source_root,bridge_root),encoding='utf-8')
    native=native_root/'native_pc/native_event_runtime'
    command=[compiler,'-std=c++17','-static','-O2','-I',str(native),'-I',str(stage/'quickjs-headers'),'-I',str(stage),str(ROOT/'tests/production_hp_quickjs.cpp'),str(native/'native_capture.cpp'),*[str(p) for p in dependencies],'-o',str(binary)]
    build=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    if build.returncode:
        raise RuntimeError(build.stdout+build.stderr)
    result=subprocess.run([str(binary),str(script)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout+result.stderr)
    report=json.loads(result.stdout)
    artifacts=[source_root/n for n in ['event_probe_runtime.js','event_probe_autochess.js','event_probe_native_pc.js']]
    artifacts+=[(bridge_root or source_root)/'native_pc/native_event_runtime/bridge.js',native/'owned_replay_view.h',native/'owned_replay_js.h',native/'native_capture.h',native/'native_capture.cpp',native/'engine_capture_plan.h',ROOT/'tests/production_hp_quickjs.cpp',ROOT/'tests/production_hp_quickjs_bundle.py',*dependencies]
    report['provenance']={str(p):sha256(p.read_bytes()).hexdigest() for p in artifacts}
    report['binary_sha256']=sha256(binary.read_bytes()).hexdigest()
    report['script_sha256']=sha256(script.read_bytes()).hexdigest()
    report['build_command']=command
    report['readiness']='NOT_FULL_WORKER_ACCEPTANCE; excluded layers remain required'
    (output/'receipt.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--count',type=int,default=10000);p.add_argument('--source-root',type=Path,default=ROOT)
    p.add_argument('--native-root',type=Path,default=ROOT);p.add_argument('--bridge-root',type=Path)
    a=p.parse_args();r=run(a.stage.resolve(),a.output.resolve(),a.count,a.source_root.resolve(),a.native_root.resolve(),a.bridge_root.resolve() if a.bridge_root else None)
    print(json.dumps({row['branch']:round(row['setters_per_second']) for row in r['results']}))
