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
function __arkdpsStructuralCaptureStatus(status){return ['missing_plan_field','invalid_plan','missing_field','bounds','ambiguous_read','invalid_phase'].includes(status);}
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
 const points=new Map();
 const point=(role,at)=>{
  const key=role+'\0'+at;
  let value=points.get(key);
  if(!value){value={segments:[],states:new Map(),ids:new Set(),maxSpan:0n};points.set(key,value);}
  return value;
 };
 for(const segment of segments){const owned=point(segment.role,segment.checkpoint);owned.segments.push(segment);owned.ids.add(segment.field_id);const size=BigInt(segment.bytes.byteLength);if(size>owned.maxSpan)owned.maxSpan=size;}
 for(const field of fields){const owned=point(field.role,field.checkpoint);if(owned.states.has(field.field_id))throw new Error('duplicate captured field state');owned.states.set(field.field_id,field);owned.ids.add(field.field_id);}
 for(const value of points.values())value.segments.sort((a,b)=>a.address<b.address?-1:a.address>b.address?1:0);
 __arkdpsReplay={record,segments,fields,points,role:record.edge,checkpoint,fieldId:0,structural:fields.filter(f=>__arkdpsStructuralCaptureStatus(f.status)).map(f=>'invalid captured field '+f.role+':'+f.field_id+':'+f.status),unreadable:0};
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
  if(typeof __arkdpsColdMetadataRead==='function'){
   const owned=__arkdpsColdMetadataRead(current.add(0x58),8);
   if(!(owned instanceof ArrayBuffer)||owned.byteLength!==8)__arkdpsReplayGap('invalid frozen class parent');
   current=ptr(new DataView(owned).getBigUint64(0,true));
  }else current=current.add(0x58).readPointer();
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
function __arkdpsReplayCheckpoint(role,checkpoint,body){
 if(__arkdpsReplay===null)return body();
 if(!Number.isSafeInteger(checkpoint)||checkpoint<0)__arkdpsReplayGap('invalid parent checkpoint');
 const replay=__arkdpsReplay,previous=[replay.role,replay.fieldId,replay.checkpoint];
 replay.role=role;replay.fieldId=null;replay.checkpoint=checkpoint;
 try{return body();}finally{[replay.role,replay.fieldId,replay.checkpoint]=previous;}
}
function __arkdpsReplayWitness(role,body){
 if(__arkdpsReplay===null)return body();
 const replay=__arkdpsReplay,previous=[replay.role,replay.fieldId,replay.checkpoint],checkpoint=replay.record.checkpoint??0;
 const ids=replay.points.get(role+'\0'+checkpoint)?.ids??new Set();
 if(ids.size!==1)__arkdpsReplayGap('missing or ambiguous witness '+role);
 replay.role=role;replay.fieldId=[...ids][0];replay.checkpoint=checkpoint;
 try{return body();}finally{[replay.role,replay.fieldId,replay.checkpoint]=previous;}
}
function __arkdpsReplayOptional(role,read){
 if(__arkdpsReplay===null)return {available:true,value:read()};
 const replay=__arkdpsReplay,checkpoint=replay.record.checkpoint??0;
 const states=[...(replay.points.get(role+'\0'+checkpoint)?.states.values()??[])];
 if(states.length!==1)__arkdpsReplayGap('missing optional capture field '+role);
 if(states[0].status!=='ok'){
  if(__arkdpsStructuralCaptureStatus(states[0].status))__arkdpsReplayGap('invalid optional capture field '+role);
  replay.unreadable++;return {available:false,value:null};
 }
 return {available:true,value:__arkdpsReplayWitness(role,read)};
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
 const point=replay.points.get(replay.role+'\0'+replay.checkpoint);
 if(!point)__arkdpsReplayGap('missing captured point '+replay.role);
 // Most reads are scalars inside short object blocks. Binary search the
 // same-point interval index, then inspect only possible covering segments.
 const end=address+BigInt(size),segments=point.segments;let low=0,high=segments.length;
 while(low<high){const middle=(low+high)>>>1;if(segments[middle].address<=address)low=middle+1;else high=middle;}
 let matches=[];
 for(let index=low-1;index>=0;index--){
  const segment=segments[index];if(segment.address+point.maxSpan<end)break;
  if((replay.fieldId===null||segment.field_id===replay.fieldId)&&address>=segment.address&&end<=segment.address+BigInt(segment.bytes.byteLength))matches.push(segment);
 }
 const failedMatching=[...new Set(matches.map(s=>s.field_id))].map(id=>point.states.get(id)).filter(field=>field&&field.status!=='ok');
 if(failedMatching.length){
  if(failedMatching.some(f=>__arkdpsStructuralCaptureStatus(f.status)))__arkdpsReplayGap('invalid captured plan field '+replay.role);
  // A failed field cannot lend its already-read links. In auto-ID mode an
  // independently successful field may still own the exact requested bytes.
  if(replay.fieldId===null)matches=matches.filter(s=>!failedMatching.some(f=>f.field_id===s.field_id));
  if(matches.length===0||replay.fieldId!==null){replay.unreadable++;throw new Error('captured field failed '+replay.role);}
 }
 if(matches.length===0){
  const failed=[...point.states.values()].filter(f=>(replay.fieldId===null||f.field_id===replay.fieldId)&&f.status!=='ok'&&
   (f.attempted_address!==undefined&&Number.isSafeInteger(f.attempted_size)&&f.attempted_size>=0&&
    address>=__integer(f.attempted_address)&&address+BigInt(size)<=__integer(f.attempted_address)+BigInt(f.attempted_size)));
  if(failed.length===1){if(__arkdpsStructuralCaptureStatus(failed[0].status))__arkdpsReplayGap('invalid captured plan field '+replay.role);replay.unreadable++;throw new Error('captured field unreadable '+replay.role);}
 }
 if(matches.length===0)__arkdpsReplayGap('missing captured read '+replay.role);
 if(matches.some(s=>s.status!=='ok')){replay.unreadable++;throw new Error('captured field unreadable '+replay.role);}
 const segment=matches[0],start=Number(address-segment.address),result=segment.bytes.slice(start,start+size);
 if(matches.length>1){
  if(replay.fieldId===null&&matches.some(s=>(s.guard_id??0)!==0))__arkdpsReplayGap('ambiguous captured witness '+replay.role);
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
function __arkdpsMarkEntryActive(){return __nativeActive();}
function NativeFunction(address,result,parameters){
 if(!Array.isArray(parameters)||parameters.length>12)throw new Error('unsupported native signature');
 const allowed=new Set(['pointer','uint','int','size_t','bool','void']);
 if(!allowed.has(result)||parameters.some(t=>!allowed.has(t)||t==='void'))throw new Error('only GP IL2CPP signatures are supported');
 const target=ptr(address).value;
 return function(...args){
  if(__arkdpsReplay!==null)__arkdpsReplayGap('NativeFunction forbidden during replay');
  if(args.length!==parameters.length)throw new Error('native argument count mismatch');
  const raw=__nativeCall(target,args.map((v,i)=>parameters[i]==='bool'?(v?1n:0n):__integer(v)));
  switch(result){case 'void':return undefined;case 'pointer':return ptr(raw);case 'bool':return BigInt.asUintN(8,raw)!==0n;
   case 'int':return Number(BigInt.asIntN(32,raw));case 'uint':return Number(BigInt.asUintN(32,raw));case 'size_t':return Number(raw);}
 };
}
const Interceptor={attach(address,callbacks){const id=__attach(ptr(address).value,callbacks);return {detach(){__detach(id);}};}};
const rpc={exports:{}};
function recv(){/* The native worker owns authenticated pipe dispatch. */}
function send(value){__send(JSON.stringify(value));}
function __arkdpsPointer(value){return ptr(value);}
function __arkdpsRequest(json){
 const command=JSON.parse(json),api=rpc.exports;
 if(!command||typeof command!=='object')throw new Error('invalid request');
 // Statistical requests have exactly one native path: immutable publication
 // dispatch. Keep only explicit lifecycle cleanup on the shared probe API.
 if(command.operation!=='stop')throw new Error('native event requests require the publication journal');
 return JSON.stringify(api.stop());
}
