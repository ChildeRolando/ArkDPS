// Actual shared producer callbacks; owned metadata/memory only.
const assert=require('node:assert/strict'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const FP=4294967296n,bits=x=>BigInt(Math.round(x*Number(FP)));
const localFaults=['bad_hp','nested_same_target','duplicate_target','duplicate_output','missing_unit','positive_zero_without_absorption'];
const globalFaults=['reused_modifier','source_uid','unlocalizable_owner','source_character_uid','source_id','source_pointer','target_uid','target_id','target_pointer','damage_type','cross_frame','cross_time','unreadable_source'];
function scenario(platform,fault,repetitions=1){
 const f=fixture({platform,settlement:true,sourcePath:path.join(__dirname,'..',
  platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 f.installSyntheticMitigation();const initialMark=f.context.rpc.exports.mark();
 const a=f.unit('char_147_shining',10),b=f.unit('char_2012_typhon',11);
 const targetA=f.unit('enemy_a',12),targetB=f.unit('enemy_b',13);
 f.clock(30,FP);
 function formula(source,target){
  const attack=f.alloc(),plan=f.context.syntheticMitigationPlan;f.write(attack.add(plan.damageType),1);
  const outer=f.hookAt(plan.outer),final=f.hookAt(plan.final),oc={threadId:1},fc={threadId:1};
  outer.onEnter.call(oc,[target,source,f.ptr(1),f.ptr(0),attack]);final.onEnter.call(fc,[attack]);
  final.onLeave.call(fc,f.ptr(bits(80)));outer.onLeave.call(oc,f.ptr(bits(40)));
 }
 function hit(source,target,mode){
  formula(source,target);const mod=f.modifier(source,target,40,0,1);
  const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
  const output=f.callback('OnAfterOutputDamage',f.entity),ac={threadId:1},tc={threadId:1};
  f.write(target.add(0x40),bits(50));
  if(mode==='unreadable_source')f.write(source.add(0x148),f.ptr(0));
  if(mode==='unlocalizable_owner')f.write(source.add(0x538).readPointer().add(0x6c),0);
  apply.onEnter.call(ac,[target,mod,f.ptr(0)]);
  if(mode!=='missing_unit'&&mode!=='positive_zero_without_absorption'&&mode!=='unreadable_source')
   take.onEnter.call(tc,[target,mod,f.ptr(0)]);
  f.write(mod.add(0x48),bits(-40));f.write(target.add(0x40),bits(10));
  if(mode==='positive_zero_without_absorption'){
   f.write(mod.add(0x30),0n);f.write(mod.add(0x48),0n);f.write(target.add(0x40),bits(50));
  }
  if(tc.started)take.onLeave.call(tc);
  if(mode==='duplicate_target'){
   const duplicate={threadId:1};take.onEnter.call(duplicate,[target,mod,f.ptr(0)]);take.onLeave.call(duplicate);
  }
  if(mode==='duplicate_output'){
   for(const real of [-40,-39]){
    f.write(mod.add(0x48),bits(real));const oc={threadId:1};
    output.onEnter.call(oc,[source,mod]);output.onLeave.call(oc);
   }
   f.write(mod.add(0x48),bits(-40));
  }
  if(mode==='reused_modifier'){
   const child={threadId:1};apply.onEnter.call(child,[target,mod,f.ptr(0)]);apply.onLeave.call(child);
  }
  if(mode==='nested_same_target'){
   const foreign=f.unit('enemy_external',88),cm=f.modifier(foreign,target,1,0,1),child={threadId:1};
   apply.onEnter.call(child,[target,cm,f.ptr(0)]);apply.onLeave.call(child);
  }
  if(mode==='bad_hp'||mode==='unlocalizable_owner')f.write(target.add(0x40),bits(11));
  if(mode==='source_uid')f.write(source.add(0x18),99);
  if(mode==='source_character_uid')f.write(source.add(0x538).readPointer().add(0x6c),9999);
  if(mode==='source_id')f.write(source.add(0x148),f.managedString('char_changed'));
  if(mode==='source_pointer')f.write(mod,b);
  if(mode==='target_uid')f.write(target.add(0x18),99);
  if(mode==='target_id')f.write(target.add(0x148),f.managedString('enemy_changed'));
  if(mode==='target_pointer')f.write(mod.add(0x10),targetB);
  if(mode==='damage_type')f.write(mod.add(0x68),2);
  if(mode==='cross_frame')f.clock(31,FP);
  if(mode==='cross_time')f.clock(30,FP+1n);
  apply.onLeave.call(ac);
 }
 for(let i=0;i<repetitions;i++)hit(a,targetA,fault);
 hit(b,targetB,null);
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 assert.deepEqual(f.context.rpc.exports.readEvents(0,mark.sequence),batch,'retained queue must be unchanged');
 const aHits=batch.events.filter(e=>e.kind==='damage_settlement'&&e.source==='char_147_shining');
 const bHits=batch.events.filter(e=>e.kind==='damage_settlement'&&e.source==='char_2012_typhon');
 assert.equal(bHits.length,1);assert.equal(bHits[0].settlement_complete,true);assert.equal(bHits[0].amount,40);
 if(localFaults.includes(fault)){
  assert.equal(batch.enemy_damage_unknown,false,fault);
  assert.equal(aHits.length,repetitions,fault+' explicit per-scope records');
  for(const hit of aHits){
   assert.equal(hit.settlement_complete,false);assert.equal(hit.amount,0);assert.equal(hit.shield_absorbed,null);
   assert.equal(typeof hit.proof.settlement_unknown_reason,'string');
   for(const key of ['nominal_amount','real_delta','hp_before','hp_after'])assert(!(key in hit),key);
   assert.equal(hit.source_character_uid,1010);
  }
 }else if(globalFaults.includes(fault))assert.equal(batch.enemy_damage_unknown,true,fault);
 else{
  assert.equal(aHits.length,repetitions);assert(aHits.every(h=>h.settlement_complete&&h.amount===40));
  assert.equal(batch.enemy_damage_unknown,false);
 }
 const ack=f.context.rpc.exports.ackEvents(mark.sequence),again=f.context.rpc.exports.ackEvents(mark.sequence);
 assert.equal(ack.acknowledged,mark.sequence);assert.deepEqual(again,ack);
 assert.equal(f.context.rpc.exports.readEvents(mark.sequence,mark.sequence).events.length,0);
 return {initial_mark:initialMark,mark,batch,ack,actor_a_hits:aHits,actor_b_hits:bHits,
  scope:'Owned producer callback fault injection, not live battle evidence'};
}
function overflow(platform){
 const f=fixture({platform,settlement:true,sourcePath:path.join(__dirname,'..',
  platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 f.vm.runInContext('queue.length=EVENT_BUFFER_LIMIT;record(null,"enemy_hp",0,time(),{},{});',f.context);
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 assert.match(mark.error,/event buffer overflow/);assert.match(batch.error,/event buffer overflow/);
 return {mark,batch};
}
const report={};
for(const platform of ['android','native_pc']){
 report[platform]={local:{},global:{}};
 for(const fault of localFaults)report[platform].local[fault]=scenario(platform,fault);
 for(const fault of globalFaults)report[platform].global[fault]=scenario(platform,fault);
 report[platform].same_amount_twice=scenario(platform,null,2);
 report[platform].same_local_gap_twice=scenario(platform,'bad_hp',2);
 report[platform].overflow=overflow(platform);
}
process.stdout.write(JSON.stringify(report));
