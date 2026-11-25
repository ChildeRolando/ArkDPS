// Owned callback memory only. This is a checked race model, not a reconstruction
// of fields absent from the rc25 desktop recording.
const path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const fixed=x=>BigInt(Math.round(x*4294967296));

function scenario(platform,fault=null,sameActor=true,terminalChild=false){
 const f=fixture({platform,settlement:true,sourcePath:path.join(__dirname,'..',
  platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 const parentSource=f.unit('char_1046_sbell2',10);
 const childSource=sameActor?parentSource:f.unit('char_2012_typhon',11);
 const target=f.unit('enemy_nested_ordinary',12);
 const parent=f.modifier(parentSource,target,200,0,2);
 const child=f.modifier(childSource,target,terminalChild?1000:50,0,2);
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 const output=f.callback('OnAfterOutputDamage',f.entity);
 f.clock(30,4294967296n);f.write(target.add(0x40),fixed(1000));
 const outer={threadId:1},outerTake={threadId:1};
 apply.onEnter.call(outer,[target,parent,f.ptr(0)]);
 take.onEnter.call(outerTake,[target,parent,f.ptr(0)]);
 if(fault!=='parent_callback_not_returned')take.onLeave.call(outerTake);
 f.write(parent.add(0x48),fixed(fault==='missing_parent_real'?0:-200));
 f.write(target.add(0x40),fixed(fault==='parent_hp_mismatch'?801:800));
 function runChild(mod=child){
  const nested={threadId:1},nestedTake={threadId:1};
  apply.onEnter.call(nested,[target,mod,f.ptr(0)]);
  if(fault==='unmatched_shield'){
   const block=f.callback('_AssignDynamicVar',f.blockDamage),blocked={threadId:1};
   block.onEnter.call(blocked,[f.ptr(0),f.ptr(0),mod,f.ptr(0)]);block.onLeave.call(blocked);
  }
  if(fault!=='missing_child_callback'){
   take.onEnter.call(nestedTake,[target,mod,f.ptr(0)]);take.onLeave.call(nestedTake);
   if(fault==='duplicate_child_callback'){
    const duplicate={threadId:1};take.onEnter.call(duplicate,[target,mod,f.ptr(0)]);take.onLeave.call(duplicate);
   }
  }
  f.write(mod.add(0x48),fixed(fault==='missing_child_real'?0:terminalChild?-800:-50));
  f.write(target.add(0x40),fixed(fault==='child_hp_mismatch'?(terminalChild?1:751):terminalChild?0:750));
  if(fault==='child_nominal_underflow')f.write(mod.add(0x30),fixed(terminalChild?700:40));
  const callback={threadId:1};output.onEnter.call(callback,[childSource,mod]);output.onLeave.call(callback);
  apply.onLeave.call(nested);
 }
 runChild();
 if(fault==='second_child'){
  const extra=f.modifier(childSource,target,10,0,2),nested={threadId:1};
  apply.onEnter.call(nested,[target,extra,f.ptr(0)]);apply.onLeave.call(nested);
 }
 if(fault==='changed_parent_real')f.write(parent.add(0x48),fixed(-199));
 if(fault==='changed_parent_hp')f.write(target.add(0x40),fixed(terminalChild?1:749));
 if(fault==='changed_source_uid')f.write(parentSource.add(0x18),99);
 if(fault==='changed_source_data_uid')f.write(parentSource.add(0x538).readPointer().add(0x6c),9999);
 if(fault==='changed_target_uid')f.write(target.add(0x18),99);
 if(fault==='zero_target_uid')f.write(target.add(0x18),0);
 if(fault==='changed_source_cid')f.write(parentSource.add(0x148),f.managedString('char_changed'));
 if(fault==='changed_target_cid')f.write(target.add(0x148),f.managedString('enemy_changed'));
 if(fault==='changed_damage_type')f.write(parent.add(0x68),3);
 if(fault==='parent_cancelled')f.write(parent.add(0x70),1);
 if(fault==='cross_frame')f.clock(31,4294967296n);
 if(fault==='cross_time')f.clock(30,4294967297n);
 const callback={threadId:1};output.onEnter.call(callback,[parentSource,parent]);output.onLeave.call(callback);
 apply.onLeave.call(outer);
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 return {mark,batch,parent_source:'char_1046_sbell2',parent_uid:1010,
  child_source:sameActor?'char_1046_sbell2':'char_2012_typhon',child_uid:sameActor?1010:1011,
  scope:terminalChild?'Synthetic parent HP 1000->800 and distinct terminal child HP 800->0; raw deltas -200/-800':
   'Synthetic parent HP 1000->800 and distinct child HP 800->750; raw deltas -200/-50'};
}
const faults=['parent_callback_not_returned','missing_parent_real','parent_hp_mismatch',
 'missing_child_real','child_hp_mismatch','second_child','changed_parent_real','changed_parent_hp',
 'changed_source_uid','changed_target_uid','changed_source_cid','changed_target_cid','changed_damage_type',
 'cross_frame','cross_time','changed_source_data_uid','unmatched_shield',
 'missing_child_callback','duplicate_child_callback','parent_cancelled','zero_target_uid','child_nominal_underflow'];
const reports={};
for(const platform of ['android','native_pc'])reports[platform]={
 valid:scenario(platform),different_actor:scenario(platform,null,false),
 terminal_child:scenario(platform,null,true,true),terminal_other_actor:scenario(platform,null,false,true),
 terminal_invalid:Object.fromEntries(faults.map(fault=>[fault,scenario(platform,fault,true,true)])),
 invalid:Object.fromEntries(faults.map(fault=>[fault,scenario(platform,fault)]))};
process.stdout.write(JSON.stringify(reports));
