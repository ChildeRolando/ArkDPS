// Diagnostic observations must not become combat events or change quantities.
const assert = require('node:assert/strict');
const path = require('node:path');
const {fixture} = require('./native_pc_frida_probe_fixture.js');

function run(platform, enabled) {
  const f = fixture({platform, settlement:true, traceHpContext:true, traceNestedParent:enabled,
    sourcePath:path.join(__dirname, '..', platform === 'native_pc' ?
      'event_probe_native_pc.js' : 'event_probe_android2791.js')});
  f.clock(30, 4294967296n);
  const source=f.unit('char_4247_oxi',10), target=f.unit('enemy_test',12);
  const parent=f.modifier(source,target,20,0,2), child=f.modifier(f.ptr(0),target,30,0,5);
  const apply=f.callback('ApplyModifier',f.entity), take=f.callback('OnTakeDamage',f.unitClass);
  const outer={threadId:1}, hit={threadId:1}, inner={threadId:1};
  f.write(target.add(0x40),50n*4294967296n);
  apply.onEnter.call(outer,[target,parent,f.ptr(0)]);
  take.onEnter.call(hit,[target,parent,f.ptr(0)]);
  take.onLeave.call(hit);
  f.write(parent.add(0x48),-20n*4294967296n);
  f.write(target.add(0x40),30n*4294967296n);
  apply.onEnter.call(inner,[target,child,f.ptr(0)]);
  f.write(child.add(0x48),-30n*4294967296n);
  f.write(target.add(0x40),0n);
  apply.onLeave.call(inner);
  apply.onLeave.call(outer);
  const mark=f.context.rpc.exports.mark();
  return f.context.rpc.exports.readEvents(0,mark.sequence);
}

const result={};
for (const platform of ['android','native_pc']) {
  const baseline=run(platform,false), observed=run(platform,true);
  assert.deepEqual(JSON.parse(JSON.stringify(observed.events)),JSON.parse(JSON.stringify(baseline.events)));
  for (const key of ['error','stopped','enemy_damage_unknown','damage_unknown','mitigation_unknown'])
    assert.deepEqual(observed[key],baseline[key]);
  assert.equal('nested_parent_trace_count' in baseline,false);
  const traces=observed.hp_context.filter(x=>x.kind==='nested_parent_checkpoint');
  assert.equal(traces.length,3);
  assert.deepEqual(JSON.parse(JSON.stringify(traces.map(x=>x.phase))),['child_entry','child_return','parent_return']);
  assert.equal(traces[0].parent_real_delta,-20);
  assert.equal(traces[0].parent_target_hit_hp_before,50);
  assert.equal(traces[0].target_hp,30);
  assert.equal(traces[1].parent_real_delta,-20);
  assert.equal(traces[1].child_real_delta,-30);
  assert.equal(traces[1].target_hp,0);
  assert.equal(traces[2].parent_real_delta,-20);
  assert.equal(traces[2].target_hp,0);
  assert.equal(observed.nested_parent_trace_count,3);
  assert.equal(observed.nested_parent_trace_dropped,0);
  result[platform]={unchanged_events:true,checkpoints:traces.length};
}
process.stdout.write(JSON.stringify(result));
