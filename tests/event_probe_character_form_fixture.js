// Use the real producer callbacks around typed, owned AutoChess memory.
const assert = require('node:assert/strict');
const {world} = require('./native_pc_autochess_context_fixture.js');
const FIXED = 1n << 32n;

function scenario(platform, kind, fault = null) {
  const f = world({platform, fixtureOptions: {settlement: true,
    globals: {arkdpsAutoChessMode: 'local'}}});
  f.players.forEach(([, player], index) =>
    f.write(player.add(0x18), f.text(String(index + 1))));
  f.klass('BattleCharacterData', 'Torappu.Battle', 36,
    [[4, 'prefabKey', 0x50, 14], [8, 'uniqueId', 0x6c, 9], [14, 'isToken', 0x90, 2],
      [15, 'isPredefined', 0x91, 2], [18, 'tokenOrHostKey', 0x98, 14],
      [19, 'tokenOrHostUniqueId', 0xa0, 9]], f.ptr(0), f.characterData);
  const raw = fault === 'other_character' ? 'char_347_jaksel' : 'char_002_amiya';
  const form = fault === 'same_form' ? raw : 'char_1037_amiya3';
  const source = f.unit(raw, 10), data = source.add(0x538).readPointer();
  const prefab = f.text(form);
  f.write(source.add(0x148), f.text(raw));
  f.write(data.add(0x50), prefab);
  f.write(data.add(0x6c), 102);
  f.write(data.add(0x90), fault === 'token' ? 1 : 0);
  f.write(data.add(0x91), fault === 'predefined' ? 1 : 0);
  f.write(data.add(0x98), f.ptr(0));
  f.write(data.add(0xa0), 0);
  f.write(f.roleData[1].add(0x50), f.text(form));
  f.clock(30, FIXED);
  assert.equal(f.mark().native_autochess_context.status, 'available');

  const target = f.unit('enemy_test', 12);
  const modifier = f.modifier(source, target, 40, 0, kind === 'elemental_bar' ? 0 : 1);
  f.write(target.add(0x40), 50n * FIXED);
  if (kind === 'elemental_bar') f.write(modifier.add(0x60), 5);
  const apply = f.callback('ApplyModifier', f.entity);
  const take = f.callback('OnTakeDamage', f.unitClass);
  const call = {threadId: 1};
  const args = [target, modifier, f.ptr(0)];
  apply.onEnter.call(call, args);
  if (kind === 'damage_settlement') {
    const targetCall = {threadId: 1};
    take.onEnter.call(targetCall, args);
    take.onLeave.call(targetCall);
    f.write(modifier.add(0x48), -40n * FIXED);
    f.write(target.add(0x40), 10n * FIXED);
  }
  if (fault === 'data_uid_changed') f.write(data.add(0x6c), 103);
  if (fault === 'data_cid_changed') f.write(data.add(0x50), f.text('char_changed_between_callbacks'));
  if (fault === 'instance_uid_changed') f.write(source.add(0x18), 11);
  if (fault === 'source_class_changed') {
    const previous = source.readPointer();
    f.write(source, f.klass('DerivedCharacter', 'Torappu.Battle', 0, [], previous));
  }
  if (fault === 'data_class_changed') f.write(data, f.roleData[1].readPointer());
  if (fault === 'data_pointer_changed') {
    const replacement = f.alloc();
    f.write(replacement, f.characterData);
    f.write(replacement.add(0x50), prefab);
    f.write(replacement.add(0x6c), 102);
    f.write(replacement.add(0x90), 0);
    f.write(replacement.add(0x91), 0);
    f.write(replacement.add(0x98), f.ptr(0));
    f.write(replacement.add(0xa0), 0);
    f.write(source.add(0x538), replacement);
  }
  if (fault === 'source_pointer_changed') f.write(modifier, f.unit(raw, 11));
  if (fault === 'round_changed') f.write(f.center.add(0x3c), 2);
  if (fault === 'phase_changed') f.write(f.machine.add(0x5c), 5);
  if (fault === 'effective_mode_changed')
    f.vm.runInContext("nativeAutoEffective = {...nativeAutoEffective, mode: 'all'}", f.context);
  if (fault === 'reused_modifier') {
    const overlap = {threadId: 1};
    apply.onEnter.call(overlap, args);
    apply.onLeave.call(overlap);
  }
  const prototype = f.ptr(0).constructor.prototype;
  const originalTextRead = prototype.readUtf16String;
  const originalReadS32 = prototype.readS32;
  const initialCache = f.vm.runInContext('nativeAutoCache', f.context);
  let prefabReads = 0;
  let changedDuringClosingScope = false;
  let scopeBeforeFailedRead;
  let effectiveBeforeFailedRead;
  prototype.readUtf16String = function (...readArgs) {
    const value = originalTextRead.apply(this, readArgs);
    if (!this.equals(prefab.add(0x14))) return value;
    prefabReads++;
    if (fault === 'clock_changed_in_proof') f.clock(30, FIXED + 1n);
    if (fault === 'instance_uid_changed_in_proof') f.write(source.add(0x18), 11);
    return value;
  };
  prototype.readS32 = function (...readArgs) {
    const value = originalReadS32.apply(this, readArgs);
    if (fault === 'closing_scope_read_failed' && prefabReads === 1 && !changedDuringClosingScope &&
        this.equals(f.machine.add(0x5c))) {
      changedDuringClosingScope = true;
      scopeBeforeFailedRead = f.vm.runInContext('nativeAutoScope', f.context);
      effectiveBeforeFailedRead = f.vm.runInContext('nativeAutoEffective', f.context);
      throw new Error('optional actor witness read failed');
    }
    if (fault === 'prefab_changed_in_closing_scope' && prefabReads === 1 && !changedDuringClosingScope &&
        this.equals(f.machine.add(0x5c))) {
      changedDuringClosingScope = true;
      f.write(data.add(0x50), f.text('char_changed_during_scope'));
    }
    return value;
  };
  try {
    apply.onLeave.call(call);
  } finally {
    prototype.readUtf16String = originalTextRead;
    prototype.readS32 = originalReadS32;
  }
  if (fault === 'clock_changed_in_proof') f.clock(30, FIXED);
  if (fault === 'closing_scope_read_failed') {
    assert(changedDuringClosingScope);
    assert.equal(f.vm.runInContext('nativeAutoCache', f.context), initialCache);
    assert.equal(f.vm.runInContext('nativeAutoScope', f.context), scopeBeforeFailedRead);
    assert.equal(f.vm.runInContext('nativeAutoEffective', f.context), effectiveBeforeFailedRead);
    assert.equal(f.stamp({frame: 30, game_time: 1}).native_autochess_scope.status, 'available');
  }
  const mark = f.mark();
  const result = f.context.rpc.exports.readEvents(0, mark.sequence);
  assert.equal(result.error, null, platform + ':' + kind + ':' + fault);
  const hit = result.events.find(e => e.kind === kind);
  assert(hit, platform + ':' + kind + ':' + fault);
  assert.equal(hit.source, raw);
  assert.equal(hit.source_character_uid, 102);
  const formProof = hit.proof.native_autochess_character_form;
  if (fault) {
    assert(result.events.every(e => e.proof.native_autochess_character_form === undefined), platform + ':' + fault);
    if (fault === 'other_character') assert.equal(prefabReads, 0, 'ordinary hit reads no form data');
    if (fault.endsWith('_in_proof')) assert(prefabReads > 0, 'fault exercised the actual witness read');
    if (fault === 'prefab_changed_in_closing_scope') assert(changedDuringClosingScope);
  } else {
    const {frame, game_time, scope_key, phase, round, collection_mode} = hit.native_autochess_scope;
    assert.deepEqual(JSON.parse(JSON.stringify(formProof)),
      {version: 1, entity_cid: raw, data_cid: form, data_uid: 102, instance_uid: 10,
        frame, game_time, scope_key, phase, round, collection_mode});
    assert(prefabReads >= 2, 'typed form checked at both reads');
    assert.equal(frame, 30);
    assert.equal(game_time, 1);
    assert(result.events.every(e => e === hit || e.proof.native_autochess_character_form === undefined));
  }
}

for (const platform of ['native_pc', 'mumu']) {
  scenario(platform, 'damage_settlement');
  scenario(platform, 'elemental_bar');
  for (const fault of ['same_form', 'other_character', 'token', 'predefined', 'data_uid_changed',
    'data_cid_changed', 'instance_uid_changed', 'source_class_changed', 'data_class_changed',
    'data_pointer_changed', 'source_pointer_changed', 'round_changed', 'phase_changed', 'effective_mode_changed',
    'clock_changed_in_proof', 'instance_uid_changed_in_proof', 'prefab_changed_in_closing_scope',
    'closing_scope_read_failed', 'reused_modifier']) {
    scenario(platform, 'elemental_bar', fault);
  }
}
