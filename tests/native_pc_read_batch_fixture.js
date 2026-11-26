'use strict';
// Fake provider isolates JS protocol/pinning. Actual C++/QuickJS reads and
// graph compile/guard priority are covered by owned_read_plan_test.cpp.
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
globalThis.retiredApisAbsent=typeof __arkdpsRegisterOwnedReadPlan==='undefined'&&typeof __arkdpsOwnedReadBatch==='undefined'&&typeof __nativeOwnedReadPlanRegister==='undefined'&&typeof __nativeOwnedReadBatch==='undefined';
globalThis.liveCount=live;
`,c);
assert(c.retiredApisAbsent);
vm.runInContext(`
mode='ok';
const scopeGraph=Object.freeze({revision:7,mode_epoch:2,fields:Object.freeze([{root:'absolute',kind:'bytes',bytes:8},{root:'absolute',kind:'utf16',max_units:200}])});
__arkdpsReplayBegin(nativeRecord());globalThis.oldGraph=__arkdpsOwnedGraphReadBatch('scope',scopeGraph,0);__arkdpsReplayEnd();
const graphPlan=plans.push({metadata:44,spec:{fields:[{tier:1,bytes:8},{tier:1,kind:'utf16',max_units:200}]}});
let graphCalls=0;
function graphExecute(kind,revision,epoch,chunk){
 graphCalls++;if(arguments.length!==4||kind!=='scope'||revision!==7||epoch!==2||chunk!==0)throw Error('graph ABI');
 return execute(graphPlan,44,4096n,1,3);
}
globalThis.__nativeOwnedGraphReadBatch=graphExecute;
__arkdpsReplayBegin(nativeRecord());
const graphBatch=__arkdpsOwnedGraphReadBatch('scope',scopeGraph,0);
globalThis.graphValue=graphBatch.slot(0).getBigUint64(0,true).toString();
globalThis.graphText=graphBatch.slot(1).getUint16(12,true);
globalThis.__nativeOwnedGraphReadBatch=()=>{throw Error('new provider');};
globalThis.graphPinned=__arkdpsOwnedGraphReadBatch('scope',scopeGraph,0).slot(0).getBigUint64(0,true).toString();
globalThis.graphEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.graphReplace=rejects(()=>__arkdpsOwnedGraphReadBatch('scope',scopeGraph,0));globalThis.graphReplaceEnd=__arkdpsReplayEnd();
globalThis.__nativeOwnedGraphReadBatch=graphExecute;
mode='planned';__arkdpsReplayBegin(nativeRecord());const graphPartial=__arkdpsOwnedGraphReadBatch('scope',scopeGraph,0);
globalThis.graphPlanned=rejects(()=>graphPartial.slot(1));globalThis.graphPlannedEnd=__arkdpsReplayEnd();
mode='ok';__arkdpsReplayBegin(nativeRecord());
const before=graphCalls;globalThis.graphUnsupported=__arkdpsOwnedGraphReadBatch('scope',{revision:8,mode_epoch:2,fields:[{root:'absolute',kind:'bytes',bytes:129}]},0);globalThis.unsupportedNoCall=before===graphCalls;globalThis.unsupportedEnd=__arkdpsReplayEnd();
`,c);
assert.equal(c.oldGraph,null);assert.equal(c.graphValue,'111');assert.equal(c.graphPinned,'111');assert.equal(c.graphText,0x4e2d);assert(c.graphEnd.complete);
assert(c.graphReplace&&!c.graphReplaceEnd.complete);assert(c.graphPlanned&&c.graphPlannedEnd.complete);assert.equal(c.graphUnsupported,null);assert(c.unsupportedNoCall&&c.unsupportedEnd.complete);
vm.runInContext(`
mode='ok';
const checkGraph=Object.freeze({revision:9,mode_epoch:2,fields:Object.freeze([
 Object.freeze({role:'scope.static',root:'absolute',kind:'bytes',bytes:4,projection:false,comparison:{version:1,words:[{offset:0,kind:'u32',value:73}]}}),
 Object.freeze({role:'scope.dynamic',root:'absolute',kind:'bytes',bytes:8,projection:true})])});
__arkdpsReplayBegin(nativeRecord());globalThis.noCheck=__arkdpsOwnedGraphCheck('scope',checkGraph);__arkdpsReplayEnd();
const checkPlan=plans.push({metadata:45,spec:{fields:[{tier:1,bytes:8}]}});
function checkExecute(kind,revision,epoch){
 if(arguments.length!==3||kind!=='scope'||revision!==9||epoch!==2)throw Error('check ABI');
 if(mode==='check-failure'){status=8;return;}
 return execute(checkPlan,45,4096n,1,3);
}
globalThis.__nativeOwnedGraphCheck=checkExecute;globalThis.__nativeOwnedGraphFailure=()=>0;
__arkdpsReplayBegin(nativeRecord());const proof=__arkdpsOwnedGraphCheck('scope',checkGraph);
globalThis.proofGood=__arkdpsVerifiedGraphProof(proof,'scope',checkGraph)&&Object.isFrozen(proof);
globalThis.proofForged=__arkdpsVerifiedGraphProof({},'scope',checkGraph)||__arkdpsVerifiedGraphProof(true,'scope',checkGraph)||__arkdpsVerifiedGraphProof(proof,'publication',checkGraph);
globalThis.proofValue=proof.slot(1).getBigUint64(0,true).toString();globalThis.proofEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.proofExpired=__arkdpsVerifiedGraphProof(proof,'scope',checkGraph);globalThis.proofExpiredSlot=rejects(()=>proof.slot(1));globalThis.proofExpiredEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());const another=__arkdpsOwnedGraphCheck('scope',checkGraph);globalThis.proofStatic=rejects(()=>another.slot(0));globalThis.proofStaticEnd=__arkdpsReplayEnd();
mode='check-failure';__arkdpsReplayBegin(nativeRecord());globalThis.checkFailure=rejects(()=>__arkdpsOwnedGraphCheck('scope',checkGraph));globalThis.checkFailureEnd=__arkdpsReplayEnd();
mode='ok';__arkdpsReplayBegin(nativeRecord());const unsupported={...checkGraph,fields:[{...checkGraph.fields[0],comparison:null,projection:true}]};globalThis.checkUnsupported=__arkdpsOwnedGraphCheck('scope',unsupported);globalThis.checkUnsupportedEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.emptyCheck=__arkdpsOwnedGraphCheck('publication',{revision:9,mode_epoch:2,chunks:[]});globalThis.emptyCheckEnd=__arkdpsReplayEnd();
`,c);
assert.equal(c.noCheck,null);assert(c.proofGood&&!c.proofForged);assert.equal(c.proofValue,'111');assert(c.proofEnd.complete);
assert(!c.proofExpired&&c.proofExpiredSlot&&!c.proofExpiredEnd.complete);assert(c.proofStatic&&!c.proofStaticEnd.complete);
assert(c.checkFailure&&c.checkFailureEnd.complete);assert.equal(c.checkFailureEnd.unreadable_reads,1);assert.equal(c.checkUnsupported,null);assert(c.checkUnsupportedEnd.complete);assert.equal(c.emptyCheck,null);assert(c.emptyCheckEnd.complete);
assert.equal(c.liveCount,0);console.log('PASS owned graph batch facade: slots/UTF16/planned/providerpin/lease/malformed/no fallback');
