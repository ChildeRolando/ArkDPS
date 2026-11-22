// Run the actual producer installers, eventQuality, mark and readEvents against
// owned synthetic IL2CPP memory. This is protocol verification, not live hook
// coverage: the formula method addresses below are synthetic resolved inputs.
const assert = require('node:assert/strict');
const path = require('node:path');
const {fixture} = require('./native_pc_frida_probe_fixture.js');

function capture(f) {
  const mark = f.context.rpc.exports.mark();
  const batch = f.context.rpc.exports.readEvents(0, mark.sequence);
  assert.notEqual(mark.retry, true);
  assert.notEqual(batch.retry, true);
  assert.equal(mark.quality_contract_version, 1);
  assert.equal(batch.quality_contract_version, 1);
  for (const message of [mark, batch]) {
    for (const [key, value] of Object.entries(message)) {
      if (key.endsWith('_ready') || key.endsWith('_unknown'))
        assert.equal(typeof value, 'boolean', key + ' must be boolean');
    }
  }
  return {mark, batch};
}

function make(platform) {
  return fixture({platform, sourcePath: path.join(__dirname, '..',
    platform === 'native_pc' ? 'event_probe_native_pc.js' : 'event_probe_android2791.js')});
}

function emitDamage(f, {before, after = 40} = {}) {
  f.clock(30, 4294967296n);
  const source = f.unit('char_147_shining', 10), target = f.unit('enemy_test', 12);
  if (before !== undefined) {
    const attack = f.alloc(), plan = f.context.syntheticMitigationPlan;
    f.write(attack.add(plan.damageType), 1);
    const outer = f.hookAt(plan.outer), final = f.hookAt(plan.final);
    const outerCall = {threadId: 1}, finalCall = {threadId: 1};
    outer.onEnter.call(outerCall, [target, source, f.ptr(1), f.ptr(0), attack]);
    final.onEnter.call(finalCall, [attack]);
    final.onLeave.call(finalCall, f.ptr(BigInt(before) * 4294967296n));
    outer.onLeave.call(outerCall, f.ptr(BigInt(after) * 4294967296n));
  }
  const modifier = f.modifier(source, target, 40, -40, 1);
  const apply = f.callback('ApplyModifier', f.entity), take = f.callback('OnTakeDamage', f.unitClass);
  const call = {threadId: 1}, targetCall = {threadId: 1};
  f.write(target.add(0x40), 50n * 4294967296n);
  apply.onEnter.call(call, [target, modifier, f.ptr(0)]);
  take.onEnter.call(targetCall, [target, modifier, f.ptr(0)]);
  f.write(target.add(0x40), 10n * 4294967296n);
  take.onLeave.call(targetCall);
  apply.onLeave.call(call);
}

function producer(platform) {
  const unavailable = make(platform);
  const unavailableCapture = capture(unavailable);
  assert.equal(unavailableCapture.batch.mitigation_ready, false);
  assert.equal(unavailableCapture.batch.mitigation_unknown, true);
  assert.match(unavailableCapture.batch.mitigation_error, /BattleFormula missing/);

  const installed = make(platform), plan = installed.installSyntheticMitigation();
  assert.equal(plan.installed_hook_delta, 3);
  const complete = capture(installed);
  assert.equal(complete.batch.mitigation_ready, true);
  assert.equal(complete.batch.mitigation_unknown, false);
  assert.equal(complete.batch.mitigation_error, null);

  const failure = make(platform), failedPlan = failure.installSyntheticMitigation({fail: true});
  assert.equal(failedPlan.installed_hook_delta, 0, 'failed installation rolls back owned hooks');
  const failureCapture = capture(failure);
  assert.equal(failureCapture.batch.mitigation_ready, false);
  assert.equal(failureCapture.batch.mitigation_unknown, true);
  assert.match(failureCapture.batch.mitigation_error, /synthetic mitigation hook installation failure/);
  assert.equal(failure.hookTrace.at(-1).operation, 'detach');

  const missing = make(platform);
  missing.installSyntheticMitigation();
  emitDamage(missing);
  const missingCapture = capture(missing);
  assert.equal(missingCapture.batch.mitigation_ready, true);
  assert.equal(missingCapture.batch.mitigation_unknown, false);
  const damage = missingCapture.batch.events.filter(event => event.kind === 'damage');
  assert.equal(damage.length, 1);
  assert.equal(damage[0].amount, 40);
  assert.equal('mitigation_before' in damage[0], false);
  assert.equal('mitigation_after' in damage[0], false);
  assert.equal(missingCapture.batch.mitigation_diagnostics.apply_missing_candidate, 1);

  const paired = make(platform);
  paired.installSyntheticMitigation();
  emitDamage(paired, {before: 80});
  const pairedCapture = capture(paired);
  const pairedDamage = pairedCapture.batch.events.find(event => event.kind === 'damage');
  assert.equal(pairedDamage.mitigation_before, 80);
  assert.equal(pairedDamage.mitigation_after, 40);
  assert.equal(pairedCapture.batch.mitigation_diagnostics.formula_scopes, 1);
  assert.equal(pairedCapture.batch.mitigation_diagnostics.formula_candidates, 1);
  assert.equal(pairedCapture.batch.mitigation_diagnostics.emitted, 1);

  const mismatched = make(platform);
  mismatched.installSyntheticMitigation();
  emitDamage(mismatched, {before: 80, after: 39});
  const mismatchedCapture = capture(mismatched);
  const mismatchedDamage = mismatchedCapture.batch.events.find(event => event.kind === 'damage');
  assert.equal('mitigation_before' in mismatchedDamage, false);
  assert.equal('mitigation_after' in mismatchedDamage, false);
  assert.equal(mismatchedCapture.batch.mitigation_diagnostics.apply_value_mismatch, 1);
  assert.equal(mismatchedCapture.batch.mitigation_ready, true);
  assert.equal(mismatchedCapture.batch.mitigation_unknown, false);

  return {...complete, synthetic: {
    memory: true, resolved_formula_method_addresses: true,
    installer: 'actual production installMitigationHooks',
    quality: 'actual production mark/readEvents; no quality fields backfilled',
    scope: 'protocol and rollback only; not live hook installation or hit coverage',
  }, scenarios: {
    unavailable: unavailableCapture,
    install_failure: failureCapture,
    per_hit_missing: missingCapture,
    paired_hit: pairedCapture,
    per_hit_mismatch: mismatchedCapture,
  }};
}

process.stdout.write(JSON.stringify({android: producer('android'), native_pc: producer('native_pc')}));
