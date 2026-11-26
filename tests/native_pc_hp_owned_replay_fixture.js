'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
function run(owned,transition=null){
 const f=fixture({platform:'native_pc',enemyHpState:true,sourcePath:path.resolve('event_probe_native_pc.js'),globals:{arkdpsCoalesceEnemyHp:false}});
 f.context.ownedScopeKey='owned:round:8';f.context.ownedRound=8;
 f.vm.runInContext("probeAdapter.stampClock=when=>{when.native_autochess_scope={protocol_version:1,status:'available',verified_same_frame:true,frame:when.frame,game_time:when.game_time,scope_key:ownedScopeKey,phase:4,round:ownedRound,collection_mode:autoCollectionMode(ownedScopeKey,4)};};",f.context);
 const enemy=f.unit('enemy_test',12),attributes=f.alloc(),cache=f.alloc(),stringClass=f.alloc();
 f.write(enemy,f.enemyClass);f.write(enemy.add(0x25),1);f.write(enemy.add(0x26),0);f.write(enemy.add(0x40),100n*4294967296n);f.write(enemy.add(0xb0),attributes);f.write(attributes.add(0x68),cache);f.write(cache.add(0x18),1);f.write(cache.add(0x20),0n);f.write(cache.add(0x28),1000n*4294967296n);f.write(cache.add(0x30),1);
 const string=enemy.add(0x148).readPointer();f.write(string,stringClass);
 f.clock(30,4294967296n);const clockAddress=f.vm.runInContext('clock',f.context);
 const hook=f.callback('SetHpInternal',f.entity),P=f.ptr(0).constructor;
 const original=Object.fromEntries(['readByteArray','readPointer','readU8','readU16','readU32','readS32','readU64','readS64','readFloat','readUtf16String','readCString'].map(name=>[name,P.prototype[name]]));
 let reads=0,bytes=0,late=0,entryEpoch=0,scopeEpoch=0,firstMode=null;let plan;
 if(owned){
  f.context.ArrayBuffer=ArrayBuffer;f.context.NativePointer=P;f.context.NativeInteger=class {};f.context.__read=()=>{late++;throw Error('LIVE');};
  const bridge=fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8');
  f.vm.runInContext(bridge.slice(bridge.indexOf('function __integer('),bridge.indexOf('class NativeInteger')),f.context);
  f.vm.runInContext("nativeCaptureModeHistory.set(0,'all');",f.context);
  f.vm.runInContext('globalThis.replaying=()=>__arkdpsReplayActive();globalThis.ownedRead=(a,n)=>__arkdpsOwnedRead(BigInt(a),n);',f.context);
  const adapter=fs.readFileSync('event_probe_native_pc.js','utf8');
  const c=vm.createContext({});vm.runInContext(adapter.slice(adapter.indexOf('function nativeCapturePlan(')),c);
  plan=c.nativeCapturePlan('hp.Entity.SetHpInternal',{toString:()=> '0x1'},{clock:clockAddress,string_class:stringClass.toString(),skill_offsets:{},classes:{Enemy:f.enemyClass.toString(),BObject:f.bObject.toString()},immutable_class_parents:true});
  function copy(p,n){reads++;bytes+=n;if(n===1)return Uint8Array.of(original.readU8.call(p)).buffer;return original.readByteArray.call(p,n);}
  function capture(edge){const segments=[],fields=[];
   for(const field of plan.fields.filter(x=>x.phase===edge)){
    let p=field.root==='global_slot'?clockAddress:enemy;
    const add=(a,b)=>segments.push({field_id:field.field_id,role:field.role,checkpoint:0,address:a.toString(),bytes:b,status:'ok',guard_id:field.guard_id});
    try{
     for(const offset of field.pointer_offsets){const a=p.add(offset);add(a,copy(a,8));p=original.readPointer.call(a);}
     p=p.add(field.offset);
     if(field.kind==='utf16'){
      add(p,copy(p,8));add(p.add(0x10),copy(p.add(0x10),4));const n=original.readS32.call(p.add(0x10)),s=original.readUtf16String.call(p.add(0x14));assert(n<=field.max_units);
      const b=new ArrayBuffer(n*2),v=new DataView(b);for(let i=0;i<n;i++)v.setUint16(i*2,s.charCodeAt(i),true);reads++;bytes+=b.byteLength;add(p.add(0x14),b);
     }else add(p,copy(p,field.bytes));
     fields.push({field_id:field.field_id,role:field.role,checkpoint:0,status:'ok'});
    }catch(e){throw new Error('owned capture plan incomplete '+field.name+': '+e);}
   }
   return {version:1,edge,segments,fields,facts:{mode_epoch:entryEpoch,scope_mode_epoch:scopeEpoch}};
  }
  for(const [name,native]of Object.entries(f.metadataExports))f.metadataExports[name]=(...args)=>{if(f.context.replaying()){late++;throw Error('NativeFunction during replay '+name);}return native(...args);};
  const view=(p,n)=>new DataView(f.context.ownedRead(p.toString(),n));
  for(const [name,size,get]of [['readU8',1,'getUint8'],['readU16',2,'getUint16'],['readU32',4,'getUint32'],['readS32',4,'getInt32'],['readFloat',4,'getFloat32']])P.prototype[name]=function(){return f.context.replaying()?view(this,size)[get](0,true):original[name].call(this);};
  P.prototype.readByteArray=function(n){return f.context.replaying()?f.context.ownedRead(this.toString(),n):original.readByteArray.call(this,n);};
  P.prototype.readPointer=function(){return f.context.replaying()?f.ptr(view(this,8).getBigUint64(0,true)):original.readPointer.call(this);};
  for(const name of ['readS64','readU64'])P.prototype[name]=function(){if(!f.context.replaying())return original[name].call(this);const value=view(this,8)[name==='readS64'?'getBigInt64':'getBigUint64'](0,true);return {toString:()=>value.toString(),toNumber:()=>Number(value)};};
  P.prototype.readUtf16String=function(n){if(!f.context.replaying())return original.readUtf16String.call(this,n);const v=view(this,n*2);let s='';for(let i=0;i<n;i++)s+=String.fromCharCode(v.getUint16(i*2,true));return s;};
  P.prototype.readCString=function(){if(f.context.replaying()){late++;throw Error('uncaptured metadata');}return original.readCString.call(this);};
  f.context.replayCall=(record,body)=>{f.context.capturedRecord=record;f.context.body=body;return f.vm.runInContext('__arkdpsReplayBegin(capturedRecord);try{withCapturedMode(capturedRecord,()=>__arkdpsReplayPoint("body",body));}finally{globalThis.summary=__arkdpsReplayEnd();}',f.context);};
  f.capture=capture;
 }
 let index=0;
 for(const hp of [101,102,98,99,99,101]){
  const invocation={threadId:1};
  if(owned)f.context.replayCall(f.capture('entry'),()=>hook.onEnter.call(invocation,[enemy]));else hook.onEnter.call(invocation,[enemy]);
  if(owned&& !invocation.write)throw Error('no write '+JSON.stringify({keys:Object.keys(invocation),summary:f.context.summary,quality:JSON.parse(f.vm.runInContext('JSON.stringify(eventQuality())',f.context))}));
  if(owned&&transition&&index===0){f.vm.runInContext('installCaptureMode(false,{version:1,mode_epoch:1})',f.context);scopeEpoch=1;if(transition==='newscope'){f.context.ownedScopeKey='owned:round:9';f.context.ownedRound=9;f.clock(31,2n*4294967296n);}}
  f.write(enemy.add(0x40),BigInt(hp)*4294967296n);
  if(owned){
   const captured=f.capture('leave');
   // Overwrite/reuse live fixture memory immediately after native return.
   // The worker must still see the owned return UID/HP, never these values.
   f.write(enemy.add(0x40),999999n*4294967296n);f.write(enemy.add(0x18),999);
   f.context.replayCall(captured,()=>hook.onLeave.call(invocation));assert.equal(f.context.summary.complete,true,JSON.stringify(f.context.summary));
   f.write(enemy.add(0x40),BigInt(hp)*4294967296n);f.write(enemy.add(0x18),12);
  }else hook.onLeave.call(invocation);
  if(owned&&index===0)firstMode=JSON.parse(f.vm.runInContext('JSON.stringify(nativeAutoEffective)',f.context));
  if(transition)entryEpoch=1;index++;
 }
 const events=JSON.parse(f.vm.runInContext('JSON.stringify(queue)',f.context));
 for(const [name,method]of Object.entries(original))P.prototype[name]=method;
 return {events,reads,bytes,late,firstMode};
}
const baseline=run(false),owned=run(true);assert.deepEqual(owned.events,baseline.events);assert.equal(owned.late,0);assert.equal(owned.events.length,5);assert.equal(owned.events[0].kind,'enemy_hp');
console.log(JSON.stringify({status:'PASS_OWNED_HP_SOURCE_REPLAY',callbacks:6,events:owned.events.length,source_reads:owned.reads,source_bytes:owned.bytes,late_reads:owned.late,raw_events_equal:true}));

const sameScope=run(true,'same'),newScope=run(true,'newscope');assert.equal(sameScope.firstMode.mode,'all');assert.equal(newScope.firstMode.mode,'local');assert.equal(newScope.firstMode.scope_key,'owned:round:9');assert.equal(newScope.events.length,4);assert.equal(newScope.late,0);console.log('PASS_REAL_HP_MODE_TRANSITION old invocation/new physical scope uses observed preference, old same scope remains latched');
