"use strict";
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const bridge=fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8');
function fixture(options={}){
 const c=vm.createContext({});vm.runInContext(`
 let leased=true,status=0,live=0,leaseCalls=0,scalarCalls=0,descriptorCalls=0,accessors=0,uid=73,factStatus=0,poisoned=false,observations=0;
 const realDescriptor=Object.getOwnPropertyDescriptor;Object.getOwnPropertyDescriptor=function(object,key){descriptorCalls++;return realDescriptor(object,key);};
 function __read(){live++;throw Error('LIVE');}function __nativeCall(){live++;throw Error('LIVE CALL');}function __pid(){return 1;}function __nativeActive(){return 0;}
 function __nativeOwnedLease(ticket){leaseCalls++;return leased&&ticket==='17';}
 function __nativeOwnedRead(role,cp,field,address,size){return new ArrayBuffer(size);}
 function __nativeOwnedScalar(role,cp,field,address,kind){scalarCalls++;if(typeof address!=='number'||address!==4096||kind!==2)throw Error('numeric capability');return uid;}
 function __nativeOwnedStatus(){return status;}function __nativeOwnedPoint(){return 1<<24;}
 function __nativeOwnedHpObservation(){observations++;status=0;return Object.freeze([1,1,0,3,5,factStatus,factStatus?undefined:4096n,factStatus?undefined:uid]);}
 function __nativeOwnedConsumeHpFailure(index){status=index===0?factStatus||1:1;poisoned=[1,2,3,9,10].includes(status);}
 function record(ticket='17',checkpoint=3){return {version:1,native_view:true,edge:'entry',ticket,checkpoint,facts:{mode_epoch:5}};}
 function rejects(read){try{read();return false;}catch(error){return true;}}
 `,c);
 vm.runInContext(bridge,c);c.options=options;
 vm.runInContext(`
 const required=['__nativeOwnedRead','__nativeOwnedScalar','__nativeOwnedStatus','__nativeOwnedPoint','__nativeOwnedLease'];
 const optional=['__nativeOwnedHpObservation','__nativeOwnedConsumeHpFailure'];
 const bundle=Object.create(options.prototype?{}:null),values={version:options.version??1,__nativeOwnedNumericAddress:options.numeric??true};
 for(const name of [...required,...optional])if(name!==options.missing)values[name]=globalThis[name];
 if(options.graphPair)values.__nativeOwnedGraphCheck=()=>{};
 if(options.extra)values.extra=1;
 for(const [name,value]of Object.entries(values)){
  if(options.memberAccessor&&name==='__nativeOwnedRead')Object.defineProperty(bundle,name,{get(){accessors++;return value;},configurable:false});
  else Object.defineProperty(bundle,name,{value,writable:options.memberWritable&&name==='__nativeOwnedRead',configurable:options.memberConfigurable&&name==='__nativeOwnedRead',enumerable:true});
 }
 if(!options.extensible)Object.preventExtensions(bundle);
 if(options.globalAccessor)Object.defineProperty(globalThis,'__nativeOwnedProvider',{get(){accessors++;return bundle;},configurable:false});
 else Object.defineProperty(globalThis,'__nativeOwnedProvider',{value:bundle,writable:!!options.globalWritable,configurable:!!options.globalConfigurable});
 `,c);return c;
}
for(const options of [{globalAccessor:true},{globalWritable:true},{globalConfigurable:true},{memberAccessor:true},{memberWritable:true},{memberConfigurable:true},{extensible:true},{prototype:true},{version:2},{numeric:false},{missing:'__nativeOwnedLease'},{missing:'__nativeOwnedConsumeHpFailure'},{graphPair:true},{extra:true}]){
 const c=fixture(options);vm.runInContext('globalThis.rejected=rejects(()=>__arkdpsReplayBegin(record()));globalThis.getterCalls=accessors',c);assert(c.rejected,JSON.stringify(options));assert.equal(c.getterCalls,0,'never execute accessors');
}
const c=fixture();vm.runInContext(`
__arkdpsReplayBegin(record());globalThis.first=__arkdpsReplayPoint('body',()=>ptr(4096n).readU32());globalThis.firstEnd=__arkdpsReplayEnd();globalThis.firstValidation=descriptorCalls;
const installed=bundle;globalThis.globalSetRejected=rejects(()=>Object.defineProperty(globalThis,'__nativeOwnedProvider',{value:{}}));globalThis.memberSetRejected=rejects(()=>Object.defineProperty(bundle,'__nativeOwnedRead',{value:()=>{throw Error('replacement');}}));
globalThis.prototypeRejected=rejects(()=>Object.setPrototypeOf(bundle,{}));
globalThis.__nativeOwnedScalar=()=>{throw Error('GLOBAL REPLACEMENT');};globalThis.__nativeOwnedNumericAddress=false;uid=74;
__arkdpsReplayBegin(record());globalThis.second=__arkdpsReplayPoint('body',()=>ptr(4096n).readU32());globalThis.secondEnd=__arkdpsReplayEnd();globalThis.cachedValidation=descriptorCalls===firstValidation;globalThis.leaseCount=leaseCalls;
leased=false;globalThis.__nativeOwnedLease=()=>true;globalThis.badLease=rejects(()=>__arkdpsReplayBegin(record()));leased=true;
globalThis.badTicket=rejects(()=>__arkdpsReplayBegin(record('bad')));globalThis.badCheckpoint=rejects(()=>__arkdpsReplayBegin(record('17',-1)));
__arkdpsReplayBegin(record());leased=false;globalThis.endLease=__arkdpsReplayEnd();leased=true;
const contract={address:'0x9000',mode_epoch:0,fields:[{phase:'entry',name:'hp.identity',role:'body',kind:'bytes',bytes:28,observation:{name:'hp.identity',values:[{kind:'u64',offset:0},{kind:'u32',offset:24}]}}]};
globalThis.__nativeOwnedHpObservation=()=>{throw Error('GLOBAL HP REPLACEMENT');};globalThis.__nativeOwnedConsumeHpFailure=()=>{};globalThis.__nativeOwnedStatus=()=>0;
factStatus=9;__arkdpsReplayBegin(record());const io=__arkdpsOwnedHpObservation(contract,ptr(4096n));globalThis.unused=!poisoned;globalThis.failed=rejects(()=>io.identity(ptr(4096n)));globalThis.nativePoison=poisoned;globalThis.failedEnd=__arkdpsReplayEnd();
factStatus=5;poisoned=false;__arkdpsReplayBegin(record());globalThis.localFailed=rejects(()=>__arkdpsOwnedHpObservation(contract,ptr(4096n)).identity(ptr(4096n)));globalThis.localPoison=poisoned;globalThis.localEnd=__arkdpsReplayEnd();
factStatus=0;__arkdpsReplayBegin(record());const latest=__arkdpsOwnedHpObservation(contract,ptr(4096n));globalThis.latest=latest.identity(ptr(4096n))[1];globalThis.latestEnd=__arkdpsReplayEnd();
globalThis.liveCount=live;
`,c);
assert.equal(c.first,73);assert.equal(c.second,74);assert(c.firstEnd.complete&&c.secondEnd.complete);assert(c.globalSetRejected&&c.memberSetRejected&&c.prototypeRejected);assert(c.cachedValidation);assert.equal(c.leaseCount,4);assert(c.badLease&&c.badTicket&&c.badCheckpoint);assert(!c.endLease.complete);
assert(c.unused&&c.failed&&c.nativePoison&&!c.failedEnd.complete);assert(c.localFailed&&!c.localPoison&&c.localEnd.complete);assert.equal(c.localEnd.unreadable_reads,1);assert.equal(c.latest,74);assert(c.latestEnd.complete);assert.equal(c.liveCount,0);
console.log('PASS_SEALED_PROVIDER readonly data ownership/cache + per-record lease/shape + deferred HP + no live');
