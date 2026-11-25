// Reproduce settlement ordering with the real producer callbacks and metadata.
// The state is owned synthetic IL2CPP memory; no game or live telemetry is used.
const assert = require('node:assert/strict');
const path = require('node:path');
const {fixture} = require('./native_pc_frida_probe_fixture.js');

function scenario(platform, order, fault = null, withoutLegacyWire = false) {
  const f = fixture({platform, settlement: true, traceHpContext: true,
    sourcePath: path.join(__dirname, '..', platform === 'native_pc' ?
      'event_probe_native_pc.js' : 'event_probe_android2791.js')});
  f.clock(30, 4294967296n);
  if(withoutLegacyWire)f.vm.runInContext(`{
    const original=record;record=function(source,kind,...args){
      if(['damage','hp_damage','elemental_burst'].includes(kind))return undefined;
      return original(source,kind,...args);
    };
  }`,f.context);
  const source = f.unit('char_147_shining', 10), target = f.unit('enemy_test', 12);
  const modifier = f.modifier(source, target, 40, 0, 1);
  f.write(target.add(0x40), 50n * 4294967296n);
  const apply = f.callback('ApplyModifier', f.entity);
  const take = f.callback('OnTakeDamage', f.unitClass);
  const output = f.callback('OnAfterOutputDamage', f.entity);
  const applyCall = {threadId: 1}, takeCall = {threadId: 1}, outputCall = {threadId: 1};
  const enterOutput = () => output.onEnter.call(outputCall, [source, modifier]);
  const leaveOutput = () => output.onLeave.call(outputCall);
  const commit = () => {
    f.write(modifier.add(0x48), -40n * 4294967296n);
    f.write(target.add(0x40), 10n * 4294967296n);
  };
  apply.onEnter.call(applyCall, [target, modifier, f.ptr(0)]);
  if (order === 'early_output_before_target') { enterOutput(); leaveOutput(); }
  take.onEnter.call(takeCall, [target, modifier, f.ptr(0)]);
  if (order === 'output_enter_zero_leave_committed') enterOutput();
  if (order !== 'target_return_before_apply_commit') commit();
  if (order === 'output_enter_zero_leave_committed') leaveOutput();
  take.onLeave.call(takeCall);
  if (fault === 'duplicate_target_callback') {
    const duplicate = {threadId: 1};
    take.onEnter.call(duplicate, [target, modifier, f.ptr(0)]);
    take.onLeave.call(duplicate);
  }
  if (fault?.startsWith('nested_')) {
    const childSource = fault === 'nested_external' ? f.unit('enemy_source', 88) : source;
    const childModifier = f.modifier(childSource, target, 1, 0, fault === 'nested_healing' ? 4 : 1);
    const child = {threadId: 1};
    apply.onEnter.call(child, [target, childModifier, f.ptr(0)]);
    apply.onLeave.call(child);
  }
  if (order === 'target_return_before_apply_commit') commit();
  if (fault === 'target_uid') f.write(target.add(0x18), 99);
  if (fault === 'source_uid') f.write(source.add(0x18), 99);
  if (fault === 'source_id') f.write(source.add(0x148), f.managedString('char_changed'));
  if (fault === 'target_id') f.write(target.add(0x148), f.managedString('enemy_changed'));
  if (fault === 'source_pointer') f.write(modifier, f.unit('char_changed', 88));
  if (fault === 'target_pointer') f.write(modifier.add(0x10), f.unit('enemy_changed', 88));
  if (fault === 'hp_difference') f.write(target.add(0x40), 11n * 4294967296n);
  if (fault === 'nominal_less_than_hp') f.write(modifier.add(0x30), 39n * 4294967296n);
  if (fault === 'damage_type') f.write(modifier.add(0x68), 2);
  if (fault === 'cancel_reason') f.write(modifier.add(0x70), 2);
  if (fault === 'cross_frame') f.clock(31, 4294967296n);
  if (fault === 'cross_time') f.clock(30, 4294967297n);
  if (order === 'late_output_after_target') { enterOutput(); leaveOutput(); }
  apply.onLeave.call(applyCall);
  const mark = f.context.rpc.exports.mark();
  const batch = f.context.rpc.exports.readEvents(0, mark.sequence);
  assert.equal(batch.enemy_damage_ready, true, 'capability comes from real optional metadata validation');
  const settlement = batch.events.find(event => event.kind === 'damage_settlement');
  const damage = batch.events.find(event => event.kind === 'damage');
  if (fault) {
    assert(!settlement || settlement.settlement_complete === false, 'fault must not certify settlement: ' + fault);
    const local=['hp_difference','nominal_less_than_hp','cancel_reason','duplicate_target_callback',
      'nested_damage','nested_external','nested_healing'].includes(fault);
    assert.equal(batch.enemy_damage_unknown,!local,'proof locality keeps source/clock failures global: '+fault);
    if(local){
      const unknown=batch.events.filter(event=>event.kind==='damage_settlement'&&!event.settlement_complete);
      assert(unknown.length>0,'localized gap must emit an explicit incomplete hit');
      assert(unknown.every(event=>event.source_character_uid===1010&&
        typeof event.proof.settlement_unknown_reason==='string'),'localized records retain owner and reason');
    }
    return {mark, batch, raw_damage: damage, settlement: settlement ?? null, synthetic: 'owned contradictory proof: ' + fault};
  }
  assert(settlement);
  if(withoutLegacyWire)assert.equal(damage,undefined);
  else {
  assert.equal(damage.amount, 40);
  assert.equal(damage.real_delta, -40);
  assert.equal(damage.hp_before, 50);
  assert.equal(damage.hp_after, 10);
  }
  assert.equal(settlement.amount, 40);
  assert.equal(settlement.settlement_complete, true);
  assert.equal(settlement.shield_absorbed, 0);
  assert.equal(settlement.proof.evidence_method, 'Unit.OnTakeDamage+Entity.ApplyModifier');
  return {mark, batch, raw_damage: damage, settlement,
    synthetic: 'Actual production callbacks; controlled method order and owned target memory'};
}

const report = {};
for (const platform of ['android', 'native_pc']) {
  report[platform] = {};
  for (const order of ['output_enter_zero_leave_committed', 'early_output_before_target',
    'no_output_callback', 'late_output_after_target', 'target_return_before_apply_commit']) report[platform][order] = scenario(platform, order);
  report[platform].without_legacy_wire = scenario(platform, 'target_return_before_apply_commit', null, true);
  report[platform].rejected = {};
  for (const fault of ['target_uid', 'source_uid', 'source_id', 'target_id', 'source_pointer',
    'target_pointer', 'hp_difference', 'nominal_less_than_hp', 'damage_type', 'cancel_reason',
    'cross_frame', 'cross_time', 'duplicate_target_callback', 'nested_damage', 'nested_external',
    'nested_healing']) report[platform].rejected[fault] = scenario(platform, 'target_return_before_apply_commit', fault);
}
process.stdout.write(JSON.stringify(report));
