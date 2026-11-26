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
`,context);
assert.equal(context.before,7);assert.equal(context.after,8);
for(const name of ['missing','roleMissing','nativeForbidden','writeForbidden','allocForbidden','shortRejected','ambiguousRejected'])assert.equal(context[name],true,name);
assert.equal(context.wrongCheckpointRejected,true);assert.equal(context.wrongCheckpointSummary.complete,false);assert.equal(context.wrapRejected,true);assert.equal(context.badSizeRejected,true);assert.equal(context.badSizeSummary.complete,false);assert.equal(context.failedSummary.complete,false);assert.equal(context.shortSummary.complete,true);assert.equal(context.shortSummary.unreadable_reads,1);assert.equal(live,0);console.log('PASS_OWNED_REPLAY 18 assertions; zero live native reads/calls');
