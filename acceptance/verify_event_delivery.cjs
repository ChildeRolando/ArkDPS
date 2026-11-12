// Execute the product's queue and message transport without a game connection.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../event_probe_android2791.js'), 'utf8');
function between(first, last) {
  const start = source.indexOf(first);
  const end = source.indexOf(last, start + first.length);
  assert(start >= 0 && end > start, `missing product section: ${first}`);
  return source.slice(start, end);
}

vm.runInNewContext(`
let sequence=0,epoch=0,active=0,fatal=null,stopped=false;
const activeHooks=new Map(),hooks=[];
const rpc={};
let receiver=null, response=null, requestId=0;
function recv(name,callback) {
  assert.equal(name,'arkdps-event-request');
  assert.equal(receiver,null);
  receiver=callback;
}
function send(value){response=value;}
function time(){return {frame:30,game_time:1};}
function eventQuality(){return {skill_ready:true};}
${between('const EVENT_BUFFER_LIMIT', 'const stacks=')}
${between('function record(source', 'function refreshEnemyEntryFrame')}
${between('function markEvents()', 'send({ready:true')}

function request(operation,fields={}){
  assert(receiver);
  const callback=receiver;
  receiver=null;
  response=null;
  const id=++requestId;
  callback({type:'arkdps-event-request',id,operation,...fields});
  assert.equal(response.kind,'arkdps-event-response');
  assert.equal(response.id,id);
  return response.result;
}
function emit(amount){record('char_a','damage',amount,time(),{},{});}
function numbers(batch){return batch.events.map(event=>event.sequence).join(',');}
function diag(batch){return batch.hp_context.map(context=>context.sequence).join(',');}
function read(after,through){return request('read',{after,through});}
function ack(sequence){return request('ack',{sequence});}

emit(1);
recordHpContext({sequence:1});
emit(2);
const mark=request('mark');
assert.equal(mark.sequence,2);
assert.equal(mark.skill_ready,true);
assert.match(ack(2).error,/read watermark/);
const first=read(0,mark.sequence);
assert.equal(numbers(first),'1,2');
assert.equal(diag(first),'1');

// The first reply is lost. A subsequent event and diagnostic must wait
// for the next mark, while the original range stays replayable.
recordHpContext({sequence:2});
emit(3);
assert.deepEqual(read(0,mark.sequence),first);
assert.equal(ack(2).acknowledged,2);
// A lost ACK response can be retried without releasing anything else.
assert.equal(ack(2).acknowledged,2);
assert.equal(ack(1).acknowledged,2);
assert.match(read(0,2).error,/unavailable/);
const next=read(2,3);
assert.equal(numbers(next),'3');
assert.equal(diag(next),'2');
assert.equal(ack(3).acknowledged,3);
assert.equal(queue.length,0);
assert.equal(hpContext.length,0);

// An empty baseline read authorizes dropping only the previous prefix.
emit(4);
emit(5);
assert.equal(numbers(read(5,5)),'');
assert.equal(ack(5).acknowledged,5);
emit(6);
assert.equal(numbers(read(5,6)),'6');
assert.equal(ack(6).acknowledged,6);
assert.match(read(6,7).error,/unavailable/);
assert.match(request('unknown').error,/unknown/);

for(let index=0;index<=HP_CONTEXT_BUFFER_LIMIT;index++)recordHpContext({sequence:index});
emit(7);
const diagnostics=read(6,7);
assert.equal(diagnostics.hp_context.length,HP_CONTEXT_BUFFER_LIMIT);
assert.equal(diagnostics.hp_context_dropped,1);
assert.equal(ack(7).acknowledged,7);
assert.equal(hpContext.length,0);

for(let index=0;index<EVENT_BUFFER_LIMIT;index++)emit(index);
assert.equal(queue.length,EVENT_BUFFER_LIMIT);
const full=request('mark');
emit(0);
assert.match(request('mark').error,/overflow/);
assert.match(read(7,full.sequence).error,/overflow/);
assert.equal(sequence,full.sequence);
assert.equal(queue.length,EVENT_BUFFER_LIMIT);
`, {assert}, {filename: 'product-event-delivery'});

