const assert=require('node:assert/strict');
const {world}=require('./native_pc_autochess_context_fixture');
const output={};
for(const platform of ['native_pc','mumu']){
 const cases={};
 for(const fault of ['valid','flags_missing','instance_zero','duplicate','short_read','token']){
  const f=world({platform,fixtureOptions:{roundResetMethod:true,settlement:true}});
  f.players.forEach(([,p],i)=>f.write(p.add(0x18),f.text(String(i+1))));
  const specs=[[1,'<instanceUid>k__BackingField',0x18,9],[3,'<playerSide>k__BackingField',0x20,17],
   [4,'<inited>k__BackingField',0x24,2],[5,'<borned>k__BackingField',0x25,2],[6,'<isDisappeared>k__BackingField',0x26,2]];
  const b=f.klass('BObject','Torappu.Battle',9,specs),e=f.klass('Entity','Torappu.Battle',410,[],b),
   u=f.klass('Unit','Torappu.Battle',10,[],e),c=f.klass('Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,18]],u),
   a=f.alloc();f.write(a,f.klass('Unit[]','Torappu.Battle',0));f.write(a.add(0x18),2);
  for(let i=0;i<2;i++){
   if(fault==='short_read'&&i===1)continue;
   const unit=f.alloc();f.write(unit,c);f.write(unit.add(0x538),fault==='token'?f.data('token_shared',990+i,{isToken:true,hostKey:'char_shared',hostUid:101}):f.roleData[i]);
   f.write(unit.add(0x18),fault==='instance_zero'?0:fault==='duplicate'?77:77+i);
   f.write(unit.add(0x20),fault==='token'?1:i+1);f.write(unit.add(0x24),1);
   if(fault!=='flags_missing')f.write(unit.add(0x25),1);
   f.write(unit.add(0x26),0);f.write(a.add(0x20+i*8),unit);
  }
  const manager=f.controller.add(platform==='mumu'?f.androidBattleLayout.unitManager??0x2b8:0x2c0).readPointer();
  const container=manager.add(0x18).readPointer();f.write(container.add(0x10),a);f.write(container.add(0x20),2);
  const h=f.callback('ClearRunTimeData',f.controllerClass),invocation={threadId:1};
  h.onEnter.call(invocation,[f.controller]);f.clock(0,0n);h.onLeave.call(invocation);
  const mark=f.mark(),batch=f.context.rpc.exports.readEvents(mark.sequence,mark.sequence),point=batch.autochess_round_start;
  assert(point,'origin still available despite independent life failure');
  const life=point.lifecycle_baseline;
  if(fault==='valid'){
   assert.equal(life.complete,true);assert.equal(life.units.length,2);
   assert.deepEqual(Array.from(life.units,x=>[x.data_uid,x.instance_uid]),[[101,77],[102,78]]);
  }else if(fault==='token')assert.equal(life.units.length,0);
  else assert.equal(life,undefined);
  cases[fault]={mark,point};
 }
 output[platform]=cases;
}
console.log(JSON.stringify(output));
