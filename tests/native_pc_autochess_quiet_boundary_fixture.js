const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {world} = require('./native_pc_autochess_context_fixture.js');

const bridge = fs.readFileSync(path.join(__dirname, '..', 'native_pc', 'native_event_runtime', 'bridge.js'), 'utf8');

// The former quiet-defer certificate is invalid for an asynchronous producer.
// Every native statistical RPC now has to use the publication journal.
for(const active of [0,1,3]){
 let legacyCalls=0,stops=0;
 const context=vm.createContext({__pid:()=>17,__nativeActive:()=>active,
  legacy:()=>{legacyCalls++;return {sequence:17};},cleanup:()=>{stops++;return {stopped:true};}});
 vm.runInContext(bridge+'\nrpc.exports={mark:legacy,readEvents:legacy,readTerminalEvents:legacy,ackEvents:legacy,stop:cleanup};',context);
 for(const operation of ['mark','read','terminal_read','ack']){
  const command={operation,after:16,through:17,sequence:17,native_quiet_boundary_version:1};
  assert.throws(()=>context.__arkdpsRequest(JSON.stringify(command)),/publication journal/);
 }
 assert.equal(legacyCalls,0,'no statistical RPC may fall back to a live source read');
 assert.equal(JSON.parse(context.__arkdpsRequest('{"operation":"stop"}')).stopped,true);
 assert.equal(stops,1);
}

let nativeActive = 0, latched = null;
const actual = world({fixtureOptions: {globals: {
    __nativeActive: () => nativeActive,
    __arkdpsMarkEntryActive: () => latched ?? nativeActive,
}}});
assert.equal(actual.mark().native_battle_state, 2);
nativeActive = 1;
assert.equal(actual.mark().mark_retry_reason, 'callback_pending_at_start');
latched = 0;
assert.equal(actual.adapter.pendingMarkCallbacks(), 1);
assert.equal(actual.mark().active, 0);
assert.equal(actual.mark().native_autochess_context.status, 'available');
actual.write(actual.controller.add(0x228), 3);
assert.equal(actual.mark().native_battle_state, 3);

// A recursive reset hook still uses the live counter during a marked request.
for (const pending of [1, 2]) {
    const reset = world({fixtureOptions: {roundResetMethod: true, globals: {
        __nativeActive: () => pending,
        __arkdpsMarkEntryActive: () => 0,
    }}});
    reset.clock(60, 2n << 32n);
    reset.mark();
    const invocation = {threadId: 1};
    reset.callback('ClearRunTimeData', reset.controllerClass).onEnter.call(invocation, [reset.controller]);
    assert.equal(Boolean(invocation.roundReset), pending === 1);
}
console.log('PASS_OWNED_NATIVE_RPC_ROUTING');
