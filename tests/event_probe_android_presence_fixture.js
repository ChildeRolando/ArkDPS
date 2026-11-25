// Exercise the existing Frida mark request against owned Android memory.
const assert=require('node:assert/strict');
const {performance}=require('node:perf_hooks');
const {world}=require('./native_pc_autochess_context_fixture.js');

const budgets={};
function sampled({phase=5,count=2,enemies=0,fault=null,platform='mumu',profile=null,wire=false}={}){
 const f=world({platform,sameCid:true});
 f.players.forEach(([,player],index)=>f.write(player.add(0x18),f.text(String(100+index))));
 f.write(f.machine.add(0x5c),phase);
 f.clock(30,4294967296n);
 f.vm.runInContext('Date.now=()=>0',f.context);
 const bObject=f.klass('BObject','Torappu.Battle',9,[[1,'<instanceUid>k__BackingField',0x18,9],
  [3,'<playerSide>k__BackingField',0x20,17],[4,'<inited>k__BackingField',0x24,2],
  [5,'<borned>k__BackingField',0x25,2],[6,'<isDisappeared>k__BackingField',0x26,2]]);
 const character=f.klass('Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,18]],bObject);
 const enemy=f.klass('Enemy','Torappu.Battle',1,[],bObject);
 const manager=f.controller.add(platform==='mumu'?0x2b8:0x2c0).readPointer();
 const container=manager.add(0x18).readPointer();
 const array=f.alloc(),arrayClass=f.klass('Unit[]','Torappu.Battle',0);
 f.write(array,arrayClass);
 f.write(array.add(0x18),fault==='slot_bound'?4097:count+enemies);
 f.write(container.add(0x10),array);
 f.write(container.add(0x20),fault==='slot_bound'?4097:count+enemies);
 const units=[];
 for(let index=0;index<count+enemies;index++){
  const unit=f.alloc();
  f.write(array.add(0x20+index*8),unit);
  f.write(unit,index<enemies?enemy:character);
  if(index<enemies)continue;
  const number=index-enemies;
  const slot=number%2===0?3:0;
  const token=fault==='token'&&number===0;
  f.write(unit.add(0x538),token?f.data('token_owned',700,{isToken:true,hostKey:'char_shared',hostUid:104}):f.roleData[slot]);
  f.write(unit.add(0x18),fault==='duplicate_instance'?901:901+number);
  f.write(unit.add(0x20),slot+1);f.write(unit.add(0x24),1);
  f.write(unit.add(0x25),fault==='inactive'&&number===0?0:1);
  f.write(unit.add(0x26),0);
  units.push(unit);
 }
 const original=f.mark();
 assert.equal(original.native_autochess_presence,undefined);
 if(fault==='deadline')f.vm.runInContext('let budgetClock=0; Date.now=()=>++budgetClock*20',f.context);
 const pointer=Object.getPrototypeOf(array),originalBlock=pointer.readByteArray,originalUid=pointer.readU32;
 const slotAddress=array.add(0x20).toString();
 let calls=0;
 pointer.readU32=function(){
  const result=originalUid.call(this);
  if(fault==='unit_changed'&&this.equals(units[0].add(0x18))&&result===901)f.write(this,991);
  return result;
 };
 pointer.readByteArray=function(size){
  const result=originalBlock.call(this,size);
  if(this.toString()===slotAddress&&++calls===1){
   if(fault==='clock')f.clock(31,4294967297n);
   if(fault==='scope')f.write(f.machine.add(0x5c),6);
   if(fault==='slot_changed')f.write(array.add(0x20),f.ptr(0));
   if(fault==='short_read')return new ArrayBuffer(0);
   if(fault==='callback_sequence')f.context.record('char_shared','damage',1,f.context.time(),{method:'owned'},undefined,104);
  }
  return result;
 };
 const counters=[
  ['readPointer',8],['readS32',4],['readU32',4],['readU16',2],['readU8',1],['readS64',8],['readU64',8],
  ['readByteArray',size=>size],['readUtf16String',size=>2*size],['readCString',(_size,result)=>Buffer.byteLength(result)+1],
 ];
 const restore=[];
 let measured=null,depth=0;
 if(profile){
  budgets[profile]=[];
  for(const [method,size] of counters){
   const original=pointer[method];
   restore.push(()=>{pointer[method]=original;});
   pointer[method]=function(...args){
    if(!measured||depth)return original.apply(this,args);
    depth++;
    try{
     const result=original.apply(this,args);
     measured.read_calls++;measured.read_bytes+=typeof size==='function'?size(args[0],result):size;
     return result;
    }finally{depth--;}
   };
  }
  f.context.measureOwnedPresence=(original,mark)=>{
   measured={read_calls:0,read_bytes:0};
   const start=performance.now();
   try{return original(mark);}
   finally{
    measured.elapsed_ms=performance.now()-start;
    budgets[profile].push(measured);measured=null;
   }
  };
  f.vm.runInContext('const measureTarget=captureAutoChessPresence; captureAutoChessPresence=mark=>measureOwnedPresence(measureTarget,mark)',f.context);
 }
 function request(with_presence){
  f.requests.get('arkdps-event-request')({operation:'mark',id:1,with_presence});
  return f.messages.at(-1).result;
 }
 try{
  if(wire){
   let after=0;
   return [0,1,2].map(second=>{
    if(second===1){
     f.clock(16,1n<<31n);
     f.vm.runInContext(`
      beginEnemyHook('Character.OnSkillFinish');
      record('char_shared','skill_end',1,time(),{method:'Character.OnSkillFinish'},{skill_key:'skchr_fixture_1'},104);
      endEnemyHook('Character.OnSkillFinish');`,f.context);
    }
    const closingFrame=32*second,openingFrame=Math.max(0,closingFrame-8);
    f.clock(openingFrame,BigInt(openingFrame)<<27n);
    const opening=request(false);
    f.clock(closingFrame,BigInt(closingFrame)<<27n);
    const closing=request(true);
    f.requests.get('arkdps-event-request')({operation:'read',id:2,after,through:closing.sequence});
    after=closing.sequence;
    return {opening,closing,drain:f.messages.at(-1).result};
   });
  }
  const marked=request(true);
  const check={...marked};delete check.native_autochess_presence;
  assert.equal(JSON.stringify(check),JSON.stringify(original),'optional observation changed core mark or fence');
  if(fault==='callback_sequence')assert.equal(f.mark().sequence,original.sequence+1);
  if(fault===null&&platform==='mumu'){
   const repeated=request(true);
   assert.equal(JSON.stringify(repeated),JSON.stringify(marked),'warm metadata changed observation');
  }
  return marked.native_autochess_presence??null;
 }finally{
  for(const callback of restore)callback();
  pointer.readByteArray=originalBlock;pointer.readU32=originalUid;
 }
}

const output={
 pc:sampled({platform:'native_pc'}),
 all:sampled({profile:'two_characters'}),local:sampled({phase:4}),boss:sampled({phase:6}),
 wire:sampled({phase:4,wire:true}),
 empty:sampled({count:0,profile:'empty'}),inactive:sampled({fault:'inactive'}),token:sampled({fault:'token'}),
 crowded:sampled({enemies:256,profile:'crowded'}),character_limit:sampled({count:128,profile:'character_limit'}),
 too_many_characters:sampled({count:129,profile:'over_character_limit'}),
 callback_sequence:sampled({fault:'callback_sequence'}),
 invalid:Object.fromEntries(['duplicate_instance','slot_bound','deadline','clock','scope','slot_changed','unit_changed','short_read']
  .map(fault=>[fault,sampled({fault})])),
 budgets,
};
process.stdout.write(JSON.stringify(output));
