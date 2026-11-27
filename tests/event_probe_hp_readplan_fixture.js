"use strict";
// Original shared pointer acquisition remains the baseline after removing the
// slower independent HP ReadPlan registry. Real native HookPoint is tested separately.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const {fixture}=require('./native_pc_frida_probe_fixture.js'),{ownedFields}=require('./native_pc_owned_field_fixture.js');
function run(mode){
 const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),enemyHpState:true,settlement:true});
 f.context.rpc.exports.mark();f.clock(30,4294967296n);
 const actor=f.unit(mode==='trap'?'trap_owned':'enemy_test',12),attrs=f.alloc(),cache=f.alloc();
 f.write(actor,mode==='nonenemy'||mode==='trap'?f.unitClass:f.enemyClass);if(mode==='nonenemy')f.write(actor.add(0x148),f.managedString('char_owned'));
 f.write(actor.add(0x25),mode==='unborn'?0:1);f.write(actor.add(0x26),0);f.write(actor.add(0x40),100n*4294967296n);f.write(actor.add(0xb0),attrs);f.write(attrs.add(0x68),cache);f.write(cache.add(0x18),mode==='empty-maxhp'?0:1);f.write(cache.add(0x20),0n);f.write(cache.add(0x28),1000n*4294967296n);f.write(cache.add(0x30),1);
 if(mode==='null-attributes')f.write(actor.add(0xb0),f.ptr(0));if(mode==='null-cache')f.write(attrs.add(0x68),f.ptr(0));
 f.write(actor.add(0x148).readPointer(),f.alloc());
 f.context.__arkdpsRegisterOwnedReadPlan=()=>{throw Error('removed HP registry invoked');};
 f.context.__arkdpsOwnedReadBatch=()=>{throw Error('removed HP batch invoked');};
 if(mode==='pending-rebirth')vm.runInContext('pendingEnemyRebirth.set(12,{})',f.context);
 const hook=f.callback(mode==='dirty-nonhp'?'OnAttributeDirty':'SetHpInternal',f.entity),call={threadId:1};
 hook.onEnter.call(call,[actor,f.ptr(1)]);if(mode==='cross-frame')f.clock(31,4294967296n);if(mode!=='unchanged')f.write(actor.add(0x40),101n*4294967296n);hook.onLeave.call(call);
 return {queue:JSON.parse(vm.runInContext('JSON.stringify(queue)',f.context)),unknown:vm.runInContext('enemyStateUnknown',f.context)};
}
for(const mode of ['normal','trap','nonenemy','unborn','dirty-nonhp','unchanged','empty-maxhp','null-attributes','null-cache','pending-rebirth','cross-frame']){
 const result=run(mode);assert(Array.isArray(result.queue),mode);
 if(['nonenemy','unborn','dirty-nonhp','pending-rebirth'].includes(mode))assert.equal(result.queue.length,0,mode);
}
function trapOverlap(){
 const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),enemyHpState:true});
 f.write(f.unitClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));f.write(f.enemyClass.add(0x58),f.entity);
 f.context.rpc.exports.mark();f.clock(30,4294967296n);
 const target=f.unit('trap_owned',12),stringClass=f.alloc();f.write(target,f.unitClass);f.write(target.add(0x148).readPointer(),stringClass);f.write(target.add(0x40),100n*4294967296n);
 const metadata={clock:vm.runInContext('clock',f.context),string_class:stringClass.toString(),immutable_class_parents:true,skill_offsets:{},classes:{Enemy:f.enemyClass.toString(),BObject:f.bObject.toString(),Unit:f.unitClass.toString(),Entity:f.entity.toString()}};
 const h=ownedFields(f,metadata),plan=vm.runInContext('nativeCapturePlan("hp.Entity.SetHpInternal",ptr(1),nativeCaptureMetadata)',f.context),body=h.captureFields(plan.fields.filter(x=>x.phase==='entry'),[target],[target],[]);
 // Read28 can only see the identity field, but the original independent
 // class Read8 must also compare this conflicting successful same-point span.
 const conflict=new ArrayBuffer(8);new DataView(conflict).setBigUint64(0,f.enemyClass.value,true);
 body.fields.push({field_id:600,role:'body',checkpoint:0,status:'ok'});body.segments.push({field_id:600,role:'body',checkpoint:0,address:target.toString(),bytes:conflict,status:'ok'});
 const record={version:1,edge:'entry',checkpoint:0,fields:body.fields,segments:body.segments,facts:{mode_epoch:0,scope_mode_epoch:0}};
 const hook=f.callback('SetHpInternal',f.entity);h.replay(record,()=>hook.onEnter.call({threadId:1},[target]));
 const summary=f.context.fixtureSummary;assert.equal(h.late(),0);h.restore();return summary;
}
const originalOverlap=trapOverlap();
assert.equal(originalOverlap.complete,false);
assert(originalOverlap.structural_gaps.some(x=>x.includes('conflicting captured point')),JSON.stringify(originalOverlap));
console.log('PASS_HP_POINTER_SOURCE_BASELINE original partial branches and independent class overlap');
