"use strict";
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const c=vm.createContext({});vm.runInContext(`
let status=0,poisoned=false,live=0,observed=0,consumed=0,scalarCalls=0,mode='ok',boundMode=5,saved=[];
function __read(){live++;throw Error('LIVE');}function __nativeCall(){live++;throw Error('LIVE CALL');}function __pid(){return 1;}function __nativeActive(){return 0;}
function __nativeOwnedLease(ticket){return ticket==='17';}function __nativeOwnedStatus(){return status;}
function __nativeOwnedRead(){live++;throw Error('unexpected byte read');}function __nativeOwnedPoint(){throw Error('unexpected point read');}
function __nativeOwnedScalar(role,cp,field,address,kind){scalarCalls++;if(role!=='body'||cp!==3||field!==-1||address!==4096n||kind!==4)throw Error('trap witness');if(mode==='trap-conflict'){status=10;poisoned=true;return;}status=0;return 0xfedcba9876543210n;}
function record(mode=5,edge='entry',checkpoint=3){return {version:1,native_view:true,ticket:'17',edge,checkpoint,facts:{mode_epoch:mode}};}
function rejects(read){try{read();return false;}catch(error){return true;}}
`,c);vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),c);
vm.runInContext(`
const field=(phase,name,bytes,values,kind='bytes')=>({phase,name,role:name.startsWith('clock_')?name:'body',kind,bytes,...(kind==='utf16'?{max_units:200}:{}),observation:{name,values}});
const codec=(kind,offset=0)=>({kind,offset});
const contract=Object.freeze({mode_epoch:0,address:'0x9000',fields:[
field('entry','hp.identity',28,[codec('u64'),codec('u32',24)]),field('entry','hp.born',1,[codec('u8')]),field('entry','hp.before',8,[codec('i64')]),field('entry','hp.id',0,[codec('utf16')],'utf16'),
...['entry','leave'].flatMap(phase=>[field(phase,'clock_open',12,[codec('u32'),codec('i64',4)]),field(phase,'clock_close',12,[codec('u32'),codec('i64',4)])]),
field('leave','hp.closing',48,[codec('u32'),codec('i64',40)]),field('leave','attributes.count',4,[codec('u32')]),field('leave','attributes.slot0',20,[codec('u64'),codec('u64',8),codec('u8',16)])]});
__arkdpsReplayBegin(record());globalThis.oldProvider=__arkdpsOwnedHpObservation(contract,ptr(4096n));__arkdpsReplayEnd();
let fixtureContract=contract;
function nativeObservation(hook,root){
 observed++;poisoned=false;status=0;if(arguments.length!==2||hook!==36864n||root!==4096n){status=1;poisoned=true;return;}
 const r=__arkdpsReplayRecord(),fields=fixtureContract.fields.filter(f=>f.phase===r.edge),packet=[1,fields.length,r.edge==='entry'?0:1,3,boundMode];saved=[];
 for(const field of fields){
  const failed=field.name==='hp.id'?(mode==='missing'?65535:mode==='planned'?5:mode==='structural'?9:0):field.name==='attributes.slot0'&&mode==='unused-words'?9:0;saved.push(failed);packet.push(failed);
  const values=field.name==='hp.identity'?[0xfedcba9876543210n,73]:field.name==='hp.born'?[1]:field.name==='hp.before'?[-9223372036854775808n]:field.name==='hp.id'?['A\0\u4e2d\ud800B\udfffC\ud83d\ude00']:field.name==='clock_open'?[30,-4294967296n]:field.name==='clock_close'?(field.observation.values.length===1?[30]:[30,-4294967296n]):field.name==='hp.closing'?[73,-9223372036854775808n]:field.name==='attributes.count'?[0]:[18446744073709551615n,7n,1];
  packet.push(...values.map(value=>failed?undefined:value));
 }
 if(mode==='version')packet[0]=2;if(mode==='count')packet[1]++;if(mode==='cp')packet[3]=4;if(mode==='mode')packet[4]=6;if(mode==='phase')packet[2]^=1;
 if(mode==='bad-type')packet[6]=1;if(mode==='bad-status')packet[5]=77;if(mode==='long-string')packet[13]='x'.repeat(201);
 return Object.freeze(packet);
}
function nativeFailure(index){consumed++;if(!Number.isInteger(index)||index<0||index>=saved.length){status=1;poisoned=true;return;}status=saved[index]===65535?9:saved[index]||1;if([1,2,3,9,10].includes(status))poisoned=true;}
globalThis.__nativeOwnedHpObservation=nativeObservation;globalThis.__nativeOwnedConsumeHpFailure=nativeFailure;
const enemy=ptr(4096n);__arkdpsReplayBegin(record());const io=__arkdpsOwnedHpObservation(contract,enemy),beforeCalls=observed;
globalThis.good=[io.identity(enemy),io.born(enemy),io.hp(enemy),io.id(enemy),io.clock(),io.trapClass(enemy)];globalThis.successCalls=observed===beforeCalls&&consumed===0&&scalarCalls===1;globalThis.goodEnd=__arkdpsReplayEnd();
const legacyContract={...contract,fields:contract.fields.map(f=>f.name==='clock_close'?{...f,observation:{...f.observation,values:[codec('u32')]}}:f)};
fixtureContract=legacyContract;__arkdpsReplayBegin(record());globalThis.legacyClock=__arkdpsOwnedHpObservation(legacyContract,enemy).clock();globalThis.legacyClockEnd=__arkdpsReplayEnd();fixtureContract=contract;
globalThis.failures=[];
for(const failure of ['planned','structural','missing']){
 mode=failure;__arkdpsReplayBegin(record());const reader=__arkdpsOwnedHpObservation(contract,enemy);globalThis['before-'+failure]=!poisoned;
 const caught=rejects(()=>reader.id(enemy)),nativeFault=poisoned,summary=__arkdpsReplayEnd();failures.push({caught,nativeFault,summary});
}
mode='structural';__arkdpsReplayBegin(record());__arkdpsOwnedHpObservation(contract,enemy);globalThis.unusedFault=!poisoned;globalThis.unusedEnd=__arkdpsReplayEnd();
mode='ok';__arkdpsReplayBegin(record());const mutated=__arkdpsOwnedHpObservation(contract,enemy);enemy.value=0x9999n;globalThis.receiverMutation=rejects(()=>mutated.identity(enemy));globalThis.receiverEnd=__arkdpsReplayEnd();enemy.value=4096n;
__arkdpsReplayBegin(record());globalThis.expired=rejects(()=>io.identity(enemy));globalThis.expiredEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(record());const pinned=__arkdpsOwnedHpObservation;globalThis.__nativeOwnedHpObservation=()=>{throw Error('replacement');};globalThis.pinned=pinned(contract,enemy).born(enemy)===1;globalThis.pinnedEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(record());globalThis.replaced=rejects(()=>__arkdpsOwnedHpObservation(contract,enemy));globalThis.replacedEnd=__arkdpsReplayEnd();globalThis.__nativeOwnedHpObservation=nativeObservation;
boundMode=0;__arkdpsReplayBegin(record(0));globalThis.oldEpoch=__arkdpsOwnedHpObservation(contract,enemy).born(enemy)===1;globalThis.oldEpochEnd=__arkdpsReplayEnd();boundMode=5;
globalThis.malformed=[];
for(const bad of ['version','count','cp','mode','phase','bad-type','bad-status']){mode=bad;__arkdpsReplayBegin(record());const caught=rejects(()=>__arkdpsOwnedHpObservation(contract,enemy).identity(enemy));malformed.push({caught,summary:__arkdpsReplayEnd()});}
mode='long-string';__arkdpsReplayBegin(record());globalThis.longString=rejects(()=>__arkdpsOwnedHpObservation(contract,enemy).id(enemy));globalThis.longStringEnd=__arkdpsReplayEnd();
mode='ok';const shortContract={...contract,fields:contract.fields.map(field=>field.kind==='utf16'?{...field,max_units:4}:field)};__arkdpsReplayBegin(record());globalThis.shortString=rejects(()=>__arkdpsOwnedHpObservation(shortContract,enemy).id(enemy));globalThis.shortStringEnd=__arkdpsReplayEnd();
mode='unused-words';__arkdpsReplayBegin(record(5,'leave'));const leave=__arkdpsOwnedHpObservation(contract,enemy),attrs=leave.attributes(enemy);globalThis.countZero=leave.attributeCount(attrs)===0&&!poisoned;globalThis.leaveClock=leave.clock();globalThis.leaveClosing=leave.closing(enemy);globalThis.leaveEnd=__arkdpsReplayEnd();
mode='ok';__arkdpsReplayBegin(record(5,'leave'));const other=__arkdpsOwnedHpObservation(contract,enemy);globalThis.foreignAttrs=rejects(()=>other.attributeCount(attrs));globalThis.foreignAttrsEnd=__arkdpsReplayEnd();
mode='trap-conflict';__arkdpsReplayBegin(record());const trap=__arkdpsOwnedHpObservation(contract,enemy);globalThis.noEarlyTrap=!poisoned;globalThis.trapConflict=rejects(()=>trap.trapClass(enemy));globalThis.trapPoison=poisoned;globalThis.trapEnd=__arkdpsReplayEnd();
// Fake native issuing seam; actual identity/adoption is separately tested in CPP.
mode='ok';const rawFactory=nativeObservation;let issued,adoptions=0;
__nativeReplayHealthy=()=>!poisoned;
__nativeOwnedHpObservation=function(hook,root,packet){if(arguments.length===3){adoptions++;if(packet!==issued){status=1;poisoned=true;return;}return packet;}return rawFactory(hook,root);};
function packedHp(edge){const b=new ArrayBuffer(176),v=new DataView(b);for(const [i,n]of [[0,17],[7,5],[8,5],[13,3],[17,31],[18,edge]])v.setBigUint64(i*8,BigInt(n),true);return b;}
function issue(edge){__arkdpsReplayBegin(record(5,edge));issued=rawFactory(36864n,4096n);__arkdpsReplayEnd();return issued;}
const originalArguments=__arkdpsArguments;__arkdpsArguments=()=>{throw Error('direct must not make arguments');};
const originalFactory=__arkdpsOwnedHpObservation;__arkdpsOwnedHpObservation=()=>{throw Error('direct must not refetch');};
let callbackKeys;
const directE=__arkdpsReplayDispatch(packedHp(0),function(actor,input){callbackKeys=Object.keys(this);if(!__arkdpsIsPreparedHpObservation(input,contract,actor))throw Error('brand');this.write=input.identity(actor)[1];},undefined,4096n,1,undefined,undefined,issue('entry'),contract);
const directL=__arkdpsReplayDispatch(packedHp(1),function(input){if(!__arkdpsIsPreparedHpObservation(input,contract))throw Error('leave brand');this.closed=this.write;},directE[3],4096n,1,undefined,undefined,issue('leave'),contract);
__arkdpsReplayBegin(record());const unissued=new io.constructor(__arkdpsReplay,Object.freeze([1]),{},enemy,4096n,contract,true);globalThis.constructorForged=!__arkdpsIsPreparedHpObservation(unissued,contract,enemy);__arkdpsReplayEnd();
globalThis.directFacts={complete:directE[2]&&directL[2],same:directE[3]===directL[3],keys:callbackKeys,closed:directL[3].closed,adoptions};
issue('entry');globalThis.forgedDirect=rejects(()=>__arkdpsReplayDispatch(packedHp(0),()=>{},undefined,4096n,1,undefined,undefined,Object.freeze([...issued]),contract));
globalThis.badDirectMasks=[];for(const mask of [0,3,'1',NaN]){issue('entry');const caught=rejects(()=>__arkdpsReplayDispatch(packedHp(0),()=>{},undefined,4096n,mask,undefined,undefined,issued,contract));badDirectMasks.push(caught&&!__arkdpsReplayActive());}
// Bounded-loop simulator exercises the actual bridge dispatch. Native FIFO /
// controller ownership is separately enforced by engine, not this fake seam.
const batchHeader=packedHp(0),batchView=new DataView(batchHeader);let batchIndex=0,finalized=0,retainedIo,retainedRecord,retainedMode;
function batchAdvance(controller,previous){
 if(controller!==batchController)throw Error('controller');
 if(previous!==undefined){if(!previous[2])throw Error('failed item cannot finalize');finalized++;}
 if(batchIndex===2)return null;
 const edge=batchIndex++,header=packedHp(edge);new Uint8Array(batchHeader).set(new Uint8Array(header));
 const packet=issue(edge===0?'entry':'leave');
 const callback=edge===0?function(actor,input){retainedIo=input;retainedRecord=__arkdpsReplayRecord();retainedMode=__arkdpsReplayModeRecord();this.write=73;}:function(input){if(this.write!==73)throw Error('receiver');if(retainedRecord.edge!=='entry'||retainedMode.edge!=='entry')throw Error('escaped record mutated');};
 return [batchHeader,callback,edge?batchReceiver:undefined,4096n,1,undefined,undefined,packet,contract];
}
const batchController={};let batchReceiver;
const trackingAdvance=(controller,previous)=>{if(previous)batchReceiver=previous[3];return batchAdvance(controller,previous);};
globalThis.batchCount=__arkdpsReplayBatch(trackingAdvance,batchController,batchHeader,__arkdpsReplayDispatch);globalThis.batchFacts={finalized,edge:retainedRecord.edge,mode:retainedMode.facts.mode_epoch,active:__arkdpsReplayActive()};
// Old IO is rejected at item2 even though the same private header was reused.
batchIndex=0;finalized=0;batchReceiver=undefined;
function faultAdvance(controller,previous){const item=trackingAdvance(controller,previous);if(item&&batchIndex===2)item[1]=function(){try{retainedIo.identity(enemy);}catch(ignored){}};return item;}
globalThis.batchFault=rejects(()=>__arkdpsReplayBatch(faultAdvance,batchController,batchHeader,__arkdpsReplayDispatch));globalThis.batchFaultFinalized=finalized;globalThis.batchFaultActive=__arkdpsReplayActive();
__arkdpsArguments=originalArguments;__arkdpsOwnedHpObservation=originalFactory;
globalThis.liveCount=live;
`,c);
assert.equal(c.oldProvider,null);assert(c.successCalls&&c.goodEnd.complete);assert.equal(c.good[0][0],0xfedcba9876543210n);assert.equal(c.good[0][1],73);assert.equal(c.good[1],1);assert.equal(c.good[2],-9223372036854775808n);assert.equal(c.good[3],'A\0中\ud800B\udfffC\ud83d\ude00');assert.deepEqual(Array.from(c.good[4]),[30,-4294967296n,30,-4294967296n]);
assert.deepEqual(Array.from(c.legacyClock),[30,-4294967296n,30]);assert(c.legacyClockEnd.complete);
assert(c['before-planned']&&c['before-structural']&&c['before-missing']);assert(c.failures[0].caught&&!c.failures[0].nativeFault&&c.failures[0].summary.complete);assert.equal(c.failures[0].summary.unreadable_reads,1);
for(const i of [1,2])assert(c.failures[i].caught&&c.failures[i].nativeFault&&!c.failures[i].summary.complete);
assert(c.unusedFault&&c.unusedEnd.complete);assert(c.receiverMutation&&!c.receiverEnd.complete);assert(c.expired&&!c.expiredEnd.complete);assert(c.pinned&&c.pinnedEnd.complete);assert(c.replaced&&!c.replacedEnd.complete);assert(c.oldEpoch&&c.oldEpochEnd.complete);
assert(Array.from(c.malformed).every(row=>row.caught&&!row.summary.complete));assert(c.longString&&!c.longStringEnd.complete);assert(c.shortString&&!c.shortStringEnd.complete);assert(c.countZero&&c.leaveEnd.complete);assert.deepEqual(Array.from(c.leaveClosing),[73,-9223372036854775808n]);assert(c.foreignAttrs&&!c.foreignAttrsEnd.complete);assert(c.noEarlyTrap&&c.trapConflict&&c.trapPoison&&!c.trapEnd.complete);assert.equal(c.liveCount,0);
assert(c.directFacts.complete&&c.directFacts.same&&c.directFacts.closed===73&&c.directFacts.adoptions===2);assert.deepEqual(Array.from(c.directFacts.keys),['threadId']);assert(c.forgedDirect&&c.constructorForged);assert.equal(c.batchCount,2);assert.equal(c.batchFacts.finalized,2);assert.equal(c.batchFacts.edge,"entry");assert.equal(c.batchFacts.mode,5);assert.equal(c.batchFacts.active,false);assert(c.batchFault&&c.batchFaultFinalized===1&&!c.batchFaultActive);assert(Array.from(c.badDirectMasks).every(Boolean));
console.log('PASS_RAW_HP_JS provider pin/primitive types/deferred structural+planned/mode/cp/private receiver/lease/trap/count0/no live');
