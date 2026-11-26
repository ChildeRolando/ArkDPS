'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm');
const {fixture}=require('./native_pc_frida_probe_fixture.js');

for(const platform of ['native_pc','android2791']){
 const f=fixture({platform,sourcePath:path.resolve('event_probe_'+platform+'.js'),settlement:true,enemyHpState:true}),c=f.context;
 f.write(f.bObject.add(0x58),f.ptr(0));
 f.write(f.entity.add(0x58),f.bObject);
 f.write(f.unitClass.add(0x58),f.entity);
 f.write(f.character.add(0x58),f.unitClass);
 function klass(name,parent){
  const k=f.alloc();
  f.write(k.add(0x10),f.text(name));f.write(k.add(0x18),f.text('Torappu.Battle'));
  f.write(k.add(0x58),parent);return k;
 }
 function derived(parent,count,prefix){
  for(let i=0;i<count;i++)parent=klass(prefix+i,parent);
  return parent;
 }
 c.deepCharacter=derived(f.character,13,'LongCharacter');
 assert.equal(vm.runInContext('isCharacterSourceClass(deepCharacter)',c),true);
 const actor=f.unit('char_deep',27);f.write(actor,c.deepCharacter);c.actor=actor;
 assert.equal(vm.runInContext('sourceCharacterUid(actor,"char_deep")',c),1027);
 const live=vm.runInContext('autoBattleClassLineage(deepCharacter)',c);
 assert(live.character.equals(f.character));assert(live.bObject.equals(f.bObject));
 let checks=0;c.check=()=>{checks++;};
 const presence=vm.runInContext('autoPresenceLineage(deepCharacter,check)',c);
 assert(presence.character.equals(f.character));assert(presence.bObject.equals(f.bObject));
 assert(checks>10,'presence must inspect past the former cutoff');

 c.foreign=derived(klass('ForeignRoot',f.ptr(0)),13,'LongForeign');
 assert.equal(vm.runInContext('isCharacterSourceClass(foreign)',c),false);
 assert.equal(vm.runInContext('autoBattleClassLineage(foreign).character',c),null);
 assert.throws(()=>vm.runInContext('autoPresenceLineage(foreign,check)',c),/presence lineage/);

 c.cycle=klass('Cycle',f.ptr(0));f.write(c.cycle.add(0x58),c.cycle);
 assert.throws(()=>vm.runInContext('isCharacterSourceClass(cycle)',c),/class lineage cycle/);
 assert.throws(()=>vm.runInContext('autoBattleClassLineage(cycle)',c),/class lineage cycle/);
 c.missing=klass('Incomplete',f.alloc());
 assert.throws(()=>vm.runInContext('isCharacterSourceClass(missing)',c),/Unreadable fixture address/);
 assert.equal(vm.runInContext('sourceCharacterLineage.has(cycle.toString())||sourceCharacterLineage.has(missing.toString())',c),false);
}
console.log('PASS_COMPLETE_CLASS_LINEAGE_CONSUMERS native_pc/android2791; source UID/live/presence/foreign/cycle/missing');
