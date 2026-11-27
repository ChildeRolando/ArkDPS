"""Owned sequential queue-only capacity/window tests with a physical-memory guard."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import argparse,ctypes,json,os,subprocess,time
ROOT=Path(__file__).resolve().parents[1]
def digest(path):return sha256(path.read_bytes()).hexdigest()
def memory():
    class Info(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong)for n in ['total_physical','available_physical','total_pagefile','available_pagefile','total_virtual','available_virtual','available_extended_virtual']]
    value=Info();value.length=ctypes.sizeof(value);assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value))
    return {'total_physical':value.total_physical,'available_physical':value.available_physical,'memory_load_percent':value.load}
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--compiler',default='C:/msys64/ucrt64/bin/g++.exe');p.add_argument('--binary',type=Path);p.add_argument('--capacity',action='store_true');p.add_argument('--build-only',action='store_true');p.add_argument('--budget',type=int,default=1024);p.add_argument('--window-ms',type=int,default=200000);p.add_argument('--burst-ms',type=int,default=60000);p.add_argument('--arrival',type=int,default=30000);p.add_argument('--service',type=int,default=28000);p.add_argument('--tail',type=int,default=10000);p.add_argument('--pause-ms',type=int,default=500);p.add_argument('--payload-bytes',type=int,default=1024);p.add_argument('--minimum-offered-rate',type=float,default=0);p.add_argument('--minimum-consumed-rate',type=float,default=0);a=p.parse_args()
    out=a.output.resolve();assert not out.exists(),'new output directory required';out.mkdir(parents=True);assert os.name=='nt','full Lane/storage experiment requires Windows; portable spill tests are separate'
    sources=[ROOT/'tests/native_capture_budget_window.cpp',ROOT/'tests/native_capture_budget_test.cpp',ROOT/'tests/run_native_capture_budget_window.py']+[ROOT/'native_pc/native_event_runtime'/n for n in ['capture_queue.h','capture_spill_pool.h','engine_capture_lane.h','capture_memory_budget.h','native_capture.h']]
    hashes={str(p.relative_to(ROOT)):digest(p)for p in sources};receipt={'scope':'owned actual queues; synthetic service rates; excludes game/RPM/QuickJS/read-ACK','source_sha256':hashes,'options':{k:str(v)for k,v in vars(a).items()},'result':'RUNNING'}
    started=time.perf_counter()
    try:
        resource=memory();receipt['resource_guard']=resource;assert resource['available_physical']>=8*1024**3,'at least8GiB physical available required; do not evict game memory'
        if a.binary:
            binary=a.binary.resolve();receipt['reused_build_receipt']=str(binary.parent/'receipt.json')
            previous=json.loads((binary.parent/'receipt.json').read_text(encoding='utf-8'));assert previous['source_sha256']==hashes,'existing binary source identity differs'
        else:
            binary=out/'capture-budget-window.exe';cmd=[a.compiler,'-std=c++17','-O2','-static','-pthread','-Wall','-Wextra','-Werror','-I',str(ROOT/'native_pc/native_event_runtime'),str(ROOT/'tests/native_capture_budget_window.cpp'),'-lpsapi','-o',str(binary)]
            build=subprocess.run(cmd,capture_output=True,text=True,timeout=60);(out/'build.log').write_text(build.stdout+build.stderr,encoding='utf-8');receipt['build']={'command':cmd,'returncode':build.returncode};assert build.returncode==0,build.stderr
        receipt['binary_sha256']=digest(binary);receipt['binary']=str(binary)
        if not a.build_only:
            args=['--capacity']if a.capacity else[str(v)for v in [a.budget,a.window_ms,a.burst_ms,a.arrival,a.service,a.tail,a.pause_ms,a.payload_bytes]]+['owned-storage-only']
            command=[str(binary),*args];receipt['command']=command;receipt['started_utc']=datetime.now(timezone.utc).isoformat();began=time.perf_counter()
            with (out/'stdout.jsonl').open('w',encoding='utf-8')as stdout,(out/'stderr.log').open('w',encoding='utf-8')as stderr:
                run=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=120 if a.capacity else a.window_ms/1000+40)
            receipt['actual_process_wall_seconds']=time.perf_counter()-began;receipt['returncode']=run.returncode;receipt['finished_utc']=datetime.now(timezone.utc).isoformat()
            data=[json.loads(line)for line in (out/'stdout.jsonl').read_text(encoding='utf-8').splitlines()if line.startswith('{')];receipt['results']=[d for d in data if d['kind']in ['full_capacity','window_result']]
            assert run.returncode==0,(run.returncode,(out/'stderr.log').read_text(encoding='utf-8'))
            assert len(receipt['results'])==(3 if a.capacity else 1)
            if not a.capacity:
                result=receipt['results'][0]
                assert result['actual_offered_burst_edges_s']>=a.minimum_offered_rate,'measured offered target not met'
                assert result['actual_consumed_burst_edges_s']>=a.minimum_consumed_rate,'measured consumed target not met'
        assert hashes=={str(p.relative_to(ROOT)):digest(p)for p in sources},'source changed during experiment'
        receipt['result']='PASS'
    except BaseException as e:receipt.update(result='FAIL',error_type=type(e).__name__,error=str(e));raise
    finally:
        receipt['whole_driver_wall_seconds']=time.perf_counter()-started;(out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
