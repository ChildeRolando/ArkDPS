'use strict';
// Shared business kernel only. This owned fake span has no native authority;
// native issuer/topology/held-byte validation is a separate required gate.
const assert=require('node:assert/strict');
const {scenario}=require('./event_probe_enemy_hp_fixture.js');
const fp=n=>BigInt(Math.round(n*4294967296));
const clone=n=>JSON.parse(JSON.stringify(n));
function replay(platform,eid,values,compression,options={}){
 const s=scenario(platform,false,eid),f=s.f;
 const frame=options.early?2:30,fixed=options.early?fp(0.1):fp(1);
 f.clock(frame,fixed);
 if(options.noBirth)f.vm.runInContext('enemyHpBirths.delete("12")',f.context);
 if(options.noMax){const attrs=s.enemy.add(0xb0).readPointer().add(0x68).readPointer();f.write(attrs.add(0x18),0);}
 if(options.badScope){f.context.invalidScope=when=>{when.native_autochess_scope={protocol_version:1,status:'unknown',verified_same_frame:false,frame:when.frame,game_time:when.game_time,reason:'owned test'};};f.vm.runInContext('probeAdapter.stampClock=invalidScope',f.context);}
 const io=f.vm.runInContext('pointerHpIo',f.context),identity=io.identity(s.enemy),attrs=io.attributes(s.enemy);
 const words=attrs?io.attributeWords(attrs):[];
 const played=[];
 const span=Object.freeze({count:values.length-1,enemy:s.enemy,klass:identity[0],uid:identity[1],closingUid:identity[1],eid,born:1,frame,fixed,
  attributeCount:attrs?io.attributeCount(attrs):0,attributeWords:Object.freeze(words),threadId:1,
  hpValues:Object.freeze(values.slice(0,-1).flatMap((v,i)=>[fp(v),fp(values[i+1])])),
  playPair(i,enter,leave){
   played.push(i);const receiver={threadId:1};f.write(s.enemy.add(0x40),fp(values[i]));
   enter(receiver,s.enemy,io);f.write(s.enemy.add(0x40),fp(values[i+1]));return leave(receiver,io);
  }
 });
 f.context.leafSpan=span;f.context.compress=!!compression;
 let consumed=f.vm.runInContext('applyHpLeafFacts(leafSpan,compress)',f.context);
 assert(Number.isInteger(consumed)&&consumed>=0&&consumed<=span.count);
 const optimized=consumed===span.count&&played.length===2&&span.count>2;
 // A declined/partial span's untouched suffix goes through the same reducer.
 for(let i=consumed;i<span.count;i++)span.playPair(i,
  (r,enemy,raw)=>{f.context.argumentsForLeaf={r,enemy,raw};return f.vm.runInContext('applyHpEntry(argumentsForLeaf.r,null,argumentsForLeaf.raw,"Entity.SetHpInternal",false,argumentsForLeaf.enemy)',f.context);},
  (r,raw)=>{f.context.argumentsForLeaf={r,raw};return f.vm.runInContext('applyHpLeave(argumentsForLeaf.r,argumentsForLeaf.raw,"Entity.SetHpInternal")',f.context);});
 // Models the physical producer already having completed every input setter.
 f.write(s.enemy.add(0x40),fp(values.at(-1)));
 const before=f.vm.runInContext('JSON.stringify({epoch,active,damageSettlementClock,births:[...enemyHpBirths],runs:[...enemyHpRuns],window:enemyHpWindow,unknown:enemyStateUnknown})',f.context);
 s.setHp(values.at(-1)+3);
 const result=s.read();
 return {result,before,optimized,consumed,played};
}
let cases=0;
for(const platform of ['native_pc','mumu'])for(const eid of ['enemy_test','enemy_1061_zomshd','enemy_1061_zomshd_2']){
 for(const [values,options,expected]of [
  [[100,101,102,103,104,105,106],{},true],
  [[100,102,104,107,109,112,113],{},eid==='enemy_test'],
  [[100,100.25,100.5,100.75,101,101.25],{},true],
  [[100,100,100,100,100,100],{},true],
  [[100,100,100,100,100,100],{noBirth:true,noMax:true},true],
  [[100,101,101,102,103,104],{},false],
  [[100,101,102,100,101,102],{},false],
  [[100,101,102,103,104,105],{noBirth:true},false],
  [[100,101,102,103,104,105],{noMax:true},false],
  [[100,101,102,103,104,105],{early:true},false],
  [[100,101,102,103,104,105],{badScope:true},false]
 ]){
  const ordinary=replay(platform,eid,values,false,options),group=replay(platform,eid,values,true,options);
  assert.deepEqual(group.result,ordinary.result,platform+':'+eid+':'+JSON.stringify(options));
  assert.equal(group.before,ordinary.before,'all retained source state before publication');
  assert.equal(group.optimized,expected,platform+':'+eid+':'+JSON.stringify(options));
  if(!expected)assert.equal(group.played.length,values.length-1);
  cases++;
 }
}
console.log(JSON.stringify({pass:true,cases,scope:'owned fake span; actual shared business owner; no native issuer/throughput/FPS acceptance'}));
