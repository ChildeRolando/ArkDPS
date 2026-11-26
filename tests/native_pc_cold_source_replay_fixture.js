'use strict';
// Offline source oracle: interpret the actual fixed cold plan over owned fixture
// memory, then forbid every live read while running the actual binding reducer.
// C++ capture ordering/budgets are independently exercised by native_capture_cold.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {world}=require('./native_pc_autochess_context_fixture.js');
const f=world({fixtureOptions:{roundResetMethod:true,settlement:true,enemyHpState:true}}),P=f.ptr(0).constructor,c=f.context;
f.write(f.character.add(0x58),f.unitClass);f.write(f.unitClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));
f.klass('BattleCharacterData','Torappu.Battle',36,[[4,'prefabKey',0x50,14],[8,'uniqueId',0x6c,9],[14,'isToken',0x90,2],[15,'isPredefined',0x91,2],[18,'tokenOrHostKey',0x98,14],[19,'tokenOrHostUniqueId',0xa0,9]],f.ptr(0),f.characterData);
for(const data of f.roleData)f.write(data,f.characterData);
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
classes.BattleController=f.controllerClass.toString();classes.Character=f.character.toString();classes.BObject=f.bObject.toString();
vm.runInContext("nativeCaptureModeHistory.set(0,'all');",c);
c.metadata={cold_metadata_ready:true,classes,string_class:classes.String};
const plan=JSON.parse(vm.runInContext('JSON.stringify(__arkdpsBuildColdCapturePlan(metadata))',c));
// The fixture's AutoChess classes are installed after initial reflection; expose
// their setup-owned exact identities, never infer a name from a replay pointer.
c.frozenIdentity=(k,name,ns)=>{const value=f.ownedClasses.get(k.toString());return !!value&&value.name===name&&value.namespace===ns;};
for(const k of [f.character,f.unitClass,f.entity,f.bObject]){const b=new ArrayBuffer(8);new DataView(b).setBigUint64(0,original.readPointer.call(k.add(0x58)).value,true);metadata.set(k.add(0x58).toString()+':8',b);}
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
for(const[name,native]of Object.entries(f.metadataExports))f.metadataExports[name]=(...args)=>{if(replaying){if(name==='il2cpp_class_is_assignable_from')return c.__arkdpsCapturedAssignable(...args);late++;throw Error('NativeFunction in replay');}return native(...args);};
function reduce(record){c.ownedRecord=record;replaying=true;try{return vm.runInContext('__arkdpsReplayBegin(ownedRecord);try{globalThis.binding=prepareCapturedColdBinding(ownedRecord);}finally{globalThis.summary=__arkdpsReplayEnd();}binding;',c);}finally{replaying=false;}}
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
// Exercise the real PLAYING transition callback with this cold tree on return.
c.planMetadata={...c.metadata,clock:vm.runInContext('clock',c),skill_offsets:{},immutable_class_parents:true};vm.runInContext('nativeCaptureMetadata=planMetadata;',c);
const statePlan=vm.runInContext('nativeCapturePlan("BattleController.set_state",ptr(1),planMetadata)',c);
function captureState(edge,cold,sourcePlan=statePlan,args=[f.controller]){
 const r=cold?capture():{version:1,edge,checkpoint:0,segments:[],fields:[],facts:{mode_epoch:0,scope_mode_epoch:0}};
 r.edge=edge;r.segments=r.segments.filter(s=>!['clock_open','clock_close'].includes(s.role));r.fields=r.fields.filter(s=>!['clock_open','clock_close'].includes(s.role));
 for(const field of sourcePlan.fields.filter(x=>x.phase===edge)){
  const add=(p,b)=>r.segments.push({field_id:field.field_id,role:field.role,checkpoint:0,address:p.toString(),bytes:b,status:'ok',guard_id:field.guard_id});
  try{let p=field.root==='global_slot'?c.planMetadata.clock:field.root==='absolute'?f.ptr(field.absolute_address):args[field.root_index];
   for(const offset of field.pointer_offsets){add(p.add(offset),raw(p.add(offset),8));p=original.readPointer.call(p.add(offset));}p=p.add(field.offset);
   if(field.kind==='utf16'){add(p,raw(p,8));add(p.add(16),raw(p.add(16),4));const n=original.readS32.call(p.add(16)),text=original.readUtf16String.call(p.add(20)),b=new ArrayBuffer(n*2),v=new DataView(b);for(let i=0;i<n;i++)v.setUint16(i*2,text.charCodeAt(i),true);add(p.add(20),b);}else add(p,raw(p,field.bytes));
   r.fields.push({field_id:field.field_id,role:field.role,checkpoint:0,status:'ok'});
  }catch(error){r.fields.push({field_id:field.field_id,role:field.role,checkpoint:0,status:'field_unreadable'});for(const segment of r.segments)if(segment.role===field.role&&segment.field_id===field.field_id)segment.status='unreadable';}
 }
 return r;
}
function dispatch(record,fn){c.ownedRecord=record;c.stateCallback=fn;replaying=true;try{vm.runInContext('__arkdpsReplayBegin(ownedRecord);try{__arkdpsReplayPoint("body",stateCallback);}finally{globalThis.summary=__arkdpsReplayEnd();}',c);}finally{replaying=false;}assert.equal(c.summary.complete,true,JSON.stringify(c.summary));}
f.write(f.controller.add(0x228),1);const stateHook=f.callback('set_state',f.controllerClass),stateCall={threadId:1};
dispatch(captureState('entry',false),()=>stateHook.onEnter.call(stateCall,[f.controller,f.ptr(2)]));
f.write(f.controller.add(0x228),2);dispatch(captureState('leave',true),()=>stateHook.onLeave.call(stateCall));
assert.equal(vm.runInContext('nativeInitialBoundary?.source',c),'native-pc-producer',vm.runInContext('JSON.stringify(nativeInitialDiagnostics)',c));
f.clock(30,4294967296n);
const resetPlan=vm.runInContext('nativeCapturePlan("BattleController.ClearRunTimeData",ptr(1),planMetadata)',c),resetHook=f.callback('ClearRunTimeData',f.controllerClass),resetCall={threadId:1};
dispatch(captureState('entry',false,resetPlan),()=>resetHook.onEnter.call(resetCall,[f.controller]));
f.clock(0,0n);f.write(f.center.add(0x3c),2);
dispatch(captureState('leave',true,resetPlan),()=>resetHook.onLeave.call(resetCall));
assert.equal(vm.runInContext('autoChessRoundStart?.native_autochess_context.round',c),2);
assert.equal(vm.runInContext('autoChessRoundStart?.native_fixed_time',c),'0');
// The real ApplyModifier path must derive token/form evidence exclusively
// from the same record's actor/data fields and full AutoChess membership tree.
f.clock(30,4294967296n);
const applyPlan=vm.runInContext('nativeCapturePlan("Entity.ApplyModifier",ptr(1),planMetadata)',c),applyHook=f.callback('ApplyModifier',f.entity);
function actorHit(id,dataId,uid,token,fault=null){
 const before=vm.runInContext('sequence',c);
 const actor=f.unit(id,100+uid),data=actor.add(0x538).readPointer(),enemy=f.unit('enemy_owned_target',200+uid);
 f.write(actor.add(0x148),f.text(id));f.write(enemy.add(0x148),f.text('enemy_owned_target'));
 f.write(data.add(0x50),f.text(dataId));f.write(data.add(0x6c),uid);f.write(data.add(0x90),token?1:0);f.write(data.add(0x91),0);f.write(data.add(0x98),token?f.text('char_shared'):f.ptr(0));f.write(data.add(0xa0),token?101:0);f.write(actor.add(0x20),1);
 f.write(enemy.add(0x40),50n*4294967296n);const mod=f.modifier(actor,enemy,40,-40,1),call={threadId:1,nativeCaptureInvocation:String(uid)},args=[enemy,mod];
 const entry=captureState('entry',true,applyPlan,args);entry.invocation_id=String(uid);entry.thread_id=1;dispatch(entry,()=>applyHook.onEnter.call(call,args));
 f.write(enemy.add(0x40),10n*4294967296n);if(fault==='data_uid')f.write(data.add(0x6c),uid+1);if(fault==='side')f.write(actor.add(0x20),5);
 const leave=captureState('leave',true,applyPlan,args);leave.invocation_id=String(uid);leave.thread_id=1;dispatch(leave,()=>applyHook.onLeave.call(call));return JSON.parse(vm.runInContext('JSON.stringify(queue)',c)).filter(e=>e.sequence>before);
}
f.write(f.roleData[1].add(0x50),f.text('char_1037_amiya3'));
actorHit('char_002_amiya','char_1037_amiya3',102,false);
actorHit('token_owned','token_owned',999,true);
const actorEvents=JSON.parse(vm.runInContext('JSON.stringify(queue)',c));
assert(actorEvents.some(e=>e.proof?.native_autochess_character_form),JSON.stringify(actorEvents));
assert(actorEvents.some(e=>e.proof?.native_autochess_token_host),JSON.stringify(actorEvents));
const badForm=actorHit('char_002_amiya','char_1037_amiya3',102,false,'data_uid');assert(badForm.every(e=>!e.proof?.native_autochess_character_form));
const badToken=actorHit('token_owned','token_owned',998,true,'side');assert(badToken.every(e=>!e.proof?.native_autochess_token_host));
// A claimed leave of an ignored Character contains no HP body fields. The
// real no-write handler still allows a fresh full publication graph endpoint.
const ignored=f.unit('char_ignored_owned',800);f.write(ignored.add(0x148),f.text('char_ignored_owned'));
const hpPlan=vm.runInContext('nativeCapturePlan("Entity.SetHpInternal",ptr(1),planMetadata)',c),hpHook=f.callback('SetHpInternal',f.entity),ignoredCall={threadId:1};
dispatch(captureState('entry',false,hpPlan,[ignored]),()=>hpHook.onEnter.call(ignoredCall,[ignored]));assert.equal(ignoredCall.write,undefined);
c.latestBinding=vm.runInContext('nativeBinding(null,true)',c);
vm.runInContext('globalThis.ownedScopeGraph=nativeCaptureScopeGraph(nativeAutoCache,nativeAutoStage,clock,1);nativeCaptureScopes.set(1,ownedScopeGraph);globalThis.ownedPublicationGuards=nativeAutoBodyCache.body.read.guards;nativeCapturePublications.set(1,{mode_epoch:0,binding:JSON.parse(JSON.stringify(latestBinding)),guards:ownedPublicationGuards});globalThis.ownedPublicationGraph=publicationGuardFields(ownedPublicationGuards,planMetadata.string_class,1);',c);
const publicationFields=[...c.ownedScopeGraph.fields,...c.ownedPublicationGraph.chunks.flatMap(x=>x.fields)].map(field=>({...field,phase:'leave'}));
const clockFields=hpPlan.fields.filter(field=>field.phase==='leave'&&['clock_open','clock_close'].includes(field.role));
const ignoredRecord=captureState('leave',false,{fields:[...clockFields,...publicationFields]},[ignored]);ignoredRecord.closed_after=true;ignoredRecord.active_after=0;ignoredRecord.facts.scope_revision=1;ignoredRecord.facts.publication_revision=1;
assert(!ignoredRecord.fields.some(field=>field.role==='body'));
dispatch(ignoredRecord,()=>{hpHook.onLeave.call(ignoredCall);c.ignoredProjection=c.__arkdpsOnClosedRecord(ignoredRecord);});
assert(c.ignoredProjection);assert.equal(c.ignoredProjection.mark.sequence,actorEvents.length+badForm.length+badToken.length);assert.equal(vm.runInContext('readThroughSequence',c),0);
const dirtyHook=f.callback('OnAttributeDirty',f.entity),dirtyCall={threadId:1},emptyEntry={version:1,edge:'entry',checkpoint:0,segments:[],fields:[],facts:{mode_epoch:0,scope_mode_epoch:0}};
dispatch(emptyEntry,()=>dirtyHook.onEnter.call(dirtyCall,[ignored,f.ptr(1),f.ptr(0)]));assert.equal(dirtyCall.write,undefined);
const dirtyLeave={...ignoredRecord,facts:{...ignoredRecord.facts}};dispatch(dirtyLeave,()=>{dirtyHook.onLeave.call(dirtyCall);c.ignoredDirtyProjection=c.__arkdpsOnClosedRecord(dirtyLeave);});
assert(c.ignoredDirtyProjection);assert.equal(c.ignoredDirtyProjection.mark.sequence,c.ignoredProjection.mark.sequence);assert.equal(vm.runInContext('readThroughSequence',c),0);

