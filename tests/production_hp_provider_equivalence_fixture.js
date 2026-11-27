// Exact raw envelopes from real pointer-provider callbacks; no copied HP reducer.
const {scenario}=require('./event_probe_enemy_hp_fixture.js');
const fp=x=>BigInt(Math.round(x*4294967296));
const rows=[];
for(const platform of ['native_pc','mumu'])for(const raw of [false,true]){
 function save(name,s){rows.push({platform,raw,name,envelope:s.read(),state:s.f.vm.runInContext('({active,enemyStateUnknown,writes:enemyHpWrites.size})',s.f.context)});}
 let s=scenario(platform,raw);s.setHp(100);s.setHp(110);s.setHp(120);save('first_heal_and_unchanged',s);
 s=scenario(platform,raw);const slot=s.enemy.add(0xb0).readPointer().add(0x68).readPointer().add(0x20);s.f.write(slot,17n);s.f.write(slot.add(8),fp(1400)^17n);s.setHp(110);s.setMaxHp(1400,1600);s.setHp(120);save('xor_maxhp_dirty',s);
 s=scenario(platform,raw);s.setHp(70);s.setHp(75);s.setHp(75);s.setHp(90,undefined,'SetHpInternalDirectly');save('damage_heal_direct',s);
 for(const thread of [1,2]){s=scenario(platform,raw);s.setHp(80,()=>s.setHp(70,undefined,'SetHpInternal',thread));save('nested_thread_'+thread,s);}
 for(const kind of ['uid','clock_frame','clock_fixed']){s=scenario(platform,raw);s.setHp(110);s.setHp(120,()=>{if(kind==='uid')s.f.write(s.enemy.add(0x18),42);else s.f.clock(kind==='clock_frame'?31:30,fp(kind==='clock_fixed'?2:1));});save(kind+'_reject',s);}
 s=scenario(platform,raw);s.f.clock(0,0n);s.setHp(105);save('zero_frame',s);
 s=scenario(platform,raw);s.setHp(101);s.setHp(102);const first=s.read();s.f.context.rpc.exports.ackEvents(first.events.at(-1).sequence);s.setHp(103);s.setHp(104);const mark=s.f.context.rpc.exports.mark();rows.push({platform,raw,name:'ack_prefix',first,after:s.f.context.rpc.exports.readEvents(first.events.at(-1).sequence,mark.sequence)});
}
console.log(JSON.stringify({kind:'exact_pointer_provider_envelopes',rows}));
