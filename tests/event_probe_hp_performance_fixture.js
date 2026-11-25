// Bounded owned-memory execution of the production setter hooks. No game access.
const assert=require('node:assert/strict');
const {scenario}=require('./event_probe_enemy_hp_fixture.js');
const frozen=require('./fixtures/enemy_hp_r5_frame1556.json');
const fp=v=>BigInt(Math.round(v*4294967296));
const plain=v=>JSON.parse(JSON.stringify(v));
function replay(platform,raw){
 const firstByUid=new Map();
 for(const e of frozen.events)if(!firstByUid.has(e.instance_uid))firstByUid.set(e.instance_uid,e);
 let owner=null;const units=new Map();
 for(const [uid,e]of firstByUid){
  const unit=scenario(platform,raw,e.eid,owner,{initialHp:e.hp_before-1,maxHp:e.enemy_max_hp});
  owner??=unit;units.set(uid,unit);
 }
 // Establish the first real recovery in a preceding frame. Values below are
 // still read from owned memory on each callback, never supplied as evidence.
 owner.f.clock(1555,fp(51));
 for(const [uid,e]of firstByUid)units.get(uid).setHp(e.hp_before);
 const prefix=owner.read();
 owner.f.clock(1556,fp(52));
 for(const e of frozen.events)units.get(e.instance_uid).setHp(e.hp_after);
 const batch=owner.read();
 assert.equal(batch.enemy_state_unknown,false);
 assert.deepEqual(batch.events.slice(0,prefix.events.length),prefix.events);
 return batch;
}
const pairs=[];
for(const platform of ['native_pc','mumu']){
 const raw=replay(platform,true),compact=replay(platform,false);
 assert(compact.events.length<raw.events.length,'real +1/+2 fragment must reduce queued records');
 pairs.push({platform,raw,compact});
 // Interleaved targets: each first witness survives; subsequent same-frame
 // tails remain independent and are frozen before a mark/read/ACK boundary.
 const a=scenario(platform),b=scenario(platform,false,'enemy_b',a);
 a.setHp(101);b.setHp(101);a.setHp(102);b.setHp(102);a.setHp(104);b.setHp(104);
 const first=a.read();
 assert.equal(first.events.filter(e=>e.kind==='enemy_hp').length,4);
 const delivered=plain(first);
 const ack=a.f.context.rpc.exports.ackEvents(first.events.at(-1).sequence);
 assert.equal(ack.error,null);
 a.f.clock(31,fp(2));a.setHp(105);b.setHp(105);a.setHp(107);b.setHp(107);
 const mark=a.f.context.rpc.exports.mark();
 const next=plain(a.f.context.rpc.exports.readEvents(first.events.at(-1).sequence,mark.sequence));
 assert.equal(next.events.length,2,'new frame may compact after the immutable first healing witness');
 assert.deepEqual(first,delivered);
 a.setHp(90);a.setHp(91);a.setHp(80);a.setHp(82);
 const end=a.f.context.rpc.exports.mark();
 const hits=plain(a.f.context.rpc.exports.readEvents(mark.sequence,end.sequence)).events;
 assert.deepEqual(hits.map(e=>[e.hp_before,e.hp_after]),[[107,90],[90,91],[91,80],[80,82]]);
 // A different callback thread is a publication boundary, even in one frame.
 const t=scenario(platform);t.setHp(101);t.setHp(102);t.setHp(103,undefined,undefined,2);t.setHp(104,undefined,undefined,1);
 assert.deepEqual(t.read().events.slice(1).map(e=>[e.hp_before,e.hp_after]),[[100,101],[101,102],[102,103],[103,104]]);
 // Concurrent setters may not certify a shared before/after interval.
 const c=scenario(platform),cb=c.f.callback('SetHpInternal',c.f.entity),outer={threadId:1},other={threadId:2};
 cb.onEnter.call(outer,[c.enemy]);cb.onEnter.call(other,[c.enemy]);
 c.f.write(c.enemy.add(0x40),fp(110));cb.onLeave.call(other);cb.onLeave.call(outer);
 assert.equal(c.read().enemy_state_unknown,true);
 // Tail overflow remains a visible fatal gap, with no fake ACK success.
 const o=scenario(platform);o.setHp(101);o.setHp(102);
 o.f.vm.runInContext('queue.length=EVENT_BUFFER_LIMIT',o.f.context);
 assert.match(o.f.context.rpc.exports.mark().error,/overflow/);
 // More independent targets than the fixed staging budget force a safe drain.
 const many=scenario(platform),group=[many];
 for(let i=1;i<130;i++)group.push(scenario(platform,false,'enemy_'+i,many));
 for(const unit of group)unit.setHp(101);
 assert(many.f.vm.runInContext('enemyHpRuns.size',many.f.context)<=128);
 assert.equal(many.read().enemy_state_unknown,false);
}
console.log(JSON.stringify({classification:'owned_memory_not_live',provenance:frozen.provenance,pairs}));
