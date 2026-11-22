// Production callbacks and wire records in owned synthetic memory. This fixture
// supplies metadata/addresses; it never supplies a quality flag or event field.
const assert = require('node:assert/strict');
const path = require('node:path');
const {fixture} = require('./native_pc_frida_probe_fixture.js');
const FP = 4294967296n;

function scenario(platform, fault) {
  const f = fixture({platform, settlement: true,
    sourcePath: path.join(__dirname, '..', platform === 'native_pc'
      ? 'event_probe_native_pc.js' : 'event_probe_android2791.js')});
  assert.equal(f.installSyntheticMitigation().installed_hook_delta, 3);
  const source = f.unit('char_147_shining', 10), target = f.unit('enemy_test', 12);
  const skill = f.alloc(), skillInfo = f.alloc();
  f.write(skill, f.basicSkill); f.write(skillInfo, f.skillData);
  f.write(source.add(0x3c8), skill); f.write(skill.add(0xb0), skillInfo);
  f.write(skillInfo.add(0x50), f.managedString('shining_s_2'));
  f.write(skillInfo.add(0x18), f.managedString('skill_2'));
  f.write(skillInfo.add(0x10), f.managedString('Shield'));
  const initialMark = f.context.rpc.exports.mark();
  const initialBatch = f.context.rpc.exports.readEvents(0, initialMark.sequence);
  f.clock(30, FP);
  const skillStart = f.callback('OnSkillStart', f.character), startCall = {threadId: 1};
  skillStart.onEnter.call(startCall, [source]); skillStart.onLeave.call(startCall);

  const attack = f.alloc(), plan = f.context.syntheticMitigationPlan;
  f.write(attack.add(plan.damageType), 1);
  const outer = f.hookAt(plan.outer), final = f.hookAt(plan.final);
  const outerCall = {threadId: 1}, finalCall = {threadId: 1};
  outer.onEnter.call(outerCall, [target, source, f.ptr(1), f.ptr(0), attack]);
  final.onEnter.call(finalCall, [attack]);
  final.onLeave.call(finalCall, f.ptr(80n * FP));
  outer.onLeave.call(outerCall, f.ptr(40n * FP));

  const modifier = f.modifier(source, target, 40, 0, 1);
  const hpBefore = fault === 'lethal_hit' ? 30n : 50n;
  f.write(target.add(0x40), hpBefore * FP);
  const apply = f.callback('ApplyModifier', f.entity), take = f.callback('OnTakeDamage', f.unitClass);
  const applyCall = {threadId: 1}, takeCall = {threadId: 1};
  const fullBlock = fault.startsWith('full_block_');
  function absorbForty() {
    const buff = f.alloc(), bb = f.alloc(), array = f.alloc();
    f.write(buff, f.buffClass); f.write(buff.add(0x18), source);
    f.write(buff.add(0x208), f.managedString('shining_s_2')); f.write(buff.add(0x27c), 1);
    f.write(bb.add(0x18), 1); f.write(bb.add(0x10), array);
    f.write(array.add(0x20), f.managedString('dynamic')); f.write(array.add(0x28), 100);
    const buffHook = f.callback('OnTakeDamage', f.buffClass);
    const block = f.callback('_AssignDynamicVar', f.blockDamage);
    const buffCall = {threadId: 1}, blockCall = {threadId: 1};
    buffHook.onEnter.call(buffCall, [buff, modifier]);
    block.onEnter.call(blockCall, [f.alloc(), bb, modifier, f.ptr(0), f.ptr(40n * FP)]);
    f.write(array.add(0x28), 60);
    block.onLeave.call(blockCall); buffHook.onLeave.call(buffCall);
    f.write(modifier.add(0x30), 0n);
  }
  apply.onEnter.call(applyCall, [target, modifier, f.ptr(0)]);
  if (fault === 'full_block_unit_after_shield') absorbForty();
  take.onEnter.call(takeCall, [target, modifier, f.ptr(0)]);
  if (fullBlock && fault !== 'full_block_unit_after_shield') absorbForty();
  take.onLeave.call(takeCall);
  if (fault === 'duplicate_target_callback') {
    const repeated = {threadId: 1};
    take.onEnter.call(repeated, [target, modifier, f.ptr(0)]);
    take.onLeave.call(repeated);
  }
  // Match the observed PC ordering: the target callback has already returned,
  // while both HP and realDelta still hold their pre-settlement values.
  assert.equal(modifier.add(0x48).readS64().toString(), '0');
  assert.equal(target.add(0x40).readS64().toString(), String(hpBefore * FP));
  if (fault === 'nested_same_target') {
    const foreign = f.unit('enemy_attacker', 13), inner = f.modifier(foreign, target, 10, 0, 1);
    const innerApply = {threadId: 1}, innerTake = {threadId: 1};
    apply.onEnter.call(innerApply, [target, inner, f.ptr(0)]);
    take.onEnter.call(innerTake, [target, inner, f.ptr(0)]);
    take.onLeave.call(innerTake);
    f.write(inner.add(0x48), -10n * FP); f.write(target.add(0x40), 40n * FP);
    apply.onLeave.call(innerApply);
  }
  if (fault === 'clock_changed') f.clock(31, FP + FP / 30n);
  if (fault === 'unproven_zero_amount') f.write(modifier.add(0x30), 0n);
  const actual = fullBlock || fault === 'unproven_zero_amount' ? 0n
    : fault === 'lethal_hit' ? 30n : fault === 'nonlethal_cap' ? 20n : 40n;
  f.write(modifier.add(0x48), -actual * FP);
  f.write(target.add(0x40), (fault === 'bad_hp_difference' ? 11n : hpBefore - actual) * FP);
  if (fault === 'changed_target_uid' || fault === 'full_block_bad_uid') f.write(target.add(0x18), 99);
  apply.onLeave.call(applyCall);

  f.clock(60, 2n * FP);
  const skillEnd = f.callback('OnSkillFinish', f.character), endCall = {threadId: 1};
  skillEnd.onEnter.call(endCall, [source]); skillEnd.onLeave.call(endCall);
  const mark = f.context.rpc.exports.mark();
  const batch = f.context.rpc.exports.readEvents(0, mark.sequence);
  assert.equal(batch.enemy_damage_ready, true);
  assert.equal(batch.mitigation_ready, true);
  assert.equal(batch.events.filter(e => e.kind === 'damage').length,
    fault === 'clock_changed' ? 0 : 1, platform + ':' + fault);
  return {initial_mark: initialMark, initial_batch: initialBatch, mark, batch,
    synthetic: 'Owned metadata, resolved addresses and callback timing; no live game evidence'};
}

const report = {};
for (const platform of ['android', 'native_pc']) {
  report[platform] = {};
  for (const fault of ['target_return_before_apply_commit', 'nonlethal_cap', 'lethal_hit',
    'full_block_unit_before_shield', 'full_block_unit_after_shield', 'full_block_bad_uid',
    'unproven_zero_amount',
    'bad_hp_difference', 'changed_target_uid', 'clock_changed', 'duplicate_target_callback',
    'nested_same_target'])
    report[platform][fault] = scenario(platform, fault);
}
process.stdout.write(JSON.stringify(report));
