// Research-only; explicitly inserted into the shared test runtime.
// The single update operation for an already proven contiguous, unpublished
// tail. Ordinary observations supply one tick; a verified leaf-fact group can
// reuse this operation only after checking every original observation.
function extendEnemyHpRun(run,hp,when,method,evidence,extraUnitTicks){
 if(extraUnitTicks)run.tail.evidence.enemy_regen_ticks=(run.tail.evidence.enemy_regen_ticks??1)+extraUnitTicks;
 if(run.tail)run.tail.evidence.hp_after=hp;
 else run.tail={when,method,evidence};
 run.after=hp;
}
function recordEnemyHp(write,when,method,hp,maxHp){
 const {key,evidence,hpBefore,maxHpBefore}=write;
 if(maxHp!==null)evidence.enemy_max_hp=maxHp;
 evidence.hp_before=hpBefore;
 evidence.hp_after=hp;
 if(coalesceEnemyHp){
  const {target:eid,target_instance:uid}=evidence;
  const birth=enemyHpBirths.get(uid);
  if(write.enemyTarget&&enemyMaxHpReady&&method==='Entity.SetHpInternal'&&maxHpBefore===null
     &&birth?.eid===eid&&when.frame>birth.frame+1&&Number.isFinite(maxHp)
     &&0<hpBefore&&hpBefore<hp&&hp<=maxHp){
   // Preserve the first +1 as an explicit continuity witness in the compact record.
   const unitTick=(eid==='enemy_1061_zomshd'||eid==='enemy_1061_zomshd_2')&&hp-hpBefore===1;
   const window=enemyHpWindow;
   if(window&&(window.thread!==write.thread||window.when.frame!==when.frame||
      window.when.game_time!==when.game_time||window.when.settlementScope!==when.settlementScope||
      !sameNativeEventScope(window.when,when)))flushCoalescedEnemyHp();
   const run=enemyHpRuns.get(key);
   if(run&&run.unitTick===unitTick&&run.key===key&&run.uid===uid&&run.eid===eid&&run.maxHp===maxHp
      &&run.when.frame===when.frame&&run.when.game_time===when.game_time
      &&run.when.settlementScope===when.settlementScope
      &&sameNativeEventScope(run.when,when)&&run.after===hpBefore){
    extendEnemyHpRun(run,hp,when,method,evidence,unitTick?1:0);
    return true;
   }
   // A discontinuity for one target is a boundary for the entire window.
   if(run||enemyHpRuns.size>=ENEMY_HP_RUN_LIMIT)flushCoalescedEnemyHp();
   const witnessed=birth.healWitness;
   const first=!witnessed||witnessed.maxHp!==maxHp||
    witnessed.when.settlementScope!==when.settlementScope||!sameNativeEventScope(witnessed.when,when);
   enemyHpWindow={thread:write.thread,when};
   if(unitTick||!first){
    // Stage even the first tick so an already published sequence is immutable.
    enemyHpRuns.set(key,{key,uid,eid,when,maxHp,after:hp,unitTick,tail:{when,method,evidence}});
   }else if(enqueueEvent(null,'enemy_hp',0,when,{method},evidence)){
    enemyHpRuns.set(key,{key,uid,eid,when,maxHp,after:hp,unitTick,tail:null});
    birth.healWitness={when,maxHp};
   }
   return enemyHpRuns.get(key)?.after===hp;
  }
 }
 return !!record(null,'enemy_hp',0,when,{method},evidence);
}
