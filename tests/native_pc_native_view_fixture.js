'use strict';
// Fake native provider validates the JS facade only. Native span ownership,
// lease poison, capture ordering and throughput require the separate C++ tests.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const c=vm.createContext({});
vm.runInContext(`
let live=0,leased=true,status=0,fault=0,readCalls=0,scalarCalls=0,pointCalls=0;
const calls=[],b=new ArrayBuffer(64),v=new DataView(b);
v.setUint8(0,251);v.setUint16(2,65000,true);v.setUint32(4,0xfedcba98,true);v.setInt32(8,-123456,true);
v.setBigUint64(16,0xfedcba9876543210n,true);v.setBigInt64(24,-9223372036854775807n,true);v.setFloat32(32,-13.25,true);
function __read(){live++;throw Error('LIVE');}function __pid(){return 1;}function __nativeActive(){return 0;}
function __nativeOwnedLease(ticket){return leased&&ticket==='17';}
function resolve(role,cp,id,address,size){
 calls.push({role,cp,id,address,size});
 if(!leased){status=2;return false;}if(fault){status=fault;return false;}
 if(role==='unreadable'){status=5;return false;}if(role==='guard'){status=8;return false;}
 if(role==='multi'){status=10;return false;}
 if(cp!==0||!['body','before','after'].includes(role)||![7,-1].includes(id)||address<4096n||address+BigInt(size)>4160n){status=9;return false;}
 status=0;return true;
}
function __nativeOwnedRead(role,cp,id,address,size){readCalls++;if(!resolve(role,cp,id,address,size))return;return b.slice(Number(address-4096n),Number(address-4096n)+size);}
function __nativeOwnedScalar(role,cp,id,address,kind){scalarCalls++;const sizes=[1,2,4,4,8,8,4];if(!resolve(role,cp,id,address,sizes[kind]))return;const at=Number(address-4096n);return [()=>v.getUint8(at),()=>v.getUint16(at,true),()=>v.getUint32(at,true),()=>v.getInt32(at,true),()=>v.getBigUint64(at,true),()=>v.getBigInt64(at,true),()=>v.getFloat32(at,true)][kind]();}
function __nativeOwnedStatus(){return status;}
function __nativeOwnedPoint(role,cp){pointCalls++;if(!leased)return (1<<24)|(2<<16)|7;if(fault)return (1<<24)|(fault<<16)|7;if(cp!==0)return (9<<16);if(role==='multi')return (2<<24)|(5<<16);if(role==='missing')return 0;return (1<<24)|(['unreadable','guard'].includes(role)?(role==='guard'?8:5)<<16:0)|7;}
function nativeRecord(){const r={version:1,native_view:true,ticket:'17',edge:'entry',checkpoint:0};Object.defineProperties(r,{segments:{get(){throw Error('enumerated native segments');}},fields:{get(){throw Error('enumerated native fields');}}});return r;}
function rejects(fn){try{fn();return false;}catch(error){return true;}}
`,c);
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),c);
vm.runInContext(`
const header=new ArrayBuffer(192),headerView=new DataView(header);
const words=[0xffffffffffffffffn,9007199254740993n,7n,11n,12n,134401659939984618n,19n,3n,4n,21n,22n,23n,8n,0n,1n,0n,2n,31n,1n,1n,0n,1n];
words.forEach((value,index)=>headerView.setBigUint64(index*8,value,true));
headerView.setBigUint64(176,9007199254741001n,true);headerView.setUint32(184,1,true);
const packed=__arkdpsPackedCaptureRecord(header);
globalThis.packedProof={ticket:packed.ticket,invocation:packed.invocation_id,parent:packed.parent_invocation_id,
 begin:packed.activity_begin,end:packed.activity_end,birth:packed.thread_birth,request:packed.publication_request,
 hook:packed.hook_id,checkpoint:packed.checkpoint,active:packed.active_at_capture,after:packed.active_after,
 lane:packed.lane,thread:packed.thread_id,edge:packed.edge,closed:packed.closed_after,
 facts:{mode:packed.facts.mode_epoch,scopeMode:packed.facts.scope_mode_epoch,scope:packed.facts.scope_revision,
 pub:packed.facts.publication_revision,cold:packed.facts.cold_revision,missing:packed.facts.scope_unavailable,
 parents:packed.facts.parent_checkpoints},stableFacts:packed.facts===packed.facts,
 frozen:Object.isFrozen(packed)&&Object.isFrozen(packed.facts)&&Object.isFrozen(packed.facts.parent_checkpoints)};
const empty=__arkdpsPackedCaptureRecord(new ArrayBuffer(176));
globalThis.packedEmpty=empty.facts.cold_revision===undefined&&empty.facts.scope_revision===undefined
 &&empty.facts.publication_revision===undefined&&empty.facts.parent_checkpoints.length===0&&empty.edge==='entry';
globalThis.packedInvalid=[rejects(()=>__arkdpsPackedCaptureRecord(new ArrayBuffer(175))),
 rejects(()=>__arkdpsPackedCaptureRecord(new ArrayBuffer(184)))];
`,c);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(packedProof)',c)),{
 ticket:'18446744073709551615',invocation:'9007199254740993',parent:'7',begin:'11',end:'12',
 birth:'134401659939984618',request:'19',hook:8,checkpoint:0,active:1,after:0,lane:2,thread:31,
 edge:'leave',closed:true,facts:{mode:3,scopeMode:4,scope:21,pub:22,cold:23,missing:false,
 parents:[{invocation_id:'9007199254741001',checkpoint:1}]},stableFacts:true,frozen:true});
