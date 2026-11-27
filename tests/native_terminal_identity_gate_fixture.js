// Actual shared functions, synthetic live pointers, and read spies only.
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../event_probe_runtime.js'),'utf8');
const start=source.indexOf('function terminalClassName('),end=source.indexOf('\nfunction currentTerminalBoundary(',start);
assert(start>=0&&end>start);const functions=source.slice(start,end);
let checks=0;
function fixture({replay=false,firstClass=0x1100n,blockClass=firstClass,loggerClass=0x3100n,levelClass=0x4100n,statsClass=0x5100n}={}){
 const pointers=new Map([[0x1000n,firstClass],[0x1010n,0x2000n],[0x10e0n,0x3000n],[0x1160n,0x4000n],
  [0x2028n,0x1000n],[0x3000n,loggerClass],[0x3018n,0x1000n],[0x3030n,0x5000n],[0x4000n,levelClass],[0x5000n,statsClass]]);
 const reads={scalars:0,blocks:0,metadata:0},rejections=[];
 class Pointer{
  constructor(value){this.value=BigInt(value);}add(offset){return new Pointer(this.value+BigInt(offset));}
  equals(other){return this.value===other.value;}isNull(){return this.value===0n;}toString(){return '0x'+this.value.toString(16);}
  readPointer(){reads.scalars++;assert(pointers.has(this.value),'unplanned pointer read');return new Pointer(pointers.get(this.value));}
  readByteArray(size){reads.blocks++;const buffer=new ArrayBuffer(size),view=new DataView(buffer);
   for(const [address,value]of pointers)if(address>=this.value&&address+8n<=this.value+BigInt(size))view.setBigUint64(Number(address-this.value),value,true);
   if(this.value===0x1000n)view.setBigUint64(0,blockClass,true);return buffer;}
 }
 const names=new Map([['0x1100','BattleController'],['0x3100','BattleLogger'],['0x4100','LevelData'],['0x5100','BattleStats'],['0x6100','Enemy']]);
 const context={probeAdapter:{loggerOffset:0xe0,levelOffset:0x160},terminalClassNames:new Map(),immutableClassNames:names,
  ptr:value=>new Pointer(value),__arkdpsReplayActive:()=>replay,
  className:klass=>{if(names.has(klass.toString()))return names.get(klass.toString());reads.metadata++;throw Error('uncaptured metadata read');},
  checkedNativeView:(pointer,size)=>new DataView(pointer.readByteArray(size))};
 vm.createContext(context);vm.runInContext(functions,context);
 const result=context.terminalIdentity(new Pointer(0x1000n),new Pointer(0x1100n),(guard,observed)=>{rejections.push({guard,observed});return null;});
 return {result,reads,rejections};
}
for(const firstClass of [0x6100n,0x7fff12120000n,0n]){
 const actual=fixture({replay:true,firstClass});assert.equal(actual.result,null);assert.equal(actual.reads.blocks,0);
 assert.equal(actual.reads.metadata,0);assert.equal(actual.rejections[0].guard,'controller_class_mismatch');checks++;
}
// A supplied captured class word cannot authorize a contradictory later block.
const raced=fixture({replay:true,firstClass:0x1100n,blockClass:0x6100n});assert.equal(raced.result,null);
assert.equal(raced.reads.scalars,1);assert.equal(raced.reads.blocks,1);assert.equal(raced.rejections[0].guard,'controller_class_mismatch');checks++;
const rawWrong=fixture({firstClass:0x1100n,blockClass:0x6100n});assert.equal(rawWrong.result,null);
assert.equal(rawWrong.reads.scalars,0);assert.equal(rawWrong.reads.blocks,1);checks++;
for(const role of ['loggerClass','levelClass','statsClass']){
 const actual=fixture({replay:true,[role]:0x7fff12120000n});assert.equal(actual.result,null);assert.equal(actual.reads.metadata,0);
 assert.equal(actual.rejections[0].guard,role.replace('Class','_class_mismatch'));checks++;
}
for(const replay of [false,true]){const actual=fixture({replay});assert(actual.result);assert.equal(actual.rejections.length,0);assert.equal(actual.reads.blocks,2);checks++;}
console.log(JSON.stringify({checks,captured_class_race_rejected:true,raw_wrong_block_rejected:true,unknown_classes_rejected_without_metadata:true}));
