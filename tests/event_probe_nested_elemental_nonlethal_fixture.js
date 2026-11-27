// Frozen live scalar checkpoints, replayed with owned metadata and addresses.
// Omitted scene fields are fixture stand-ins; this is not a new live receipt,
// nor a replacement for the original incomplete parent wire records.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const input=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/event_probe_business/20261124-native-elemental-nonlethal-fields.json'),'utf8'));
const fixed=value=>BigInt(Math.round(value*4294967296));
const faults=['parent_callback_not_returned','missing_parent_real','parent_hp_mismatch',
 'changed_parent_real','changed_parent_hp','changed_parent_source_uid','changed_parent_source_cid',
 'changed_parent_source_data_uid','changed_target_uid','changed_target_cid','changed_target_instance',
 'changed_parent_type','parent_cancelled','cross_frame','cross_time','cross_scope','second_child_hp_discontinuous',
 'child_delta_mismatch','child_hp_mismatch','child_cancelled','child_shield_unknown',
 'child_incomplete','child_settlement_amount','child_settlement_uid','child_settlement_instance',
 'child_settlement_clock','child_certificate_delta','changed_child_source','reused_modifier'];
function run(platform,group,index,fault=null){
 const a=group.raw_child_entry.record,b=group.raw_child_return.record;
 const f=fixture({platform,settlement:true,sourcePath:path.join(__dirname,'..',
  platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 f.clock(a.frame,fixed(a.game_time));
 const source=f.unit(a.parent_source,10),target=f.unit(a.target,a.target_uid);
 f.write(source.add(0x538).readPointer().add(0x6c),a.parent_source_character_uid);
 f.write(target,f.enemyClass);f.write(target.add(0x25),1);f.write(target.add(0x26),0);
 const parent=f.modifier(source,target,a.parent_amount,0,group.parent_type);
 const child=f.modifier(f.ptr(0),target,a.child_amount,0,a.child_type);
 const actualBar=group.raw_elemental_bar.event,bar=f.modifier(source,target,actualBar.amount,0,0);
 f.write(bar.add(0x60),5);
 if(fault?.startsWith('child_settlement')||fault==='child_incomplete'){
  f.context.fixtureFault=fault;
  f.vm.runInContext(`{
   const original=damageSettlement;
   damageSettlement=function(...args){let event=original(...args);
    if(event?.damage_type===5){
     const changes={};
     if(fixtureFault==='child_incomplete')changes.settlement_complete=false;
     if(fixtureFault==='child_settlement_amount')changes.amount=event.amount+1;
     if(fixtureFault==='child_settlement_uid')changes.target_uid='999';
     if(fixtureFault==='child_settlement_instance')changes.target_instance='0xdead';
     if(fixtureFault==='child_settlement_clock')changes.frame=event.frame+1;
     event=corruptQueuedEvent(event,changes);args[0].settlementRecord=event;
    }return event;};
  }`,f.context);
 }
 if(fault==='child_certificate_delta')f.vm.runInContext(`{
  const original=record;record=function(...args){const event=original(...args);
   if(event?.kind==='damage_settlement'&&event.damage_type===5)return corruptQueuedEvent(event,{real_delta:event.real_delta+1});return event;};
 }`,f.context);
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 const outer={threadId:1},hit={threadId:1},elemental={threadId:1},inner={threadId:1};
 f.write(target.add(0x40),fixed(a.parent_target_hit_hp_before));
 apply.onEnter.call(outer,[target,parent,f.ptr(0)]);
 take.onEnter.call(hit,[target,parent,f.ptr(0)]);
 if(fault!=='parent_callback_not_returned')take.onLeave.call(hit);
 f.write(parent.add(0x48),fixed(fault==='missing_parent_real'?0:a.parent_real_delta));
 f.write(target.add(0x40),fixed(a.target_hp+(fault==='parent_hp_mismatch'?1:0)));
 apply.onEnter.call(elemental,[target,bar,f.ptr(0)]);
 const actualChild=fault==='reused_modifier'?parent:child;
 if(fault==='reused_modifier'){
  f.write(parent,f.ptr(0));f.write(parent.add(0x68),5);f.write(parent.add(0x30),fixed(a.child_amount));
 }
 apply.onEnter.call(inner,[target,actualChild,f.ptr(0)]);
 if(fault==='changed_child_source')f.write(actualChild,source);
 if(fault==='child_shield_unknown')inner.numericShieldUnknown=true;
 if(fault==='child_cancelled')f.write(actualChild.add(0x70),1);
 f.write(actualChild.add(0x48),fixed(b.child_real_delta+(fault==='child_delta_mismatch'?1:0)));
 f.write(target.add(0x40),fixed(b.target_hp+(fault==='child_hp_mismatch'?1:0)));
 apply.onLeave.call(inner);apply.onLeave.call(elemental);
 if(fault==='second_child_hp_discontinuous'){
  const extra=f.modifier(f.ptr(0),target,1,0,5),call={threadId:1};
  f.write(target.add(0x40),fixed(b.target_hp-1));
  apply.onEnter.call(call,[target,extra,f.ptr(0)]);f.write(extra.add(0x48),fixed(-1));
  f.write(target.add(0x40),fixed(b.target_hp-1));apply.onLeave.call(call);
 }
 if(fault==='changed_parent_real')f.write(parent.add(0x48),fixed(a.parent_real_delta+1));
 if(fault==='changed_parent_hp')f.write(target.add(0x40),fixed(b.target_hp-1));
 if(fault==='changed_parent_source_uid')f.write(source.add(0x18),99);
 if(fault==='changed_parent_source_cid')f.write(source.add(0x148),f.managedString('char_changed'));
 if(fault==='changed_parent_source_data_uid')f.write(source.add(0x538).readPointer().add(0x6c),999);
 if(fault==='changed_target_uid')f.write(target.add(0x18),999);
 if(fault==='changed_target_cid')f.write(target.add(0x148),f.managedString('enemy_changed'));
 if(fault==='changed_target_instance')f.write(parent.add(0x10),f.unit(a.target,a.target_uid));
 if(fault==='changed_parent_type')f.write(parent.add(0x68),3);
 if(fault==='parent_cancelled')f.write(parent.add(0x70),1);
 if(fault==='cross_frame')f.clock(a.frame+1,fixed(a.game_time));
 if(fault==='cross_time')f.clock(a.frame,fixed(a.game_time)+1n);
 if(fault==='cross_scope')f.vm.runInContext('resetDamageSettlementScope()',f.context);
 apply.onLeave.call(outer);
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 return {mark,batch,primitive_index:index,original_parent_sequence:group.original_parent_sequence,
  measured:{parent_hp_before:a.parent_target_hit_hp_before,parent_hp_after:a.target_hp,parent_real_delta:a.parent_real_delta,
   child_hp_before:a.child_hp_before,child_hp_after:b.target_hp,child_real_delta:b.child_real_delta},
  scope:'frozen-native-fields-owned-callback-replay-not-live'};
}
const output={};
for(const platform of ['android','native_pc'])output[platform]={
 valid:input.elemental_nonlethal_groups.map((group,index)=>run(platform,group,index)),
 invalid:Object.fromEntries(faults.map(fault=>[fault,run(platform,input.elemental_nonlethal_groups[0],0,fault)]))};
process.stdout.write(JSON.stringify(output));
