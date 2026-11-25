// Fixed <=1000-callback budget per revision. Actual shared runtime, no game.
const {performance}=require('node:perf_hooks');
const {world}=require('./native_pc_autochess_context_fixture.js');
const {scenario}=require('./event_probe_enemy_hp_fixture.js');
const fp=v=>BigInt(Math.round(v*4294967296));
function meter(f,run){
 let reads=0,bytes=0,depth=0;const proto=f.ptr(0).constructor.prototype,restore=[];
 const widths={readPointer:8,readU8:1,readU16:2,readU32:4,readS32:4,readFloat:4,readS64:8,readU64:8};
 for(const name of [...Object.keys(widths),'readByteArray','readCString','readUtf16String']){
  const original=proto[name];restore.push([name,original]);
  proto[name]=function(...args){const outer=depth++===0;try{
   const value=original.apply(this,args);
   if(outer){reads++;bytes+=widths[name]??(name==='readByteArray'?args[0]:name==='readCString'?value.length+1:(value.length+1)*2);}
   return value;
  }finally{depth--;}};
 }
 const start=performance.now();let callbacks;
 try{callbacks=run();}finally{for(const [name,original]of restore)proto[name]=original;}
 return {callbacks,read_calls:reads,logical_read_bytes:bytes,owned_callback_wall_ms:performance.now()-start};
}
function pattern(eid,count,ticks,frames){
 const f=world({fixtureOptions:{enemyHpState:true}});f.mark();
 const owner={f,nextUid:12},units=[];
 for(let i=0;i<count;i++)units.push(scenario('native_pc',false,eid,owner,{initialHp:100,maxHp:9331,scopeObserver:false}));
 f.clock(30,fp(1));const before=f.mark();
 const measured=meter(f,()=>{
  let callbacks=0;
  for(let frame=31;frame<31+frames;frame++){
   f.clock(frame,fp(frame/30));
   for(let i=0;i<units.length;i++)for(let tick=1;tick<=ticks;tick++){
    const delta=eid==='enemy_1061_zomshd'?1:[1,1,1,3,3,2][i%6];
    units[i].setHp(100+((frame-31)*ticks+tick)*delta);callbacks++;
   }
  }
  return callbacks;
 });
 const mark=f.mark(),batch=f.context.rpc.exports.readEvents(before.sequence,mark.sequence);
 if(batch.error||batch.enemy_state_unknown)throw Error(JSON.stringify(batch));
 return {...measured,hp_records:batch.events.length,events:JSON.parse(JSON.stringify(batch.events))};
}
console.log(JSON.stringify({classification:'owned_memory_not_live',budget_per_revision:728,
 r8_shieldguard:pattern('enemy_1061_zomshd',9,7,8),
 r13_aclts:pattern('enemy_9025_aclts',14,1,16),
 note:'Logical read bytes include observed scalar/block/string widths, not physical memory traffic. Callback time is Node owned memory, not native QuickJS lock or game FPS.'}));
