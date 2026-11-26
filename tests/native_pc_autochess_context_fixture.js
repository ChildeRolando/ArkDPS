// Execute the production Windows adapter against owned managed-memory fixtures.
const assert=require('node:assert/strict');
const path=require('node:path');
const vm=require('node:vm');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const sourcePath=path.join(process.env.ARKDPS_PROBE_FIXTURE_ROOT ?? path.join(__dirname,'..'),'event_probe_native_pc.js');

function world({sameCid=true,conflictingUid=false,duplicatePlayer=false,token=false,unknownToken=false,tokenCount=1,rolesPerPlayer=1,fixtureOptions={},platform='native_pc'}={}){
 const actualSource=platform==='mumu'?path.join(process.env.ARKDPS_PROBE_FIXTURE_ROOT ?? path.join(__dirname,'..'),'event_probe_android2791.js'):sourcePath;
 const f=fixture({platform,sourcePath:actualSource,...fixtureOptions}),zero=f.ptr(0),NS='Torappu.Battle.AutoChess';
 const adapter=vm.runInContext('probeAdapter',f.context);
 const ownedClasses=new Map();
 const oldMark=f.context.rpc.exports.mark();
 assert.equal(oldMark.native_autochess_context,undefined,'ordinary stages must not read an absent host');
 function type(kind,data=zero){const p=f.alloc();f.write(p,data);f.write(p.add(8),kind<<16);return p;}
 function klass(name,ns,count,fields=[],parent=zero,existing=null){
  if(platform==='mumu'){fields=fields.map((field,index)=>[index,...field.slice(1)]);count=fields.length;}
  const k=existing||f.alloc(),table=f.alloc();
  f.write(k.add(0x10),f.text(name));f.write(k.add(0x18),f.text(ns));
  f.write(k.add(0x124),count);f.write(k.add(0x80),table);f.write(k.add(0x58),parent);
  for(const [index,name,offset,kind,data]of fields){
   const p=table.add(index*32);f.write(p,f.text(name));f.write(p.add(8),type(kind,data));
   f.write(p.add(16),k);f.write(p.add(24),offset);
  }
  ownedClasses.set(k.toString(),{name,namespace:ns,klass:k});return k;
 }
 function obj(k){const p=f.alloc();f.write(p,k);return p;}
 const stringClass=klass('String','System',0);
 if(platform==='mumu')klass('LevelData','Torappu',1,[[0,'levelId',f.androidBattleLayout.levelId,14]],zero,f.level.readPointer());
 function text(s){const p=f.managedString(s);f.write(p,stringClass);return p;}
 const listClasses=new Map();
 function listClass(element=zero,key='plain'){
  if(!listClasses.has(key))listClasses.set(key,klass('List`1','System.Collections.Generic',6,
   [[1,'_items',0x10,29,element],[2,'_size',0x18,8],[3,'_version',0x1c,8]]));
  return listClasses.get(key);
 }
 function list(values,name,ns){
  const p=obj(listClass(zero,name+ns)),array=obj(klass(name+'[]',ns,0));
  f.write(p.add(0x10),array);f.write(p.add(0x18),values.length);f.write(p.add(0x1c),1);
  f.write(array.add(0x18),values.length);
  values.forEach((value,i)=>f.write(array.add(0x20+i*8),value));return p;
 }
 function pairs(values,valueKind){
  const pair=klass('KeyValuePair`2','System.Collections.Generic',2,
   [[0,'key',0x10,8],[1,'value',0x18,valueKind]]);
  const generic=f.alloc();f.write(generic.add(24),pair);
  const element=type(21,generic),parent=listClass(element,element.toString());
  const p=obj(klass('ListDict`2','Torappu',1,[],parent)),array=obj(klass('KeyValuePair`2[]','System.Collections.Generic',0));
  f.write(p.add(0x10),array);f.write(p.add(0x18),values.length);f.write(p.add(0x1c),1);
  f.write(array.add(0x18),values.length);
  values.forEach(([key,value],i)=>{f.write(array.add(0x20+i*16),key);f.write(array.add(0x28+i*16),value);});
  return p;
 }
 const dataClass=klass('BattleCharacterData','Torappu.Battle',36,
  [[4,'prefabKey',0x50,14],[8,'uniqueId',0x6c,9],[14,'isToken',0x90,2],
   [15,'isPredefined',0x91,2],[18,'tokenOrHostKey',0x98,14],[19,'tokenOrHostUniqueId',0xa0,9]]);
 function data(cid,uid,{isToken=false,hostKey=null,hostUid=0}={}){
  const p=obj(dataClass);f.write(p.add(0x50),text(cid));f.write(p.add(0x6c),uid);
  f.write(p.add(0x90),+isToken);f.write(p.add(0x91),0);
  f.write(p.add(0x98),hostKey?text(hostKey):zero);f.write(p.add(0xa0),hostUid);return p;
 }
 const cardClass=klass('PlayerCard',NS,6,[[0,'nickName',0x10,14],[1,'nickNumber',0x18,14]]);
 const playerClass=klass('ScenePlayerData','',37,[[0,'index',0x10,8],[1,'uid',0x18,14],[6,'playerCard',0x40,18]]);
 const players=[];
 for(let index=1;index<=4;index++){
  const p=obj(playerClass),card=obj(cardClass);
  f.write(p.add(0x10),index);f.write(p.add(0x18),text('account-'+(duplicatePlayer&&index===2?1:index)));
  f.write(card.add(0x10),text('玩家'+index));f.write(card.add(0x18),text('000'+index));
  f.write(p.add(0x40),card);players.push([index,p]);
 }
 const playerList=pairs(players,18),playerModel=obj(klass('AutoChessPlayerDataModel',NS,17,[[0,'m_allPlayerDatas',0x18,21]]));
 f.write(playerModel.add(0x18),playerList);
 const chessClass=klass('ChessInst',NS,27,[[6,'playerIndex',0x30,8],[8,'side',0x40,17],[10,'battleCharacterData',0x48,18]]);
 const roles=[],roleData=[];
 for(let index=1;index<=4;index++){
  const members=[];
  for(let offset=0;offset<rolesPerPlayer;offset++){
   const p=obj(chessClass),d=data(sameCid?'char_shared':'char_'+index,
    conflictingUid&&index===2&&offset===0?101:100+index+offset*10);
   f.write(p.add(0x30),index);f.write(p.add(0x40),index);f.write(p.add(0x48),d);
   roleData.push(d);members.push(p);
  }
  roles.push([index,list(members,'ChessInst',NS)]);
 }
 const roleList=pairs(roles,21),battleData=obj(klass('AutoChessBattleDataModel',NS,53,[[1,'battleChessInsts',0x20,21]]));
 f.write(battleData.add(0x20),roleList);
 const machine=obj(klass('AutoChessGameStateMachine','',13,[[1,'<gameCurrentStateId>k__BackingField',0x5c,17]]));
 let phaseEnum=null;const enumValues=new Map();
 if(platform==='mumu'){
  const names={NONE:0,LOADING:1,SP_PREPARE:2,PREPARE:3,BATTLE:4,HELP_BATTLE:5,BOSS_BATTLE:6,
   SETTLE:7,END:8,PAUSE:9,BATTLE_WAITING:100,BOSS_PREPARE_WAITING:101,PREPARE_RESTART:102,
   SP_PREPARE_RESTART:103,BOSS_BATTLE_RESTART:104};
  phaseEnum=klass('AutoChessGameStateType','Torappu',16,[[0,'value__',0x10,8]],klass('Enum','System',0));
  f.write(phaseEnum.add(0x124),16);
  const table=phaseEnum.add(0x80).readPointer();let index=1;
  for(const [name,value]of Object.entries(names)){
   const field=table.add(index++*32),t=type(17,phaseEnum);
   f.write(t.add(8),(17<<16)|0x50);f.write(field,f.text(name));f.write(field.add(8),t);
   f.write(field.add(16),phaseEnum);f.write(field.add(24),0);enumValues.set(field.toString(),value);
  }
  const machineType=machine.readPointer().add(0x80).readPointer().add(8).readPointer();
  f.write(machineType,phaseEnum);
  f.metadataExports.il2cpp_class_from_type=t=>t.readPointer();
  f.metadataExports.il2cpp_field_static_get_value=(field,out)=>{
   assert(enumValues.has(field.toString()),'only proved literal metadata can be read');
   f.write(out,enumValues.get(field.toString()));
  };
 }
 f.write(machine.add(0x5c),4);
 const model=obj(klass('AutoChessGameStateModel','',13,[[0,'m_stateMachine',0x18,18]]));f.write(model.add(0x18),machine);
 const status=obj(klass('AutoChessGameStatus',NS,62,[[0,'m_stateModel',0x10,18]]));f.write(status.add(0x10),model);
 const center=obj(klass('AutoChessDataCenter','Torappu.Battle.DataCenter',151,
  [[5,'<selfPlayerIndex>k__BackingField',0x38,8],[6,'<currentRound>k__BackingField',0x3c,8],
   [28,'battleData',0xe8,18],[30,'playerDataModel',0xf8,18]]));
 f.write(center.add(0x38),4);f.write(center.add(0x3c),1);f.write(center.add(0xe8),battleData);f.write(center.add(0xf8),playerModel);
 const viewMode=platform==='mumu'?fixtureOptions.autoChessViewMode:undefined;
 const viewFields=[];
 if(viewMode&&Object.hasOwn(viewMode,'in_step_mode'))viewFields.push([5,'<inStepMode>k__BackingField',0x1c,2]);
 if(viewMode&&Object.hasOwn(viewMode,'enable_view_multi_battle'))viewFields.push([7,'<enableViewMultiBattle>k__BackingField',0x1e,2]);
 const manager=obj(klass('AutoChessGameManager',NS,152,[[12,'gameStatus',0x38,18],[13,'dataCenter',0x40,18],...viewFields]));
 f.write(manager.add(0x38),status);f.write(manager.add(0x40),center);
 if(viewMode&&Object.hasOwn(viewMode,'in_step_mode'))f.write(manager.add(0x1c),Number(viewMode.in_step_mode));
 if(viewMode&&Object.hasOwn(viewMode,'enable_view_multi_battle'))f.write(manager.add(0x1e),Number(viewMode.enable_view_multi_battle));
 const entry=klass('Entry','',4,[[0,'hashCode',0x10,8],[1,'next',0x14,8],[2,'key',0x18,18],[3,'value',0x20,28]]);
 const generic=f.alloc();f.write(generic.add(24),entry);
 const element=type(21,generic),dict=obj(klass('Dictionary`2','System.Collections.Generic',14,
  [[1,'_entries',0x18,29,element],[2,'_count',0x20,8],[4,'_freeCount',0x28,8],[5,'_version',0x2c,8]]));
 const entries=obj(klass('Entry[]','',0));f.write(entries.add(0x18),1);
 f.write(entries.add(0x20),1);f.write(entries.add(0x28),obj(klass('RuntimeType','System',0)));f.write(entries.add(0x30),manager);
 f.write(dict.add(0x18),entries);f.write(dict.add(0x20),1);f.write(dict.add(0x28),0);f.write(dict.add(0x2c),1);
 const host=obj(klass('SingletonHost','',1,[[0,'m_instStore',0x10,21]]));f.write(host.add(0x10),dict);
 const container=obj(klass('UnorderedArray`1','Torappu',3,
  [[0,'m_items',0x10,29],[2,'<count>k__BackingField',0x20,8]]));
 f.write(container.add(0x10),zero);f.write(container.add(0x20),0);
 const unitManager=obj(klass('UnitManager','Torappu.Battle',7,[[1,'<characters>k__BackingField',0x18,21]]));
 f.write(unitManager.add(0x18),container);
 klass('BattleController','Torappu.Battle',515,
  [[0,'m_state',fixtureOptions.stateMetadataOffset??(f.androidBattleLayout?.state??0x228),fixtureOptions.stateMetadataKind??8],
   [1,'m_logger',fixtureOptions.loggerMetadataOffset??(f.androidBattleLayout?.logger??0xe0),18],
   [2,'m_levelData',f.androidBattleLayout?.level??0x160,18],
   [100,'<unitManager>k__BackingField',fixtureOptions.unitManagerOffset??(platform==='mumu'?0x2b8:0x2c0),18],
   [110,'m_singletonHost',0x2e8,18]],zero,f.controllerClass);
 // Keep the existing singleton parent used by currentNativeIdentity.
 const runtimeParent=f.alloc(),statics=f.alloc();f.write(runtimeParent.add(0xb8),statics);f.write(statics,f.controller);
 f.write(f.controllerClass.add(0x58),runtimeParent);
 f.write(f.controller.add(0x2e8),host);f.write(f.controller.add(fixtureOptions.unitManagerOffset??(platform==='mumu'?0x2b8:0x2c0)),unitManager);
 f.write(f.level.add(f.androidBattleLayout?.levelId??0x18),text('Activities/ACT3AUTOCHESS/level_act3autochess_m01'));
 const tokenData=[];
 if(token||unknownToken){
  const bObject=klass('BObject','Torappu.Battle',9,[[3,'<playerSide>k__BackingField',0x20,17],
   [4,'<inited>k__BackingField',0x24,2],[5,'<borned>k__BackingField',0x25,2],
   [6,'<isDisappeared>k__BackingField',0x26,2]]);
  const entity=klass('Entity','Torappu.Battle',410,[],bObject),unit=klass('Unit','Torappu.Battle',10,[],entity);
  const character=klass('Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,18]],unit);
  const array=obj(klass('Unit[]','Torappu.Battle',0));f.write(array.add(0x18),tokenCount);
  for(let i=0;i<tokenCount;i++){
   const t=obj(character),d=data('token_shared',999+i,{isToken:true,hostKey:'char_shared',hostUid:unknownToken?0:101});
   f.write(t.add(0x538),d);f.write(t.add(0x20),1);f.write(t.add(0x24),1);f.write(t.add(0x25),1);f.write(t.add(0x26),0);
   f.write(array.add(0x20+8*i),t);tokenData.push(d);
  }
  f.write(container.add(0x10),array);f.write(container.add(0x20),tokenCount);
 }
 const apiCount=f.apiCalls.length;
 return {...f,ownedClasses,adapter,machine,center,manager,dict,playerList,players,roles,roleData,roleList,apiCount,phaseEnum,enumValues,
  tokenData,text,klass,data,list,pairs,mark:()=>f.context.rpc.exports.mark(),
  stamp(when={frame:0,game_time:0}){adapter.stampClock(when);return when;}};
}

module.exports={world};
if(require.main===module){
const w=world();
let mark=w.mark(),packet=mark.native_autochess_context;
assert.equal(packet.status,'available');assert.equal(packet.verified_same_frame,true);
assert.equal(packet.phase,4);assert.equal(packet.round,1);assert.equal(packet.self_player_index,4);
assert.equal(packet.players.length,4);assert.equal(packet.memberships.length,4);
assert.deepEqual(Array.from(packet.memberships,x=>[x.cid,x.data_uid,x.player_index,x.player_side]),
 [['char_shared',101,1,1],['char_shared',102,2,2],['char_shared',103,3,3],['char_shared',104,4,4]]);
assert.equal(w.apiCalls.length,w.apiCount,'snapshot reads must not invoke game or metadata-initialization methods');
let before=w.stamp(),scope=before.native_autochess_scope.scope_key;
assert.equal(before.native_autochess_scope.status,'available');
assert.equal(before.native_autochess_scope.scope_key,packet.scope_key);
w.write(w.machine.add(0x5c),5);
let after=w.stamp();assert.notEqual(after.native_autochess_scope.scope_key,scope);
assert.equal(before.native_autochess_scope.phase,4,'a delayed/coalesced tail retains its old stamp');
assert.equal(after.native_autochess_scope.phase,5);
assert.equal(w.mark().native_generation,mark.native_generation,'phase changes do not modify native generation');
const role3=w.roles[2][1].add(0x10).readPointer().add(0x20).readPointer();
w.write(role3.add(0x40),1);packet=w.mark().native_autochess_context;
assert.equal(packet.memberships.find(x=>x.player_index===3).player_side,1,'HELP index3 is not side3');
scope=packet.scope_key;
w.write(w.players[2][1].add(0x18),w.text('replacement-account'));
assert.equal(w.stamp().native_autochess_scope.status,'unknown','unverified owner changes cannot reuse old cache');
packet=w.mark().native_autochess_context;assert.equal(packet.status,'available');assert.notEqual(packet.scope_key,scope);
assert.equal(packet.players.find(x=>x.index===3).uid,'replacement-account');

const conflict=world({conflictingUid:true});packet=conflict.mark().native_autochess_context;
assert.equal(packet.status,'available');assert.equal(packet.memberships.some(x=>x.data_uid===101),false);
assert(packet.unknown_memberships.some(x=>x.data_uid===101));
const badPlayers=world({duplicatePlayer:true});packet=badPlayers.mark().native_autochess_context;
assert.equal(packet.status,'unknown');assert.equal(badPlayers.stamp().native_autochess_scope.status,'unknown');
const knownToken=world({token:true});packet=knownToken.mark().native_autochess_context;
assert.equal(packet.status,'available');assert.equal(packet.memberships.length,5);
assert.deepEqual(JSON.parse(JSON.stringify(packet.memberships.find(x=>x.data_uid===999))),
 {cid:'token_shared',data_uid:999,is_token:true,is_predefined:false,host_key:'char_shared',host_uid:101,player_index:1,player_side:1});
const lostToken=world({unknownToken:true});packet=lostToken.mark().native_autochess_context;
assert.equal(packet.status,'available');assert.equal(packet.memberships.length,4);
assert(packet.unknown_memberships.some(x=>x.data_uid===999));
const forty=world({rolesPerPlayer:10});packet=forty.mark().native_autochess_context;
assert.equal(packet.status,'available');assert.equal(packet.memberships.length,40);
assert.equal(new Set(packet.memberships.map(x=>x.data_uid)).size,40);
assert.equal(packet.unknown_memberships.length,0,'all forty roles come from one complete mark');
const badMember=world();badMember.write(badMember.roleData[0],badMember.players[0][1].readPointer());
packet=badMember.mark().native_autochess_context;
assert.equal(packet.status,'available');assert.equal(packet.memberships.length,3);
assert.equal(packet.unknown_memberships.length,1,'one invalid class does not discard other owners');
const changedOwnerSlot=world();changedOwnerSlot.mark();
const ownerItems=changedOwnerSlot.playerList.add(0x10).readPointer();
changedOwnerSlot.write(ownerItems.add(0x28),changedOwnerSlot.players[1][1]);
assert.equal(changedOwnerSlot.stamp().native_autochess_scope.status,'unknown',
 'even an array rebind without version change invalidates the owner cache');
const normalAgain=world();normalAgain.mark();
normalAgain.write(normalAgain.level.add(0x18),normalAgain.managedString('Obt/Main/level_main_01-07'));
assert.equal(normalAgain.stamp().native_autochess_scope,undefined,'an ordinary stage cannot inherit AutoChess phase');

const changed=world();
const proto=changed.ptr(0).constructor.prototype,original=proto.readS32;let reads=0;
proto.readS32=function(){
 if(this.equals(changed.roleList.add(0x1c))&&++reads===2)changed.write(this,2);
 return original.call(this);
};
packet=changed.mark().native_autochess_context;proto.readS32=original;
assert.equal(packet.status,'unknown','container mutation must not publish a complete context');

const rootChange=world();rootChange.mark();rootChange.write(rootChange.controller.add(0x160),rootChange.ptr(0));
assert.equal(rootChange.stamp().native_autochess_scope.status,'unknown');
const clockChange=world();clockChange.mark();clockChange.clock(30,4294967296n);
assert.equal(clockChange.stamp({frame:0,game_time:0}).native_autochess_scope.status,'unknown');
const lateRoot=world();lateRoot.mark();
const lateProto=lateRoot.ptr(0).constructor.prototype,lateRead=lateProto.readPointer;let guardReads=0;
lateProto.readPointer=function(){
 if(this.equals(lateRoot.controller.add(0x2e8))&&++guardReads===2)
  lateRoot.write(lateRoot.controller.add(0x160),lateRoot.ptr(0));
 return lateRead.call(this);
};
const lateStamp=lateRoot.stamp();lateProto.readPointer=lateRead;
assert.equal(guardReads,2);assert.equal(lateStamp.native_autochess_scope.status,'unknown',
 'the five-root closing check must follow the final per-field guard reads');
const crossing=world(),crossProto=crossing.ptr(0).constructor.prototype,crossRead=crossProto.readU32;
let crossed=false;
crossProto.readU32=function(){
 if(!crossed&&this.equals(crossing.roleData[3].add(0x6c))){crossed=true;crossing.clock(1,143165576n);}
 return crossRead.call(this);
};
const rejected=crossing.mark();crossProto.readU32=crossRead;
assert.equal(rejected.retry,true);assert.equal(rejected.native_autochess_context,undefined);

// Read the real retained queue at a late, empty baseline range. Metadata can
// describe a discarded start; it never inserts a skill event or a window.
function emitSkill(f,uid,kind='skill_start',method='Character.OnSkillStart',evidence={}){
 f.context.skillFixture={uid,kind,method,evidence};
 vm.runInContext("record('char_shared',skillFixture.kind,1,time(),{method:skillFixture.method},skillFixture.evidence,skillFixture.uid)",f.context);
}
const baseline=world();baseline.mark();
baseline.write(baseline.machine.add(0x5c),5);baseline.clock(30,4294967296n);
emitSkill(baseline,104); // Confirmed other phase is not a current uncertainty.
baseline.write(baseline.machine.add(0x5c),4);baseline.clock(60,8589934592n);
emitSkill(baseline,101);emitSkill(baseline,101); // CID+UID+scope deduplication.
emitSkill(baseline,103,'skill_end','Character.OnSkillFinish');
emitSkill(baseline,104,'skill_start','BasicSkill.OnCastFinish',{skill_wait_for_end:false});
vm.runInContext('nativeAutoCache=null',baseline.context);
emitSkill(baseline,102); // Unknown scope retains only the original candidate UID.
baseline.clock(300,42949672960n);
const baselineMark=baseline.mark(),baselineThrough=baselineMark.sequence;
const prefix=baseline.context.rpc.exports.readEvents(baselineThrough,baselineThrough);
assert.equal(prefix.events.length,0,'a baseline must keep the original empty event slice');
assert.equal(prefix.baseline_discarded_skill_events_complete,true);
assert.deepEqual(Array.from(prefix.baseline_discarded_skill_events,x=>x.source_character_uid),[101,102]);
assert(prefix.baseline_discarded_skill_events.every(x=>!('amount'in x)&&!('kind'in x)));
assert.equal(prefix.baseline_discarded_skill_events[0].frame,60);
assert.equal(prefix.baseline_discarded_skill_events[0].game_time,2);
assert.equal(prefix.baseline_discarded_skill_events[0].native_autochess_scope.status,'available');
assert.equal(prefix.baseline_discarded_skill_events[1].native_autochess_scope.status,'unknown');
assert.equal(baseline.mark().sequence,baselineThrough);
assert.equal(baseline.context.rpc.exports.ackEvents(baselineThrough).acknowledged,baselineThrough);
const acknowledged=baseline.context.rpc.exports.readEvents(baselineThrough,baselineThrough);
assert.equal(acknowledged.events.length,0);assert.equal(acknowledged.baseline_discarded_skill_events.length,0);
assert.equal(acknowledged.baseline_discarded_skill_events_complete,true);

// An earlier epoch under the same five roots/phase/round is not proof of a
// foreign actor. A clock reset cannot certify a complete non-skill baseline.
const rollover=world();rollover.mark();
rollover.clock(5400,180n*4294967296n);
emitSkill(rollover,101,'skill_start','Character.SwitchToSkillState');
const beforeReset=vm.runInContext('queue[0]',rollover.context);
rollover.clock(0,0n);
const afterReset=rollover.mark();
assert.notEqual(beforeReset.native_autochess_scope.scope_key,afterReset.native_autochess_context.scope_key);
const resetPrefix=rollover.context.rpc.exports.readEvents(afterReset.sequence,afterReset.sequence);
assert.equal(resetPrefix.events.length,0);
assert.equal(resetPrefix.baseline_discarded_skill_events_complete,true);
assert.equal(resetPrefix.baseline_discarded_skill_events.length,1,'same-roots old-epoch start remains actor-local uncertainty');
const retainedStart=resetPrefix.baseline_discarded_skill_events[0];
assert.equal(retainedStart.source_character_uid,101);
assert.equal(retainedStart.frame,5400);assert.equal(retainedStart.game_time,180);
assert.equal(retainedStart.native_autochess_scope.scope_key,beforeReset.native_autochess_scope.scope_key);
assert.equal(retainedStart.proof.method,'Character.SwitchToSkillState');
assert.equal('amount'in retainedStart,false);assert.equal('kind'in retainedStart,false);
assert.equal(rollover.mark().sequence,afterReset.sequence);

const bounded=world();bounded.mark();
for(let i=0;i<513;i++)emitSkill(bounded,10000+i);
const boundedMark=bounded.mark(),truncated=bounded.context.rpc.exports.readEvents(boundedMark.sequence,boundedMark.sequence);
assert.equal(truncated.events.length,0);assert.equal(truncated.baseline_discarded_skill_events.length,512);
assert.equal(truncated.baseline_discarded_skill_events_complete,false);
assert.equal(bounded.mark().sequence,513);

// The adapter's baseline method itself must be pure JS queue work: even a
// native pointer read or a protocol cursor mutation would fail this check.
const scalarState=vm.runInContext('({sequence,acknowledgedSequence,readThroughSequence})',bounded.context);
const queue=vm.runInContext('queue',bounded.context),pointerProto=bounded.ptr(0).constructor.prototype;
const pointerMethods=['readByteArray','readPointer','readCString','readUtf16String','readU8','readU16','readU32','readS32','readFloat','readS64','readU64'];
const pointerReads=pointerMethods.map(name=>[name,pointerProto[name]]);
for(const name of pointerMethods)pointerProto[name]=function(){throw new Error('baseline attempted a native read');};
let pure;
try{pure=bounded.adapter.baselineEnvelope(boundedMark,queue,0,513);}
finally{for(const [name,method]of pointerReads)pointerProto[name]=method;}
assert.equal(pure.baseline_discarded_skill_events.length,512);
assert.deepEqual(vm.runInContext('({sequence,acknowledgedSequence,readThroughSequence})',bounded.context),scalarState);
assert.deepEqual(Object.keys(bounded.adapter.baselineEnvelope({},queue,0,513)),[]);
const waitFalseStart={...queue[0],sequence:1,skill_wait_for_end:false};
const waitFalse=bounded.adapter.baselineEnvelope(boundedMark,[waitFalseStart],0,1);
assert.equal(waitFalse.baseline_discarded_skill_events.length,1,'an explicit Character start is not a passive BasicSkill cast');
const unknownCurrent={...boundedMark,native_frame:1};
const foreignCandidate={...queue[0],sequence:1,native_autochess_scope:{...queue[0].native_autochess_scope,phase:5}};
assert.equal(bounded.adapter.baselineEnvelope(unknownCurrent,[foreignCandidate],0,1).baseline_discarded_skill_events.length,1,
 'an inconsistent current envelope cannot certify a foreign-scope discard');
const originalScope=queue[0].native_autochess_scope,scopeParts=originalScope.scope_key.split(':');
const otherRoots=[...scopeParts];otherRoots[0]='0x'+(BigInt(otherRoots[0])+8n).toString(16);
for(const changedScope of [
 {...originalScope,phase:5},
 {...originalScope,round:originalScope.round+1},
 {...originalScope,scope_key:otherRoots.join(':')},
]){
 const foreign={...queue[0],sequence:1,native_autochess_scope:changedScope};
 const summary=bounded.adapter.baselineEnvelope(boundedMark,[foreign],0,1);
 assert.equal(summary.baseline_discarded_skill_events.length,0,'verified other roots/phase/round stays excluded');
 assert.equal(summary.baseline_discarded_skill_events_complete,true);
}
const otherEpoch=[...scopeParts];otherEpoch[6]=String(Number(otherEpoch[6])+1);
const epochRecords=[{...queue[0],sequence:1},
 {...queue[0],sequence:2,native_autochess_scope:{...originalScope,scope_key:otherEpoch.join(':')}}];
const epochSummary=bounded.adapter.baselineEnvelope(boundedMark,epochRecords,0,2);
assert.equal(epochSummary.baseline_discarded_skill_events.length,2,'evidence deduplication preserves distinct original epochs');
assert.equal(epochSummary.baseline_discarded_skill_events_complete,true);

for(const platform of ['native_pc','android']){
 const ordinary=fixture({platform,sourcePath:platform==='native_pc'?sourcePath:path.join(process.env.ARKDPS_PROBE_FIXTURE_ROOT ?? path.join(__dirname,'..'),'event_probe_android2791.js')});
 emitSkill(ordinary,101);const m=ordinary.context.rpc.exports.mark();
 const empty=ordinary.context.rpc.exports.readEvents(m.sequence,m.sequence);
 assert.equal(empty.events.length,0);
 assert.equal('baseline_discarded_skill_events'in empty,false,'ordinary native/MuMu contracts remain unchanged');
}
console.log(JSON.stringify({autochess_context:true,same_cid_four_players:true,help_index_not_side:true,
 native_generation_preserved:true,token_host_verified:true,conflicting_uid_isolated:true,
 players_fingerprint_scope:true,delayed_stamp_immutable:true,container_guard:true,root_clock_guard:true,
 forty_roles_single_envelope:true,invalid_member_isolated:true,owner_slot_cache_guard:true,
 ordinary_stage_inheritance_blocked:true,full_snapshot_clock_guard:true,late_closing_root_guard:true,
 late_baseline_start_evidence:true,baseline_events_still_empty:true,baseline_ack_scope:true,
 baseline_truncation_honest:true,baseline_no_native_reads:true,mumu_baseline_unchanged:true,
 old_epoch_start_uncertainty_retained:true,foreign_scope_start_excluded:true,epoch_evidence_deduplication:true}));
}
