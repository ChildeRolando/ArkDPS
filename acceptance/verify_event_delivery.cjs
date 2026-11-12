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
${between('function recordEnemyDamage(', 'function includeEnemyHp(')}
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
recordEnemyDamage({source:'char_a',real:-17,mod:mod(2),target:null},{frame:1});
assert.equal(enemyDamageUnknown,true);
assert.equal(nonlethalSettlement(target,30,-17,17).hp_after,13);
assert.equal(nonlethalSettlement(target,17,-17,17),null);
`, {assert}, {filename: 'product-enemy-settlement'});

console.log('PASS_EVENT_DELIVERY');
