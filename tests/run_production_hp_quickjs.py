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

def seal_business_events(report):
    for row in report['results']:
        value=row.pop('business_events_json')
        row['business_events_sha256']=sha256(value.encode('utf-8')).hexdigest()
    return report

def run(stage, output, count=10000, source_root=ROOT, native_root=ROOT, bridge_root=None, disable_ascii=False, full_scope=False, pub_every=1000, queue_rate=None, pause_ms=0, compare_direct=False, compare_scope=False):
    if not 2 <= count <= (300000 if queue_rate else 100000):
        raise ValueError('bounded count outside experiment limit')
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
    script.write_text(bundle(count,True,source_root,bridge_root,full_scope,pub_every,{'count':count,'rate':queue_rate,'pause_ms':pause_ms,'pub_every':pub_every} if queue_rate else None),encoding='utf-8')
    native=native_root/'native_pc/native_event_runtime'
    command=[compiler,'-std=c++17','-static','-O2','-I',str(native),'-I',str(stage/'quickjs-headers'),'-I',str(stage),str(ROOT/'tests/production_hp_quickjs.cpp'),str(native/'native_capture.cpp'),*[str(p) for p in dependencies],'-o',str(binary)]
    if 'inline JSValue read_batch' in (native/'owned_replay_js.h').read_text(encoding='utf-8'):
        command.insert(1,'-DARKDPS_CAPTURE_READ_BATCH')
    if 'inline JSValue graph_read_batch' in (native/'owned_replay_js.h').read_text(encoding='utf-8'):
        command.insert(1,'-DARKDPS_CAPTURE_GRAPH_BATCH')
    if 'inline JSValue graph_check' in (native/'owned_replay_js.h').read_text(encoding='utf-8'):
        command.insert(1,'-DARKDPS_CAPTURE_GRAPH_CHECK')
    if 'inline JSValue hook_point' in (native/'owned_replay_js.h').read_text(encoding='utf-8'):
        command.insert(1,'-DARKDPS_CAPTURE_HOOK_POINT')
    if 'inline JSValue hp_observation' in (native/'owned_replay_js.h').read_text(encoding='utf-8'):
        command.insert(1,'-DARKDPS_CAPTURE_HP_OBSERVATION')
    if 'bool sealed=true' in (native/'owned_replay_js.h').read_text(encoding='utf-8'):
        command.insert(1,'-DARKDPS_SEALED_PROVIDER')
    if 'inline JSValue prepare_hp_observation' in (native/'owned_replay_js.h').read_text(encoding='utf-8') and 'direct_hp_reason' in (native/'engine_capture_pipeline.h').read_text(encoding='utf-8'):
        command.insert(1,'-DARKDPS_DIRECT_HP')
    build=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    (output/'build-stdout.txt').write_text(build.stdout,encoding='utf-8')
    (output/'build-stderr.txt').write_text(build.stderr,encoding='utf-8')
    if build.returncode:
        raise RuntimeError(build.stdout+build.stderr)
    result=subprocess.run([str(binary),str(script)]+(['--direct-hp','--disable-scope-primitives'] if compare_scope else [])+(['--disable-ascii'] if disable_ascii else []),cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    (output/'ordinary-stdout.txt').write_text(result.stdout,encoding='utf-8')
    (output/'ordinary-stderr.txt').write_text(result.stderr,encoding='utf-8')
    if result.returncode:
        raise RuntimeError(result.stdout+result.stderr)
    report=seal_business_events(json.loads(result.stdout))
    if compare_direct or compare_scope:
        direct_command=[str(binary),str(script),'--direct-hp']
        direct=subprocess.run(direct_command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        (output/'direct-stdout.txt').write_text(direct.stdout,encoding='utf-8')
        (output/'direct-stderr.txt').write_text(direct.stderr,encoding='utf-8')
        if direct.returncode:
            raise RuntimeError(direct.stdout+direct.stderr)
        report['same_binary_direct']=seal_business_events(json.loads(direct.stdout))
        for control,treatment in zip(report['results'],report['same_binary_direct']['results']):
            assert control['business_events_sha256']==treatment['business_events_sha256'], 'business event sequence differs'
        report['direct_command']=direct_command
    artifacts=[source_root/n for n in ['event_probe_runtime.js','event_probe_autochess.js','event_probe_native_pc.js']]
    artifacts+=[(bridge_root or source_root)/'native_pc/native_event_runtime/bridge.js',native/'owned_replay_view.h',native/'owned_replay_js.h',native/'native_capture.h',native/'native_capture.cpp',native/'engine_capture_plan.h',native/'engine_capture_pipeline.h',native/'engine_capture_config.h',native/'engine_capture_lane.h',native/'capture_spill_pool.h',ROOT/'tests/production_hp_queue.h',ROOT/'tests/production_hp_quickjs.cpp',ROOT/'tests/production_hp_quickjs_bundle.py',ROOT/'tests/run_production_hp_quickjs.py',ROOT/'tests/native_pc_frida_probe_fixture.js',ROOT/'tests/native_pc_autochess_context_fixture.js',*dependencies]
    report['provenance']={str(p):sha256(p.read_bytes()).hexdigest() for p in artifacts}
    report['ascii_disabled']=disable_ascii
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
    p.add_argument('--compare-direct',action='store_true')
    p.add_argument('--compare-scope',action='store_true')
    p.add_argument('--queue-rate',type=int);p.add_argument('--pause-ms',type=int,default=0);p.add_argument('--full-scope',action='store_true');p.add_argument('--pub-every',type=int,default=1000);p.add_argument('--disable-ascii',action='store_true');p.add_argument('--native-root',type=Path,default=ROOT);p.add_argument('--bridge-root',type=Path)
    a=p.parse_args();r=run(a.stage.resolve(),a.output.resolve(),a.count,a.source_root.resolve(),a.native_root.resolve(),a.bridge_root.resolve() if a.bridge_root else None,a.disable_ascii,a.full_scope,a.pub_every,a.queue_rate,a.pause_ms,a.compare_direct,a.compare_scope)
    print(json.dumps({row['branch']:round(row['setters_per_second']) for row in r['results']}))
