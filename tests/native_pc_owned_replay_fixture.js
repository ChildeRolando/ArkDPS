'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let live=0;
const context=vm.createContext({__pid:()=>1,__nativeActive:()=>0,__read:()=>{live++;throw Error('live read');},__nativeCall:()=>{live++;throw Error('live function');}});
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),context);
vm.runInContext(`
function bytes(value){const b=new ArrayBuffer(4);new DataView(b).setUint32(0,value,true);return b;}
const opening=bytes(7),closing=bytes(8);
__arkdpsReplayBegin({version:1,edge:'entry',segments:[
 {field_id:1,role:'clock_open',checkpoint:0,address:'4096',bytes:opening,status:'ok'},
 {field_id:2,role:'clock_close',checkpoint:0,address:'4096',bytes:closing,status:'ok'}]});
new DataView(opening).setUint32(0,999,true);
__arkdpsReplayRole('clock_open',0,1);globalThis.before=ptr(4096).readU32();
__arkdpsReplayRole('clock_close',0,2);globalThis.after=ptr(4096).readU32();
function rejects(fn){try{fn();return false;}catch(e){return true;}}
globalThis.missing=rejects(()=>ptr(8192).readU32());
globalThis.roleMissing=rejects(()=>{__arkdpsReplayRole('clock_open',0,2);ptr(4096).readU32();});
globalThis.nativeForbidden=rejects(()=>NativeFunction(ptr(1),'uint',[])());
globalThis.writeForbidden=rejects(()=>ptr(1).writeU64(1));
globalThis.allocForbidden=rejects(()=>Memory.alloc(4));
globalThis.failedSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin({version:1,edge:'entry',segments:[{field_id:0,role:'entry',checkpoint:0,address:'4096',bytes:bytes(7),status:'short'}]});
globalThis.shortRejected=rejects(()=>ptr(4096).readU32());globalThis.shortSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin({version:1,edge:'entry',segments:[{field_id:0,role:'entry',checkpoint:0,address:'4096',bytes:bytes(7),status:'ok'},{field_id:0,role:'entry',checkpoint:0,address:'4096',bytes:bytes(8),status:'ok'}]});
globalThis.ambiguousRejected=rejects(()=>ptr(4096).readU32());__arkdpsReplayEnd();

__arkdpsReplayBegin({version:1,edge:'entry',checkpoint:0,segments:[],fields:[{field_id:0,role:'entry',status:'unreadable'}]});
__arkdpsReplayRole('entry',1,0);globalThis.wrongCheckpointRejected=rejects(()=>ptr(4096).readU32());globalThis.wrongCheckpointSummary=__arkdpsReplayEnd();
globalThis.wrapRejected=rejects(()=>__arkdpsReplayBegin({version:1,edge:'entry',segments:[{field_id:0,role:'entry',checkpoint:0,address:'0xffffffffffffffff',bytes:bytes(7),status:'ok'}]}));
__arkdpsReplayBegin({version:1,edge:'entry',segments:[]});globalThis.badSizeRejected=rejects(()=>ptr(4096).readByteArray(-1));globalThis.badSizeSummary=__arkdpsReplayEnd();

__arkdpsReplayBegin({version:1,edge:'entry',segments:[]});
const argsBytes=new ArrayBuffer(128);new DataView(argsBytes).setBigUint64(0,42n,true);
const maskedArgs=__arkdpsArguments(argsBytes,1);globalThis.capturedArgument=maskedArgs[0].toInt32();
globalThis.maskedArgumentRejected=rejects(()=>maskedArgs[1]);globalThis.maskSummary=__arkdpsReplayEnd();
globalThis.invalidMaskRejected=rejects(()=>__arkdpsArguments(new ArrayBuffer(8),2));
globalThis.defaultArgs=__arkdpsArguments(argsBytes)[0].toInt32();

function pointRecord(a,b,guard=0){return {version:1,edge:'entry',segments:[{field_id:1,role:'body',checkpoint:0,address:'4096',bytes:bytes(a),status:'ok',guard_id:guard},{field_id:2,role:'body',checkpoint:0,address:'4096',bytes:bytes(b),status:'ok',guard_id:guard}]};}
__arkdpsReplayBegin(pointRecord(12,12));globalThis.equalPoint=__arkdpsReplayPoint('body',()=>ptr(4096).readU32());globalThis.equalSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin(pointRecord(12,13));globalThis.conflictPoint=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4096).readU32()));globalThis.conflictSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin(pointRecord(12,12,1));globalThis.guardMerge=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4096).readU32()));globalThis.guardSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin({version:1,edge:'entry',segments:[],fields:[{field_id:1,role:'body',status:'unreadable',attempt_address:'4096',attempt_size:4}]});globalThis.exactFailure=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4096).readU32()));globalThis.exactFailureSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin({version:1,edge:'entry',segments:[],fields:[{field_id:1,role:'body',status:'unreadable',attempt_address:'8192',attempt_size:4}]});globalThis.otherFailure=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4096).readU32()));globalThis.otherFailureSummary=__arkdpsReplayEnd();

const optionalRecord=pointRecord(12,12);optionalRecord.fields=[{field_id:2,role:'body',checkpoint:0,status:'unreadable',attempted_address:'8192',attempted_size:8}];
__arkdpsReplayBegin(optionalRecord);globalThis.independentGood=__arkdpsReplayPoint('body',()=>ptr(4096).readU32());globalThis.independentSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin(optionalRecord);__arkdpsReplayRole('body',0,2);globalThis.failedLinkRejected=rejects(()=>ptr(4096).readU32());__arkdpsReplayEnd();
// Dynamic catch branches share a role but never lend failed pointer links.
const branchRecord={version:1,edge:'entry',segments:[{field_id:32768,role:'cold.body',checkpoint:0,address:'4096',bytes:bytes(21),status:'ok'},{field_id:32769,role:'cold.body',checkpoint:0,address:'8192',bytes:bytes(99),status:'ok'}],fields:[{field_id:32768,role:'cold.body',status:'ok'},{field_id:32769,role:'cold.body',status:'field_unreadable',attempted_address:'8200',attempted_size:8}]};
__arkdpsReplayBegin(branchRecord);globalThis.goodBranch=__arkdpsReplayPoint('cold.body',()=>ptr(4096).readU32());globalThis.badBranch=rejects(()=>__arkdpsReplayPoint('cold.body',()=>ptr(8192).readU32()));globalThis.branchSummary=__arkdpsReplayEnd();
for(const state of branchRecord.fields)state.status='changed_guard';
__arkdpsReplayBegin(branchRecord);globalThis.changedBranch=rejects(()=>__arkdpsReplayPoint('cold.body',()=>ptr(4096).readU32()));globalThis.changedBranchSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin({version:1,edge:'entry',segments:[],fields:[{field_id:2,role:'unused',status:'bounds'}]});globalThis.unusedBounds=__arkdpsReplayEnd();
__arkdpsReplayBegin({version:1,edge:'entry',segments:[],fields:[{field_id:32768,role:'cold.body',status:'field_unreadable',attempted_address:'8192',attempted_size:8}]});__arkdpsReplayRole('cold.body',0,32768);globalThis.explicitUnvisited=rejects(()=>ptr(4096).readU32());globalThis.explicitUnvisitedSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin({version:1,edge:'entry',segments:[],fields:[{field_id:32768,role:'cold.body',status:'field_unreadable',attempted_address:'8192',attempted_size:8}]});__arkdpsReplayRole('cold.body',0,32768);globalThis.explicitAttempt=rejects(()=>ptr(8192).readU32());globalThis.explicitAttemptSummary=__arkdpsReplayEnd();
__arkdpsReplayBegin({version:1,edge:'entry',segments:[],fields:[{field_id:1,role:'unused',status:'invalid_phase'}]});globalThis.invalidPhase=__arkdpsReplayEnd();
`,context);
assert.equal(context.before,7);assert.equal(context.after,8);
for(const name of ['missing','roleMissing','nativeForbidden','writeForbidden','allocForbidden','shortRejected','ambiguousRejected'])assert.equal(context[name],true,name);
assert.equal(context.wrongCheckpointRejected,true);assert.equal(context.wrongCheckpointSummary.complete,false);assert.equal(context.wrapRejected,true);assert.equal(context.badSizeRejected,true);assert.equal(context.badSizeSummary.complete,false);assert.equal(context.failedSummary.complete,false);assert.equal(context.shortSummary.complete,true);assert.equal(context.shortSummary.unreadable_reads,1);assert.equal(context.capturedArgument,42);assert.equal(context.defaultArgs,42);assert.equal(context.maskedArgumentRejected,true);assert.equal(context.maskSummary.complete,false);assert.equal(context.invalidMaskRejected,true);assert.equal(context.equalPoint,12);assert.equal(context.equalSummary.complete,true);assert.equal(context.conflictPoint,true);assert.equal(context.conflictSummary.complete,false);assert.equal(context.guardMerge,true);assert.equal(context.guardSummary.complete,false);assert.equal(context.exactFailure,true);assert.equal(context.exactFailureSummary.complete,true);assert.equal(context.otherFailure,true);assert.equal(context.otherFailureSummary.complete,false);assert.equal(context.independentGood,12);assert.equal(context.independentSummary.complete,true);assert.equal(context.failedLinkRejected,true);assert.equal(live,0);console.log('PASS_OWNED_REPLAY 36 assertions; zero live native reads/calls');

assert.equal(context.goodBranch,21);assert.equal(context.badBranch,true);assert.equal(context.branchSummary.complete,true);assert.equal(context.changedBranch,true);assert.equal(context.changedBranchSummary.complete,true);assert.equal(context.unusedBounds.complete,false);

assert.equal(context.explicitUnvisited,true);assert.equal(context.explicitUnvisitedSummary.complete,false);assert.equal(context.explicitAttempt,true);assert.equal(context.explicitAttemptSummary.complete,true);assert.equal(context.invalidPhase.complete,false);
