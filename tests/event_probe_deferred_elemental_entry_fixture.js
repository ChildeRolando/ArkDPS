const path=require('node:path'),assert=require('node:assert/strict');
const {fixture}=require('./native_pc_frida_probe_fixture');
const fixed=n=>BigInt(Math.round(n*4294967296)),output={};
function scenario(platform,mode){
 const f=fixture({platform,settlement:true,enemyHpState:true,sourcePath:path.resolve(platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 const source=f.unit('char_elemental_fixture',10),target=f.unit('enemy_elemental_fixture',12);
 f.write(target,f.enemyClass);f.write(target.add(0x25),0);f.write(target.add(0x26),0);f.write(target.add(0x40),fixed(100));f.write(target.add(0x360),f.ptr(0));
 const apply=f.callback('ApplyModifier',f.entity);
 function bar(when){const mod=f.modifier(source,target,20,0,0),call={threadId:1};f.write(mod.add(0x60),5);f.clock(Math.round(when*30),fixed(when));apply.onEnter.call(call,[target,mod,f.ptr(0)]);apply.onLeave.call(call);}
 if(mode==='before_born')bar(.5);
 const born=f.callback('Born',f.bObject),birth={threadId:1};f.clock(30,fixed(1));
 born.onEnter.call(birth,[target]);f.write(target.add(0x25),1);
 if(mode==='inside_born')bar(1);
 born.onLeave.call(birth);
 if(!['before_born','inside_born'].includes(mode))bar(2);
 function reborn(when){
  f.clock(Math.round(when*30),fixed(when));f.write(target.add(0x40),fixed(0));
  const fake=f.callback('DoFakeDeath',f.enemyClass),a={threadId:1};fake.onEnter.call(a,[target,f.ptr(0)]);fake.onLeave.call(a);
  const restore=f.callback('DoReborn',f.enemyClass),b={threadId:1};restore.onEnter.call(b,[target,f.ptr(0)]);
  f.write(target.add(0x40),fixed(100));restore.onLeave.call(b);
 }
 if(mode==='cycle_before_entry')reborn(2.5);
 if(mode==='new_born_cycle'){
  const recycle=f.callback('OnRecycle',f.bObject);recycle.onEnter.call({threadId:1},[target]);
  f.write(target.add(0x25),0);f.clock(83,fixed(2.75));const next={threadId:1};
  born.onEnter.call(next,[target]);f.write(target.add(0x25),1);born.onLeave.call(next);
 }
 if(mode==='uid_changed')f.write(target.add(0x18),13);
 const layer=f.callback('_UpdatePlayerSideAndMapLayer',f.enemyClass),admission={threadId:1};f.clock(90,fixed(3));
 layer.onEnter.call(admission,[target]);f.write(target.add(0x360),f.alloc());layer.onLeave.call(admission);
 if(mode==='reborn'||mode==='reborn_new_bar')reborn(3.25);
 if(mode==='reborn_new_bar')bar(3.5);
 const burst=f.modifier(f.ptr(0),target,50,0,5),trigger={threadId:1};f.clock(120,fixed(4));
 apply.onEnter.call(trigger,[target,burst,f.ptr(0)]);f.write(burst.add(0x48),fixed(-50));f.write(target.add(0x40),fixed(50));apply.onLeave.call(trigger);
 const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 const entry=batch.events.find(e=>e.kind==='enemy_enter');
 if(mode==='uid_changed')assert.equal(entry,undefined);
 else if(mode==='cycle_before_entry')assert.equal(entry.proof.native_enemy_birth,undefined);
 else if(mode==='new_born_cycle')assert.equal(entry.proof.native_enemy_birth.sequence_before_born,1);
 else{
  assert(entry.proof.native_enemy_birth);assert.equal(entry.proof.native_enemy_birth.frame,30);
  assert.equal(entry.proof.native_enemy_birth.sequence_before_born,mode==='before_born'?1:0);
  assert.equal(entry.proof.native_enemy_birth.sequence_after_born,['before_born','inside_born'].includes(mode)?1:0);
 }
 if(mode==='valid')assert.deepEqual(Array.from(batch.events,e=>e.kind),['elemental_bar','enemy_enter','damage_settlement']);
 return {mark,batch};
}
for(const platform of ['native_pc','android'])output[platform]=Object.fromEntries(['valid','before_born','reborn','reborn_new_bar','cycle_before_entry','new_born_cycle','uid_changed','inside_born'].map(mode=>[mode,scenario(platform,mode)]));
console.log(JSON.stringify(output));
