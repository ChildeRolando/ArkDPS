import sys,subprocess,json
from pathlib import Path
sys.path.insert(0,'tests');from production_hp_quickjs_bundle import bundle
s=Path('E:/文档/ChatGPT/ArkDPS/arkdps-graph-predicate-source');b=Path('E:/文档/ChatGPT/ArkDPS/arkdps-native-queue-throughput');o=Path('runtime/production-hp-throughput/function-sampling')
for name in ['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/publication.js','native_pc/native_event_runtime/bridge.js']:
 target=s/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(subprocess.check_output(['git','show','6e934a4:'+name]))
js=bundle(5000,True,s,b,True,8571)
setup=r'''
let sampledFunctions={};
function sampleFunction(fn,name){return function(){const label=name==='withCapturedMode'?name+'.'+arguments[0].edge:name;let t=sampledFunctions[label];if(!t)t=sampledFunctions[label]={calls:0,samples:0,sampled_us:0,max_us:0};const take=(t.calls++&127)===0;if(!take)return fn.apply(this,arguments);const start=__monotonicMs();try{return fn.apply(this,arguments);}finally{const us=(__monotonicMs()-start)*1000;t.samples++;t.sampled_us+=us;t.max_us=Math.max(t.max_us,us);}};}
f.context.sampleFunction=sampleFunction;
for(const name of ['time','hpPointClock','entityId','hpPointId','enemyMaxHp','capturedNativeScopePoint','recordEnemyHp','withCapturedMode'])f.vm.runInContext('if(typeof '+name+'==="function")'+name+'=globalThis.sampleFunction('+name+','+JSON.stringify(name)+')',f.context);
f.vm.runInContext("probeAdapter.captureScopePoint=globalThis.sampleFunction(probeAdapter.captureScopePoint,'scope.adapter')",f.context);
setter.onEnter=sampleFunction(setter.onEnter,'HP.entry');setter.onLeave=sampleFunction(setter.onLeave,'HP.leave');
'''
js=js.replace('const results=[];',setup+'\nconst results=[];').replace('__ownedProfile(true);scopeMs=', '__ownedProfile(true);sampledFunctions={};scopeMs=').replace('results.push({js_profile:', 'results.push({sampled_functions:sampledFunctions,js_profile:')
p=o/'sampled.js';p.write_text(js,encoding='utf-8');r=subprocess.run([str((o/'production-hp.exe').resolve()),str(p.resolve()),'--sample-functions'],capture_output=True,text=True,timeout=60)
if r.returncode:raise RuntimeError(r.stderr)
d=json.loads(r.stdout);(o/'sampled.json').write_text(json.dumps(d,indent=2))
for x in d['results']:
 print(x['branch'],round(x['setters_per_second']),{k:round(v['sampled_us']/v['samples'],2) for k,v in x['sampled_functions'].items()}, {k:x['native_profile'][k] for k in ['hook_point_timing','graph_check_timing']},flush=True)
