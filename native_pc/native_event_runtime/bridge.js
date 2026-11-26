// Narrow compatibility surface for the unmodified PC event probe. Not a Frida runtime.
'use strict';
function __integer(value) {
 if(value instanceof NativePointer)return value.value;
 if(value instanceof NativeInteger)return value.value;
 if(typeof value==='bigint')return value;
 if(typeof value==='number'&&!Number.isSafeInteger(value))throw new TypeError('unsafe integer');
 return BigInt(value);
}
// QuickJS builds may represent asUintN(64, high-bit BigInt) as signed.
// Preserve already canonical unsigned values and normalize only out-of-range
// integers with arithmetic; never truncate addresses through a JS Number.
function __arkdpsUnsigned64(value){
 const integer=__integer(value),modulus=0x10000000000000000n;
 if(integer>=0n&&integer<modulus)return integer;
 const remainder=integer%modulus;return remainder<0n?remainder+modulus:remainder;
}
// Replay never dereferences a game address. A role is an independently captured
// observation point, not a last-value cache keyed by pointer.
let __arkdpsReplay=null,__arkdpsNativeProvider=null,__arkdpsSealedNativeProvider=null;
// A record owns a small, fixed wire header. Cold fields stay lazy: most HP
// edges never inspect ancestry, parents, publication or terminal metadata.
// None of these getters consult the native view or a later capture lease.
const __arkdpsEmptyParents=Object.freeze([]),__arkdpsParentCache=Symbol();
function __arkdpsPackedNumber(view,slot){const offset=slot*8;return view.getUint32(offset,true)+view.getUint32(offset+4,true)*4294967296;}
function __arkdpsPackedParents(view){
 const cached=view[__arkdpsParentCache];if(cached!==undefined)return cached;
 const count=__arkdpsPackedNumber(view,21);if(!count)return __arkdpsEmptyParents;
 const values=[];
 for(let i=0;i<count;i++){
  const at=176+i*16,invocation=view.getBigUint64(at,true),checkpoint=view.getUint32(at+8,true);
  if(invocation===0n||checkpoint!==i+1||view.getUint32(at+12,true)!==0)throw Error('invalid packed parent checkpoint');
  values.push(Object.freeze({invocation_id:invocation.toString(),checkpoint}));
 }
 return view[__arkdpsParentCache]=Object.freeze(values);
}
class __ArkDpsCaptureFacts {
 #view;
 constructor(view){this.#view=view;Object.freeze(this);}
 get mode_epoch(){return __arkdpsPackedNumber(this.#view,7);}
 get scope_mode_epoch(){return __arkdpsPackedNumber(this.#view,8);}
 get scope_revision(){return __arkdpsPackedNumber(this.#view,9)||undefined;}
 get publication_revision(){return __arkdpsPackedNumber(this.#view,10)||undefined;}
 get cold_revision(){return __arkdpsPackedNumber(this.#view,11)||undefined;}
 get scope_unavailable(){return (__arkdpsPackedNumber(this.#view,20)&1)!==0;}
 get capture_concurrent(){return (__arkdpsPackedNumber(this.#view,20)&2)!==0;}
 get parent_checkpoints(){return __arkdpsPackedParents(this.#view);}
}
class __ArkDpsCaptureRecord {
 #view;#facts;#ticket;
 constructor(bytes){
  if(!(bytes instanceof ArrayBuffer)||bytes.byteLength<176)throw Error('invalid packed capture header');
  this.#view=new DataView(bytes);
  const count=this.number(21);
  if(count>256||bytes.byteLength!==176+count*16||this.number(18)>1||this.number(20)>3)throw Error('invalid packed capture geometry');
  this.#ticket=this.text(0);Object.freeze(this);
 }
 number(slot){return __arkdpsPackedNumber(this.#view,slot);}
 text(slot){return this.#view.getBigUint64(slot*8,true).toString();}
 parents(){return __arkdpsPackedParents(this.#view);}
 get version(){return 1;}get native_view(){return true;}
 get ticket(){return this.#ticket;}
 get invocation_id(){return this.text(1);}get parent_invocation_id(){return this.text(2);}
 get activity_begin(){return this.text(3);}get activity_end(){return this.text(4);}
 get thread_birth(){return this.text(5);}get publication_request(){return this.text(6);}
 get hook_id(){return this.number(12);}get checkpoint(){return this.number(13);}
 get active_at_capture(){return this.number(14);}get active_after(){return this.number(15);}
 get lane(){return this.number(16);}get thread_id(){return this.number(17);}
 get edge(){return this.number(18)===0?'entry':'leave';}get closed_after(){return this.number(19)!==0;}
 get facts(){return this.#facts??(this.#facts=new __ArkDpsCaptureFacts(this.#view));}
}
function __arkdpsPackedCaptureRecord(bytes){return new __ArkDpsCaptureRecord(bytes);}
// One C++ -> JS entry per edge. The native owned-view lease is established
// by the worker before calling this function and remains live through End.
// The result is only a transport envelope; C++ must independently inspect its
// poison flag and complete bit before passing the projection to the journal.
function __arkdpsReplayDispatch(header,callback,receiver,payload,argumentMask,prefix,returnAddress,prepared,contract){
 const record=__arkdpsPackedCaptureRecord(header);
 __arkdpsReplayBegin(record);
 const replay=__arkdpsReplay;
 let projection,summary,healthy=false;
 try{
  if(typeof __nativeReplayHealthy!=='function')__arkdpsReplayGap('native replay health helper unavailable');
  if(prepared!==undefined){
   if(typeof callback!=='function'||typeof payload!=='bigint')__arkdpsReplayGap('invalid prepared HP callback');
   if(record.edge==='entry'&&argumentMask!==1)__arkdpsReplayGap('uncaptured native argument');
   const previousRole=replay.role,previousField=replay.fieldId,previousCheckpoint=replay.checkpoint;
   replay.role='body';replay.fieldId=null;replay.checkpoint=record.checkpoint??0;
   try{
    const enemy=record.edge==='entry'?ptr(payload):payload;
    const io=__arkdpsPreparedHpObservation(contract,enemy,prepared);
    if(record.edge==='entry'){receiver={threadId:record.thread_id};callback.call(receiver,enemy,io);}
    else callback.call(receiver,io);
   }finally{replay.role=previousRole;replay.fieldId=previousField;replay.checkpoint=previousCheckpoint;}
  }else{
  if(record.edge==='entry'){
   if(typeof returnAddress!=='bigint')__arkdpsReplayGap('native entry return address unavailable');
   receiver={threadId:record.thread_id,returnAddress:ptr(returnAddress),nativeCaptureInvocation:record.invocation_id,nativeCaptureModeEpoch:record.number(7)};
  }
  if(typeof callback==='function'){
   if(record.edge==='entry')callback.call(receiver,__arkdpsArguments(payload,argumentMask));
   else callback.call(receiver,ptr(payload));
  }
  }
  healthy=__nativeReplayHealthy();
  if(typeof healthy!=='boolean')__arkdpsReplayGap('invalid native replay health');
  if(healthy&&__arkdpsReplay.structural.length===0&&record.edge==='leave'&&record.closed_after&&typeof prefix==='function')
   projection=prefix(record);
 }finally{summary=__arkdpsReplayEnd(true);}
 const result=[record,projection,healthy&&summary,receiver];
 if(!result[2])result.push(replay.diagnostic||__arkdpsReplayDiagnostic(replay));
 return result;
}
function __arkdpsStructuralCaptureStatus(status){return ['missing_plan_field','invalid_plan','missing_field','bounds','ambiguous_read','invalid_phase'].includes(status);}
const __arkdpsProviderDescriptor=Object.getOwnPropertyDescriptor,__arkdpsProviderKeys=Reflect.ownKeys,__arkdpsProviderExtensible=Object.isExtensible,__arkdpsProviderPrototype=Object.getPrototypeOf,__arkdpsProviderHasOwn=Object.hasOwn;
function __arkdpsSealedProvider(){
 if(__arkdpsSealedNativeProvider!==null)return __arkdpsSealedNativeProvider;
 const installed=__arkdpsProviderDescriptor(globalThis,'__nativeOwnedProvider');
 if(installed===undefined)return null;
 if(!__arkdpsProviderHasOwn(installed,'value')||installed.writable!==false||installed.configurable!==false||installed.value===null||typeof installed.value!=='object')throw Error('native owned provider bundle is mutable');
 const bundle=installed.value,keys=__arkdpsProviderKeys(bundle);
 if(__arkdpsProviderExtensible(bundle)||__arkdpsProviderPrototype(bundle)!==null)throw Error('native owned provider bundle has mutable structure');
 const required=['__nativeOwnedLease','__nativeOwnedRead','__nativeOwnedScalar','__nativeOwnedStatus','__nativeOwnedPoint'];
 const optional=['__nativeOwnedHpObservation','__nativeOwnedConsumeHpFailure','__nativeOwnedGraphReadBatch','__nativeOwnedGraphCheck','__nativeOwnedGraphFailure'];
 for(const key of keys){
  if(typeof key!=='string'||(key!=='version'&&key!=='__nativeOwnedNumericAddress'&&!required.includes(key)&&!optional.includes(key)))throw Error('unexpected native owned provider member');
  const descriptor=__arkdpsProviderDescriptor(bundle,key);
  if(!descriptor||!__arkdpsProviderHasOwn(descriptor,'value')||descriptor.writable!==false||descriptor.configurable!==false)throw Error('native owned provider member is mutable');
 }
 const version=__arkdpsProviderDescriptor(bundle,'version'),numeric=__arkdpsProviderDescriptor(bundle,'__nativeOwnedNumericAddress');
 if(version?.value!==1||numeric?.value!==true)throw Error('invalid native owned provider bundle version');
 for(const key of required)if(typeof __arkdpsProviderDescriptor(bundle,key)?.value!=='function')throw Error('native owned provider member unavailable');
 for(const key of optional){const descriptor=__arkdpsProviderDescriptor(bundle,key);if(descriptor!==undefined&&typeof descriptor.value!=='function')throw Error('invalid optional native owned provider');}
 for(const pair of [['__nativeOwnedHpObservation','__nativeOwnedConsumeHpFailure'],['__nativeOwnedGraphCheck','__nativeOwnedGraphFailure']])if(__arkdpsProviderHasOwn(bundle,pair[0])!==__arkdpsProviderHasOwn(bundle,pair[1]))throw Error('incomplete native owned provider pair');
 return __arkdpsSealedNativeProvider=bundle;
}
function __arkdpsReplayBegin(record){
 if(__arkdpsReplay!==null)throw new Error('replay already active');
 if(record?.version!==1||typeof record.edge!=='string')
  throw new Error('invalid capture record');
 const checkpoint=record.checkpoint??0;
 if(!Number.isSafeInteger(checkpoint)||checkpoint<0)throw new Error('invalid record checkpoint');
 if(record.native_view===true){
  let native=__arkdpsSealedProvider();
  if(native===null){
  const lease=globalThis.__nativeOwnedLease,read=globalThis.__nativeOwnedRead,scalar=globalThis.__nativeOwnedScalar,
   status=globalThis.__nativeOwnedStatus,point=globalThis.__nativeOwnedPoint,numeric=globalThis.__nativeOwnedNumericAddress===true,hpObservation=globalThis.__nativeOwnedHpObservation,hpFailure=globalThis.__nativeOwnedConsumeHpFailure,graphCheck=globalThis.__nativeOwnedGraphCheck,graphFailure=globalThis.__nativeOwnedGraphFailure,graphBatch=globalThis.__nativeOwnedGraphReadBatch;
  if(typeof lease!=='function'||typeof read!=='function'||typeof scalar!=='function'||typeof status!=='function'||typeof point!=='function')
   throw new Error('native owned provider unavailable');
  if((hpObservation!==undefined||hpFailure!==undefined)&&(typeof hpObservation!=='function'||typeof hpFailure!=='function'))throw new Error('native HP observation provider unavailable');
  native=__arkdpsNativeProvider;
  if(!native||native.__nativeOwnedLease!==lease||native.__nativeOwnedRead!==read||native.__nativeOwnedScalar!==scalar||native.__nativeOwnedStatus!==status||native.__nativeOwnedPoint!==point||native.__nativeOwnedNumericAddress!==numeric||native.__nativeOwnedHpObservation!==hpObservation||native.__nativeOwnedConsumeHpFailure!==hpFailure||native.__nativeOwnedGraphCheck!==graphCheck||native.__nativeOwnedGraphFailure!==graphFailure||native.__nativeOwnedGraphReadBatch!==graphBatch)
   native=__arkdpsNativeProvider=Object.freeze({__nativeOwnedLease:lease,__nativeOwnedRead:read,__nativeOwnedScalar:scalar,__nativeOwnedStatus:status,__nativeOwnedPoint:point,__nativeOwnedNumericAddress:numeric,__nativeOwnedHpObservation:hpObservation,__nativeOwnedConsumeHpFailure:hpFailure,__nativeOwnedGraphCheck:graphCheck,__nativeOwnedGraphFailure:graphFailure,__nativeOwnedGraphReadBatch:graphBatch});
  }
  if(typeof record.ticket!=='string'||!/^\d+$/.test(record.ticket)||native.__nativeOwnedLease(record.ticket)!==true)throw new Error('native owned lease unavailable');
  __arkdpsReplay={record,native,role:record.edge,checkpoint,fieldId:0,structural:[],unreadable:0};return;
 }
 if(record.native_view!==undefined&&record.native_view!==false)throw new Error('invalid owned provider selection');
 if(!Array.isArray(record.segments))throw new Error('invalid capture segments');
 const segments=record.segments.map(segment=>{
  if(!Number.isInteger(segment.field_id)||segment.field_id<0||segment.field_id>65535||typeof segment.role!=='string'||!Number.isSafeInteger(segment.checkpoint)||segment.checkpoint<0||
     !(segment.bytes instanceof ArrayBuffer)||!['ok','unreadable','short','limit','guard_changed'].includes(segment.status))
   throw new Error('invalid owned segment');
  const address=__integer(segment.address);
  if(address<0n||address>0xffffffffffffffffn||address+BigInt(segment.bytes.byteLength)>0x10000000000000000n)throw new Error('invalid segment address');
  return {...segment,address,bytes:segment.bytes.slice(0)};
 });
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
 if(typeof __arkdpsColdAssignable==='function')return __arkdpsColdAssignable(base,klass);
 if(__arkdpsReplay.native)__arkdpsReplayGap('native replay class metadata not frozen');
 base=ptr(base);let current=ptr(klass);const seen=new Set();let matched=false;
 while(!current.isNull()){
  const key=current.toString();
  if(seen.has(key))__arkdpsReplayGap('captured class ancestry cycle');
  if(seen.size>=65536)__arkdpsReplayGap('captured class ancestry budget');
  seen.add(key);if(current.equals(base))matched=true;
  current=current.add(0x58).readPointer();
 }
 return matched;
}
function __arkdpsReplayPoint(role,body){
 if(__arkdpsReplay===null)return body();
 if(typeof role!=='string'||typeof body!=='function')__arkdpsReplayGap('invalid replay point');
 const replay=__arkdpsReplay,previousRole=replay.role,previousField=replay.fieldId;
 replay.role=role;replay.fieldId=null;
 try{return body();}finally{replay.role=previousRole;replay.fieldId=previousField;}
}
function __arkdpsReplayCheckpoint(role,checkpoint,body){
 if(__arkdpsReplay===null)return body();
 if(!Number.isSafeInteger(checkpoint)||checkpoint<0)__arkdpsReplayGap('invalid parent checkpoint');
 const replay=__arkdpsReplay,previousRole=replay.role,previousField=replay.fieldId,previousCheckpoint=replay.checkpoint;
 replay.role=role;replay.fieldId=null;replay.checkpoint=checkpoint;
 try{return body();}finally{replay.role=previousRole;replay.fieldId=previousField;replay.checkpoint=previousCheckpoint;}
}
function __arkdpsReplayWitness(role,body){
 if(__arkdpsReplay===null)return body();
 const replay=__arkdpsReplay,previousRole=replay.role,previousField=replay.fieldId,previousCheckpoint=replay.checkpoint,checkpoint=replay.record.checkpoint??0;
 let fieldId;
 if(replay.native){
  const packed=__arkdpsNativePoint(role,checkpoint);
  if((packed>>>24)!==1)__arkdpsReplayGap('missing or ambiguous witness '+role,{role,checkpoint,field_id:-1});
  const status=(packed>>>16)&255;if(status)__arkdpsNativeReadFailure(status);
  fieldId=packed&65535;
 }else{
  const ids=replay.points.get(role+'\0'+checkpoint)?.ids??new Set();
  if(ids.size!==1)__arkdpsReplayGap('missing or ambiguous witness '+role,{role,checkpoint,field_id:-1});
  fieldId=[...ids][0];
 }
 replay.role=role;replay.fieldId=fieldId;replay.checkpoint=checkpoint;
 try{return body();}finally{replay.role=previousRole;replay.fieldId=previousField;replay.checkpoint=previousCheckpoint;}
}
function __arkdpsReplayOptional(role,read){
 if(__arkdpsReplay===null)return {available:true,value:read()};
 const replay=__arkdpsReplay,checkpoint=replay.record.checkpoint??0;
 if(replay.native){
  const packed=__arkdpsNativePoint(role,checkpoint),status=(packed>>>16)&255;
  if((packed>>>24)!==1)__arkdpsReplayGap('missing or ambiguous optional capture field '+role,{role,checkpoint,field_id:-1});
  if(status){if(__arkdpsNativeStructuralStatus(status))__arkdpsReplayGap('invalid optional native capture field '+role,{role,checkpoint,field_id:-1});replay.unreadable++;return {available:false,value:null};}
  return {available:true,value:__arkdpsReplayWitness(role,read)};
 }
 const states=[...(replay.points.get(role+'\0'+checkpoint)?.states.values()??[])];
 if(states.length!==1)__arkdpsReplayGap('missing optional capture field '+role,{role,checkpoint,field_id:-1});
 if(states[0].status!=='ok'){
  if(__arkdpsStructuralCaptureStatus(states[0].status))__arkdpsReplayGap('invalid optional capture field '+role,{role,checkpoint,field_id:-1});
  replay.unreadable++;return {available:false,value:null};
 }
 return {available:true,value:__arkdpsReplayWitness(role,read)};
}
function __arkdpsReplayEnd(compact=false){
 if(__arkdpsReplay===null)throw new Error('no replay active');
 if(__arkdpsReplay.native){
  const replay=__arkdpsReplay;
  try{if(replay.native.__nativeOwnedLease(replay.record.ticket)!==true)replay.structural.push('native owned lease ended');
   const status=replay.native.__nativeOwnedStatus();if(!Number.isInteger(status)||status<0||status>10||__arkdpsNativeStructuralStatus(status))replay.structural.push('native owned structural status '+status);
  }catch(error){replay.structural.push('native owned provider failed at end');}
 }
 const replay=__arkdpsReplay,complete=replay.structural.length===0;
 __arkdpsReplay=null;
 if(compact===true)return complete;
 return Object.freeze({complete,structural_gaps:Object.freeze([...replay.structural]),unreadable_reads:replay.unreadable});
}
// Failure-only, bounded labels. Raw reasons never cross the native transport.
function __arkdpsReplayDiagnostic(replay){
 const reasons=replay.structural.slice(0,16);
 const patterns=[/unverified cold metadata/,/metadata not frozen|class lineage not frozen|frozen class parent/,/cold class identity/,/witness/,/optional capture field/,/parent checkpoint/,/missing captured point/,/missing captured read/,/conflicting|ambiguous captured/,/native owned structural/,/invalid.*(?:size|address|point|bytes)/,/NativeFunction|native write|native allocation/,/mode.*epoch/,/lease ended/,/provider failed/,/ancestry|class hierarchy/,/cold.*binding/,/argument/,/health/];
 const codes=reasons.map(reason=>{const i=patterns.findIndex(p=>p.test(reason));return i<0?0:i+1;});
 const size=reasons.map(r=>/^unverified cold metadata [^ :]+:([0-9]+)$/.exec(r)).find(Boolean);
 return {codes:codes.length?codes:[30],count:Math.max(1,reasons.length),role:replay.gapRole||replay.role,
  checkpoint:replay.gapCheckpoint??replay.checkpoint,field_id:(Object.hasOwn(replay,'gapField')?replay.gapField:replay.fieldId)??-1,
  metadata_read_size:size&&Number(size[1])<=1048576?Number(size[1]):0,frames:replay.gapFrames||[]};
}
function __arkdpsReplayGap(reason,point){
 const error=new Error(reason),replay=__arkdpsReplay;
 if(replay!==null){
  if(replay.structural.length===0){
   replay.gapRole=point?.role??replay.role;replay.gapCheckpoint=point?.checkpoint??replay.checkpoint;replay.gapField=point?.field_id??replay.fieldId;
   replay.gapFrames=[];
   const stack=typeof error.stack==='string'?error.stack:'';
   const pattern=/(event-probe\.js|native-bridge\.js):([0-9]+)(?::[0-9]+)?/g;
   let match;while(replay.gapFrames.length<3&&(match=pattern.exec(stack))!==null){
    const line=Number(match[2]);if(line>0&&line<=999999)replay.gapFrames.push([match[1]==='event-probe.js'?1:2,line]);
   }
  }
  if(replay.structural.length<16)replay.structural.push(reason);
 }
 throw error;
}
function __arkdpsNativeStructuralStatus(status){return [1,2,3,9,10].includes(status);}
function __arkdpsNativeReadFailure(status){
 if(!Number.isInteger(status)||status<1||status>10||__arkdpsNativeStructuralStatus(status))__arkdpsReplayGap('native owned structural failure '+status);
 __arkdpsReplay.unreadable++;throw new Error('native owned unreadable '+status);
}
function __arkdpsNativeCall(name,a,b,c,d,e){
 // Preserve the native ABI's exact argc without allocating a rest array for
 // every scalar/read. Provider selection remains pinned by ReplayBegin.
 try{
  const native=__arkdpsReplay.native;
  switch(name){
   case '__nativeOwnedStatus':return native.__nativeOwnedStatus();
   case '__nativeOwnedHpObservation':return c===undefined?native.__nativeOwnedHpObservation(a,b):native.__nativeOwnedHpObservation(a,b,c);
   case '__nativeOwnedConsumeHpFailure':return native.__nativeOwnedConsumeHpFailure(a);
   case '__nativeOwnedGraphFailure':return native.__nativeOwnedGraphFailure();
   case '__nativeOwnedGraphCheck':return d===undefined?native.__nativeOwnedGraphCheck(a,b,c):native.__nativeOwnedGraphCheck(a,b,c,d);
   case '__nativeOwnedPoint':return native.__nativeOwnedPoint(a,b);
   case '__nativeOwnedRead':return native.__nativeOwnedRead(a,b,c,d,e);
   case '__nativeOwnedScalar':return native.__nativeOwnedScalar(a,b,c,d,e);
   case '__nativeOwnedGraphReadBatch':return native.__nativeOwnedGraphReadBatch(a,b,c,d);
  }
 }catch(error){__arkdpsReplayGap('native owned provider failed '+name);}
 __arkdpsReplayGap('invalid native owned operation '+name);
}
function __arkdpsNativePoint(role,checkpoint){
 const packed=__arkdpsNativeCall('__nativeOwnedPoint',role,checkpoint);
 if(!Number.isSafeInteger(packed)||packed<0||packed>0xffffffff)__arkdpsReplayGap('invalid native owned point');
 const count=packed>>>24,status=(packed>>>16)&255,id=packed&65535;
 if(count>2||status>10)__arkdpsReplayGap('invalid native owned point shape');
 return packed;
}
function __arkdpsNativeAddress(address){
 // Only the ABI parameter changes representation. Pointer values and wide
 // addresses remain full uint64 BigInts; legacy providers never see Numbers.
 return __arkdpsReplay.native.__nativeOwnedNumericAddress&&typeof address==='bigint'&&address>=0n&&address<=9007199254740991n?Number(address):address;
}
// Read geometry comes from the same immutable compiled capture contract;
// there is no independent HP read-plan registry. Values belong to this lease.
const __arkdpsHpObservationGeometry=new WeakMap(),__arkdpsPreparedHpObservations=new WeakMap();
class __ArkDpsHpObservation {
 #replay;#packet;#geometry;#root;#attributes=null;
 constructor(replay,packet,geometry,receiver,root){this.#replay=replay;this.#packet=packet;this.#geometry=geometry;this.#root=root;Object.freeze(this);}
 #active(){if(this.#replay!==__arkdpsReplay)__arkdpsReplayGap('HP observation lease ended');}
 #object(receiver){this.#active();let root;try{root=__integer(receiver);}catch(error){__arkdpsReplayGap('invalid HP observation receiver');}if(root!==this.#root)__arkdpsReplayGap('HP observation receiver changed');}
 #fact(name){
  const field=this.#geometry.names[name];
  if(!field){__arkdpsNativeCall('__nativeOwnedConsumeHpFailure',-1);__arkdpsReplayGap('missing HP observation descriptor');}
  const status=this.#packet[field.at];
  if(!Number.isInteger(status)||status<0||(status>10&&status!==65535))__arkdpsReplayGap('invalid HP observation status');
  if(status!==0)return __arkdpsReplayCheckpoint(field.role,this.#replay.record.checkpoint??0,()=>{
   __arkdpsNativeCall('__nativeOwnedConsumeHpFailure',field.index);__arkdpsNativeReadFailure(__arkdpsNativeCall('__nativeOwnedStatus'));
  });
  for(let i=0;i<field.kinds.length;i++){
   const value=this.#packet[field.at+1+i],kind=field.kinds[i];
   if(kind==='u8'||kind==='u32'){if(!Number.isInteger(value)||value<0||value>(kind==='u8'?255:4294967295))__arkdpsReplayGap('invalid HP observation integer');}
   else if(kind==='u64'||kind==='i64'){if(typeof value!=='bigint'||value<(kind==='u64'?0n:-0x8000000000000000n)||value>(kind==='u64'?0xffffffffffffffffn:0x7fffffffffffffffn))__arkdpsReplayGap('invalid HP observation bigint');}
   else if(typeof value!=='string'||value.length>field.max_units)__arkdpsReplayGap('invalid HP observation string');
  }
  return field.at+1;
 }
 identity(receiver){this.#object(receiver);const at=this.#fact('hp.identity');return [this.#packet[at],this.#packet[at+1]];}
 born(receiver){this.#object(receiver);return this.#packet[this.#fact('hp.born')];}
 id(receiver){this.#object(receiver);return this.#packet[this.#fact('hp.id')];}
 hp(receiver){this.#object(receiver);return this.#packet[this.#fact('hp.before')];}
 clock(){this.#active();const open=this.#fact('clock_open'),close=this.#fact('clock_close');return [this.#packet[open],this.#packet[open+1],this.#packet[close]];}
 closing(receiver){this.#object(receiver);const at=this.#fact('hp.closing');return [this.#packet[at],this.#packet[at+1]];}
 trapClass(receiver){this.#object(receiver);return __arkdpsReplayCheckpoint('body',this.#replay.record.checkpoint??0,()=>ptr(receiver).readPointer().value);}
 attributes(receiver){this.#object(receiver);return this.#attributes??(this.#attributes=Object.freeze({}));}
 attributeCount(handle){this.#active();if(handle!==this.#attributes||handle===null)__arkdpsReplayGap('foreign HP attribute observation');return this.#packet[this.#fact('attributes.count')];}
 attributeWords(handle){this.#active();if(handle!==this.#attributes||handle===null)__arkdpsReplayGap('foreign HP attribute observation');const at=this.#fact('attributes.slot0');return [this.#packet[at],this.#packet[at+1],this.#packet[at+2]];}
}
function __arkdpsCreateHpObservation(contract,receiver,prepared){
 const replay=__arkdpsReplay;if(!replay?.native)__arkdpsReplayGap('HP observation requires native replay');
 const native=replay.native;
 if(native.__nativeOwnedHpObservation===undefined&&native.__nativeOwnedConsumeHpFailure===undefined){if(prepared!==undefined)__arkdpsReplayGap('prepared HP provider unavailable');return null;}
 if(typeof native.__nativeOwnedHpObservation!=='function'||typeof native.__nativeOwnedConsumeHpFailure!=='function'||!contract||typeof contract!=='object')__arkdpsReplayGap('invalid HP observation provider');
 let layouts=__arkdpsHpObservationGeometry.get(contract);
 if(!layouts){
  if(!Array.isArray(contract.fields))__arkdpsReplayGap('missing HP observation contract');
  const fields=contract.fields.filter(field=>field.phase==='entry'||field.phase==='leave');
  if(fields.every(field=>field.observation===undefined))return null;
  layouts=Object.create(null);
  for(const edge of ['entry','leave']){
   const names=Object.create(null);let at=5,index=0;
   for(const field of fields){if(field.phase!==edge)continue;const descriptor=field.observation;
    if(!descriptor||descriptor.name!==field.name||Object.hasOwn(names,descriptor.name)||!Array.isArray(descriptor.values)||!descriptor.values.length||descriptor.values.length>4)__arkdpsReplayGap('invalid HP observation descriptor');
    const kinds=[];
    for(const codec of descriptor.values){
     const width=codec.kind==='u8'?1:codec.kind==='u32'?4:8;
     if(!['u8','u32','u64','i64','utf16'].includes(codec.kind)||!Number.isInteger(codec.offset)||codec.offset<0||
       (codec.kind==='utf16'?(field.kind!=='utf16'||codec.offset!==0||descriptor.values.length!==1||!Number.isInteger(field.max_units)||field.max_units<1||field.max_units>200):(field.kind!=='bytes'||codec.offset+width>field.bytes)))__arkdpsReplayGap('invalid HP observation codec');
     kinds.push(codec.kind);
    }
    names[descriptor.name]=Object.freeze({index:index++,at,role:field.role,max_units:field.max_units,kinds:Object.freeze(kinds)});at+=1+kinds.length;
   }
   if(index>16)__arkdpsReplayGap('HP observation field budget');
   layouts[edge]=Object.freeze({names:Object.freeze(names),count:index,length:at});
  }
  layouts=Object.freeze(layouts);__arkdpsHpObservationGeometry.set(contract,layouts);
 }
 const geometry=layouts[replay.record.edge];if(!geometry)__arkdpsReplayGap('invalid HP observation phase');
 let root,hook;try{root=__integer(receiver);hook=__integer(contract.address);}catch(error){__arkdpsReplayGap('invalid HP observation receiver');}
 if(root<=0n||root>0xffffffffffffffffn||hook<=0n||hook>0xffffffffffffffffn)__arkdpsReplayGap('invalid HP observation receiver');
 const packet=__arkdpsNativeCall('__nativeOwnedHpObservation',__arkdpsNativeAddress(hook),__arkdpsNativeAddress(root),prepared);
 if(packet===undefined)__arkdpsNativeReadFailure(__arkdpsNativeCall('__nativeOwnedStatus'));
 if(prepared!==undefined&&packet!==prepared)__arkdpsReplayGap('prepared HP packet identity changed');
 if(!Array.isArray(packet)||packet.length!==geometry.length||packet[0]!==1||packet[1]!==geometry.count||packet[2]!== (replay.record.edge==='entry'?0:1)||packet[3]!== (replay.record.checkpoint??0)||packet[4]!==replay.record.facts?.mode_epoch)__arkdpsReplayGap('invalid HP observation packet');
 try{Object.freeze(packet);}catch(error){__arkdpsReplayGap('mutable HP observation packet');}
 const io=new __ArkDpsHpObservation(replay,packet,geometry,receiver,root);
 if(prepared!==undefined)__arkdpsPreparedHpObservations.set(io,{replay,contract,root});
 return io;
}
function __arkdpsOwnedHpObservation(contract,receiver){return __arkdpsCreateHpObservation(contract,receiver,undefined);}
function __arkdpsPreparedHpObservation(contract,receiver,packet){
 if(packet===undefined)__arkdpsReplayGap('missing prepared HP packet');
 const io=__arkdpsCreateHpObservation(contract,receiver,packet);
 if(io===null)__arkdpsReplayGap('unsupported prepared HP contract');return io;
}
function __arkdpsIsPreparedHpObservation(io,contract,receiver){
 const state=__arkdpsPreparedHpObservations.get(io);
 if(!state||state.replay!==__arkdpsReplay||state.contract!==contract)return false;
 if(receiver!==undefined){try{if(__integer(receiver)!==state.root)return false;}catch(error){return false;}}
 return true;
}
class __ArkDpsOwnedBatch {
 #view;#state;#mask;#head;
 constructor(bytes,state,mask){
  if(!(bytes instanceof ArrayBuffer)||bytes.byteLength<16)__arkdpsReplayGap('invalid owned batch bytes');
  const view=new DataView(bytes),head=16+state.tiers.length*16;
  if(view.getUint32(0,true)!==1||view.getUint32(4,true)!==state.tiers.length||view.getUint32(8,true)!==mask||
     head>bytes.byteLength||view.getUint32(12,true)!==bytes.byteLength-head||bytes.byteLength>16384)__arkdpsReplayGap('invalid owned batch header');
  this.#view=view;this.#state=state;this.#mask=mask;this.#head=head;Object.freeze(this);
 }
 slot(index){
  const state=this.#state,view=this.#view;
  if(!Number.isInteger(index)||index<0||index>=state.tiers.length)__arkdpsReplayGap('invalid owned batch slot');
  const at=16+index*16,status=view.getUint16(at,true),kind=view.getUint16(at+2,true),offset=view.getUint32(at+4,true),size=view.getUint32(at+8,true);
  if(!(this.#mask&state.tiers[index])||status===65535)__arkdpsReplayGap('unrequested owned batch slot');
  if(kind!==state.kinds[index]||view.getUint32(at+12,true)!==0||status>10)__arkdpsReplayGap('invalid owned batch slot header');
  if(status!==0){if(offset!==0||size!==0)__arkdpsReplayGap('failed owned batch slot has bytes');__arkdpsNativeReadFailure(status);}
  if(offset<this.#head||offset>view.byteLength||size>view.byteLength-offset)__arkdpsReplayGap('invalid owned batch slot range');
  if(kind===0&&size!==state.sizes[index])__arkdpsReplayGap('invalid owned batch slot size');
  if(kind===1){
   if(size<12)__arkdpsReplayGap('invalid owned batch string header');
   const units=view.getInt32(offset+8,true);
   if(units<0||units>state.sizes[index]||size!==12+units*2)__arkdpsReplayGap('invalid owned batch string size');
  }
  return new DataView(view.buffer,offset,size);
 }
}
const __arkdpsGraphReadGeometry=new WeakMap();
function __arkdpsOwnedGraphReadBatch(kind,graph,chunk){
 const replay=__arkdpsReplay;
 if(!replay?.native)__arkdpsReplayGap('owned graph batch requires native replay');
 const execute=replay.native.__nativeOwnedGraphReadBatch;
 if(execute===undefined)return null;
 if(typeof execute!=='function'||(kind!=='scope'&&kind!=='publication')||!graph||typeof graph!=='object'||
    !Number.isSafeInteger(graph.revision)||graph.revision<=0||!Number.isSafeInteger(graph.mode_epoch)||graph.mode_epoch<0||
    !Number.isInteger(chunk)||chunk<0)__arkdpsReplayGap('invalid owned graph batch');
 let geometry=__arkdpsGraphReadGeometry.get(graph);
 if(!geometry){
  const fields=kind==='scope'?graph.fields:graph.chunks?.flatMap(part=>part.fields);
  if(!Array.isArray(fields)||!fields.length)__arkdpsReplayGap('invalid owned graph geometry');
  const chunks=[];
  // Unsupported optimization geometry keeps the original strict reader; it is
  // never an execution-error fallback and cannot suppress a native failure.
  if(fields.some(field=>!field||field.root!=='absolute'||!['bytes','utf16'].includes(field.kind)||
     (field.kind==='bytes'?(!Number.isInteger(field.bytes)||field.bytes<1||field.bytes>128):(!Number.isInteger(field.max_units)||field.max_units<1||field.max_units>200))))return null;
  for(let start=0;start<fields.length;start+=32){
   const part=fields.slice(start,start+32);
   if(part.some(field=>!field||!['bytes','utf16'].includes(field.kind)||
      (field.kind==='bytes'?(!Number.isInteger(field.bytes)||field.bytes<1||field.bytes>128):(!Number.isInteger(field.max_units)||field.max_units<1||field.max_units>200))))__arkdpsReplayGap('invalid owned graph field');
   chunks.push(Object.freeze({tiers:Object.freeze(part.map(()=>1)),kinds:Object.freeze(part.map(f=>f.kind==='utf16'?1:0)),sizes:Object.freeze(part.map(f=>f.kind==='utf16'?f.max_units:f.bytes))}));
  }
  geometry=Object.freeze({kind,chunks:Object.freeze(chunks),revision:graph.revision,mode:graph.mode_epoch});__arkdpsGraphReadGeometry.set(graph,geometry);
 }
 if(geometry.kind!==kind||geometry.revision!==graph.revision||geometry.mode!==graph.mode_epoch||chunk>=geometry.chunks.length)__arkdpsReplayGap('owned graph geometry changed');
 const bytes=__arkdpsNativeCall('__nativeOwnedGraphReadBatch',kind,geometry.revision,geometry.mode,chunk);
 if(bytes===undefined)__arkdpsNativeReadFailure(__arkdpsNativeCall('__nativeOwnedStatus'));
 return new __ArkDpsOwnedBatch(bytes,geometry.chunks[chunk],1);
}
const __arkdpsGraphChecks=new WeakMap(),__arkdpsGraphProofs=new WeakMap();
function __arkdpsVerifiedGraphProof(proof,kind,graph){
 const state=__arkdpsGraphProofs.get(proof);
 return state!==undefined&&state.format===0&&state.replay===__arkdpsReplay&&state.kind===kind&&state.graph===graph;
}
class __ArkDpsVerifiedGraph {
 constructor(){Object.freeze(this);}
 slot(index){
  const state=__arkdpsGraphProofs.get(this);
  if(!state||state.format!==0||state.replay!==__arkdpsReplay)__arkdpsReplayGap('owned graph proof lease ended');
  const projected=state.geometry.indices[index];
  if(!Number.isInteger(index)||index<0||projected===undefined||projected<0)__arkdpsReplayGap('unprojected owned graph field');
  return state.batch.slot(projected);
 }
}
function __arkdpsOwnedGraphCheck(kind,graph){
 const replay=__arkdpsReplay;
 if(!replay?.native)__arkdpsReplayGap('owned graph check requires native replay');
 const native=replay.native;
 if(native.__nativeOwnedGraphCheck===undefined&&native.__nativeOwnedGraphFailure===undefined)return null;
 if(typeof native.__nativeOwnedGraphCheck!=='function'||typeof native.__nativeOwnedGraphFailure!=='function'||
    (kind!=='scope'&&kind!=='publication')||!graph||typeof graph!=='object'||
    !Number.isSafeInteger(graph.revision)||graph.revision<=0||!Number.isSafeInteger(graph.mode_epoch)||graph.mode_epoch<0)__arkdpsReplayGap('invalid owned graph check');
 let geometry=__arkdpsGraphChecks.get(graph);
 if(!geometry){
  const fields=kind==='scope'?graph.fields:graph.chunks?.flatMap(part=>part.fields);
  if(!Array.isArray(fields))__arkdpsReplayGap('invalid owned graph comparison fields');
  if(!fields.length)return null; // Legal normal-stage publication has no membership guards.
  if(fields.some(f=>!f||typeof f.projection!=='boolean'||f.comparison===null||(!f.projection&&f.comparison===undefined)||
    f.root!=='absolute'||!['bytes','utf16'].includes(f.kind)||(f.kind==='bytes'?f.bytes<1||f.bytes>128:f.max_units<1||f.max_units>200)))return null;
  const projected=[],indices=[];
  for(const field of fields){indices.push(field.projection?projected.length:-1);if(field.projection)projected.push(field);}
  if(projected.length>32)return null;
  geometry=Object.freeze({kind,fields:Object.freeze([...fields]),indices:Object.freeze(indices),revision:graph.revision,mode:graph.mode_epoch,
   tiers:Object.freeze(projected.map(()=>1)),kinds:Object.freeze(projected.map(f=>f.kind==='utf16'?1:0)),sizes:Object.freeze(projected.map(f=>f.kind==='utf16'?f.max_units:f.bytes))});
  __arkdpsGraphChecks.set(graph,geometry);
 }
 if(geometry.kind!==kind||geometry.revision!==graph.revision||geometry.mode!==graph.mode_epoch)__arkdpsReplayGap('owned graph comparison geometry changed');
 const bytes=__arkdpsNativeCall('__nativeOwnedGraphCheck',kind,geometry.revision,geometry.mode);
 if(bytes===undefined){
  const status=__arkdpsNativeCall('__nativeOwnedStatus'),index=__arkdpsNativeCall('__nativeOwnedGraphFailure');
  if(Number.isInteger(index)&&index>=0&&index<geometry.fields.length)return __arkdpsReplayCheckpoint(geometry.fields[index].role,replay.record.checkpoint??0,()=>__arkdpsNativeReadFailure(status));
  __arkdpsNativeReadFailure(status);
 }
 const batch=new __ArkDpsOwnedBatch(bytes,geometry,1);
 const proof=new __ArkDpsVerifiedGraph();__arkdpsGraphProofs.set(proof,{format:0,replay,kind,graph,geometry,batch});return proof;
}
// A primitive projection certifies the same complete GraphCheck; it is not a
// separately registered read plan. Only the successful native factory mints it.
const __arkdpsPrimitiveGeometry=new WeakMap();
class __ArkDpsGraphPrimitives {
 constructor(){Object.freeze(this);}
 values(index){
  const state=__arkdpsGraphProofs.get(this);
  if(!state||state.format!==1||state.replay!==__arkdpsReplay)__arkdpsReplayGap('owned primitive graph lease ended');
  const offset=state.flat.offsets[index];
  if(!Number.isInteger(index)||index<0||offset===undefined||offset<0)__arkdpsReplayGap('unprojected primitive graph field');
  return Object.freeze(state.flat.packet.slice(offset,offset+state.geometry.codecs[index].length));
 }
 flat(){
  const state=__arkdpsGraphProofs.get(this);
  if(!state||state.format!==1||state.replay!==__arkdpsReplay)__arkdpsReplayGap('owned primitive graph lease ended');
  return state.flat;
 }
}
function __arkdpsVerifiedGraphPrimitives(io,kind,graph){
 const state=__arkdpsGraphProofs.get(io);
 return state!==undefined&&state.format===1&&state.replay===__arkdpsReplay&&state.kind===kind&&state.graph===graph;
}
function __arkdpsOwnedGraphPrimitives(kind,graph){
 const replay=__arkdpsReplay;
 if(!replay?.native)__arkdpsReplayGap('owned primitive graph requires native replay');
 const native=replay.native;
 if(native.__nativeOwnedGraphCheck===undefined&&native.__nativeOwnedGraphFailure===undefined)return null;
 if(typeof native.__nativeOwnedGraphCheck!=='function'||typeof native.__nativeOwnedGraphFailure!=='function'||kind!=='scope'||!graph||typeof graph!=='object'||
    !Number.isSafeInteger(graph.revision)||graph.revision<=0||!Number.isSafeInteger(graph.mode_epoch)||graph.mode_epoch<0)__arkdpsReplayGap('invalid primitive graph');
 let geometry=__arkdpsPrimitiveGeometry.get(graph);
 if(!geometry){
  const fields=graph.fields;if(!Array.isArray(fields)||fields.length===0)return null;
  const codecs=[];
  for(const field of fields){
   if(!field||typeof field.projection!=='boolean'||field.comparison===null||(!field.projection&&field.comparison===undefined)||field.root!=='absolute'||!['bytes','utf16'].includes(field.kind)||(field.kind==='bytes'? !Number.isInteger(field.bytes)||field.bytes<1||field.bytes>128:!Number.isInteger(field.max_units)||field.max_units<1||field.max_units>200))return null;
   if(!field.projection){codecs.push(null);continue;}
   const values=field.observation?.values;
   if(field.kind!=='bytes'||!Number.isInteger(field.bytes)||field.bytes<1||field.bytes>128||!Array.isArray(values)||values.length<1||values.length>4)return null;
   const owned=[];for(const value of values){
    const width=value?.kind==='u32'||value?.kind==='i32'?4:8;
    if(!value||!['u32','i32','u64','i64'].includes(value.kind)||!Number.isInteger(value.offset)||value.offset<0||value.offset>field.bytes-width)return null;
    owned.push(Object.freeze({kind:value.kind,offset:value.offset}));
   }codecs.push(Object.freeze(owned));
  }
  if(codecs.filter(value=>value!==null).length>32)return null;
  let offset=4;const offsets=codecs.map(values=>{if(values===null)return -1;const start=offset+2;offset=start+values.length;return start;});
  geometry=Object.freeze({fields:Object.freeze([...fields]),codecs:Object.freeze(codecs),offsets:Object.freeze(offsets),revision:graph.revision,mode:graph.mode_epoch});__arkdpsPrimitiveGeometry.set(graph,geometry);
 }
 if(geometry.revision!==graph.revision||geometry.mode!==graph.mode_epoch)__arkdpsReplayGap('primitive graph geometry changed');
 const packet=__arkdpsNativeCall('__nativeOwnedGraphCheck',kind,geometry.revision,geometry.mode,1);
 if(packet===undefined){
  const status=__arkdpsNativeCall('__nativeOwnedStatus'),index=__arkdpsNativeCall('__nativeOwnedGraphFailure');
  if(Number.isInteger(index)&&index>=0&&index<geometry.fields.length)return __arkdpsReplayCheckpoint(geometry.fields[index].role,replay.record.checkpoint??0,()=>__arkdpsNativeReadFailure(status));
  __arkdpsNativeReadFailure(status);
 }
 if(!Array.isArray(packet)||packet[0]!==1||packet[1]!==geometry.fields.length||packet[2]!==(replay.record.checkpoint??0)||packet[3]!==geometry.mode)__arkdpsReplayGap('invalid primitive graph packet');
 let at=4;
 for(let index=0;index<geometry.codecs.length;index++){
  const codecs=geometry.codecs[index];
  if(codecs===null)continue;
  if(packet[at++]!==index||packet[at++]!==codecs.length)__arkdpsReplayGap('invalid primitive graph value count');
  for(const codec of codecs){
   const value=packet[at++];
   if(codec.kind==='u32'||codec.kind==='i32'){
    if(typeof value!=='number'||!Number.isInteger(value)||value<(codec.kind==='u32'?0:-2147483648)||value>(codec.kind==='u32'?4294967295:2147483647))__arkdpsReplayGap('invalid primitive graph number');
   }else if(typeof value!=='bigint'||value<(codec.kind==='u64'?0n:-9223372036854775808n)||value>(codec.kind==='u64'?18446744073709551615n:9223372036854775807n))__arkdpsReplayGap('invalid primitive graph integer');
  }
 }
 if(at!==packet.length)__arkdpsReplayGap('invalid primitive graph trailing values');
 Object.freeze(packet);
 const flat=Object.freeze({packet,offsets:geometry.offsets});
 const io=new __ArkDpsGraphPrimitives();__arkdpsGraphProofs.set(io,{format:1,replay,kind,graph,geometry,flat});return io;
}
function __arkdpsOwnedScalar(address,kind){
 const replay=__arkdpsReplay;
 if(!Number.isInteger(kind)||kind<0||kind>6)__arkdpsReplayGap('invalid native scalar kind');
 const value=__arkdpsNativeCall('__nativeOwnedScalar',replay.role,replay.checkpoint,replay.fieldId===null?-1:replay.fieldId,__arkdpsNativeAddress(address),kind);
 if(value===undefined)__arkdpsNativeReadFailure(__arkdpsNativeCall('__nativeOwnedStatus'));
 if(kind<4){
  const lo=kind===3?-2147483648:0,hi=kind===0?255:kind===1?65535:kind===2?4294967295:2147483647;
  if(!Number.isInteger(value)||value<lo||value>hi)__arkdpsReplayGap('invalid native scalar integer');
 }
 else if(kind===4||kind===5){if(typeof value!=='bigint'||value<(kind===4?0n:-0x8000000000000000n)||value>(kind===4?0xffffffffffffffffn:0x7fffffffffffffffn))__arkdpsReplayGap('invalid native scalar bigint');}
 else if(kind!==6||typeof value!=='number')__arkdpsReplayGap('invalid native scalar float');
 return value;
}
function __arkdpsOwnedRead(address,size){
 if(!Number.isSafeInteger(size)||size<0||size>1048576){
  if(__arkdpsReplay!==null)__arkdpsReplayGap('invalid captured read size');
  throw new Error('invalid captured read size');
 }
 const replay=__arkdpsReplay;
 if(replay===null)return __read(address,size);
 if(replay.native){
  try{address=__integer(address);}catch(error){__arkdpsReplayGap('invalid native captured address');}
  if(address<0n||address>0xffffffffffffffffn||address+BigInt(size)>0x10000000000000000n)__arkdpsReplayGap('invalid native captured address');
  const bytes=__arkdpsNativeCall('__nativeOwnedRead',replay.role,replay.checkpoint,replay.fieldId===null?-1:replay.fieldId,__arkdpsNativeAddress(address),size);
  if(bytes===undefined)__arkdpsNativeReadFailure(__arkdpsNativeCall('__nativeOwnedStatus'));
  if(!(bytes instanceof ArrayBuffer)||bytes.byteLength!==size)__arkdpsReplayGap('invalid native owned bytes');
  return bytes;
 }
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
 constructor(value){this.value=__arkdpsUnsigned64(value);}
 add(value){return new NativePointer(this.value+__integer(value));}
 sub(value){return new NativePointer(this.value-__integer(value));}
 equals(value){return this.value===ptr(value).value;}
 isNull(){return this.value===0n;}
 toString(){return '0x'+this.value.toString(16);}
 toInt32(){return Number(BigInt.asIntN(32,this.value));}
 readByteArray(size){return __arkdpsOwnedRead(this.value,size);}
 __view(size){return new DataView(this.readByteArray(size));}
 readPointer(){return new NativePointer((__arkdpsReplay?.native?__arkdpsOwnedScalar(this.value,4):this.__view(8).getBigUint64(0,true)));}
 readU8(){return (__arkdpsReplay?.native?__arkdpsOwnedScalar(this.value,0):this.__view(1).getUint8(0));}
 readU16(){return (__arkdpsReplay?.native?__arkdpsOwnedScalar(this.value,1):this.__view(2).getUint16(0,true));}
 readU32(){return (__arkdpsReplay?.native?__arkdpsOwnedScalar(this.value,2):this.__view(4).getUint32(0,true));}
 readS32(){return (__arkdpsReplay?.native?__arkdpsOwnedScalar(this.value,3):this.__view(4).getInt32(0,true));}
 readU64(){return new NativeInteger((__arkdpsReplay?.native?__arkdpsOwnedScalar(this.value,4):this.__view(8).getBigUint64(0,true)));}
 readS64(){return new NativeInteger((__arkdpsReplay?.native?__arkdpsOwnedScalar(this.value,5):this.__view(8).getBigInt64(0,true)));}
 readFloat(){return (__arkdpsReplay?.native?__arkdpsOwnedScalar(this.value,6):this.__view(4).getFloat32(0,true));}
 readCString(){return __arkdpsOwnedString(this.value,0,false);}
 readUtf16String(length){return __arkdpsOwnedString(this.value,length,true);}
 writeU64(value){if(__arkdpsReplay!==null)__arkdpsReplayGap('native write during replay');const b=new ArrayBuffer(8);new DataView(b).setBigUint64(0,__arkdpsUnsigned64(value),true);__write(this.value,b);return this;}
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
 const view=new DataView(bytes);
 // Small fully captured signatures dominate HP/lifecycle callbacks. Eager
 // owned pointers preserve ordinary Array descriptors without Proxy/WeakMaps.
 if(count<=3&&mask===allowed){
  const args=[];for(let i=0;i<count;i++)args.push(new NativePointer(view.getBigUint64(i*8,true)));
  return args;
 }
 const args=Array(count).fill(__deferredArgument);
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
