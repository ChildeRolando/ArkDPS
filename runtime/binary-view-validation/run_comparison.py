from pathlib import Path
from hashlib import sha256
import json,subprocess,sys,shutil
ROOT=Path('E:/文档/ChatGPT/ArkDPS/arkdps-async-capture-performance')
SRC=Path('E:/文档/ChatGPT/ArkDPS/arkdps-scope-binary-view')
OUT=SRC/'runtime/binary-view-validation'; OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'tests'))
from production_hp_quickjs_bundle import bundle
from run_production_hp_quickjs import seal_business_events
BIN=Path('E:/文档/ChatGPT/ArkDPS/arkdps-production-hp-throughput/runtime/direct-hp-d-validation/run/production-hp.exe')
names=['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/bridge.js','native_pc/native_event_runtime/publication.js']
frozen=OUT/'source'
for name in names:
 p=frozen/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(SRC/name,p)
report={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SRC,text=True).strip(),'binary_sha256':sha256(BIN.read_bytes()).hexdigest(),'source_files':{n:sha256((frozen/n).read_bytes()).hexdigest() for n in names},'arms':{}}
for label,enabled in [('primitive',False),('binary',True)]:
 script=bundle(2000,True,frozen,frozen,True,1000)
 script=script.replace('const results=[];',"let binaryFlatCalls=0;const oldBinaryFlat=__ArkDpsVerifiedGraph.prototype.flat;__ArkDpsVerifiedGraph.prototype.flat=function(){binaryFlatCalls++;return oldBinaryFlat.call(this);};\nconst results=[];")
 script=script.replace('let hp=100000,dirtyCount=0;',f'f.context.arkdpsScopeBinaryView={str(enabled).lower()};let hp=100000,dirtyCount=0;')
 script=script.replace('__ownedProfile(true);nativeRequests=', '__ownedProfile(true);binaryFlatCalls=0;nativeRequests=')
 script=script.replace('results.push({business_events_json:', 'results.push({binary_flat_calls:binaryFlatCalls,business_events_json:')
 script=script.replace("if(globalThis.__ownedDisableScopePrimitives===true)assertion.equal(dispatchCounters.graph_format1,0,'disabled format1');", "if(f.context.arkdpsScopeBinaryView===true){assertion.equal(dispatchCounters.graph_format1,0,'binary format1 absent');assertion(binaryFlatCalls>0,'binary flat active');}else if(globalThis.__ownedDisableScopePrimitives===true)assertion.equal(dispatchCounters.graph_format1,0,'disabled format1');")
 target=OUT/(label+'.js');target.write_text(script,encoding='utf8')
 r=subprocess.run([str(BIN),str(target),'--direct-hp'],capture_output=True,text=True,encoding='utf8',errors='replace',timeout=45)
 (OUT/(label+'-stdout.json')).write_text(r.stdout,encoding='utf8');(OUT/(label+'-stderr.txt')).write_text(r.stderr,encoding='utf8')
 assert r.returncode==0,(label,r.stderr)
 raw=json.loads(r.stdout)
 for row in raw['results']:
  if row['branch'] in ['changed_damage','heal_coalesced','maxhp_dirty']:
   assert row['binary_flat_calls']>0 if enabled else row['binary_flat_calls']==0
 arm=seal_business_events(raw);arm['script_sha256']=sha256(target.read_bytes()).hexdigest();report['arms'][label]=arm
for a,b in zip(report['arms']['primitive']['results'],report['arms']['binary']['results']):assert a['business_events_sha256']==b['business_events_sha256'],a['branch']
report['driver_sha256']=sha256(Path(__file__).read_bytes()).hexdigest()
(OUT/'receipt.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print(json.dumps({label:{r['branch']:round(r['setters_per_second']) for r in arm['results']} for label,arm in report['arms'].items()}))
