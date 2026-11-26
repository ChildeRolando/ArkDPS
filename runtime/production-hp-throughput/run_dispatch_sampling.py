import sys,subprocess,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tests'));from production_hp_quickjs_bundle import bundle
out=Path(__file__).resolve().parent/'dispatch-sampling';out.mkdir(parents=True,exist_ok=True);source=out/'source-8502fe3';assert source.resolve().is_relative_to(ROOT/'runtime')
names=['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/publication.js','native_pc/native_event_runtime/bridge.js']
for n in names:
 p=source/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(subprocess.check_output(['git','show','8502fe3:'+n],cwd=ROOT))
p=source/'native_pc/native_event_runtime/bridge.js';s=p.read_text();start=s.index('function __arkdpsReplayDispatch(');end=s.index('\nfunction __arkdpsStructuralCaptureStatus',start);fn=s[start:end]
header="let __dispatchProfile={entry:{calls:0,samples:0,us:{}},leave:{calls:0,samples:0,us:{}}};\n"
mark=lambda name:"if(__take){const __now=__monotonicMs();__row.us['"+name+"']=(__row.us['"+name+"']??0)+(__now-__tick)*1000;__tick=__now;}"
fn=fn.replace(' const record=', " const __row=__dispatchProfile[receiver===undefined?'entry':'leave'],__take=(__row.calls++&127)===0;let __tick=__take?__monotonicMs():0;if(__take)__row.samples++;\n const record=",1)
fn=fn.replace(' __arkdpsReplayBegin(record);', ' '+mark('packed_record')+'\n __arkdpsReplayBegin(record);'+mark('replay_begin'))
fn=fn.replace("  if(typeof callback==='function'){", '  '+mark('receiver_setup')+"\n  if(typeof callback==='function'){")
old="   if(record.edge==='entry')callback.call(receiver,__arkdpsArguments(payload,argumentMask));\n   else callback.call(receiver,ptr(payload));"
new="   const __argument=record.edge==='entry'?__arkdpsArguments(payload,argumentMask):ptr(payload);"+mark('argument_proxy')+"\n   callback.call(receiver,__argument);"+mark('actual_callback')
assert old in fn;fn=fn.replace(old,new).replace(' }finally{summary=__arkdpsReplayEnd(true);}', ' '+mark('health_and_prefix')+'\n }finally{summary=__arkdpsReplayEnd(true);'+mark('replay_end')+'}')
fn=fn.replace(' return result;', ' '+mark('result_envelope')+'\n return result;')
p.write_text(s[:start]+header+fn+s[end:])
js=bundle(5000,True,source,source,True,8571);js=js.replace('__ownedProfile(true);scopeMs=',"__ownedProfile(true);__dispatchProfile={entry:{calls:0,samples:0,us:{}},leave:{calls:0,samples:0,us:{}}};scopeMs=").replace('results.push({js_profile:', 'results.push({dispatch_profile:__dispatchProfile,js_profile:')
p=out/'sampled.js';p.write_text(js);exe=ROOT/'runtime/production-hp-throughput/function-sampling/production-hp.exe';r=subprocess.run([str(exe),str(p)],capture_output=True,text=True,timeout=60)
if r.returncode:raise RuntimeError(r.stderr)
d=json.loads(r.stdout);d['provenance']={'source':'8502fe3; bridge instrumented only in own runtime','binary_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source_files':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in names}};(out/'sampled.json').write_text(json.dumps(d,indent=2))
for row in d['results']:
 print(row['branch'],round(row['setters_per_second']),{edge:{k:round(v/max(t['samples'],1),2) for k,v in t['us'].items()} for edge,t in row['dispatch_profile'].items()},flush=True)
