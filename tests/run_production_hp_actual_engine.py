"""Bounded exact-engine single/batch pairs; no detours/IPC/Game/FPS acceptance."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys
from production_hp_actual_engine_bundle import engine_bundle,ROOT

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--counts',type=int,nargs='+',default=[500,2000]);p.add_argument('--branches',nargs='+',choices=['heal_coalesced','changed_damage','unchanged'],default=['heal_coalesced']);p.add_argument('--source-root',type=Path,default=ROOT);a=p.parse_args()
    assert all(1<=n<=2000 for n in a.counts)
    a.output.mkdir(parents=True,exist_ok=True);rows=[]
    digest=lambda b:hashlib.sha256(b).hexdigest()
    for count in a.counts:
        for branch in a.branches:
            hashes=[]
            for batch in [False,True]:
                name=f'{branch}-{count}-'+('batch' if batch else 'single');source=engine_bundle(count,batch,branch,a.source_root);script=a.output/(name+'.js');script.write_text(source,encoding='utf-8')
                run=subprocess.run([str(a.binary.resolve()),str(script.resolve())],cwd=ROOT,capture_output=True,text=True,timeout=30)
                (a.output/(name+'.stdout')).write_text(run.stdout,encoding='utf-8');(a.output/(name+'.stderr')).write_text(run.stderr,encoding='utf-8')
                assert run.returncode==0,(name,run.returncode,run.stderr)
                result=json.loads(run.stdout);report=result['report'];events=json.loads(result['event_json']);event_sha=digest(result['event_json'].encode());hashes.append(event_sha)
                assert report['processed_ticket']==report['closed_ticket']==2*count and report['failure_code']==0
                assert report['published_ticket']==2*count and result['unknown'] is False
                expected_hp=100000 if branch=='unchanged' else 100000-count if branch=='changed_damage' else 100000+count
                assert int(report['last_hp_raw'])==expected_hp<<32 and int(report['max_hp_raw'])==1000000<<32
                assert result['scope_fields']==64 and result['publication_fields']%2==0
                if batch:assert report['batch_calls']>0 and report['batch_issued']==report['batch_finalized'] and report['maximum_batch_edges']<=32
                else:assert report['batch_calls']==0 and report['single_replayed']==2*count
                row={k:v for k,v in result.items() if k!='event_json'};row.update(event_sha256=event_sha,event_count=len(events),bundle_sha256=digest(source.encode()));rows.append(row)
            assert hashes[0]==hashes[1],(count,branch,hashes)
    receipt={'scope':'prefilled self-owned real CaptureLanes -> actual engine prepare/finalize/drain -> production shared HP callbacks','excluded':['detours','assembly ingress','IPC','paced concurrent producer','sustained throughput','Game FPS'],'binary_sha256':digest(a.binary.read_bytes()),'engine_sha256':digest((ROOT/'native_pc/native_event_runtime/engine.cpp').read_bytes()),'rows':rows}
    (a.output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8');print(a.output/'receipt.json')

if __name__=='__main__':main()