vm.runInNewContext(`
let enemyDamageUnknown=false;
const enemyDamageReady=true;
const enemyEntryConfig={bObject:{},assignable:(_base,klass)=>klass===1};
const emitted=[];
const targetKey=target=>target?.eid;
const own=source=>source.startsWith('char_')||source.startsWith('token_');
const record=(source,kind,amount,when,proof,evidence)=>emitted.push({source,kind,amount,evidence});
${between('function enemyDamageIdentity(', 'function includeEnemyHp(')}
${between('function nonlethalSettlement(', 'function dynamic(')}
const mod=type=>({add:offset=>({readS32:()=>{
  assert.equal(offset,0x68);
  return type;
}})});
const target={
  eid:'trap_box',readPointer:()=>1,toString:()=>'0x1',
  add:offset=>({readU32:()=>{
    assert.equal(offset,0x18);
    return 123;
  }})
};
recordEnemyDamage({source:'char_a',real:-17,mod:mod(2),target},{frame:1});
recordEnemyDamage({source:'char_a',real:0,mod:mod(2),target},{frame:1});
recordEnemyDamage({source:'enemy_a',real:-17,mod:mod(2),target},{frame:1});
assert.equal(emitted.length,1);
assert.equal(emitted[0].kind,'enemy_damage');
assert.equal(emitted[0].amount,17);
assert.equal(emitted[0].evidence.target_uid,'123');
assert.equal(enemyDamageUnknown,false);

const when={frame:2};
const burst={target:target.eid,real_delta:-7};
const sourceNull={source:null,nativeSourceNull:true,real:-7,mod:mod(5),target,
                  burstTarget:enemyDamageIdentity(target)};
const sourceOwned={...sourceNull,source:'char_a',nativeSourceNull:false};
const sourceEnemy={...sourceOwned,source:'enemy_a'};
recordEnemyDamage(sourceNull,when);
assert.equal(emitted.length,1);
recordSourcelessEnemyBurst(sourceNull,burst,when);
assert.equal(emitted.length,2);
assert.equal(emitted[1].source,null);
assert.equal(emitted[1].amount,7);
assert.equal(emitted[1].evidence.target_uid,'123');
assert.equal(emitted[1].evidence.damage_type,5);
recordEnemyDamage(sourceOwned,when);
recordSourcelessEnemyBurst(sourceOwned,burst,when);
recordEnemyDamage(sourceEnemy,when);
recordSourcelessEnemyBurst(sourceEnemy,burst,when);
assert.equal(emitted.length,3);
assert.equal(emitted[2].source,'char_a');
assert.equal(enemyDamageUnknown,false);

recordSourcelessEnemyBurst({...sourceNull,nativeSourceNull:false},burst,when);
assert.equal(emitted.length,3);
assert.equal(enemyDamageUnknown,true);
enemyDamageUnknown=false;
recordSourcelessEnemyBurst({...sourceNull,burstTarget:{...sourceNull.burstTarget,target_uid:'122'}},burst,when);
assert.equal(emitted.length,3);
assert.equal(enemyDamageUnknown,true);
enemyDamageUnknown=false;
recordEnemyDamage({source:'char_a',real:-17,mod:mod(2),target:null},{frame:1});
assert.equal(enemyDamageUnknown,true);
assert.equal(nonlethalSettlement(target,30,-17,17).hp_after,13);
assert.equal(nonlethalSettlement(target,17,-17,17),null);
`, {assert}, {filename: 'product-enemy-settlement'});

