// Actual shared producer callbacks against typed synthetic IL2CPP metadata.
const path=require('node:path'),assert=require('node:assert/strict');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const sourceRoot=process.argv[2]||path.resolve(__dirname,'..');
const RAW_UID=2147483989;
function setup(platform,metadata=true,sourceId='char_1041_angel2'){
 const f=fixture({platform,sourcePath:path.join(sourceRoot,platform==='android'?'event_probe_android2791.js':'event_probe_native_pc.js'),skillCastState:metadata,settlement:true});
 const source=f.unit(sourceId,697),skill=f.alloc(),data=f.alloc(),ability=f.alloc();
 f.write(source.add(0x538).readPointer().add(0x6c),RAW_UID);
 f.write(source.add(0x3c8),skill);f.write(skill,f.castSkillClass);f.write(skill.add(0xa8),source);f.write(skill.add(0xb0),data);f.write(skill.add(0x98),0);
 f.write(data,f.skillData);f.write(data.add(0x50),f.managedString('skchr_angel2_3'));f.write(data.add(0x18),f.managedString('skchr_angel2_3'));f.write(data.add(0x10),f.managedString('Synthetic skill'));
 f.write(ability,f.abilityClass);
 f.clock(313,44810782720n);
 const initial_mark=f.context.rpc.exports.mark(),initial_batch=f.context.rpc.exports.readEvents(0,initial_mark.sequence);
 function legacy(name,frame=313,args=[source]){
  f.clock(frame,BigInt(frame)*143165440n);const call={threadId:1},hook=f.callback(name,f.character);
  hook.onEnter.call(call,args);hook.onLeave.call(call);
 }
 function finish(wait,reason=1,fault=null,frame=314,emitLegacy=true){
  f.clock(frame,BigInt(frame)*143165440n);
  const hook=f.callback('OnCastFinish',f.basicSkill),call={threadId:1};
  hook.onEnter.call(call,[fault==='receiver'?source:skill,ability,f.ptr(reason),f.ptr(0)]);
  if(emitLegacy)legacy('OnAfterSkill',frame,[source,skill,f.ptr(reason)]);
  f.write(skill.add(0x98),wait);
  if(fault==='owner_uid')f.write(source.add(0x18),698);
  if(fault==='data_uid')f.write(source.add(0x538).readPointer().add(0x6c),RAW_UID+1);
  if(fault==='zero_uid')f.write(source.add(0x538).readPointer().add(0x6c),0);
  if(fault==='owner_cid')f.write(source.add(0x148),f.managedString('char_changed'));
  if(fault==='skill_key')f.write(data.add(0x50),f.managedString('changed_key'));
  if(fault==='current_skill')f.write(source.add(0x3c8),f.alloc());
  if(fault==='skill_owner')f.write(skill.add(0xa8),f.unit('char_other',99));
  if(fault==='skill_class')f.write(skill,f.unitClass);
  if(fault==='owner_class')f.write(source,f.unitClass);
  if(fault==='skill_data_class')f.write(data,f.basicSkill);
  if(fault==='character_data_class')f.write(source.add(0x538).readPointer(),f.skillData);
  if(fault==='character_data_pointer'){
   const other=f.alloc();f.write(other,f.characterData);f.write(other.add(0x6c),RAW_UID);f.write(source.add(0x538),other);
  }
  if(fault==='clock')f.clock(frame+1,BigInt(frame+1)*143165440n);
  if(fault==='clock_fraction')f.clock(frame,BigInt(frame)*143165440n+1n);
  hook.onLeave.call(call);
 }
 function report(){
  const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
  assert(f.apiCalls.every(name=>!['il2cpp_runtime_class_init','il2cpp_runtime_invoke','il2cpp_class_init'].includes(name)));
  return {initial_mark,initial_batch,mark,batch,ready:f.messages.find(x=>x.ready===true),synthetic_callback_execution:true};
 }
 return {f,source,skill,legacy,finish,report};
}
const result={provenance:{scope:'Synthetic execution of actual producer callbacks; no live lifecycle acceptance',
 real_field_reference_sha256:'da6122420941983fa56037c5e4039a81ea8b8ca2e14c3d53e7f0bab2ec52912f',
 real_first_start_frame:313,real_first_cast_frame:314,real_first_true_finish_frame:2543,real_second_start_frame:3772,real_second_cast_frame:3773,
 native_class:'CastSkillWithLimitTimes',extra_mode:true,finish_skill_with_progress:true,counter_binding:'unknown; no raw counter used'}};
