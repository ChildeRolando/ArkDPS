"""Rebuild exact baseline/final damage field ledger and real common scope table.
Run from repository root: python tests/damage_demand_aggregate.py
No production writes; all snapshots/results are under runtime/capture-demand/final.
"""
from pathlib import Path
import csv, hashlib, json, subprocess
ROOT = Path(__file__).resolve().parents[1]
BASE = 'ef1486640e42146e7a8aaaae22488b1ac0be35f0'
EXPECTED = 'd0c972dfc2c5d376fd640b6212baf972600dbc1e01369b0adc5bb4f4491b4ad6'
OUT = ROOT / 'runtime/capture-demand/final'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def node(script, *args):
    return json.loads(subprocess.check_output(['node', str(ROOT / 'tests' / script), *map(str,args)], cwd=ROOT))
def key(f):
    return json.dumps({k:f.get(k) for k in ('phase','name','role','root','root_index','pointer_offsets','offset','bytes','kind','guard_id','guard_side')},sort_keys=True)
def classify(h,f,kept):
    n,o,phase=f['name'],f['offset'],f['phase']
    if kept:
        if n.startswith('clock'): return 'KEEP', 'E/L clock or publication-tier envelope; not ordinary scope', 'runtime45-46,793-802,3148,3312'
        if n.startswith('blackboard'): return 'KEEP','dynamic before/after; first matching key; count/member/close guard','runtime1592-1598,3274,3482-3490'
        if n.startswith('buff.'): return 'KEEP','Block E parent Buff key/source/flag and source Character dataUID','runtime807,3270-3293'
        if phase=='checkpoint': return 'KEEP','exact parent.body source/target/HP/modifier identity; nested proof and stable anchor','adapter272-284; runtime689-706,995-1020,1332-1589'
        if n.startswith('receiver'): return 'KEEP','OnTake receiver target identity and HP-before; trace return evidence','runtime3181-3211,3337-3372'
        if n=='modifier' and o==128: return 'KEEP_FORMULA_COUPLING','Apply originAtk/originValue mitigation match; independent formula owner','runtime2230-2231'
        if n.startswith('source'): return 'KEEP','E source identity/dataUID; Apply/Output token/form and stable return source; source HP/born unused','runtime483-498,3150-3260,2966-2976'
        return 'KEEP','modifier amount/delta/domain/cancel and target identity/HP at current exact callback point','runtime556-613,968-1107,1291-1326,3337-3479'
    if h=='Buff.OnTakeDamage': return 'DELETE','Buff E clock+opaque push; L pop; child Buff reads independently retained','runtime3148-3149,3309'
    if n.startswith('receiver'): return 'DELETE','Apply/Output never dereferences args0 receiver','runtime3150-3178,3213-3260'
    if 'source' in n and o in (0x25,0x40) and f['pointer_offsets'][-1:] != [0x538]: return 'DELETE','source HP/born has no damage consumer; target HP is retained','runtime464-498,3150-3260'
    if 'target' in n and o==0x25: return 'DELETE','domain consumes klass/CID/UID rather than born flags','runtime556-575'
    if h.endswith('.OnTakeDamage') and n.startswith('source'): return 'DELETE','leaf E only class/CID/dataUID; leaf L identity recheck uses exact parent.body; no token/form','runtime995-1011,1085-1107,3190-3202; adapter272-284'
    if h=='BlockDamage._AssignDynamicVar': return 'DELETE','E needs only modifier target/value/target UID; L needs blackboard+clock','runtime3258-3293,3481-3490'
    if n=='modifier' and o==128: return 'DELETE','origin fields used only by Apply mitigation match, not leaf callbacks','runtime2230-2231'
    return 'DELETE','redundant broad source/target shape; narrow direct-demand evidence retained','runtime3150-3260,3337-3490'
