"""Compare exact old/new pointer-provider envelopes; never normalize evidence."""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess
ROOT=Path(__file__).resolve().parents[1]

def capture(source):
    env=dict(os.environ,ARKDPS_PROBE_FIXTURE_ROOT=str(source))
    result=subprocess.run([shutil.which('node') or 'node',str(ROOT/'tests/production_hp_provider_equivalence_fixture.js')],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    if result.returncode:
        raise RuntimeError(result.stdout+result.stderr)
    return json.loads(result.stdout)

def difference(a,b,path='$'):
    if type(a) is not type(b): return path
    if isinstance(a,dict):
        if set(a)!=set(b): return path+' keys'
        for key in a:
            found=difference(a[key],b[key],path+'.'+key)
            if found:return found
    elif isinstance(a,list):
        if len(a)!=len(b):return path+' length'
        for index,(left,right) in enumerate(zip(a,b)):
            found=difference(left,right,f'{path}[{index}]')
            if found:return found
    elif a!=b:return path
    return None

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(ROOT/'runtime'):raise ValueError('owned receipt must stay under this worktree runtime')
    output.mkdir(parents=True,exist_ok=True);reports={}
    for label,source in [('baseline',args.baseline.resolve()),('candidate',args.candidate.resolve())]:
        result=capture(source);(output/f'{label}.json').write_text(json.dumps(result,indent=2),encoding='utf-8');reports[label]=result
    first=difference(reports['baseline'],reports['candidate']);report={'equal':first is None,'first_difference':first,'cases':len(reports['baseline']['rows']),'source_sha256':{label:{name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['event_probe_native_pc.js','event_probe_android2791.js','event_probe_runtime.js','event_probe_autochess.js']} for label,source in [('baseline',args.baseline.resolve()),('candidate',args.candidate.resolve())]}}
    (output/'comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report));raise SystemExit(0 if first is None else 1)
