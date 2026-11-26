'use strict';
// Offline source oracle: interpret the actual fixed cold plan over owned fixture
// memory, then forbid every live read while running the actual binding reducer.
// C++ capture ordering/budgets are independently exercised by native_capture_cold.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {world}=require('./native_pc_autochess_context_fixture.js');
const f=world(),P=f.ptr(0).constructor,c=f.context;
const methods=['readByteArray','readPointer','readU8','readU16','readU32','readS32','readU64','readS64','readFloat','readUtf16String','readCString'];
const original=Object.fromEntries(methods.map(name=>[name,P.prototype[name]]));
const metadata=new Map();let replaying=false,late=0;
// Freeze the explicit metadata reads used by the already verified setup layout.
for(const [name,size] of [['readPointer',8],['readU16',2],['readU32',4]])P.prototype[name]=function(){
 const value=original[name].call(this),b=new ArrayBuffer(size),v=new DataView(b);
 if(size===8)v.setBigUint64(0,BigInt(value.value),true);else if(size===2)v.setUint16(0,value,true);else v.setUint32(0,value,true);
 metadata.set(this.toString()+':'+size,b);return value;
};
const baseline=f.mark();assert.equal(baseline.native_autochess_context.status,'available');
for(const name of methods)P.prototype[name]=original[name];
c.ArrayBuffer=ArrayBuffer;c.NativePointer=P;c.NativeInteger=class{};c.__read=()=>{late++;throw Error('live read during replay');};
const bridge=fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8');
vm.runInContext(bridge.slice(bridge.indexOf('function __integer('),bridge.indexOf('class NativeInteger')),c);
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/cold_plan.js','utf8'),c);
const classes=Object.fromEntries([...f.ownedClasses.values()].map(x=>[x.name,x.klass.toString()]));
classes.BattleController=f.controllerClass.toString();classes.Character=f.character.toString();
vm.runInContext("nativeCaptureModeHistory.set(0,'all');",c);
c.metadata={cold_metadata_ready:true,classes,string_class:classes.String};
const plan=JSON.parse(vm.runInContext('JSON.stringify(__arkdpsBuildColdCapturePlan(metadata))',c));
// The fixture's AutoChess classes are installed after initial reflection; expose
// their setup-owned exact identities, never infer a name from a replay pointer.
c.frozenIdentity=(k,name,ns)=>{const value=f.ownedClasses.get(k.toString());return !!value&&value.name===name&&value.namespace===ns;};
c.frozenMetadata=(p,n)=>{const b=metadata.get(p.toString()+':'+n);if(!b)throw Error('metadata not frozen '+p+':'+n);return b.slice(0);};
vm.runInContext('__arkdpsColdClassIdentity=frozenIdentity;__arkdpsColdMetadataRead=frozenMetadata;',c);
function raw(p,n){
 if(n===1)return Uint8Array.of(original.readU8.call(p)).buffer;
 if(n===2){const b=new ArrayBuffer(2);new DataView(b).setUint16(0,original.readU8.call(p)|(original.readU8.call(p.add(1))<<8),true);return b;}
 return original.readByteArray.call(p,n);
}
function capture(){
 const segments=[],fields=[];let dynamic=32768;
 function segment(field,p,b){segments.push({field_id:field.field_id,role:field.role,checkpoint:0,address:p.toString(),bytes:b,status:'ok',guard_id:field.guard_id});}
 function bytes(field,p,n){if(n)segment(field,p,raw(p,n));}
 function pointer(field,p){bytes(field,p,8);return original.readPointer.call(p);}
 function string(field,p,max){bytes(field,p,8);bytes(field,p.add(16),4);const n=original.readS32.call(p.add(16));assert(n>=0&&n<=max);const text=original.readUtf16String.call(p.add(20));const b=new ArrayBuffer(n*2),v=new DataView(b);for(let i=0;i<n;i++)v.setUint16(i*2,text.charCodeAt(i),true);segment(field,p.add(20),b);}
 function node(index,parent,field){
  const n=plan.projections[index];let active=field;
  if(n.local_fail_boundary){active={...field,field_id:dynamic++,guard_id:0};fields.push({...active,status:'ok'});}
  try{
   if(n.parent_byte_offset!==undefined){bytes(active,parent.add(n.parent_byte_offset),1);if(original.readU8.call(parent.add(n.parent_byte_offset))!==n.parent_byte_value)return;}
   let p=parent;for(const offset of n.pointer_offsets)p=pointer(active,p.add(offset));p=p.add(n.offset);
   if(p.isNull()&&n.allow_null)return;
   if(n.gate){let k=pointer(active,p),matched=k.toString()===n.expected_class;
    if(n.gate==='ancestor')for(let i=0;!matched&&!k.isNull()&&i<n.ancestry_depth;i++){k=pointer(active,k.add(0x58));matched=k.toString()===n.expected_class;}
    if(!matched)return;
   }
   if(n.kind==='utf16'){string(active,p,n.max_units);return;}
   if(n.kind==='array'){
    bytes(active,p.add(n.count_offset),4);const count=original.readS32.call(p.add(n.count_offset)),items=pointer(active,p.add(n.items_offset));assert(count>=0&&count<=n.max_count);
    for(let i=0;i<count;i++){const slot=items.add(n.data_offset+i*n.element_bytes);bytes(active,slot,n.element_bytes);if(n.skip_negative_i32_offset>=0&&original.readS32.call(slot.add(n.skip_negative_i32_offset))<0)continue;for(let j=0;j<n.child_count;j++)node(n.child_begin+j,slot,active);}return;
   }
   bytes(active,p,n.bytes);for(let j=0;j<n.child_count;j++)node(n.child_begin+j,p,active);
  }catch(error){if(active===field)throw error;const state=fields.find(x=>x.field_id===active.field_id);state.status='field_unreadable';for(const s of segments)if(s.field_id===active.field_id)s.status='unreadable';}
 }
 for(const field of plan.fields){fields.push({...field,status:'ok'});node(field.projection_root,f.ptr(field.absolute_address),field);}
 const clock=vm.runInContext('clock',c);for(const [id,role]of [[100,'clock_open'],[101,'clock_close']]){const field={field_id:id,role,guard_id:0};bytes(field,clock,12);fields.push({...field,status:'ok'});}
 return {version:1,edge:'leave',checkpoint:0,segments,fields,facts:{cold_revision:1,mode_epoch:0,scope_mode_epoch:0}};
}
const view=(p,n)=>new DataView(c.ownedRead(p.toString(),n));
c.ownedRead=(p,n)=>c.__arkdpsOwnedRead(BigInt(p),n);
for(const[name,size,get]of [['readU8',1,'getUint8'],['readU16',2,'getUint16'],['readU32',4,'getUint32'],['readS32',4,'getInt32'],['readFloat',4,'getFloat32']])P.prototype[name]=function(){return replaying?view(this,size)[get](0,true):original[name].call(this);};
P.prototype.readByteArray=function(n){return replaying?c.ownedRead(this.toString(),n):original.readByteArray.call(this,n);};
P.prototype.readPointer=function(){return replaying?f.ptr(view(this,8).getBigUint64(0,true)):original.readPointer.call(this);};
for(const name of ['readU64','readS64'])P.prototype[name]=function(){if(!replaying)return original[name].call(this);const x=view(this,8)[name==='readS64'?'getBigInt64':'getBigUint64'](0,true);return{toString:()=>x.toString(),toNumber:()=>Number(x)};};
P.prototype.readUtf16String=function(n){if(!replaying)return original.readUtf16String.call(this,n);const v=view(this,n*2);let text='';for(let i=0;i<n;i++)text+=String.fromCharCode(v.getUint16(i*2,true));return text;};
P.prototype.readCString=function(){if(replaying){late++;throw Error('metadata live read');}return original.readCString.call(this);};
for(const[name,native]of Object.entries(f.metadataExports))f.metadataExports[name]=(...args)=>{if(replaying){late++;throw Error('NativeFunction in replay');}return native(...args);};
function reduce(record){c.record=record;replaying=true;try{return vm.runInContext('__arkdpsReplayBegin(record);try{globalThis.binding=prepareCapturedColdBinding(record);}finally{globalThis.summary=__arkdpsReplayEnd();}binding;',c);}finally{replaying=false;}}
const record=capture(),binding=reduce(record);
assert.equal(c.summary.complete,true,JSON.stringify(c.summary));
assert(binding,vm.runInContext('JSON.stringify(nativeAutoBindingDiagnostic)',c));
assert.equal(binding.native_autochess_context.status,'available');assert.equal(binding.native_autochess_context.memberships.length,4);
assert.deepEqual(JSON.parse(JSON.stringify(binding.native_autochess_context.players)),JSON.parse(JSON.stringify(baseline.native_autochess_context.players)));
// One failed Chess branch must preserve the other players/memberships.
f.write(f.roleData[0].add(0x50),undefined);
const partialRecord=capture(),partial=reduce(partialRecord);
assert.equal(c.summary.complete,true,JSON.stringify(c.summary));
assert(partial);assert.equal(partial.native_autochess_context.status,'available');assert.equal(partial.native_autochess_context.unknown_memberships.length,1);
assert(partial.native_autochess_context.memberships.length>0);
f.write(f.roleData[0].add(0x50),f.text('char_shared'));
// Cold zero is actual freshly captured clock evidence, never a warm template.
f.clock(0,0n);const zeroRecord=capture(),zero=reduce(zeroRecord);
assert.equal(c.summary.complete,true,JSON.stringify(c.summary));assert(zero);assert.equal(zero.native_frame,0);assert.equal(zero.native_fixed_time,'0');
// Independent closing root refuses replacement, even when an old body exists.
const changed=capture(),link=changed.segments.find(s=>s.role==='cold.root_close'&&s.address===f.controller.add(0xe0).toString());
assert(link);link.bytes=new ArrayBuffer(8);assert.equal(reduce(changed),null);
assert.equal(c.summary.complete,true,JSON.stringify(c.summary));
assert.equal(late,0);
console.log(JSON.stringify({status:'PASS_COLD_SOURCE_REPLAY',fields:record.fields.length,segments:record.segments.length,late_reads:late,scope:binding.native_autochess_context.scope_key}));
