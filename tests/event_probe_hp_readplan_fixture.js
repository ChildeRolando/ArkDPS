"use strict";
// Acquisition-contract simulator only. Shared callbacks/validators are real;
// native lease/provider/ReadPlan compilation requires the separate DLL tests.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const {fixture}=require('./native_pc_frida_probe_fixture.js'),{ownedFields}=require('./native_pc_owned_field_fixture.js');
function run(mode,batch){
 const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),enemyHpState:true,settlement:true});
 f.context.rpc.exports.mark();f.clock(30,4294967296n);
 const actor=f.unit(mode==='trap'?'trap_owned':'enemy_test',12),attrs=f.alloc(),cache=f.alloc();
 f.write(actor,mode==='nonenemy'||mode==='trap'?f.unitClass:f.enemyClass);if(mode==='nonenemy')f.write(actor.add(0x148),f.managedString('char_owned'));
 f.write(actor.add(0x25),mode==='unborn'?0:1);f.write(actor.add(0x26),0);f.write(actor.add(0x40),100n*4294967296n);f.write(actor.add(0xb0),attrs);f.write(attrs.add(0x68),cache);f.write(cache.add(0x18),mode==='empty-maxhp'?0:1);f.write(cache.add(0x20),0n);f.write(cache.add(0x28),1000n*4294967296n);f.write(cache.add(0x30),1);
 if(mode==='null-attributes')f.write(actor.add(0xb0),f.ptr(0));if(mode==='null-cache')f.write(attrs.add(0x68),f.ptr(0));
 f.write(actor.add(0x148).readPointer(),f.alloc());
 const stages=[],plans=[];
 if(batch){
  f.context.__arkdpsRegisterOwnedReadPlan=(_metadata,spec)=>{plans.push(spec);return spec;};f.context.__arkdpsOwnedReadBatch=()=>{throw Error('adapter bypassed simulated acquisition');};
  f.context.meta={clock:vm.runInContext('clock',f.context)};vm.runInContext('globalThis.readPlans=prepareNativeHpReadPlans(meta)',f.context);
  f.context.batchRead=(edge,arg,tiers)=>{
   stages.push([edge,tiers]);const spec=plans[edge==='entry'?0:1],slots=new Map();
   for(let i=0;i<spec.fields.length;i++){const field=spec.fields[i];if(!(field.tier&tiers))continue;
    let p=field.root==='absolute'?f.ptr(field.absolute_address):arg;for(const off of field.pointer_offsets)p=p.add(off).readPointer();p=p.add(field.offset);
    let bytes;
    if(field.kind==='utf16'){const n=p.add(16).readS32(),text=p.add(20).readUtf16String(n);bytes=new ArrayBuffer(12+n*2);const v=new DataView(bytes);v.setBigUint64(0,p.readPointer().value,true);v.setInt32(8,n,true);for(let j=0;j<n;j++)v.setUint16(12+j*2,text.charCodeAt(j),true);}
    else if(field.bytes===1){bytes=Uint8Array.of(p.readU8()).buffer;}
    else if(field.bytes===4){bytes=new ArrayBuffer(4);new DataView(bytes).setUint32(0,p.readU32(),true);}
    else bytes=p.readByteArray(field.bytes);
    slots.set(i,new DataView(bytes));
   }
   return {slot(i){assert(slots.has(i),'unrequested slot');if(mode==='failed-slot')throw Error('planned unavailable');return slots.get(i);}};
  };
  vm.runInContext('probeAdapter.captureHpRead=batchRead',f.context);
 }
 if(mode==='pending-rebirth')vm.runInContext('pendingEnemyRebirth.set(12,{})',f.context);
 const hook=f.callback(mode==='dirty-nonhp'?'OnAttributeDirty':'SetHpInternal',f.entity),call={threadId:1};
 hook.onEnter.call(call,[actor,f.ptr(1)]);if(mode==='cross-frame')f.clock(31,4294967296n);if(mode!=='unchanged')f.write(actor.add(0x40),101n*4294967296n);hook.onLeave.call(call);
 return {stages,queue:JSON.parse(vm.runInContext('JSON.stringify(queue)',f.context)),unknown:vm.runInContext('enemyStateUnknown',f.context)};
}
for(const mode of ['normal','trap','nonenemy','unborn','dirty-nonhp','unchanged','empty-maxhp','null-attributes','null-cache','pending-rebirth','cross-frame']){
 const old=run(mode,false),fresh=run(mode,true);assert.deepEqual(fresh.queue,old.queue,mode);assert.equal(fresh.unknown,old.unknown,mode);
 if(mode==='dirty-nonhp')assert.deepEqual(fresh.stages,[]);
 if(mode==='nonenemy')assert.deepEqual(fresh.stages,[['entry',1],['entry',8]]);
 if(mode==='pending-rebirth')assert.deepEqual(fresh.stages,[['entry',1],['entry',2],['entry',4]]);
 if(mode==='unborn')assert.deepEqual(fresh.stages,[['entry',1],['entry',2]]);
 if(mode==='unchanged')assert(!fresh.stages.some(([edge,tier])=>edge==='leave'&&tier!==1));
 if(mode==='empty-maxhp')assert(!fresh.stages.some(([edge,tier])=>edge==='leave'&&tier===4));
}
assert.equal(run('failed-slot',true).unknown,true);
function trapOverlap(batch){
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
 if(batch){
  f.context.strictBatch=(edge,arg,tiers)=>({slot(index){
   if(tiers===1&&index===0)return new DataView(arg.readByteArray(28));
   assert.equal(tiers,8);assert.equal(index,5);const p=arg.add(0x148).readPointer(),n=p.add(16).readS32(),text=p.add(20).readUtf16String(n),bytes=new ArrayBuffer(12+2*n),view=new DataView(bytes);view.setInt32(8,n,true);for(let i=0;i<n;i++)view.setUint16(12+i*2,text.charCodeAt(i),true);return view;
  }});vm.runInContext('probeAdapter.captureHpRead=strictBatch',f.context);
 }
 const hook=f.callback('SetHpInternal',f.entity);h.replay(record,()=>hook.onEnter.call({threadId:1},[target]));
 const summary=f.context.fixtureSummary;assert.equal(h.late(),0);h.restore();return summary;
}
const originalOverlap=trapOverlap(false),batchOverlap=trapOverlap(true);
assert.equal(originalOverlap.complete,false);assert.equal(batchOverlap.complete,false);
assert(originalOverlap.structural_gaps.some(x=>x.includes('conflicting captured point')),JSON.stringify(originalOverlap));
assert.deepEqual(batchOverlap.structural_gaps,originalOverlap.structural_gaps);

console.log('PASS_HP_READPLAN_SOURCE_TIERS fake acquisition; real canonical callback parity');
