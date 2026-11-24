// Synthetic native memory and actual producer callbacks. Not a live mechanism receipt.
const assert=require('node:assert/strict'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const fixed=n=>BigInt(n)*4294967296n;
function run(platform,fault=null){
 const f=fixture({platform,settlement:true,sourcePath:path.join(__dirname,'..',
  platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 f.clock(30,4294967296n);
 const source=f.unit('char_owned_parent',10),target=f.unit('enemy_owned_nested',12);
 f.write(target,f.enemyClass);f.write(target.add(0x25),1);f.write(target.add(0x26),0);
 f.write(target.add(0x40),fixed(80));
 const parent=f.modifier(source,target,100,0,2),child=f.modifier(f.ptr(0),target,120,0,5);
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 const outer={threadId:1},hit={threadId:1},inner={threadId:1};
 apply.onEnter.call(outer,[target,parent,f.ptr(0)]);
 take.onEnter.call(hit,[target,parent,f.ptr(0)]);
 apply.onEnter.call(inner,[target,child,f.ptr(0)]);
 if(fault==='child_incomplete')inner.numericShieldUnknown=true;
 f.write(child.add(0x48),fixed(fault==='child_delta_mismatch'?-79:-80));
 f.write(target.add(0x40),fixed(0));apply.onLeave.call(inner);
 take.onLeave.call(hit);
 if(fault==='parent_nonzero_delta')f.write(parent.add(0x48),fixed(-1));
 if(fault==='target_uid_changed')f.write(target.add(0x18),13);
 apply.onLeave.call(outer);
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 return {mark,batch,scope:'owned-synthetic-child-before-parent-zero'};
}
const result={};
for(const platform of ['android','native_pc']){
 result[platform]={valid:run(platform),invalid:Object.fromEntries(
  ['child_incomplete','child_delta_mismatch','parent_nonzero_delta','target_uid_changed'].map(fault=>[fault,run(platform,fault)]))};
 const records=result[platform].valid.batch.events;
 const parent=records.find(e=>e.kind==='damage'&&e.source==='char_owned_parent');
 assert.equal(parent?.settled_after_nested_kill,true);
 assert(parent.proof.nested_settlement);
}
console.log(JSON.stringify(result));