vm.runInNewContext(`
const hooks=[],activeHooks=new Map(),registered=new Map(),cfg={};
let active=0,epoch=0,frame=30,gameTime=1;
const mitigationMethods={outer:'outer',internal:'internal',final:'final',distance:'distance',damageType:0};
const Interceptor={attach(address,hook){
  registered.set(address,hook);
  return {detach(){registered.delete(address);}};
}};
const time=()=>({frame,game_time:gameTime});
const targetKey=pointer=>pointer.id;
const entityId=pointer=>pointer.id;
const own=id=>id.startsWith('char_');
const fixedArg=value=>Number(BigInt(value.toString()))/4294967296;
${between('const activeModifiers=new Map();', 'function markShieldUnknown(')}
${between('// One optional observation joins the existing ApplyModifier damage event.', 'function includeEnemyHp(')}
installMitigationHooks();
assert.equal(mitigationReady,true);
assert.equal(mitigationDistanceReady,true);
assert.equal(hooks.length,4);

const raw=value=>BigInt(Math.round(value*4294967296));
function pointer(address,id='',type=1){
  return {
    id,equals(other){return address===other.toString();},
    isNull(){return false;},toString(){return address;},add(offset){assert.equal(offset,0);return this;},readS32(){return type;}
  };
}
const source=pointer('0x101','char_one'),target=pointer('0x202','enemy_one');
const other=pointer('0x203','enemy_two'),attack=pointer('0x303'),copy=pointer('0x404');
const flag=value=>({toInt32:()=>value});
function enter(address,args,threadId=7){
  const hook=registered.get(address);
  const context={threadId};
  hook.onEnter.call(context,args);
  return result=>hook.onLeave.call(context,result);
}
function calculate(before,after,to=target,actionFlags=[1,1],from=source,thread=7,type=1){
  const initiallyActive=active;
  const info=type===1?attack:pointer('0x303','',type), internalCopy=type===1?copy:pointer('0x404','',type);
  const finish=enter('outer',[to,from,...actionFlags.map(flag),info],thread);
  assert.equal(active,initiallyActive+1);
  const finishInner=enter('internal',[internalCopy,raw(0),raw(0),to],thread);
  const finishPre=enter('final',[internalCopy],thread);
  finishPre(raw(before));finishInner(raw(after));finish(raw(after));
  assert.equal(active,initiallyActive);
}
function modifier(after,type=1,cancel=0,to=target,from=source){
  const fields={after,type,cancel,to,from};
  return {
    fields,
    equals(other){return this===other;},
    readPointer:()=>fields.from,
    add:offset=>({
      readS32:()=>({0x60:0,0x68:fields.type,0x70:fields.cancel})[offset],
      readS64:()=>raw(({0x30:fields.after,0x80:fields.originAtk,0x88:fields.originValue})[offset]),
      readPointer:()=>fields.to,
    }),
  };
}
function openModifier(mod,thread=7){
  const scope={tid:thread,source:mod.fields.from.id,target:mod.fields.to,when:time(),mod};
  enterModifier(scope);
  captureMitigation(scope,mod.fields.from);
  return scope;
}
function apply(after,to=target,thread=7,type=1,from=source){
  return openModifier(modifier(after,type,0,to,from),thread);
}
function ratio(scope,amount,type=1){
  try{return JSON.stringify(mitigationEvidence(scope,type,amount));}
  finally{leaveModifier(scope);}
}

calculate(100,60);
assert.equal(ratio(apply(60,target,8),60),'null');
assert.equal(ratio(apply(60,target,7,1,pointer('0x102','char_two')),60),'null');
assert.equal(ratio(apply(60,other),60),'null');
assert.equal(ratio(apply(60,target,7,2),60,2),'null');
assert.equal(ratio(apply(60),60),'{"mitigation_before":100,"mitigation_after":60}');
assert.equal(ratio(apply(60),60),'null');

calculate(200,80);
assert.equal(ratio(apply(79),79),'null');
assert.equal(ratio(apply(80),80),'null');

calculate(100,60);
calculate(101,61);
assert.equal(ratio(apply(60),60),'null');
assert.equal(ratio(apply(61),61),'null');

calculate(100,60,other);
calculate(110,90);
assert.equal(ratio(apply(90),90),'{"mitigation_before":110,"mitigation_after":90}');
assert.equal(ratio(apply(60,other),60),'{"mitigation_before":100,"mitigation_after":60}');

calculate(100,60,target,[0,0]);
assert.equal(ratio(apply(60),60),'null');

// CE-5 SilverAsh: the formula returned 306, then the same Modifier&
// settled at 397.79998540878296 after adjustment.
const adjusted=397.79998540878296;
calculate(1306,306);
const hit=apply(306);
hit.mod.fields.after=adjusted;
assert.equal(ratio(hit,adjusted),JSON.stringify({mitigation_before:1306,mitigation_after:adjusted}));
for(const changed of [{cancel:1},{type:2},{to:other},{from:pointer('0x102','char_two')}]){
  calculate(1306,306);
  const invalid=apply(306);
  Object.assign(invalid.mod.fields,{after:adjusted},changed);
  assert.equal(ratio(invalid,adjusted,invalid.mod.fields.type),'null');
}

calculate(100,60);
frame++;
gameTime+=1/30;
assert.equal(ratio(apply(60),60),'null');
assert.equal(formulaScopes.size,0);
assert.equal(activeHooks.size,0);
assert.equal(epoch,13); // the outer formula alone accounts for callback barriers

const diagnostics=mitigationDiagnostics();
assert.equal(diagnostics.formula_scopes,12);
assert.equal(diagnostics.formula_candidates,12);
assert.equal(diagnostics.formula_missing_pre,0);
assert.equal(diagnostics.apply_missing_candidate,9);
assert.equal(diagnostics.apply_ambiguous_candidate,1);
assert.equal(diagnostics.apply_value_mismatch,1);
assert.equal(diagnostics.apply_changed_at_leave,4);
assert.equal(diagnostics.emitted,4);
assert.equal(diagnostics.emitted_after_adjustment,1);
assert.equal(diagnostics.samples.length,3);
const mismatch=diagnostics.samples[0];
assert.equal(mismatch.source,'char_one');
assert.equal(mismatch.formula_before,200);
assert.equal(mismatch.formula_after,80);
assert.equal(mismatch.modifier_amount,79);
const adjustment=diagnostics.samples[1];
assert.equal(adjustment.reason,'emitted_after_adjustment');
assert.equal(adjustment.formula_before,1306);
assert.equal(adjustment.formula_after,306);
assert.equal(adjustment.modifier_amount,adjusted);

// An eligible calculation missing the native GetFinalAtk return records the
// diagnostic but neither fabricates a candidate nor consumes any damage event.
const finishWithoutPre=enter('outer',[target,source,flag(1),flag(1),attack]);
finishWithoutPre(raw(58));
assert.equal(mitigationDiagnostics().formula_missing_pre,1);
assert.equal(mitigationDiagnostics().samples[3].formula_after,58);
assert.equal(ratio(apply(58),58),'null');
for(const reason of ['formula_missing_pre','apply_value_mismatch','apply_changed_at_leave','emitted_after_adjustment']){
  for(let index=0;index<10;index++)
    noteMitigationDiagnostic(reason,'char_'+index,time(),{after:58},null);
  assert.equal(mitigationDiagnostics().samples.filter(sample=>sample.reason===reason).length,
               MITIGATION_SAMPLES_PER_REASON);
}
assert.equal(mitigationDiagnostics().samples.length,8);
assert.equal(diagnostics.samples.length,3);

calculate(100,60);
const outer=apply(60);
calculate(110,90,other);
const inner=apply(90,other);
assert.equal(ratio(inner,90),'{"mitigation_before":110,"mitigation_after":90}');
assert.equal(ratio(outer,60),'{"mitigation_before":100,"mitigation_after":60}');

calculate(1306,306);
const shared=apply(306);
calculate(1400,306);
const nested=openModifier(shared.mod);
shared.mod.fields.after=adjusted;
assert.equal(ratio(nested,adjusted),'null');
assert.equal(ratio(shared,adjusted),'null');
assert.equal(activeModifiers.size,0);

for(let index=0;index<=FORMULA_PENDING_LIMIT;index++)
  calculate(100,60,pointer(String(index),'enemy_limit'));
assert.equal(pendingFormulaDamage.get(7).hits.size,FORMULA_PENDING_LIMIT);
assert.equal(ratio(apply(60,pointer(String(FORMULA_PENDING_LIMIT),'enemy_limit')),60),'null');
assert.equal(ratio(apply(60,pointer('0','enemy_limit')),60),'{"mitigation_before":100,"mitigation_after":60}');

const nullEntity={isNull:()=>true};
const gladiia=pointer('0x501','char_474_glady');
const foreign=pointer('0x502','char_two');
const distanceNode={add(offset){assert.equal(offset,0x20);return {readS32:()=>2};}};
function beginDistance(to=target,from=gladiia,sourceType=1){
  const buff={isNull:()=>false,add(offset){assert.equal(offset,0x18);return {readPointer:()=>from};}};
  const snapshot={isNull:()=>false,add(offset){return {readPointer:()=>({0x8:to,0x10:buff})[offset]};}};
  return enter('distance',[distanceNode,null,flag(sourceType),snapshot]);
}
const units=bits=>Number(bits)/4294967296;
function calculateDistance(before,after,to=target,thread=7,flags=[1,0],type=2){
  calculate(before,after,to,flags,nullEntity,thread,type);
}
function applyDistance(before,after,to=target,thread=7,from=gladiia,originValue=after){
  const mod=modifier(after,2,0,to,from);
  Object.assign(mod.fields,{originAtk:before,originValue});
  return openModifier(mod,thread);
}

// CE-5, source-null formula inside glady_e_rupture's DamageByDistance.Execute.
const distancePre=units(1707964825600n),distancePost=units(853982393712n);
let endDistance=beginDistance();
calculateDistance(distancePre,distancePost);
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),
  JSON.stringify({mitigation_before:distancePre,mitigation_after:distancePost}));
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),'null');
endDistance();

// This real hit's modifier received a further +30% before HP settlement.
const secondPre=units(525398835200n),secondPost=units(262699411728n),secondFinal=units(341509222719n);
endDistance=beginDistance();
calculateDistance(secondPre,secondPost);
const adjustedDistance=applyDistance(secondPre,secondPost);
adjustedDistance.mod.fields.after=secondFinal;
assert.equal(ratio(adjustedDistance,secondFinal,2),
  JSON.stringify({mitigation_before:secondPre,mitigation_after:secondFinal}));
endDistance();

calculateDistance(distancePre,distancePost);
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),'null');
endDistance=beginDistance();
calculateDistance(distancePre,distancePost);
endDistance();
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),'null');

endDistance=beginDistance();
calculateDistance(distancePre,distancePost,other);
calculateDistance(distancePre,distancePost,target,8);
calculateDistance(distancePre,distancePost,target,7,[1,1]);
calculateDistance(distancePre,distancePost,target,7,[1,0],1);
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),'null');
calculateDistance(distancePre,distancePost);
assert.equal(ratio(applyDistance(distancePre,distancePost,target,7,foreign),distancePost,2),'null');
assert.equal(ratio(applyDistance(distancePre,distancePost,other),distancePost,2),'null');
assert.equal(ratio(applyDistance(distancePre,distancePost,target,8),distancePost,2),'null');
assert.equal(ratio(applyDistance(distancePre,distancePost,target,7,gladiia,0),distancePost,2),'null');
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),'null');
calculateDistance(distancePre,distancePost);
assert.equal(ratio(applyDistance(distancePre+1,distancePost),distancePost,2),'null');
calculateDistance(distancePre,distancePost);
assert.equal(ratio(applyDistance(distancePre,distancePost+1),distancePost+1,2),'null');
calculateDistance(distancePre,distancePost);
calculateDistance(distancePre,distancePost);
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),'null');
calculateDistance(distancePre,distancePost);
frame++;
gameTime+=1/30;
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),'null');
endDistance();

endDistance=beginDistance();
const endNestedDistance=beginDistance(target,gladiia,0);
calculateDistance(distancePre,distancePost);
endNestedDistance();
assert.equal(ratio(applyDistance(distancePre,distancePost),distancePost,2),'null');
endDistance();
assert.equal(distanceDamageScopes.size,0);
assert.equal(activeHooks.size,0);
assert.equal(mitigationDiagnostics().distance_emitted,2);
`, {assert}, {filename: 'product-mitigation-delivery'});

console.log('PASS_EVENT_DELIVERY');
