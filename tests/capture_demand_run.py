"""Actual producer read/byte ledger. Instrumented output is never a timing result.

Run with a frozen --source-root; fixtures and production inputs are hash-bound.
--reference-input enforces identical synthetic heap, args and hook identities.
"""
from pathlib import Path
import argparse,csv,hashlib,json,os,subprocess
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dependencies',type=Path,default=ROOT/'runtime/fresh-sampling/production-final/dependencies')
    p.add_argument('--compiler',default='C:/msys64/ucrt64/bin/g++.exe')
    p.add_argument('--reference-input',type=Path)
    p.add_argument('--compare',type=Path,help='Baseline result directory for comparison.csv and report.md')
    p.add_argument('--label',default='unspecified frozen source')
    args=p.parse_args();source=args.source_root.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    native=source/'native_pc/native_event_runtime';inputs=out/'input';inputs.mkdir(exist_ok=True)
    relevant=[*sorted(native.glob('*.h')),*sorted(native.glob('*.js')),native/'native_capture.cpp',*[source/name for name in ['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','event_probe_store.js']],*sorted((source/'tests').glob('native_pc_*fixture.js'))]
    before={str(x.relative_to(source)):digest(x) for x in relevant}
    env=dict(os.environ,PATH=str(Path(args.compiler).parent)+';'+os.environ['PATH'])
    receipt={'kind':'instrumented_owned_producer_read_count','source_label':args.label,'source_root':str(source),'source_sha256':before,'timing_result':False,'game_access':False,'backend_owned_reads_measured':False,'runs':[]}
    def run(command,name,timeout=60):
        r=subprocess.run(list(map(str,command)),cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        (out/(name+'.stdout')).write_text(r.stdout,encoding='utf-8');(out/(name+'.stderr')).write_text(r.stderr,encoding='utf-8')
        receipt['runs'].append({'name':name,'command':list(map(str,command)),'returncode':r.returncode})
        (out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        if r.returncode:raise RuntimeError(name+' failed: '+r.stderr[-2000:])
        return r
    run(['node',ROOT/'tests/capture_demand_world.js',source,inputs],'world')
    world=json.loads((inputs/'world.json').read_text())
    if args.reference_input:
        reference=args.reference_input.resolve();old=json.loads((reference/'world.json').read_text())
        receipt['reference_input']={'path':str(reference),'heap_sha256':digest(reference/'heap.bin'),'candidate_heap_sha256':digest(inputs/'heap.bin')}
        (out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        assert digest(reference/'heap.bin')==digest(inputs/'heap.bin'),'candidate heap changed; not same-input comparison'
        assert [(h['name'],h['overload'],h['args'],h['parent_refs']) for h in old['hooks']]==[(h['name'],h['overload'],h['args'],h['parent_refs']) for h in world['hooks']]
    code=(native/'native_capture.cpp').read_text(encoding='utf-8')
    prefix='''extern void audit_enter(unsigned,unsigned,unsigned);extern void audit_leave();
struct AuditScope{AuditScope(unsigned f,unsigned r,unsigned c){audit_enter(f,r,c);}~AuditScope(){audit_leave();}};
'''
    # RAII labels restore the parent projection after recursive child traversal.
    needle='const auto& field = plan.fields[index];'
    assert code.count('Error field_capture(')==1
    field_start=code.index('Error field_capture(');at=code.index(needle,field_start)+len(needle)
    code=code[:at]+'\n    AuditScope audit_scope(field.id,field.role,writer.checkpoint);'+code[at:]
    proj_start=code.index('Error projection_body(');at=code.index('const auto& node = plan.projections[index];',proj_start)+len('const auto& node = plan.projections[index];')
    code=code[:at]+'\n    AuditScope audit_scope(field.id,field.role,writer.checkpoint);'+code[at:]
    instrumented=out/'instrumented-native_capture.cpp';instrumented.write_text(prefix+code,encoding='utf-8')
    binary=out/'producer-ledger.exe'
    command=[args.compiler,'-std=c++17','-static','-O1','-DNOMINMAX','-I'+str(native),'-I'+str(args.dependencies.resolve()),ROOT/'tests/capture_demand_producer.cpp',instrumented,'-o',binary]
    run(command,'build',90);result=run([binary,inputs/'world.json',inputs/'heap.bin'],'trace',90)
    data=json.loads(result.stdout);(out/'trace.json').write_text(result.stdout,encoding='utf-8')
    rows=[];summary=[]
    for case in data['cases']:
        identity={k:case[k] for k in ['name','overload','scenario','edge']}
        rows.extend([{**identity,**r} for r in case['reads']])
        summary.append({**{k:v for k,v in case.items() if k not in ['reads','field_states']},'field_error_counts':{str(e):sum(f['error']==e for f in case['field_states']) for e in sorted(set(f['error'] for f in case['field_states']))}})
        assert case['capture_reads']+sum(r['category']=='abi' for r in case['reads'])==case['native_reads_with_abi']
        assert sum(b['packed_bytes'] for b in case['buckets'].values())+case['record_header_bytes']+case['parent_checkpoint_bytes']==case['packed_enqueue_bytes']
    with (out/'read-ledger.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (out/'summary.json').write_text(json.dumps({'sizeof':data['sizeof'],'cases':summary},indent=2),encoding='utf-8')
    report=['# Producer read and byte ledger','',f'Source: {args.label}. {len(summary)} boundary cases; {len(rows)} native read requests.',
            '', 'Counts come from actual capture_scope_point and CaptureLanes::publish_to_lane over an owned synthetic heap. ABI return-address and selected stack-argument reads follow onEnter and are reported separately from Snapshot read_count. Field labels are the only native instrumentation.',
            '', 'This is a counting build, not a performance or game FPS result. Backend OwnedReplayView reads are not measured and are never counted as producer reads. Queue bytes include the exact serialized header, parent checkpoints, FieldState, Segment and payload; they exclude preallocated queue metadata and unused arena capacity.',
            '', 'Input has '+str(len(world['scope']['fields']))+' scope fields and '+str(sum(len(c['fields']) for c in world['publication']['chunks']))+' publication fields. These reflect this fixture roster, not an assertion about the field count of every live battle. The synthetic source data has null CID/host pointers, so its optional missing-root field states remain visible in trace.json; this is one reproducible branch, not a maximum-read bound. capture_error=0 does not assert business semantic acceptance.',
            '', 'ordinary uses the warm graph; publication requests the leave endpoint; cold explicitly requests a cold binding at both boundaries; nested_parent adds a live ApplyModifier parent and its checkpoint. Cold projection field reads keep the actual dynamic field IDs.',
            '', '| Hook | Overload | Edge | Capture reads | ABI reads | Payload bytes | Enqueued bytes |','| --- | ---: | --- | ---: | ---: | ---: | ---: |']
    for c in summary:
        if c['scenario']=='ordinary':report.append(f"| {c['name']} | {c['overload']} | {c['edge']} | {c['capture_reads']} | {c['native_reads_with_abi']-c['capture_reads']} | {c['payload_bytes']} | {c['packed_enqueue_bytes']} |")
    if args.compare:
        old=json.loads((args.compare/'summary.json').read_text())['cases'];key=lambda c:(c['name'],c['overload'],c['scenario'],c['edge'])
        previous={key(c):c for c in old};assert set(previous)=={key(c) for c in summary}
        comparison=[];report+=['','## Same-input comparison','','| Hook | Overload | Edge | Capture reads before → after | Enqueued bytes before → after |','| --- | ---: | --- | ---: | ---: |']
        for c in summary:
            b=previous[key(c)];r={k:c[k] for k in ['name','overload','scenario','edge']}
            for measure in ['capture_reads','capture_read_bytes','native_reads_with_abi','payload_bytes','fields','segments','packed_enqueue_bytes']:
                r[measure+'_before']=b[measure];r[measure+'_after']=c[measure];r[measure+'_delta']=c[measure]-b[measure]
            comparison.append(r)
            if c['scenario']=='ordinary':report.append(f"| {c['name']} | {c['overload']} | {c['edge']} | {b['capture_reads']} → {c['capture_reads']} | {b['packed_enqueue_bytes']} → {c['packed_enqueue_bytes']} |")
        with (out/'comparison.csv').open('w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=list(comparison[0]));w.writeheader();w.writerows(comparison)
        def field_counts(directory):
            result={}
            with (directory/'read-ledger.csv').open(encoding='utf-8-sig',newline='') as f:
                for row in csv.DictReader(f):
                    k=tuple(row[x] for x in ['name','overload','scenario','edge','category','field_name','role','checkpoint']);v=result.setdefault(k,Counter());v['reads']+=1;v['bytes']+=int(row['bytes'])
            return result
        left,right=field_counts(args.compare),field_counts(out);field_diff=[]
        for k in sorted(left.keys()|right.keys()):
            b,a=left.get(k,Counter()),right.get(k,Counter())
            field_diff.append(dict(zip(['name','overload','scenario','edge','category','field_name','role','checkpoint'],k))|{'reads_before':b['reads'],'reads_after':a['reads'],'bytes_before':b['bytes'],'bytes_after':a['bytes']})
        with (out/'field-comparison.csv').open('w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=list(field_diff[0]));w.writeheader();w.writerows(field_diff)
    (out/'report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    receipt.update({'binary_sha256':digest(binary),'heap_sha256':digest(inputs/'heap.bin'),'world_sha256':digest(inputs/'world.json'),'instrumented_source_sha256':digest(instrumented),'tool_sha256':{x.name:digest(x) for x in [ROOT/'tests/capture_demand_world.js',ROOT/'tests/capture_demand_producer.cpp',Path(__file__)]},'case_count':len(summary),'read_count':len(rows),'capture_error_cases':[{k:c[k] for k in ['name','overload','scenario','edge','capture_error']} for c in summary if c['capture_error']]})
    assert before=={str(x.relative_to(source)):digest(x) for x in relevant},'source changed during audit'
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(json.dumps({k:receipt[k] for k in ['case_count','read_count','capture_error_cases','heap_sha256']}))
if __name__=='__main__':main()
