import sys,subprocess,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tests'));from production_hp_quickjs_bundle import bundle
out=Path(__file__).resolve().parent/'visit-abba';out.mkdir(parents=True,exist_ok=True);names=['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/publication.js','native_pc/native_event_runtime/bridge.js']
for label,ref,bridge_ref in [('base','8502fe3','8502fe3'),('candidate','8d5315e','c83c50f')]:
 source=out/label;assert source.resolve().is_relative_to(ROOT/'runtime')
 for n in names:
  p=source/n;p.parent.mkdir(parents=True,exist_ok=True);r=bridge_ref if n.endswith('/bridge.js') else ref;p.write_bytes(subprocess.check_output(['git','show',r+':'+n],cwd=ROOT))
 js=bundle(2000,True,source,source,True,8571)
 inject="let visitCalls=0;if(typeof __arkdpsWithGraphProjection==='function'){const originalVisit=__arkdpsWithGraphProjection;__arkdpsWithGraphProjection=function(){visitCalls++;return originalVisit.apply(this,arguments);};}\n"
 js=js.replace('const results=[];',inject+'const results=[];').replace('__ownedProfile(true);scopeMs=', '__ownedProfile(true);visitCalls=0;scopeMs=').replace('results.push({js_profile:', 'results.push({visit_calls:visitCalls,js_profile:');(out/f'{label}.js').write_text(js)
exe=ROOT/'runtime/production-hp-throughput/function-sampling/production-hp.exe'
for i,label in enumerate(['base','candidate','candidate','base']):
 p=out/f'{label}.js';r=subprocess.run([str(exe),str(p)],capture_output=True,text=True,timeout=60)
 if r.returncode:raise RuntimeError(r.stderr)
 d=json.loads(r.stdout);d['provenance']={'source':'8502fe3' if label=='base' else '8d5315e','bridge':'8502fe3' if label=='base' else 'c83c50f','binary_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'files':{n:hashlib.sha256((out/label/n).read_bytes()).hexdigest() for n in names}};(out/f'{i}-{label}.json').write_text(json.dumps(d,indent=2));print(i,label,[(x['branch'],round(x['setters_per_second']),x['visit_calls'],x['native_profile']['graph_check_calls']) for x in d['results']],flush=True)
