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

for (const traceHpContext of [false, true]) {
  vm.runInNewContext(`
    ${between('function shouldObserveCallback(', 'for(const m of cfg.methods.filter(')}
    for (const [owner, name] of [
      ['Entity', 'ApplyModifier'], ['Entity', 'OnAfterOutputDamage'],
      ['Unit', 'OnTakeDamage'], ['Buff', 'OnTakeDamage'], ['BlockDamage', '_AssignDynamicVar'],
      ['Character', 'OnSkillFinish'], ['BattleController', 'OnCharacterFinished']
    ]) assert.equal(shouldObserveCallback({owner, name}), true);
    for (const [owner, name] of [['Entity', 'OnTakeDamage'], ['Unit', 'OnAfterOutputDamage']])
      assert.equal(shouldObserveCallback({owner, name}), traceHpContext);
  `, {assert, traceHpContext});
}

vm.runInNewContext(`
let sequence=0,epoch=0,active=0,fatal=null,stopped=false;
const coalesceEnemyHp=false;
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
function currentTerminalBoundary(){return null;}
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
let sequence=0,fatal=null,stopped=false;
const clock={frame:30,game_time:1};
const coalesceEnemyHp=false;
let sourceCharacterClasses=null;
const sourceId='char_002_amiya'; // native alias for a roster CharacterData UID
const charClass={equals:value=>value===charClass};
const dataClass={equals:value=>value===dataClass};
const otherClass={equals:()=>false};
const pointer=(klass,fields={})=>({isNull:()=>false,readPointer:()=>klass,
 add(offset){assert(Object.hasOwn(fields,offset));return fields[offset];}});
const nullPtr={isNull:()=>true};
const actor=(uid,klass=charClass,identityClass=dataClass)=>pointer(klass,{
  0x538:{readPointer:()=>pointer(identityClass,{0x6c:{readU32:()=>uid}})}
});
const eventQuality=()=>({});
${between('const EVENT_BUFFER_LIMIT', 'const stacks=')}
${between('function sourceCharacterUid(', 'function time(')}
${between('function own(', 'function refreshEnemyEntryFrame')}

assert.equal(sourceCharacterUid(actor(1049037),sourceId),undefined);
sourceCharacterClasses={character:charClass,data:dataClass};
assert.equal(sourceCharacterUid(actor(1049037),sourceId),1049037);
assert.equal(sourceCharacterUid(actor(476),sourceId),476); // same CID, different UID
assert.equal(sourceCharacterUid(actor(4294967295),'token_a'),4294967295);
assert.equal(sourceCharacterUid(actor(0),sourceId),undefined);
assert.equal(sourceCharacterUid(actor(476,otherClass),sourceId),undefined);
assert.equal(sourceCharacterUid(actor(476,charClass,otherClass),sourceId),undefined);
assert.equal(sourceCharacterUid(actor(476),'trap_a'),undefined);
assert.equal(sourceCharacterUid(pointer(charClass,{0x538:{readPointer:()=>nullPtr}}),sourceId),undefined);
assert.equal(sourceCharacterUid(nullPtr,null),undefined);
const details={hp_before:88};
for (const kind of ['damage','healing','enemy_damage','hp_damage','elemental_bar',
                    'elemental_burst','shield_absorbed','skill_start','skill_end']){
 const event=record(sourceId,kind,1,clock,{},details,1049037);
 assert.equal(event.source_character_uid,1049037);
 assert.equal(event.hp_before,88);
}
assert.equal(Object.hasOwn(details,'source_character_uid'),false);
assert.equal(Object.hasOwn(record(sourceId,'damage',1,clock,{},{}),'source_character_uid'),false);
assert.equal(Object.hasOwn(record(null,'elemental_burst',1,clock,{},{},476),'source_character_uid'),false);
const batch=readEvents(0,sequence);
assert.deepEqual(readEvents(0,sequence),batch);
assert.equal(batch.events.filter(event=>event.source_character_uid).length,9);
`, {assert}, {filename: 'product-character-uid-evidence'});

