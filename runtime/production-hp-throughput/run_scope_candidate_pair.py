import sys,subprocess,json,shutil,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'));from production_hp_quickjs_bundle import bundle
out=Path(__file__).resolve().parent/'scope-candidates';assert out.resolve().is_relative_to(ROOT/'runtime');out.mkdir(parents=True,exist_ok=True);base=out/'base';names=['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/publication.js','native_pc/native_event_runtime/bridge.js']
for n in names:
 p=base/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(subprocess.check_output(['git','show','4c6deda:'+n],cwd=ROOT))
bridge=out/'bridge-base';p=bridge/'native_pc/native_event_runtime/bridge.js';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(subprocess.check_output(['git','show','7de38c3:native_pc/native_event_runtime/bridge.js'],cwd=ROOT))
source=out/'closure';shutil.copytree(base,source,dirs_exist_ok=True);(source/'event_probe_native_pc.js').write_bytes((ROOT.parent/'arkdps-scope-adapter-local/event_probe_native_pc.js').read_bytes())
word=out/'bridge-descriptor';p=word/'native_pc/native_event_runtime/bridge.js';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((ROOT.parent/'arkdps-native-queue-throughput/native_pc/native_event_runtime/bridge.js').read_bytes())
exe=str((ROOT/'runtime/production-hp-throughput/function-sampling/production-hp.exe').resolve())
for label,s,b in [('control',base,bridge),('closure',source,bridge),('descriptor',base,word)]:
 p=out/f'{label}.js';p.write_text(bundle(2000,True,s,b,True,8571));r=subprocess.run([exe,str(p.resolve())],capture_output=True,text=True,timeout=60)
 if r.returncode:raise RuntimeError(r.stderr)
 d=json.loads(r.stdout);d['source_snapshot']={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in [s/n for n in names]};d['bridge_sha256']=hashlib.sha256((b/'native_pc/native_event_runtime/bridge.js').read_bytes()).hexdigest();(out/f'{label}.json').write_text(json.dumps(d,indent=2));print(label,[(x['branch'],round(x['setters_per_second'])) for x in d['results']],flush=True)