assert.equal(c.packedEmpty,true);assert.deepEqual([...c.packedInvalid],[true,true]);
vm.runInContext(`
__arkdpsReplayBegin(nativeRecord());
globalThis.values=__arkdpsReplayPoint('body',()=>[ptr(4096).readU8(),ptr(4098).readU16(),ptr(4100).readU32(),ptr(4104).readS32(),ptr(4112).readU64().toString(),ptr(4120).readS64().toString(),ptr(4128).readFloat(),ptr(4112).readPointer().toString()]);
globalThis.copy=__arkdpsReplayWitness('body',()=>ptr(4096).readByteArray(4));new Uint8Array(copy)[0]=0;
globalThis.original=__arkdpsReplayPoint('body',()=>ptr(4096).readU8());
globalThis.good=__arkdpsReplayEnd();globalThis.cost={readCalls,scalarCalls};
globalThis.failureCases=[];
for(const code of [1,2,3,4,5,6,7,8,9,10]){fault=code;status=0;__arkdpsReplayBegin(nativeRecord());const rejected=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4096).readU8()));const summary=__arkdpsReplayEnd();failureCases.push({code,rejected,complete:summary.complete,unreadable:summary.unreadable_reads});}
fault=0;status=0;
__arkdpsReplayBegin(nativeRecord());globalThis.optional=__arkdpsReplayOptional('unreadable',()=>{throw Error('optional read ran');});globalThis.optionalEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.guardRejected=rejects(()=>__arkdpsReplayWitness('guard',()=>ptr(4096).readU8()));globalThis.guardEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.multiRejected=rejects(()=>__arkdpsReplayWitness('multi',()=>0));globalThis.multiEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(nativeRecord());globalThis.cpRejected=rejects(()=>__arkdpsReplayCheckpoint('body',1,()=>ptr(4096).readU8()));globalThis.cpEnd=__arkdpsReplayEnd();
status=0;__arkdpsReplayBegin(nativeRecord());leased=false;globalThis.expiredRejected=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4096).readU8()));globalThis.expiredEnd=__arkdpsReplayEnd();leased=true;
globalThis.badLease=rejects(()=>__arkdpsReplayBegin({...nativeRecord(),ticket:'18'}));
const savedRead=__nativeOwnedRead;globalThis.__nativeOwnedRead=undefined;globalThis.missingHelper=rejects(()=>__arkdpsReplayBegin(nativeRecord()));globalThis.__nativeOwnedRead=savedRead;
status=0;__arkdpsReplayBegin(nativeRecord());globalThis.savedSelection=__arkdpsReplay.native.__nativeOwnedRead===savedRead;globalThis.__nativeOwnedRead=()=>{throw Error('provider replaced');};__arkdpsReplayPoint('body',()=>ptr(4096).readByteArray(1));globalThis.pinnedEnd=__arkdpsReplayEnd();globalThis.__nativeOwnedRead=savedRead;
__arkdpsReplayBegin(nativeRecord());globalThis.rangeRejected=rejects(()=>ptr(0xffffffffffffffffn).readByteArray(4));globalThis.rangeEnd=__arkdpsReplayEnd();
globalThis.allHelpersRejected=[];for(const name of ['__nativeOwnedRead','__nativeOwnedScalar','__nativeOwnedStatus','__nativeOwnedPoint','__nativeOwnedLease']){const saved=globalThis[name];globalThis[name]=undefined;allHelpersRejected.push(rejects(()=>__arkdpsReplayBegin(nativeRecord())));globalThis[name]=saved;}
status=0;__arkdpsReplayBegin(nativeRecord());leased=false;globalThis.idleExpiredEnd=__arkdpsReplayEnd();leased=true;
const scalar=__nativeOwnedScalar;globalThis.__nativeOwnedScalar=()=>3;status=0;__arkdpsReplayBegin(nativeRecord());globalThis.badScalar=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4112).readU64()));globalThis.badScalarEnd=__arkdpsReplayEnd();globalThis.__nativeOwnedScalar=scalar;
globalThis.__nativeOwnedRead=()=>undefined;status=0;__arkdpsReplayBegin(nativeRecord());globalThis.noErrorUndefined=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4096).readByteArray(1)));globalThis.noErrorUndefinedEnd=__arkdpsReplayEnd();globalThis.__nativeOwnedRead=savedRead;
globalThis.__nativeOwnedRead=()=>new ArrayBuffer(2);status=0;__arkdpsReplayBegin(nativeRecord());globalThis.shortBytes=rejects(()=>__arkdpsReplayPoint('body',()=>ptr(4096).readByteArray(4)));globalThis.shortBytesEnd=__arkdpsReplayEnd();globalThis.__nativeOwnedRead=savedRead;
const point=__nativeOwnedPoint;globalThis.__nativeOwnedPoint=()=>0x03000007;status=0;__arkdpsReplayBegin(nativeRecord());globalThis.badPoint=rejects(()=>__arkdpsReplayWitness('body',()=>1));globalThis.badPointEnd=__arkdpsReplayEnd();globalThis.__nativeOwnedPoint=point;
status=0;__arkdpsReplayBegin(nativeRecord());globalThis.nativeCall=rejects(()=>NativeFunction(ptr(1),'uint',[])());globalThis.nativeCallEnd=__arkdpsReplayEnd();
status=0;__arkdpsReplayBegin(nativeRecord());status=9;globalThis.directPoisonEnd=__arkdpsReplayEnd();
globalThis.versionRejected=rejects(()=>__arkdpsReplayBegin({...nativeRecord(),version:2}));
status=0;const beforeMetadataReads=readCalls;__arkdpsReplayBegin(nativeRecord());globalThis.metadataMissing=rejects(()=>__arkdpsCapturedAssignable(ptr(1),ptr(2)));globalThis.metadataMissingEnd=__arkdpsReplayEnd();globalThis.metadataDidNotRead=readCalls===beforeMetadataReads;
globalThis.__arkdpsColdMetadataRead=(address,size)=>{if(address.toString()!=='0x5a'||size!==8)throw Error('unexpected immutable key');const owned=new ArrayBuffer(8);new DataView(owned).setBigUint64(0,1n,true);return owned;};status=0;__arkdpsReplayBegin(nativeRecord());globalThis.metadataAssignable=__arkdpsCapturedAssignable(ptr(1),ptr(2));globalThis.metadataGoodEnd=__arkdpsReplayEnd();
status=0;__arkdpsReplayBegin(nativeRecord());globalThis.badAddress=rejects(()=>__arkdpsOwnedRead('invalid',1));globalThis.badAddressEnd=__arkdpsReplayEnd();
globalThis.wrapValues=[ptr(-1n).toString(),ptr(0x10000000000000001n).toString(),ptr(-0x10000000000000001n).toString()];
globalThis.liveCount=live;
`,c);
assert.deepEqual(Array.from(c.values),[251,65000,0xfedcba98,-123456,'18364758544493064720','-9223372036854775807',-13.25,'0xfedcba9876543210']);
assert.equal(c.original,251);assert.equal(c.good.complete,true);assert.equal(c.cost.readCalls,1);assert.equal(c.cost.scalarCalls,9);
for(const r of c.failureCases){assert(r.rejected);assert.equal(r.complete,![1,2,3,9,10].includes(r.code));assert.equal(r.unreadable,[1,2,3,9,10].includes(r.code)?0:1);}
assert.equal(c.optional.available,false);assert.equal(c.optionalEnd.complete,true);assert(c.guardRejected);assert.equal(c.guardEnd.complete,true);
for(const key of ['multi','cp','expired','range']){assert(c[key+'Rejected']);assert.equal(c[key+'End'].complete,false);}
assert(c.badLease);assert(c.missingHelper);assert(c.savedSelection);assert.equal(c.pinnedEnd.complete,true);assert.equal(c.liveCount,0);
console.log('PASS_NATIVE_OWNED_FACADE fake backend only; seven scalar kinds/pointer/copy/status/points/lease/provider pin/no live fallback');

assert(Array.from(c.allHelpersRejected).every(Boolean));assert.equal(c.idleExpiredEnd.complete,false);for(const key of ['badScalar','noErrorUndefined','shortBytes','badPoint','nativeCall']){assert(c[key]);assert.equal(c[key+'End'].complete,false);}assert.equal(c.directPoisonEnd.complete,false);assert(c.versionRejected);

assert(c.metadataMissing);assert.equal(c.metadataMissingEnd.complete,false);assert(c.metadataDidNotRead);assert(c.metadataAssignable);assert.equal(c.metadataGoodEnd.complete,true);

assert(c.badAddress);assert.equal(c.badAddressEnd.complete,false);

assert.deepEqual(Array.from(c.wrapValues),['0xffffffffffffffff','0x1','0xffffffffffffffff']);
