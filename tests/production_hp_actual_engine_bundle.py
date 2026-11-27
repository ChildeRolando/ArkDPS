"""Reuse the frozen full-scope world, but consume through actual engine.cpp."""
from pathlib import Path
import argparse,json
from production_hp_quickjs_bundle import bundle
ROOT=Path(__file__).resolve().parents[1]

def engine_bundle(count=500,batch=True,branch='heal_coalesced',source_root=ROOT,pub_every=1000,bridge_root=None,paced=None,light=False):
    text=bundle(count,True,source_root=source_root,bridge_root=bridge_root or source_root,full_scope=True,pub_every=pub_every,light=light)
    marker='const results=[];'
    assert text.count(marker)==1
    text=text.split(marker)[0]
    for key,flag in [('Scope','false'),('Publication','true')]:
        original='c.__publishCapture'+key+'=graph=>{c.published'+key+'=JSON.parse(JSON.stringify(graph));};'
        assert text.count(original)==1,(key,text.count(original))
        text=text.replace(original,original.replace('};','__ownedGraphPublish('+flag+',graph);};'))
    # The older isolated replay fixture used synthetic revision 101 without
    # advancing its source owner. Actual engine retirement requires both owners
    # to share the real revision lifecycle, not a manual Map-only insertion.
    old='nativeCaptureScopeGraph(nativeAutoCache,nativeAutoStage,graphClock,101)'
    assert text.count(old)==1
    text=text.replace(old,'nativeCaptureScopeGraph(nativeAutoCache,nativeAutoStage,graphClock,++nativeCaptureScopeRevision)')
    text=text.replace('nativeCaptureScopes.set(101,graphScope)','nativeCaptureScopes.set(graphScope.revision,graphScope)')
    options=json.dumps(json.dumps({'count':count,'batch':batch,'branch':branch,'pub_every':pub_every,**(paced or {})}))
    if paced is not None:
        return text+r'''
__ownedSetCallbacks(setter,sourcePrefix,f.vm.runInContext('__arkdpsRetireCaptureGraphs',f.context));
f.write(enemy.add(0x40),fp(100000));
const report=JSON.parse(__ownedEngineRun(OPTIONS));
const outcome=f.vm.runInContext('flushCoalescedEnemyHp();({active,error:fatal,enemyStateUnknown,sequence,events:queue.filter(e=>e.kind==="enemy_hp")})',f.context);
print(JSON.stringify({kind:'paced_actual_engine_production_hp_self_owned',count:COUNT,scope_fields:scope.fields.length,publication_fields:publication.chunks.reduce((n,c)=>n+c.fields.length,0),report,source_active:outcome.active,source_error:outcome.error,unknown:outcome.enemyStateUnknown,event_json:JSON.stringify(outcome.events),dispatch_counters:dispatchCounters}));
'''.replace('OPTIONS',options).replace('COUNT',str(count))
    return text+r'''
__ownedSetCallbacks(setter,sourcePrefix,f.vm.runInContext('__arkdpsRetireCaptureGraphs',f.context));
f.write(enemy.add(0x40),fp(100000));
const report=JSON.parse(__ownedEngineRun(OPTIONS));
const outcome=f.vm.runInContext('flushCoalescedEnemyHp();({active,error:fatal,enemyStateUnknown,sequence,events:queue.filter(e=>e.kind==="enemy_hp")})',f.context);
assertion.equal(outcome.active,0);assertion.equal(outcome.error,null);assertion.equal(outcome.enemyStateUnknown,false);
assertion.equal(report.failure_code,0);assertion.equal(report.processed_ticket,2*COUNT);assertion.equal(report.closed_ticket,2*COUNT);
assertion.equal(report.raw_entry_count,COUNT);assertion.equal(report.raw_leave_count,COUNT);
if(BATCH)assertion(report.batch_calls>0&&report.batch_issued===report.batch_finalized&&report.maximum_batch_edges<=32,'actual batch path');
else assertion.equal(report.batch_calls,0,'actual single core control');
if(BRANCH==='unchanged')assertion.equal(outcome.events.length,0);
else {assertion.equal(outcome.events[0].hp_before,100000);assertion.equal(outcome.events.at(-1).hp_after,BRANCH==='changed_damage'?100000-COUNT:100000+COUNT);}
print(JSON.stringify({kind:'actual_engine_production_hp_self_owned',branch:BRANCH,count:COUNT,batch:BATCH,scope_fields:scope.fields.length,publication_fields:publication.chunks.reduce((n,c)=>n+c.fields.length,0),report,unknown:outcome.enemyStateUnknown,event_json:JSON.stringify(outcome.events),dispatch_counters:dispatchCounters,native_requests:nativeRequests,excluded:['detours','assembly ingress','IPC','Game FPS']}));
'''.replace('OPTIONS',options).replace('COUNT',str(count)).replace('BATCH','true' if batch else 'false').replace('BRANCH',json.dumps(branch))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--count',type=int,default=500);p.add_argument('--single',action='store_true');p.add_argument('--branch',choices=['heal_coalesced','changed_damage','unchanged'],default='heal_coalesced');p.add_argument('--source-root',type=Path,default=ROOT);p.add_argument('--bridge-root',type=Path);a=p.parse_args();a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(engine_bundle(a.count,not a.single,a.branch,a.source_root,bridge_root=a.bridge_root),encoding='utf-8')
