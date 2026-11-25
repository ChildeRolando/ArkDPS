// RC08 R15 observed HP, nominal values, and clocks, bound to owned callbacks.
// The original raw records do not contain Modifier pointers or a call tree.
// This fixture supplies matching calls and the parent's independent native
// realDelta. It does not reclassify the archived incomplete wire records.
const {world}=require('./native_pc_autochess_context_fixture.js');
const fixed=value=>BigInt(Math.round(value*4294967296));
const qiu='char_4082_qiubai',yu='char_2026_yu',blaze='char_1040_blaze2';
const after='parent_before_children',before='children_before_parent';
const rc08={
 source_head:'c176946373c4e540f4ea4679b5b1d40b45768ec5',
 capture_delta_sha256:'d704f75918b4a25bf11aad67d6eb2625aea683b12895ffe1def9e9dd62d15635',
 capture_delta_offset:11430,raw_parent_modifier_real_delta_available:false,
};
const rows=[
 {name:'r15_qiubai_f172',recorded:23510,recordedLine:166,actor:qiu,type:1,frame:172,when:8.583333269692957,order:after,
  parent:[4533521.301147055,4530446.337170612,3074.963976442814],
  steps:[[null,4530446.337170612,4528696.337209728,1749.9999608844519],
   [blaze,4528696.337209728,4519682.801489779,9013.53571994882]]},
 {name:'r15_yu_f200_child_kill',recorded:23647,recordedLine:198,actor:yu,type:2,frame:200,when:9.983333264477551,order:after,
  parent:[23743.865455746418,21884.55657313182,1859.3088826145977],
  steps:[[null,21884.55657313182,14884.556729594013,6999.9998435378075],
   [blaze,14884.556729594013,0,18084.90533238114]]},
 {name:'r15_qiubai_f517_children_first',recorded:25424,recordedLine:498,actor:qiu,type:2,frame:517,when:25.8333332054317,order:before,
  parent:[3827311.8947653356,3825273.89277103,2038.0019943059888],
  steps:[[null,3839075.7441209694,3837325.744160085,1749.9999608844519],
   [blaze,3837325.744160085,3827311.8947653356,10013.849394749384]]},
];
const synthetic=(count,order=after,lethal=false)=>({
 name:lethal?'owned_children_first_parent_kill':'owned_'+count+'_elemental_steps',
 actor:qiu,type:2,frame:30,when:1,order,
 parent:order===after?[200,180,20]:[200-count,lethal?0:180-count,lethal?250:20],
 steps:Array.from({length:count},(_,i)=>[i%2===0?blaze:null,
  (order===after?180:200)-i,(order===after?179:199)-i,1]),
});
const faults=['parent_real_missing','parent_final_hp_changed','parent_checkpoint_pending',
 'child_real_changed','child_hp_discontinuous','child_cancelled','child_overlaps',
 'child_source_pointer','child_source_cid','child_source_uid','child_source_instance',
 'child_target_cid','child_target_class','child_shield_unknown','child_record_changed',
 'child_wrong_scope','cross_thread_hp_write','unsupported_third_child','chain_limit',
 'untracked_retarget_same_modifier','cross_thread_untracked_already_active',
 'children_first_missing_parent_real','children_first_checkpoint_nonzero','children_first_parent_hp_disagrees'];
