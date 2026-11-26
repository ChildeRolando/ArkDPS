from pathlib import Path
import sys,subprocess,json,hashlib,shutil
own=Path(__file__).resolve().parents[2];life=own.parent/'arkdps-production-hp-throughput';sys.path.insert(0,str(life/'tests'))
from production_hp_quickjs_bundle import bundle
out=own/'runtime/hp-provider-profile';out.mkdir(parents=True,exist_ok=True)
source=own;native=own.parent/'arkdps-hp-raw-observation';stage=own.parent/'arkdps-native-async-engine/runtime/async-engine-build/stage'
assert hashlib.sha256((stage/'json.hpp').read_bytes()).hexdigest()=='9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'
cpp=(life/'tests/production_hp_quickjs.cpp').read_text();cpp=cpp.replace('static FunctionTiming hook_timing, graph_timing;','static FunctionTiming hook_timing, graph_timing, hp_timing;').replace('  ++hp_observation_calls;','  ++hp_observation_calls;\n  SampleTimer sample(hp_timing);').replace('    graph_timing = {};','    graph_timing = {};\n    hp_timing = {};').replace('{"graph_check_timing", &graph_timing}})', '{"graph_check_timing", &graph_timing},{"hp_observation_timing", &hp_timing}})')
(out/'profile.cpp').write_text(cpp)
js=bundle(5000,True,source,native,True,8571)
setup=r'''
let sampledFunctions={};
function sampleFunction(fn,name){return function(){const label=name==='withCapturedMode'?name+'.'+arguments[0].edge:name;let t=sampledFunctions[label];if(!t)t=sampledFunctions[label]={calls:0,samples:0,sampled_us:0,max_us:0};const take=(t.calls++&127)===0;if(!take)return fn.apply(this,arguments);const start=__monotonicMs();try{return fn.apply(this,arguments);}finally{const us=(__monotonicMs()-start)*1000;t.samples++;t.sampled_us+=us;t.max_us=Math.max(t.max_us,us);}};}
f.context.sampleFunction=sampleFunction;
for(const name of ['applyHpEntry','applyHpLeave','validatedClockValues','enemyMaxHp','hpObservationIo','recordEnemyHp','withCapturedMode'])f.vm.runInContext('if(typeof '+name+'==="function")'+name+'=globalThis.sampleFunction('+name+','+JSON.stringify(name)+')',f.context);
f.vm.runInContext("probeAdapter.captureScopePoint=globalThis.sampleFunction(probeAdapter.captureScopePoint,'scope.adapter')",f.context);
f.vm.runInContext("probeAdapter.captureHpObservation=globalThis.sampleFunction(probeAdapter.captureHpObservation,'IO.adapter')",f.context);
__arkdpsOwnedHpObservation=sampleFunction(__arkdpsOwnedHpObservation,'IO.factory');
for(const name of Object.getOwnPropertyNames(__ArkDpsHpObservation.prototype))if(name!=='constructor')__ArkDpsHpObservation.prototype[name]=sampleFunction(__ArkDpsHpObservation.prototype[name],'IO.'+name);
setter.onEnter=sampleFunction(setter.onEnter,'HP.entry');setter.onLeave=sampleFunction(setter.onLeave,'HP.leave');
'''
js=js.replace('const results=[];',setup+'\nconst results=[];').replace('__ownedProfile(true);scopeMs=', '__ownedProfile(true);sampledFunctions={};scopeMs=').replace('results.push({js_profile:', 'results.push({sampled_functions:sampledFunctions,js_profile:')
(out/'sampled.js').write_text(js)
command=[shutil.which('g++'),'-DARKDPS_CAPTURE_HP_OBSERVATION','-DARKDPS_CAPTURE_GRAPH_BATCH','-DARKDPS_CAPTURE_GRAPH_CHECK','-std=c++17','-static','-O2','-I',str(native/'native_pc/native_event_runtime'),'-I',str(stage/'quickjs-headers'),'-I',str(stage),'-I',str(life/'tests'),str(out/'profile.cpp'),str(native/'native_pc/native_event_runtime/native_capture.cpp'),*[str(stage/f'object-{i}.o') for i in range(5)],'-o',str(out/'profile.exe')]
build=subprocess.run(command,capture_output=True,text=True,timeout=60);(out/'build.stdout').write_text(build.stdout);(out/'build.stderr').write_text(build.stderr)
if build.returncode:raise RuntimeError(build.stderr)
r=subprocess.run([str(out/'profile.exe'),str(out/'sampled.js'),'--sample-functions'],capture_output=True,text=True,timeout=30);(out/'run.stdout').write_text(r.stdout);(out/'run.stderr').write_text(r.stderr)
if r.returncode:raise RuntimeError(r.stderr)
x=json.loads(r.stdout)
for row in x['results']:
 if row['branch']!='input_only_baseline':assert row['native_profile']['hp_observation_calls']==10000,row['native_profile']
x['source_head']=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip();x['native_head']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=native).decode().strip();x['source_sha256']={f:hashlib.sha256((source/f).read_bytes()).hexdigest() for f in ['event_probe_runtime.js','event_probe_native_pc.js','event_probe_autochess.js']};x['profile_cpp_sha256']=hashlib.sha256((out/'profile.cpp').read_bytes()).hexdigest();x['profile_js_sha256']=hashlib.sha256((out/'sampled.js').read_bytes()).hexdigest();x['build_command']=command;x['sampling']='1/128 inclusive mean/max, not exact total; no queue/IPC/native trampoline or FPS measurement';(out/'receipt.json').write_text(json.dumps(x,indent=2))
for row in x['results']:
 print(row['branch'],row['native_profile']['hp_observation_calls'],{k:round(v['sampled_us']/v['samples'],2) for k,v in row['sampled_functions'].items() if v['samples']},row['native_profile'].get('hp_observation_timing'))
