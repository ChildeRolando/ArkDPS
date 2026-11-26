// Actual production control and hooks in owned synthetic memory, never a game.
const assert=require('node:assert/strict'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
for(const platform of ['native_pc','mumu']){
 const f=fixture({platform,sourcePath:path.join(__dirname,'..',platform==='native_pc'
  ?'event_probe_native_pc.js':'event_probe_android2791.js')});
 const api=f.context.rpc.exports;
 assert.equal(f.messages.at(-1).capture_pause_version,1);
 // A failed optional installation has permanently detached its partial hook.
 f.installSyntheticMitigation({fail:true});
 const live=()=>f.vm.runInContext('captureHooks.filter(x=>x.listener&&!x.detached).length',f.context);
 const initialHooks=live(),source=f.unit('char_147_shining',12);
 f.clock(30,4294967296n);
 const callback=f.callback('OnSkillStart',f.character),call={threadId:1};
 callback.onEnter.call(call,[source]);
 const busy=api.setCapturePaused(true,1);
 assert.equal(busy.retry,true);assert.equal(busy.paused,false);assert.equal(live(),initialHooks);
 callback.onLeave.call(call);
 const paused=api.setCapturePaused(true,1);
 assert.equal(paused.retry,false);assert.equal(paused.paused,true);assert.equal(paused.active,0);
 assert.equal(live(),0);assert(paused.sequence>0);
 const retained=api.readEvents(0,paused.sequence);
 assert.equal(retained.events.length,paused.sequence);
 assert.equal(api.setCapturePaused(true,1).capture_generation,paused.capture_generation);
 f.clock(60,8589934592n);
 // Even a saved listener cannot admit work during the paused interval.
 const skipped={threadId:1};callback.onEnter.call(skipped,[source]);callback.onLeave.call(skipped);
 assert.equal(api.captureControlStatus().sequence,paused.sequence);
 const resumed=api.setCapturePaused(false,2);
 assert.equal(resumed.paused,false);assert.equal(resumed.capture_generation,paused.capture_generation+1);
 assert.equal(resumed.sequence,paused.sequence);assert.equal(resumed.epoch,paused.epoch);
 assert.equal(live(),initialHooks); // Never revive the failed optional hook.
 assert(api.setCapturePaused(true,1).error);
 const next=f.callback('OnSkillFinish',f.character),end={threadId:1};
 next.onEnter.call(end,[source]);next.onLeave.call(end);
 const mark=api.mark(),batch=api.readEvents(0,mark.sequence);
 assert.equal(mark.sequence,paused.sequence+1);assert.equal(batch.events.length,mark.sequence);
 assert.equal(batch.events[0].sequence,1); // Pause/resume did not ACK or discard.
 assert.equal(api.ackEvents(mark.sequence).acknowledged,mark.sequence);
 assert.equal(api.setCapturePaused('yes',3).error,'invalid capture control request');
 // A reverse request before busy pause confirmation still exposes the
 // interval in which the entry gate suppressed callbacks.
 const start=f.callback('OnSkillStart',f.character),inflight={threadId:1};
 start.onEnter.call(inflight,[source]);
 assert.equal(api.setCapturePaused(true,3).retry,true);
 const suppressed={threadId:2};start.onEnter.call(suppressed,[source]);start.onLeave.call(suppressed);
 start.onLeave.call(inflight);
 const beforeReverse=api.captureControlStatus(),reverse=api.setCapturePaused(false,4);
 assert.equal(reverse.paused,false);assert.equal(reverse.retry,false);
 assert.equal(reverse.capture_generation,beforeReverse.capture_generation+1);
 assert.equal(reverse.sequence,beforeReverse.sequence);assert.equal(live(),initialHooks);
 assert.equal(api.setCapturePaused(false,4).capture_generation,reverse.capture_generation);
}
process.stdout.write('PASS_OWNED_CAPTURE_PAUSE');
