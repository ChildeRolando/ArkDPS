// Run the shared setter hooks with owned enemy memory on both probe adapters.
const assert = require('node:assert/strict');
const path = require('node:path');
const {fixture} = require('./native_pc_frida_probe_fixture.js');

const fp = value => BigInt(value) * 4294967296n;
const plain = value => JSON.parse(JSON.stringify(value));

function scenario(platform, raw = false, eid = 'enemy_test') {
  const adapter = platform === 'native_pc' ? 'event_probe_native_pc.js' : 'event_probe_android2791.js';
  const f = fixture({platform, enemyHpState: true, sourcePath: path.join(__dirname, '..', adapter),
    globals: raw ? {arkdpsCoalesceEnemyHp: false} : {}});
  let phase = 4, mode = 'local', stamps = 0;
  f.context.observeScope = when => {
    stamps++;
    when.native_autochess_scope = {protocol_version: 1, status: 'available', verified_same_frame: true,
      frame: when.frame, game_time: when.game_time, scope_key: 'synthetic:round:8',
      phase, round: 8, collection_mode: mode};
  };
  f.vm.runInContext('probeAdapter.stampClock = observeScope', f.context);

  const enemy = f.unit(eid, 12), attributes = f.alloc(), cache = f.alloc();
  const maxHpSlot = cache.add(0x20);
  f.write(enemy, f.enemyClass);
  f.write(enemy.add(0x40), fp(100));
  f.write(enemy.add(0x25), 0);
  f.write(enemy.add(0x26), 0);
  f.write(enemy.add(0x360), f.alloc());
  f.write(enemy.add(0xb0), attributes);
  f.write(attributes.add(0x68), cache);
  f.write(cache.add(0x18), 1);
  f.write(maxHpSlot, 0n);
  f.write(maxHpSlot.add(8), fp(1000));
  f.write(maxHpSlot.add(0x10), 1);

  f.clock(1, 0n);
  const birth = f.callback('Born', f.bObject), birthCall = {};
  birth.onEnter.call(birthCall, [enemy]);
  f.write(enemy.add(0x25), 1);
  birth.onLeave.call(birthCall);
  f.clock(30, fp(1));

  function setHp(hp, change = () => {}, method = 'SetHpInternal') {
    const callback = f.callback(method, f.entity), call = {};
    const before = stamps;
    callback.onEnter.call(call, [enemy]);
    f.write(enemy.add(0x40), fp(hp));
    change();
    callback.onLeave.call(call);
    return stamps - before;
  }
  function setMaxHp(before, after) {
    const callback = f.callback('OnAttributeDirty', f.entity), call = {};
    const stampsBefore = stamps;
    callback.onEnter.call(call, [enemy, f.ptr(0), f.ptr(fp(before))]);
    f.write(maxHpSlot.add(8), fp(after));
    callback.onLeave.call(call);
    return stamps - stampsBefore;
  }
  function read() {
    const mark = f.context.rpc.exports.mark();
    assert.equal(mark.active, 0);
    return plain(f.context.rpc.exports.readEvents(0, mark.sequence));
  }
  return {f, enemy, setHp, setMaxHp, read,
    setPhase: value => {phase = value;}, setMode: value => {mode = value;}};
}

