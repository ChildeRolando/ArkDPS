import sys,json,subprocess,shutil
from pathlib import Path
sys.path.insert(0,'tests');from production_hp_quickjs_bundle import bundle
base=Path('runtime/production-hp-throughput/function-sampling');source=base/'source-segmented';shutil.copytree(base/'source-6e934a4',source,dirs_exist_ok=True)
p=source/'event_probe_native_pc.js';s=p.read_text();start=s.index('function capturedNativeScopePoint(when){');end=s.index('\nfunction capturedActorDataPoint',start);fn=s[start:end]
header='let __scopeSegments={calls:0,samples:0,us:{}};\n'
fn=fn.replace('function capturedNativeScopePoint(when){','function capturedNativeScopePoint(when){\n const __sample=(__scopeSegments.calls++&127)===0;let __tick=__sample?__monotonicMs():0;if(__sample)__scopeSegments.samples++;')
def mark(name):return "if(__sample){const __now=__monotonicMs();__scopeSegments.us['"+name+"']=(__scopeSegments.us['"+name+"']??0)+(__now-__tick)*1000;__tick=__now;}"
old="let phase=null,round=null,state=null;const chunks=nativeGraphChunks(),proof=checkedGraphProof('scope',graph),compiled=compileCapturedScopeReads(graph);"
new="let phase=null,round=null,state=null;const chunks=nativeGraphChunks();"+mark('head_revision_chunks')+"const proof=checkedGraphProof('scope',graph);"+mark('graph_proof')+"const compiled=compileCapturedScopeReads(graph);"+mark('compiled_geometry')
assert old in fn;fn=fn.replace(old,new).replace(' if(!graph.context.autochess)', ' '+mark('dynamic_field_loop')+'\n if(!graph.context.autochess)')
fn=fn.replace(' const context=graph.context,fixed=captureClockFixed().toString();', ' '+mark('phase_state_validation')+'\n const context=graph.context,fixed=captureClockFixed().toString();'+mark('tail_clock_fixed'))
fn=fn.replace(' return {protocol_version:1,status:', ' '+mark('auto_scope')+'\n const __result={protocol_version:1,status:').replace('scope_key,phase,round,collection_mode:autoCollectionMode(scope_key,phase)};', 'scope_key,phase,round,collection_mode:autoCollectionMode(scope_key,phase)};'+mark('collection_mode')+'return __result;')
s=s[:start]+header+fn+s[end:];p.write_text(s)
b=Path('E:/文档/ChatGPT/ArkDPS/arkdps-native-queue-throughput');js=bundle(5000,True,source,b,True,8571)
js=js.replace('__ownedProfile(true);scopeMs=', "__ownedProfile(true);f.vm.runInContext('__scopeSegments={calls:0,samples:0,us:{}}',f.context);scopeMs=").replace('results.push({js_profile:',"results.push({scope_segments:f.vm.runInContext('__scopeSegments',f.context),js_profile:")
p=base/'segmented.js';p.write_text(js);r=subprocess.run([str((base/'production-hp.exe').resolve()),str(p.resolve())],capture_output=True,text=True,timeout=60)
if r.returncode:raise RuntimeError(r.stderr)
d=json.loads(r.stdout);(base/'segmented.json').write_text(json.dumps(d,indent=2));print([(x['branch'],x['scope_segments'],round(x['setters_per_second'])) for x in d['results']])
