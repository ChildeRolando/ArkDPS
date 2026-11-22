// Replay measured native HP checkpoints through production callbacks. Addresses
// and object tables belong to the VM simulator; quantities and order are frozen.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const measured=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/event_probe_business/2206-native-callback-fields.json'),'utf8'));
const fixed=value=>BigInt(Math.round(value*4294967296));

function run(platform,group,fault=null){
 const entry=group.raw_child_entry.record,returned=group.raw_child_return.record;
 const f=fixture({platform,settlement:true,
   sourcePath:path.join(__dirname,'..',platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 f.clock(entry.frame,fixed(entry.game_time));
 const source=f.unit(entry.parent_source,10),target=f.unit(entry.target,entry.target_uid);
 f.write(source.add(0x538).readPointer().add(0x6c),entry.parent_source_character_uid);
 const parent=f.modifier(source,target,entry.parent_amount,0,group.parent_type);
 const child=f.modifier(f.ptr(0),target,entry.child_amount,0,entry.child_type);
 const actualBar=group.canonical_records_same_frame.map(x=>x.event).find(x=>x.kind==='elemental_bar');
 assert(actualBar);
 const bar=f.modifier(source,target,actualBar.amount,0,0);f.write(bar.add(0x60),5);
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 const outer={threadId:1},hit={threadId:1},elemental={threadId:1},inner={threadId:1};
 f.write(target.add(0x40),fixed(entry.parent_target_hit_hp_before));
 apply.onEnter.call(outer,[target,parent,f.ptr(0)]);
 take.onEnter.call(hit,[target,parent,f.ptr(0)]);take.onLeave.call(hit);
 f.write(parent.add(0x48),fixed(fault==='missing_parent_real'?0:entry.parent_real_delta));
 f.write(target.add(0x40),fixed(entry.target_hp));
 apply.onEnter.call(elemental,[target,bar,f.ptr(0)]);
 apply.onEnter.call(inner,[target,child,f.ptr(0)]);
 f.write(child.add(0x48),fixed(returned.child_real_delta));
 f.write(target.add(0x40),fixed(returned.target_hp));
 apply.onLeave.call(inner);apply.onLeave.call(elemental);
 if(fault==='changed_parent_real')f.write(parent.add(0x48),fixed(entry.parent_real_delta+1));
 apply.onLeave.call(outer);
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 return {mark,batch,measured_before:entry.parent_target_hit_hp_before,
   measured_parent_hp:-entry.parent_real_delta,measured_child_hp:-returned.child_real_delta,
   source:entry.parent_source,source_uid:entry.parent_source_character_uid,target_uid:entry.target_uid};
}

const output={};
for(const platform of ['android','native_pc']){
 output[platform]={valid:measured.nested_kill_groups.map(g=>run(platform,g)),
  invalid:Object.fromEntries(['missing_parent_real','changed_parent_real'].map(f=>[f,run(platform,measured.nested_kill_groups[0],f)]))};
}
process.stdout.write(JSON.stringify(output));
