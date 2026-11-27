// Research-only; explicitly inserted into the shared test runtime.
// Called only behind the platform's native-issued leaf-span gate. The span
// owns every original E/L and exposes the same IO/clock/scope operations for
// ordinary pairs. Returning zero is allowed only before touching source state.
// A partial positive result names the exact consumed pair prefix; the caller
// must replay only its untouched suffix through the ordinary path.
function applyHpLeafFacts(span,compress){
 const count=span.count;
 if(!Number.isInteger(count)||count<1||count>16)throw new Error('invalid HP leaf fact count');
 const play=index=>span.playPair(index,
  (receiver,enemy,io)=>applyHpEntry(receiver,null,io,'Entity.SetHpInternal',false,enemy),
  (receiver,io)=>applyHpLeave(receiver,io,'Entity.SetHpInternal'));
 if(stopped||fatal||active!==0||activeHooks.size||activeApplyScopes.size||enemyHpWrites.size)return 0;
 if(!compress){for(let i=0;i<count;i++)play(i);return count;}
 if(count<3||stopped||fatal||!coalesceEnemyHp||!enemyEntryReady
    ||active!==0||activeHooks.size||activeApplyScopes.size||enemyHpWrites.size
    ||!Number.isSafeInteger(epoch)||!Number.isSafeInteger(epoch+count))return 0;
 const {enemy,klass,uid,closingUid,eid,born,frame,fixed,attributeCount,attributeWords}=span;
 if(!enemy||enemy.isNull()||!ptr(klass).equals(enemyEntryConfig.enemy)||born!==1||!uid
    ||typeof eid!=='string'||!eid||!Number.isInteger(frame)||frame<0||typeof fixed!=='bigint'
    ||closingUid!==uid||awaitingEnemyTile.has(uid)||pendingEnemyRebirth.has(uid))return 0;
 const game_time=Number(fixed)/4294967296;
 if(!Number.isFinite(game_time)||game_time<0||enemyEntryFrame!==null&&frame<enemyEntryFrame
    ||damageSettlementClock&&probeAdapter.clockRegressed(damageSettlementClock,frame,game_time))return 0;
 const values=span.hpValues;
 if(!Array.isArray(values)||values.length!==2*count)throw new Error('invalid HP leaf primitive count');
 let previous=null,secondAfter=null,last=null,unitTick=null,kind=null;
 for(let i=0;i<count;i++){
  const rawBefore=values[2*i],rawAfter=values[2*i+1];
  if(typeof rawBefore!=='bigint'||typeof rawAfter!=='bigint')throw new Error('invalid HP leaf primitive facts');
  const before=Number(rawBefore)/4294967296,after=Number(rawAfter)/4294967296;
  const observedKind=before===after?'unchanged':'heal';
  const tick=(eid==='enemy_1061_zomshd'||eid==='enemy_1061_zomshd_2')&&after-before===1;
  if(!Number.isFinite(before)||!Number.isFinite(after)||before<0||after<before||after>=1e9
     ||i&&previous!==before||kind!==null&&kind!==observedKind||unitTick!==null&&unitTick!==tick)return 0;
  kind=observedKind;unitTick=tick;previous=after;last=after;if(i===1)secondAfter=after;
 }
 let maxHp=null;
 if(kind==='heal'){
  const birth=enemyHpBirths.get(String(uid));
  if(!enemyMaxHpReady||!enemyAttributesReady||birth?.eid!==eid||frame<=birth.frame+1||Number(values[0])/4294967296<=0
     ||!Number.isInteger(attributeCount)||attributeCount<1||attributeCount>64||!Array.isArray(attributeWords)||attributeWords.length!==3)return 0;
  maxHp=decodeEnemyMaxHpWords(attributeWords[0],attributeWords[1],attributeWords[2]);
  if(maxHp===null||last>maxHp)return 0;
 }
 // Preserve the first heal and the first continuation with the exact ordinary
 // reducer, including finishModifierHpWriter before its unchanged return.
 if(play(0)!==true)return 1;
 if(play(1)!==true)return 2;
 if(kind==='unchanged'){
  if(stopped||fatal||active!==0||activeHooks.size||enemyHpWrites.size)return 2;
  epoch+=count-2;damageSettlementClock={...damageSettlementClock};return count;
 }
 const key=enemy.toString(),run=enemyHpRuns.get(key),when=run?.when;
 if(stopped||fatal||active!==0||activeHooks.size||enemyHpWrites.size||!run?.tail
    ||run.key!==key||run.uid!==String(uid)||run.eid!==eid||run.maxHp!==maxHp||run.after!==secondAfter
    ||run.unitTick!==unitTick||when.frame!==frame||when.game_time!==game_time
    ||when.settlementScope!==damageSettlementScope||!sameNativeEventScope(when,when)
    ||enemyHpWindow?.thread!==span.threadId)return 2;
 // The native span proves that all remaining independent clocks/scope bytes
 // and attribute words are equal and fresh, with no intervening callback or
 // observation boundary. Source still checked every individual HP delta.
 extendEnemyHpRun(run,last,when,'Entity.SetHpInternal',run.tail.evidence,unitTick?count-2:0);
 epoch+=count-2;
 // No observer can access the internal intermediate sample objects. Preserve
 // the final clock fields while keeping the stored continuation's own sample.
 damageSettlementClock={...damageSettlementClock};
 return count;
}
