"use strict";
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const c=vm.createContext({});vm.runInContext(`
let status=0,poisoned=false,issued=null,issues=0,adoptions=0,mode='ok';
function __read(){throw Error('LIVE');}function __pid(){return 1;}function __nativeActive(){return 0;}
function __nativeOwnedLease(t){return t==='17';}function __nativeOwnedStatus(){return status;}
function __nativeOwnedRead(){throw Error('unexpected read');}function __nativeOwnedScalar(){throw Error('unexpected scalar');}function __nativeOwnedPoint(){throw Error('unexpected point');}
function __nativeOwnedGraphFailure(){return 1;}
globalThis.__nativeOwnedGraphTypedPackets=1;
function __nativeOwnedGraphCheck(kind,rev,epoch,format,packet){
 if(kind!=='scope'||rev!==7||epoch!==5){poisoned=true;status=1;return;}
 if(format===2){adoptions++;if(packet!==issued||mode==='reject'){poisoned=true;status=1;return;}if(mode==='bool')return true;if(mode==='throw')throw Error('provider');return packet;}
 if(format!==1)throw Error('format');issues++;status=0;
 const p=[1,2,3,5,1,2,4294967295,-1];if(mode==='header')p[2]=4;if(mode==='length')p.push(1);
 Object.defineProperty(p,'__arkdpsGraphLayout',{value:'v1|2|1:8:2:u32@0:i32@4'});issued=Object.freeze(p);if(mode==='copy'){const copy=[...p];Object.defineProperty(copy,'__arkdpsGraphLayout',{value:p.__arkdpsGraphLayout});return Object.freeze(copy);}return issued;
}
function record(){return {version:1,native_view:true,ticket:'17',edge:'leave',checkpoint:3,facts:{mode_epoch:5}};}
function reject(fn){try{fn();return false;}catch(error){return true;}}
`,c);vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),c);
vm.runInContext(`
const graph={revision:7,mode_epoch:5,fields:[{root:'absolute',kind:'bytes',bytes:4,role:'scope.static',projection:false,comparison:{version:1,words:[{offset:0,kind:'u32',value:2}]}},{root:'absolute',kind:'bytes',bytes:8,role:'scope.int',projection:true,observation:{values:[{kind:'u32',offset:0},{kind:'i32',offset:4}]}}]};
__arkdpsReplayBegin(record());const io=__arkdpsOwnedGraphPrimitives('scope',graph);globalThis.good=__arkdpsVerifiedGraphPrimitives(io,'scope',graph)&&io.values(1)[1]===-1&&adoptions===1&&issues===1;globalThis.goodEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(record());globalThis.oldRejected=reject(()=>io.values(1));globalThis.oldEnd=__arkdpsReplayEnd();
globalThis.bad=[];for(mode of ['reject','bool','throw','header','length','copy']){__arkdpsReplayBegin(record());const caught=reject(()=>__arkdpsOwnedGraphPrimitives('scope',graph));bad.push({mode,caught,end:__arkdpsReplayEnd()});}
globalThis.layoutClones=[];
for(const change of [f=>({...f,bytes:12}),f=>({...f,observation:{values:[{kind:'u32',offset:0},{kind:'u32',offset:4}]}}),f=>({...f,observation:{values:[{kind:'u32',offset:4},{kind:'i32',offset:0}]}})]){
 mode='ok';__arkdpsReplayBegin(record());const clone={...graph,fields:[graph.fields[0],change(graph.fields[1])]};const caught=reject(()=>__arkdpsOwnedGraphPrimitives('scope',clone));layoutClones.push(caught&&!__arkdpsReplayEnd().complete);
}
mode='ok';__arkdpsReplayBegin(record());const shifted={...graph,fields:[graph.fields[1],graph.fields[0]]};globalThis.layoutIndex=reject(()=>__arkdpsOwnedGraphPrimitives('scope',shifted));__arkdpsReplayEnd();
mode='ok';__arkdpsReplayBegin(record());const fake=new io.constructor();globalThis.constructorRejected=!__arkdpsVerifiedGraphPrimitives(fake,'scope',graph)&&reject(()=>fake.values(1));__arkdpsReplayEnd();
`,c);
assert(Array.from(c.layoutClones).every(Boolean)&&c.layoutIndex);
assert(c.good&&c.goodEnd.complete&&c.oldRejected&&!c.oldEnd.complete&&c.constructorRejected);
for(const row of c.bad){assert(row.caught,row.mode);assert(!row.end.complete,row.mode);}
console.log('PASS_GRAPH_TYPED_ADOPTION pinned issuer recognition/no boolean certificate/no fallback/header/length/lease/constructor');

