// Actual production queue/callback functions in owned memory; no live client.
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),vm=require('node:vm');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const sourcePath=path.join(__dirname,'..','event_probe_native_pc.js');
const json=value=>JSON.parse(JSON.stringify(value));
function heal(f){
 const source=f.unit('char_owned_source',10),target=f.unit('char_owned_target',11);
 f.write(target.add(0x40),10n*4294967296n);
 const mod=f.modifier(source,target,5,5,4),hook=f.callback('ApplyModifier',f.entity),call={threadId:1};
 hook.onEnter.call(call,[target,mod,f.ptr(0)]);f.write(target.add(0x40),15n*4294967296n);hook.onLeave.call(call);
}
function slot(f){return f.vm.runInContext('nativeControllerClass.add(0x58).readPointer().add(0xb8).readPointer()',f.context);}
function make(){
 const f=fixture({sourcePath});f.clock(30,4294967296n);heal(f);
 assert.equal(f.messages.at(-1).terminal_read_version,1);
 assert.equal(f.context.rpc.exports.mark().terminal_read_version,1);
 f.context.rpc.exports.readEvents(0,1);assert.equal(f.context.rpc.exports.ackEvents(1).acknowledged,1);
 heal(f);heal(f);heal(f);f.write(f.controller.add(0x228),3);
 return f;
}
function destroy(f,{afterEnter}={}){
 const hook=f.callback('OnDestroy',f.controllerClass),call={threadId:1};
 hook.onEnter.call(call,[f.controller]);if(afterEnter)afterEnter();hook.onLeave.call(call);
 return f.context.rpc.exports.mark();
}
let cases=0;
{
 const f=make();assert.equal(f.context.rpc.exports.readTerminalEvents(1,4).retry,true);
 assert(f.context.rpc.exports.ackEvents(4).error);cases++;
}
{
 const f=make(),mark=destroy(f,{afterEnter:()=>{f.write(slot(f),f.ptr(0));f.write(f.controller.add(0x10),f.ptr(0));}});
 assert.equal(mark.retry,true);assert(mark.terminal_boundary);assert.equal(mark.terminal_read_version,1);
 const b=mark.terminal_boundary;
 assert.equal(b.native_frame,30);assert.equal(b.native_fixed_time,'4294967296');
 assert.equal(b.native_stage_id,'Obt/Weekly/level_weekly_melee_5');assert.equal(b.native_process_pid,1234);
 assert.equal(b.native_identity.controller,b.controller);assert.equal(b.native_identity.stats,b.stats);
 assert.equal(f.context.rpc.exports.readEvents(1,4).retry,true,'ordinary read stays unavailable');
 assert(f.context.rpc.exports.ackEvents(4).error,'normal retry did not authorize ACK');
 assert.equal(f.context.rpc.exports.readTerminalEvents(1,3).retry,true);
 assert(f.context.rpc.exports.ackEvents(4).error,'wrong terminal window did not authorize ACK');
 const tail=f.context.rpc.exports.readTerminalEvents(1,4);
 assert.equal(tail.retry,false);assert.equal(tail.terminal_only,true);assert.equal(tail.error,null);
 assert.equal(tail.after,1);assert.equal(tail.through,4);assert.equal(tail.active,0);
 assert.deepEqual(Array.from(tail.active_hooks),[]);assert.deepEqual(Array.from(tail.events,e=>e.sequence),[2,3,4]);
 assert.deepEqual(json(tail.native_identity),json(b.native_identity));assert.deepEqual(json(tail.terminal_boundary),json(b));
 assert.deepEqual(json(f.context.rpc.exports.readTerminalEvents(1,4)),json(tail),'retained read is replayable');
 assert.equal(f.context.rpc.exports.ackEvents(4).acknowledged,4);
 assert.equal(f.context.rpc.exports.readTerminalEvents(1,4).retry,true,'already ACKed data is not manufactured');
 assert.equal(f.context.rpc.exports.readTerminalEvents(4,4).events.length,0);cases++;
}
{
 const f=make();destroy(f);f.write(f.controller.add(0x10),f.ptr(0));
 assert.equal(f.context.rpc.exports.readEvents(1,4).retry,true);
 assert.equal(f.context.rpc.exports.readTerminalEvents(1,4).events.length,3,'cleared destruction root is historical only');cases++;
}
{
 const f=make();f.write(f.controller.add(0x228),1);const mark=destroy(f);
 assert.equal(mark.retry,true);assert(mark.terminal_boundary,'missing full binding does not erase the old raw final fence');
 assert.equal(mark.terminal_boundary.native_identity,undefined);
 assert.equal(f.context.rpc.exports.readTerminalEvents(1,4).retry,true,'raw-only final cannot authorize tail reads');
 assert(f.context.rpc.exports.ackEvents(4).error);cases++;
}
for(const [change,mutate]of [
 ['clock',f=>f.clock(31,4438132736n)],
 ['fixed',f=>f.clock(30,4294967297n)],
 ['generation',f=>f.vm.runInContext('nativeGeneration++',f.context)],
 ['scope',f=>f.vm.runInContext('resetDamageSettlementScope()',f.context)],
 ['new_singleton',f=>f.write(slot(f),f.alloc())],
 ['replaced_root',f=>f.write(f.controller.add(0xe0),f.alloc())],
 ['stage',f=>f.write(f.level.add(0x18),f.managedString('Owned/NewStage'))],
 ['active',f=>f.vm.runInContext('active++;activeHooks.set("owned",1)',f.context)],
 ['epoch',f=>f.vm.runInContext('epoch++',f.context)],
 ['event',f=>heal(f)],
 ['fatal',f=>f.vm.runInContext('fatal="owned failure"',f.context)],
 ['stopped',f=>f.context.rpc.exports.stop()],
 ['queue_gap',f=>f.vm.runInContext('queue.splice(2,1)',f.context)],
 ['queue_sequence',f=>f.vm.runInContext('queue[1].sequence=99',f.context)],
 ['queue_other_scope',f=>f.vm.runInContext('queueSettlementScopes.set(queue[1],damageSettlementScope-1)',f.context)],
 ['queue_unknown_scope',f=>f.vm.runInContext('queueSettlementScopes.delete(queue[1])',f.context)],
 ['queue_future_clock',f=>f.vm.runInContext('queue[1].frame=31',f.context)],
 ['unreadable_singleton_slot',f=>f.write(f.controllerClass.add(0x58),f.alloc())],
]){
 const f=make();destroy(f);mutate(f);const rejected=f.context.rpc.exports.readTerminalEvents(1,4);
 assert.equal(rejected.retry,true,change);assert.equal(rejected.events.length,0,change);
 assert(f.context.rpc.exports.ackEvents(4).error,change+' cannot authorize ACK');cases++;
}
for(const range of [[-1,4],[0.5,4],[1,3],[1,5],[5,4],[1,'4'],[null,4]]){
 const f=make();destroy(f);assert.equal(f.context.rpc.exports.readTerminalEvents(...range).retry,true);
 assert(f.context.rpc.exports.ackEvents(4).error);cases++;
}
{
 const f=make();destroy(f);f.clock(31,4438132736n);
 assert.equal(f.context.rpc.exports.readTerminalEvents(1,4).retry,true);f.clock(30,4294967296n);
 assert.equal(f.context.rpc.exports.readTerminalEvents(1,4).retry,true,'a rejected clock cannot resurrect an old fence');cases++;
}
{
 const f=make();destroy(f);f.write(slot(f),f.ptr(0));
 const respond=f.requests.get('arkdps-event-request');
 respond({id:7,operation:'terminal_read',after:1,through:4});
 const reply=f.messages.at(-1);assert.equal(reply.id,7);assert.equal(reply.result.terminal_only,true);
 assert.equal(reply.result.events.length,3);assert.equal(reply.result.retry,false);cases++;
}
{
 const f=fixture({platform:'android',sourcePath:path.join(__dirname,'..','event_probe_android2791.js')});
 assert.equal(f.messages.at(-1).terminal_read_version,undefined);
 assert.equal(f.context.rpc.exports.mark().terminal_read_version,undefined);
 assert.equal(f.context.rpc.exports.readTerminalEvents(0,0).terminal_read_rejection,'unsupported');cases++;
}
{
 let nativeActive=0,calls=0;const ctx={__pid:()=>1234,__nativeActive:()=>nativeActive};vm.createContext(ctx);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'..','native_pc/native_event_runtime/bridge.js'),'utf8'),ctx);
 ctx.read=()=>{calls++;return {events:[{sequence:4}],hp_context:[{}],active:0,active_hooks:[],retry:false};};
 vm.runInContext('rpc.exports={readTerminalEvents:()=>read(),mark:()=>({active:0}),ackEvents:()=>({acknowledged:4})}',ctx);
 function request(){return JSON.parse(vm.runInContext('__arkdpsRequest(JSON.stringify({operation:"terminal_read",after:1,through:4}))',ctx));}
 nativeActive=1;const busy=request();assert.equal(busy.retry,true);assert.equal(busy.active,1);assert.equal(calls,0);
 assert.deepEqual(busy.events,[]);assert.equal(busy.after,1);assert.equal(busy.through,4);
 nativeActive=0;assert.equal(request().events.length,1);assert.equal(calls,1);
 ctx.read=()=>{calls++;nativeActive=2;return {events:[{sequence:4}],hp_context:[{}],active:0,retry:false};};
 const raced=request();assert.equal(raced.retry,true);assert.equal(raced.active,2);assert.deepEqual(raced.events,[]);
 assert.deepEqual(raced.hp_context,[]);cases++;
}
console.log(JSON.stringify({owned_terminal_read_cases:cases,live_game:false}));
