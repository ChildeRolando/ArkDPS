// Owned memory drives the same typed reset hook and queue reads as production.
const assert = require('node:assert/strict');
const {world} = require('./native_pc_autochess_context_fixture.js');
const FIXED = 1n << 32n;

function fixture(platform, options = {}, resetMethod = true) {
  const f = world({platform, ...options, fixtureOptions: {roundResetMethod: resetMethod, settlement: true}});
  f.players.forEach(([, exposed], index) => f.write(exposed.add(0x18), f.text(String(index + 1))));
  return f;
}

function capture(f, performReset = () => f.clock(0, 0n), receiver = f.controller) {
  const reset = f.callback('ClearRunTimeData', f.controllerClass);
  const invocation = {threadId: 1};
  reset.onEnter.call(invocation, [receiver]);
  performReset();
  reset.onLeave.call(invocation);
}

function skill(f) {
  invokeSkill(f, f.unit('char_shared', 10));
}

function invokeSkill(f, unit) {
  const hook = f.callback('OnSkillStart', f.character);
  const invocation = {threadId: 1};
  hook.onEnter.call(invocation, [unit]);
  hook.onLeave.call(invocation);
}

function baseline(f) {
  const mark = f.mark();
  assert.equal(mark.retry ?? false, false);
  return [mark, f.context.rpc.exports.readEvents(mark.sequence, mark.sequence)];
}

function json(value) { return JSON.parse(JSON.stringify(value)); }

