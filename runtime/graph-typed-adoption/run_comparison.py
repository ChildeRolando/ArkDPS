from pathlib import Path
import subprocess,json,hashlib,sys,shutil,statistics
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent/'same-binary'
STAGE=ROOT.parent/'arkdps-native-async-engine/runtime/async-engine-build/stage'
sys.path.insert(0,str(ROOT/'tests'))
from production_hp_quickjs_bundle import bundle
from run_production_hp_quickjs import seal_business_events
NAMES=['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/bridge.js','native_pc/native_event_runtime/publication.js']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def once(s,old,new):assert s.count(old)==1,(old,s.count(old));return s.replace(old,new)
def main():
 OUT.mkdir(parents=True,exist_ok=True);native=ROOT/'native_pc/native_event_runtime';objects=[STAGE/f'object-{i}.o' for i in range(5)];binary=OUT/'production-hp.exe'
 assert sha(STAGE/'json.hpp')=='9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'
 flags=['GRAPH_BATCH','GRAPH_CHECK','HP_OBSERVATION']
 command=['g++','-std=c++17','-static','-O2','-DARKDPS_SEALED_PROVIDER','-DARKDPS_DIRECT_HP',*['-DARKDPS_CAPTURE_'+f for f in flags],'-I',str(native),'-I',str(STAGE/'quickjs-headers'),'-I',str(STAGE),str(ROOT/'tests/production_hp_quickjs.cpp'),str(native/'native_capture.cpp'),*[str(p) for p in objects],'-o',str(binary)]
 built=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=50)
 (OUT/'build-stdout.txt').write_text(built.stdout,encoding='utf8');(OUT/'build-stderr.txt').write_text(built.stderr,encoding='utf8');assert built.returncode==0,built.stderr
 report={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'command':command,'binary_sha256':sha(binary),'samples':[],'source_sha256':{},'included':'actual shared source, compiled capture plan, OwnedView and pinned provider, B direct, fullscope and real publication; not engine thread/IPC/FIFO/Game/FPS'}
 for label in ['control','typed']:
  frozen=OUT/label/'source'
  for n in NAMES:
   p=frozen/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,p)
  p=frozen/NAMES[4];s=p.read_text(encoding='utf8');s=once(s,'let issued;try{issued=native.__nativeOwnedGraphCheck(kind,geometry.revision,geometry.mode,2,packet);}',"let issued;try{globalThis.__typedGraphAdoptions++;issued=native.__nativeOwnedGraphCheck(kind,geometry.revision,geometry.mode,2,packet);}")
  if label=='control':s=once(s,'if(native.__nativeOwnedGraphTypedPackets===1){','if(false&&native.__nativeOwnedGraphTypedPackets===1){')
  p.write_text(s,encoding='utf8');report['source_sha256'][label]={n:sha(frozen/n) for n in NAMES}
  script=bundle(5000,True,frozen,frozen,True,1000)
  script=once(script,"['input_only_baseline','unchanged','changed_damage','heal_coalesced','maxhp_dirty']","['heal_coalesced']")
  script="globalThis.__typedGraphAdoptions=0;\n"+script
  script=once(script,'results.push({business_events_json:',"assertion.equal(globalThis.__typedGraphAdoptions,"+('5005' if label=='typed' else '0')+",'actual typed recognitions');results.push({typed_adoptions:globalThis.__typedGraphAdoptions,business_events_json:")
  js=OUT/label/'execute.js';js.write_text(script,encoding='utf8')
 for run,label in enumerate(['control','typed','typed','control']):
  js=OUT/label/'execute.js';result=subprocess.run([str(binary),str(js),'--direct-hp'],cwd=ROOT,capture_output=True,text=True,timeout=35)
  (OUT/(str(run)+'-'+label+'-stdout.json')).write_text(result.stdout,encoding='utf8');(OUT/(str(run)+'-'+label+'-stderr.txt')).write_text(result.stderr,encoding='utf8');assert result.returncode==0,(label,result.stderr)
  arm=seal_business_events(json.loads(result.stdout));arm['label']=label;arm['script_sha256']=sha(js);report['samples'].append(arm)
 rows=[a['results'][0] for a in report['samples']];assert len({r['business_events_sha256'] for r in rows})==1
 assert all(r['dispatch_counters']==rows[0]['dispatch_counters'] and r['native_request_counts']==rows[0]['native_request_counts'] for r in rows)
 report['summary']={label:statistics.mean(a['results'][0]['setters_per_second'] for a in report['samples'] if a['label']==label) for label in ['control','typed']}
 report['driver_sha256']=sha(Path(__file__));report['inputs_sha256']={str(p):sha(p) for p in [*objects,ROOT/'tests/production_hp_quickjs.cpp',native/'owned_replay_js.h',native/'engine_capture_config.h',native/'engine_capture_plan.h']}
 (OUT/'receipt.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report['summary']));print('gain_percent',100*(report['summary']['typed']/report['summary']['control']-1))
if __name__=='__main__':main()
