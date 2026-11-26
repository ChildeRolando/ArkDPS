'use strict';
// Small fixed-field interpreter for source-only tests. Native capture's own
// rereads, budgets and TLS ownership are tested independently in C++.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
function ownedFields(f,metadata){
 const c=f.context,P=f.ptr(0).constructor,names=['readByteArray','readPointer','readU8','readU16','readU32','readS32','readU64','readS64','readFloat','readUtf16String','readCString'];
 const old=Object.fromEntries(names.map(n=>[n,P.prototype[n]]));let replaying=false,late=0;
 c.ArrayBuffer=ArrayBuffer;c.NativePointer=P;c.NativeInteger=class{};c.__read=()=>{late++;throw Error('live read');};
 const bridge=fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8');vm.runInContext(bridge.slice(bridge.indexOf('function __integer('),bridge.indexOf('class NativeInteger')),c);
 c.fixtureMetadata=metadata;vm.runInContext("nativeCaptureMetadata=fixtureMetadata;nativeCaptureModeHistory.set(0,'all');",c);
 const parents=new Map();for(const value of Object.values(metadata.classes)){
  let k=f.ptr(value);for(let i=0;!k.isNull()&&i<12;i++){let p;try{p=old.readPointer.call(k.add(0x58));}catch{p=f.ptr(0);}const b=new ArrayBuffer(8);new DataView(b).setBigUint64(0,p.value,true);parents.set(k.add(0x58).toString()+':8',b);k=p;}
 }
 c.frozenRead=(p,n)=>{const b=parents.get(p.toString()+':'+n);if(!b)c.__arkdpsReplayGap('unfrozen metadata');return b.slice(0);};vm.runInContext('__arkdpsColdMetadataRead=frozenRead;',c);
 function raw(p,n){if(n===1)return Uint8Array.of(old.readU8.call(p)).buffer;if(n===2){const b=new ArrayBuffer(2);new DataView(b).setUint16(0,old.readU8.call(p)|(old.readU8.call(p.add(1))<<8),true);return b;}return old.readByteArray.call(p,n);}
 function captureFields(fields,args,entryRefs,parentRefs,checkpoint=0){const segments=[],states=[],counts=new Map();
  for(const field of fields){let attempted=null,size=0;const add=(p,b)=>segments.push({field_id:field.field_id,role:field.role,checkpoint,address:p.toString(),bytes:b,status:'ok',guard_id:field.guard_id});const copy=(p,n)=>{attempted=p;size=n;const b=raw(p,n);add(p,b);return b;};
   try{let p=field.root==='absolute'?f.ptr(field.absolute_address):field.root==='global_slot'?metadata.clock:(field.root==='parent_ref'?parentRefs:field.root==='entry_ref'?entryRefs:args)[field.root_index];if(!p)throw Error('missing root');
    for(const offset of field.pointer_offsets){copy(p.add(offset),8);p=old.readPointer.call(p.add(offset));}p=p.add(field.offset);
    if(field.kind==='utf16'){copy(p,8);assert.equal(old.readPointer.call(p).toString(),field.expected_class); copy(p.add(16),4);const n=old.readS32.call(p.add(16));assert(n>=0&&n<=field.max_units);const text=old.readUtf16String.call(p.add(20)),b=new ArrayBuffer(n*2),v=new DataView(b);for(let i=0;i<n;i++)v.setUint16(i*2,text.charCodeAt(i),true);add(p.add(20),b);}
    else if(field.kind==='utf16_array'){
     const count=counts.get(field.count_field_id);assert(Number.isInteger(count)&&count>=0&&count<=field.max_count);
     for(let i=0;i<count;i++){const slot=p.add(i*field.element_bytes),b=raw(slot,field.element_bytes);new DataView(b).setFloat32(8,old.readFloat.call(slot.add(8)),true);add(slot,b);const key=old.readPointer.call(slot.add(field.element_pointer_offset));copy(key,8);assert.equal(old.readPointer.call(key).toString(),field.expected_class);copy(key.add(16),4);const text=old.readUtf16String.call(key.add(20)),n=old.readS32.call(key.add(16));assert(n>=0&&n<=field.max_units);const t=new ArrayBuffer(n*2),v=new DataView(t);for(let j=0;j<n;j++)v.setUint16(j*2,text.charCodeAt(j),true);add(key.add(20),t);if(field.stop_utf16_literal&&text===field.stop_utf16_literal)break;}
    }else if(field.kind==='bytes'){copy(p,field.bytes);if(field.bytes===4)counts.set(field.field_id,old.readS32.call(p));}else throw Error('unsupported owned field kind '+field.kind);
    states.push({field_id:field.field_id,role:field.role,checkpoint,status:'ok'});
   }catch(error){states.push({field_id:field.field_id,role:field.role,checkpoint,status:'field_unreadable',...(attempted?{attempted_address:attempted.toString(),attempted_size:size}:{})});for(const s of segments)if(s.field_id===field.field_id)s.status='unreadable';}
  }return {segments,fields:states};
 }
 const view=(p,n)=>new DataView(c.__arkdpsOwnedRead(p.value,n));
 for(const[n,size,get]of [['readU8',1,'getUint8'],['readU16',2,'getUint16'],['readU32',4,'getUint32'],['readS32',4,'getInt32'],['readFloat',4,'getFloat32']])P.prototype[n]=function(){return replaying?view(this,size)[get](0,true):old[n].call(this);};
 P.prototype.readByteArray=function(n){return replaying?c.__arkdpsOwnedRead(this.value,n):old.readByteArray.call(this,n);};
 P.prototype.readPointer=function(){return replaying?f.ptr(view(this,8).getBigUint64(0,true)):old.readPointer.call(this);};
 for(const n of ['readU64','readS64'])P.prototype[n]=function(){if(!replaying)return old[n].call(this);const v=view(this,8)[n==='readS64'?'getBigInt64':'getBigUint64'](0,true);return{toString:()=>v.toString(),toNumber:()=>Number(v)};};
 P.prototype.readUtf16String=function(n){if(!replaying)return old.readUtf16String.call(this,n);const v=view(this,n*2);let text='';for(let i=0;i<n;i++)text+=String.fromCharCode(v.getUint16(i*2,true));return text;};
 P.prototype.readCString=function(){if(replaying){late++;throw Error('live metadata');}return old.readCString.call(this);};
 for(const[n,native]of Object.entries(f.metadataExports))f.metadataExports[n]=(...args)=>{if(replaying){if(n==='il2cpp_class_is_assignable_from')return c.__arkdpsCapturedAssignable(...args);late++;throw Error('NativeFunction in replay '+n);}return native(...args);};
 return {captureFields,raw,old,late:()=>late,replay(record,fn){c.fixtureRecord=record;c.fixtureCall=fn;replaying=true;try{return vm.runInContext('__arkdpsReplayBegin(fixtureRecord);try{__arkdpsReplayPoint("body",fixtureCall);}finally{globalThis.fixtureSummary=__arkdpsReplayEnd();}',c);}finally{replaying=false;}},restore(){for(const n of names)P.prototype[n]=old[n];}};
}
module.exports={ownedFields};
