"""Same actual-engine binary; source before/after same-item clock reuse."""
from pathlib import Path
from hashlib import sha256
import json, subprocess, sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
from production_hp_actual_engine_bundle import engine_bundle
OUT=Path(__file__).resolve().parent/'actual-engine'
BINARY=ROOT.parent/'arkdps-engine-production-hp/runtime/actual-engine/production-hp-engine.exe'
NAMES=['event_probe_runtime.js','event_probe_autochess.js','event_probe_native_pc.js',
       'native_pc/native_event_runtime/bridge.js','native_pc/native_event_runtime/cold_plan.js',
       'native_pc/native_event_runtime/publication.js']

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    report={'scope':'actual engine binary, same full-scope world; no Game/IPC/paced FIFO',
            'binary_sha256':sha256(BINARY.read_bytes()).hexdigest(),'arms':{}}
    for arm,rev in [('control','99a42a2'),('reuse','e1dde22')]:
        source=OUT/arm/'source'
        hashes={}
        for name in NAMES:
            target=source/name;target.parent.mkdir(parents=True,exist_ok=True)
            value=subprocess.check_output(['git','show',rev+':'+name],cwd=ROOT)
            target.write_bytes(value);hashes[name]=sha256(value).hexdigest()
        rows=[]
        for branch in ['unchanged','heal_coalesced','changed_damage']:
            script=engine_bundle(2000,True,branch,source)
            target=OUT/arm/(branch+'.js');target.write_text(script,encoding='utf-8')
            run=subprocess.run([str(BINARY),str(target)],cwd=ROOT,capture_output=True,text=True,timeout=30)
            target.with_suffix('.stdout').write_text(run.stdout,encoding='utf-8')
            target.with_suffix('.stderr').write_text(run.stderr,encoding='utf-8')
            assert run.returncode==0,(arm,branch,run.stderr)
            row=json.loads(run.stdout);event_json=row.pop('event_json')
            row['event_sha256']=sha256(event_json.encode()).hexdigest()
            row['executed_script_sha256']=sha256(script.encode()).hexdigest();rows.append(row)
        report['arms'][arm]={'revision':rev,'source_sha256':hashes,'rows':rows}
    for left,right in zip(report['arms']['control']['rows'],report['arms']['reuse']['rows']):
        assert left['event_sha256']==right['event_sha256'],left['branch']
        assert left['report']['capture_reads']==right['report']['capture_reads']
        assert left['report']['capture_read_bytes']==right['report']['capture_read_bytes']
    (OUT/'receipt.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({key:{r['branch']:round(2000000/r['report']['consume_ms']) for r in arm['rows']}for key,arm in report['arms'].items()}))

if __name__=='__main__':main()