def write(name,obj): (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def csv_write(name,rows):
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def main():
    assert sha(ROOT/'event_probe_native_pc.js')==EXPECTED, 'source changed; do not mislabel final snapshot'
    OUT.mkdir(parents=True,exist_ok=True)
    baseline=OUT/'baseline-adapter-ef148664.js'
    baseline.write_bytes(subprocess.check_output(['git','show',BASE+':event_probe_native_pc.js'],cwd=ROOT))
    (OUT/'final-adapter.js').write_bytes((ROOT/'event_probe_native_pc.js').read_bytes())
    plans=node('damage_demand_field_ledger.js',baseline);write('field-plans.json',plans)
    rows=[];counts={}
    for h,b in plans['base'].items():
        c=plans['current'][h];cur={key(f):f for f in c['fields']};base={key(f):f for f in b['fields']}
        for f in [*b['fields'],*[f for f in c['fields'] if key(f) not in base]]:
            nf=cur.get(key(f));old=base.get(key(f));decision,use,refs=classify(h,f,nf is not None)
            if old is None: decision='NARROW_REPLACEMENT'
            rows.append(dict(hook=h,baseline_field_id=old['field_id'] if old else None,current_field_id=nf['field_id'] if nf else None,**{k:f.get(k) for k in ('phase','name','role','root','root_index','pointer_offsets','offset','bytes','kind','guard_id','guard_side')},decision=decision,branch_use=use,refs=refs,current_tier=nf.get('tier') if nf else None))
        counts[h]=dict(baseline=len(b['fields']),current=len(c['fields']),removed=len(base.keys()-cur.keys()),narrow_replacement=len(cur.keys()-base.keys()),required_projections=c['required_projections'])
    common=node('damage_demand_common_scope.js');write('common-scope-plan.json',common)
    scope=[]
    edges='ordinary OnTake/Block L only; Apply/Output E conditional token/form/actor-local issue proof + L time(); any pub/cold E or L always fullscope'
    for f in common['graph']['fields']:
        role=f['role']
        if 'clock' in role: use='independent frame/fixed equals event when; also native before/after guard';refs='adapter957-966,972-986'
        elif 'stage_text' in role: use='String class/length/UTF16 stage ID equality against sealed graph stage ID';refs='adapter667-704,957-961'
        elif 'logger.class' in role or 'stats.class' in role: use='dynamic klass pointer; frozen metadata className BattleLogger/BattleStats; no arbitrary negative';refs='adapter969-970,1005-1008'
        elif 'controller.state' in role: use='dynamic state at both edges, allow 2 or3';refs='adapter966,1009-1012'
        elif role in ('scope.open.3','scope.close.3','scope.open.4','scope.close.4'): use='dynamic phase/round; allowed phase set and nonnegative integer round; scope key/mode';refs='adapter966,1009-1016'
        else: use='exact frozen pointer/int/block words or same-point pair; native graph proof retains OwnedView/guard uniqueness and raw comparisons';refs='adapter667-704,735-755,957-970; native GraphCheck executor'
        scope.append(dict(field_id=f['field_id'],role=role,address=f['absolute_address'],pointer_offsets=f['pointer_offsets'],bytes=f['bytes'],kind=f['kind'],guard_id=f['guard_id'],guard_side=f['guard_side'],expected=f.get('expected'),comparison=f.get('comparison'),projection=f.get('projection'),consumer_edges=edges,purpose=use,refs=refs))
    assert len(scope)==64
    hashes={n:sha(ROOT/n) for n in ['event_probe_native_pc.js','event_probe_runtime.js','tests/damage_demand_aggregate.py','tests/damage_demand_field_ledger.js','tests/damage_demand_common_scope.js']}
    write('damage-field-ledger.json',dict(base=BASE,final_source_sha256=EXPECTED,sha256=hashes,counts=counts,scope_policy=edges,rows=rows,limits=['No DLL/actualgame/FPS evidence','Original round2 files retained unchanged','Field identity matches semantic name/role+geometry, not shifting numeric ID']))
    csv_write('damage-field-ledger.csv',rows);write('common-scope-ledger.json',dict(source_sha256=EXPECTED,actual_field_count=64,fields=scope));csv_write('common-scope-ledger.csv',scope)
    md=['# Final damage demand ledger','',f'Final adapter SHA256: `{EXPECTED}`. Baseline `{BASE}`. Reproduce: `python tests/damage_demand_aggregate.py`. Root run from repo.','',f'{len(rows)} exact baseline/replacement rows; all decision/use/ref columns in CSV/JSON. Round2 retained at parent directory.','', '| Hook | Baseline | Final | Removed | New narrow |','|---|---:|---:|---:|---:|']
    md += [f'| {h} | {c["baseline"]} | {c["current"]} | {c["removed"]} | {c["narrow_replacement"]} |' for h,c in counts.items()]
    md += ['', 'Third-round scope policy: '+edges+'. E/L clock and parent byte evidence remain. Consumer policies and amounts stay shared-JS-owned; no parallel C++ business state.','', 'Companion common-scope-ledger has every one of actual 64 scope fields from world rolesPerPlayer10+token, with exact before/after role, address, expected predicate, dynamic projection and branch refs. Static graph predicates authorize no actor/amount inference.']
    (OUT/'damage-field-ledger.md').write_text('\n'.join(md)+'\n',encoding='utf8')
    (OUT/'common-scope-ledger.md').write_text('# Common scope consumer map\n\n'+edges+'\n\n| Role | Bytes | Purpose | Branch refs |\n|---|---:|---|---|\n'+'\n'.join(f'| {z["role"]} | {z["bytes"]} | {z["purpose"]} | {z["refs"]} |' for z in scope)+'\n',encoding='utf8')
    assert sha(ROOT/'event_probe_native_pc.js')==EXPECTED, 'source changed during generation'
    print(json.dumps(dict(rows=len(rows),scope_fields=len(scope),source_sha256=EXPECTED,output=str(OUT))))
if __name__=='__main__': main()
