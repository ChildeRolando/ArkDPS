// Exercise the real shared clock/enqueue boundary with a mocked adapter scope.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const path = require('node:path');
const fs = require('node:fs');
const {fixture} = require('./native_pc_frida_probe_fixture.js');

const f = fixture({platform: 'native_pc', sourcePath: path.join(__dirname, '..', 'event_probe_native_pc.js')});
vm.runInContext(`
 let fakeScope={protocol_version:1,status:'available',verified_same_frame:true,
  scope_key:'native:autochess:1',phase:4,round:4};
 probeAdapter.stampClock=when=>{when.native_autochess_scope=fakeScope;};
 globalThis.clockStamp=()=>time(true);
 globalThis.enqueueTail=when=>enqueueEvent('char_a','enemy_hp',20,when,{method:'synthetic'},null);
 globalThis.setScope=(phase,round,key)=>{fakeScope.phase=phase;fakeScope.round=round;fakeScope.scope_key=key;};
 globalThis.throwScope=()=>{probeAdapter.stampClock=()=>{throw Error('unreadable scope');};};
`, f.context);
f.clock(30, 4294967296n);
const old = f.context.clockStamp();
f.context.setScope(5, 4, 'native:autochess:2');
f.clock(60, 8589934592n);
const current = f.context.clockStamp();
const delayed = f.context.enqueueTail(old);
assert.equal(delayed.native_autochess_scope.phase, 4);
assert.equal(delayed.native_autochess_scope.scope_key, 'native:autochess:1');
assert.equal(current.native_autochess_scope.phase, 5);
assert.equal(delayed.frame, 30);
assert.equal(delayed.game_time, 1);
assert.equal(delayed.amount, 20);
assert.notEqual(old.native_autochess_scope, delayed.native_autochess_scope);
f.context.throwScope();
const failed = f.context.enqueueTail(f.context.clockStamp());
assert.equal(failed.native_autochess_scope.status, 'unknown');
assert.equal(failed.native_autochess_scope.verified_same_frame, false);
assert.equal(failed.amount, 20);

const m = fixture({platform: 'mumu', sourcePath: path.join(__dirname, '..', 'event_probe_android2791.js')});
m.clock(30, 4294967296n);
const ordinary = vm.runInContext("enqueueEvent('char_a','damage',20,time(true),null,null)", m.context);
assert.equal(Object.hasOwn(ordinary, 'native_autochess_scope'), false);
assert.deepEqual(Object.keys(ordinary), ['sequence', 'frame', 'game_time', 'source', 'kind', 'amount', 'proof']);

// Exercise the actual coalescer too: changing phase without advancing the
// shared clock must flush the old tail before admitting the next phase's HP.
const runtime = fs.readFileSync(path.join(__dirname, '..', 'event_probe_runtime.js'), 'utf8');
const recordFrom = runtime.indexOf('function record(source,');
const recordTo = runtime.indexOf('function recordHpContext(', recordFrom);
assert(recordFrom >= 0 && recordTo > recordFrom, 'production coalescer functions missing');
function coalesced(useNativeScope) {
 const context = {};
 vm.createContext(context);
 vm.runInContext(`
  let sequence=0,stopped=false,fatal=null;
  const queue=[],EVENT_BUFFER_LIMIT=100,coalesceEnemyHp=true;
  let enemyMaxHpReady=true;
  ${runtime.slice(recordFrom, recordTo)}
  const when=phase=>({frame:10,game_time:1,...(${useNativeScope} ? {
   native_autochess_scope:{protocol_version:1,status:'available',verified_same_frame:true,
    scope_key:'native:autochess:'+phase,phase,round:4,frame:10,game_time:1}} : {})});
  const write=hpBefore=>({key:'enemy:1',enemyTarget:true,maxHpBefore:null,hpBefore,
   evidence:{target:'enemy_test',target_instance:'enemy:1'}});
  enemyHpBirths.set('enemy:1',{eid:'enemy_test',frame:1});
  recordEnemyHp(write(100),when(4),'Entity.SetHpInternal',110,1000);
  recordEnemyHp(write(110),when(4),'Entity.SetHpInternal',120,1000);
  recordEnemyHp(write(120),when(5),'Entity.SetHpInternal',130,1000);
  recordEnemyHp(write(130),when(5),'Entity.SetHpInternal',140,1000);
  recordEnemyHp(write(140),when(5),'Entity.SetHpInternal',150,1000);
  flushCoalescedEnemyHp();
 `, context);
 return JSON.parse(vm.runInContext('JSON.stringify(queue)', context));
}
const nativeCoalesced = coalesced(true);
assert.deepEqual(nativeCoalesced.map(event=>[
 event.native_autochess_scope.phase,event.hp_before,event.hp_after]), [
 [4,100,110],[4,110,120],[5,120,130],[5,130,150],
], 'an old native tail must not absorb the next phase at the same clock');
const mumuCoalesced = coalesced(false);
assert.deepEqual(mumuCoalesced.map(event=>[event.hp_before,event.hp_after]), [
 [100,110],[110,150],
], 'MuMu without a native scope retains its original head plus contiguous tail');
assert(mumuCoalesced.every(event=>!Object.hasOwn(event,'native_autochess_scope')));
process.stdout.write(JSON.stringify({delayed, failed, ordinary,
 coalesced:{native:nativeCoalesced,mumu:mumuCoalesced}}));
