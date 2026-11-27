// One normalized event store per probe. Transports retain certificates, not events.
'use strict';
function ownProbeValue(value){
 const text=JSON.stringify(value,(_key,item)=>{
  if(typeof item==='number'&&!Number.isFinite(item))throw Error('non-finite normalized value');
  return item;
 });
 if(typeof text!=='string')throw Error('invalid normalized value');
 const owned=JSON.parse(text);
 const freeze=item=>{if(item&&typeof item==='object'){for(const child of Object.values(item))freeze(child);Object.freeze(item);}return item;};
 return {value:freeze(owned),bytes:text.length*3};
}
class ArkdpsEventStore {
 #acknowledged=0;#readThrough=0;#bytes=0;#costs=new WeakMap();#view;
 constructor(options={}){
  this.maxEvents=options.maxEvents??100000;this.maxBytes=options.maxBytes??64*1024*1024;
  for(const n of [this.maxEvents,this.maxBytes])if(!Number.isSafeInteger(n)||n<=0)throw Error('invalid event store budget');
  this.events=[];this.contexts=[];
 }
 get acknowledged(){return this.#acknowledged;}
 get readThrough(){return this.#readThrough;}
 get sequence(){return this.#acknowledged+this.events.length;}
 get retainedBytes(){return this.#bytes;}
 own(value){
  const {value:owned,bytes}=ownProbeValue(value);
  if(bytes>this.maxBytes||this.#bytes+bytes>this.maxBytes)throw Error('event store byte budget exceeded');
  this.#costs.set(owned,bytes);return {owned,bytes};
 }
 append(event){
  if(this.events.length>=this.maxEvents)throw Error('event store event budget exceeded');
  if(!event||event.sequence!==this.sequence+1||!Number.isSafeInteger(event.sequence))throw Error('event store sequence gap');
  const {owned,bytes}=this.own(event);this.events.push(owned);this.#bytes+=bytes;return owned;
 }
 appendContext(eventSequence,context){
  if(!Number.isSafeInteger(eventSequence)||eventSequence<=this.#acknowledged||eventSequence>this.sequence+1)
   throw Error('event store context sequence unavailable');
  if(this.contexts.length&&eventSequence<this.contexts[this.contexts.length-1].eventSequence)
   throw Error('event store context sequence regressed');
  const {owned,bytes}=this.own({eventSequence,context});this.contexts.push(owned);this.#bytes+=bytes;return owned;
 }
 snapshot(after,through){
  if(!Number.isSafeInteger(after)||!Number.isSafeInteger(through)||after<this.#acknowledged||after>through||through>this.sequence)
   throw Error('event store range unavailable');
  return Object.freeze({events:Object.freeze(this.events.slice(after-this.#acknowledged,through-this.#acknowledged)),
   hp_context:Object.freeze(this.contexts.filter(item=>after<item.eventSequence&&item.eventSequence<=through))});
 }
 served(through){
  if(!Number.isSafeInteger(through)||through<this.#acknowledged||through>this.sequence)throw Error('event store served range unavailable');
  this.#readThrough=Math.max(this.#readThrough,through);
 }
 ack(through){
  if(!Number.isSafeInteger(through)||through<0||through>this.#readThrough)
   return {acknowledged:this.#acknowledged,error:'event acknowledgement exceeds the read watermark'};
  if(through<=this.#acknowledged)return {acknowledged:this.#acknowledged,error:null};
  const countEvents=through-this.#acknowledged;
  let count=0;while(count<this.contexts.length&&this.contexts[count].eventSequence<=through)count++;
  let released=0;
  for(let i=0;i<countEvents;i++)released+=this.#costs.get(this.events[i]);
  for(let i=0;i<count;i++)released+=this.#costs.get(this.contexts[i]);
  if(!Number.isSafeInteger(released)||released<0||released>this.#bytes)throw Error('invalid event store retained prefix');
  // Validate before committing, and do not allocate removed-prefix arrays.
  this.events.copyWithin(0,countEvents);this.events.length-=countEvents;
  this.contexts.copyWithin(0,count);this.contexts.length-=count;
  this.#bytes-=released;
  this.#acknowledged=through;return {acknowledged:this.#acknowledged,error:null};
 }
 view(){
  if(!this.#view){const store=this;this.#view=Object.freeze({version:1,
   state:()=>Object.freeze({acknowledged:store.acknowledged,readThrough:store.readThrough,sequence:store.sequence,
    retainedBytes:store.retainedBytes,eventCount:store.events.length}),
   snapshot:(after,through)=>store.snapshot(after,through),served:through=>store.served(through)});}
  return this.#view;
 }
}
