from pathlib import Path
import json,sys,subprocess,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tests'));from production_hp_quickjs_bundle import bundle
root=ROOT.parent/'arkdps-async-capture-performance';out=ROOT/'runtime/production-hp-throughput/sealed-owner-abba';source=out/'source';names=['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/publication.js','native_pc/native_event_runtime/bridge.js']
for n in names:
 p=source/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/n).read_bytes())
p=out/'fixture.js';p.write_text(bundle(2000,True,source,source,True,8571));exe=out/'production-hp.exe'
for i,sealed in enumerate([False,True,True,False]):
 flags=[] if sealed else ['--unsealed-provider'];r=subprocess.run([str(exe),str(p),*flags],capture_output=True,text=True,timeout=60)
 if r.returncode:raise RuntimeError(r.stderr)
 d=json.loads(r.stdout)
 for row in d['results']:
  if row['branch']=='input_only_baseline':continue
  assert row['native_request_counts']['__nativeOwnedHpObservation']==4000,row
  assert row['native_profile']['cpp_io_counters_available'] is False,row
  assert row['native_profile']['hp_observation_calls'] is None,row
  assert row['native_profile']['sealed_owner'] is sealed,row
 d['provenance']={'root_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'binary_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'flags':flags,'source_files':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in names},'counters':'JS NativeCall routed requests; not CPP count; directlease calls not included'};(out/(f'{i}-'+('sealed' if sealed else 'legacy')+'.json')).write_text(json.dumps(d,indent=2))
 print(i,sealed,[(x['branch'],round(x['setters_per_second']),x['native_request_counts'].get('__nativeOwnedHpObservation',0)) for x in d['results']],flush=True)
