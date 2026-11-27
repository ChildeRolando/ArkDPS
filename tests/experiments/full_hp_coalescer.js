// Research-only; explicitly inserted into the shared test runtime.
// Keep the first heal intact. It may establish the HP and MaxHP needed to
// prove subsequent healing. An exactly contiguous, increasing tail with the
// same verified MaxHP then contributes its last HP minus its first HP.
const enemyHpBirths=new Map();
const ENEMY_HP_RUN_LIMIT=128;
const enemyHpRuns=new Map();
let enemyHpWindow=null;
function flushCoalescedEnemyHp(){
 // Only unpublished tails are mutable. Every transport/authoritative boundary
 // drains this bounded window before exposing a sequence. Targets are independent;
 // within each target the first witness and all damage boundaries stay ordered.
 const runs=[...enemyHpRuns.values()];
 enemyHpRuns.clear();enemyHpWindow=null;
 for(const {tail} of runs)if(tail)enqueueEvent(null,'enemy_hp',0,tail.when,{method:tail.method},tail.evidence);
}
function resetEnemyHpCoalescing(){
 if(!coalesceEnemyHp)return;
 flushCoalescedEnemyHp();
 enemyHpBirths.clear();
}
