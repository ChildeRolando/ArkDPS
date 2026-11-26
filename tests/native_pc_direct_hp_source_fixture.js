"use strict";
// Source-level controlled issuer simulator; native JSValue issuance and dispatch
// are independently tested by the bridge/C++ owner. All HP state and reads here
// use real callbacks, real capture recipes and the strict owned-field harness.
const assert=require('assert'),path=require('path'),vm=require('vm');
const {fixture}=require('./native_pc_frida_probe_fixture.js'),{ownedFields}=require('./native_pc_owned_field_fixture.js');
function run(direct,mode='damage'){
 const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),settlement:true,enemyHpState:true});
 f.context.modeOnlySource=process.env.ARKDPS_SOURCE_MODE_RECORD==='1';
 f.write(f.enemyClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));
 f.context.rpc.exports.mark();f.clock(30,4294967296n);
 const target=f.unit('enemy_test',12),attributes=f.alloc(),cache=f.alloc(),stringClass=f.alloc();
 f.write(target,f.enemyClass);f.write(target.add(0x25),1);f.write(target.add(0x26),0);f.write(target.add(0x40),50n*4294967296n);
 f.write(target.add(0xb0),attributes);f.write(attributes.add(0x68),cache);f.write(cache.add(0x18),1);f.write(cache.add(0x20),0n);f.write(cache.add(0x28),100n*4294967296n);f.write(cache.add(0x30),1);
 for(const p of [target.add(0x148).readPointer(),f.level.add(0x18).readPointer()])f.write(p,stringClass);
 const meta={clock:vm.runInContext('clock',f.context),string_class:stringClass.toString(),skill_offsets:vm.runInContext('({...skillF})',f.context),immutable_class_parents:true,classes:{BObject:f.bObject.toString(),Enemy:f.enemyClass.toString(),BattleController:f.controllerClass.toString(),LevelData:f.level.readPointer().toString()}};
 const harness=ownedFields(f,meta),baseline=f.context.rpc.exports.mark(),roots=Object.values(baseline.native_identity).join(':');
 f.context.scopeCache={autochess:false,identity:baseline.native_identity,roots,scopeGuards:[],addresses:{}};
 const scope=JSON.parse(vm.runInContext('JSON.stringify(nativeCaptureScopeGraph(scopeCache,nativeAutoStage,clock,1))',f.context));f.context.scopeGraph=scope;vm.runInContext('nativeCaptureScopes.set(1,scopeGraph)',f.context);
 const hook=f.callback('SetHpInternal',f.entity);f.context.directHook=hook;f.context.observationTarget=target;
 vm.runInContext(`const issued=new WeakMap();globalThis.issueSourceIo=()=>{const io=Object.create(pointerHpIo);if(globalThis.directFault==='unused')Object.defineProperty(io,'attributeWords',{value:()=>__arkdpsReplayGap('unused HP attribute missing')});if(globalThis.directFault==='closing')Object.defineProperty(io,'closing',{value:()=>__arkdpsReplayGap('demanded HP closing missing')});Object.freeze(io);issued.set(io,{record:__arkdpsReplay.record,contract:directHook.arkdpsCaptureContract,root:observationTarget.toString()});return io;};
 globalThis.__arkdpsIsPreparedHpObservation=(io,contract,enemy)=>{const owner=issued.get(io);return !!owner&&owner.record===__arkdpsReplay.record&&owner.contract===contract&&(enemy===undefined||enemy.toString()===owner.root);};
 arkdpsNativeAsyncCapture=true;prepareNativeCaptureHook(ptr(0x9000),directHook);
 globalThis.publicModeRecordReads=0;const realPublicRecord=__arkdpsReplayRecord;globalThis.__arkdpsReplayRecord=()=>{publicModeRecordReads++;return realPublicRecord();};
 globalThis.__arkdpsReplayModeRecord=modeOnlySource?()=>Object.freeze({edge:__arkdpsReplay.record.edge,facts:Object.freeze({mode_epoch:__arkdpsReplay.record.facts.mode_epoch})}):undefined;
 globalThis.finishCalls=0;const realFinish=finishModifierHpWriter;finishModifierHpWriter=function(...args){finishCalls++;return realFinish(...args);};`,f.context);
 f.context.directFault=mode==='unused-error'?'unused':null;
 const plan=hook.arkdpsCaptureContract,call={threadId:1};let latestIo;
 function invoke(edge){
  const body=harness.captureFields(plan.fields.filter(field=>field.phase===edge),[target],[target],[]),guards=harness.captureFields(scope.fields.map((field,index)=>({...field,field_id:1000+index})),[target],[target],[]);
  const record={version:1,edge,checkpoint:0,thread_id:1,fields:[...body.fields,...guards.fields],segments:[...body.segments,...guards.segments],facts:{mode_epoch:0,scope_mode_epoch:0,scope_revision:1}};
  harness.replay(record,()=>{
   if(direct){let io=f.context.issueSourceIo();if(mode==='forged'&&edge==='entry')io={verified:true,identity(){throw Error('forged IO read');}};if(mode==='old-lease'&&edge==='leave')io=latestIo;try{edge==='entry'||mode==='wrong-edge'?hook.arkdpsDirectHpEntry.call(call,mode==='wrong-root'?f.ptr(0xdead):target,io):hook.arkdpsDirectHpLeave.call(call,io);}catch(error){if(!['forged','old-lease','wrong-edge','wrong-root','demanded-error'].includes(mode))throw error;}latestIo=io;}
   else edge==='entry'?hook.onEnter.call(call,[target]):hook.onLeave.call(call);
  });return f.context.fixtureSummary;
 }
 const beforeEntry=f.context.publicModeRecordReads;const entry=invoke('entry');const entryPublicReads=f.context.publicModeRecordReads-beforeEntry;if(mode==='demanded-error')f.context.directFault='closing';if(mode==='damage')f.write(target.add(0x40),10n*4294967296n);else if(mode==='heal')f.write(target.add(0x40),60n*4294967296n);
 const beforeLeave=f.context.publicModeRecordReads;const leave=mode==='forged'?null:invoke('leave');const leavePublicReads=f.context.publicModeRecordReads-beforeLeave;
 const value={publicReads:{entry:entryPublicReads,leave:leavePublicReads},entry:JSON.parse(JSON.stringify(entry)),leave:leave&&JSON.parse(JSON.stringify(leave)),queue:JSON.parse(vm.runInContext('JSON.stringify(queue)',f.context)),active:vm.runInContext('active',f.context),epoch:vm.runInContext('epoch',f.context),unknown:vm.runInContext('enemyStateUnknown',f.context),finish:f.context.finishCalls};
 assert.equal(harness.late(),0);harness.restore();return value;
}
for(const mode of ['damage','heal','unchanged']){const generic=run(false,mode),direct=run(true,mode);const g={...generic},d={...direct};delete g.publicReads;delete d.publicReads;assert.deepEqual(d,g,mode);assert.equal(direct.finish,1,mode);assert.equal(direct.active,0);if(process.env.ARKDPS_SOURCE_MODE_RECORD==='1'){assert.equal(direct.publicReads.entry,0);if(mode==='unchanged')assert.equal(direct.publicReads.leave,0);assert(generic.publicReads.entry>0);}}
const forged=run(true,'forged');assert.equal(forged.entry.complete,false);assert.equal(forged.queue.length,0);
const wrongRoot=run(true,'wrong-root');assert.equal(wrongRoot.entry.complete,false);assert.equal(wrongRoot.queue.length,0);
const stale=run(true,'old-lease');assert.equal(stale.leave.complete,false);
const wrong=run(true,'wrong-edge');assert.equal(wrong.leave.complete,false);
const unused=run(true,'unused-error');assert.equal(unused.leave.complete,true);assert.equal(unused.finish,1);assert.equal(unused.active,0);
const demanded=run(true,'demanded-error');assert.equal(demanded.leave.complete,false);assert.equal(demanded.queue.length,0);
{
 const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),enemyHpState:true});
 const h=ownedFields(f,{clock:vm.runInContext('clock',f.context),string_class:'0x1000',classes:{Enemy:f.enemyClass.toString()},skill_offsets:{}});
 f.context.normal=f.callback('SetHpInternal',f.entity);f.context.other=f.callback('SetHpInternalDirectly',f.entity);
 vm.runInContext("arkdpsNativeAsyncCapture=true;globalThis.__arkdpsIsPreparedHpObservation=undefined;prepareNativeCaptureHook(ptr(0x9100),normal);globalThis.oldApiFallback=normal.arkdpsDirectHpEntry===undefined&&normal.arkdpsDirectHpLeave===undefined;globalThis.__arkdpsIsPreparedHpObservation=()=>true;prepareNativeCaptureHook(ptr(0x9200),other);globalThis.nonNormalFallback=other.arkdpsDirectHpEntry===undefined&&other.arkdpsDirectHpLeave===undefined;",f.context);
 assert(f.context.oldApiFallback);assert(f.context.nonNormalFallback);h.restore();
}
console.log('PASS_DIRECT_HP_SOURCE actual source owner/golden/unchanged writer/forgery/oldlease + strict owned recipes');
