"""Preserve actual-engine Destroy reproduction and rejection receipts."""
from pathlib import Path
import argparse,json,hashlib,subprocess
from production_terminal_bundle import ROOT,terminal_bundle


def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-root',type=Path,default=ROOT);p.add_argument('--expect-broken',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);cases=['released'] if a.expect_broken else ['released','retained','stage_changed','missing_body','missing_singleton']
    digest=lambda b:hashlib.sha256(b).hexdigest()
    identity=json.loads((a.binary.parent/'build-receipt.json').read_text());assert identity['binary_sha256']==digest(a.binary.read_bytes())
    receipt=dict(build=identity,binary_sha256=digest(a.binary.read_bytes()),source_root=str(a.source_root.resolve()),passed=False,cases=[])
    for case in cases:
        source=terminal_bundle(a.source_root,case);script=a.output/(case+'.js');script.write_text(source,encoding='utf8')
        run=subprocess.run([str(a.binary.resolve()),str(script.resolve())],capture_output=True,text=True,encoding='utf8',timeout=30)
        (a.output/(case+'.stdout')).write_text(run.stdout,encoding='utf8');(a.output/(case+'.stderr')).write_text(run.stderr,encoding='utf8')
        row=dict(case=case,returncode=run.returncode,bundle_sha256=digest(source.encode()));receipt['cases'].append(row)
        (a.output/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
        if a.expect_broken:
            assert run.returncode!=0 and 'native_replay_gap:v1,' in run.stderr and ',r=0,p=0,f=0,g=2,m=0,k=10.10' in run.stderr,run.stderr
            assert json.loads(run.stdout)['native_status']==9;row['reproduced_old_gap']=True
        elif case.startswith('missing_'):
            assert run.returncode!=0 and 'native_replay_gap:v1,' in run.stderr,run.stderr
            assert json.loads(run.stdout)['native_status']==9;row['structural_rejection']=True
        else:
            assert run.returncode==0,run.stdout+run.stderr;row['result']=json.loads(run.stdout)
    receipt['passed']=True;(a.output/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8');print(a.output/'receipt.json')


if __name__=='__main__':main()
