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
 ['queue_sequence',f=>f.vm.runInContext('queue[1]={...queue[1],sequence:99}',f.context)],
 ['queue_other_scope',f=>f.vm.runInContext('queueSettlementScopes.set(queue[1],damageSettlementScope-1)',f.context)],
 ['queue_unknown_scope',f=>f.vm.runInContext('queueSettlementScopes.delete(queue[1])',f.context)],
 ['queue_future_clock',f=>f.vm.runInContext('const old=queue[1];queue[1]={...old,frame:31};queueSettlementScopes.set(queue[1],queueSettlementScopes.get(old))',f.context)],
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
 nativeActive=1;assert.throws(request,/publication journal/);assert.equal(calls,0);
 nativeActive=0;assert.throws(request,/publication journal/);assert.equal(calls,0);
 ctx.read=()=>{calls++;nativeActive=2;return {events:[{sequence:4}],hp_context:[{}],active:0,retry:false};};
 assert.throws(request,/publication journal/);assert.equal(calls,0);cases++;
 // The retired live mark cannot bypass the immutable publication dispatcher.
 nativeActive=0;
 ctx.mark=()=>{nativeActive=1;return {active:0,retry:false,sequence:4};};
 vm.runInContext('rpc.exports.mark=()=>mark()',ctx);
 assert.throws(()=>vm.runInContext('__arkdpsRequest(JSON.stringify({operation:"mark"}))',ctx),/publication journal/);
 assert.equal(nativeActive,0);
}
console.log(JSON.stringify({owned_terminal_read_cases:cases,live_game:false}));

