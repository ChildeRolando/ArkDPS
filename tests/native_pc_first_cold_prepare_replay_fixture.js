'use strict';
// Real setup metadata closure -> first cold replay. No baseline auto mark,
// hand-recorded metadata or replacement autoLayout/identity resolver.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {world}=require('./native_pc_autochess_context_fixture.js');
const f=world({fixtureOptions:{roundResetMethod:true,settlement:true,enemyHpState:true}}),P=f.ptr(0).constructor,c=f.context;
f.write(f.character.add(0x58),f.unitClass);f.write(f.unitClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));
f.klass('Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,18]],f.unitClass,f.character);
f.klass('BObject','Torappu.Battle',9,[[1,'<instanceUid>k__BackingField',0x18,9],[3,'<playerSide>k__BackingField',0x20,17],[4,'<inited>k__BackingField',0x24,2],[5,'<borned>k__BackingField',0x25,2],[6,'<isDisappeared>k__BackingField',0x26,2]],f.ptr(0),f.bObject);
f.klass('BattleCharacterData','Torappu.Battle',36,[[4,'prefabKey',0x50,14],[8,'uniqueId',0x6c,9],[14,'isToken',0x90,2],[15,'isPredefined',0x91,2],[18,'tokenOrHostKey',0x98,14],[19,'tokenOrHostUniqueId',0xa0,9]],f.ptr(0),f.characterData);
for(const data of f.roleData)f.write(data,f.characterData);
for(const [key,value]of f.ownedClasses)if(value.name==='BattleCharacterData'&&key!==f.characterData.toString())f.ownedClasses.delete(key);
const methods=['readByteArray','readPointer','readU8','readU16','readU32','readS32','readU64','readS64','readFloat','readUtf16String','readCString'];
const original=Object.fromEntries(methods.map(name=>[name,P.prototype[name]]));
let replaying=false,late=0;
const oldCount=f.metadataExports.il2cpp_image_get_class_count,oldClass=f.metadataExports.il2cpp_image_get_class;
const all=new Map();for(let i=0;i<oldCount();i++){const k=oldClass(null,i);if(!k.isNull())all.set(k.toString(),k);}
for(const value of f.ownedClasses.values())all.set(value.klass.toString(),value.klass);
const parent=original.readPointer.call(f.controllerClass.add(0x58));f.klass('Singleton`1','Torappu',0,[],f.ptr(0),parent);all.set(parent.toString(),parent);
const name=k=>original.readCString.call(original.readPointer.call(k.add(0x10)));
const only=n=>[...all.values()].find(k=>name(k)===n);
const stringClass=only('String'),host=original.readPointer.call(f.controller.add(0x2e8)),manager=original.readPointer.call(f.controller.add(0x2c0)),container=original.readPointer.call(manager.add(0x18));
const arrayClass=f.klass('Unit[]','Torappu.Battle',0);all.set(arrayClass.toString(),arrayClass);
const live=f.unit('char_shared',55),liveItems=f.alloc();f.write(live,f.character);f.write(live.add(0x538),f.roleData[0]);f.write(live.add(0x24),1);f.write(live.add(0x25),1);f.write(live.add(0x26),0);f.write(live.add(0x20),1);f.write(liveItems,arrayClass);f.write(liveItems.add(0x18),1);f.write(liveItems.add(0x20),live);f.write(container.add(0x10),liveItems);f.write(container.add(0x20),1);
const token=f.unit('token_sample',56),tokenData=f.data('token_sample',998,{isToken:true,hostKey:'char_shared',hostUid:101});f.write(tokenData,f.characterData);f.write(token,f.character);f.write(token.add(0x538),tokenData);f.write(token.add(0x20),1);f.write(token.add(0x24),1);f.write(token.add(0x25),1);f.write(token.add(0x26),0);f.write(liveItems.add(0x28),token);f.write(liveItems.add(0x18),2);f.write(container.add(0x20),2);
const status=original.readPointer.call(f.manager.add(0x38)),model=original.readPointer.call(status.add(0x10));
const battle=original.readPointer.call(f.center.add(0xe8)),playerModel=original.readPointer.call(f.center.add(0xf8));
const groups=f.roleList,refs=f.roles[0][1];
const groupPair=original.readPointer.call(original.readPointer.call(original.readPointer.call(groups).add(0x58)).add(0x80)).add(1*32+8).readPointer().readPointer().readPointer().add(24).readPointer();
const target=new Map();
function link(k,n,value){target.set(k.toString()+':'+n,value);}
function objectLink(k,n,obj){link(k,n,original.readPointer.call(obj));}
objectLink(f.controllerClass,'m_singletonHost',host);objectLink(f.controllerClass,'<unitManager>k__BackingField',manager);
objectLink(only('SingletonHost'),'m_instStore',f.dict);objectLink(only('AutoChessGameManager'),'gameStatus',status);objectLink(only('AutoChessGameManager'),'dataCenter',f.center);
objectLink(only('AutoChessGameStatus'),'m_stateModel',model);objectLink(only('AutoChessGameStateModel'),'m_stateMachine',f.machine);
objectLink(only('AutoChessDataCenter'),'battleData',battle);objectLink(only('AutoChessDataCenter'),'playerDataModel',playerModel);
objectLink(only('AutoChessPlayerDataModel'),'m_allPlayerDatas',f.playerList);objectLink(only('AutoChessBattleDataModel'),'battleChessInsts',groups);
objectLink(only('ScenePlayerData'),'playerCard',original.readPointer.call(f.players[0][1].add(0x40)));
objectLink(groupPair,'value',refs);objectLink(only('UnitManager'),'<characters>k__BackingField',container);link(only('UnorderedArray`1'),'m_items',arrayClass);
objectLink(original.readPointer.call(f.dict),'_entries',original.readPointer.call(f.dict.add(0x18)));
for(const list of [f.playerList,groups])objectLink(original.readPointer.call(original.readPointer.call(list).add(0x58)),'_items',original.readPointer.call(list.add(0x10)));
objectLink(original.readPointer.call(refs),'_items',original.readPointer.call(refs.add(0x10)));
const otherClass=f.klass('UnrelatedManager','Torappu.Battle',0),other=f.alloc(),otherKey=f.alloc(),entries=original.readPointer.call(f.dict.add(0x18));f.write(other,otherClass);all.set(otherClass.toString(),otherClass);f.write(entries.add(0x18),2);f.write(entries.add(0x38),2);f.write(entries.add(0x40),otherKey);f.write(entries.add(0x48),other);f.write(f.dict.add(0x20),2);
let deepClass=f.character;for(let i=0;i<16;i++){deepClass=f.klass('DeepCharacter'+i,'Owned.Test',0,[],deepClass);all.set(deepClass.toString(),deepClass);}
const byType=new Map(),tables=new Map();
for(const k of all.values()){
 let count=original.readU16.call(k.add(0x124));if(!Number.isFinite(count))count=0;
 let table;try{table=original.readPointer.call(k.add(0x80));}catch{}const rows=[];
 for(let i=0;i<count;i++)try{const field=table.add(i*32);if(original.readPointer.call(field.add(16)).equals(k)){let type;try{type=original.readPointer.call(field.add(8));}catch{type=f.alloc();f.write(type,f.ptr(0));f.write(type.add(8),18<<16);}rows[i]=[original.readPointer.call(field),type,original.readS32.call(field.add(24))];}}catch{}
 try{original.readPointer.call(k.add(0x58));}catch{f.write(k.add(0x58),f.ptr(0));}
 const fresh=f.alloc();for(let i=1;i<Math.ceil(count*32/4096);i++)f.alloc();
 for(let i=0;i<count;i++){
  const row=rows[i]??[f.text('__unused_'+i),f.alloc(),0x1000+i*4],field=fresh.add(i*32),fieldName=original.readCString.call(row[0]);
  if(!rows[i]){f.write(row[1],f.ptr(0));f.write(row[1].add(8),8<<16);}
  f.write(field,row[0]);f.write(field.add(8),row[1]);f.write(field.add(16),k);f.write(field.add(24),row[2]);
  const selected=target.get(k.toString()+':'+fieldName);if(selected)byType.set(row[1].toString(),selected);
  if(['prefabKey','uid','nickName','nickNumber'].includes(fieldName))byType.set(row[1].toString(),stringClass);
 }
 f.write(k.add(0x80),fresh);f.write(k.add(0x124),count);tables.set(k.toString(),{table:fresh,count});
}
const values=[...all.values()];f.metadataExports.il2cpp_image_get_class_count=()=>values.length;f.metadataExports.il2cpp_image_get_class=(_image,index)=>values[index]??f.ptr(0);
f.metadataExports.il2cpp_class_get_fields=(k,it)=>{const n=Number(original.readU64.call(it).toString()),t=tables.get(k.toString());f.write(it,n+1);return t&&n<t.count?t.table.add(n*32):f.ptr(0);};
f.metadataExports.il2cpp_field_get_type=field=>original.readPointer.call(field.add(8));
f.metadataExports.il2cpp_class_from_type=t=>{if(byType.has(t.toString()))return byType.get(t.toString());const kind=(original.readU32.call(t.add(8))>>>16)&255,p=original.readPointer.call(t);if(kind===21)return original.readPointer.call(p.add(24));return p;};
c.__publishCaptureScope=graph=>{c.publishedScope=JSON.parse(JSON.stringify(graph));};c.__publishCapturePublication=graph=>{c.publishedPublication=JSON.parse(JSON.stringify(graph));};
c.ArrayBuffer=ArrayBuffer;c.NativePointer=P;c.NativeInteger=class{};c.__read=()=>{late++;throw Error('live read during replay');};
const bridge=fs.readFileSync('native_pc/native_event_runtime/bridge.js','utf8');vm.runInContext(bridge.slice(bridge.indexOf('function __integer('),bridge.indexOf('class NativeInteger')),c);
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/cold_plan.js','utf8'),c);
c.setupFound=Object.fromEntries(values.map(k=>[name(k),k]));c.setupFound.BattleCharacterData=f.characterData;c.setupFound.Character=f.character;c.setupFound.BObject=f.bObject;c.setupFound.SkillData=f.skillData;c.setupFound.Enemy=f.enemyClass;
assert.equal(vm.runInContext('nativeAutoLayouts.size',c),0,'No AutoChess baseline or layout preheat before real prepare');
P.prototype.readByteArray=function(n){if(n===1||n===2||n===4){const b=new ArrayBuffer(n),v=new DataView(b);if(n===1)v.setUint8(0,original.readU8.call(this));else if(n===2)v.setUint16(0,original.readU16.call(this),true);else v.setUint32(0,original.readU32.call(this),true);return b;}return original.readByteArray.call(this,n);};
vm.runInContext('globalThis.arkdpsNativeAsyncCapture=true;prepareNativeCaptureMetadata({found:setupFound,clock,skillF,mitigationMethods,skillCastStateMethods});',c);
assert.equal(vm.runInContext('nativeAutoLayouts.size',c)>0,true);
const plan=JSON.parse(vm.runInContext('JSON.stringify(nativeCaptureMetadata.cold_plan)',c));
function raw(p,n){
 if(n===1)return Uint8Array.of(original.readU8.call(p)).buffer;
 if(n===2){const b=new ArrayBuffer(2);new DataView(b).setUint16(0,original.readU8.call(p)|(original.readU8.call(p.add(1))<<8),true);return b;}
 const bytes=original.readByteArray.call(p,n),view=new DataView(bytes);
 // The scalar-cell fixture has no byte type tags; serialize these actual
 // adjacent BObject Boolean fields explicitly instead of folding into i32.
 for(const object of [live,token])for(const offset of [0x24,0x25,0x26]) {
  const at=BigInt(object.value)+BigInt(offset)-BigInt(p.value);
  if(at>=0n&&at<BigInt(n))view.setUint8(Number(at),original.readU8.call(object.add(offset)));
 }
 return bytes;
}
function capture(){
 const segments=[],fields=[];let dynamic=32768;
 function segment(field,p,b){segments.push({field_id:field.field_id,role:field.role,checkpoint:0,address:p.toString(),bytes:b,status:'ok',guard_id:field.guard_id});}
 function bytes(field,p,n){if(n)segment(field,p,raw(p,n));}
 function pointer(field,p){bytes(field,p,8);return original.readPointer.call(p);}
 function string(field,p,max){bytes(field,p,8);bytes(field,p.add(16),4);const n=original.readS32.call(p.add(16));assert(n>=0&&n<=max);const text=original.readUtf16String.call(p.add(20));const b=new ArrayBuffer(n*2),v=new DataView(b);for(let i=0;i<n;i++)v.setUint16(i*2,text.charCodeAt(i),true);segment(field,p.add(20),b);}
 function node(index,parent,field){
  const n=plan.projections[index];let active=field;
  if(n.local_fail_boundary){active={...field,field_id:dynamic++,guard_id:0};fields.push({...active,status:'ok'});}
  try{
   if(n.parent_byte_offset!==undefined){bytes(active,parent.add(n.parent_byte_offset),1);if(original.readU8.call(parent.add(n.parent_byte_offset))!==n.parent_byte_value)return;}
   let p=parent;for(const offset of n.pointer_offsets)p=pointer(active,p.add(offset));p=p.add(n.offset);
   if(p.isNull()&&n.allow_null)return;
   if(n.gate){let k=pointer(active,p),matched=k.toString()===n.expected_class;
    if(n.gate==='class_set')matched=n.expected_classes.includes(k.toString());
    if(n.gate==='ancestor')for(let i=0;!matched&&!k.isNull()&&i<n.ancestry_depth;i++){k=pointer(active,k.add(0x58));matched=k.toString()===n.expected_class;}
    if(!matched)return;
   }
   if(n.kind==='utf16'){string(active,p,n.max_units);return;}
   if(n.kind==='array'){
    bytes(active,p.add(n.count_offset),4);const count=original.readS32.call(p.add(n.count_offset)),items=pointer(active,p.add(n.items_offset));assert(count>=0&&count<=n.max_count);
    for(let i=0;i<count;i++){const slot=items.add(n.data_offset+i*n.element_bytes);bytes(active,slot,n.element_bytes);if(n.skip_negative_i32_offset>=0&&original.readS32.call(slot.add(n.skip_negative_i32_offset))<0)continue;for(let j=0;j<n.child_count;j++)node(n.child_begin+j,slot,active);}return;
   }
   bytes(active,p,n.bytes);for(let j=0;j<n.child_count;j++)node(n.child_begin+j,p,active);
  }catch(error){if(active===field||!String(error.message).includes('Unreadable fixture address'))throw error;const state=fields.find(x=>x.field_id===active.field_id);state.status='field_unreadable';for(const s of segments)if(s.field_id===active.field_id)s.status='unreadable';}
 }
 for(const field of plan.fields){const start=fields.length;fields.push({...field,status:'ok'});try{node(field.projection_root,f.ptr(field.absolute_address),field);}catch(error){if(!String(error.message).includes('Unreadable fixture address'))throw error;for(const state of fields.slice(start))if(state.status==='ok')state.status='field_unreadable';for(const segment of segments)if(fields.slice(start).some(state=>state.field_id===segment.field_id))segment.status='unreadable';}}
 const clock=vm.runInContext('clock',c);for(const [id,role]of [[100,'clock_open'],[101,'clock_close']]){const field={field_id:id,role,guard_id:0};bytes(field,clock,12);fields.push({...field,status:'ok'});}
 return {version:1,edge:'leave',checkpoint:0,segments,fields,facts:{cold_revision:1,mode_epoch:0,scope_mode_epoch:0}};
}
const view=(p,n)=>new DataView(c.ownedRead(p.toString(),n));
c.ownedRead=(p,n)=>c.__arkdpsOwnedRead(BigInt(p),n);
for(const[name,size,get]of [['readU8',1,'getUint8'],['readU16',2,'getUint16'],['readU32',4,'getUint32'],['readS32',4,'getInt32'],['readFloat',4,'getFloat32']])P.prototype[name]=function(){return replaying?view(this,size)[get](0,true):original[name].call(this);};
P.prototype.readByteArray=function(n){return replaying?c.ownedRead(this.toString(),n):original.readByteArray.call(this,n);};
P.prototype.readPointer=function(){return replaying?f.ptr(view(this,8).getBigUint64(0,true)):original.readPointer.call(this);};
for(const name of ['readU64','readS64'])P.prototype[name]=function(){if(!replaying)return original[name].call(this);const x=view(this,8)[name==='readS64'?'getBigInt64':'getBigUint64'](0,true);return{toString:()=>x.toString(),toNumber:()=>Number(x)};};
P.prototype.readUtf16String=function(n){if(!replaying)return original.readUtf16String.call(this,n);const v=view(this,n*2);let text='';for(let i=0;i<n;i++)text+=String.fromCharCode(v.getUint16(i*2,true));return text;};
P.prototype.readCString=function(){if(replaying){late++;throw Error('metadata live read');}return original.readCString.call(this);};
for(const[name,native]of Object.entries(f.metadataExports))f.metadataExports[name]=(...args)=>{if(replaying){if(name==='il2cpp_class_is_assignable_from')return c.__arkdpsCapturedAssignable(...args);late++;throw Error('NativeFunction in replay');}return native(...args);};
function reduce(record){c.ownedRecord=record;replaying=true;try{return vm.runInContext('__arkdpsReplayBegin(ownedRecord);try{globalThis.binding=prepareCapturedColdBinding(ownedRecord);}finally{globalThis.summary=__arkdpsReplayEnd();}binding;',c);}finally{replaying=false;}}
for(const state of [0,1]){f.write(f.controller.add(0x228),state);const unavailable=reduce(capture());assert.equal(c.summary.complete,true,JSON.stringify(c.summary));assert.equal(unavailable,null);}
f.write(f.controller.add(0x228),2);
const savedStage=original.readPointer.call(f.level.add(0x18));
for(const stage of [f.ptr(0),f.text('')]){f.write(f.level.add(0x18),stage);const unavailable=reduce(capture());assert.equal(c.summary.complete,true,JSON.stringify(c.summary));assert.equal(unavailable,null);}
f.write(f.level.add(0x18),savedStage);
const savedStatus=original.readPointer.call(f.manager.add(0x38));f.write(f.manager.add(0x38),f.ptr(0));
const notInitialized=reduce(capture());assert.equal(c.summary.complete,true,JSON.stringify(c.summary));assert(notInitialized);assert.equal(notInitialized.native_autochess_context.status,'unknown');
f.write(f.manager.add(0x38),savedStatus);
const autoStage=original.readPointer.call(f.level.add(0x18));f.write(f.level.add(0x18),f.text('main_01-01'));
const normal=reduce(capture());assert.equal(c.summary.complete,true,JSON.stringify(c.summary));assert(normal);assert.equal(normal.native_autochess_context,undefined);
f.write(f.level.add(0x18),autoStage);
f.write(container.add(0x20),0);f.write(container.add(0x10),f.ptr(0));
const empty=reduce(capture());assert.equal(c.summary.complete,true,JSON.stringify(c.summary));assert(empty);assert.equal(empty.native_autochess_context.status,'available');assert.equal(empty.native_autochess_context.memberships.length,4);
f.write(container.add(0x20),2);f.write(container.add(0x10),liveItems);
const record=capture(),binding=reduce(record);
assert.equal(c.summary.complete,true,JSON.stringify(c.summary));
assert(binding,vm.runInContext('JSON.stringify(nativeAutoBindingDiagnostic)',c));
assert.equal(binding.native_autochess_context.status,'available');assert.equal(binding.native_autochess_context.memberships.length,5);

// Actual source HP callback checks Enemy assignability on a >10 ancestor
// foreign Character. Known-unrelated must be proved without replay parent reads.
const ignored=f.unit('char_long_chain',700);f.write(ignored,deepClass);f.write(ignored.add(0x148),f.text('char_long_chain'));
const hpHook=f.callback('SetHpInternal',f.entity),call={threadId:1};
const parentBytes=new ArrayBuffer(8);new DataView(parentBytes).setBigUint64(0,deepClass.value,true);
const hpRecord={version:1,edge:'entry',checkpoint:0,fields:[{field_id:300,role:'body',checkpoint:0,status:'ok'}],segments:[{field_id:300,role:'body',checkpoint:0,address:ignored.toString(),bytes:raw(ignored,28),status:'ok'}],facts:{mode_epoch:0,scope_mode_epoch:0}};
const ignoredId=original.readPointer.call(ignored.add(0x148));
for(const [address,bytes]of [[ignored.add(0x148),8],[ignoredId,8],[ignoredId.add(16),4],[ignoredId.add(20),'char_long_chain'.length*2]])hpRecord.segments.push({field_id:300,role:'body',checkpoint:0,address:address.toString(),bytes:raw(address,bytes),status:'ok'});
c.hpRecord=hpRecord;c.hpInvoke=()=>hpHook.onEnter.call(call,[ignored]);replaying=true;
try{vm.runInContext('__arkdpsReplayBegin(hpRecord);try{__arkdpsReplayPoint("body",hpInvoke);}finally{globalThis.hpSummary=__arkdpsReplayEnd();}',c);}finally{replaying=false;}
assert.equal(c.hpSummary.complete,true,JSON.stringify(c.hpSummary));assert.equal(call.write,undefined);
assert.equal(late,0);
console.log('PASS_FIRST_COLD_REAL_PREPARE empty/nonempty Character+Token/mixed singleton/nonplaying0+1/null+empty stage/unready manager/normal-to-Auto; zero live replay reads');
