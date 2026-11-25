const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {world} = require('./native_pc_autochess_context_fixture.js');

const bridge = fs.readFileSync(path.join(__dirname, '..', 'native_pc', 'native_event_runtime', 'bridge.js'), 'utf8');

function dispatch({entry = 0, closing = 1, optIn = true, result = {}, scope = {},
                   nativeCall = false, operation = 'mark'} = {}) {
    let active = entry;
    const context = vm.createContext({
        __pid: () => 17,
        __nativeActive: () => active,
        __nativeCall: () => 0n,
        closing, result, scope, nativeCall,
        queueArrival: () => active = closing,
    });
    vm.runInContext(bridge + `
        rpc.exports.mark = () => {
            const entry = __arkdpsMarkEntryActive();
            queueArrival();
            if (nativeCall) new NativeFunction(ptr(1), 'void', [])();
            return {frame: 60, game_time: 2, sequence: 17, epoch: 4, error: null,
                stopped: false, active: entry, active_hooks: [], native_battle_state: 2,
                native_autochess_context: {status: 'available', protocol_version: 1,
                    frame: 60, game_time: 2, phase: 4, round: 12, verified_same_frame: true,
                    scope_key: 'fixture:autochess:2', collection_mode: 'local', ...scope}, ...result};
        };
        rpc.exports.readTerminalEvents = () => ({events: [1], hp_context: [2]});
    `, context);
    const request = {operation, after: 16, through: 17};
    if (optIn) request.native_quiet_boundary_version = 1;
    return JSON.parse(context.__arkdpsRequest(JSON.stringify(request)));
}

for (const phase of [4, 5, 6]) {
    const accept = dispatch({closing: 3, scope: {phase}});
    assert.equal(accept.native_quiet_boundary_version, 1);
    assert.equal(accept.native_deferred_callbacks, 3);
    assert.equal(accept.active, 0);
    assert.equal(accept.retry, undefined);
    assert.deepEqual([accept.sequence, accept.epoch, accept.frame, accept.game_time], [17, 4, 60, 2]);
}
const idle = dispatch({closing: 0});
assert.equal(idle.native_deferred_callbacks, undefined);
assert.equal(idle.retry, undefined);
for (const fault of [
    {entry: 1}, {entry: 1, closing: 0}, {optIn: false}, {nativeCall: true},
    {result: {retry: true}}, {result: {error: 'probe error'}}, {result: {stopped: true}},
    {result: {active: 1}}, {result: {active_hooks: ['Unit.OnTakeDamage']}},
    {result: {native_autochess_context: null}},
    {result: {native_battle_state: 3}}, {result: {terminal_boundary: {phase: 8}}},
    {scope: {phase: 3}}, {scope: {phase: 8}}, {scope: {verified_same_frame: false}},
    {scope: {status: 'unavailable'}}, {scope: {frame: 59}}, {scope: {game_time: 3}},
    {scope: {scope_key: ''}}, {scope: {round: -1}}, {scope: {collection_mode: undefined}},
]) {
    const reject = dispatch(fault);
    assert.equal(reject.retry, true, JSON.stringify(fault));
    assert.equal(reject.native_quiet_boundary_version, undefined, JSON.stringify(fault));
    assert.equal(reject.native_deferred_callbacks, undefined, JSON.stringify(fault));
}
const terminal = dispatch({entry: 1, operation: 'terminal_read'});
assert.equal(terminal.retry, true);
assert.deepEqual(terminal.events, []);
assert.equal(terminal.native_quiet_boundary_version, undefined);

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
console.log('PASS_OWNED_QUIET_BOUNDARY');
