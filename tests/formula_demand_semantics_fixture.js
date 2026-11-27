'use strict';
// Actual shared reducers over owned synthetic fixture memory; no game attachment.
const assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const results=[];
for(const platform of ['native_pc','android2791']){
 const f=fixture({platform:platform==='native_pc'?'native_pc':'mumu',sourcePath:path.resolve(platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
 f.installSyntheticMitigation({noEvent:true,factories:2});
 const plan=f.context.syntheticMitigationPlan,source=f.unit('char_147_shining',10),target=f.unit('enemy_formula_demand',12),attack=f.alloc();
 f.write(attack.add(plan.damageType),1);f.clock(30,4294967296n);
 const outer=f.hookAt(plan.outer),internal=f.hookAt(plan.internal),final=f.hookAt(plan.final);
 const a={threadId:1},b={threadId:1},c={threadId:1};
 // A formula pair needs time and its exact actor IDs, never a per-hit scope stamp.
 vm.runInContext('globalThis.formulaDemandStampCalls=0;globalThis.savedDemandStamp=probeAdapter.stampClock;probeAdapter.stampClock=()=>{formulaDemandStampCalls++;throw Error("formula must not request scope");}',f.context);
 outer.onEnter.call(a,[target,source,f.ptr(1),f.ptr(0),attack]);
 assert(a.formula,'actual formula entry admitted');
 // Internal merely borrows exact argument pointers: neither clock nor attack type is read.
 const proto=Object.getPrototypeOf(attack),saved={};
 const readNames=Object.getOwnPropertyNames(proto).filter(n=>n.startsWith('read')&&typeof proto[n]==='function');
 function noReads(fn){let attempted=0;try{for(const n of readNames){saved[n]=proto[n];proto[n]=()=>{attempted++;throw Error('unexpected formula demand read '+n);};}fn();assert.equal(attempted,0,'even caught memory reads violate the zero-read edge');}finally{for(const n of readNames)proto[n]=saved[n];}}
 noReads(()=>internal.onEnter.call(b,[attack,f.ptr(0),f.ptr(0),target]));
 assert.equal(b.scope,a.formula,'internal pointer admission survives zero memory reads');
 final.onEnter.call(c,[attack]);
 noReads(()=>final.onLeave.call(c,f.ptr(80n*4294967296n)));
 assert.equal(a.formula.before,80,'normal final result needs no leave memory');
 noReads(()=>internal.onLeave.call(b));
 outer.onLeave.call(a,f.ptr(40n*4294967296n));
 assert.equal(vm.runInContext('mitigationCounts.formula_candidates',f.context),1);
 assert.equal(f.context.formulaDemandStampCalls,0,'formula never stamps full event scope');
 // Independent negative: same pointers and values across a changed frame cannot pair.
 const d={threadId:2},e={threadId:2};
 outer.onEnter.call(d,[target,source,f.ptr(1),f.ptr(0),attack]);final.onEnter.call(e,[attack]);final.onLeave.call(e,f.ptr(80n*4294967296n));
 f.clock(31,4294967296n);outer.onLeave.call(d,f.ptr(40n*4294967296n));
 assert.equal(vm.runInContext('mitigationCounts.formula_candidates',f.context),1,'cross-frame candidate rejected');
 vm.runInContext('probeAdapter.stampClock=savedDemandStamp',f.context);
 results.push({platform,internal_zero_reads:true,normal_final_leave_zero_reads:true,scope_calls:0,cross_frame_rejected:true});
}
console.log(JSON.stringify({formula_demand:results,live_game:false}));
