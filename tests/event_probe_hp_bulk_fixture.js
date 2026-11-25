const assert=require('node:assert/strict');
const {scenario}=require('./event_probe_enemy_hp_fixture.js');
const fp=v=>BigInt(Math.round(v*4294967296));
const checks=[];
for(const platform of ['native_pc','mumu']){
 const s=scenario(platform),f=s.f;
 const cached=s.enemy.add(0xb0).readPointer().add(0x68).readPointer(),slot=cached.add(0x20);
 s.setHp(110);
 f.write(slot.add(8),fp(1200));s.setHp(120);
 f.write(slot.add(0x10),0);s.setHp(130);
 f.write(slot.add(0x10),1);f.write(slot,17n);f.write(slot.add(8),fp(1400)^17n);s.setHp(140);
 // A zero length must never read even the first attribute slot.
 const proto=f.ptr(0).constructor.prototype,original=proto.readByteArray;let slotReads=0;
 proto.readByteArray=function(size){if(this.equals(slot)){slotReads++;throw Error('empty-array slot must not be read');}return original.call(this,size);};
 try{f.write(cached.add(0x18),0);s.setHp(150);}finally{proto.readByteArray=original;}
 assert.equal(slotReads,0);
 const result=s.read();assert.equal(result.enemy_state_unknown,false);
 assert.deepEqual(result.events.slice(1).map(e=>[e.hp_before,e.hp_after,e.enemy_max_hp]),[
  [100,110,1000],[110,120,1200],[120,130,undefined],[130,140,1400],[140,150,undefined],
 ]);
 checks.push(platform+':live_count_key_value_ready_not_cached');
 for(const at of ['opening','closing','max_hp'])for(const failure of ['short','null','unreadable']){
  const s=scenario(platform),f=s.f,proto=f.ptr(0).constructor.prototype,original=proto.readByteArray;
  const slot=s.enemy.add(0xb0).readPointer().add(0x68).readPointer().add(0x20);
  const address=at==='opening'?s.enemy:at==='closing'?s.enemy.add(0x18):slot;
  proto.readByteArray=function(size){
   if(this.equals(address)){
    if(failure==='short')return new ArrayBuffer(size-1);
    if(failure==='null')return null;
    throw Error('owned unmapped cross-page block');
   }
   return original.call(this,size);
  };
  try{s.setHp(110);}finally{proto.readByteArray=original;}
  const batch=s.read(),hp=batch.events.filter(e=>e.kind==='enemy_hp');
  if(at==='max_hp'){
   assert.equal(batch.enemy_state_unknown,false,'optional max HP fault leaves raw HP observation');
   assert.equal(hp.length,1);assert.equal(hp[0].enemy_max_hp,undefined);
   assert.equal(hp[0].hp_before,100);assert.equal(hp[0].hp_after,110);
  }else{
   assert.equal(batch.enemy_state_unknown,true,'identity/HP block failure cannot certify a callback');
   assert.equal(hp.length,0);
  }
  checks.push(platform+':'+at+':'+failure+':fail_closed');
 }
 // Reused pointers/new instances and real clock changes still reject the old
 // interval, independently of whether the fields arrived in one memory copy.
 for(const mutate of [s=>s.f.write(s.enemy.add(0x18),99),s=>s.f.clock(31,fp(1))]){
  const s=scenario(platform);s.setHp(110,()=>mutate(s));const b=s.read();
  assert.equal(b.enemy_state_unknown,true);assert.equal(b.events.filter(e=>e.kind==='enemy_hp').length,0);
 }
 const s2=scenario(platform);s2.setHp(101);s2.setHp(102);const first=s2.read();
 const frozen=JSON.parse(JSON.stringify(first));
 s2.f.context.rpc.exports.ackEvents(first.events.at(-1).sequence);
 const born=s2.f.callback('Born',s2.f.bObject),call={threadId:1};
 s2.f.write(s2.enemy.add(0x18),44);s2.f.write(s2.enemy.add(0x25),0);
 born.onEnter.call(call,[s2.enemy]);s2.f.write(s2.enemy.add(0x25),1);born.onLeave.call(call);
 s2.f.clock(35,fp(2));s2.setHp(103);s2.setHp(104);
 const mark=s2.f.context.rpc.exports.mark(),next=s2.f.context.rpc.exports.readEvents(first.events.at(-1).sequence,mark.sequence);
 assert.deepEqual(first,frozen);
 assert.equal(next.events[0].kind,'enemy_enter');assert.equal(next.events[0].target_instance,'44');
 assert.deepEqual(Array.from(next.events.slice(1),e=>[e.hp_before,e.hp_after]),[[102,103],[103,104]],'new Born keeps its own first healing witness');
 checks.push(platform+':birth_uid_clock_ack_boundaries');
}
console.log(JSON.stringify({classification:'owned_memory_not_live',checks}));
