'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
let next=0x10000n,replaying=false,calls=0,reads=0;const cells=new Map(),classes=[],types=new Map(),layouts=new Map();
class Pointer{
 constructor(value){this.value=BigInt(value);}
 add(offset){return new Pointer(this.value+BigInt(offset));}
 toString(){return '0x'+this.value.toString(16);}
 isNull(){return this.value===0n;}
 equals(other){return this.value===other.value;}
 readPointer(){return new Pointer(cells.get(this.toString())??0);}
 readU16(){return Number(cells.get(this.toString())??0);}
 readU64(){return {toNumber:()=>this.readU16()};}
 readCString(){return cells.get(this.toString());}
 readByteArray(size){
  if(replaying)throw Error('live metadata read during replay');reads++;
  const bytes=new ArrayBuffer(size),view=new DataView(bytes),value=BigInt(cells.get(this.toString())??0);
  if(size===8)view.setBigUint64(0,value,true);else if(size===4)view.setUint32(0,Number(value),true);else if(size===2)view.setUint16(0,Number(value),true);else throw Error('unexpected metadata read');
  return bytes;
 }
}
const ptr=value=>new Pointer(value),zero=ptr(0),alloc=()=>{const p=ptr(next);next+=0x1000n;return p;};
function write(p,value){cells.set(p.toString(),value instanceof Pointer?value.value:value);}
const object=alloc(),domain=alloc(),assemblyList=alloc(),image=alloc(),assembly=alloc();
write(assemblyList,assembly);const names=new Map();
function klass(name,namespace,parent=zero){const k=alloc();names.set(k.toString(),{name,namespace});classes.push(k);write(k.add(0x58),parent);return k;}
const systemObject=klass('Object','System'),string=klass('String','System',systemObject);
const ns='Torappu.Battle.AutoChess';
const definitions=[['BattleController','Torappu.Battle'],['Character','Torappu.Battle'],['BObject','Torappu.Battle'],
 ['SingletonHost',''],['AutoChessGameManager',ns],['AutoChessGameStatus',ns],['AutoChessGameStateModel',''],['AutoChessGameStateMachine',''],
 ['AutoChessDataCenter','Torappu.Battle.DataCenter'],['AutoChessPlayerDataModel',ns],['ScenePlayerData',''],['PlayerCard',ns],
 ['AutoChessBattleDataModel',ns],['ChessInst',ns],['UnitManager','Torappu.Battle'],['BattleCharacterData','Torappu.Battle']];