// Destroy entry owns the full old binding; leave owns the released singleton
// and independently copied old views. No normal live binding is available.
f.write(f.controller.add(0x228),3);
const destroyPlan=vm.runInContext('nativeCapturePlan("BattleController.Destroy",ptr(1),planMetadata)',c),destroyHook=f.callback('OnDestroy',f.controllerClass),destroyCall={threadId:1};
const oldLogger=original.readPointer.call(f.controller.add(0xe0)),oldNative=original.readPointer.call(f.controller.add(0x10)),oldStats=original.readPointer.call(oldLogger.add(0x30));
dispatch(captureState('entry',true,destroyPlan),()=>destroyHook.onEnter.call(destroyCall,[f.controller]));
const singleton=original.readPointer.call(original.readPointer.call(f.controllerClass.add(0x58)).add(0xb8));f.write(singleton,f.ptr(0));f.write(f.controller.add(0x10),f.ptr(0));
const refs=[f.controller];refs[8]=oldLogger;refs[9]=f.level;refs[10]=oldNative;refs[11]=oldStats;
const finalRecord=captureState('leave',false,destroyPlan,refs);finalRecord.closed_after=true;finalRecord.active_after=0;finalRecord.ticket='100';finalRecord.activity_begin='200';finalRecord.activity_end='201';
dispatch(finalRecord,()=>{destroyHook.onLeave.call(destroyCall);c.finalProjection=c.__arkdpsOnClosedRecord(finalRecord);});
assert(c.finalProjection,vm.runInContext('JSON.stringify(terminalDiagnostics)',c));assert.equal(c.finalProjection.terminal_read.terminal_only,true);assert.equal(c.finalProjection.terminal_read.events.length,actorEvents.length+badForm.length+badToken.length);assert.equal(vm.runInContext('readThroughSequence',c),0);
assert.equal(late,0);
console.log(JSON.stringify({status:'PASS_COLD_SOURCE_REPLAY',fields:record.fields.length,segments:record.segments.length,late_reads:late,scope:binding.native_autochess_context.scope_key}));
