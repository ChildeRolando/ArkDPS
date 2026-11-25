// Bounded diagnostic copies exercise the actual runtime helper without reads.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const f=fixture({settlement:true,sourcePath:path.join(__dirname,'..','event_probe_native_pc.js')});
const result=vm.runInContext(`(()=>{
 const scope={source:'char_test',sourceCharacterUid:301,name:'Entity.ApplyModifier',
  targetHit:{hpBefore:50},settlementObservedHp:{hp_before:50,hp_after:11,real_delta:-40},
  settlementFailureCheckpoint:{nativeAmount:40,cancel:0},targetCommitRejected:true,
  settlementAnchor:{identity:{target:'enemy_test',target_uid:'12',target_instance:'0x123'}},
  mod:{readPointer(){throw new Error('diagnostic performed native read');}}};
 const when={frame:30,game_time:1,settlementScope:damageSettlementScope};
 captureSettlementFailureSample(scope,when,'first rejection');
 const first=damageSettlementQuality();
 scope.settlementObservedHp.hp_after=999;
 for(let n=0;n<50;n++)captureSettlementFailureSample(scope,when,'first rejection');
 const repeated=damageSettlementQuality();
 for(let n=0;n<100;n++)captureSettlementFailureSample({...scope,source:'char_'+n},when,'other rejection');
 const bounded=damageSettlementQuality();
 resetDamageSettlementScope();
 return {first,repeated,bounded,reset:damageSettlementQuality()};
})()`,f.context);
assert.equal(result.first.damage_settlement_failure_samples.length,1);
assert.equal(result.repeated.damage_settlement_failure_samples.length,1);
assert.equal(result.repeated.damage_settlement_failure_samples[0].hp_after,11);
assert.equal(result.bounded.damage_settlement_failure_samples.length,16);
assert.equal(result.reset.damage_settlement_failure_samples.length,0);
process.stdout.write(JSON.stringify(result.bounded));