// The closed publication must use the same prepared Destroy callback and replay
// lease as the source prefix. These are strict JS-owned bytes, not native/game.
{
 const f=make(),hook=f.callback('OnDestroy',f.controllerClass),call={threadId:1};
 hook.onEnter.call(call,[f.controller]);
 f.write(slot(f),f.ptr(0));f.write(f.controller.add(0x10),f.ptr(0));
 const P=f.ptr(0).constructor,names=['readByteArray','readPointer','readU8','readU16','readU32','readS32','readU64','readS64','readFloat','readUtf16String','readCString'];
 const original=Object.fromEntries(names.map(name=>[name,P.prototype[name]]));
 const c=f.context;c.ArrayBuffer=ArrayBuffer;c.NativePointer=P;c.NativeInteger=class{};
 const bridge=fs.readFileSync(path.join(__dirname,'../native_pc/native_event_runtime/bridge.js'),'utf8');
 vm.runInContext(bridge.slice(bridge.indexOf('function __integer('),bridge.indexOf('class NativeInteger')),c);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../native_pc/native_event_runtime/cold_plan.js'),'utf8'),c);
 const classNames=['BattleController','SingletonHost','AutoChessGameManager','AutoChessGameStatus','AutoChessGameStateModel','AutoChessGameStateMachine','AutoChessDataCenter','AutoChessPlayerDataModel','ScenePlayerData','PlayerCard','AutoChessBattleDataModel','ChessInst','BattleCharacterData','UnitManager','Character'];
 c.terminalClasses=Object.fromEntries(classNames.map(name=>[name,(name==='BattleController'?f.controllerClass:name==='Character'?f.character:f.alloc()).toString()]));
 const stringClass=f.alloc(),stage=f.level.add(0x18).readPointer();f.write(stage,stringClass);c.terminalStringClass=stringClass.toString();c.destroyCallbacks=hook;
 vm.runInContext(`
  __arkdpsSealClassHierarchy(new Map(Object.values(terminalClasses).map(k=>[k,'0x0'])));
  nativeCaptureMetadata={clock,classes:terminalClasses,string_class:terminalStringClass,cold_metadata_ready:true,immutable_class_parents:true};
  nativeCaptureMetadata.cold_plan=__arkdpsBuildColdCapturePlan(nativeCaptureMetadata);
  globalThis.__nativeCaptureCapabilities=()=>({version:1,raw_collection:'all',lowered_projections:['live_scope_guards','same_point_class_ancestry','open_parent_checkpoint','token_host_and_character_form','full_native_binding','current_buff_and_blackboard_keys','consumer_edge_evidence']});
  globalThis.arkdpsNativeAsyncCapture=true;nativeCaptureModeHistory.set(0,'all');
  prepareNativeCaptureHook(ptr(0x9000),destroyCallbacks);
  if(destroyCallbacks.arkdpsCaptureContract.complete!==true)throw Error('incomplete owned Destroy contract');
  globalThis.ownedBytes=(at,n)=>__arkdpsOwnedRead(BigInt(at),n);
  probeAdapter.capturedPublicationBinding=()=>{throw Error('terminal borrowed normal binding');};
 `,c);
 const plan=json(hook.arkdpsCaptureContract),clock=f.vm.runInContext('clock',c),segments=[],fields=[];
 const bodyRoots={0:f.controller,8:f.controller.add(0xe0).readPointer(),9:f.level};
 for(const field of plan.fields.filter(field=>field.phase==='leave')){
  const add=(at,bytes)=>segments.push({field_id:field.field_id,role:field.role,checkpoint:0,address:at.toString(),bytes,status:'ok',guard_id:field.guard_id});
  let status='ok';
  try{
   let at=field.root==='absolute'?f.ptr(field.absolute_address):field.root==='global_slot'?clock:bodyRoots[field.root_index];
   if(!at)throw Error('missing owned root');
   for(const offset of field.pointer_offsets){add(at.add(offset),original.readByteArray.call(at.add(offset),8));at=original.readPointer.call(at.add(offset));if(at.isNull())throw Error('null owned path');}
   at=at.add(field.offset);
   if(field.kind==='utf16'){
    add(at,original.readByteArray.call(at,8));add(at.add(16),original.readByteArray.call(at.add(16),4));
    const n=original.readS32.call(at.add(16)),text=original.readUtf16String.call(at.add(20),n),bytes=new ArrayBuffer(2*n),view=new DataView(bytes);
    for(let i=0;i<n;i++)view.setUint16(2*i,text.charCodeAt(i),true);add(at.add(20),bytes);
   }else add(at,original.readByteArray.call(at,field.bytes));
  }catch(error){status='field_unreadable';}
  fields.push({field_id:field.field_id,role:field.role,checkpoint:0,status});
 }
 c.ownedTerminalRecord={version:1,edge:'leave',closed_after:true,checkpoint:0,facts:{mode_epoch:0,scope_mode_epoch:0},segments,fields};c.destroyReceiver=call;
 const originalReadThrough=f.vm.runInContext('eventStore.readThrough',c);
 for(const name of names)P.prototype[name]=function(...args){
  if(!vm.runInContext('__arkdpsReplayActive()',c))return original[name].apply(this,args);
  const size=name==='readByteArray'?args[0]:name==='readUtf16String'?2*args[0]:name==='readU8'?1:name==='readU16'?2:['readU32','readS32','readFloat'].includes(name)?4:8;
  if(name==='readCString')throw Error('uncaptured terminal metadata read');
  const bytes=c.ownedBytes(this.toString(),size),v=new DataView(bytes);
  if(name==='readByteArray')return bytes;
  if(name==='readPointer')return f.ptr(v.getBigUint64(0,true));
  if(name==='readUtf16String'){let text='';for(let i=0;i<args[0];i++)text+=String.fromCharCode(v.getUint16(2*i,true));return text;}
  if(name==='readU64'||name==='readS64'){const n=name==='readU64'?v.getBigUint64(0,true):v.getBigInt64(0,true);return {toString:()=>n.toString(),toNumber:()=>Number(n)};}
  return name==='readU8'?v.getUint8(0):name==='readU16'?v.getUint16(0,true):name==='readU32'?v.getUint32(0,true):name==='readFloat'?v.getFloat32(0,true):v.getInt32(0,true);
 };
 let projection;
 try{
  projection=vm.runInContext(`
   __arkdpsReplayBegin(ownedTerminalRecord);
   let terminalProjection;
   try{
    destroyCallbacks.onLeave.call(destroyReceiver);
    if(probeAdapter.capturedTerminalRecord({...ownedTerminalRecord}))throw Error('forged record qualified');
    terminalProjection=__arkdpsOnClosedRecord(ownedTerminalRecord);
   }
   finally{globalThis.terminalReplaySummary=__arkdpsReplayEnd();}
   terminalProjection;
  `,c);
 }finally{for(const name of names)P.prototype[name]=original[name];}
 assert(projection,'closed terminal publication unavailable');
 assert.equal(c.terminalReplaySummary.complete,true,'terminal projection stays inside its owned roles');
 assert.equal(projection.terminal_read.retry,false);assert.equal(projection.terminal_read.terminal_only,true);
 assert.deepEqual(json(projection.mark.terminal_boundary),json(projection.terminal_read.terminal_boundary));
 for(const key of ['sequence','epoch','frame','game_time','damage_settlement_scope'])assert.equal(projection.mark[key],projection.terminal_read[key],key);
 for(const key of Object.keys(projection.mark).filter(x=>x.startsWith('native_')))assert.deepEqual(json(projection.mark[key]),json(projection.terminal_read[key]),key);
 assert.equal(f.vm.runInContext('eventStore.readThrough',c),originalReadThrough,'snapshot must not authorize ACK');
 assert(f.context.rpc.exports.ackEvents(4).error);
 // The record loses its private qualification at the end of that exact lease.
 vm.runInContext(`__arkdpsReplayBegin(ownedTerminalRecord);try{if(probeAdapter.capturedTerminalRecord(ownedTerminalRecord))throw Error('expired Destroy qualification');if(__arkdpsOnClosedRecord(ownedTerminalRecord)!==null)throw Error('expired record published');}finally{globalThis.expiredSummary=__arkdpsReplayEnd();}`,c);
 assert.equal(c.expiredSummary.complete,true,'expired qualification rejects without reading missing roles');
 // A different ordinary leave owns identical bytes but has no Destroy callback admission.
 vm.runInContext(`globalThis.ordinaryLeaveRecord={...ownedTerminalRecord};__arkdpsReplayBegin(ordinaryLeaveRecord);try{if(probeAdapter.capturedTerminalRecord(ordinaryLeaveRecord))throw Error('ordinary leave qualified');if(__arkdpsOnClosedRecord(ordinaryLeaveRecord)!==null)throw Error('ordinary leave published terminal');}finally{globalThis.ordinarySummary=__arkdpsReplayEnd();}`,c);
 assert.equal(c.ordinarySummary.complete,true,'ordinary leave rejects before any uncaptured terminal read');

 f.vm.runInContext('__arkdpsPublicationServed(4)',c);assert.equal(f.context.rpc.exports.ackEvents(4).acknowledged,4);
}
