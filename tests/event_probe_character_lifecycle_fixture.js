const path=require('node:path'),assert=require('node:assert/strict');
const {fixture}=require('./native_pc_frida_probe_fixture');
const out={};
for(const platform of ['native_pc','android']){
 const scenarios={};
 for(const [label,metadata] of [['valid',true],['nested_start',true],['wrong_enum',{wrongEnumOwner:true}],['wrong_value',{wrongEnumValue:true}],['wrong_actor',{wrongActor:true}],['alias_born',{aliasBorn:true}]]){
  const f=fixture({platform,sourcePath:path.resolve(platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js'),skillCastState:true,characterLifecycle:metadata,settlement:true});
  const ready=f.messages.at(-1);
  assert.equal(ready.character_lifecycle_ready,(label==='valid'||label==='nested_start'));
  const actor=f.unit('char_237_gravel',10),skill=f.alloc(),data=f.alloc();
  f.write(actor.add(0x3c8),skill);f.write(skill,f.castSkillClass);f.write(skill.add(0xa8),actor);f.write(skill.add(0xb0),data);
  f.write(data,f.skillData);for(const offset of [0x50,0x18,0x10])f.write(data.add(offset),f.managedString('skill-fixture'));
  function invoke(name,owner,args,beforeLeave){const call={threadId:1},h=f.callback(name,owner);h.onEnter.call(call,args);if(beforeLeave)beforeLeave();h.onLeave.call(call);}
  f.clock(30,4294967296n);
  if((label==='valid'||label==='nested_start')){
   const enemy=f.unit('enemy_fixture',99);f.write(enemy,f.enemyClass);f.write(enemy.add(0x40),42949672960n);
   f.write(enemy.add(0x25),0);f.write(enemy.add(0x26),0);f.write(enemy.add(0x360),f.alloc());
   invoke('Born',f.bObject,[enemy],()=>f.write(enemy.add(0x25),1));
   f.write(actor.add(0x25),0);f.write(actor.add(0x26),0);
   invoke('Born',f.character,[actor],()=>{
    if(label==='nested_start')invoke('SwitchToSkillState',f.character,[actor]);
    f.write(actor.add(0x25),1);
   });
  }
  invoke('SwitchToSkillState',f.character,[actor]);
  f.clock(60,8589934592n);
  invoke('OnAfterSkill',f.character,[actor,skill,f.ptr(2)]);
  invoke('OnCharacterFinished',f.controllerClass,[f.controller,actor,f.ptr(2)]);
  const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
  if((label==='valid'||label==='nested_start')){
   assert(batch.events.some(e=>e.kind==='enemy_enter'),'enemy base Born remains installed beside Character override');
   const born=batch.events.find(e=>e.kind==='character_born');assert.equal(born.source_instance_uid,10);assert.equal(born.actor_lifecycle.fixed,'4294967296');
   const finish=batch.events.find(e=>e.proof.method==='BattleController.OnCharacterFinished');assert.equal(finish.actor_lifecycle.reason_type,'Torappu.Battle.Entity.FinishReason');assert.equal(finish.actor_lifecycle.reason,2);
  }else assert(batch.events.every(e=>e.actor_lifecycle===undefined));
  scenarios[label]={ready,batch};
 }
 out[platform]=scenarios;
}
console.log(JSON.stringify(out));