for(const platform of ['android','native_pc']){
 const scenarios={};
 for(const [name,wait,reason,actor] of [['wait_true',1,1],['wait_false',0,1],['other_reason_wait_true',1,17],['generic_actor_wait_true',1,1,'char_9999_fixture']]){
  const c=setup(platform,true,actor);c.legacy('OnSkillStart');c.finish(wait,reason);const r=c.report();
  const native=r.batch.events.filter(e=>e.proof?.method==='BasicSkill.OnCastFinish');
  assert.equal(r.ready.skill_cast_state_ready,true);assert.equal(r.ready.skill_cast_state_error,null);
  assert.equal(native.length,1);assert.equal(native[0].kind,'skill_end');assert.equal(native[0].skill_wait_for_end,!!wait);assert.equal(native[0].skill_reason,reason);assert.equal(native[0].source_character_uid,RAW_UID);
  assert(r.batch.events.some(e=>e.proof?.method==='Character.OnAfterSkill'));scenarios[name]=r;
 }
 {
  const c=setup(platform);c.legacy('OnSkillStart');c.finish(1);c.legacy('OnSkillFinish',2543);c.legacy('OnSkillStart',3772);c.finish(1,1,null,3773);
  const r=c.report();assert.equal(r.batch.events.filter(e=>e.proof?.method==='Character.OnSkillFinish').length,1);
  assert.deepEqual(Array.from(r.batch.events.filter(e=>e.proof?.method==='BasicSkill.OnCastFinish'),e=>e.skill_wait_for_end),[true,true]);scenarios.two_activations=r;
 }
 for(const fault of ['owner_uid','data_uid','zero_uid','owner_cid','skill_key','current_skill','skill_owner','skill_class','owner_class','skill_data_class','character_data_class','character_data_pointer','clock','clock_fraction','invalid_bool']){
  const c=setup(platform);c.legacy('OnSkillStart');c.finish(fault==='invalid_bool'?2:1,1,fault);const r=c.report();
  assert.equal(r.mark.skill_unknown,true,fault);assert.equal(r.batch.events.filter(e=>e.proof?.method==='BasicSkill.OnCastFinish').length,0,fault);
  assert(r.batch.events.some(e=>e.proof?.method==='Character.OnAfterSkill'));scenarios[fault]=r;
 }
 for(const [name,metadata] of Object.entries({wait_field_int:{waitInt:true},wait_field_static:{waitStatic:true},static_method:{staticMethod:true},ability_byref:{abilityByRef:true},reason_byref:{reasonByRef:true},reason_not_enum:{reasonNotEnum:true},reason_size8:{reasonSize:8},reason_wrong_owner:{reasonWrongOwner:true},last_int:{lastInt:true},last_byref:{lastByRef:true}})){
  const c=setup(platform,metadata);c.legacy('OnSkillStart');c.legacy('OnAfterSkill',314,[c.source,c.skill,c.f.ptr(1)]);const r=c.report();
  assert.equal(r.ready.skill_cast_state_ready,false,name);assert.equal(typeof r.ready.skill_cast_state_error,'string',name);
  assert.equal(r.batch.events.filter(e=>'skill_wait_for_end' in e).length,0,name);assert(r.batch.events.some(e=>e.proof?.method==='Character.OnAfterSkill'));scenarios[name]=r;
 }
 result[platform]=scenarios;
 for(const [name,cid,ownerKind] of [['foreign_enemy','enemy_1001_fixture','enemyClass'],['foreign_token','token_1001_fixture','unitClass'],['null_owner',null,null],['non_entity_owner',null,'skillData'],['unsupported_receiver',null,null],['foreign_clock','enemy_1001_fixture','enemyClass']]){
  const c=setup(platform);c.legacy('OnSkillStart');
  if(cid){const foreign=c.f.unit(cid,999);c.f.write(foreign,c.f[ownerKind]);c.f.write(foreign.add(0x3c8),c.f.ptr(1));c.f.write(foreign.add(0x538),c.f.ptr(1));c.f.write(c.skill.add(0xa8),foreign);}
  else if(name==='null_owner')c.f.write(c.skill.add(0xa8),c.f.ptr(0));
  else if(name==='non_entity_owner'){const foreign=c.f.alloc();c.f.write(foreign,c.f.skillData);c.f.write(c.skill.add(0xa8),foreign);}
  c.finish(1,1,name==='unsupported_receiver'?'receiver':name==='foreign_clock'?'clock':null,314,false);const r=c.report();
  assert.equal(r.mark.skill_unknown,false,name);assert.equal(r.batch.events.filter(e=>e.proof?.method==='BasicSkill.OnCastFinish').length,0,name);
  assert.deepEqual(Array.from(r.batch.events,e=>e.kind),['skill_start']);
  scenarios[name]=r;
 }
}
console.log(JSON.stringify(result));
