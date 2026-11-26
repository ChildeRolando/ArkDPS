"use strict";
// Real installed HP callbacks use the ordinary provider and sole apply owner.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
function run(mode){
 const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),enemyHpState:true,settlement:true});
 f.context.rpc.exports.mark();f.clock(30,4294967296n);
 const actor=f.unit(mode==='trap'?'trap_owned':mode==='nonenemy'?'char_owned':'enemy_test',12),attrs=f.alloc(),cache=f.alloc();
 f.write(actor,mode==='trap'||mode==='nonenemy'?f.unitClass:f.enemyClass);
 f.write(actor.add(0x25),mode==='unborn'?0:1);f.write(actor.add(0x26),0);f.write(actor.add(0x40),100n*4294967296n);
 f.write(actor.add(0xb0),attrs);f.write(attrs.add(0x68),cache);f.write(cache.add(0x18),mode==='empty-maxhp'?0:1);
 f.write(cache.add(0x20),0n);f.write(cache.add(0x28),1000n*4294967296n);f.write(cache.add(0x30),1);
 if(mode==='null-attributes')f.write(actor.add(0xb0),f.ptr(0));
 const trace=[];f.context.hpIoTrace=(name)=>trace.push(name);
 vm.runInContext(`const baseApplyEntry=applyHpEntry,baseApplyLeave=applyHpLeave;
 class ObservedHpIo{#base=pointerHpIo;read(name,args){hpIoTrace(name);return this.#base[name](...args);}}
 for(const name of Object.keys(pointerHpIo))ObservedHpIo.prototype[name]=function(...args){return this.read(name,args);};
 const observedIo=new ObservedHpIo();
 applyHpEntry=(receiver,args,io,label,attribute)=>baseApplyEntry(receiver,args,observedIo,label,attribute);
 applyHpLeave=(receiver,io,label)=>baseApplyLeave(receiver,observedIo,label);`,f.context);
 if(mode==='pending-rebirth')vm.runInContext('pendingEnemyRebirth.set(12,{})',f.context);
 const hook=f.callback(mode==='dirty-nonhp'?'OnAttributeDirty':'SetHpInternal',f.entity),call={threadId:1};
 hook.onEnter.call(call,[actor,f.ptr(1)]);
 if(mode!=='unchanged')f.write(actor.add(0x40),101n*4294967296n);
 hook.onLeave.call(call);
 return {trace,queue:JSON.parse(vm.runInContext('JSON.stringify(queue)',f.context)),active:vm.runInContext('active',f.context)};
}
assert.deepEqual(run('normal').trace,['identity','born','clock','id','hp','clock','closing','attributes','attributeCount','attributeWords']);
assert.deepEqual(run('unborn').trace,['identity','born']);
assert.deepEqual(run('nonenemy').trace,['identity','id']);
assert.deepEqual(run('trap').trace,['identity','id','trapClass','clock','id','hp','clock','closing']);
assert.deepEqual(run('dirty-nonhp').trace,[]);
assert.deepEqual(run('pending-rebirth').trace,['identity','born','clock']);
assert.deepEqual(run('unchanged').trace,['identity','born','clock','id','hp','clock','closing']);
assert.deepEqual(run('empty-maxhp').trace,['identity','born','clock','id','hp','clock','closing','attributes','attributeCount']);
assert.deepEqual(run('null-attributes').trace,['identity','born','clock','id','hp','clock','closing','attributes']);
for(const mode of ['normal','unborn','nonenemy','trap','dirty-nonhp','pending-rebirth','unchanged','empty-maxhp','null-attributes'])assert.equal(run(mode).active,0,mode);
{
 const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),enemyHpState:true});
 f.context.observationMeta={clock:vm.runInContext('clock',f.context),string_class:'0x1000',classes:{Enemy:f.enemyClass.toString()},skill_offsets:{}};
 const plan=vm.runInContext('nativeCapturePlan("hp.Entity.SetHpInternal",ptr(1),observationMeta)',f.context);
 for(const field of plan.fields){assert(field.observation,field.name);assert.equal(field.observation.name,field.name);for(const value of field.observation.values){assert(['u8','u32','u64','i64','utf16'].includes(value.kind));assert(Number.isInteger(value.offset));}}
 f.context.rawObservationPlan=plan;
 const sealed=vm.runInContext('sealedCaptureContract(rawObservationPlan)',f.context);
 const original=plan.fields.find(field=>field.name==='hp.identity'),copied=sealed.fields.find(field=>field.name==='hp.identity');
 assert.deepEqual(JSON.parse(JSON.stringify(copied.observation.values)),[{kind:'u64',offset:0},{kind:'u32',offset:24}]);
 original.observation.values[0].offset=99;original.pointer_offsets.push(999);
 assert.equal(copied.observation.values[0].offset,0);assert.equal(copied.pointer_offsets.length,0);
 assert(Object.isFrozen(copied.observation.values));assert(Object.isFrozen(copied.observation.values[0]));assert(!Object.isFrozen(original.observation.values));
}
console.log('PASS_HP_OBSERVATION_OWNER count1 original branch-demand and active closure');
