'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const c=vm.createContext({});
vm.runInContext(fs.readFileSync('event_probe_store.js','utf8')+'\n'+fs.readFileSync('native_pc/native_event_runtime/publication.js','utf8')+
 '\nglobalThis.Store=ArkdpsEventStore;globalThis.Journal=ArkdpsPublicationJournal;',c);
const make=()=>{const store=new c.Store(),journal=new c.Journal({store:store.view()});return {store,journal};};
const record=t=>({version:1,edge:'leave',ticket:String(t),closed_after:true,active_after:0,activity_begin:'0',activity_end:'1'});
function projection(store,journal){
 const sequence=store.sequence,retained=store.snapshot(journal.publishedThrough,sequence);
 return {mark:{sequence,epoch:1,frame:30,game_time:1,active:0,active_hooks:[],error:null,stopped:false},
  acknowledged:store.acknowledged,events_after:journal.publishedThrough,events_through:sequence,
  events:retained.events,hp_context:retained.hp_context,read_fields:{sequence,epoch:1},terminal_read:null};
}
function seed(store){const original={sequence:1,kind:'damage',proof:{amount:10}};store.append(original);
 original.proof.amount=999;store.append({sequence:2,kind:'heal'});store.appendContext(2,{method:'owned'});}
function read(store,journal,token,served=n=>store.served(n)){
 return journal.read({operation:'read',publication_token:token,after:0,through:2},served);
}
{
 const {store,journal}=make();seed(store);const view=store.snapshot(0,2);
 assert.equal(store.events[0].proof.amount,10);assert(Object.isFrozen(store.events[0].proof));assert(Object.isFrozen(view.events));
 const mark=journal.publish(record(1),projection(store,journal));assert.equal(mark.sequence,2);
 assert.strictEqual(journal.events[0],store.events[0]);assert.strictEqual(journal.contexts[0],store.contexts[0]);
 for(const key of ['events','contexts','acknowledged','readThrough'])assert.equal(Object.hasOwn(journal,key),false);
 assert.equal(store.readThrough,0);assert.equal(store.acknowledged,0);
 assert.throws(()=>read(store,journal,mark.native_publication.token,()=>{throw Error('before served');}));
 assert.equal(store.readThrough,0);assert.equal(store.events.length,2);
 const reply=read(store,journal,mark.native_publication.token);reply.events[0].proof.amount=42;
 assert.equal(store.events[0].proof.amount,10);assert.equal(journal.readThrough,store.readThrough);
 assert.throws(()=>journal.ack(2,()=>{throw Error('before commit');}));
 assert.equal(store.acknowledged,0);assert.equal(journal.acknowledged,0);assert.equal(store.events.length,2);
 assert.equal(journal.ack(2,()=>({acknowledged:2,error:null})).error,'event store acknowledgement was not committed');
 assert.equal(store.events.length,2);
 assert.equal(journal.ack(2,n=>store.ack(n)).acknowledged,2);
 assert.equal(store.events.length,0);assert.equal(store.contexts.length,0);assert.equal(store.retainedBytes,0);
 assert.equal(journal.ack(2,()=>{throw Error('idempotent ACK must not call owner again');}).acknowledged,2);
}
{
 const {store,journal}=make();seed(store);const p=projection(store,journal);
 p.events=JSON.parse(JSON.stringify(p.events));
 assert(journal.publish(record(1),p).native_publication_failure);
 assert.equal(journal.publishedThrough,0);assert.equal(journal.events.length,0);
 assert.equal(store.events.length,2);assert.equal(store.acknowledged,0);assert.equal(store.readThrough,0);
}
{
 const {store,journal}=make();seed(store);const mark=journal.publish(record(1),projection(store,journal));
 read(store,journal,mark.native_publication.token);
 assert.throws(()=>journal.ack(2,n=>{store.ack(n);throw Error('reply lost after commit');}));
 assert.equal(store.acknowledged,2);assert.equal(journal.acknowledged,2);assert.equal(store.events.length,0);
 assert.equal(journal.ack(2,()=>{throw Error('must not replay committed ACK');}).acknowledged,2);
}
{
 const store=new c.Store();store.append({sequence:1});store.append({sequence:2});
 store.appendContext(2,{method:'future'});const bytes=store.retainedBytes;
 assert.throws(()=>store.appendContext(1,{method:'regressed'}),/context sequence regressed/);
 assert.equal(store.contexts.length,1);assert.equal(store.retainedBytes,bytes);
 store.served(2);store.ack(2);assert.equal(store.contexts.length,0);assert.equal(store.retainedBytes,0);
}
{
 const store=new c.Store({maxEvents:1,maxBytes:1024});store.append({sequence:1});
 const bytes=store.retainedBytes;assert.throws(()=>store.append({sequence:2}));
 assert.equal(store.sequence,1);assert.equal(store.retainedBytes,bytes);assert.equal(store.events.length,1);
 assert.equal(store.ack(1).acknowledged,0);store.served(1);assert.equal(store.ack(1).acknowledged,1);
}
console.log('PASS_CANONICAL_EVENT_STORE ownership/immutability/budget/publish/read/ACK failure boundaries');
