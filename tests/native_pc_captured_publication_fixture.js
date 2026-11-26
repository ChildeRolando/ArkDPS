'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let live=0;
const context=vm.createContext({__pid:()=>1,__nativeActive:()=>0,__read:()=>{live++;throw Error('LIVE');},__nativeCall:()=>{live++;throw Error('LIVE CALL');}});
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),context);
const adapter=fs.readFileSync('event_probe_native_pc.js','utf8');vm.runInContext(adapter.slice(adapter.indexOf('function nativeCapturePlan(')),context);
const shared=fs.readFileSync('event_probe_runtime.js','utf8');vm.runInContext(shared.slice(shared.indexOf('function capturedUtf16('),shared.indexOf('function buff(')),context);
const autoSource=fs.readFileSync('event_probe_autochess.js','utf8');vm.runInContext(autoSource.slice(autoSource.indexOf('function scopeBlock('),autoSource.indexOf('function scopeGuardGroup(')),context);
vm.runInContext(shared.slice(shared.indexOf('function __arkdpsPublicationServed('),shared.indexOf('function receiveEventRequest(')),context);
context.graphBatchSource=process.env.ARKDPS_SOURCE_GRAPH_BATCH==='1';
vm.runInContext(`
const mem=new Map();
function write(address,bytes){new Uint8Array(bytes).forEach((b,i)=>mem.set(BigInt(address)+BigInt(i),b));}
function integer(address,value,size=8){const bytes=new ArrayBuffer(size),v=new DataView(bytes);if(size===8)v.setBigUint64(0,BigInt(value),true);else v.setUint32(0,Number(value),true);write(address,bytes);}
function read(address,size){const bytes=new Uint8Array(size);for(let i=0;i<size;i++){const b=mem.get(BigInt(address)+BigInt(i));if(b===undefined)throw Error('fixture missing '+address+' '+size);bytes[i]=b;}return bytes.buffer;}
function text(address,value){integer(address,0x20000);integer(BigInt(address)+16n,value.length,4);const bytes=new ArrayBuffer(value.length*2),v=new DataView(bytes);for(let i=0;i<value.length;i++)v.setUint16(i*2,value.charCodeAt(i),true);write(BigInt(address)+20n,bytes);}
nativeCaptureModeEpoch=0;nativeCaptureMetadata={classes:{BattleController:'0x10000',LevelData:'0x60000'},string_class:'0x20000'};
nativeCaptureScopes=new Map();nativeGeneration=2;
const identity={controller:'0x1000',native:'0x2000',logger:'0x3000',stats:'0x4000',level:'0x5000'};
const roots=Object.values(identity).join(':'),stageId='level_main_01';
integer(0x10000n+0x58n,0x11000);integer(0x11000n+0xb8n,0x12000);integer(0x12000,0x1000);
for(const [a,v]of [[0x1000,0x10000],[0x1010,0x2000],[0x10e0,0x3000],[0x1160,0x5000],[0x2028,0x1000],[0x3000,0x30000],[0x3018,0x1000],[0x3030,0x4000],[0x4000,0x40000],[0x5000,0x60000],[0x5018,0xa000]])integer(a,v);
integer(0x1228,2,4);text(0xa000,stageId);
function setClock(frame,fixed){integer(0xb000,frame,4);integer(0xb004,fixed);}
setClock(10,5n*4294967296n);
const graph=nativeCaptureScopeGraph({autochess:false,identity,roots,scopeGuards:[],addresses:{}},{key:roots+':'+stageId,enabled:false,stageAddress:ptr(0x5018),stagePointer:ptr(0xa000)},ptr(0xb000),1);
globalThis.scopeGeometryFrozen=Object.isFrozen(graph)&&Object.isFrozen(graph.fields)&&Object.isFrozen(graph.fields[0])&&Object.isFrozen(graph.fields[0].pointer_offsets)&&Object.isFrozen(graph.context.identity);
let geometryParses=0;const originalPtr=ptr;ptr=value=>{if(typeof value==='string')geometryParses++;return originalPtr(value);};
const compiledScope=compileCapturedScopeReads(graph),firstParses=geometryParses;
globalThis.compiledScopeReused=compiledScope===compileCapturedScopeReads(graph)&&geometryParses===firstParses&&firstParses>0;
ptr=originalPtr;
const cacheExpected=[['i',0,4]],geometryProbe=sealNativeScopeGraph({fields:[{role:'scope.probe',absolute_address:'0x1000',pointer_offsets:[],offset:0,bytes:4,expected:cacheExpected}],context:{identity:{controller:'0x1000'}}});
cacheExpected[0][2]=9;globalThis.cacheArrayNotFrozen=geometryProbe.fields[0].expected[0][2]===4&&!Object.isFrozen(cacheExpected)&&!Object.isFrozen(cacheExpected[0]);
const manualGraph={fields:graph.fields.map(field=>({...field,pointer_offsets:[...field.pointer_offsets]}))};
const manualPlan=compileCapturedScopeReads(manualGraph),manualAddress=manualPlan[0].base.toString();manualGraph.fields[0].absolute_address='0x9999';
globalThis.manualOwnerNotFrozen=!Object.isFrozen(manualGraph)&&manualPlan[0].base.toString()===manualAddress;
nativeCaptureScopes.set(1,graph);
const binding={native_generation:2,native_identity:identity,native_process_pid:1,native_stage_id:stageId,native_frame:1,native_fixed_time:'1',native_battle_state:2,lifecycle_baseline:{forbidden:true},live_character_collection_empty:true,live_character_data_uids:[]};
nativeCapturePublications.set(1,{mode_epoch:0,binding,guards:[]});
function record(){
 const segments=[];
 const own=(f,address,size)=>segments.push({field_id:f.field_id,role:f.role,checkpoint:0,address:String(address),bytes:read(address,size),status:'ok',guard_id:f.guard_id});
 for(const f of graph.fields){let address=BigInt(f.absolute_address);for(const offset of f.pointer_offsets){own(f,address+BigInt(offset),8);address=new DataView(read(address+BigInt(offset),8)).getBigUint64(0,true);}address+=BigInt(f.offset);
  if(f.kind==='utf16'){own(f,address,8);own(f,address+16n,4);own(f,address+20n,new DataView(read(address+16n,4)).getUint32(0,true)*2);}else own(f,address,f.bytes);
 }
 for(const [i,role]of ['clock_open','clock_close'].entries())segments.push({field_id:201+i,role,checkpoint:0,address:'0xb000',bytes:read(0xb000,12),status:'ok',guard_id:63});
 return {version:1,edge:'leave',closed_after:true,checkpoint:0,facts:{mode_epoch:0,scope_mode_epoch:0,scope_revision:1,publication_revision:1},segments};
}
checkedNativeView=(p,n)=>new DataView(p.readByteArray(n));
str=p=>p.add(0x14).readUtf16String(p.add(0x10).readS32());
className=p=>p.equals(ptr(0x30000))?'BattleLogger':p.equals(ptr(0x40000))?'BattleStats':'unknown';
captureClockFixed=()=>__arkdpsReplayWitness('clock_close',()=>ptr(0xb004).readS64());
time=()=>__arkdpsReplayWitness('clock_open',()=>{const v=new DataView(ptr(0xb000).readByteArray(12));return {frame:v.getUint32(0,true),game_time:Number(v.getBigInt64(4,true))/4294967296};});
active=0;activeHooks=new Map();coalesceEnemyHp=false;sequence=3;epoch=8;fatal=null;stopped=false;queue=[{sequence:1},{sequence:2},{sequence:3}];acknowledgedSequence=0;readThroughSequence=0;hpContext=[];hpContextDropped=0;autoChessRoundStartEnvelope=()=>({});
eventQuality=()=>({enemy_damage_ready:true});currentTerminalBoundary=()=>null;terminalDiagnostics={};
nativeInitialEnvelope=()=>({initial_boundary:null});probeAdapter={capturedPublicationBinding:nativeCapturedPublicationBinding};
if(graphBatchSource){
 // Source acquisition simulator only: actual graph field geometry is read
 // through the strict owned JS provider; native graph program has its own tests.
 nativeGraphChunks=()=>[];
 globalThis.__arkdpsOwnedGraphReadBatch=(kind,readGraph,chunk)=>({slot(slot){
  const fields=kind==='scope'?readGraph.fields:readGraph.chunks.flatMap(part=>part.fields),field=fields[chunk*32+slot];if(!field)__arkdpsReplayGap('unrequested graph slot');
  return __arkdpsReplayWitness(field.role,()=>{
   let address=ptr(field.absolute_address);for(const off of field.pointer_offsets)address=address.add(off).readPointer();address=address.add(field.offset);
   if(field.kind!=='utf16')return new DataView(address.readByteArray(field.bytes));
   const klass=address.readPointer().value,n=address.add(16).readS32(),text=address.add(20).readUtf16String(n),bytes=new ArrayBuffer(12+2*n),view=new DataView(bytes);view.setBigUint64(0,klass,true);view.setInt32(8,n,true);for(let i=0;i<n;i++)view.setUint16(12+2*i,text.charCodeAt(i),true);return view;
  });
 }});
}
function attempt(change){const r=record();if(change)change(r);__arkdpsReplayBegin(r);let result;try{result=__arkdpsOnClosedRecord(r);}finally{__arkdpsReplayEnd();}return result;}
let scopeReadCount=0;const originalWitness=__arkdpsReplayWitness;__arkdpsReplayWitness=(role,body)=>{if(role.startsWith('scope.'))scopeReadCount++;return originalWitness(role,body);};
globalThis.good=attempt();const firstScopeReads=scopeReadCount;attempt();globalThis.scopeReadsRepeat=firstScopeReads>=graph.fields.length&&scopeReadCount===firstScopeReads*2;__arkdpsReplayWitness=originalWitness;
queue[0].future='changed-after-publication';
__arkdpsPublicationJournal={publishedThrough:2};globalThis.delta=attempt();__arkdpsPublicationJournal={publishedThrough:3};globalThis.emptyDelta=attempt();delete globalThis.__arkdpsPublicationJournal;
globalThis.notClosed=attempt(r=>r.closed_after=false);
globalThis.wrongRevision=attempt(r=>r.facts.scope_revision=99);
nativeGeneration=3;globalThis.wrongGeneration=attempt();nativeGeneration=2;
setClock(0,0n);globalThis.coldRejected=attempt();setClock(10,5n*4294967296n);
globalThis.wrongRoots=attempt(r=>{nativeCaptureScopes.set(1,{...graph,context:{...graph.context,identity:{...identity,controller:'0x9999'}}});});nativeCaptureScopes.set(1,graph);

// AutoChess reuses only a full membership view whose EVERY input has freshly
// captured before/after values. The scope graph alone cannot prove this text.
text(0xa000,'AUTOCHESS_LIVE');integer(0xe000,4,4);integer(0xe004,8,4);text(0xc000,'account-one');
const autoGraph=nativeCaptureScopeGraph({identity,roots,scopeGuards:[],addresses:{phase:ptr(0xe000),round:ptr(0xe004)},headerKey:'header',fingerprint:'fingerprint'}, {key:roots+':AUTOCHESS_LIVE',enabled:true,stageAddress:ptr(0x5018),stagePointer:ptr(0xa000)},ptr(0xb000),2);
nativeCaptureScopes.set(2,autoGraph);
const autoGuards=[['text',ptr(0xc000),'account-one']];for(let i=1;i<=65;i++){integer(0xf000+i*4,1000+i,4);autoGuards.push(['i',ptr(0xf000+i*4),1000+i]);}
const autoBinding={...binding,native_stage_id:'AUTOCHESS_LIVE',native_autochess_context:{status:'available',scope_key:roots+':scope',phase:4,round:8,frame:1,game_time:1,memberships:[{data_uid:7,player_uid:'account-one'}]}};
nativeCapturePublications.set(2,{mode_epoch:0,binding:autoBinding,guards:autoGuards});
autoScope=()=>roots+':scope';autoCollectionMode=()=> 'local';
function autoRecord(){const r=record();r.facts={mode_epoch:0,scope_mode_epoch:0,scope_revision:2,publication_revision:2};
 // Replace normal scope fields with the complete auto scope graph snapshots.
 r.segments=r.segments.filter(s=>s.role==='clock_open'||s.role==='clock_close');
 const own=(f,address,size)=>r.segments.push({field_id:f.field_id,role:f.role,checkpoint:0,address:String(address),bytes:read(address,size),status:'ok',guard_id:f.guard_id});
 for(const f of autoGraph.fields){let address=BigInt(f.absolute_address);for(const offset of f.pointer_offsets){own(f,address+BigInt(offset),8);address=new DataView(read(address+BigInt(offset),8)).getBigUint64(0,true);}address+=BigInt(f.offset);if(f.kind==='utf16'){own(f,address,8);own(f,address+16n,4);own(f,address+20n,new DataView(read(address+16n,4)).getUint32(0,true)*2);}else own(f,address,f.bytes);}
 const publication=publicationGuardFields(autoGuards,'0x20000',2);
 for(const chunk of publication.chunks)for(const f of chunk.fields){const address=BigInt(f.absolute_address);if(f.kind==='utf16'){own(f,address,8);own(f,address+16n,4);own(f,address+20n,new DataView(read(address+16n,4)).getUint32(0,true)*2);}else own(f,address,f.bytes);}
 return r;
}
function autoAttempt(change){const r=autoRecord();if(change)change(r);__arkdpsReplayBegin(r);try{return __arkdpsOnClosedRecord(r);}finally{globalThis.lastAutoSummary=__arkdpsReplayEnd();}}
globalThis.autoGood=autoAttempt();
globalThis.autoMissingMembershipRejected=false;
globalThis.autoMissingMembershipRejected=autoAttempt(r=>r.segments=r.segments.filter(s=>!s.role.startsWith('publication.')))===null;globalThis.missingSummary=lastAutoSummary;
globalThis.autoChangedMembershipRejected=false;
globalThis.autoChangedMembershipRejected=autoAttempt(r=>{const s=r.segments.find(s=>s.role==='publication.close.0'&&s.address==='49172');globalThis.changedMemberInjected=Boolean(s);if(s)new DataView(s.bytes).setUint16(0,88,true);})===null;
globalThis.autoCrossChunkRejected=autoAttempt(r=>{const segment=r.segments.find(item=>item.role==='publication.close.65');if(!segment)throw Error('cross chunk field absent');new DataView(segment.bytes).setInt32(0,99999,true);})===null;

`,context);
assert(context.scopeGeometryFrozen);assert(context.compiledScopeReused);assert(context.cacheArrayNotFrozen);assert(context.manualOwnerNotFrozen);assert(context.scopeReadsRepeat);assert(context.good);assert.equal(context.good.mark.sequence,3);assert.equal(context.good.mark.frame,10);assert.equal(context.good.mark.native_battle_state,2);assert.equal(context.good.mark.lifecycle_baseline,undefined);assert(Object.isFrozen(context.good));
for(const key of ['notClosed','wrongRevision','wrongGeneration','coldRejected','wrongRoots'])assert.equal(context[key],null,key);
assert(context.autoCrossChunkRejected);assert(context.autoGood);assert.equal(context.autoGood.mark.native_autochess_context.frame,10);assert.equal(context.autoGood.mark.native_autochess_context.memberships[0].player_uid,'account-one');assert.equal(context.autoMissingMembershipRejected,true);assert.equal(context.autoChangedMembershipRejected,true);assert.equal(context.missingSummary.complete,false);assert.equal(context.changedMemberInjected,true);assert.equal(context.readThroughSequence,0);assert.equal(context.good.events[0].future,undefined);assert.equal(context.delta.events_after,2);assert.equal(context.delta.events_through,3);assert.equal(context.delta.events.length,1);assert.equal(context.delta.events[0].sequence,3);assert.equal(context.emptyDelta.events.length,0);assert.equal(live,0);console.log('PASS_CAPTURED_PUBLICATION normal closed-prefix + five negative fences; no live read/call');
