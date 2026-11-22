// Real native field inputs, synthetic metadata/memory, actual producer callbacks.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const inputsPath=path.join(__dirname,'fixtures/event_probe_business/owned-ammo-pipeline-inputs.json');
const inputsRaw=fs.readFileSync(inputsPath);
assert.equal(crypto.createHash('sha256').update(inputsRaw).digest('hex'),'bfc347718c1bdd0ae1f839c1d8deb902c1ba8c281e2e3603eee582a6d9573c94');
const inputs=JSON.parse(inputsRaw),FP=4294967296n;
const bits=value=>BigInt(Math.round(value*Number(FP)));
function scenario(platform){
 const f=fixture({platform,settlement:true,skillCastState:true,
  sourcePath:path.join(__dirname,'..',platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 assert.equal(f.installSyntheticMitigation().installed_hook_delta,3);
 const source=f.unit(inputs.actor.cid,inputs.actor.bobject_uid),skill=f.alloc(),data=f.alloc(),ability=f.alloc();
 f.write(source.add(0x538).readPointer().add(0x6c),inputs.actor.data_uid);
 f.write(source.add(0x3c8),skill);f.write(skill,f.castSkillClass);f.write(skill.add(0xa8),source);f.write(skill.add(0xb0),data);f.write(skill.add(0x98),0);
 f.write(data,f.skillData);f.write(data.add(0x50),f.managedString(inputs.actor.skill_key));f.write(data.add(0x18),f.managedString(inputs.actor.skill_key));f.write(data.add(0x10),f.managedString('Synthetic metadata skill'));
 f.write(ability,f.abilityClass);
 const stages=[];let counterDamage=0;
 function stage(name){
  const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
  assert.equal(mark.skill_unknown,false,name);assert.equal(batch.enemy_damage_unknown,false,name);
  stages.push({name,mark,batch,counter_damage:counterDamage});
 }
 function marker(sequence){
  const event=inputs.markers.find(x=>x.event.sequence===sequence).event;
  f.clock(event.frame,bits(event.game_time));const method=event.proof.method.slice('Character.'.length),hook=f.callback(method,f.character),call={threadId:1};
  const args=method==='OnAfterSkill'?[source,skill,f.ptr(event.skill_reason)]:[source];
  hook.onEnter.call(call,args);hook.onLeave.call(call);
 }
 function cast(frame,legacySequences){
  const witness=inputs.native_lifecycle.find(x=>x.record.methods[0]==='BasicSkill.OnCastFinish'&&x.record.phase==='leave'&&x.record.clock.frame===frame).record;
  f.clock(frame,BigInt(witness.clock.native_fixed_time));const hook=f.callback('OnCastFinish',f.basicSkill),call={threadId:1};
  hook.onEnter.call(call,[skill,ability,f.ptr(1),f.ptr(0)]);
  for(const sequence of legacySequences)marker(sequence);
  f.write(skill.add(0x98),witness.snapshot.wait_for_skill_end?1:0);hook.onLeave.call(call);
 }
 function hit(name){
  const sample=inputs.hits[name],event=sample.damage,entry=sample.hp_damage,settlement=sample.settlement;
  f.clock(event.frame,bits(event.game_time));const target=f.unit(event.target,Number(settlement.target_uid));
  f.write(target,f.enemyClass);f.write(target.add(0x25),1);f.write(target.add(0x26),0);f.write(target.add(0x40),bits(event.hp_before));
  const attack=f.alloc(),plan=f.context.syntheticMitigationPlan;f.write(attack.add(plan.damageType),event.damage_type);
  const outer=f.hookAt(plan.outer),final=f.hookAt(plan.final),outerCall={threadId:1},finalCall={threadId:1};
  outer.onEnter.call(outerCall,[target,source,f.ptr(1),f.ptr(0),attack]);
  final.onEnter.call(finalCall,[attack]);final.onLeave.call(finalCall,f.ptr(bits(event.mitigation_before)));
  // The actual Unit entry amount is known; the formula return is an owned
  // stand-in seeded with that amount. It is not a newly claimed native trace.
  outer.onLeave.call(outerCall,f.ptr(bits(entry.amount)));
  const mod=f.modifier(source,target,entry.amount,0,event.damage_type);
  const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass),applyCall={threadId:1},takeCall={threadId:1};
  apply.onEnter.call(applyCall,[target,mod,f.ptr(0)]);take.onEnter.call(takeCall,[target,mod,f.ptr(0)]);take.onLeave.call(takeCall);
  f.write(mod.add(0x30),bits(event.amount));f.write(mod.add(0x48),bits(event.real_delta));f.write(target.add(0x40),bits(event.hp_after));apply.onLeave.call(applyCall);
  counterDamage+=event.amount;
  const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
  const produced=batch.events.filter(x=>x.kind==='damage').at(-1),producedSettlement=batch.events.filter(x=>x.kind==='damage_settlement').at(-1);
  for(const key of ['amount','hp_before','hp_after','real_delta','mitigation_before','mitigation_after'])assert.equal(produced[key],event[key],name+':'+key);
  assert.equal(producedSettlement.settlement_complete,true);
 }
 stage('baseline');marker(129);marker(131);marker(132);cast(314,[133,134]);hit('first_live');stage('first_live');
 marker(832);f.write(skill.add(0x98),0);hit('outside_after_true_finish');stage('outside_after_true_finish');
 marker(1025);marker(1027);marker(1028);cast(3773,[1029,1030]);stage('second_live_no_hit');
 assert.equal(inputs.second_activation_has_no_outgoing_hp_formula_hit,true);
 f.clock(inputs.terminal_boundary.frame,bits(inputs.terminal_boundary.game_time));
 const destroy=f.callback('OnDestroy',f.controllerClass),destroyCall={threadId:1};
 destroy.onEnter.call(destroyCall,[f.controller]);destroy.onLeave.call(destroyCall);
 assert.equal(f.context.rpc.exports.mark().terminal_boundary.frame,inputs.terminal_boundary.frame);
 stage('terminal_without_skill_end');
 assert(f.apiCalls.every(name=>!['il2cpp_runtime_class_init','il2cpp_runtime_invoke','il2cpp_class_init'].includes(name)));
 return {actor:inputs.actor,stages,inputs:inputs.hits,
  provenance:{frozen_input_sha256:crypto.createHash('sha256').update(inputsRaw).digest('hex'),source_prefix_sha256:inputs.provenance.source_prefix_sha256,
   scope:'Synthetic metadata/addresses/modifier memory and cumulative counter; actual source hooks. Numeric hit fields and skill timeline are frozen native inputs. No second-activation hit is fabricated.'}};
}
console.log(JSON.stringify({android:scenario('android'),native_pc:scenario('native_pc')}));
