// Actual shared callbacks, with owned modifier and shield memory.
const assert = require('node:assert/strict');
const path = require('node:path');
const {fixture} = require('./native_pc_frida_probe_fixture.js');

const FIXED = 1n << 32n;

function observe(platform, scenario) {
  const module = platform === 'native_pc' ? 'event_probe_native_pc.js' : 'event_probe_android2791.js';
  const f = fixture({platform, settlement: true, sourcePath: path.join(__dirname, '..', module)});
  const source = f.unit('char_347_jaksel', 10);
  const target = f.unit('enemy_test', 12);
  const modifier = f.modifier(source, target, 40, 0, 2);
  f.clock(30, FIXED);
  f.write(target.add(0x40), 50n * FIXED);
  const apply = f.callback('ApplyModifier', f.entity);
  const take = f.callback('OnTakeDamage', f.unitClass);
  const output = f.callback('OnAfterOutputDamage', f.entity);
  const block = f.callback('_AssignDynamicVar', f.blockDamage);
  const buff = f.callback('OnTakeDamage', f.buffClass);
  const applyCall = {threadId: 1};
  const takeCall = {threadId: 1};
  const modifierArgs = [target, modifier, f.ptr(0)];

  function unreadableShieldClock() {
    f.clock(30, undefined);
    const nativeClock = f.vm.runInContext('clock', f.context);
    // The same deliberate field fault must remain unreadable through scalar
    // and bulk APIs; it must never become a genuine zero-time regression.
    assert.throws(() => nativeClock.add(4).readS64(), /undefined/);
    assert.throws(() => nativeClock.readByteArray(12), /Unreadable fixture address/);
    const call = {threadId: 1};
    block.onEnter.call(call, [f.alloc(), f.alloc(), modifier, f.ptr(0), f.ptr(FIXED)]);
    block.onLeave.call(call);
    f.clock(30, FIXED);
  }

  function observedShield() {
    const instance = f.alloc(), blackboard = f.alloc(), values = f.alloc();
    f.write(instance, f.buffClass);
    f.write(instance.add(0x18), source);
    f.write(instance.add(0x208), f.managedString('shining_s_2'));
    f.write(instance.add(0x27c), 1);
    f.write(blackboard.add(0x18), 1);
    f.write(blackboard.add(0x10), values);
    f.write(values.add(0x20), f.managedString('dynamic'));
    f.write(values.add(0x28), 100);
    const buffCall = {threadId: 1};
    const blockCall = {threadId: 1};
    buff.onEnter.call(buffCall, [instance, modifier]);
    block.onEnter.call(blockCall, [f.alloc(), blackboard, modifier, f.ptr(0), f.ptr(5n * FIXED)]);
    f.write(values.add(0x28), 95);
    block.onLeave.call(blockCall);
    buff.onLeave.call(buffCall);
  }

  function cancel() {
    f.write(modifier.add(0x70), scenario === 'other_cancellation' ? 4 : 3);
    if (scenario !== 'nonzero_amount') f.write(modifier.add(0x30), 0n);
  }

  if (scenario === 'previous_shield_fault') unreadableShieldClock();
  apply.onEnter.call(applyCall, modifierArgs);
  if (scenario !== 'missing_target_return') {
    take.onEnter.call(takeCall, modifierArgs);
    if (scenario === 'early_cancel') cancel();
    if (scenario === 'nonzero_shield') observedShield();
    take.onLeave.call(takeCall);
  }
  if (scenario === 'output_ambiguity') {
    for (const proposed of [39n, 38n]) {
      f.write(modifier.add(0x30), proposed * FIXED);
      const outputCall = {threadId: 1};
      output.onEnter.call(outputCall, [source, modifier]);
      output.onLeave.call(outputCall);
    }
  }
  if (scenario === 'nested_modifier' || scenario === 'reused_modifier') {
    const child = {threadId: 1};
    const inner = scenario === 'reused_modifier' ? modifier
      : f.modifier(f.unit('enemy_attacker', 13), target, 1, 0, 2);
    apply.onEnter.call(child, [target, inner, f.ptr(0)]);
    apply.onLeave.call(child);
  }
  if (scenario === 'ambiguous_target') {
    const duplicate = {threadId: 1};
    take.onEnter.call(duplicate, modifierArgs);
    take.onLeave.call(duplicate);
  }
  if (scenario === 'unclocked_shield') unreadableShieldClock();
  if (scenario === 'unknown_shield') {
    const call = {threadId: 1};
    block.onEnter.call(call, [f.alloc(), f.alloc(), modifier, f.ptr(0), f.ptr(FIXED)]);
    block.onLeave.call(call);
  }
  if (scenario !== 'early_cancel') cancel();
  if (scenario === 'changed_hp') {
    f.write(modifier.add(0x48), -5n * FIXED);
    f.write(target.add(0x40), 45n * FIXED);
  }
  if (scenario === 'nonzero_delta') f.write(modifier.add(0x48), -5n * FIXED);
  if (scenario === 'target_uid_changed') f.write(target.add(0x18), 99);
  if (scenario === 'source_uid_changed') f.write(source.add(0x538).readPointer().add(0x6c), 99);
  if (scenario === 'clock_changed') f.clock(31, FIXED + 1n);
  apply.onLeave.call(applyCall);
  const mark = f.context.rpc.exports.mark();
  const batch = f.context.rpc.exports.readEvents(0, mark.sequence);
  assert.equal(batch.error, null, scenario);
  assert.equal(batch.enemy_damage_ready, true, scenario);
  return batch;
}

for (const platform of ['native_pc', 'android']) {
  for (const scenario of ['early_cancel', 'late_cancel', 'previous_shield_fault']) {
    const batch = observe(platform, scenario);
    assert.equal(batch.enemy_damage_unknown, false, platform + ':' + scenario);
    assert.deepEqual(Array.from(batch.events, event => [event.kind, event.amount]),
      [['hp_damage', 40]], platform + ':' + scenario);
    assert.equal(batch.shield_unknown, scenario === 'previous_shield_fault');
  }
  for (const scenario of ['other_cancellation', 'nonzero_amount', 'changed_hp', 'nonzero_delta',
    'target_uid_changed', 'source_uid_changed', 'clock_changed', 'missing_target_return',
    'ambiguous_target', 'nested_modifier', 'reused_modifier', 'output_ambiguity',
    'unknown_shield', 'unclocked_shield', 'nonzero_shield']) {
    const batch = observe(platform, scenario);
    assert(batch.enemy_damage_unknown || batch.events.some(event =>
      event.kind === 'damage_settlement' && event.settlement_complete === false),
    platform + ':' + scenario + ' lost unknown settlement evidence');
  }
}