vm.runInNewContext(`
let sequence=12,epoch=24,active=0,fatal=null,stopped=false;
const coalesceEnemyHp=true;
const enemyMaxHpReady=true;
let clock={frame:60,game_time:2};
const time=()=>{if(clock instanceof Error)throw clock;return clock;};
const eventQuality=()=>({});
const activeHooks=new Map(),hooks=[];
const installed={};
const Interceptor={attach:(method,handlers)=>installed[method]=handlers};
const cfg={};
const ptr=(address,fields={})=>({
  isNull:()=>address==='0x0',equals:other=>other.toString()===address,
  toString:()=>address,readPointer:()=>fields[0],
  add:offset=>({readPointer:()=>fields[offset]})
});
const className=klass=>klass;
const controllerClass=ptr('0x100');
const terminalMethods={state:'state',destroy:'destroy',controller:controllerClass};
${between('const EVENT_BUFFER_LIMIT', 'const stacks=')}
${between('function record(source', 'function refreshEnemyEntryFrame')}
${between('let terminalBoundary=', 'const enemyStateError=')}
${between('function markEvents()', 'function receiveEventRequest()')}

const controllerFields={0:controllerClass};
const controller=ptr('0x200',controllerFields);
const native=ptr('0x300',{0x28:controller});
const loggerFields={0:'BattleLogger',0x18:controller,0x30:ptr('0x500',{0:'BattleStats'})};
const logger=ptr('0x400',loggerFields);
Object.assign(controllerFields,{0x10:native,0xd8:logger,0x158:ptr('0x600',{0:'LevelData'})});
const expected={controller:'0x200',native:'0x300',logger:'0x400',stats:'0x500',level:'0x600',
  frame:60,game_time:2,sequence:12,epoch:24,active:0};
const destroy=installed.destroy;
function enter(){
  const call={};
  destroy.onEnter.call(call,[controller]);
  return ()=>destroy.onLeave.call(call);
}
const restart=()=>installed.state.onEnter([controller,{toInt32:()=>1}]);

const leave=enter();
assert.equal(markEvents().terminal_boundary,null);
leave();
assert.deepEqual(markEvents().terminal_boundary,expected);
clock=new Error('clock unavailable after destruction');
assert.equal(markEvents().retry,true);
assert.deepEqual(markEvents().terminal_boundary,expected);
clock={frame:60,game_time:2};
restart();
assert.equal(markEvents().terminal_boundary,null);

let finish=enter();
epoch++;
finish();
assert.equal(markEvents().terminal_boundary,null);
finish=enter();
sequence++;
finish();
assert.equal(markEvents().terminal_boundary,null);
finish=enter();
clock={frame:61,game_time:2.03};
finish();
assert.equal(markEvents().terminal_boundary,null);
finish=enter();
restart();
finish();
assert.equal(markEvents().terminal_boundary,null);
active=1;
finish=enter();
active=0;
finish();
assert.equal(markEvents().terminal_boundary,null);
enter()();
assert.notEqual(markEvents().terminal_boundary,null);
epoch++;
assert.equal(markEvents().terminal_boundary,null);

loggerFields[0x18]=ptr('0x999');
enter()();
assert.equal(markEvents().terminal_boundary,null);

// Finish sequencing a pending heal before capturing destruction's watermark.
loggerFields[0x18]=controller;
record(null,'enemy_enter',0,{frame:57,game_time:1.9},{},{target:'enemy_a',target_instance:'7'});
for(let hp=10;hp<13;hp++){
 const evidence={target:'enemy_a',target_instance:'7'};
 recordEnemyHp({enemyTarget:true,key:'0x7',hpBefore:hp,maxHpBefore:null,evidence},
               clock,'Entity.SetHpInternal',hp+1,100);
}
const prior=sequence;
enter()();
const end=markEvents();
assert.equal(end.sequence,prior+1);
assert.equal(end.terminal_boundary.sequence,end.sequence);
assert.equal(end.terminal_boundary.epoch,epoch);
assert.equal(queue.at(-1).hp_before,11);
assert.equal(queue.at(-1).hp_after,13);
restart();
assert.equal(enemyHpBirths.size,0);
`, {assert}, {filename: 'product-terminal-boundary'});

