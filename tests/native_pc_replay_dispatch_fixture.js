'use strict';
// Fixed-boundary fake native provider. This tests fused dispatch ordering and
// cleanup, not native throughput or a substitute business reducer.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),{isProxy}=require('util').types;
const c=vm.createContext({isProxy});
vm.runInContext(`
let status=0,poison=false,lease=true,live=0,endCalls=0,prefixCalls=0;
function __read(){live++;throw Error('LIVE');}function __pid(){return 1;}function __nativeActive(){return 0;}
function __nativeOwnedLease(ticket){return lease&&ticket==='17';}
function __nativeOwnedStatus(){return status;}
function __nativeOwnedPoint(role,cp){return cp===0?((1<<24)|7):(9<<16);}
function __nativeOwnedRead(role,cp,id,address,size){if(cp!==0||role!=='body'||address!==4096n){status=9;poison=true;return;}status=0;const b=new ArrayBuffer(size);if(size)new Uint8Array(b)[0]=251;return b;}
function __nativeOwnedScalar(role,cp,id,address,kind){if(cp!==0||role!=='body'||address!==4096n||kind!==0){status=9;poison=true;return;}status=0;return 251;}
function __nativeReplayHealthy(){return !poison;}
function header(edge=0,closed=0){const b=new ArrayBuffer(176),v=new DataView(b);v.setBigUint64(0,17n,true);v.setBigUint64(8,9007199254740993n,true);v.setBigUint64(7*8,2n,true);v.setBigUint64(8*8,3n,true);v.setBigUint64(18*8,BigInt(edge),true);v.setBigUint64(19*8,BigInt(closed),true);return b;}
function argumentsBytes(count){const b=new ArrayBuffer(count*8),v=new DataView(b);for(let i=0;i<count;i++)v.setBigUint64(i*8,i===0?0xffffffffffffffffn:BigInt(i),true);return b;}
function rejects(fn){try{fn();return false;}catch(error){return true;}}
`,c);
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),c);
vm.runInContext(`
const originalEnd=__arkdpsReplayEnd;__arkdpsReplayEnd=function(compact){endCalls++;return originalEnd(compact);};
const receiver={calls:0},input=argumentsBytes(3),prefix=record=>{prefixCalls++;if(!__arkdpsReplayActive()||record!==__arkdpsReplayRecord())throw Error('prefix moved after End');return Object.freeze({value:__arkdpsReplayPoint('body',()=>ptr(4096).readU8())});};
globalThis.entry=__arkdpsReplayDispatch(header(),function(args){this.calls++;this.args=args;this.value=__arkdpsReplayPoint('body',()=>ptr(4096).readU8());},receiver,input,7,prefix);
globalThis.entryFacts={calls:receiver.calls,value:receiver.value,args:receiver.args.map(p=>p.toString()),proxy:isProxy(receiver.args),active:__arkdpsReplayActive(),endCalls,prefixCalls};
const result=0xfedcba9876543210n;
globalThis.leave=__arkdpsReplayDispatch(header(1,1),function(value){this.result=value.toString();},receiver,result,0,prefix);
globalThis.leaveFacts={result:receiver.result,active:__arkdpsReplayActive(),endCalls,prefixCalls};
globalThis.retained={invocation:entry[0].invocation_id,epoch:entry[0].facts.mode_epoch,scopeEpoch:entry[0].facts.scope_mode_epoch,args:receiver.args.map(p=>p.toString())};
globalThis.failures=[];
function failure(name,operation){const before=endCalls,thrown=rejects(operation);failures.push({name,thrown,ends:endCalls-before,active:__arkdpsReplayActive()});status=0;poison=false;}
failure('callback',()=>__arkdpsReplayDispatch(header(),()=>{throw Error('callback failed');},{},argumentsBytes(1),1,prefix));
failure('prefix',()=>__arkdpsReplayDispatch(header(1,1),()=>{},{},0n,0,()=>{throw Error('prefix failed');}));
failure('arguments',()=>__arkdpsReplayDispatch(header(),()=>{},{},argumentsBytes(1),2,prefix));
failure('masked-read',()=>__arkdpsReplayDispatch(header(),args=>args[1],{},argumentsBytes(3),1,prefix));
const healthy=__nativeReplayHealthy;__nativeReplayHealthy=undefined;
failure('health-missing',()=>__arkdpsReplayDispatch(header(),()=>{throw Error('callback must not run');},{},argumentsBytes(1),1,prefix));__nativeReplayHealthy=healthy;
__nativeReplayHealthy=()=>1;failure('health-type',()=>__arkdpsReplayDispatch(header(),()=>{},{},argumentsBytes(1),1,prefix));__nativeReplayHealthy=healthy;
lease=false;failure('lease',()=>__arkdpsReplayDispatch(header(),()=>{},{},argumentsBytes(1),1,prefix));lease=true;
const beforePoisonPrefix=prefixCalls;
globalThis.poisoned=__arkdpsReplayDispatch(header(1,1),()=>{__nativeOwnedRead('body',99,-1,4096n,1);__nativeOwnedScalar('body',0,-1,4096n,0);},{},0n,0,prefix);
globalThis.poisonFacts={prefixCalls:prefixCalls-beforePoisonPrefix,lastStatus:status,active:__arkdpsReplayActive()};poison=false;status=0;
const beforeGapPrefix=prefixCalls;
globalThis.caughtGap=__arkdpsReplayDispatch(header(1,1),()=>{try{__arkdpsReplayGap('caught own structure');}catch(ignored){}},{},0n,0,prefix);
globalThis.gapPrefixCalls=prefixCalls-beforeGapPrefix;
globalThis.small=[];for(let count=0;count<=3;count++){
 const args=__arkdpsArguments(argumentsBytes(count),(1<<count)-1),descriptor=count?Object.getOwnPropertyDescriptor(args,'0'):null;
 small.push({count,array:Array.isArray(args),proxy:isProxy(args),keys:Object.keys(args),descriptor:descriptor&&{writable:descriptor.writable,enumerable:descriptor.enumerable,configurable:descriptor.configurable},stable:!count||args[0]===args[0]});
}
globalThis.large=[isProxy(__arkdpsArguments(argumentsBytes(12),4095)),isProxy(__arkdpsArguments(argumentsBytes(16),65535)),isProxy(__arkdpsArguments(argumentsBytes(3),1))];
globalThis.liveCount=live;
`,c);
assert.equal(c.entry[2],true);assert.equal(c.entry[1],undefined);assert.equal(c.entryFacts.calls,1);assert.equal(c.entryFacts.value,251);assert.equal(c.entryFacts.proxy,false);assert.equal(c.entryFacts.active,false);assert.equal(c.entryFacts.endCalls,1);assert.equal(c.entryFacts.prefixCalls,0);
assert.deepEqual(Array.from(c.entryFacts.args),['0xffffffffffffffff','0x1','0x2']);
assert.equal(c.leave[2],true);assert.equal(c.leave[1].value,251);assert.equal(c.leaveFacts.result,'0xfedcba9876543210');assert.equal(c.leaveFacts.active,false);assert.equal(c.leaveFacts.endCalls,2);assert.equal(c.leaveFacts.prefixCalls,1);
assert.equal(c.retained.invocation,'9007199254740993');assert.equal(c.retained.epoch,2);assert.equal(c.retained.scopeEpoch,3);
for(const item of c.failures){assert(item.thrown,item.name);assert.equal(item.active,false,item.name);assert.equal(item.ends,item.name==='lease'?0:1,item.name);}
assert.equal(c.poisoned[2],false);assert.equal(c.poisoned[1],undefined);assert.equal(c.poisonFacts.prefixCalls,0);assert.equal(c.poisonFacts.lastStatus,0);assert.equal(c.poisonFacts.active,false);
assert.equal(c.caughtGap[2],false);assert.equal(c.gapPrefixCalls,0);
for(const item of c.small){assert(item.array);assert.equal(item.proxy,false);assert.equal(item.keys.length,item.count);assert(item.stable);if(item.descriptor)assert.deepEqual(JSON.parse(JSON.stringify(item.descriptor)),{writable:true,enumerable:true,configurable:true});}
assert.deepEqual(Array.from(c.large),[true,true,true]);assert.equal(c.liveCount,0);
console.log('PASS_FUSED_REPLAY_DISPATCH fake provider only; single-edge lifecycle/healthy-prefix/End-finally/poison/small-dense/masked-lazy/no live');
