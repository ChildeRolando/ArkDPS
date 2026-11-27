from pathlib import Path
import hashlib,json,subprocess,sys,shutil
ROOT=Path('E:/文档/ChatGPT/ArkDPS/arkdps-async-capture-performance')
OWN=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'tests'))
from production_hp_quickjs_bundle import bundle
from run_production_hp_quickjs import seal_business_events
BIN=ROOT.parent/'arkdps-production-hp-throughput/runtime/direct-hp-d-validation/run/production-hp.exe'
NAMES=['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/bridge.js','native_pc/native_event_runtime/publication.js']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def once(s,old,new):
 assert s.count(old)==1,(old,s.count(old));return s.replace(old,new)
def instrument_factory(s):
 start=s.index('function __arkdpsOwnedGraphPrimitives(kind,graph){');end=s.index('\nfunction __arkdpsOwnedScalar(',start)
 part=s[start:end];part=once(part,'function __arkdpsOwnedGraphPrimitives(kind,graph){',"function __arkdpsOwnedGraphPrimitives(kind,graph){\n const __sample=globalThis.__segmentBegin('factory');try{")
 part=once(part," const packet=__arkdpsNativeCall('__nativeOwnedGraphCheck',kind,geometry.revision,geometry.mode,1);", " globalThis.__segmentStep(__sample,'preamble_geometry_lease');\n const packet=__arkdpsNativeCall('__nativeOwnedGraphCheck',kind,geometry.revision,geometry.mode,1);\n globalThis.__segmentStep(__sample,'native_call');")
 part=once(part,' Object.freeze(packet);'," globalThis.__segmentStep(__sample,'packet_header_index_type_width');\n Object.freeze(packet);")
 part=once(part,'graph,geometry,flat});return io;','graph,geometry,flat});globalThis.__segmentStep(__sample,\'freeze_mint\');return io;')
 assert part.endswith('\n}');part=part[:-2]+"\n }finally{globalThis.__segmentEnd(__sample);}\n}"
 return s[:start]+part+s[end:]
def instrument_scope(s):
 start=s.index('function capturedNativeScopePoint(when){');end=s.index('\nfunction capturedActorDataPoint(',start)
 part=s[start:end];part=once(part,'function capturedNativeScopePoint(when){',"function capturedNativeScopePoint(when){\n const __sample=globalThis.__segmentBegin('scope');try{")
 part=once(part,' for(const field of primitives===null&&proof===null?compiled:compiled.dynamic){'," globalThis.__segmentStep(__sample,'preamble_including_factory');\n for(const field of primitives===null&&proof===null?compiled:compiled.dynamic){")
 part=once(part,' const context=graph.context,fixed=captureClockFixed().toString();'," globalThis.__segmentStep(__sample,'field_canonical_loop_and_semantic_validation');\n const context=graph.context,fixed=captureClockFixed().toString();\n globalThis.__segmentStep(__sample,'tail_captureClockFixed');")
 part=once(part,' const scope_key=autoScope(context.roots,phase,round,when.frame,fixed,context.fingerprint,context.header_key);'," const scope_key=autoScope(context.roots,phase,round,when.frame,fixed,context.fingerprint,context.header_key);\n globalThis.__segmentStep(__sample,'tail_autoScope');")
 part=once(part," return {protocol_version:1,status:'available',verified_same_frame:true,frame:when.frame,game_time:when.game_time,\n  scope_key,phase,round,collection_mode:autoCollectionMode(scope_key,phase)};", " const __result={protocol_version:1,status:'available',verified_same_frame:true,frame:when.frame,game_time:when.game_time,\n  scope_key,phase,round,collection_mode:autoCollectionMode(scope_key,phase)};\n globalThis.__segmentStep(__sample,'tail_collectionMode_and_result');return __result;")
 assert part.endswith('\n}\n');part=part[:-3]+"\n }finally{globalThis.__segmentEnd(__sample);}\n}\n"
 return s[:start]+part+s[end:]
HELPERS=r'''
const __segments={},__segmentCounters={};let __segmentEnabled=false;
function __segmentBegin(owner){if(!__segmentEnabled)return null;const n=(__segmentCounters[owner]??0)+1;__segmentCounters[owner]=n;if((n&127)!==1)return null;const t=__monotonicMs();return {owner,start:t,last:t};}
function __segmentStore(key,us){const x=__segments[key]??(__segments[key]={samples:0,sum_us:0,max_us:0});x.samples++;x.sum_us+=us;x.max_us=Math.max(x.max_us,us);}
function __segmentStep(s,name){if(s===null)return;const t=__monotonicMs();__segmentStore(s.owner+'.'+name,(t-s.last)*1000);s.last=t;}
function __segmentEnd(s){if(s===null)return;__segmentStore(s.owner+'.total',(__monotonicMs()-s.start)*1000);}
'''
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 report={'root_head':head,'binary_sha256':sha(BIN),'count':5000,'sampling':'per-owner counter 1/128, phase1; only selected calls invoke clock','arms':{}}
 for label in ['control','sampled']:
  folder=OUT/label/'source'
  for n in NAMES:
   p=folder/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,p)
  if label=='sampled':
   b=folder/NAMES[4];b.write_text(instrument_factory(b.read_text(encoding='utf8')),encoding='utf8')
   a=folder/NAMES[0];a.write_text(instrument_scope(a.read_text(encoding='utf8')),encoding='utf8')
  script=bundle(5000,True,folder,folder,True,1000)
  script=once(script,"['input_only_baseline','unchanged','changed_damage','heal_coalesced','maxhp_dirty']","['heal_coalesced']")
  script=HELPERS+'\n'+script
  script=once(script,'let stamps=0;',"f.context.__segmentBegin=__segmentBegin;f.context.__segmentStep=__segmentStep;f.context.__segmentEnd=__segmentEnd;let stamps=0;")
  script=once(script,';}const start=__monotonicMs();',";}__segmentEnabled=true;const start=__monotonicMs();")
  script=once(script,'const ms=__monotonicMs()-start;',"const ms=__monotonicMs()-start;__segmentEnabled=false;")
  script=once(script,'results.push({business_events_json:',"results.push({segments:__segments,segment_calls:__segmentCounters,business_events_json:")
  js=OUT/label/'execute.js';js.write_text(script,encoding='utf8')
  result=subprocess.run([str(BIN),str(js),'--direct-hp'],capture_output=True,text=True,encoding='utf8',errors='replace',timeout=35)
  (OUT/label/'stdout.json').write_text(result.stdout,encoding='utf8');(OUT/label/'stderr.txt').write_text(result.stderr,encoding='utf8')
  assert result.returncode==0,(label,result.stderr)
  raw=json.loads(result.stdout);arm=seal_business_events(raw)
  arm['source_sha256']={n:sha(folder/n) for n in NAMES};arm['execute_sha256']=sha(js);report['arms'][label]=arm
 assert report['arms']['control']['results'][0]['business_events_sha256']==report['arms']['sampled']['results'][0]['business_events_sha256']
 rows=report['arms']['sampled']['results'][0]['segments']
 for v in rows.values():v['mean_us']=v['sum_us']/v['samples']
 report['driver_sha256']=sha(Path(__file__));report['summary']=rows
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2),encoding='utf8')
 print(json.dumps({'calls':report['arms']['sampled']['results'][0]['segment_calls'],'segments':rows},indent=2))
if __name__=='__main__':main()

