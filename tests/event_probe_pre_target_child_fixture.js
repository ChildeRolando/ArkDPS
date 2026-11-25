// The R15 raw damage and HP-before/after scalars are observed. The controlled
// nesting, callback lifetimes, and addresses below are owned fixture inputs.
const path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const fixed=x=>BigInt(Math.round(x*4294967296));
const provenance={
 source_head:'c176946373c4e540f4ea4679b5b1d40b45768ec5',
 capture_delta_sha256:'d704f75918b4a25bf11aad67d6eb2625aea683b12895ffe1def9e9dd62d15635',
 native_callback_tree_available:false
};
const rows=[
 {sequence:24813,line:427,frame:439,when:21.933333219960332,uid:4000,
  before:11066.46849864861,between:10342.286699681776,child:724.1817989668343,
  nominal:17380.363606853876},
 {sequence:30971,line:1349,frame:1456,when:72.78333303052932,uid:4500,
  before:14942.348168308847,between:14240.747777654324,child:701.6003906545229,
  nominal:16838.409793901257}
];
const modes=['active_child','active_untracked_child','active_cross_thread_child','active_cross_thread_untracked',
 'alias_restored','alias_after_target_entry','cross_thread_after_target_entry',
 'cross_thread_unreadable_after_target_entry',
 'after_target_entry','hp_before_failed','duplicate_target_callback','missing_target_callback'];

function run(platform,row,mode=null){
 const f=fixture({platform,settlement:true,sourcePath:path.join(__dirname,'..',
  platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 const source=f.unit('char_4082_qiubai',10),foreign=f.unit('enemy_scoped_source',11);
 const target=f.unit('enemy_1116_liprr_2',row.uid),other=f.unit('enemy_other_target',row.uid+1);
 for(const object of [foreign,target,other])f.write(object,f.enemyClass);
 f.write(other.add(0x40),fixed(100));
 const parent=f.modifier(source,target,row.nominal,0,2);
 const isForeign=mode?.includes('untracked'),otherTarget=mode==='different_target_parallel'?other:target;
 const childAmount=otherTarget===other?1:row.child;
 const child=f.modifier(isForeign?foreign:source,otherTarget,childAmount,0,2);
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 const outer={threadId:1},outerTake={threadId:1};
 const inner={threadId:mode?.includes('cross_thread')||mode==='different_target_parallel'?2:1};
 const busy=mode?.startsWith('active_')||mode==='different_target_parallel';
 f.clock(row.frame,fixed(row.when));f.write(target.add(0x40),fixed(otherTarget===other?row.between:row.before));
 apply.onEnter.call(outer,[target,parent,f.ptr(0)]);
 if(mode==='after_target_entry')take.onEnter.call(outerTake,[target,parent,f.ptr(0)]);
 function transientAlias(){
  f.write(parent,foreign);f.write(parent.add(0x10),other);
  apply.onEnter.call(inner,[other,parent,f.ptr(0)]);
  apply.onLeave.call(inner);
  f.write(parent,source);f.write(parent.add(0x10),target);
 }
 if(mode==='alias_restored'){
  transientAlias();
  f.write(target.add(0x40),fixed(row.between));
 }else{
  apply.onEnter.call(inner,[otherTarget,child,f.ptr(0)]);
  if(!isForeign){
   const receiver={threadId:inner.threadId};
   take.onEnter.call(receiver,[otherTarget,child,f.ptr(0)]);
   take.onLeave.call(receiver);
  }
  f.write(child.add(0x48),fixed(-childAmount));
  f.write(otherTarget.add(0x40),fixed(otherTarget===other?99:row.between));
  if(!busy)apply.onLeave.call(inner);
 }
 if(mode==='hp_before_failed')f.write(target.add(0x40),-1n);
 if(mode!=='after_target_entry'&&mode!=='missing_target_callback')take.onEnter.call(outerTake,[target,parent,f.ptr(0)]);
 if(mode==='alias_after_target_entry')transientAlias();
 if(mode==='cross_thread_after_target_entry'||mode==='cross_thread_unreadable_after_target_entry'){
  const external=f.modifier(foreign,target,row.between,0,2),concurrent={threadId:2};
  if(mode==='cross_thread_unreadable_after_target_entry'){
   const prototype=Object.getPrototypeOf(external),original=prototype.readPointer;
   const targetSlot=external.add(0x10).toString();
   prototype.readPointer=function(){
    if(this.toString()===targetSlot)throw new Error('owned unreadable target slot');
    return original.call(this);
   };
   try{apply.onEnter.call(concurrent,[target,external,f.ptr(0)]);}
   finally{prototype.readPointer=original;}
   if(concurrent.target!==null)throw new Error('target read did not fail');
  }else apply.onEnter.call(concurrent,[target,external,f.ptr(0)]);
  // The game-side write can land even though the observer could not read
  // this Modifier's target pointer at entry.
  f.write(target.add(0x40),0n);
  apply.onLeave.call(concurrent);
 }
 if(busy&&mode!=='different_target_parallel')apply.onLeave.call(inner);
 if(mode==='hp_before_failed')f.write(target.add(0x40),fixed(row.between));
 if(mode!=='missing_target_callback')take.onLeave.call(outerTake);
 if(mode==='duplicate_target_callback'){
  const duplicate={threadId:1};
  take.onEnter.call(duplicate,[target,parent,f.ptr(0)]);take.onLeave.call(duplicate);
 }
 f.write(parent.add(0x48),fixed(-row.between));f.write(target.add(0x40),0n);
 apply.onLeave.call(outer);
 if(mode==='different_target_parallel')apply.onLeave.call(inner);
 if(f.vm.runInContext('activeApplyScopes.size',f.context)!==0)throw new Error('raw ApplyModifier activity leaked');
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 return {mark,batch,parent_source:'char_4082_qiubai',parent_uid:1010,mode,
  expected:{amount:row.between,nominal:row.nominal,child_amount:row.child},
  provenance:{...provenance,original_event_sequence:row.sequence,original_capture_line:row.line}};
}
const report={};
for(const platform of ['android','native_pc'])report[platform]={
 valid:rows.map(row=>run(platform,row)),
 different_target_parallel:run(platform,rows[0],'different_target_parallel'),
 invalid:Object.fromEntries(modes.map(mode=>[mode,run(platform,rows[0],mode)]))
};
process.stdout.write(JSON.stringify(report));
