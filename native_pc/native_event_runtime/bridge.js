// Narrow compatibility surface for the unmodified PC event probe. Not a Frida runtime.
'use strict';
function __integer(value) {
 if(value instanceof NativePointer)return value.value;
 if(value instanceof NativeInteger)return value.value;
 if(typeof value==='bigint')return value;
 if(typeof value==='number'&&!Number.isSafeInteger(value))throw new TypeError('unsafe integer');
 return BigInt(value);
}
// Replay never dereferences a game address. A role is an independently captured
// observation point, not a last-value cache keyed by pointer.
let __arkdpsReplay=null;
function __arkdpsReplayBegin(record){
 if(__arkdpsReplay!==null)throw new Error('replay already active');
 if(record?.version!==1||!Array.isArray(record.segments)||typeof record.edge!=='string')
  throw new Error('invalid capture record');
 const segments=record.segments.map(segment=>{
  if(!Number.isInteger(segment.field_id)||segment.field_id<0||segment.field_id>65535||typeof segment.role!=='string'||!Number.isSafeInteger(segment.checkpoint)||segment.checkpoint<0||
     !(segment.bytes instanceof ArrayBuffer)||!['ok','unreadable','short','limit','guard_changed'].includes(segment.status))
   throw new Error('invalid owned segment');
  const address=__integer(segment.address);
  if(address<0n||address>0xffffffffffffffffn||address+BigInt(segment.bytes.byteLength)>0x10000000000000000n)throw new Error('invalid segment address');
  return {...segment,address,bytes:segment.bytes.slice(0)};
 });
 const checkpoint=record.checkpoint??0;
 if(!Number.isSafeInteger(checkpoint)||checkpoint<0)throw new Error('invalid record checkpoint');
 const fields=(record.fields||[]).map(field=>{
  const point=field.checkpoint??checkpoint;
  if(!Number.isSafeInteger(point)||point<0||!Number.isInteger(field.field_id)||field.field_id<0||field.field_id>65535||typeof field.role!=='string'||typeof field.status!=='string')throw new Error('invalid captured field state');
  const attempted_address=field.attempted_address??field.attempt_address,attempted_size=field.attempted_size??field.attempt_size;
  if(attempted_address!==undefined||attempted_size!==undefined){
   const address=__integer(attempted_address);
   if(address<0n||address>0xffffffffffffffffn||!Number.isSafeInteger(attempted_size)||attempted_size<0||address+BigInt(attempted_size)>0x10000000000000000n)throw new Error('invalid captured attempt range');
  }
  return {...field,checkpoint:point,attempted_address,attempted_size};
 });
 __arkdpsReplay={record,segments,fields,role:record.edge,checkpoint,fieldId:0,structural:[],unreadable:0};
}
function __arkdpsReplayRole(role,checkpoint=0,fieldId=0){
 if(__arkdpsReplay===null||typeof role!=='string'||!Number.isSafeInteger(checkpoint)||checkpoint<0||!Number.isInteger(fieldId)||fieldId<0||fieldId>65535)
  throw new Error('invalid replay role');
 __arkdpsReplay.role=role;__arkdpsReplay.checkpoint=checkpoint;__arkdpsReplay.fieldId=fieldId;
}
function __arkdpsReplayActive(){return __arkdpsReplay!==null;}
function __arkdpsReplayRecord(){if(__arkdpsReplay===null)throw new Error('no replay active');return __arkdpsReplay.record;}
function __arkdpsCapturedAssignable(base,klass){
 if(__arkdpsReplay===null)throw new Error('captured ancestry outside replay');
 base=ptr(base);let current=ptr(klass);
 for(let depth=0;depth<10&&!current.isNull();depth++){
  if(current.equals(base))return true;
  current=current.add(0x58).readPointer();
 }
 if(current.isNull())return false;
 __arkdpsReplayGap('captured class ancestry limit');
}
function __arkdpsReplayPoint(role,body){
 if(__arkdpsReplay===null)return body();
 if(typeof role!=='string'||typeof body!=='function')__arkdpsReplayGap('invalid replay point');
 const replay=__arkdpsReplay,previous=[replay.role,replay.fieldId];
 replay.role=role;replay.fieldId=null;
 try{return body();}finally{replay.role=previous[0];replay.fieldId=previous[1];}
}
function __arkdpsReplayWitness(role,body){
 if(__arkdpsReplay===null)return body();
 const replay=__arkdpsReplay,previous=[replay.role,replay.fieldId];
 const ids=new Set([...replay.segments,...replay.fields].filter(f=>f.role===role&&f.checkpoint===replay.checkpoint).map(f=>f.field_id));
 if(ids.size!==1)__arkdpsReplayGap('missing or ambiguous witness '+role);
 replay.role=role;replay.fieldId=[...ids][0];
 try{return body();}finally{replay.role=previous[0];replay.fieldId=previous[1];}
}
function __arkdpsReplayEnd(){
 if(__arkdpsReplay===null)throw new Error('no replay active');
 const result=Object.freeze({complete:__arkdpsReplay.structural.length===0,structural_gaps:Object.freeze([...__arkdpsReplay.structural]),unreadable_reads:__arkdpsReplay.unreadable});
 __arkdpsReplay=null;return result;
}
function __arkdpsReplayGap(reason){
 if(__arkdpsReplay!==null&&__arkdpsReplay.structural.length<16)__arkdpsReplay.structural.push(reason);
 throw new Error(reason);
}
function __arkdpsOwnedRead(address,size){
 if(!Number.isSafeInteger(size)||size<0||size>1048576){
  if(__arkdpsReplay!==null)__arkdpsReplayGap('invalid captured read size');
  throw new Error('invalid captured read size');
 }
 const replay=__arkdpsReplay;
 if(replay===null)return __read(address,size);
 const matches=replay.segments.filter(s=>s.role===replay.role&&s.checkpoint===replay.checkpoint&&(replay.fieldId===null||s.field_id===replay.fieldId)&&
  address>=s.address&&address+BigInt(size)<=s.address+BigInt(s.bytes.byteLength));
 const failedMatching=replay.fields.filter(f=>f.role===replay.role&&f.checkpoint===replay.checkpoint&&f.status!=='ok'&&
  matches.some(s=>s.field_id===f.field_id));
 if(failedMatching.length){
  if(failedMatching.some(f=>['missing_plan_field','invalid_plan','missing_field'].includes(f.status)))__arkdpsReplayGap('invalid captured plan field '+replay.role);
  replay.unreadable++;throw new Error('captured field failed '+replay.role);
 }
 if(matches.length===0){
  const failed=replay.fields.filter(f=>f.role===replay.role&&f.checkpoint===replay.checkpoint&&(replay.fieldId===null||f.field_id===replay.fieldId)&&f.status!=='ok'&&
   (replay.fieldId!==null||f.attempted_address!==undefined&&Number.isSafeInteger(f.attempted_size)&&f.attempted_size>=0&&
    address>=__integer(f.attempted_address)&&address+BigInt(size)<=__integer(f.attempted_address)+BigInt(f.attempted_size)));
  if(failed.length===1){if(['missing_plan_field','invalid_plan','missing_field'].includes(failed[0].status))__arkdpsReplayGap('invalid captured plan field '+replay.role);replay.unreadable++;throw new Error('captured field unreadable '+replay.role);}
 }
 if(matches.length===0)__arkdpsReplayGap('missing captured read '+replay.role);
 if(matches.some(s=>s.status!=='ok')){replay.unreadable++;throw new Error('captured field unreadable '+replay.role);}
 const segment=matches[0],start=Number(address-segment.address),result=segment.bytes.slice(start,start+size);
 if(matches.length>1){
  if(replay.fieldId!==null||matches.some(s=>(s.guard_id??0)!==0))__arkdpsReplayGap('ambiguous captured witness '+replay.role);
  const expected=new Uint8Array(result);
  for(const candidate of matches.slice(1)){
   const other=new Uint8Array(candidate.bytes,Number(address-candidate.address),size);
   if(other.some((v,i)=>v!==expected[i]))__arkdpsReplayGap('conflicting captured point '+replay.role);
  }
 }
 return result;
}
function __arkdpsOwnedString(address,length,utf16){
 if(__arkdpsReplay===null)return utf16?__utf16(address,length):__cstring(address);
 if(utf16){
  if(!Number.isSafeInteger(length)||length<0||length>200)throw new Error('invalid captured string length');
  const v=new DataView(__arkdpsOwnedRead(address,length*2));let result='';
  for(let i=0;i<length;i++)result+=String.fromCharCode(v.getUint16(i*2,true));
  return result;
 }
 // Immutable metadata strings also require owned bytes during replay.
 let result='';
 for(let i=0;i<1024;i++){const c=new Uint8Array(__arkdpsOwnedRead(address+BigInt(i),1))[0];if(c===0)return result;result+=String.fromCharCode(c);}
 throw new Error('unterminated captured C string');
}
class NativeInteger {
 constructor(value){this.value=BigInt(value);}
 toNumber(){return Number(this.value);}
 toString(radix=10){return this.value.toString(radix);}
}
class NativePointer {
 constructor(value){this.value=BigInt.asUintN(64,__integer(value));}
 add(value){return new NativePointer(this.value+__integer(value));}
 sub(value){return new NativePointer(this.value-__integer(value));}
 equals(value){return this.value===ptr(value).value;}
 isNull(){return this.value===0n;}
 toString(){return '0x'+this.value.toString(16);}
 toInt32(){return Number(BigInt.asIntN(32,this.value));}
 readByteArray(size){return __arkdpsOwnedRead(this.value,size);}
 __view(size){return new DataView(this.readByteArray(size));}
 readPointer(){return new NativePointer(this.__view(8).getBigUint64(0,true));}
 readU8(){return this.__view(1).getUint8(0);}
 readU16(){return this.__view(2).getUint16(0,true);}
 readU32(){return this.__view(4).getUint32(0,true);}
 readS32(){return this.__view(4).getInt32(0,true);}
 readU64(){return new NativeInteger(this.__view(8).getBigUint64(0,true));}
 readS64(){return new NativeInteger(this.__view(8).getBigInt64(0,true));}
 readFloat(){return this.__view(4).getFloat32(0,true);}
 readCString(){return __arkdpsOwnedString(this.value,0,false);}
 readUtf16String(length){return __arkdpsOwnedString(this.value,length,true);}
 writeU64(value){if(__arkdpsReplay!==null)__arkdpsReplayGap('native write during replay');const b=new ArrayBuffer(8);new DataView(b).setBigUint64(0,BigInt.asUintN(64,__integer(value)),true);__write(this.value,b);return this;}
 writePointer(value){return this.writeU64(value);}
}
function ptr(value){return value instanceof NativePointer?value:new NativePointer(value);}
// The engine passes an owned copy of every integer argument slot. Preserve
// dense-array behavior, but build a pointer only when its value is inspected.
const __deferredArgument=Symbol(),__argumentViews=new WeakMap(),__argumentMasks=new WeakMap();
function __materializeArgument(array,key){
 const index=Number(key);
 if((__argumentMasks.get(array)&(1<<index))===0)__arkdpsReplayGap('uncaptured native argument '+index);
 const pointer=new NativePointer(__argumentViews.get(array).getBigUint64(Number(key)*8,true));
 array[key]=pointer;
 return pointer;
}
const __argumentHandler={
 get(array,key,receiver){
  const value=Reflect.get(array,key,receiver);
  return value===__deferredArgument?__materializeArgument(array,key):value;
 },
 getOwnPropertyDescriptor(array,key){
  const descriptor=Reflect.getOwnPropertyDescriptor(array,key);
  if(descriptor?.value===__deferredArgument)descriptor.value=__materializeArgument(array,key);
  return descriptor;
 },
 defineProperty(array,key,descriptor){
  if(descriptor.writable===false&&!Object.hasOwn(descriptor,'value')&&
     Reflect.getOwnPropertyDescriptor(array,key)?.value===__deferredArgument)
   __materializeArgument(array,key);
  return Reflect.defineProperty(array,key,descriptor);
 }
};
function __arkdpsArguments(bytes,mask=null){
 if(!(bytes instanceof ArrayBuffer)||bytes.byteLength%8!==0||bytes.byteLength>128)__arkdpsReplayGap('invalid native argument bytes');
 const count=bytes.byteLength/8,allowed=(1<<count)-1;
 if(mask===null)mask=allowed;
 if(!Number.isInteger(mask)||mask<0||mask>65535||(mask&~allowed)!==0)__arkdpsReplayGap('invalid native argument mask');
 const view=new DataView(bytes),args=Array(count).fill(__deferredArgument);
 __argumentViews.set(args,view);__argumentMasks.set(args,mask);
 return new Proxy(args,__argumentHandler);
}
const Memory={alloc(size){if(__arkdpsReplay!==null)__arkdpsReplayGap('native allocation during replay');return ptr(__alloc(size));}};
function __module(name){
 const found=__moduleInfo(String(name));
 return {name:found.name,base:ptr(found.base),size:found.size,
  getExportByName(symbol){return ptr(__export(found.name,String(symbol)));}};
}
const Process={platform:'windows',arch:'x64',id:__pid(),
 getModuleByName:__module,
 findModuleByAddress(address){const found=__findModule(ptr(address).value);return found===null?null:__module(found);}};
