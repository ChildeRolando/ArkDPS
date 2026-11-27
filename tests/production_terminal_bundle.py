"""Real Destroy callback followed by the engine's out-of-body publication."""
from production_sampling_bundle import ROOT, sampling_bundle


def terminal_bundle(source_root=ROOT, case='released'):
    text=sampling_bundle(source_root).rsplit('\nconst rows=[];',1)[0]
    return text+r'''
{
f.context.__arkdpsPublicationJournal=__arkdpsPublicationJournal;
for(const name of Object.keys(original).filter(name=>name.startsWith('read'))){
 const before=original[name];P.prototype[name]=function(...args){return (__arkdpsReplayActive()?NativePointer.prototype[name]:before).apply(this,args);};
}
const destroy=f.callback('OnDestroy',f.controllerClass);f.context.testDestroy=destroy;
f.vm.runInContext('prepareNativeCaptureHook(ptr(0x9100),testDestroy)',f.context);
assertion(destroy.arkdpsCaptureContract.complete,'actual Destroy capture contract');
const plan=JSON.parse(JSON.stringify(destroy.arkdpsCaptureContract));
const mode=CASE;
if(mode==='missing_body')plan.fields=plan.fields.filter(field=>field.phase!=='leave'||field.role!=='body');
if(mode==='missing_singleton')plan.fields=plan.fields.filter(field=>field.phase!=='leave'||
 !(field.name==='terminal.singleton'||field.name.startsWith('root.')));
const parent=original.readPointer.call(f.controllerClass.add(0x58));
const singleton=original.readPointer.call(parent.add(0xb8));
let report;
const options={plan,controller:f.controller.toString(),singleton:singleton.toString(),release:mode!=='retained'};
if(mode==='stage_changed')Object.assign(options,{stage_slot:f.level.add(0x18).toString(),stage_value:original.readPointer.call(enemy.add(0x148)).toString()});
try{report=JSON.parse(__ownedDestroyRun(JSON.stringify(options),destroy));}
catch(error){print(JSON.stringify({case:mode,error:String(error),native_status:__nativeOwnedStatus()}));throw error;}
assertion.equal(report.failure,0);assertion.equal(report.raw,2);assertion.equal(report.processed,2);assertion.equal(report.closed,2);
if(mode==='stage_changed'){
 assertion.equal(report.published,0,'changed old stage must reject the Destroy endpoint');assertion.equal(report.terminal_fence,false);
 print(JSON.stringify({case:mode,report,stage_rejected:true}));
}else{
assertion.equal(report.published,2);assertion.equal(report.terminal_fence,true);
const mark=__arkdpsPublicationRequest({operation:'mark',sampling:'receipt',publication_token:report.token});
assertion(mark.native_publication.terminal_verified===true,'only actual Destroy completion certifies terminal');
assertion.equal(__arkdpsPublicationJournal.readThrough,0);assertion.equal(__arkdpsPublicationJournal.acknowledged,0);
const before=report.token;
const terminal=JSON.parse(__ownedTerminalRequest(JSON.stringify({operation:'mark',sampling:'terminal'})));
assertion.equal(terminal.reply.native_publication.token,before,'scheduler must serve the actual Destroy endpoint');
assertion.equal(terminal.before_requests,terminal.after_requests,'terminal reuse cannot allocate a request');
assertion.equal(terminal.before_attempts,terminal.after_attempts,'terminal reuse cannot take a sample');
let rejectedAck=false;try{__ownedTerminalRequest(JSON.stringify({operation:'ack',sequence:1}));}catch(error){rejectedAck=String(error).includes('ACK exceeds delivered');}
assertion(rejectedAck,'unserved ACK must remain rejected');
const tail=JSON.parse(__ownedTerminalRequest(JSON.stringify({operation:'terminal_read',publication_token:before,after:0,through:0})));
assertion(tail.reply.terminal_only===true&&tail.reply.retry===false,'real scheduler/journal terminal read');
const ack=JSON.parse(__ownedTerminalRequest(JSON.stringify({operation:'ack',sequence:0})));
assertion.equal(ack.reply.acknowledged,0);
const checkpoint=JSON.parse(__ownedSamplingRun(JSON.stringify({frame:30})));
assertion.equal(checkpoint.accepted,false,'checkpoint must not re-issue the terminal certificate');
assertion.equal(checkpoint.failure,0,'checkpoint not-ready must remain healthy');
assertion.equal(__arkdpsPublicationJournal.latest.token,before,'checkpoint must preserve the completed Destroy endpoint');
const retained=__arkdpsPublicationRequest({operation:'mark',sampling:'receipt',publication_token:before});
assertion.equal(retained.native_publication.token,before);
print(JSON.stringify({case:mode,report,checkpoint,terminal_verified:mark.native_publication.terminal_verified,
 scheduler:terminal,unserved_ack_rejected:rejectedAck,terminal_read:tail.reply.terminal_only,
 delivered:__arkdpsPublicationJournal.readThrough,acknowledged:__arkdpsPublicationJournal.acknowledged,
 excluded:['Game','FPS','detours','IPC']}));
}
}
'''.replace('CASE',__import__('json').dumps(case))
