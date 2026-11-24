// Owned metadata and callback ordering; no live game or manufactured host frame.
const assert=require('node:assert/strict'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const fresh=()=>fixture({sourcePath:path.join(__dirname,'..','event_probe_native_pc.js')});
function enterPlaying(f,{frame=0,fixed=0n,actual=2}={}){
 f.clock(frame,fixed);f.write(f.controller.add(0x228),1);
 const hook=f.callback('set_state',f.controllerClass),call={threadId:1};
 hook.onEnter.call(call,[f.controller,f.ptr(2)]);
 assert.equal(f.context.rpc.exports.mark().initial_boundary,null);
 f.write(f.controller.add(0x228),actual);hook.onLeave.call(call);
 return f.context.rpc.exports.mark();
}
function heal(f){
 const source=f.unit('char_owned_source',10),target=f.unit('char_owned_target',11);
 f.write(target.add(0x40),10n*4294967296n);
 const mod=f.modifier(source,target,5,5,4),hook=f.callback('ApplyModifier',f.entity),call={threadId:1};
 hook.onEnter.call(call,[target,mod,f.ptr(0)]);f.write(target.add(0x40),15n*4294967296n);hook.onLeave.call(call);
}
let tests=0;
{
 const f=fresh(),mark=enterPlaying(f),start=mark.initial_boundary;
 assert(start);assert.equal(start.version,1);assert.equal(start.source,'native-pc-producer');
 assert.equal(start.method,'BattleController.set_state');assert.equal(start.phase,'leave');
 assert.equal(start.state,2);assert.equal(start.hooks_ready,true);
 for(const key of ['frame','game_time','native_frame','sequence','active'])assert.equal(start[key],0);
 assert.equal(start.native_fixed_time,'0');
 assert.deepEqual(Object.keys(start.native_identity).sort(),['controller','level','logger','native','stats']);
 assert.equal(start.native_stage_id,mark.native_stage_id);assert.equal(start.native_generation,mark.native_generation);
 assert.equal(start.native_process_pid,mark.native_process_pid);assert.equal(start.damage_settlement_scope,mark.damage_settlement_scope);
 f.clock(5,715827200n);heal(f);
 const later=f.context.rpc.exports.mark();assert.equal(later.frame,5);assert.equal(later.sequence,1);
 assert(later.epoch>start.epoch);assert.deepEqual(later.initial_boundary,start);
 assert.equal(f.context.rpc.exports.readEvents(0,1).events.length,1);
 f.context.rpc.exports.ackEvents(1);assert.deepEqual(f.context.rpc.exports.mark().initial_boundary,start);
 f.clock(0,0n);assert.equal(f.context.rpc.exports.mark().initial_boundary,null);tests++;
}
for(const [options,reason] of [[{frame:1,fixed:143165440n},'not_frame_zero'],[{actual:1},'binding_unavailable']]){
 const f=fresh(),mark=enterPlaying(f,options);assert.equal(mark.initial_boundary,null);
 assert.equal(mark.initial_boundary_diagnostics.last_rejection,reason);tests++;
}
{
 const f=fresh();f.clock(0,0n);f.write(f.controller.add(0x228),2);
 const hook=f.callback('set_state',f.controllerClass),call={threadId:1};
 hook.onEnter.call(call,[f.controller,f.ptr(2)]);hook.onLeave.call(call);
 const mark=f.context.rpc.exports.mark();assert.equal(mark.initial_boundary,null);
 assert.equal(mark.initial_boundary_diagnostics.last_rejection,'not_playing_transition');tests++;
}
{
 const f=fresh();f.vm.runInContext('probeHooksReady=false',f.context);
 const mark=enterPlaying(f);assert.equal(mark.initial_boundary,null);
 assert.equal(mark.initial_boundary_diagnostics.last_rejection,'hooks_not_ready');
 f.vm.runInContext('probeHooksReady=true',f.context);assert.equal(f.context.rpc.exports.mark().initial_boundary,null);tests++;
}
{
 const f=fresh();heal(f);const mark=enterPlaying(f);assert.equal(mark.initial_boundary,null);
 assert.equal(mark.initial_boundary_diagnostics.last_rejection,'events_precede_initial_boundary');tests++;
}
for(const change of ['stage','scope','fatal','stop']){
 const f=fresh();assert(enterPlaying(f).initial_boundary);
 if(change==='stage')f.write(f.level.add(0x18),f.managedString('Owned/OtherStage'));
 if(change==='scope')f.vm.runInContext('resetDamageSettlementScope()',f.context);
 if(change==='fatal')f.vm.runInContext("fatal='owned fixture failure'",f.context);
 if(change==='stop')f.context.rpc.exports.stop();
 assert.equal(f.context.rpc.exports.mark().initial_boundary,null);tests++;
}
{
 const f=fresh();enterPlaying(f);f.clock(30,4294967296n);f.write(f.controller.add(0x228),3);
 assert.equal(f.context.rpc.exports.mark().terminal_boundary,null,'state3 alone is not a final witness');
 const destroy=f.callback('OnDestroy',f.controllerClass),call={threadId:1};
 destroy.onEnter.call(call,[f.controller]);destroy.onLeave.call(call);
 const mark=f.context.rpc.exports.mark();assert(mark.terminal_boundary);
 assert.equal(mark.terminal_boundary_diagnostics.destroy_entered,1);assert.equal(mark.terminal_boundary_diagnostics.captured,1);
 f.write(f.controller.add(0x10),f.ptr(0));
 const retry=f.context.rpc.exports.mark();assert.equal(retry.retry,true);assert(retry.terminal_boundary);
 assert.equal(retry.active,0);assert.deepEqual(Array.from(retry.active_hooks),[]);
 assert.equal(retry.sequence,mark.sequence);assert.equal(retry.epoch,mark.epoch);tests++;
}
{
 const f=fresh();f.clock(30,4294967296n);
 const destroy=f.callback('OnDestroy',f.controllerClass),call={threadId:1};
 destroy.onEnter.call(call,[f.controller]);f.clock(31,4438132736n);destroy.onLeave.call(call);
 const mark=f.context.rpc.exports.mark();assert.equal(mark.terminal_boundary,null);
 assert.equal(mark.terminal_boundary_diagnostics.last_rejection,'destroy_clock_changed');tests++;
}
console.log(JSON.stringify({owned_boundary_cases:tests,live_game:false}));
