'use strict';
// Fake provider isolates JS protocol/pinning. Actual C++/QuickJS reads and
// registration/guard priority are covered by owned_read_plan_test.cpp.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const c=vm.createContext({});
vm.runInContext(`
let live=0,status=0,leased=true,plans=[],calls=0,mode='ok';
function __read(){live++;throw Error('LIVE');}function __pid(){return 1;}function __nativeActive(){return 0;}
function __nativeOwnedLease(ticket){return leased&&ticket==='17';}
function __nativeOwnedStatus(){return status;}
function __nativeOwnedPoint(){throw Error('unexpected point fallback');}
function __nativeOwnedRead(){live++;throw Error('unexpected byte fallback');}
function __nativeOwnedScalar(){live++;throw Error('unexpected scalar fallback');}
function nativeRecord(){return {version:1,native_view:true,ticket:'17',edge:'entry',checkpoint:3};}
function rejects(fn){try{fn();return false;}catch(error){return true;}}
`,c);
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),c);
vm.runInContext(`
const metadata={};globalThis.oldProvider=__arkdpsRegisterOwnedReadPlan(metadata,{version:1,fields:[]});
function register(metadata,spec){plans.push({metadata,spec});return plans.length;}
function execute(id,metadata,root,mask,cp){
 calls++;if(arguments.length!==5||cp!==3||root!==4096n||plans[id-1].metadata!==metadata)throw Error('batch ABI');
 if(mode==='undefined'){status=9;return;}
 status=0;const p=plans[id-1].spec,head=16+16*p.fields.length,b=new ArrayBuffer(head+64),v=new DataView(b);let cursor=head;
 v.setUint32(0,1,true);v.setUint32(4,p.fields.length,true);v.setUint32(8,mask,true);
 for(let i=0;i<p.fields.length;i++){
  const f=p.fields[i],at=16+i*16;v.setUint16(at+2,f.kind==='utf16'?1:0,true);
  if(!(mask&f.tier)){v.setUint16(at,65535,true);continue;}
  if(mode==='planned'&&i===1){v.setUint16(at,5,true);continue;}
  const size=f.kind==='utf16'?16:f.bytes;v.setUint32(at+4,cursor,true);v.setUint32(at+8,size,true);
  if(f.kind==='utf16'){v.setBigUint64(cursor,0x5000n,true);v.setInt32(cursor+8,2,true);v.setUint16(cursor+12,0x4e2d,true);v.setUint16(cursor+14,0xd800,true);}
  else if(size===8)v.setBigUint64(cursor,111n,true);else v.setUint32(cursor,73,true);
  cursor+=size;
 }
 v.setUint32(12,cursor-head,true);
 if(mode==='version')v.setUint32(0,2,true);
 if(mode==='count')v.setUint32(4,99,true);
 if(mode==='mask')v.setUint32(8,255,true);
 if(mode==='kind')v.setUint16(18,9,true);
 if(mode==='range')v.setUint32(20,1,true);
 if(mode==='short')v.setUint32(24,1,true);
 if(mode==='reserved')v.setUint32(28,1,true);
 return b.slice(0,cursor);
}
globalThis.__nativeOwnedReadPlanRegister=register;globalThis.__nativeOwnedReadBatch=execute;
const spec={version:1,fields:[{tier:1,role:'body',bytes:8},{tier:2,role:'body',bytes:4},{tier:4,role:'body',kind:'utf16',max_units:200}]};
const handle=__arkdpsRegisterOwnedReadPlan(metadata,spec);globalThis.handleFrozen=Object.isFrozen(handle);
__arkdpsReplayBegin(nativeRecord());
const batch=__arkdpsOwnedReadBatch(handle,4096n,5);globalThis.bytesValue=batch.slot(0).getBigUint64(0,true).toString();
const raw=batch.slot(2);globalThis.rawString=[raw.getBigUint64(0,true).toString(),raw.getInt32(8,true),raw.getUint16(12,true),raw.getUint16(14,true)];
globalThis.success=__arkdpsReplayEnd();
mode='planned';__arkdpsReplayBegin(nativeRecord());const partial=__arkdpsOwnedReadBatch(handle,4096n,3);
globalThis.partialValue=partial.slot(0).getBigUint64(0,true).toString();globalThis.planned=rejects(()=>partial.slot(1));globalThis.plannedEnd=__arkdpsReplayEnd();
mode='ok';__arkdpsReplayBegin(nativeRecord());const pinned=__arkdpsOwnedReadBatch;
globalThis.__nativeOwnedReadBatch=()=>{throw Error('replacement');};
globalThis.pinnedValue=pinned(handle,4096n,1).slot(0).getBigUint64(0,true).toString();globalThis.pinnedEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.changedProvider=rejects(()=>__arkdpsOwnedReadBatch(handle,4096n,1));globalThis.changedEnd=__arkdpsReplayEnd();globalThis.__nativeOwnedReadBatch=execute;
globalThis.bad=[];
for(const badMode of ['version','count','mask','kind','range','short','reserved','undefined']){
 mode=badMode;status=0;__arkdpsReplayBegin(nativeRecord());
 const rejected=rejects(()=>__arkdpsOwnedReadBatch(handle,4096n,1).slot(0));bad.push({rejected,complete:__arkdpsReplayEnd().complete});
}
mode='ok';__arkdpsReplayBegin(nativeRecord());globalThis.unrequested=rejects(()=>__arkdpsOwnedReadBatch(handle,4096n,1).slot(2));globalThis.unrequestedEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.forged=rejects(()=>__arkdpsOwnedReadBatch({},4096n,1));globalThis.forgedEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.lateRegister=rejects(()=>__arkdpsRegisterOwnedReadPlan(metadata,spec));globalThis.lateEnd=__arkdpsReplayEnd();
globalThis.liveCount=live;
`,c);
assert.equal(c.oldProvider,null);assert(c.handleFrozen);assert.equal(c.bytesValue,'111');assert.deepEqual(Array.from(c.rawString),['20480',2,0x4e2d,0xd800]);assert(c.success.complete);
assert(c.planned);assert(c.plannedEnd.complete);assert.equal(c.plannedEnd.unreadable_reads,1);assert.equal(c.partialValue,'111');
assert.equal(c.pinnedValue,'111');assert(c.pinnedEnd.complete);assert(c.changedProvider);assert(!c.changedEnd.complete);
assert(Array.from(c.bad).every(x=>x.rejected&&!x.complete));for(const k of ['unrequested','forged','late'])assert(c[k==='late'?'lateRegister':k]&&!c[k+'End'].complete);
assert.equal(c.liveCount,0);console.log('PASS owned read batch facade: slots/UTF16/planned/tiers/providerpin/metadatahandle/malformed/registration/no fallback');
