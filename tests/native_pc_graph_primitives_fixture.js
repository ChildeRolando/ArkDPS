"use strict";
// Fake-provider protocol tests; actual CPP graph/read executor is tested separately.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const c=vm.createContext({});vm.runInContext(`
let status=0,live=0,mode='ok',calls=0;function __read(){live++;throw Error('LIVE');}function __pid(){return 1;}function __nativeActive(){return 0;}
function __nativeOwnedLease(t){return t==='17';}function __nativeOwnedStatus(){return status;}
function __nativeOwnedRead(){live++;throw Error('read forbidden');}function __nativeOwnedScalar(){live++;throw Error('scalar forbidden');}function __nativeOwnedPoint(){live++;throw Error('point forbidden');}
function __nativeOwnedGraphFailure(){return 1;}
function __nativeOwnedGraphCheck(kind,revision,epoch,format){calls++;if(kind!=='scope'||revision!==7||epoch!==5||format!==1)throw Error('ABI');if(mode==='guard'){status=8;return;}if(mode==='missing'){status=9;return;}
 const p=[1,2,3,5,1,4,4294967295,-1,18446744073709551615n,-9223372036854775808n];if(mode==='phase')p[2]=0;if(mode==='sign')p[7]=4294967295;if(mode==='wide')p[8]=18446744073709551616n;if(mode==='extra')p.push(1);return Object.freeze(p);}
function rec(){return {version:1,native_view:true,ticket:'17',edge:'leave',checkpoint:3,facts:{mode_epoch:5}};}function reject(fn){try{fn();return false;}catch(e){return true;}}
`,c);vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8'),c);
vm.runInContext(`
const field={root:'absolute',kind:'bytes',bytes:24,role:'scope.class',projection:true,observation:{values:[{kind:'u32',offset:0},{kind:'i32',offset:4},{kind:'u64',offset:8},{kind:'i64',offset:16}]}};
const graph={revision:7,mode_epoch:5,fields:[{root:'absolute',kind:'bytes',bytes:4,role:'scope.static',projection:false,comparison:{version:1,words:[{offset:0,kind:'u32',value:2}]}},field]};
__arkdpsReplayBegin(rec());const io=__arkdpsOwnedGraphPrimitives('scope',graph);globalThis.good=__arkdpsVerifiedGraphPrimitives(io,'scope',graph);globalThis.values=io.values(1);globalThis.staticRejected=reject(()=>io.values(0));globalThis.staticEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(rec());globalThis.oldRejected=reject(()=>io.values(1));globalThis.oldEnd=__arkdpsReplayEnd();
__arkdpsReplayBegin(rec());const forged=new io.constructor({format:1,replay:__arkdpsReplay,kind:'scope',graph});globalThis.forged=!__arkdpsVerifiedGraphPrimitives(forged,'scope',graph)&&reject(()=>forged.values(1));__arkdpsReplayEnd();
globalThis.bad=[];for(const m of ['phase','sign','wide','extra','missing','guard']){mode=m;status=0;__arkdpsReplayBegin(rec());const caught=reject(()=>__arkdpsOwnedGraphPrimitives('scope',graph));bad.push({m,caught,end:__arkdpsReplayEnd()});}
mode='ok';__arkdpsReplayBegin(rec());const unsupported={...graph,fields:[{...field,observation:{values:[{kind:'float',offset:0}]}}]};const count=calls;globalThis.unsupported=__arkdpsOwnedGraphPrimitives('scope',unsupported)===null&&calls===count;__arkdpsReplayEnd();globalThis.live=live;
`,c);
assert(c.good&&c.staticRejected&&!c.staticEnd.complete&&c.oldRejected&&!c.oldEnd.complete&&c.forged&&c.unsupported);assert.deepEqual(Array.from(c.values),[4294967295,-1,18446744073709551615n,-9223372036854775808n]);assert(Object.isFrozen(c.values));for(const row of c.bad){assert(row.caught,row.m);assert.equal(row.end.complete,row.m==='guard',row.m);}assert.equal(c.live,0);console.log('PASS_GRAPH_PRIMITIVES fake provider ABI/typed signs/widths/brands/lease/static unsupported/no live');