let __arkdpsMarkRequest=null;
function __arkdpsMarkEntryActive(){return __arkdpsMarkRequest?.entryActive??__nativeActive();}
function NativeFunction(address,result,parameters){
 if(!Array.isArray(parameters)||parameters.length>12)throw new Error('unsupported native signature');
 const allowed=new Set(['pointer','uint','int','size_t','bool','void']);
 if(!allowed.has(result)||parameters.some(t=>!allowed.has(t)||t==='void'))throw new Error('only GP IL2CPP signatures are supported');
 const target=ptr(address).value;
 return function(...args){
  if(__arkdpsReplay!==null)__arkdpsReplayGap('NativeFunction forbidden during replay');
  if(args.length!==parameters.length)throw new Error('native argument count mismatch');
  // A native call could enter a hook recursively on this lock's owner.
  if(__arkdpsMarkRequest!==null)__arkdpsMarkRequest.nativeCall=true;
  const raw=__nativeCall(target,args.map((v,i)=>parameters[i]==='bool'?(v?1n:0n):__integer(v)));
  switch(result){case 'void':return undefined;case 'pointer':return ptr(raw);case 'bool':return BigInt.asUintN(8,raw)!==0n;
   case 'int':return Number(BigInt.asIntN(32,raw));case 'uint':return Number(BigInt.asUintN(32,raw));case 'size_t':return Number(raw);}
 };
}
const Interceptor={attach(address,callbacks){const id=__attach(ptr(address).value,callbacks);return {detach(){__detach(id);}};}};
const rpc={exports:{}};
function recv(){/* Event requests are dispatched synchronously by the authenticated pipe. */}
function send(value){__send(JSON.stringify(value));}
function __arkdpsPointer(value){return ptr(value);}
function __arkdpsRequest(json){
 const command=JSON.parse(json),api=rpc.exports;
 if(!command||typeof command!=='object')throw new Error('invalid request');
 // Do not even advance the replayable-read watermark while a native callback
 // is pending on this JS lock. A retry cannot authorize a later tail ACK.
 if(command.operation==='terminal_read'&&__nativeActive()>0)
  return JSON.stringify({events:[],hp_context:[],retry:true,terminal_only:true,terminal_read_version:1,
   active:__nativeActive(),active_hooks:[],after:command.after,through:command.through});
 let result,markRequest=null;
 switch(command.operation){
  case 'mark':{
   if(Object.prototype.hasOwnProperty.call(command,'autochess_enabled')&&typeof probeAdapter!=='undefined'&&typeof probeAdapter.setAutoChessMode==='function')
    probeAdapter.setAutoChessMode(command.autochess_enabled);
   if(command.native_quiet_boundary_version===1)
    __arkdpsMarkRequest=markRequest={entryActive:__nativeActive(),nativeCall:false};
   try{result=api.mark();}finally{__arkdpsMarkRequest=null;}
   break;
  }
  case 'read':result=api.readEvents(command.after,command.through);break;
  case 'terminal_read':result=api.readTerminalEvents(command.after,command.through);break;
  case 'ack':result=api.ackEvents(command.sequence);break;
  case 'stop':result=api.stop();break;
  default:throw new Error('unknown operation');
 }
 if(command.operation==='mark'||command.operation==='terminal_read'){
  const pending=__nativeActive(),scope=result.native_autochess_context;
  // The engine holds the same lock for this request and the JS onEnter.
  // With no callback at entry, incoming threads have yet to run onEnter or
  // the original function. Native calls made by the lock owner disqualify
  // this boundary because the mutex permits recursive entry.
  const defer=pending>0&&markRequest?.entryActive===0&&!markRequest.nativeCall&&
   result.active===0&&Array.isArray(result.active_hooks)&&result.active_hooks.length===0&&
   !result.retry&&result.error===null&&result.stopped===false&&result.terminal_boundary==null&&
   result.native_battle_state===2&&scope?.protocol_version===1&&scope.status==='available'&&
   scope.verified_same_frame===true&&[4,5,6].includes(scope.phase)&&
   typeof scope.scope_key==='string'&&scope.scope_key.length>0&&Number.isSafeInteger(scope.round)&&scope.round>=0&&
   ['all','local'].includes(scope.collection_mode)&&
   scope.frame===result.frame&&scope.game_time===result.game_time;
  const outstanding=Math.max(pending,markRequest?.entryActive??0);
  if(defer){
   result.native_quiet_boundary_version=1;result.native_deferred_callbacks=pending;
  }else if(outstanding>0){
   result.active=Math.max(result.active||0,outstanding);result.retry=true;
   if(command.operation==='terminal_read'){result.events=[];result.hp_context=[];}
  }
 }
 return JSON.stringify(result);
}
