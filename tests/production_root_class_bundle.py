"""Actual-engine cold root gates; faults affect only this process's owned bytes."""
from pathlib import Path
from production_sampling_bundle import ROOT, sampling_bundle


def root_class_bundle(source_root=ROOT):
    # Keep the existing nine-row sampler gate intact. This is a separate run.
    text = sampling_bundle(source_root).rsplit('\nconst rows=[];', 1)[0]
    return text + r'''
f.context.__arkdpsPublicationJournal=__arkdpsPublicationJournal;
// Setup retains fixture Pointer instances in immutable roots. Their original
// scalar methods read the setup Map. During replay all those reads must use
// the actual native owned lease, including the newly added class precheck.
for(const name of Object.keys(original).filter(name=>name.startsWith('read'))){
 const before=original[name];P.prototype[name]=function(...args){
  return (__arkdpsReplayActive()?NativePointer.prototype[name]:before).apply(this,args);
 };
}
f.vm.runInContext('nativeCaptureScopes.clear();nativeCapturePublications.clear();nativeCaptureModeEpoch=1;nativeCaptureModeHistory.set(1,nativeAutoDesiredMode);nativeAutoCache=null;nativeAutoScope=null;nativeAutoBodyCache=null;nativeLastBinding=null;',f.context);
const rows=[];
const journalState=()=>({token:__arkdpsPublicationJournal.latest?.token??null,
 published:__arkdpsPublicationJournal.publishedThrough,read:__arkdpsPublicationJournal.readThrough,
 ack:__arkdpsPublicationJournal.acknowledged});
function sample(frame,options={}){
 const result=JSON.parse(__ownedSamplingRun(JSON.stringify({frame,cold:true,mode_epoch:1,...options})));
 rows.push({...result,mark:undefined});return result;
}
const first=sample(31);assertion(first.accepted,'first mode1 cold sample');
const logger=original.readPointer.call(f.controller.add(0xe0));
const stats=original.readPointer.call(logger.add(0x30));
const unknown='0x7fff12120000';
const cases=[['controller_known',f.controller,f.enemyClass.toString()],
 ['controller_unknown',f.controller,unknown],['controller_null',f.controller,'0x0'],
 ['logger_unknown',logger,unknown],['level_unknown',f.level,unknown],['stats_unknown',stats,unknown]];
let frame=32;
for(const [name,address,value]of cases){
 const before=journalState(),prior=rows.at(-1);
 const rejected=sample(frame++,{fault:'root_class',address:address.toString(),value});
 assertion.equal(rejected.accepted,false,name+' must reject');assertion.equal(rejected.failure,0,name+' must not poison');
 assertion.equal(rejected.token,prior.token,name+' must not replace the publication');
 assertion.equal(rejected.completed_request,prior.completed_request,name+' must not complete the request');
 assertion.equal(rejected.delivered_sequence,0,name+' must not deliver');
 assertion.equal(JSON.stringify(journalState()),JSON.stringify(before),name+' must not publish/read/ACK');
 const recovered=sample(frame++);assertion(recovered.accepted,name+' must recover with restored class');
 assertion(recovered.token!==before.token,name+' recovery needs a new actual sample');
}
// A correct class does not authorize a missing body. Keep this adversarial
// contract last: its real structural gap must permanently poison this process.
const shortPlan=JSON.parse(JSON.stringify(samplingPlan));
shortPlan.cold_plan.projections[shortPlan.cold_plan.fields[0].projection_root].bytes=8;
__ownedConfigureSampler(JSON.stringify(shortPlan));
const beforeMissing=journalState();let missing=null;
try{sample(frame++);}catch(error){missing={rejected:true,native_status:__nativeOwnedStatus(),error:String(error)};}
assertion(missing?.rejected&&missing.native_status===9,'a genuinely missing body remains structural');
assertion.equal(JSON.stringify(journalState()),JSON.stringify(beforeMissing),'missing body must not publish/read/ACK');
for(const row of rows){assertion.equal(row.raw,0);assertion.equal(row.processed,0);assertion.equal(row.closed,0);assertion.equal(row.sequence,0);assertion.equal(row.failure,0);}
print(JSON.stringify({kind:'production_cold_root_class_gates',cases:cases.map(c=>c[0]),rows,missing,
 excluded:['Game','FPS','detours','IPC','initialize/worker request integration']}));
'''
