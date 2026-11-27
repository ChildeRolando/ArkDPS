'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let live=0;const c=vm.createContext({__pid:()=>1,__nativeActive:()=>0,__read:()=>{live++;throw Error('LIVE');}});
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),c);
const adapter=fs.readFileSync('event_probe_native_pc.js','utf8');vm.runInContext(adapter.slice(adapter.indexOf('function captureNativeInvocationRead('),adapter.indexOf('function prepareNativeCaptureHook(')),c);
vm.runInContext(`
function bytes(value){const b=new ArrayBuffer(4);new DataView(b).setUint32(0,value,true);return b;}
function record(){return {version:1,edge:'entry',invocation_id:'20',thread_id:1,segments:[{field_id:1,role:'body',checkpoint:0,address:'4096',bytes:bytes(80),status:'ok'},{field_id:2,role:'parent.body',checkpoint:1,address:'4096',bytes:bytes(100),status:'ok'},{field_id:3,role:'parent.body',checkpoint:2,address:'4096',bytes:bytes(90),status:'ok'}],facts:{parent_checkpoints:[{invocation_id:'10',checkpoint:1},{invocation_id:'9',checkpoint:2}]}};}
__arkdpsReplayBegin(record());
globalThis.parent=captureNativeInvocationRead({nativeCaptureInvocation:'10',tid:1},()=>ptr(4096).readU32());
globalThis.current=captureNativeInvocationRead({nativeCaptureInvocation:'20',tid:1},()=>ptr(4096).readU32());
globalThis.older=captureNativeInvocationRead({nativeCaptureInvocation:'9',tid:1},()=>ptr(4096).readU32());
globalThis.good=__arkdpsReplayEnd();
function rejects(body){try{body();return false;}catch(e){return true;}}
__arkdpsReplayBegin(record());globalThis.missing=rejects(()=>captureNativeInvocationRead({nativeCaptureInvocation:'99',tid:1},()=>ptr(4096).readU32()));globalThis.bad=__arkdpsReplayEnd();
__arkdpsReplayBegin(record());globalThis.foreign=rejects(()=>captureNativeInvocationRead({nativeCaptureInvocation:'10',tid:2},()=>ptr(4096).readU32()));__arkdpsReplayEnd();
`,c);
assert.equal(c.parent,100);assert.equal(c.current,80);assert.equal(c.older,90);assert.equal(c.good.complete,true);assert.equal(c.missing,true);assert.equal(c.bad.complete,false);assert.equal(c.foreign,true);assert.equal(live,0);
console.log('PASS_PARENT_CHECKPOINT exact invocation/thread/point; missing checkpoint sticky; zero live');
