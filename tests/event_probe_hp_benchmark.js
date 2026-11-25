// A fixed short callback budget against owned memory, with actual scope guards.
const {performance}=require('node:perf_hooks');
const {world}=require('./native_pc_autochess_context_fixture.js');
const {scenario}=require('./event_probe_enemy_hp_fixture.js');
const frozen=require('./fixtures/enemy_hp_r5_frame1556.json');
const fp=v=>BigInt(Math.round(v*4294967296));
function meter(f,run){
 let reads=0,bytes=0,depth=0;const proto=f.ptr(0).constructor.prototype,restore=[];
 const widths={readPointer:8,readU8:1,readU16:2,readU32:4,readS32:4,readFloat:4,readS64:8,readU64:8};
 for(const name of [...Object.keys(widths),'readByteArray','readCString','readUtf16String']){
  const original=proto[name];restore.push([name,original]);
  proto[name]=function(...args){const outer=depth++===0;try{
   const result=original.apply(this,args);
   if(outer){reads++;bytes+=widths[name]??(name==='readByteArray'?args[0]:name==='readCString'?result.length+1:(result.length+1)*2);}
   return result;
  }finally{depth--;}};
 }
 const start=performance.now();let result;
 try{result=run();}finally{for(const [name,original]of restore)proto[name]=original;}
 return {reads,logical_read_bytes:bytes,callback_wall_ms:performance.now()-start,...result};
}
const f=world({fixtureOptions:{enemyHpState:true}});f.mark();
const owner={f,nextUid:12},units=new Map();
for(const e of frozen.events)if(!units.has(e.instance_uid))units.set(e.instance_uid,
 scenario('native_pc',false,e.eid,owner,{initialHp:100,maxHp:e.enemy_max_hp,scopeObserver:false}));
f.clock(30,fp(1));f.mark();
// Each round uses the same target ordering and signed deltas as the real R5
// fragment; replay 32 frames only, avoiding large archives and concurrent runs.
function run(start){
 let callbacks=0;
 for(let frame=start;frame<start+32;frame++){
  f.clock(frame,fp(frame/30));
  for(const e of frozen.events){const s=units.get(e.instance_uid);
   // Restore this frozen callback's opening native memory, outside its hook.
   f.write(s.enemy.add(0x40),fp(e.hp_before));
   s.setHp(e.hp_after);callbacks++;
  }
 }
 const mark=f.context.rpc.exports.mark();
 if(mark.error)throw Error(mark.error);
 return {callbacks,queued_events:mark.sequence};
}
const cold=meter(f,()=>run(31));
const warm=meter(f,()=>run(63));
console.log(JSON.stringify({classification:'owned_memory_not_live',budget:'32 frames x frozen 38 callbacks, sequential cold/warm',cold,warm,
 lock_time:null,note:'wall time is owned-memory JS callback time, not native QuickJS lock occupancy or game FPS; bytes are logical scalar/block/string sizes'}));
