const assert=require('node:assert/strict'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
function world(platform){const f=fixture({platform,settlement:true,sourcePath:path.resolve(platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});f.clock(30,4294967296n);f.vm.runInContext('globalThis.demandStampCalls=0;const demandOriginalStampClock=probeAdapter.stampClock;probeAdapter.stampClock=function(...args){demandStampCalls++;return demandOriginalStampClock.apply(this,args);};',f.context);return f;}
const results=[];
for(const platform of ['native_pc','mumu']){
 const f=world(platform),hook=f.callback('OnTakeDamage',f.buffClass),call={threadId:7};
 const buffStamps=f.context.demandStampCalls;hook.onEnter.call(call,[f.ptr(0xdead)]);assert.equal(f.context.demandStampCalls,buffStamps,'Buff E must not stamp scope');
 assert.equal(call.pushed,true);assert.equal(f.vm.runInContext('stacks.get(7).length',f.context),1);
 hook.onLeave.call(call);assert.equal(f.context.demandStampCalls,buffStamps,'Buff L must not stamp scope');assert.equal(f.vm.runInContext('stacks.has(7)',f.context),false);
 assert.equal(f.vm.runInContext('active',f.context),0);assert.equal(f.context.rpc.exports.mark().shield_unknown,false);
 results.push({platform,case:'unreadable_buff_without_block',stack_balanced:true,shield_unknown:false});
 const g=world(platform),source=g.unit('char_147_shining',10),target=g.unit('enemy_test',12),mod=g.modifier(source,target,40,-30,1);
 const buff=g.alloc(),bb=g.alloc(),array=g.alloc();g.write(buff,g.buffClass);g.write(buff.add(0x18),source);g.write(buff.add(0x208),g.managedString('shining_s_2'));g.write(buff.add(0x27c),1);g.write(bb.add(0x18),1);g.write(bb.add(0x10),array);g.write(array.add(0x20),g.managedString('dynamic'));g.write(array.add(0x28),15);
 const bh=g.callback('OnTakeDamage',g.buffClass),block=g.callback('_AssignDynamicVar',g.blockDamage),bc={threadId:1},sc={threadId:1};
 const beforeBuff=g.context.demandStampCalls;bh.onEnter.call(bc,[buff,mod]);assert.equal(g.context.demandStampCalls,beforeBuff,'Buff E no scope');const beforeBlock=g.context.demandStampCalls;block.onEnter.call(sc,[g.ptr(0),bb,mod,g.ptr(0),g.ptr(10n*4294967296n)]);assert.equal(g.context.demandStampCalls,beforeBlock,'Block E no scope');g.write(array.add(0x28),5);block.onLeave.call(sc);assert(g.context.demandStampCalls>beforeBlock,'Block L requires scope');const afterBlock=g.context.demandStampCalls;bh.onLeave.call(bc);assert.equal(g.context.demandStampCalls,afterBlock,'Buff L no scope');
 const mark=g.context.rpc.exports.mark(),batch=g.context.rpc.exports.readEvents(0,mark.sequence),event=batch.events.find(e=>e.kind==='shield_absorbed');
 assert(event);assert.equal(event.amount,10);assert.equal(event.proof.before,15);assert.equal(event.proof.after,5);assert.equal(event.proof.incoming,40);
 results.push({platform,case:'block_reads_parent_buff_and_blackboard_at_own_edges',amount:event.amount,before:event.proof.before,after:event.proof.after});
 const bad={threadId:1},badBlock={threadId:1};bh.onEnter.call(bad,[g.ptr(0xdead),mod]);block.onEnter.call(badBlock,[g.ptr(0),bb,mod,g.ptr(0),g.ptr(1n*4294967296n)]);block.onLeave.call(badBlock);bh.onLeave.call(bad);assert.equal(g.context.rpc.exports.mark().shield_unknown,true);results.push({platform,case:'block_bad_parent_buff_is_unknown',shield_unknown:true});
}
console.log(JSON.stringify({kind:'damage_demand_owned_semantics',results}));