const found=Object.fromEntries(definitions.map(([n,s])=>[n,klass(n,s,systemObject)]));
const dictionary=klass('Dictionary`2','System.Collections.Generic',systemObject),entry=klass('Entry','',systemObject),entryArray=klass('Entry[]','',systemObject);
const unordered=klass('UnorderedArray`1','Torappu',systemObject),unitArray=klass('Unit[]','Torappu.Battle',systemObject);
const refs=klass('List`1','System.Collections.Generic',systemObject),refArray=klass('ChessInst[]',ns,systemObject);
function generic(pair,valueClass){
 const list=klass('List`1','System.Collections.Generic',systemObject),dict=klass('ListDict`2','Torappu',list),array=klass('KeyValuePair`2[]','System.Collections.Generic',systemObject);
 write(dict.add(0x124),1);layouts.set(dict.toString(),{valueClass});return {pair,list,dict,array};
}
const player=generic(klass('KeyValuePair`2','System.Collections.Generic',systemObject),found.ScenePlayerData);
const group=generic(klass('KeyValuePair`2','System.Collections.Generic',systemObject),refs);
function field(target=string,data=null){const type=alloc();types.set(type.toString(),target);return {type,data:data??target,kind:18,offset:0x10};}
function element(k){const type=alloc(),genericClass=alloc();write(type,genericClass);write(type.add(8),21<<16);write(genericClass.add(24),k);types.set(type.toString(),k);return type;}
const elements=new Map([[dictionary.toString(),element(entry)],[player.list.toString(),element(player.pair)],[group.list.toString(),element(group.pair)]]);
const links=new Map([
 [found.SingletonHost.toString()+':m_instStore',dictionary],
 [found.AutoChessPlayerDataModel.toString()+':m_allPlayerDatas',player.dict],
 [found.AutoChessBattleDataModel.toString()+':battleChessInsts',group.dict],
 [group.pair.toString()+':value',refs],
 [found.UnitManager.toString()+':<characters>k__BackingField',unordered],
 [unordered.toString()+':m_items',unitArray],
 [dictionary.toString()+':_entries',entryArray],
 [player.list.toString()+':_items',player.array],[group.list.toString()+':_items',group.array],[refs.toString()+':_items',refArray],
]);
const apiTable={
 il2cpp_domain_get_assemblies:(_domain,size)=>{write(size,1);return assemblyList;},
 il2cpp_assembly_get_image:()=>image,il2cpp_image_get_name:()=>({readCString:()=> 'Assembly-CSharp.dll'}),
 il2cpp_image_get_class_count:()=>classes.length,il2cpp_image_get_class:(_image,i)=>classes[i],
 il2cpp_class_from_type:type=>types.get(type.toString())??zero,
};
const context=vm.createContext({ArrayBuffer,DataView,Uint8Array,domain,ptr,Memory:{alloc},
 api(name){return (...args)=>{if(replaying)throw Error('metadata API in replay');calls++;return apiTable[name](...args);};},
 className:k=>names.get(k.toString()).name,classNamespace:k=>names.get(k.toString()).namespace,
 classFieldOffsets:k=>{assert(!replaying);assert(names.has(k.toString()));return {};},
 autoIdentity:(k,name,namespace)=>names.get(k.toString())?.name===name&&names.get(k.toString())?.namespace===namespace,
 autoLayout(k,name,namespace,_count,specs){
  assert.equal(names.get(k.toString()).name,name);assert.equal(names.get(k.toString()).namespace,namespace);
  return Object.fromEntries(specs.map(([_i,n,offset,kinds])=>{
   const f=field(links.get(k.toString()+':'+n)??string,elements.get(k.toString()));f.kind=kinds[0];f.offset=offset;return [n,f];
  }));
 },__arkdpsReplayActive:()=>replaying,__arkdpsReplayGap:message=>{throw Error('GAP '+message);}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../native_pc/native_event_runtime/cold_plan.js'),'utf8'),context);
context.meta={found,clock:object};
const metadata=vm.runInContext('__arkdpsPrepareColdMetadata(meta)',context);
assert.equal(metadata.cold_metadata_ready,true);
assert.equal(metadata.classes.AutoChessGameManager,found.AutoChessGameManager.toString());
// Run the production adapter setup, rather than only its builder in isolation.
const skillData=klass('SkillData','Torappu.Battle',systemObject),fieldTable=alloc(),prefabName=alloc(),prefabType=alloc(),systemNamespace=alloc();
write(prefabName,'prefabKey');write(fieldTable,prefabName);write(fieldTable.add(8),prefabType);types.set(prefabType.toString(),string);
write(skillData.add(0x80),fieldTable);write(skillData.add(0x124),1);write(systemNamespace,'System');write(string.add(0x18),systemNamespace);
found.SkillData=skillData;apiTable.il2cpp_field_get_type=f=>f.add(8).readPointer();
const adapter=fs.readFileSync(path.join(__dirname,'../event_probe_native_pc.js'),'utf8');
const setup=adapter.slice(adapter.indexOf('function prepareNativeCaptureMetadata('),adapter.indexOf('const nativeCaptureColdBindings='));
vm.runInContext("let nativeCaptureMetadata=null;const nativeCaptureModeHistory=new Map();const nativeAutoDesiredMode='all';globalThis.arkdpsNativeAsyncCapture=true;"+setup,context);
vm.runInContext('prepareNativeCaptureMetadata({...meta,skillF:{charSkill:1,owner:2,data:3,prefab:4,id:5,name:6}});globalThis.adapterMetadata=nativeCaptureMetadata;',context);
assert.equal(context.adapterMetadata.cold_metadata_ready,true);assert.equal(context.adapterMetadata.immutable_class_parents,true);
assert.equal(context.adapterMetadata.cold_plan.fields.length,3);assert.equal(context.adapterMetadata.classes.AutoChessGameManager,found.AutoChessGameManager.toString());
const before={reads,calls};replaying=true;
context.manager=found.AutoChessGameManager;
assert.equal(vm.runInContext('__arkdpsColdClassIdentity(manager,"AutoChessGameManager","Torappu.Battle.AutoChess")',context),true);
assert.equal(vm.runInContext('__arkdpsColdClassIdentity(ptr(0xdead),"AutoChessGameManager","Torappu.Battle.AutoChess")',context),false);
assert.throws(()=>vm.runInContext('__arkdpsColdClassIdentity(ptr(0xdead),"UnverifiedClass","Unknown")',context),/GAP/);
context.pointer=found.Character.add(0x58);
const frozen=vm.runInContext('__arkdpsColdMetadataRead(pointer,8)',context);
assert.equal(new DataView(frozen).getBigUint64(0,true),systemObject.value);
write(found.Character.add(0x58),0xdead);new DataView(frozen).setBigUint64(0,0n,true);
assert.equal(new DataView(vm.runInContext('__arkdpsColdMetadataRead(pointer,8)',context)).getBigUint64(0,true),systemObject.value);
assert.throws(()=>vm.runInContext('__arkdpsColdMetadataRead(ptr(0xdead),8)',context),/GAP/);
assert.deepEqual({reads,calls},before);
// Closed generic pairs are separate keys even though their class names match.
context.playerElement=elements.get(player.list.toString());context.groupElement=elements.get(group.list.toString());
const p=vm.runInContext('__arkdpsColdMetadataRead(playerElement,8)',context),g=vm.runInContext('__arkdpsColdMetadataRead(groupElement,8)',context);
assert.notEqual(new DataView(p).getBigUint64(0,true),new DataView(g).getBigUint64(0,true));
process.stdout.write(JSON.stringify({metadata_ready:true,generic_instantiations:2,replay_live_reads:reads-before.reads,replay_native_calls:calls-before.calls})+'\n');