function run(platform,row,fault=null){
 const f=world({platform:platform==='android'?'mumu':'native_pc',fixtureOptions:{settlement:true}});
 f.players.forEach(([,exposed],i)=>f.write(exposed.add(0x18),f.text(String(i+1))));
 f.write(f.roleData[0].add(0x50),f.text(blaze));
 f.write(f.roleData[2].add(0x50),f.text(row.actor));
 f.clock(row.frame,fixed(row.when));f.mark();
 const source=f.unit(row.actor,10),explicit=f.unit(blaze,11),target=f.unit('enemy_9029_acfnc_2',501);
 f.write(source.add(0x538).readPointer().add(0x6c),301);
 f.write(explicit.add(0x538).readPointer().add(0x6c),101);
 f.write(target,f.enemyClass);f.write(target.add(0x25),1);f.write(target.add(0x26),0);
 const [parentBefore,parentAfter,nominal]=row.parent,parentReal=parentAfter-parentBefore;
 const modifier=f.modifier(source,target,nominal,0,row.type);
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 const outer={threadId:1},parentHit={threadId:1};
 const childFirst=row.order===before;
 const openingHp=childFirst?row.steps[0][1]:parentBefore;
 f.write(target.add(0x40),fixed(openingHp));
 let foreign=null;
 if(row.parallel||fault==='cross_thread_untracked_already_active'){
  const foreignTarget=row.parallel?f.unit('enemy_concurrent_other',701):target;
  if(row.parallel){f.write(foreignTarget,f.enemyClass);f.write(foreignTarget.add(0x40),fixed(100));}
  const foreignSource=f.unit('enemy_concurrent_source',702);
  f.write(foreignSource,f.enemyClass);
  foreign={threadId:2};
  const other=f.modifier(foreignSource,foreignTarget,nominal,0,row.type);
  apply.onEnter.call(foreign,[foreignTarget,other,f.ptr(0)]);
  // The source is deliberately outside the existing money stack.
  if(foreign.tracksModifier)throw new Error('enemy source accidentally entered the owned money stack');
 }
 apply.onEnter.call(outer,[target,modifier,f.ptr(0)]);
 take.onEnter.call(parentHit,[target,modifier,f.ptr(0)]);
 if(!childFirst&&fault!=='parent_checkpoint_pending')take.onLeave.call(parentHit);
 if(!childFirst){
  f.write(modifier.add(0x48),fixed(fault==='parent_real_missing'?0:parentReal));
  f.write(target.add(0x40),fixed(parentAfter));
 }else if(fault==='children_first_checkpoint_nonzero')f.write(modifier.add(0x48),fixed(-1));

 const steps=row.steps.map(step=>[...step]);
 if(fault==='unsupported_third_child'){
  const hp=steps[steps.length-1][2];
  steps.push([blaze,hp,hp-1,1,2]);
 }
 const expectedChildren=[];
 for(let index=0;index<steps.length;index++){
  const [cid,hpBefore,hpAfter,amount,type=5]=steps[index],last=index===steps.length-1;
  const childSource=cid===null?f.ptr(0):cid===row.actor?source:explicit;
  const child=f.modifier(childSource,target,amount,0,type);
  const invocation={threadId:last&&fault==='cross_thread_hp_write'?2:1};
  const bar=cid===null?f.modifier(source,target,1,0,0):null,barCall={threadId:invocation.threadId};
  if(bar){
   f.write(bar.add(0x60),5);
   apply.onEnter.call(barCall,[target,bar,f.ptr(0)]);
  }
  if(last&&fault==='child_hp_discontinuous')f.write(target.add(0x40),fixed(hpBefore-1));
  apply.onEnter.call(invocation,[target,child,f.ptr(0)]);
  if(last&&fault==='untracked_retarget_same_modifier'){
   const transientTarget=f.unit('enemy_other_untracked',701),transientSource=f.unit('enemy_transient_source',702);
   f.write(transientTarget,f.enemyClass);f.write(transientSource,f.enemyClass);
   f.write(transientTarget.add(0x40),fixed(100));
   f.write(child,transientSource);f.write(child.add(0x10),transientTarget);
   const untracked={threadId:1};
   apply.onEnter.call(untracked,[transientTarget,child,f.ptr(0)]);
   apply.onLeave.call(untracked);
   f.write(child,childSource);f.write(child.add(0x10),target);
  }
  if(last&&fault==='child_overlaps'){
   const inner=f.modifier(explicit,target,1,0,5),nested={threadId:1};
   apply.onEnter.call(nested,[target,inner,f.ptr(0)]);
   f.write(inner.add(0x48),fixed(-1));f.write(target.add(0x40),fixed(hpBefore-1));
   apply.onLeave.call(nested);
  }
  if(last&&fault==='child_source_pointer'){
   const copy=f.unit(blaze,11);
   f.write(copy.add(0x538).readPointer().add(0x6c),101);
   f.write(child,copy);
  }
  if(last&&fault==='child_source_uid')f.write(explicit.add(0x538).readPointer().add(0x6c),999);
  if(last&&fault==='child_source_cid')f.write(explicit.add(0x148),f.managedString('char_changed'));
  if(last&&fault==='child_source_instance')f.write(explicit.add(0x18),99);
  if(last&&fault==='child_target_cid')f.write(target.add(0x148),f.managedString('enemy_changed'));
  if(last&&fault==='child_target_class')f.write(target,f.character);
  if(last&&fault==='child_shield_unknown')invocation.numericShieldUnknown=true;
  if(last&&fault==='child_cancelled')f.write(child.add(0x70),1);
  if(last&&fault==='child_wrong_scope')f.write(f.machine.add(0x5c),5);
  f.write(child.add(0x48),fixed(hpAfter-hpBefore+(last&&fault==='child_real_changed'?1:0)));
  f.write(target.add(0x40),fixed(hpAfter));
  apply.onLeave.call(invocation);
  if(bar)apply.onLeave.call(barCall);
  if(last&&fault==='child_record_changed'&&invocation.settlementRecord)invocation.settlementRecord.amount++;
  if(last&&fault==='child_target_cid')f.write(target.add(0x148),f.managedString('enemy_9029_acfnc_2'));
  if(last&&fault==='child_target_class')f.write(target,f.enemyClass);
  expectedChildren.push({source:cid,source_character_uid:cid===null?null:cid===row.actor?301:101,amount:hpBefore-hpAfter,
   hp_before:hpBefore,hp_after:hpAfter,nominal_amount:amount});
 }
 if(childFirst){
  take.onLeave.call(parentHit);
  f.write(modifier.add(0x48),fixed(fault==='children_first_missing_parent_real'?0:parentReal));
  f.write(target.add(0x40),fixed(parentAfter+(fault==='children_first_parent_hp_disagrees'?1:0)));
 }else if(fault==='parent_checkpoint_pending')take.onLeave.call(parentHit);
 if(fault==='parent_final_hp_changed')f.write(target.add(0x40),fixed(123));
 apply.onLeave.call(outer);
 if(foreign)apply.onLeave.call(foreign);
 if(f.vm.runInContext('activeApplyScopes.size',f.context)!==0)throw new Error('raw ApplyModifier activity leaked');
 const mark=f.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 return {name:row.name,recorded:row.recorded??null,order:row.order,mark,batch,
  provenance:row.recorded?{...rc08,original_capture_line:row.recordedLine,original_event_sequence:row.recorded}:null,
  expected:{parent:{source:row.actor,source_character_uid:301,amount:-parentReal,
    hp_before:parentBefore,hp_after:parentAfter,damage_type:row.type,nominal_amount:nominal},children:expectedChildren}};
}
const output={};
for(const platform of ['android','native_pc']){
 output[platform]={
  valid:[...rows,synthetic(1),synthetic(3),synthetic(16),synthetic(2,before,true),
   {...synthetic(1),name:'owned_child_same_source_as_parent',steps:[[qiu,180,179,1]]},
   {...synthetic(2),name:'different_target_untracked_thread_is_independent',parallel:true}].map(row=>run(platform,row)),
  invalid:Object.fromEntries(faults.map(fault=>[fault,run(platform,
   fault==='chain_limit'?synthetic(17):fault.startsWith('children_first')?rows[2]:rows[0],fault)]))
 };
}
process.stdout.write(JSON.stringify(output));