for (const platform of ['native_pc', 'mumu']) {
  for (const phase of [4, 5, 6]) {
    const f = fixture(platform);
    const ready = f.messages.find(message => message.ready === true);
    assert.equal(ready.autochess_round_start_ready, true);
    assert.equal(ready.autochess_round_start_error, null);
    f.clock(60, 2n * FIXED);
    f.mark();
    skill(f);
    f.vm.runInContext('skillUnknown=true;shieldUnknown=true;unknownSources.add("char_old");', f.context);
    const previous = f.mark();
    assert.equal(previous.skill_unknown, true);
    assert.equal(previous.shield_unknown, true);
    assert.equal(previous.sequence, 1);

    f.write(f.machine.add(0x5c), phase);
    f.write(f.center.add(0x3c), 2);
    const [early, stillOld] = baseline(f);
    assert.equal(early.frame, 60);
    assert.equal(stillOld.autochess_round_start, undefined);
    capture(f);
    f.clock(1, FIXED / 30n);
    skill(f);
    // First host mark for this actual round comes after the zero frame.
    f.write(f.roleData[0].add(0x50), f.text('char_later'));
    f.clock(8, 8n * FIXED / 30n);
    const [later, batch] = baseline(f);
    assert.equal(later.autochess_round_start_ready, true);
    assert.equal(batch.autochess_round_start_ready, true);
    const start = batch.autochess_round_start;
    assert(start, platform + ':phase-' + phase);
    assert.equal(start.source_method, 'BattleController.ClearRunTimeData');
    assert.equal(start.version, 2);
    assert.equal(start.frame, 0);
    assert.equal(start.game_time, 0);
    assert.equal(start.native_fixed_time, '0');
    assert.equal(start.sequence, 1, 'old sequence is not reset');
    assert.equal(start.active, 0);
    assert(start.epoch > previous.epoch);
    assert.equal(start.native_generation, later.native_generation);
    assert.equal(start.native_process_pid, later.native_process_pid);
    assert.deepEqual(json(start.native_identity), json(later.native_identity));
    assert.equal(start.native_autochess_context.round, 2);
    assert.equal(start.native_autochess_context.phase, phase);
    assert.equal(start.native_autochess_context.frame, 0);
    assert.equal(start.native_autochess_context.memberships[0].cid, 'char_shared');
    assert.equal(later.native_autochess_context.memberships[0].cid, 'char_later');
    assert.equal(start.native_autochess_context.scope_key, later.native_autochess_context.scope_key);
    assert.notEqual(start.native_autochess_context.scope_key, early.native_autochess_context.scope_key);
    assert.equal(start.quality.skill_unknown, false);
    assert.equal(start.quality.shield_unknown, false);
    assert.equal(start.quality.unknown_sources.length, 0);
    assert.equal(start.live_character_collection_empty, true);
    assert.deepEqual(json(start.live_character_data_uids), []);
    assert.equal(batch.events.length, 0);
    assert.equal(batch.autochess_round_start_events_complete, true);
    const raw = f.context.rpc.exports.readEvents(0, later.sequence).events;
    assert.deepEqual(json(batch.autochess_round_start_events), [json(raw[1])]);
    assert.equal(raw[0].frame, 60, 'older immutable event stays in its original scope');
    assert.equal(raw[1].frame, 1);
    assert.equal(f.context.rpc.exports.ackEvents(1).acknowledged, 1);
    assert.equal(baseline(f)[1].autochess_round_start_events_complete, true);
    assert.equal(f.context.rpc.exports.ackEvents(later.sequence).acknowledged, later.sequence);
    const released = baseline(f)[1];
    assert.equal(released.autochess_round_start, undefined);
    assert.equal(released.autochess_round_start_events, undefined);
    assert.equal(released.autochess_round_start_events_complete, undefined);
    assert.equal(released.autochess_round_start_unavailable, 'prefix_already_acknowledged');
  }

  for (const scenario of ['no_transition', 'prepare', 'advanced', 'foreign_receiver', 'nested_event']) {
    const f = fixture(platform);
    f.clock(60, 2n * FIXED);
    f.mark();
    if (scenario === 'prepare') f.write(f.machine.add(0x5c), 3);
    if (scenario !== 'no_transition') capture(f, () => {
      f.clock(scenario === 'advanced' ? 1 : 0, scenario === 'advanced' ? FIXED / 30n : 0n);
      if (scenario === 'nested_event') skill(f);
    }, scenario === 'foreign_receiver' ? f.alloc() : f.controller);
    if (scenario === 'no_transition') f.clock(0, 0n);
    f.write(f.machine.add(0x5c), 4);
    f.clock(8, 8n * FIXED / 30n);
    assert.equal(baseline(f)[1].autochess_round_start, undefined, platform + ':' + scenario);
  }

  for (const options of [{arity: 1}, {static: true}, {nonVoid: true}, {byRef: true}]) {
    const f = fixture(platform, {}, options);
    assert.equal(f.vm.runInContext('autoChessRoundResetMethod', f.context), null);
    const ready = f.messages.find(message => message.ready === true);
    assert.equal(ready.autochess_round_start_ready, false);
    assert.match(ready.autochess_round_start_error, /ClearRunTimeData signature/);
    const [mark, batch] = baseline(f);
    assert.equal(mark.autochess_round_start_ready, false);
    assert.equal(batch.autochess_round_start_ready, false);
    assert.equal(batch.autochess_round_start, undefined);
  }

  const staticSkillGap = fixture(platform);
  staticSkillGap.vm.runInContext('cfg.skillReady=false;skillUnknown=true', staticSkillGap.context);
  capture(staticSkillGap);
  const staticQuality = baseline(staticSkillGap)[1].autochess_round_start.quality;
  assert.equal(staticQuality.skill_ready, false, 'a reset does not install absent skill hooks');
  assert.equal(staticQuality.skill_unknown, true);

  for (const happenedBefore of [true, false]) {
    const duplicateAfterFailure = fixture(platform);
    if (!happenedBefore) duplicateAfterFailure.clock(60, 2n * FIXED);
    duplicateAfterFailure.mark();
    if (happenedBefore) invokeSkill(duplicateAfterFailure, duplicateAfterFailure.ptr(0));
    capture(duplicateAfterFailure, () => {
      duplicateAfterFailure.clock(0, 0n);
      if (!happenedBefore) invokeSkill(duplicateAfterFailure, duplicateAfterFailure.ptr(0));
    });
    capture(duplicateAfterFailure);
    const [failedMark, failedBatch] = baseline(duplicateAfterFailure);
    assert.equal(failedMark.sequence, 0, 'unreadable callback did not emit a sequence');
    assert.equal(failedMark.skill_unknown, true, 'second zero reset cannot erase the callback gap');
    assert.equal(failedBatch.autochess_round_start, undefined);
  }

  for (const interveningCallback of [false, true]) {
    const loading = fixture(platform);
    loading.clock(60, 2n * FIXED);
    loading.write(loading.machine.add(0x5c), 3);
    loading.mark();
    capture(loading);
    assert.equal(baseline(loading)[1].autochess_round_start, undefined);
    loading.write(loading.machine.add(0x5c), 4);
    if (interveningCallback) invokeSkill(loading, loading.ptr(0));
    capture(loading);
    const [combatMark, combatBatch] = baseline(loading);
    assert.equal(combatMark.sequence, 0);
    assert.equal(combatMark.skill_unknown, interveningCallback);
    assert.equal(Boolean(combatBatch.autochess_round_start), !interveningCallback,
      'PREPARE zero can carry to combat only without intervening callbacks');
  }

  const occupied = fixture(platform, {token: true});
  capture(occupied);
  const occupiedStart = baseline(occupied)[1].autochess_round_start;
  assert.equal(occupiedStart.live_character_collection_empty, false);
  assert.deepEqual(json(occupiedStart.live_character_data_uids), [999]);
  occupied.vm.runInContext('skillUnknown=true', occupied.context);
  capture(occupied);
  assert.deepEqual(json(baseline(occupied)[1].autochess_round_start), json(occupiedStart));
  assert.equal(occupied.mark().skill_unknown, true, 'duplicate zero reset does not discard current quality');

  for (const badUid of [0, 999]) {
    const unreadable = fixture(platform, {token: true, tokenCount: 2});
    unreadable.write(unreadable.tokenData[1].add(0x6c), badUid);
    capture(unreadable);
    const origin = baseline(unreadable)[1].autochess_round_start;
    assert.equal(origin.live_character_collection_empty, false);
    assert.equal(origin.live_character_data_uids, null,
      'an unreadable or duplicate live UID cannot certify any absent actor');
  }

  for (const changed of [null, 'source_uid', 'scope']) {
    const f = fixture(platform);
    capture(f);
    f.clock(30, FIXED);
    const source = f.unit('char_shared', 11);
    const sourceData = source.add(0x538).readPointer();
    f.write(sourceData.add(0x6c), 101);
    const target = f.unit('char_friendly', 12);
    f.write(target.add(0x40), 10n * FIXED);
    const modifier = f.modifier(source, target, 10, 0, 1);
    const apply = f.callback('ApplyModifier', f.entity), invocation = {threadId: 1};
    apply.onEnter.call(invocation, [target, modifier, f.ptr(0)]);
    const take = f.callback('OnTakeDamage', f.unitClass), hit = {threadId: 1};
    take.onEnter.call(hit, [target, modifier, f.ptr(0)]);
    f.write(target.add(0x40), 0n);
    f.write(modifier.add(0x48), -10n * FIXED);
    take.onLeave.call(hit);
    f.write(target.add(0x18), 99);
    if (changed === 'source_uid') f.write(sourceData.add(0x6c), 102);
    if (changed === 'scope') f.write(f.machine.add(0x5c), 5);
    apply.onLeave.call(invocation);
    const mark = f.mark(), batch = f.context.rpc.exports.readEvents(0, mark.sequence);
    assert.equal(mark.enemy_damage_unknown, true, 'the world keeps its unknown target-domain quality');
    assert.equal(mark.damage_settlement_first_issue.reason, 'unverified enemy settlement domain');
    assert.equal(mark.damage_settlement_source_quality_version, 1);
    assert.equal(mark.damage_settlement_unlocalized_unknown, changed !== null);
    assert.equal(mark.damage_settlement_source_issues.length, changed === null ? 1 : 0);
    if (changed === null) {
      const issue = mark.damage_settlement_source_issues[0];
      assert.equal(issue.source, 'char_shared');
      assert.equal(issue.source_character_uid, 101);
      assert.equal(issue.frame, 30);
      assert.equal(issue.game_time, 1);
      assert.equal(issue.native_autochess_scope.scope_key, mark.native_autochess_context.scope_key);
    }
    assert.equal(batch.events.filter(e => e.kind === 'damage_settlement').length, 0,
      'localized quality does not invent a proven or incomplete HP settlement');
    f.write(f.center.add(0x3c), 2);
    capture(f);
    const reset = f.mark();
    assert.equal(reset.enemy_damage_unknown, false);
    assert.equal(reset.damage_settlement_unlocalized_unknown, false);
    assert.deepEqual(json(reset.damage_settlement_source_issues), [], 'source issues end with their original round');
  }

  const capped = fixture(platform);
  capture(capped);
  capped.clock(1, FIXED / 30n);
  capped.vm.runInContext(`const point=time();
    for(let i=0;i<=AUTOCHESS_ROUND_START_EVENT_LIMIT;i++)
      record('char_shared','skill_start',1,point,{method:'Character.OnSkillStart'},{},101)`, capped.context);
  const large = baseline(capped)[1];
  assert(large.autochess_round_start);
  assert.equal(large.autochess_round_start_events_complete, false);
  assert.equal(large.autochess_round_start_events.length, 0);

  for (const oversized of [false, true]) {
    for (const fault of ['unknown_scope', 'foreign_scope', 'foreign_clock']) {
      const unsafe = fixture(platform);
      capture(unsafe);
      unsafe.clock(1, FIXED / 30n);
      unsafe.vm.runInContext(`for(let i=0;i<${oversized ? 'AUTOCHESS_ROUND_START_EVENT_LIMIT' : '0'};i++)
        record('char_shared','skill_start',1,time(),{method:'Character.OnSkillStart'},{},101)`, unsafe.context);
      const patch = {
        unknown_scope: "point.native_autochess_scope.status='unknown';",
        foreign_scope: 'point.native_autochess_scope.round++;',
        foreign_clock: 'point.native_autochess_scope.frame++;'
      }[fault];
      unsafe.vm.runInContext(`const point=time();${patch}
        record('char_shared','skill_start',1,point,{method:'Character.OnSkillStart'},{},101)`, unsafe.context);
      const [mark, discarded] = baseline(unsafe);
      assert.equal(discarded.autochess_round_start, undefined);
      assert.equal(discarded.autochess_round_start_events_complete, undefined);
      assert.equal(discarded.autochess_round_start_unavailable, 'invalid_prefix_scope_or_clock');
      const retained = unsafe.context.rpc.exports.readEvents(mark.sequence - 1, mark.sequence).events;
      assert.equal(retained.length, 1, 'invalid bootstrap does not remove the ordinary raw event');
    }
  }
}
