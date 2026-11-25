const path=require('node:path'),assert=require('node:assert/strict');
const {fixture}=require('./native_pc_frida_probe_fixture');
const output={};
for(const platform of ['native_pc','android']){
 const cases={};
 for(const scenario of ['nested_after_first','native_first','no_effect_start','wait_false','real_end','foreign_after','missing_instance']){
  const f=fixture({platform,sourcePath:path.resolve(platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js'),skillCastState:true,characterLifecycle:true,settlement:true});
  const actor=f.unit('char_237_gravel',10),other=f.unit('char_237_gravel',20),skill=f.alloc(),data=f.alloc(),ability=f.alloc();
  f.write(actor.add(0x3c8),skill);f.write(skill,f.castSkillClass);f.write(skill.add(0xa8),actor);f.write(skill.add(0xb0),data);f.write(data,f.skillData);f.write(ability,f.abilityClass);
  for(const offset of [0x50,0x18,0x10])f.write(data.add(offset),f.managedString('skill-fixture'));
  function invoke(name,args){const h=f.callback(name,f.character),call={threadId:1};h.onEnter.call(call,args);h.onLeave.call(call);}
  f.clock(30,4294967296n);
  invoke(scenario==='no_effect_start'?'OnSkillStart':'SwitchToSkillState',[actor]);
  f.clock(60,8589934592n);
  if(scenario==='missing_instance')f.write(actor.add(0x18),0);
  const native=f.callback('OnCastFinish',f.basicSkill),call={threadId:1};
  native.onEnter.call(call,[skill,ability,f.ptr(0),f.ptr(0)]);
  if(scenario==='real_end')invoke('OnSkillFinish',[actor]);
  const after=()=>invoke('OnAfterSkill',[scenario==='foreign_after'?other:actor,skill,f.ptr(0)]);
  if(scenario!=='native_first')after();
  f.write(skill.add(0x98),scenario==='wait_false'?0:1);native.onLeave.call(call);
  if(scenario==='native_first')after();
  const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
  const legacy=batch.events.find(e=>e.proof.method==='Character.OnAfterSkill'),cast=batch.events.find(e=>e.proof.method==='BasicSkill.OnCastFinish');
  if(!['native_first','foreign_after','missing_instance'].includes(scenario)){
   assert.equal(legacy.skill_cast_invocation,cast.skill_cast_invocation);assert.equal(cast.source_instance_uid,10);
   assert(legacy.sequence<cast.sequence,'original nested callback order is preserved');
  }else assert.equal(legacy.skill_cast_invocation,undefined);
  if(scenario==='missing_instance'){assert.equal(cast,undefined);assert.equal(mark.skill_unknown,true);}
  cases[scenario]={mark,batch};
 }
 output[platform]=cases;
}
console.log(JSON.stringify(output));
