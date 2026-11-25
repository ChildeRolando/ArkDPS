// Production callbacks and wire records in owned synthetic memory. This fixture
// supplies metadata/addresses; it never supplies a quality flag or event field.
const assert = require('node:assert/strict');
const path = require('node:path');
const {fixture} = require('./native_pc_frida_probe_fixture.js');
const FP = 4294967296n;
const bits = value => BigInt(Math.round(value * Number(FP)));
const valueOf = value => Number(bits(value)) / Number(FP);
const lateCases = {
  late_amplification_130_after_unit: {entry: 306, final: 397.79998540878296, before: 1306},
  late_amplification_unrelated_after_unit: {entry: 40, final: 57.25, before: 80},
  late_attenuation_after_unit: {entry: 80, final: 37.5, before: 160},
  late_amplification_before_unit: {entry: 40, final: 57.25, before: 80, timing: 'before_unit'},
  late_amplification_during_unit: {entry: 40, final: 57.25, before: 80, timing: 'during_unit'},
  late_amplification_old_output: {entry: 40, final: 57.25, before: 80, staleOutput: true},
  final_nominal_below_hp: {entry: 40, final: 39, actual: 40, before: 80, invalid: true},
  zero_nominal_with_hp_damage: {entry: 40, final: 0, actual: 40, before: 80, invalid: true},
  late_amplification_bad_uid: {entry: 40, final: 57.25, before: 80, invalid: true},
  late_amplification_bad_hp: {entry: 40, final: 57.25, before: 80, invalid: true},
};

function scenario(platform, fault) {
  const late = lateCases[fault];
  const entryNominal = late?.entry ?? 40, finalNominal = late?.final ?? 40;
  const formulaBefore = late?.before ?? 80;
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
  final.onLeave.call(finalCall, f.ptr(bits(formulaBefore)));
  outer.onLeave.call(outerCall, f.ptr(bits(entryNominal)));

  const modifier = f.modifier(source, target, entryNominal, 0, 1);
  const hpBefore = late ? 1000 : fault === 'lethal_hit' ? 30 : 50;
  f.write(target.add(0x40), bits(hpBefore));
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
  if (late?.timing === 'before_unit') f.write(modifier.add(0x30), bits(finalNominal));
  take.onEnter.call(takeCall, [target, modifier, f.ptr(0)]);
  if (late?.staleOutput) {
    const output = f.callback('OnAfterOutputDamage', f.entity), outputCall = {threadId: 1};
    output.onEnter.call(outputCall, [source, modifier]); output.onLeave.call(outputCall);
  }
  if (late?.timing === 'during_unit') f.write(modifier.add(0x30), bits(finalNominal));
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
  assert.equal(target.add(0x40).readS64().toString(), String(bits(hpBefore)));
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
  const actual = fullBlock ? 0 : late?.actual ?? late?.final
    ?? (fault === 'lethal_hit' ? 30 : fault === 'nonlethal_cap' ? 20 : 40);
  if (!fullBlock) f.write(modifier.add(0x30), bits(finalNominal));
  f.write(modifier.add(0x48), -bits(actual));
  const hpAfter = fault === 'bad_hp_difference' ? 11
    : fault === 'late_amplification_bad_hp' ? hpBefore - actual + .25 : hpBefore - actual;
  f.write(target.add(0x40), bits(hpAfter));
  if (['changed_target_uid', 'full_block_bad_uid', 'late_amplification_bad_uid'].includes(fault))
    f.write(target.add(0x18), 99);
  apply.onLeave.call(applyCall);
  const activeMark = f.context.rpc.exports.mark();
  const activeBatch = f.context.rpc.exports.readEvents(0, activeMark.sequence);

  f.clock(60, 2n * FP);
  const skillEnd = f.callback('OnSkillFinish', f.character), endCall = {threadId: 1};
  skillEnd.onEnter.call(endCall, [source]); skillEnd.onLeave.call(endCall);
  const mark = f.context.rpc.exports.mark();
  const batch = f.context.rpc.exports.readEvents(0, mark.sequence);
  assert.equal(batch.enemy_damage_ready, true);
  assert.equal(batch.mitigation_ready, true);
  const legacy=batch.events.filter(e=>e.kind==='damage');
  if(legacy.length){
    assert.equal(legacy.length,1,platform+':'+fault);
    assert(batch.enemy_damage_unknown,platform+':'+fault+' retains explicit failed-domain quality');
    assert(!batch.events.some(e=>e.kind==='damage_settlement'&&e.source===legacy[0].source),
      platform+':'+fault+' cannot duplicate one modifier as legacy and certificate');
  }
  const valid = ['target_return_before_apply_commit', 'nonlethal_cap', 'lethal_hit',
    'full_block_unit_before_shield', 'full_block_unit_after_shield'].includes(fault) || !!late && !late.invalid;
  return {initial_mark: initialMark, initial_batch: initialBatch,
    active_mark: activeMark, active_batch: activeBatch, mark, batch,
    inputs: {entry_nominal: valueOf(entryNominal), final_nominal: valueOf(fullBlock ? 0 : finalNominal),
      counter_damage: valueOf(fullBlock ? 40 : finalNominal), formula_before: valueOf(formulaBefore),
      hp_damage: valueOf(actual), shield_damage: fullBlock ? 40 : 0,
      certified: valid, timing: late?.timing ?? 'after_unit', stale_output: !!late?.staleOutput},
    synthetic: 'Owned metadata, resolved addresses and callback timing; no live game evidence'};
}

const report = {};
for (const platform of ['android', 'native_pc']) {
  report[platform] = {};
  for (const fault of ['target_return_before_apply_commit', 'nonlethal_cap', 'lethal_hit',
    'full_block_unit_before_shield', 'full_block_unit_after_shield', 'full_block_bad_uid',
    'bad_hp_difference', 'changed_target_uid', 'clock_changed', 'duplicate_target_callback',
    'nested_same_target', ...Object.keys(lateCases)])
    report[platform][fault] = scenario(platform, fault);
}
process.stdout.write(JSON.stringify(report));
