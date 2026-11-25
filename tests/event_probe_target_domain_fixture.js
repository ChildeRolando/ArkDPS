// Enemy-ledger target-domain matrix using actual shared callbacks and 2207 values.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const original=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/event_probe_business/2207-friendly-scope-first-issue.jsonl'),'utf8'));
const raw=original.data.events.drain.events.find(e=>e.sequence===54);
assert.equal(raw.kind,'damage');assert.equal(raw.damage_type,3);assert.equal(raw.target,raw.source);
const bits=x=>BigInt(Math.round(x*4294967296)),specs=[
 ['self_uid0','self',0,false],['self_uid10','self',10,false],
 ['ally_uid0','ally',0,false],['ally_uid12','ally',12,false],
 ['token_uid0','token',0,false],['token_uid12','token',12,false],
 ['token_unit_uid0','token',0,false,'unit_class'],['token_bobject_uid0','token',0,false,'bobject_class'],
 ['token_enemy_class','token',12,true,'enemy_class'],['self_end_clock_unknown','self',10,true,'bad_clock_leave'],
 ['self_output_uid0','self',0,false,'output'],['ally_output','ally',12,false,'output'],
 ['self_output_only','self',0,false,'output_only'],
 ['ally_healing','ally',0,false,'healing'],
 ['neutral_trap','trap',12,false,'unit'],['enemy','enemy',12,false,'unit'],
 ['unknown_id','unknown',12,true],['empty_id','empty',12,true],
 ['unreadable_target','unreadable',12,true],['unreadable_class','bad_class',12,true],
 ['char_id_enemy_class','char_enemy',12,true],['enemy_uid0','enemy',0,true,'unit'],
 ['ally_uid0_to12','ally',0,true,'uid_changed'],['enemy_uid_changed','enemy',12,true,'uid_changed'],
 ['ally_death_uid_zero','ally',12,false,'uid_clear'],
 ['token_uid12_to0','token',12,true,'uid_clear'],
 ['enemy_uid12_to0','enemy',12,true,'uid_clear'],
 ['trap_uid12_to0','trap',12,true,'uid_clear'],
 ['ally_uid12_to99','ally',12,true,'uid_changed'],
 ['ally_noncharacter_uid12_to0','ally',12,true,'uid_clear_unit_class'],
 ['ally_death_nested','ally',12,true,'uid_clear_nested'],
 ['ally_death_nested_other_target','ally',12,true,'uid_clear_nested_other_target'],
 ['ally_death_nested_enemy_source','ally',12,true,'uid_clear_nested_enemy_source'],
 ['ally_death_inside_ally_apply','ally',12,true,'uid_clear_inside_ally'],
 ['ally_death_inside_enemy_apply','ally',12,true,'uid_clear_inside_enemy'],
 ['ally_death_inside_enemy_source','ally',12,true,'uid_clear_inside_enemy_source'],
 ['ally_death_parallel_enemy_source','ally',12,false,'uid_clear_inside_enemy_source_other_thread'],
 ['ally_death_retarget','ally',12,true,'uid_clear_retarget'],
 ['ally_death_earlier_enemy','ally',12,true,'enemy_roundtrip_uid_clear'],
 ['ally_death_uid_restored','ally',12,true,'uid_clear_restore'],
 ['enemy_to_ally','enemy',12,true,'to_ally'],['ally_to_enemy','ally',12,true,'to_enemy'],
 ['ally_enemy_ally','ally',12,true,'enemy_roundtrip'],['enemy_ally_enemy','enemy',12,true,'ally_roundtrip']
];
function scenario(platform,[name,kind,uid,global,mode]){
 const f=fixture({platform,settlement:true,sourcePath:path.join(__dirname,'..',
  platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 f.installSyntheticMitigation();
 const source=f.unit(raw.source,kind==='self'?uid:10),other=f.unit('char_2012_typhon',11);
 f.write(source.add(0x538).readPointer().add(0x6c),raw.source_character_uid);
 const ids={ally:'char_122_beagle',token:'token_test_friend',trap:'trap_neutral_object',
  enemy:'enemy_domain_test',unknown:'unknown_target',empty:'',bad_class:'char_122_beagle',char_enemy:'char_122_beagle'};
 let target=kind==='self'?source:f.unit(Object.hasOwn(ids,kind)?ids[kind]:'char_122_beagle',uid);
 const enemyClass=f.vm.runInContext('enemyEntryConfig.enemy',f.context);
 if(kind==='enemy'||kind==='char_enemy')f.write(target,enemyClass);
 if(mode==='unit_class'||mode==='uid_clear_unit_class')f.write(target,f.unitClass);
 if(mode==='bobject_class')f.write(target,f.vm.runInContext('enemyEntryConfig.bObject',f.context));
 if(mode==='enemy_class')f.write(target,enemyClass);
 if(kind==='bad_class')f.write(target,f.ptr(0xdeadbeef));
 if(kind==='unreadable')target=f.ptr(0xdeadbeef);
 const initial=f.context.rpc.exports.mark();
 f.clock(raw.frame,bits(raw.game_time));
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass),
  output=f.callback('OnAfterOutputDamage',f.entity);
 const mod=f.modifier(source,target,raw.amount,0,mode==='healing'?4:raw.damage_type);
 if(kind!=='unreadable')f.write(target.add(0x40),bits(raw.hp_before));
 const ac={threadId:1},tc={threadId:1};
 let parent=null;
 if(mode?.startsWith('uid_clear_inside')){
  const enemy=mode==='uid_clear_inside_enemy';
  const parentTarget=f.unit(enemy?'enemy_parent':'char_ally_parent',31);
  if(enemy)f.write(parentTarget,enemyClass);
  f.write(parentTarget.add(0x40),bits(50));
  const untracked=mode.includes('enemy_source'),parentSource=untracked?f.unit('enemy_outer_source',32):source;
  if(untracked)f.write(parentSource,enemyClass);
  const parentModifier=f.modifier(parentSource,parentTarget,0,0,raw.damage_type);
  parent={threadId:mode.endsWith('other_thread')?2:1};
  apply.onEnter.call(parent,[parentTarget,parentModifier,f.ptr(0)]);
  assert.equal(!!parent.tracksModifier,!untracked);
 }
 if(mode!=='output_only')apply.onEnter.call(ac,[target,mod,f.ptr(0)]);
 if(mode==='unit')take.onEnter.call(tc,[target,mod,f.ptr(0)]);
 if(mode?.startsWith('enemy_roundtrip')||mode==='ally_roundtrip'){
  const temporaryEnemy=mode.startsWith('enemy_roundtrip');
  f.write(target.add(0x148),f.managedString(temporaryEnemy?'enemy_temporary':'char_122_beagle'));
  f.write(target,temporaryEnemy?enemyClass:f.character);
  const transient={threadId:1};output.onEnter.call(transient,[source,mod]);output.onLeave.call(transient);
  f.write(target.add(0x148),f.managedString(ids[kind]));f.write(target,kind==='enemy'?enemyClass:f.character);
 }
 if(mode?.startsWith('uid_clear_nested')){
  const untracked=mode==='uid_clear_nested_enemy_source';
  const sameTarget=untracked||mode==='uid_clear_nested';
  const childTarget=sameTarget?target:f.unit('enemy_nested_other',31);
  if(!sameTarget){f.write(childTarget,enemyClass);f.write(childTarget.add(0x40),bits(50));}
  const childSource=untracked?f.unit('enemy_inner_source',32):source;
  if(untracked)f.write(childSource,enemyClass);
  const child=f.modifier(childSource,childTarget,0,0,raw.damage_type),nested={threadId:1};
  apply.onEnter.call(nested,[childTarget,child,f.ptr(0)]);apply.onLeave.call(nested);
 }
 f.write(mod.add(0x48),bits(mode==='healing'?-raw.real_delta:raw.real_delta));
 if(kind!=='unreadable')f.write(target.add(0x40),bits(mode==='healing'?raw.hp_before-raw.real_delta:raw.hp_after));
 if(tc.started)take.onLeave.call(tc);
 if(mode==='uid_changed')f.write(target.add(0x18),uid===0?12:99);
 if((mode==='uid_clear'||parent)&&kind==='ally'){
  const finish=f.callback('OnCharacterFinished',f.controllerClass),death={threadId:1};
  finish.onEnter.call(death,[f.controller,target,f.ptr(11)]);
  f.write(target.add(0x18),0);
  finish.onLeave.call(death);
 }else if(mode?.includes('uid_clear'))f.write(target.add(0x18),0);
 if(mode==='uid_clear_retarget')f.write(mod.add(0x10),f.unit(ids.ally,0));
 if(mode==='uid_clear_restore'){
  const transient={threadId:1};output.onEnter.call(transient,[source,mod]);output.onLeave.call(transient);
  f.write(target.add(0x18),uid);
 }
 if(mode==='to_ally'){f.write(target.add(0x148),f.managedString('char_122_beagle'));f.write(target,f.character);}
 if(mode==='to_enemy'){f.write(target.add(0x148),f.managedString('enemy_returned'));f.write(target,enemyClass);}
 if(mode==='output'||mode==='output_only'){
  const oc={threadId:1};output.onEnter.call(oc,[source,mod]);output.onLeave.call(oc);
 }
 if(mode==='bad_clock_leave')f.write(f.vm.runInContext('clock',f.context).add(4),-1n);
 if(mode!=='output_only')apply.onLeave.call(ac);
 if(parent){
  assert.equal(f.vm.runInContext('enemyDamageUnknown',f.context),parent.threadId===ac.threadId,
    'only same-thread nesting invalidates the retirement proof');
  apply.onLeave.call(parent);
 }
 if(mode==='bad_clock_leave')f.clock(raw.frame,bits(raw.game_time));
 assert.equal(f.vm.runInContext('applyModifierDepth.size',f.context),0,
  'every started ApplyModifier releases the per-thread depth');
 const afterUnrelated=f.context.rpc.exports.mark();
 assert.equal(afterUnrelated.active,0,'failed end-clock must still release its hook barrier');
 // Same-frame real enemy hit from another known actor must remain independently complete.
 const enemy=f.unit('enemy_other',13);f.write(enemy,enemyClass);f.write(enemy.add(0x40),bits(50));
 const attack=f.alloc(),plan=f.context.syntheticMitigationPlan;f.write(attack.add(plan.damageType),1);
 const outer=f.hookAt(plan.outer),final=f.hookAt(plan.final),oc={threadId:1},fc={threadId:1};
 outer.onEnter.call(oc,[enemy,other,f.ptr(1),f.ptr(0),attack]);final.onEnter.call(fc,[attack]);
 final.onLeave.call(fc,f.ptr(bits(80)));outer.onLeave.call(oc,f.ptr(bits(40)));
 const enemyMod=f.modifier(other,enemy,40,0,1),bc={threadId:1},bt={threadId:1};
 apply.onEnter.call(bc,[enemy,enemyMod,f.ptr(0)]);take.onEnter.call(bt,[enemy,enemyMod,f.ptr(0)]);
 f.write(enemyMod.add(0x48),bits(-40));f.write(enemy.add(0x40),bits(10));
 take.onLeave.call(bt);apply.onLeave.call(bc);
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 const records=batch.events.filter(e=>e.kind==='damage_settlement');
 const otherHits=records.filter(e=>e.source==='char_2012_typhon');
 assert.equal(otherHits.length,1);assert.equal(otherHits[0].settlement_complete,true);assert.equal(otherHits[0].amount,40);
 assert.equal(batch.enemy_damage_unknown,global,name);
 assert.deepEqual(f.context.rpc.exports.readEvents(0,mark.sequence),batch);
 const ack=f.context.rpc.exports.ackEvents(mark.sequence);
 return {name,initial_mark:initial,after_unrelated:afterUnrelated,mark,batch,ack,
  inputs:{kind,uid,mode:mode||'apply',global,original_damage:raw,
    actor_a_counter:kind==='enemy'||kind==='trap'?raw.amount:0,
    actor_a_healing:mode==='healing'?raw.amount:0},
  original_enemy_hit:otherHits[0]};
}
const report={};
for(const platform of ['android','native_pc'])report[platform]=Object.fromEntries(specs.map(s=>[s[0],scenario(platform,s)]));
process.stdout.write(JSON.stringify(report));
