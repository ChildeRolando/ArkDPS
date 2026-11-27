"""Serial, fixed-budget semantic and throughput comparison; no game access."""
from pathlib import Path
import argparse,json,statistics,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--before-source',type=Path,required=True);p.add_argument('--before-binary',type=Path,required=True);p.add_argument('--after-source',type=Path,required=True);p.add_argument('--after-binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();output=a.output.resolve();output.mkdir(parents=True,exist_ok=True);cases=[]
 def run(label,source,binary,name,flags,compare=None):
  out=output/label/name;out.mkdir(parents=True,exist_ok=True)
  cmd=[sys.executable,str(ROOT/'tests/capture_demand_engine_run.py'),'--source-root',str(source.resolve()),'--binary',str(binary.resolve()),'--output',str(out),*flags]
  if compare:cmd+=['--compare',str(compare)]
  r=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=115);(out/'runner.stdout').write_text(r.stdout,encoding='utf-8');(out/'runner.stderr').write_text(r.stderr,encoding='utf-8')
  receipt=json.loads((out/'receipt.json').read_text());row={'version':label,'case':name,'directory':str(out),'returncode':r.returncode,'report':receipt.get('report'),'input_sha256':receipt.get('owned_input_sha256'),'event_sha256':receipt.get('event_sha256')};cases.append(row);(output/'suite.json').write_text(json.dumps({'cases':cases},indent=2),encoding='utf-8')
  print(json.dumps({'version':label,'case':name,'returncode':r.returncode,'elapsed_ms':(receipt.get('report')or{}).get('elapsed_ms'),'catchup_ms':(receipt.get('report')or{}).get('catchup_ms')}),flush=True)
  assert r.returncode==0,r.stderr[-3000:]
  return out
 for route in range(6):
  name='semantic-route'+str(route);before=run('before',a.before_source,a.before_binary,name,['--count','1','--route',str(route)])
  run('after',a.after_source,a.after_binary,name,['--count','1','--route',str(route)],before)
 for repeat in range(3):
  name='unpaced1000-repeat'+str(repeat+1);before=run('before',a.before_source,a.before_binary,name,['--count','1000','--pub-every','100'])
  run('after',a.after_source,a.after_binary,name,['--count','1000','--pub-every','100'],before)
 before=run('before',a.before_source,a.before_binary,'paced400-pause500',['--count','4000','--rate','400','--pub-every','100','--pause-ms','500'])
 run('after',a.after_source,a.after_binary,'paced400-pause500',['--count','4000','--rate','400','--pub-every','100','--pause-ms','500'],before)
 summary={}
 for label in ['before','after']:
  raw=[r['report'] for r in cases if r['version']==label and r['case'].startswith('unpaced')]
  summary[label]={'unpaced_cycles_per_s':[r['cycles']*1000/r['elapsed_ms'] for r in raw],'unpaced_edges_per_s':[r['edges']*1000/r['elapsed_ms'] for r in raw],'median_cycles_per_s':statistics.median(r['cycles']*1000/r['elapsed_ms'] for r in raw),'pause':[r['report'] for r in cases if r['version']==label and r['case']=='paced400-pause500'][0]}
 (output/'suite.json').write_text(json.dumps({'kind':'serial_uninstrumented_owned_mixed_gate','distribution':'five equally weighted synthetic routes; not a measured game distribution','semantics':'six independent routes including non-player numeric parent shield','cases':cases,'summary':summary},indent=2),encoding='utf-8');print(json.dumps(summary))
if __name__=='__main__':main()