for (const platform of ['native_pc', 'mumu']) {
  for (const raw of [false, true]) {
    const s = scenario(platform, raw);
    assert.equal(s.setHp(100), raw ? 2 : 0, 'an unchanged setter need not stamp attachment scope');
    for (let hp = 110; hp <= 170; hp += 10)
      assert.equal(s.setHp(hp), raw ? 2 : 1, 'a changed setter retains a verified closing scope');
    assert.equal(s.setHp(180, () => s.setPhase(5)), raw ? 2 : 1);
    assert.equal(s.setHp(190), raw ? 2 : 1);
    assert.equal(s.setHp(200, () => s.setMode('all_defense')), raw ? 2 : 1);
    assert.equal(s.setHp(210), raw ? 2 : 1);
    assert.equal(s.setHp(220), raw ? 2 : 1);
    assert.equal(s.setHp(215), raw ? 2 : 1, 'negative HP changes stay individual');
    assert.equal(s.setHp(230, undefined, 'SetHpInternalDirectly'), 2, 'direct setters retain both scope checks');

    const batch = s.read();
    assert.equal(batch.enemy_state_unknown, false);
    assert.equal(batch.events[0].kind, 'enemy_enter');
    const hpEvents = batch.events.slice(1);
    assert(hpEvents.every(event => event.kind === 'enemy_hp' && event.enemy_max_hp === 1000));
    assert.deepEqual(hpEvents.map(event => event.sequence), hpEvents.map((_, i) => i + 2));
    const observed = hpEvents.map(event => [event.hp_before, event.hp_after,
      event.native_autochess_scope.phase, event.native_autochess_scope.collection_mode]);
    const expected = raw
      ? Array.from({length: 7}, (_, i) => [100 + i * 10, 110 + i * 10, 4, 'local'])
      : [[100, 110, 4, 'local'], [110, 170, 4, 'local']];
    expected.push([170, 180, 5, 'local'], [180, 190, 5, 'local'], [190, 200, 5, 'all_defense']);
    if (raw) expected.push([200, 210, 5, 'all_defense'], [210, 220, 5, 'all_defense']);
    else expected.push([200, 220, 5, 'all_defense']);
    expected.push([220, 215, 5, 'all_defense'], [215, 230, 5, 'all_defense']);
    assert.deepEqual(observed, expected);
    assert.equal(hpEvents.at(-1).proof.method, 'Entity.SetHpInternalDirectly');
  }

  for (const eid of ['enemy_1061_zomshd', 'enemy_1061_zomshd_2', 'enemy_test']) {
    const s = scenario(platform, false, eid);
    for (let hp = 101; hp <= 107; hp++) s.setHp(hp);
    const first = s.read();
    const ranges = batch => batch.events.slice(1).map(e => [e.hp_before, e.hp_after, e.enemy_regen_ticks]);
    const shieldguard = eid !== 'enemy_test';
    assert.deepEqual(ranges(first), shieldguard ? [[100, 107, 7]] : [[100, 101, undefined], [101, 107, undefined]]);
    s.setHp(108);
    s.setHp(109, () => s.setPhase(5));
    s.setHp(110);
    s.setHp(120); // A separate larger recovery on the same frame.
    s.setHp(80);  // Damage breaks the run.
    s.setHp(81);
    const next = s.read();
    assert.deepEqual(next.events.slice(0, first.events.length), first.events);
    if (shieldguard) assert.deepEqual(ranges(next).slice(1), [
      [107, 108, undefined], [108, 110, 2], [110, 120, undefined],
      [120, 80, undefined], [80, 81, undefined],
    ]);
  }

  const attributes = scenario(platform);
  attributes.setHp(110);
  attributes.setHp(120);
  assert.equal(attributes.setMaxHp(1000, 1200), 2);
  attributes.setHp(130);
  assert.deepEqual(attributes.read().events.slice(1).map(event => [
    event.hp_before, event.hp_after, event.enemy_max_hp, event.proof.method,
  ]), [
    [100, 110, 1000, 'Entity.SetHpInternal'],
    [110, 120, 1000, 'Entity.SetHpInternal'],
    [120, 120, 1200, 'Entity.OnAttributeDirty'],
    [120, 130, 1200, 'Entity.SetHpInternal'],
  ]);

  const rounds = scenario(platform);
  rounds.setHp(110);
  rounds.setHp(120);
  rounds.f.vm.runInContext('resetDamageSettlementScope()', rounds.f.context);
  rounds.setHp(130);
  rounds.setHp(140);
  rounds.setHp(150);
  assert.deepEqual(rounds.read().events.slice(1).map(event => event.hp_after), [110, 120, 130, 150]);

  for (const change of [
    s => s.f.clock(31, fp(1)),
    s => s.f.clock(30, fp(2)),
    s => s.f.write(s.enemy.add(0x18), 42),
  ]) {
    const s = scenario(platform);
    s.setHp(110);
    s.setHp(120);
    assert.equal(s.setHp(130, () => change(s)), 0, 'a rejected setter does not stamp a publishable scope');
    const batch = s.read();
    assert.equal(batch.enemy_state_unknown, true);
    assert.deepEqual(batch.events.filter(event => event.kind === 'enemy_hp').map(event => event.hp_after), [110, 120]);
  }
}
