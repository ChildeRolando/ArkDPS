"""Run actual mixed callbacks; preserve failures and exact self-owned inputs."""
from pathlib import Path
import argparse,hashlib,json,subprocess
from capture_demand_engine_bundle import damage_bundle,ROOT
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser()
 for flag in ['binary','source-root','output']:p.add_argument('--'+flag,type=Path,required=True)
 for flag,default in [('count',5),('rate',0),('pause-ms',0),('pause-at-ms',1000),('pub-every',1),('route',-1),('hp-padding',0)]:p.add_argument('--'+flag,type=int,default=default)
 p.add_argument('--compare',type=Path);p.add_argument('--profile',action='store_true');a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);source=a.source_root.resolve();binary=a.binary.resolve()
 options={'count':a.count,'rate':a.rate,'pause_ms':a.pause_ms,'pause_at_ms':a.pause_at_ms,'pub_every':a.pub_every,'route':a.route,'hp_padding':a.hp_padding,'profile':a.profile}
 script=out/'mixed.js';script.write_text(damage_bundle(source,options),encoding='utf-8');owned=out/'owned-input.json'
 tracked=[Path(__file__),ROOT/'tests/capture_demand_engine_bundle.py',ROOT/'tests/capture_demand_engine.cpp'];tool_hashes={x.name:sha(x) for x in tracked}
 command=[str(binary),str(script),str(owned)];r=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=110)
 (out/'stdout.log').write_text(r.stdout,encoding='utf-8');(out/'stderr.log').write_text(r.stderr,encoding='utf-8')
 receipt={'kind':'actual_owned_mixed_damage','returncode':r.returncode,'command':command,'options':options,'source_root':str(source),'binary_sha256':sha(binary),'script_sha256':sha(script),'producer_reader':'actual capture_read/ReadProcessMemory(GetCurrentProcess)','sampled_stage_profile':a.profile,'native_capture_instrumented':False,'Game':False,'tool_sha256':tool_hashes,'build_receipt_sha256':sha(binary.parent/'build-receipt.json')}
 if owned.exists():receipt['owned_input_sha256']=sha(owned)
 rows=[json.loads(line) for line in r.stdout.splitlines() if line.startswith('{')];data=rows[-1] if rows else None
 if data:
  (out/'result.json').write_text(json.dumps(data,indent=2),encoding='utf-8');receipt['report']=data['report'];receipt['event_sha256']=hashlib.sha256(json.dumps(data['outcome']['events'],sort_keys=True).encode()).hexdigest()
 if a.compare and data:
  previous=json.loads((a.compare/'receipt.json').read_text());receipt['comparison']={'directory':str(a.compare.resolve()),'same_owned_input':receipt.get('owned_input_sha256')==previous.get('owned_input_sha256'),'same_events':receipt['event_sha256']==previous.get('event_sha256')}
 (out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
 assert tool_hashes=={x.name:sha(x) for x in tracked},'test driver source changed during execution'
 assert r.returncode==0,r.stderr[-3000:]
 if a.compare:assert receipt['comparison']['same_owned_input'] and receipt['comparison']['same_events'],'before/after owned inputs or event stream differ'
 if a.pause_ms:assert data['report']['pause_observed'] and data['report']['catchup_ms']>=0,'consumer pause/recovery not demonstrated'
 print(json.dumps({'status':'PASS','output':str(out),'report':data['report'],'comparison':receipt.get('comparison')}))
if __name__=='__main__':main()