for (const coalesceEnemyHp of [false, true]) {
  vm.runInNewContext(`
    let sequence=0,epoch=0,active=0,fatal=null,stopped=false;
    let clock={frame:11,game_time:1.1};
    const time=()=>clock;
    const activeHooks=new Map(),enemyHpWrites=new Map(),awaitingEnemyTile=new Map();
    const enemyHealthPhases=new Map(),pendingEnemyRebirth=new Map();
    let enemyEntryFrame=null,enemyEntryUnknown=false,enemyPhaseUnknown=false;
    let enemyStateUnknown=false,bossDamageUnknown=false;
    const enemyMaxHpReady=true;
    const eventQuality=frame=>{if(frame!==undefined)refreshEnemyEntryFrame(frame);return {};};
    const currentTerminalBoundary=()=>null;
    ${between('const EVENT_BUFFER_LIMIT', 'const stacks=')}
    ${between('function record(source', 'function isEnemy(')}
    ${between('function beginEnemyHook(', 'function enemyIdentity(')}
    ${between('function markEvents()', 'function receiveEventRequest()')}
    const main={key:'0x7',target:'enemy_a',target_instance:'7'};
    function lifecycle(kind,identity=main){
      record(null,kind,0,clock,{}, {target:identity.target,target_instance:identity.target_instance});
    }
    function write(before,after,{identity=main,max=100,method='Entity.SetHpInternal',previousMax=null}={}){
      beginEnemyHook(method);
      recordEnemyHp({key:identity.key,enemyTarget:true,hpBefore:before,maxHpBefore:previousMax,
        evidence:{target:identity.target,target_instance:identity.target_instance}},clock,method,after,max);
      endEnemyHook(method);
    }
    function ranges(batch){return batch.events.filter(event=>event.kind==='enemy_hp')
      .map(event=>[event.hp_before,event.hp_after].join('>')).join(',');}
    function prefix(after=0){const mark=markEvents();return readEvents(after,mark.sequence);}
    lifecycle('enemy_enter');
    clock={frame:12,game_time:1.2};
    write(1,2);write(2,3);write(3,4);
    assert.equal(ranges(prefix()),'1>2,2>3,3>4'); // spawn clamp window
    const baseline=sequence;
    assert.equal(ackEvents(baseline).acknowledged,baseline);
    clock={frame:13,game_time:1.3};
    write(4,5);write(5,6);write(6,7);
    active=1;
    const busy=markEvents();
    assert.equal(busy.active,1);
    assert.equal(busy.sequence,baseline+(coalesceEnemyHp?1:3));
    active=0;
    const marked=markEvents();
    assert.equal(marked.epoch,6); // every raw write retains its epoch edge
    const published=readEvents(baseline,marked.sequence);
    assert.equal(ranges(published),coalesceEnemyHp?'4>5,5>7':'4>5,5>6,6>7');
    write(7,8);write(8,9);write(9,10); // same frame, next watermark
    assert.deepEqual(readEvents(baseline,marked.sequence),published);
    assert.equal(ackEvents(marked.sequence).acknowledged,marked.sequence);
    assert.equal(ackEvents(marked.sequence).acknowledged,marked.sequence);
    assert.equal(ranges(prefix(marked.sequence)),coalesceEnemyHp?'7>8,8>10':'7>8,8>9,9>10');
    let previous=sequence;
    write(10,11);write(11,12);write(12,13);
    record('char_a','damage',1,clock,{},{});
    write(13,14);write(14,15);write(15,16);
    let batch=prefix(previous);
    assert.equal(batch.events.map(event=>event.kind).join(','),coalesceEnemyHp
      ?'enemy_hp,enemy_hp,damage,enemy_hp,enemy_hp':'enemy_hp,enemy_hp,enemy_hp,damage,enemy_hp,enemy_hp,enemy_hp');

    // Different identity, time, MaxHP, callback, discontinuity and direction
    // each prevent extending the currently buffered run.
    previous=sequence;
    write(16,17);write(17,18);write(18,19,{max:101,previousMax:100});
    write(19,20,{max:101});write(20,21,{max:101,method:'Entity.SetHpInternalDirectly'});
    write(21,22,{max:101});write(23,24,{max:101});write(24,23,{max:101});
    const reused={...main,key:'0x99'};
    write(23,24,{identity:reused,max:101});write(24,25,{identity:reused,max:101});
    clock={frame:13,game_time:1.31};
    write(25,26,{identity:reused,max:101});write(26,27,{max:101});
    batch=prefix(previous);
    assert.equal(ranges(batch),'16>17,17>18,18>19,19>20,20>21,21>22,23>24,24>23,23>24,24>25,25>26,26>27');

    previous=sequence;
    lifecycle('enemy_fake_death');
    write(27,28);write(28,29);write(29,30); // no active phase
    lifecycle('enemy_reborn');
    clock={frame:14,game_time:1.4};
    write(30,31);write(31,32);write(32,33); // new phase guard
    clock={frame:15,game_time:1.5};
    write(33,34);write(34,35);write(35,36);
    refreshEnemyEntryFrame(clock.frame);
    clock={frame:13,game_time:1.3};
    refreshEnemyEntryFrame(clock.frame);
    write(36,37);write(37,38);write(38,39); // old birth invalid after reset
    batch=prefix(previous);
    assert.equal(ranges(batch),coalesceEnemyHp?'27>28,28>29,29>30,30>31,31>32,32>33,33>34,34>36,36>37,37>38,38>39'
      :'27>28,28>29,29>30,30>31,31>32,32>33,33>34,34>35,35>36,36>37,37>38,38>39');
    assert.equal(active,0);
  `, {assert, coalesceEnemyHp}, {filename: 'product-enemy-hp-coalescing'});
}

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
let active=0,epoch=0,frame=30,gameTime=1,clockReads=0;
const mitigationMethods={outer:'outer',noEvent:'no_event',internal:'internal',final:'final',
  factories:['factory7','factory8'],damageType:0};
