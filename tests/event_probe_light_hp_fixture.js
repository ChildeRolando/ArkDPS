"use strict";
const assert=require('node:assert/strict'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const {scenario}=require('./event_probe_enemy_hp_fixture.js');
for(const platform of ['native_pc','mumu']){
 const f=fixture({platform,enemyHpState:true,sourcePath:path.resolve(platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js'),globals:{arkdpsCollectEnemyHpChanges:false}});
 const s=scenario(platform,false,'enemy_test',{f,nextUid:11});
 const proto=Object.getPrototypeOf(s.enemy),saved=new Map();
 for(const name of ['readPointer','readU32','readS32','readU8','readS64','readByteArray']){
  saved.set(name,proto[name]);proto[name]=function(){throw Error('ordinary HP must not read '+name);};
 }
 try{
  for(const method of ['SetHpInternal','SetHpInternalDirectly']){
   assert.equal(s.setHp(50,()=>{},method),0);
   assert.equal(s.setHp(80,()=>{},method),0);
   assert.equal(s.setHp(80,()=>{},method),0);
  }
 }finally{for(const [name,fn]of saved)proto[name]=fn;}
 const after=s.read();assert.equal(after.enemy_hp_mode,'snapshots');
 assert.equal(after.enemy_state_unknown,false);assert.equal(after.events.filter(e=>e.kind==='enemy_hp').length,0);
 assert(after.events.some(e=>e.kind==='enemy_enter'),'birth remains an event');
 s.setMaxHp(1000,1200);
 const dirty=s.read().events.filter(e=>e.kind==='enemy_hp');assert.equal(dirty.length,1);
 assert.equal(dirty[0].proof.method,'Entity.OnAttributeDirty');assert.equal(dirty[0].enemy_max_hp,1200);
 f.write(s.enemy.add(0x158),1);
 const finish=f.callback('OnFinish',f.enemyClass),call={threadId:1};
 finish.onEnter.call(call,[s.enemy,f.ptr(1)]);finish.onLeave.call(call);
 assert(s.read().events.some(e=>e.kind==='enemy_finish'),'death/exit stays independently captured');
}
console.log('PASS_LIGHT_HP both adapters / no ordinary reads / MaxHP and lifecycle retained');