const il={name:'libil2cpp.so',base:0};
const Process={findModuleByAddress:address=>address.module};
const Interceptor={attach(address,hook){
  registered.set(address,hook);
  return {detach(){registered.delete(address);}};
}};
const time=()=>{clockReads++;return {frame,game_time:gameTime};};
const targetKey=pointer=>pointer.id;
const entityId=pointer=>pointer.id;
const own=id=>id.startsWith('char_')||id.startsWith('token_');
const recorded=[];
const record=(source,kind,amount,when,proof,evidence)=>recorded.push({source,kind,amount,when,evidence});
const fp=pointer=>Number(pointer.readS64())/4294967296;
${between('const activeModifiers=new Map();', 'function markShieldUnknown(')}
${between('// One optional observation joins the existing ApplyModifier damage event.', 'function includeEnemyHp(')}
installMitigationHooks();
assert.equal(mitigationReady,true);
assert.equal(mitigationNoEventReady,true);
assert.equal(mitigationFactoryReady,true);
assert.equal(hooks.length,6);

const raw=value=>BigInt(Math.round(value*4294967296));
function pointer(address,id='',type=1,uid=1){
  return {
    id,uid,equals(other){return address===other.toString();},
    isNull(){return address==='0x0';},toString(){return address;},
    add(offset){
      if(offset===0x18)return {readU32:()=>this.uid};
      assert.equal(offset,0);
      return this;
    },readS32(){return type;}
  };
}
const source=pointer('0x101','char_one'),target=pointer('0x202','enemy_one');
const other=pointer('0x203','enemy_two'),attack=pointer('0x303'),copy=pointer('0x404');
const flag=value=>({toInt32:()=>value});
function enter(address,args,threadId=7,x8=null){
  const hook=registered.get(address);
  const context={threadId,context:{x8}};
  hook.onEnter.call(context,args);
  return result=>hook.onLeave.call(context,result);
}
function calculate(before,after,to=target,actionFlags=[1,1],from=source,thread=7,type=1,route='outer'){
  const initiallyActive=active;
  const info=type===1?attack:pointer('0x303','',type), internalCopy=type===1?copy:pointer('0x404','',type);
  const finish=route==='no_event'?enter('no_event',[to,from,info],thread):
    enter('outer',[to,from,...actionFlags.map(flag),info],thread);
  assert.equal(active,initiallyActive+1);
  const finishNested=route==='nested'?enter('no_event',[to,from,info],thread):null;
  const finishInner=enter('internal',[internalCopy,raw(0),raw(0),to],thread);
  const finishPre=enter('final',[internalCopy],thread);
  finishPre(raw(before));
  finishInner(raw(after));
  if(finishNested)finishNested(raw(after));
  finish(raw(after));
  assert.equal(active,initiallyActive);
}
function modifier(after,type=1,cancel=0,to=target,from=source){
  const fields={after,type,cancel,to,from};
  return {
    fields,
    equals(other){return this===other;},
    isNull(){return false;},
    readPointer:()=>fields.from,
    add:offset=>({
      readS32:()=>({0x60:0,0x68:fields.type,0x70:fields.cancel})[offset],
      readS64:()=>raw(({0x30:fields.after,0x48:fields.realDelta,0x80:fields.originAtk,0x88:fields.originValue})[offset]),
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

const nullEntity=pointer('0x0');
const gladiia=pointer('0x501','char_474_glady',1,1833);
const foreign=pointer('0x502','enemy_two');
const trap=pointer('0x503','trap_321_shnbox',1,2332);
const wisadel=pointer('0x504','char_1035_wisdel');
const angel=pointer('0x505','char_1041_angel2');
const token=pointer('0x506','token_10069_mcnist_mcgraf');
const units=bits=>Number(bits)/4294967296;
function nextFrame(){frame++;gameTime+=1/30;}
function fixtureModifier(before,after,{to=target,from=gladiia,type=2,originValue=after}={}){
  const mod=modifier(after,type,0,to,from);
  Object.assign(mod.fields,{originAtk:before,originValue});
  return mod;
}
function factory(before,after,{to=target,from=gladiia,type=2,thread=7,rawValue,returned}={}){
  const eight=rawValue!==undefined;
  const args=[raw(after)];
  if(eight)args.push(raw(rawValue));
  args.push(flag(type),flag(0),from,to,flag(4),raw(before));
  const storage=returned||fixtureModifier(before,after,{to,from,type,originValue:rawValue??after});
  enter(eight?'factory8':'factory7',args,thread,storage)(null);
}
const sample=(before,after)=>JSON.stringify({mitigation_before:before,mitigation_after:after});

// CE-5, nested source-null formula and the measured seven-argument factory.
const distancePre=units(1707964825600n),distancePost=units(853982393712n);
nextFrame();
calculate(distancePre,distancePost,target,[1,0],nullEntity,7,2,'nested');
factory(distancePre,distancePost);
assert.equal(ratio(openModifier(fixtureModifier(distancePre,distancePost)),distancePost,2),sample(distancePre,distancePost));
assert.equal(ratio(openModifier(fixtureModifier(distancePre,distancePost)),distancePost,2),'null');

// This real hit's modifier received a further +30% before HP settlement.
const secondPre=units(525398835200n),secondPost=units(262699411728n),secondFinal=units(341509222719n);
calculate(secondPre,secondPost,target,[1,0],nullEntity,7,2,'no_event');
factory(secondPre,secondPost);
const adjustedDistance=openModifier(fixtureModifier(secondPre,secondPost));
Object.assign(adjustedDistance.mod.fields,{after:secondFinal,realDelta:-10});
assert.equal(ratio(adjustedDistance,secondFinal,2),sample(secondPre,secondFinal));

// The eight-argument factory's rawValue is a distinct reference calculation,
// not the formula output that becomes m_value.
const tokenPre=units(1228360646656n),tokenPost=units(982688495360n);
const referencePre=units(2456721293312n),referencePost=units(1965376990720n);
calculate(tokenPre,tokenPost,target,[1,0],nullEntity,7,1,'no_event');
calculate(referencePre,referencePost,target,[1,0],nullEntity,7,1,'nested');
factory(tokenPre,tokenPost,{from:token,type:1,rawValue:referencePost});
assert.equal(ratio(openModifier(fixtureModifier(tokenPre,tokenPost,{from:token,type:1,originValue:referencePost})),tokenPost),
  sample(tokenPre,tokenPost));
factory(referencePre,referencePost,{from:token,type:1});
assert.equal(ratio(openModifier(fixtureModifier(referencePre,referencePost,{from:token,type:1})),referencePost),'null');

// A known source still wins when the reference calculation exactly matches it.
calculate(100,60,trap,[1,1],wisadel);
calculate(100,60,trap,[1,0],nullEntity);
factory(100,60,{to:trap,from:wisadel,type:1,rawValue:60});
assert.equal(ratio(apply(60,trap,7,1,wisadel),60),sample(100,60));
factory(100,60,{to:trap,from:wisadel,type:1});
assert.equal(ratio(openModifier(fixtureModifier(100,60,{to:trap,from:wisadel,type:1})),60),'null');
for(let index=0;index<2;index++){
  calculate(100,60,trap,[1,1],wisadel,7,1,'no_event');
  assert.equal(ratio(apply(60,trap,7,1,wisadel),60),sample(100,60));
}
for(const friendly of [angel,token]){
  calculate(100,60,friendly,[1,1],angel);
  calculate(100,60,friendly,[1,0],nullEntity);
  factory(100,60,{to:friendly,from:angel,type:1});
  assert.equal(ratio(apply(60,friendly,7,1,angel),60),'null');
}

function calculateUnowned(){calculate(distancePre,distancePost,target,[1,0],nullEntity,7,2);}
function noPair(mod=fixtureModifier(distancePre,distancePost),thread=7){
  assert.equal(ratio(openModifier(mod,thread),mod.fields.after,mod.fields.type),'null');
}
nextFrame();
calculateUnowned();
noPair(); // a formula alone must not guess the source at Apply
factory(distancePre,distancePost,{from:foreign});
factory(distancePre,distancePost);
noPair();
calculateUnowned();
calculateUnowned();
factory(distancePre,distancePost);
noPair();
assert.equal(mitigationDiagnostics().factory_ambiguous,1);
calculateUnowned();
factory(distancePre,distancePost,{returned:fixtureModifier(distancePre+1,distancePost)});
noPair();
calculateUnowned();
factory(distancePre,distancePost,{to:other});
factory(distancePre,distancePost,{thread:8});
factory(distancePre,distancePost,{type:1});
noPair();
factory(distancePre,distancePost);
noPair(fixtureModifier(distancePre,distancePost,{from:wisadel}));
noPair(fixtureModifier(distancePre,distancePost,{to:other}));
noPair(fixtureModifier(distancePre,distancePost),8);
noPair(fixtureModifier(distancePre,distancePost,{originValue:0}));
noPair();
calculateUnowned();
factory(distancePre,distancePost);
noPair(fixtureModifier(distancePre,distancePost+1));
calculateUnowned();
factory(distancePre,distancePost);
const canceled=fixtureModifier(distancePre,distancePost);
canceled.fields.cancel=1;
noPair(canceled);
calculateUnowned();
factory(distancePre,distancePost);
target.uid++;
noPair();
calculateUnowned();
factory(distancePre,distancePost);
nextFrame();
noPair();

// A direct modifier with plausible origin values is still missing a formula.
noPair(fixtureModifier(80,40,{type:3}));
noPair(fixtureModifier(80,40,{type:5}));
assert.equal(activeDamageFactories.size,0);
assert.equal(formulaScopes.size,0);
assert.equal(activeHooks.size,0);
assert.equal(mitigationDiagnostics().factory_emitted,3);

const caller={module:il,sub:()=>'0x317152c'};
function noteUnmatched(to,from,at,amount,pre){
  const scope={target:to,mod:modifier(amount,1,0,to,from),returnAddress:caller};
  noteUnpairedDamage(scope,{source:from.id,frame:at,amount,mitigation_before:pre},1);
}
noteUnmatched(trap,wisadel,100,0);
noteUnmatched(angel,angel,100,10);
noteUnmatched(token,angel,100,10);
noteUnmatched(trap,wisadel,100,10,20);
assert.equal(mitigationDiagnostics().unpaired_damage,0);
noteUnmatched(trap,wisadel,100,10);
noteUnmatched(trap,wisadel,101,11);
let missing=mitigationDiagnostics();
assert.equal(missing.unpaired_damage,2);
assert.equal(missing.unpaired_damage_samples.length,1);
assert.equal(missing.unpaired_damage_samples[0].source,wisadel.id);
assert.equal(missing.unpaired_damage_samples[0].target,trap.id);
assert.equal(missing.unpaired_damage_samples[0].damage_type,1);
assert.equal(missing.unpaired_damage_samples[0].frame,101);
assert.equal(missing.unpaired_damage_samples[0].amount,11);
assert.equal(missing.unpaired_damage_samples[0].caller_rva,'0x317152c');
for(let index=0;index<UNPAIRED_DAMAGE_SAMPLE_LIMIT;index++)
  noteUnmatched(pointer('trap-'+index,'trap_'+index),wisadel,102+index,20);
missing=mitigationDiagnostics();
assert.equal(missing.unpaired_damage,UNPAIRED_DAMAGE_SAMPLE_LIMIT+2);
assert.equal(missing.unpaired_damage_samples.length,UNPAIRED_DAMAGE_SAMPLE_LIMIT);
assert.equal(missing.unpaired_damage_samples[0].target,'trap_0');

const delayedBefore=140,delayedAfter=70;
const delayedAttack=pointer('0x701','',2),referenceAttack=pointer('0x702','',2);
function finishWithoutInput({from=gladiia,to=target,info=delayedAttack,standalone=false,
                             after=delayedAfter}={}){
  if(standalone)enter('no_event',[to,from,info])(raw(after));
  else enter('outer',[to,from,flag(1),flag(1),info])(raw(after));
}
function laterInput(info=delayedAttack,before=delayedBefore,thread=7){
  enter('final',[info],thread)(raw(before));
}
function delayedFactory(overrides={}){
  factory(delayedBefore,delayedAfter,overrides);
}
function delayedHit(overrides={}){
  return openModifier(fixtureModifier(delayedBefore,delayedAfter,overrides));
}

// Owned outer formula. An eight-argument factory carries the distinct
// source-less reference calculation between the main formula and its getter.
nextFrame();
finishWithoutInput();
calculate(60,30,target,[1,0],nullEntity,7,1,'nested');
laterInput();
delayedFactory({rawValue:30});
assert.equal(ratio(delayedHit({originValue:30}),delayedAfter,2),sample(delayedBefore,delayedAfter));
assert.equal(ratio(delayedHit({originValue:30}),delayedAfter,2),'null');

// Neither a delayed formula alone nor an unowned formula can provide the
// positive source attribution needed for this fallback.
nextFrame();
finishWithoutInput({standalone:true});
laterInput();
noPair(fixtureModifier(delayedBefore,delayedAfter));
nextFrame();
finishWithoutInput({from:nullEntity,standalone:true});
laterInput();
delayedFactory();
assert.equal(ratio(delayedHit(),delayedAfter,2),'null');

// Re-entrant formula callbacks can inspect alternate AttackInfo values.
// The exact caller input and factory replace the ambiguous in-scope pair.
nextFrame();
const initialInfo=pointer('0x701','',2);
const finishConflicted=enter('outer',[target,gladiia,flag(1),flag(1),initialInfo]);
for(const [type,value] of [[2,delayedBefore+1],[1,delayedBefore]]){
  const local=pointer('0x703','',type);
  const finishNested=enter('no_event',[target,gladiia,local]);
  const finishInternal=enter('internal',[local,raw(0),raw(0),target]);
  laterInput(local,value);
  finishInternal(raw(delayedAfter));
  finishNested(raw(delayedAfter));
}
initialInfo.readS32=()=>1;
finishConflicted(raw(delayedAfter));
laterInput(initialInfo);
delayedFactory({type:1});
assert.equal(ratio(delayedHit({type:1}),delayedAfter),sample(delayedBefore,delayedAfter));

nextFrame();
finishWithoutInput({standalone:true});
laterInput();
delayedFactory();
assert.equal(ratio(delayedHit(),delayedAfter,2),sample(delayedBefore,delayedAfter));

for(const [name,withCandidate] of [
  ['wrong AttackInfo pointer',()=>laterInput(referenceAttack)],
  ['changed AttackInfo type',()=>laterInput(pointer('0x701','',1))],
  ['different getter value',()=>laterInput(delayedAttack,delayedBefore+1)],
  ['non-positive getter',()=>laterInput(delayedAttack,0)],
  ['different getter thread',()=>laterInput(delayedAttack,delayedBefore,8)],
  ['getter after frame advance',()=>{nextFrame();laterInput();}],
  ['factory after frame advance',()=>{laterInput();nextFrame();}],
  ['duplicate complete pairs',()=>{laterInput();finishWithoutInput();laterInput();}],
]){
  nextFrame();
  finishWithoutInput();
  withCandidate();
  delayedFactory();
  assert.equal(ratio(delayedHit(),delayedAfter,2),'null',name);
}

for(const [name,overrides] of [
  ['wrong factory owner',{from:wisadel}],
  ['wrong factory target',{to:other}],
  ['wrong factory type',{type:1}],
  ['different factory output',{returned:fixtureModifier(delayedBefore,delayedAfter+1)}],
  ['different factory input',{returned:fixtureModifier(delayedBefore+1,delayedAfter)}],
]){
  nextFrame();
  finishWithoutInput();
  laterInput();
  delayedFactory(overrides);
  assert.equal(ratio(delayedHit(overrides),delayedAfter,overrides.type??2),'null',name);
}
nextFrame();
finishWithoutInput({after:delayedAfter+1});
laterInput();
delayedFactory();
noPair(fixtureModifier(delayedBefore,delayedAfter));

nextFrame();
finishWithoutInput();
laterInput();
delayedFactory();
target.uid++;
assert.equal(ratio(delayedHit(),delayedAfter,2),'null','target instance changed before ApplyModifier');
target.uid--;
for(const friendly of [gladiia,angel,token]){
  nextFrame();
  finishWithoutInput({to:friendly});
  laterInput();
  delayedFactory({to:friendly});
  assert.equal(ratio(delayedHit({to:friendly}),delayedAfter,2),'null');
}

// Elemental HP settlement needs the caller's GetFinalAtk after the formula,
// the factory's verified return and the same modifier fields on apply.
const elementalBefore=6000,elementalAfter=units(21474836000000n);
const elementalAttack=pointer('0x801','',5);
function elementalFormula({from=nullEntity,to=target}={}){
  finishWithoutInput({from,to,info:elementalAttack,after:elementalAfter});
}
function elementalGetter(info=elementalAttack,before=elementalBefore){
  laterInput(info,before);
}
function elementalFactory(overrides={}){
  factory(elementalBefore,elementalAfter,{from:nullEntity,type:5,...overrides});
}
function elementalModifier(overrides={}){
  return fixtureModifier(elementalBefore,elementalAfter,{from:nullEntity,type:5,...overrides});
}

nextFrame();
elementalFormula();
elementalGetter();
elementalFactory();
assert.equal(ratio(openModifier(elementalModifier()),elementalAfter,5),sample(elementalBefore,elementalAfter));
noPair(elementalModifier()); // a second settlement cannot reuse the pair

nextFrame();
elementalFormula({from:gladiia});
elementalGetter();
elementalFactory({from:gladiia,rawValue:5800});
const elementalAdjusted=openModifier(elementalModifier({from:gladiia,originValue:5800}));
elementalAdjusted.mod.fields.after=6500;
assert.equal(ratio(elementalAdjusted,6500,5),sample(elementalBefore,6500));

nextFrame();
elementalFormula({from:gladiia});
elementalGetter();
elementalFactory({from:gladiia});
const elementalBar=elementalModifier({from:gladiia,type:0});
const readBar=elementalBar.add;
elementalBar.add=offset=>offset===0x60 ? {readS32:()=>5} : readBar(offset);
noPair(elementalBar); // targetType 5 and type 0 are buildup, not HP settlement
assert.equal(ratio(openModifier(elementalModifier({from:gladiia})),elementalAfter,5),
             sample(elementalBefore,elementalAfter));

// H7-4: the triggering bar is still on the native stack when its
// source-null HP settlement returns. Emit it ahead of the burst, once.
const triggeringBar=openModifier(elementalBar);
triggeringBar.elementalBarTarget={pointer:target,uid:target.uid};
recordTriggeringElementalBar({tid:8,target},time());
recordTriggeringElementalBar({tid:7,target:other},time());
target.uid++;
recordTriggeringElementalBar({tid:7,target},time());
target.uid--;
const ambiguousBar={...triggeringBar,mod:elementalModifier({from:gladiia,type:0})};
enterModifier(ambiguousBar);
recordTriggeringElementalBar({tid:7,target},time());
leaveModifier(ambiguousBar);
assert.equal(recorded.length,0);
recordTriggeringElementalBar({tid:7,target},time());
record(null,'elemental_burst',elementalAfter,time(),{},null);
recordElementalBar(triggeringBar,time(),elementalAfter);
leaveModifier(triggeringBar);
assert.equal(recorded.length,2);
assert.equal(recorded[0].source,gladiia.id);
assert.equal(recorded[0].kind,'elemental_bar');
assert.equal(recorded[0].evidence.target_instance,target.toString());
assert.equal(recorded[1].kind,'elemental_burst');

nextFrame();
calculate(elementalBefore,elementalAfter,target,[1,1],gladiia,7,5);
elementalFactory({from:gladiia});
noPair(elementalModifier({from:gladiia})); // only an in-formula getter was seen

for(const [label,change] of [
  ['wrong post-formula input pointer',()=>elementalGetter(pointer('0x802','',5))],
  ['wrong post-formula input type',()=>elementalGetter(pointer('0x801','',0))],
  ['post-formula input disagrees with factory',()=>elementalGetter(elementalAttack,elementalBefore+1)],
  ['post-formula input in a later frame',()=>{nextFrame();elementalGetter();}],
]){
  nextFrame();
  elementalFormula();
  change();
  elementalFactory();
  assert.equal(ratio(openModifier(elementalModifier()),elementalAfter,5),'null',label);
}

for(const [label,overrides] of [
  ['factory source differs from formula', {from:gladiia}],
  ['factory target differs from formula', {to:other}],
  ['factory type differs from formula', {type:2}],
  ['factory returned a mismatched raw amount', {returned:modifier(elementalAfter+1,5,0,target,nullEntity)}],
  ['factory returned a mismatched origin value', {returned:elementalModifier({originValue:0})}],
]){
  nextFrame();
  elementalFormula();
  elementalGetter();
  elementalFactory(overrides);
  const scope=openModifier(elementalModifier(overrides));
  assert.equal(ratio(scope,elementalAfter,scope.mod.fields.type),'null',label);
}

nextFrame();
elementalFormula();
elementalGetter();
nextFrame();
elementalFactory();
noPair(elementalModifier());

nextFrame();
elementalFormula();
elementalGetter();
elementalFactory();
noPair(elementalModifier({from:foreign}));
noPair(elementalModifier({to:other}));
target.uid++;
noPair(elementalModifier());
target.uid--;

nextFrame();
elementalFormula();
elementalGetter();
elementalFactory();
const canceledElemental=openModifier(elementalModifier());
canceledElemental.mod.fields.cancel=1;
assert.equal(ratio(canceledElemental,elementalAfter,5),'null');
assert.equal(active,0);
assert.equal(activeHooks.size,0);

// Calls with no pending evidence, or a getter using a different AttackInfo,
// do not need to read the native clock. Keep factory epoch bookkeeping and
// consume only a same-frame claim on the matching AttackInfo pointer.
const noCandidateThread=51;
const countBeforeEmptyFactory=clockReads,epochBeforeEmptyFactory=epoch;
const leaveEmptyFactory=enter('factory7',[],noCandidateThread);
assert.equal(active,1);
leaveEmptyFactory(null);
assert.equal(clockReads,countBeforeEmptyFactory);
assert.equal(epoch,epochBeforeEmptyFactory+1);
assert.equal(activeDamageFactories.has(noCandidateThread),false);

nextFrame();
elementalFormula();
const key=elementalAttack.toString();
const batch=pendingFormulaDamage.get(7);
assert.equal(batch.postFormulaAttacks.has(key),true);
const countBeforeUnmatched=clockReads;
elementalGetter(pointer('0x802','',5));
enter('factory7',[],7)(null); // only an unclaimed formula exists
assert.equal(clockReads,countBeforeUnmatched);
assert.equal(batch.postFormulaAttacks.has(key),true);
elementalGetter();
assert.equal(batch.postFormulaAttacks.has(key),false);
elementalFactory();
assert.equal(ratio(openModifier(elementalModifier()),elementalAfter,5),sample(elementalBefore,elementalAfter));

nextFrame();
elementalFormula();
const staleBatch=pendingFormulaDamage.get(7);
nextFrame();
const countBeforeStale=clockReads;
elementalGetter();
assert.equal(clockReads,countBeforeStale+1);
assert.equal(staleBatch.postFormulaAttacks.has(key),true);
assert.equal(staleBatch.postFormulaHits.size,0);

nextFrame();
elementalFormula();
const mismatchedBatch=pendingFormulaDamage.get(7);
elementalGetter(pointer(key,'',0));
assert.equal(mismatchedBatch.postFormulaAttacks.has(key),false);
elementalGetter();
elementalFactory();
noPair(elementalModifier());
`, {assert}, {filename: 'product-mitigation-delivery'});

console.log('PASS_EVENT_DELIVERY');
