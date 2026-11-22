// Shared typed AutoChess observation; platform layouts are verified by each adapter.
// AutoChess identities come from the scene's own index/UID tables. These are
// managed metadata reads only; no game getter, class initializer or new hook.
const nativeAutoLayouts=new Map();
let nativeAutoStage=null,nativeAutoCache=null,nativeAutoEpoch=0,nativeAutoScope=null;
let nativeAutoBindingDiagnostic=null;
let nativeAutoDesiredMode=globalThis.arkdpsAutoChessMode===undefined?'all':globalThis.arkdpsAutoChessMode;
if(!['all','local'].includes(nativeAutoDesiredMode))throw new Error('invalid AutoChess collection mode');
let nativeAutoEffective=null,nativeAutoOwners=null;
function setAutoChessMode(enabled){
 if(typeof enabled!=='boolean')throw new Error('autochess_enabled must be boolean');
 nativeAutoDesiredMode=enabled?'all':'local';
 return {desired_mode:nativeAutoDesiredMode,effective_mode:nativeAutoEffective?.mode??null};
}
function autoCollectionMode(scope_key,phase){
 // Only a proven combat scope can latch a preference. Terminal observations
 // retain the completed combat's effective mode until another combat starts.
 if([4,5,6].includes(phase)&&nativeAutoEffective?.scope_key!==scope_key)
  nativeAutoEffective={scope_key,mode:nativeAutoDesiredMode};
 return nativeAutoEffective?.mode??nativeAutoDesiredMode;
}
function autoEventOwner(event){
 const scope=event.native_autochess_scope,owners=nativeAutoOwners;
 if(!nativeAutoCache||!owners||owners.capture!==nativeAutoCache||scope?.status!=='available'||scope.verified_same_frame!==true||
    scope.collection_mode!=='local'||![4,5,6].includes(scope.phase)||scope.scope_key!==owners.scope_key||
    scope.phase!==owners.phase||scope.round!==owners.round||scope.frame!==event.frame||
    scope.game_time!==event.game_time||event.frame!==owners.frame||event.game_time!==owners.game_time||
    typeof event.source!=='string'||!Number.isInteger(event.source_character_uid)||
    event.source_character_uid<=0||event.source_character_uid>0xffffffff)return 'unknown';
 const member=owners.byUid.get(event.source_character_uid);
 if(!member||member.cid!==event.source)return 'unknown';
 return member.player_index===owners.self?'local':'foreign';
}
function autoCollectionOwners(result,scope_key,mode,frame,game_time){
 if(mode!=='local'){nativeAutoOwners=null;return;}
 if(nativeAutoOwners?.body===result&&nativeAutoOwners.scope_key===scope_key){
  nativeAutoOwners.capture=nativeAutoCache;nativeAutoOwners.frame=frame;nativeAutoOwners.game_time=game_time;return;
 }
 const {players,memberships,unknown,header}=result;
 const validUid=uid=>typeof uid==='string'&&/^[0-9]{1,20}$/.test(uid)&&/[1-9]/.test(uid)&&
  (uid.length<20||uid<='18446744073709551615');
 if(!players.every(player=>validUid(player.uid))){nativeAutoOwners=null;return;}
 const excluded=new Set(unknown.map(row=>row.data_uid)),byUid=new Map();
 for(const row of memberships){
  if(excluded.has(row.data_uid)||!Number.isInteger(row.data_uid)||row.data_uid<=0||row.data_uid>0xffffffff||
     typeof row.cid!=='string'||!/^[A-Za-z0-9_#.-]{1,128}$/.test(row.cid)||
     !players.some(player=>player.index===row.player_index))continue;
  if(byUid.has(row.data_uid)){byUid.delete(row.data_uid);excluded.add(row.data_uid);}
  else byUid.set(row.data_uid,row);
 }
 for(const [uid,row]of byUid){
  if(row.is_token){
   const host=byUid.get(row.host_uid);
   if(!host||host.is_token||host.cid!==row.host_key||host.player_index!==row.player_index||
      host.player_side!==row.player_side)byUid.delete(uid);
  }
 }
 nativeAutoOwners={body:result,capture:nativeAutoCache,frame,game_time,scope_key,
  phase:header.phase,round:header.round,self:header.self,byUid};
}
const AUTO_NS='Torappu.Battle.AutoChess';
const autoPhaseEnums=new Set();
function verifyAutoChessPhaseEnum(field){
 const classFrom=api('il2cpp_class_from_type','pointer',['pointer']);
 const k=classFrom(field.type),key=k.toString();
 if(autoPhaseEnums.has(key))return;
 if(!autoIdentity(k,'AutoChessGameStateType','Torappu')||
    !autoIdentity(k.add(0x58).readPointer(),'Enum','System'))throw new Error('AutoChess phase enum type');
 const table=k.add(0x80).readPointer(),count=k.add(0x124).readU16();
 if(table.isNull()||count<2||count>128)throw new Error('AutoChess phase enum fields');
 const expected={NONE:0,LOADING:1,SP_PREPARE:2,PREPARE:3,BATTLE:4,HELP_BATTLE:5,
  BOSS_BATTLE:6,SETTLE:7,END:8,PAUSE:9,BATTLE_WAITING:100,BOSS_PREPARE_WAITING:101,
  PREPARE_RESTART:102,SP_PREPARE_RESTART:103,BOSS_BATTLE_RESTART:104};
 const found=new Set(),literals=[];let underlying=false;
 for(let i=0;i<count;i++){
  const f=table.add(i*32),type=f.add(8).readPointer(),name=f.readPointer().readCString();
  if(!f.add(16).readPointer().equals(k)||type.isNull())throw new Error('AutoChess phase enum owner');
  const bits=type.add(8).readU32(),kind=(bits>>>16)&255;
  if(name==='value__'){
   if(underlying||kind!==8||bits&16)throw new Error('AutoChess phase enum underlying type');
   underlying=true;continue;
  }
  if(!(name in expected))continue;
  // Literal values come from IL2CPP default metadata, never an initialized
  // gameplay instance/static field. Non-literals never reach this API.
  if(found.has(name)||(bits&0x50)!==0x50||kind!==17||!classFrom(type).equals(k))
   throw new Error('AutoChess phase enum literal');
  found.add(name);literals.push([f,name]);
 }
 if(!underlying||found.size!==Object.keys(expected).length)throw new Error('AutoChess phase enum names');
 const literalValue=api('il2cpp_field_static_get_value','void',['pointer','pointer']),out=Memory.alloc(4);
 for(const [f,name]of literals){literalValue(f,out);if(out.readS32()!==expected[name])throw new Error('AutoChess phase enum value '+name);}
 autoPhaseEnums.add(key);
}
function autoIdentity(k,name,namespace){
 return !k.isNull()&&className(k)===name&&k.add(0x18).readPointer().readCString()===namespace;
}
function autoLayout(k,name,namespace,count,specs){
 if(!autoIdentity(k,name,namespace))throw new Error('AutoChess class '+name);
 const cacheKey=k.toString()+':'+JSON.stringify(specs);
 if(nativeAutoLayouts.has(cacheKey))return nativeAutoLayouts.get(cacheKey);
 const actualCount=k.add(0x124).readU16(),reflected=probeAdapter.autoChessReflected===true;
 if(reflected?(actualCount<1||actualCount>1000):actualCount!==count)throw new Error('AutoChess field count '+name);
 const table=k.add(0x80).readPointer(),out={};
 if(table.isNull())throw new Error('AutoChess field metadata unavailable '+name);
 for(const [index,field,offset,kinds] of specs){
  let f=table.add(index*32);
  if(reflected){
   const matches=[];
   for(let i=0;i<actualCount;i++){
    const candidate=table.add(i*32);
    if(candidate.readPointer().readCString()===field)matches.push(candidate);
   }
   if(matches.length!==1)throw new Error('AutoChess field missing or ambiguous '+name+'.'+field);
   f=matches[0];
  }
  const t=f.add(8).readPointer(),actualOffset=f.add(24).readS32();
  if((!reflected&&index>=count)||!f.add(16).readPointer().equals(k)||f.readPointer().readCString()!==field
     ||(!reflected&&actualOffset!==offset)||actualOffset<0x10||actualOffset>0x2000||t.isNull())
   throw new Error('AutoChess field '+name+'.'+field);
  // These inline value layouts establish the array stride. A different
  // Android value layout is unknown, never traversed with a PC stride.
  if(reflected&&['Entry','KeyValuePair`2'].includes(name)&&actualOffset!==offset)
   throw new Error('AutoChess inline value layout '+name+'.'+field);
  const bits=t.add(8).readU32(),kind=(bits>>>16)&255;
  if(bits&16||!kinds.includes(kind))throw new Error('AutoChess field type '+name+'.'+field);
  out[field]={data:t.readPointer(),kind,offset:actualOffset,type:t};
 }
 nativeAutoLayouts.set(cacheKey,out);return out;
}
function scopePointerNumber(pointer){
 const value=Number(BigInt(pointer.toString()));
 if(!Number.isSafeInteger(value)||value<0||value>0x7fffffffffff)throw new Error('AutoChess cached pointer range');
 return value;
}
function scopeBlock(address,size,fields){
 const bytes=address.readByteArray(size);
 if(bytes===null||bytes.byteLength!==size)throw new Error('AutoChess block read unavailable');
 const view=new DataView(bytes);
 for(const [kind,offset,expected]of fields){
  if(kind==='p'){
   if(view.getUint32(offset,true)!==expected%4294967296||
      view.getUint32(offset+4,true)!==Math.floor(expected/4294967296))
    throw new Error('AutoChess scope block pointer changed');
  }else if(view.getInt32(offset,true)!==expected)throw new Error('AutoChess scope block integer changed');
 }
}
function scopeBlocks(cached){
 if(probeAdapter.autoChessReflected)return null;
 const p=(offset,value)=>['p',offset,scopePointerNumber(value)],i=(offset,value)=>['i',offset,value];
 return {
  manager:[cached.manager.add(0x38),16,[p(0,cached.status),p(8,cached.center)]],
  dict:[cached.dict.add(0x18),24,[p(0,cached.dictEntries),i(8,cached.dictCount),i(16,cached.dictFree),i(20,cached.dictVersion)]],
  list:[cached.playerList.add(0x10),16,[p(0,cached.playerItems),i(8,cached.ownerRefs.length),i(12,cached.playerVersion)]],
  owners:cached.ownerRefs.map(owner=>[
   [owner.slot,16,[i(0,owner.index),p(8,owner.value)]],
   [owner.value.add(0x10),16,[i(0,owner.index),p(8,owner.uidRef)]]])
 };
}
function autoRead(){
 const guards=[],layouts=new Map();
 function value(kind,address){
  let result;
  if(kind==='p')result=address.readPointer();
  else if(kind==='i')result=address.readS32();
  else if(kind==='u')result=address.readU32();
  else if(kind==='b')result=address.readU8();
  else result=address.readU64().toNumber();
  guards.push([kind,address,result]);return result;
 }
 const read={guards,p:a=>value('p',a),i:a=>value('i',a),u:a=>value('u',a),
  b:a=>value('b',a),n:a=>value('n',a)};
 read.layout=(address,layout)=>{
  const key=address.toString();layouts.set(key,{...layouts.get(key),...layout});return layout;
 };
 read.at=(address,field)=>{
  const layout=layouts.get(address.toString());
  if(!layout||!layout[field])throw new Error('AutoChess unverified field address '+field);
  return address.add(layout[field].offset);
 };
 read.object=(address,name,namespace,count,specs)=>{
  if(!address||address.isNull())throw new Error('AutoChess missing '+name);
  return read.layout(address,autoLayout(read.p(address),name,namespace,count,specs));
 };
 read.text=address=>{
  if(address.isNull())return null;
  if(!autoIdentity(read.p(address),'String','System'))throw new Error('AutoChess string class');
  const count=read.i(address.add(0x10));
  if(count<0||count>128)throw new Error('AutoChess string length');
  const result=address.add(0x14).readUtf16String(count);
  if(typeof result!=='string'||/[\u0000-\u001f\u007f]/.test(result))throw new Error('AutoChess string value');
  guards.push(['text',address,result]);return result;
 };
 read.block=(address,size,fields)=>{scopeBlock(address,size,fields);guards.push(['block',address,{size,fields}]);};
 read.validate=()=>{
  for(const [kind,address,before] of guards){
   if(kind==='block'){scopeBlock(address,before.size,before.fields);continue;}
   const after=kind==='p'?address.readPointer():kind==='i'?address.readS32():
    kind==='u'?address.readU32():kind==='b'?address.readU8():kind==='n'?address.readU64().toNumber():
    address.add(0x14).readUtf16String(address.add(0x10).readS32());
   if(kind==='p'?!after.equals(before):after!==before)throw new Error('AutoChess changed during read');
  }
 };
 return read;
}
function autoGeneric(data,name,namespace,read){
 if(data.isNull())throw new Error('AutoChess generic metadata');
 const k=read.p(data.add(24));
 if(!autoIdentity(k,name,namespace))throw new Error('AutoChess generic class '+name);
 return k;
}
function autoList(address,maximum,read,listdict=false){
 let k=read.p(address);
 if(listdict){
  if(!autoIdentity(k,'ListDict`2','Torappu')||!probeAdapter.autoChessReflected&&k.add(0x124).readU16()!==1)
   throw new Error('AutoChess ListDict class');
  k=read.p(k.add(0x58));
 }
 const typed=autoLayout(k,'List`1','System.Collections.Generic',6,
  [[1,'_items',0x10,[29]],[2,'_size',0x18,[8]],[3,'_version',0x1c,[8]]]);
 read.layout(address,typed);
 const items=read.p(read.at(address,'_items')),count=read.i(read.at(address,'_size'));read.i(read.at(address,'_version'));
 if(count<0||count>maximum)throw new Error('AutoChess list bound');
 if(items.isNull()){if(count)throw new Error('AutoChess list missing array');}
 else{const capacity=read.n(items.add(0x18));if(capacity<count||capacity>4096)throw new Error('AutoChess array bound');}
 return {items,count,typed};
}
function autoPairs(address,maximum,read,kinds){
 const list=autoList(address,maximum,read,true),element=list.typed._items.data;
 if(element.isNull()||((element.add(8).readU32()>>>16)&255)!==21)throw new Error('AutoChess pair metadata');
 const pair=autoGeneric(element.readPointer(),'KeyValuePair`2','System.Collections.Generic',read);
 autoLayout(pair,'KeyValuePair`2','System.Collections.Generic',2,
  [[0,'key',0x10,[8]],[1,'value',0x18,kinds]]);
 if(!list.items.isNull()&&!autoIdentity(read.p(list.items),'KeyValuePair`2[]','System.Collections.Generic'))
  throw new Error('AutoChess pair array class');
 const result=[],seen=new Set();
 for(let i=0;i<list.count;i++){
  const slot=list.items.add(0x20+i*16),index=read.i(slot),value=read.p(slot.add(8));
  if(seen.has(index)||value.isNull())throw new Error('AutoChess pair key/value');
  seen.add(index);result.push([index,value]);
 }
 return result;
}
function autoReferences(address,maximum,name,namespace,read){
 const list=autoList(address,maximum,read);
 if(!list.items.isNull()&&!autoIdentity(read.p(list.items),name+'[]',namespace))throw new Error('AutoChess reference array');
 const result=[],seen=new Set();
 for(let i=0;i<list.count;i++){
  const value=read.p(list.items.add(0x20+i*8)),key=value.toString();
  if(value.isNull()||seen.has(key))throw new Error('AutoChess reference slot');
  seen.add(key);result.push(value);
 }
 return result;
}
function autoHeader(identity,read){
 const controller=ptr(identity.controller);
 read.object(controller,'BattleController','Torappu.Battle',515,[[110,'m_singletonHost',0x2e8,[18]]]);
 const host=read.p(read.at(controller,'m_singletonHost'));
 read.object(host,'SingletonHost','',1,[[0,'m_instStore',0x10,[21]]]);
 const dict=read.p(read.at(host,'m_instStore'));
 const typed=read.object(dict,'Dictionary`2','System.Collections.Generic',14,
  [[1,'_entries',0x18,[29]],[2,'_count',0x20,[8]],[4,'_freeCount',0x28,[8]],[5,'_version',0x2c,[8]]]);
 const element=typed._entries.data;
 if(element.isNull()||((element.add(8).readU32()>>>16)&255)!==21)throw new Error('AutoChess dictionary metadata');
 const entry=autoGeneric(element.readPointer(),'Entry','',read);
 autoLayout(entry,'Entry','',4,[[0,'hashCode',0x10,[8]],[1,'next',0x14,[8]],[2,'key',0x18,[18]],[3,'value',0x20,[28,18]]]);
 const entries=read.p(read.at(dict,'_entries')),count=read.i(read.at(dict,'_count')),free=read.i(read.at(dict,'_freeCount'));
 const dictVersion=read.i(read.at(dict,'_version'));
 if(count<0||count>32||free<0||free>count||entries.isNull())throw new Error('AutoChess host dictionary bound');
 if(!autoIdentity(read.p(entries),'Entry[]',''))throw new Error('AutoChess host dictionary array');
 const capacity=read.n(entries.add(0x18));if(capacity<count||capacity>128)throw new Error('AutoChess host capacity');
 const managers=[],keys=new Set();let active=0;
 for(let i=0;i<count;i++){
  const slot=entries.add(0x20+i*24),hash=read.i(slot);if(hash<0)continue;
  active++;const key=read.p(slot.add(8)),value=read.p(slot.add(16));
  if(key.isNull()||keys.has(key.toString()))throw new Error('AutoChess host dictionary key');keys.add(key.toString());
  if(!value.isNull()&&autoIdentity(read.p(value),'AutoChessGameManager',AUTO_NS))managers.push({value,slot:slot.add(16)});
 }
 if(active!==count-free||managers.length!==1)throw new Error('AutoChess manager not unique');
 const manager=managers[0].value,managerSlot=managers[0].slot;
 read.object(manager,'AutoChessGameManager',AUTO_NS,152,
  [[12,'gameStatus',0x38,[18]],[13,'dataCenter',0x40,[18]]]);
 const status=read.p(read.at(manager,'gameStatus')),center=read.p(read.at(manager,'dataCenter'));
 read.object(status,'AutoChessGameStatus',AUTO_NS,62,[[0,'m_stateModel',0x10,[18]]]);
 const model=read.p(read.at(status,'m_stateModel'));
 read.object(model,'AutoChessGameStateModel','',13,[[0,'m_stateMachine',0x18,[18]]]);
 const machine=read.p(read.at(model,'m_stateMachine'));
 const machineFields=read.object(machine,'AutoChessGameStateMachine','',13,[[1,'<gameCurrentStateId>k__BackingField',0x5c,[17]]]);
 if(probeAdapter.autoChessReflected)verifyAutoChessPhaseEnum(machineFields['<gameCurrentStateId>k__BackingField']);
 read.object(center,'AutoChessDataCenter','Torappu.Battle.DataCenter',151,
  [[5,'<selfPlayerIndex>k__BackingField',0x38,[8]],[6,'<currentRound>k__BackingField',0x3c,[8]],
   [28,'battleData',0xe8,[18]],[30,'playerDataModel',0xf8,[18]]]);
 const phase=read.i(read.at(machine,'<gameCurrentStateId>k__BackingField')),round=read.i(read.at(center,'<currentRound>k__BackingField'));
 if(![0,1,2,3,4,5,6,7,8,9,100,101,102,103,104].includes(phase)||round<0)throw new Error('AutoChess phase/round');
 return {controller,host,dict,dictEntries:entries,dictCount:count,dictFree:free,managerSlot,
  manager,status,model,machine,center,phase,round,
  self:read.i(read.at(center,'<selfPlayerIndex>k__BackingField')),dictVersion,
  addresses:{controllerHost:read.at(controller,'m_singletonHost'),hostDict:read.at(host,'m_instStore'),
   managerStatus:read.at(manager,'gameStatus'),managerCenter:read.at(manager,'dataCenter'),
   statusModel:read.at(status,'m_stateModel'),modelMachine:read.at(model,'m_stateMachine'),
   dictEntries:read.at(dict,'_entries'),dictCount:read.at(dict,'_count'),dictFree:read.at(dict,'_freeCount'),
   dictVersion:read.at(dict,'_version'),centerPlayers:read.at(center,'playerDataModel'),
   phase:read.at(machine,'<gameCurrentStateId>k__BackingField'),round:read.at(center,'<currentRound>k__BackingField'),
   self:read.at(center,'<selfPlayerIndex>k__BackingField')}};
}
function autoData(address,read){
 read.object(address,'BattleCharacterData','Torappu.Battle',36,
  [[4,'prefabKey',0x50,[14]],[8,'uniqueId',0x6c,[9]],[14,'isToken',0x90,[2]],
   [15,'isPredefined',0x91,[2]],[18,'tokenOrHostKey',0x98,[14]],[19,'tokenOrHostUniqueId',0xa0,[9]]]);
 const cid=read.text(read.p(read.at(address,'prefabKey'))),data_uid=read.u(read.at(address,'uniqueId'));
 const token=read.b(read.at(address,'isToken')),predefined=read.b(read.at(address,'isPredefined'));
 if(!cid||!data_uid||token>1||predefined>1)throw new Error('AutoChess character identity');
 const host=read.p(read.at(address,'tokenOrHostKey')),hostUid=read.u(read.at(address,'tokenOrHostUniqueId'));
 return {cid,data_uid,is_token:!!token,is_predefined:!!predefined,
  host_key:token&&!host.isNull()?read.text(host):null,host_uid:token?hostUid:null};
}
function autoScope(identity,phase,round,frame,fixed,fingerprint,headerKey){
 const roots=['controller','native','logger','stats','level'].map(k=>ptr(identity[k]).toString()).join(':');
 const previous=nativeAutoScope;
 if(previous&&(previous.roots!==roots||previous.phase!==phase||previous.round!==round||
   frame<previous.frame||BigInt(fixed)<BigInt(previous.fixed)||
   headerKey!==undefined&&previous.headerKey!==undefined&&headerKey!==previous.headerKey||
   fingerprint!==undefined&&previous.fingerprint!==undefined&&fingerprint!==previous.fingerprint))nativeAutoEpoch++;
 nativeAutoScope={roots,phase,round,frame,fixed,fingerprint:fingerprint===undefined?previous?.fingerprint:fingerprint,
  headerKey:headerKey===undefined?previous?.headerKey:headerKey};
 return roots+':autochess:'+nativeAutoEpoch;
}
function readAutoChess(identity,frame,fixed){
 const read=autoRead(),unknown=[];
 try{
  const header=autoHeader(identity,read),model=read.p(read.at(header.center,'playerDataModel'));
  read.object(model,'AutoChessPlayerDataModel',AUTO_NS,17,[[0,'m_allPlayerDatas',0x18,[21]]]);
  const playerList=read.p(read.at(model,'m_allPlayerDatas')),pairs=autoPairs(playerList,8,read,[18]);
  if(!pairs.length)throw new Error('AutoChess players unavailable');
  const playerItems=read.p(read.at(playerList,'_items')),playerVersion=read.i(read.at(playerList,'_version'));
  const players=[],indexes=new Set(),uids=new Set(),ownerRefs=[];let playerOrdinal=0;
  for(const [index,value]of pairs){
   read.object(value,'ScenePlayerData','',37,[[0,'index',0x10,[8]],[1,'uid',0x18,[14]],[6,'playerCard',0x40,[18]]]);
   const actual=read.i(read.at(value,'index')),uidRef=read.p(read.at(value,'uid')),uid=read.text(uidRef);
   if(actual!==index||index<0||indexes.has(index)||!uid||uids.has(uid))throw new Error('AutoChess player bijection');
   indexes.add(index);uids.add(uid);ownerRefs.push({value,index,uidRef,slot:playerItems.add(0x20+16*playerOrdinal++),
    indexAddress:read.at(value,'index'),uidAddress:read.at(value,'uid')});
   const card=read.p(read.at(value,'playerCard'));let nick_name=null,nick_number=null;
   if(!card.isNull()){
    read.object(card,'PlayerCard',AUTO_NS,6,[[0,'nickName',0x10,[14]],[1,'nickNumber',0x18,[14]]]);
    nick_name=read.text(read.p(read.at(card,'nickName')));nick_number=read.text(read.p(read.at(card,'nickNumber')));
   }
   players.push({index,uid,nick_name,nick_number});
  }
  if(!indexes.has(header.self))throw new Error('AutoChess self player missing');
  const battle=read.p(read.at(header.center,'battleData'));
  read.object(battle,'AutoChessBattleDataModel',AUTO_NS,53,[[1,'battleChessInsts',0x20,[21]]]);
  const groups=autoPairs(read.p(read.at(battle,'battleChessInsts')),8,read,[21]),rows=[];let total=0;
  for(const [index,list]of groups){
   if(!indexes.has(index))throw new Error('AutoChess role player missing');
   for(const value of autoReferences(list,64,'ChessInst',AUTO_NS,read)){
    if(++total>256)throw new Error('AutoChess role total bound');
    try{
     read.object(value,'ChessInst',AUTO_NS,27,
      [[6,'playerIndex',0x30,[8]],[8,'side',0x40,[17]],[10,'battleCharacterData',0x48,[18]]]);
     const player_index=read.i(read.at(value,'playerIndex')),player_side=read.i(read.at(value,'side'));
     if(player_index!==index||player_side<0||player_side>4)throw new Error('AutoChess role index/side');
     rows.push({...autoData(read.p(read.at(value,'battleCharacterData')),read),player_index,player_side});
    }catch(e){unknown.push({candidate_player_index:index,reason:String(e)});}
   }
  }
  const byUid=new Map(),conflicts=new Set();
  function add(row){
   const prior=byUid.get(row.data_uid);
   if(prior&&JSON.stringify(prior)!==JSON.stringify(row)){conflicts.add(row.data_uid);unknown.push({data_uid:row.data_uid,reason:'conflicting role UID'});}
   else if(!prior)byUid.set(row.data_uid,row);
  }
  for(const row of rows)add(row);
  // Tokens are admitted only through an unambiguous native host data UID.
  // Live collection membership does not establish activation or damage time.
  try{
   read.object(header.controller,'BattleController','Torappu.Battle',515,[[100,'<unitManager>k__BackingField',0x2c0,[18]]]);
   const manager=read.p(read.at(header.controller,'<unitManager>k__BackingField'));
   read.object(manager,'UnitManager','Torappu.Battle',7,[[1,'<characters>k__BackingField',0x18,[21]]]);
   const container=read.p(read.at(manager,'<characters>k__BackingField'));
   read.object(container,'UnorderedArray`1','Torappu',3,[[0,'m_items',0x10,[29]],[2,'<count>k__BackingField',0x20,[8]]]);
   const items=read.p(read.at(container,'m_items')),count=read.i(read.at(container,'<count>k__BackingField'));
   if(count<0||count>128||count&&items.isNull())throw new Error('AutoChess token collection bound');
   if(!items.isNull()){
    if(!autoIdentity(read.p(items),'Unit[]','Torappu.Battle'))throw new Error('AutoChess token array class');
    const capacity=read.n(items.add(0x18));if(capacity<count||capacity>4096)throw new Error('AutoChess token capacity');
   }
   for(let i=0;i<count;i++){
    const unit=read.p(items.add(0x20+i*8));
    try{
     let k=read.p(unit),character=null,bObject=null;const seen=new Set();
     for(let depth=0;depth<10&&!k.isNull();depth++){
      if(seen.has(k.toString()))throw new Error('AutoChess token lineage cycle');seen.add(k.toString());
      if(autoIdentity(k,'Character','Torappu.Battle'))character=k;
      if(autoIdentity(k,'BObject','Torappu.Battle')){bObject=k;break;}
      k=read.p(k.add(0x58));
     }
     if(!character)continue;
     read.layout(unit,autoLayout(character,'Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,[18]]]));
     const row=autoData(read.p(read.at(unit,'<data>k__BackingField')),read);
     if(!row.is_token){
      const known=byUid.get(row.data_uid);
      if(!known||conflicts.has(row.data_uid))unknown.push({cid:row.cid,data_uid:row.data_uid,reason:'live role absent or conflicting in ChessInst'});
      else if(known.cid!==row.cid){conflicts.add(row.data_uid);unknown.push({cid:row.cid,data_uid:row.data_uid,reason:'conflicting live role UID'});}
      continue;
     }
     if(!bObject)throw new Error('AutoChess token BObject missing');
     read.layout(unit,autoLayout(bObject,'BObject','Torappu.Battle',9,[[3,'<playerSide>k__BackingField',0x20,[17]],
      [4,'<inited>k__BackingField',0x24,[2]],[5,'<borned>k__BackingField',0x25,[2]],
      [6,'<isDisappeared>k__BackingField',0x26,[2]]]));
     const inited=read.b(read.at(unit,'<inited>k__BackingField')),borned=read.b(read.at(unit,'<borned>k__BackingField')),
      disappeared=read.b(read.at(unit,'<isDisappeared>k__BackingField'));
     if(inited!==1||borned!==1||disappeared!==0){unknown.push({cid:row.cid,data_uid:row.data_uid,reason:'token lifecycle unavailable or inactive'});continue;}
     const side=read.i(read.at(unit,'<playerSide>k__BackingField')),host=byUid.get(row.host_uid);
     if(!host||conflicts.has(row.host_uid)||host.is_token||host.cid!==row.host_key||side!==host.player_side){
      unknown.push({cid:row.cid,data_uid:row.data_uid,host_uid:row.host_uid,reason:'token host unavailable or conflicting'});continue;
     }
     add({...row,player_index:host.player_index,player_side:side});
    }catch(e){unknown.push({reason:String(e)});}
   }
  }catch(e){unknown.push({reason:'token collection unavailable: '+String(e)});}
  read.validate();
  const fingerprint=JSON.stringify(players.map(p=>[p.index,p.uid]).sort((a,b)=>a[0]-b[0]));
  return {read,header,model,playerList,playerItems,playerVersion,ownerRefs,fingerprint,players,
   playerAddresses:{list:read.at(model,'m_allPlayerDatas'),items:read.at(playerList,'_items'),
    count:read.at(playerList,'_size'),version:read.at(playerList,'_version')},
   memberships:[...byUid.values()].filter(row=>!conflicts.has(row.data_uid)),unknown};
 }catch(e){nativeAutoCache=null;return {error:String(e)};}
}
function finishAutoChess(result,identity,frame,fixed){
 if(result.error)return {protocol_version:1,status:'unknown',verified_same_frame:false,frame,
  game_time:Number(BigInt(fixed))/4294967296,reason:result.error};
 const {header,model,playerList,playerItems,playerVersion,ownerRefs,players,memberships,unknown,fingerprint,playerAddresses}=result;
 const headerKey=[header.manager,header.status,header.model,header.machine,header.center].map(p=>p.toString()).join(':');
 const scope_key=autoScope(identity,header.phase,header.round,frame,fixed,fingerprint,headerKey);
 const collection_mode=autoCollectionMode(scope_key,header.phase);
 nativeAutoCache={...header,playerModel:model,playerList,playerItems,playerVersion,
  ownerRefs,identity:{...identity},fingerprint,headerKey,playerAddresses};
 nativeAutoCache.blocks=scopeBlocks(nativeAutoCache);
 autoCollectionOwners(result,scope_key,collection_mode,frame,Number(BigInt(fixed))/4294967296);
 return {protocol_version:1,status:'available',verified_same_frame:true,frame,
  game_time:Number(BigInt(fixed))/4294967296,scope_key,phase:header.phase,round:header.round,
  self_player_index:header.self,players,memberships,unknown_memberships:unknown,collection_mode,
  match_key:header.manager.toString()+':'+header.center.toString()};
}
function stampAutoChessClock(when){
 if(!nativeAutoStage?.enabled)return;
 const unknown=reason=>{when.native_autochess_scope={protocol_version:1,status:'unknown',
  verified_same_frame:false,frame:when.frame,game_time:when.game_time,reason};};
 try{
  const cached=nativeAutoCache,before=currentNativeIdentity();
  if(!before)throw new Error('AutoChess scope cache unavailable');
  const stagePointer=nativeAutoStage.stageAddress.readPointer();
  if(!stagePointer.equals(nativeAutoStage.stagePointer)){
   nativeAutoCache=null;
   if(!/AUTOCHESS/i.test(str(stagePointer)||'')){nativeAutoStage.enabled=false;return;}
   throw new Error('AutoChess stage cache changed');
  }
  if(!cached||Object.keys(before).some(k=>before[k]!==cached.identity[k]))throw new Error('AutoChess scope cache unavailable');
  const frame=clock.readU32(),fixed=clock.add(4).readS64().toString();
  if(frame!==when.frame||Number(BigInt(fixed))/4294967296!==when.game_time)throw new Error('AutoChess event clock mismatch');
  const read=autoRead();
  let phase,round;
  if(probeAdapter.autoChessReflected){
   if(!read.p(ptr(before.level)).equals(nativeAutoStage.levelClass))throw new Error('AutoChess level class changed');
   if(!read.p(stagePointer).equals(nativeAutoStage.stageClass))throw new Error('AutoChess stage String class changed');
   const a=cached.addresses,p=cached.playerAddresses;
   const pointers=[[a.controllerHost,cached.host],[a.hostDict,cached.dict],[a.managerStatus,cached.status],
    [a.managerCenter,cached.center],[a.statusModel,cached.model],[a.modelMachine,cached.machine],
    [a.dictEntries,cached.dictEntries],[cached.managerSlot,cached.manager],[a.centerPlayers,cached.playerModel],
    [p.list,cached.playerList],[p.items,cached.playerItems]];
   const integers=[[a.dictCount,cached.dictCount],[a.dictFree,cached.dictFree],[a.dictVersion,cached.dictVersion],
    [p.count,cached.ownerRefs.length],[p.version,cached.playerVersion],[a.self,cached.self]];
   for(const owner of cached.ownerRefs){
    pointers.push([owner.slot.add(8),owner.value],[owner.uidAddress,owner.uidRef]);
    integers.push([owner.slot,owner.index],[owner.indexAddress,owner.index]);
   }
   for(const [address,expected]of pointers)if(!read.p(address).equals(expected))throw new Error('AutoChess reflected pointer changed');
   for(const [address,expected]of integers)if(read.i(address)!==expected)throw new Error('AutoChess reflected scalar changed');
   phase=read.i(a.phase);round=read.i(a.round);
  }else{
  for(const [address,expected]of [[cached.controller.add(0x2e8),cached.host],[cached.host.add(0x10),cached.dict]]){
   if(!read.p(address).equals(expected))throw new Error('AutoChess header cache changed');
  }
  read.block(...cached.blocks.manager);
  for(const [address,expected]of [[cached.status.add(0x10),cached.model],[cached.model.add(0x18),cached.machine]]){
   if(!read.p(address).equals(expected))throw new Error('AutoChess header cache changed');
  }
  read.block(...cached.blocks.dict);
  if(!read.p(cached.managerSlot).equals(cached.manager)||!read.p(cached.center.add(0xf8)).equals(cached.playerModel)||
   !read.p(cached.playerModel.add(0x18)).equals(cached.playerList))throw new Error('AutoChess players cache changed');
  read.block(...cached.blocks.list);
  for(const owner of cached.blocks.owners){read.block(...owner[0]);read.block(...owner[1]);}
   phase=read.i(cached.machine.add(0x5c));round=read.i(cached.center.add(0x3c));
  }
  if(![0,1,2,3,4,5,6,7,8,9,100,101,102,103,104].includes(phase)||round<0)throw new Error('AutoChess event phase/round');
  read.validate();const after=currentNativeIdentity();
  if(!after||Object.keys(before).some(k=>before[k]!==after[k])||clock.readU32()!==frame||clock.add(4).readS64().toString()!==fixed)
   throw new Error('AutoChess event scope changed');
  const scope_key=autoScope(before,phase,round,frame,fixed,cached.fingerprint,cached.headerKey);
  when.native_autochess_scope={protocol_version:1,status:'available',verified_same_frame:true,
   frame,game_time:when.game_time,scope_key,phase,round,collection_mode:autoCollectionMode(scope_key,phase)};
 }catch(e){nativeAutoCache=null;unknown(String(e));}
}
let nativeAutoBodyCache=null;
function readAutoChessBody(identity,frame,fixed,key){
 const cached=nativeAutoBodyCache;
 if(cached&&cached.key===key&&cached.generation===nativeGeneration&&cached.epoch===nativeAutoEpoch
    &&frame>=cached.frame&&BigInt(fixed)>=BigInt(cached.fixed)){
  try{cached.body.read.validate();}
  catch(e){nativeAutoBodyCache=null;return readAutoChess(identity,frame,fixed);}
  try{cached.body.read.validate();}
  catch(e){nativeAutoCache=null;nativeAutoBodyCache=null;return {error:String(e)};}
  return cached.body;
 }
 nativeAutoBodyCache=null;return readAutoChess(identity,frame,fixed);
}
const nativeRootFailures={
 controller_class_unavailable:'BattleController class unavailable',
 singleton_parent_missing:'BattleController singleton parent missing',
 singleton_statics_missing:'BattleController singleton statics missing',
 singleton_empty:'BattleController singleton slot empty',
 controller_class_mismatch:'BattleController object class does not match probe metadata',
 native_missing:'BattleController native object missing',
 native_owner_mismatch:'BattleController native object does not point back to the singleton',
 logger_missing:'BattleController logger missing',
 logger_class_mismatch:'Logger object class differs from BattleLogger',
 logger_owner_mismatch:'BattleLogger does not point back to the singleton',
 level_missing:'BattleController level data missing',
 level_class_mismatch:'Level object class differs from LevelData',
 stats_missing:'BattleLogger statistics object missing',
 stats_class_mismatch:'Statistics object class differs from BattleStats'
};
function currentNativeIdentity(reject){
 if(!nativeControllerClass)return reject?.('controller_class_unavailable')??null;
 const parent=nativeControllerClass.add(0x58).readPointer();
 if(parent.isNull())return reject?.('singleton_parent_missing')??null;
 const statics=parent.add(0xb8).readPointer();
 if(statics.isNull())return reject?.('singleton_statics_missing')??null;
 const controller=statics.readPointer();
 return terminalIdentity(controller,nativeControllerClass,reject);
}
function readAutoChessViewMode(manager){
 // This optional witness describes which battle objects the client can load.
 // Keep its reads outside the membership and settlement acceptance guards.
 try{
  const read=autoRead();
  const stepField='<inStepMode>k__BackingField',viewField='<enableViewMultiBattle>k__BackingField';
  read.object(manager,'AutoChessGameManager',AUTO_NS,152,[[5,stepField,0x1c,[2]],[7,viewField,0x1e,[2]]]);
  const step=read.b(read.at(manager,stepField)),multi=read.b(read.at(manager,viewField));
  if(step>1||multi>1)return null;
  read.validate();
  return {in_step_mode:step===1,enable_view_multi_battle:multi===1};
 }catch(e){return null;}
}
function nativeBinding(){
 // Read the same five ownership roots and exact Q32.32 clock as the guarded
 // Windows sampler. The host binds this evidence to its battle serial.
 let step='root_identity';nativeAutoBindingDiagnostic=null;
 const unavailable=(reason,guard)=>{
  nativeAutoBodyCache=null;
  nativeAutoBindingDiagnostic={status:'unavailable',step,reason:String(reason).slice(0,300)};
  if(guard)nativeAutoBindingDiagnostic.guard=guard;
  return null;
 };
 const rejectRoot=(guard,actualClass)=>{
  const observed=typeof actualClass==='string'&&/^[A-Za-z0-9_`.<>+]{1,96}$/.test(actualClass)?
   ' (observed '+actualClass+')':'';
  return unavailable(nativeRootFailures[guard]+observed,guard);
 };
 try{
  const before=currentNativeIdentity(rejectRoot);
  if(before===null)return null;
  // Android supplies the same effective state slot as the guarded host.
  // Both reads still bracket the ownership roots and exact game clock.
  step='controller_state';
  const stateOffset=probeAdapter.autoChessStateOffset??0x228;
  const stateAddress=ptr(before.controller).add(stateOffset),state=stateAddress.readS32();
  if(state!==2&&state!==3)return unavailable('BattleController is not PLAYING or FINISHED');
  step='stage_metadata';
  const level=ptr(before.level),levelClass=probeAdapter.autoChessReflected?level.readPointer():null;
  const stageAddress=level.add(probeAdapter.autoChessStageOffset??0x18),stagePointer=stageAddress.readPointer(),stage=str(stagePointer);
  if(typeof stage!=='string'||!stage.length)return unavailable('LevelData stage identifier unavailable');
  const frame=clock.readU32(),fixed=clock.add(4).readS64().toString();
  const enabled=/AUTOCHESS/i.test(stage);
  let stageClass=null;
  if(enabled&&probeAdapter.autoChessReflected){
   stageClass=stagePointer.readPointer();
   if(!autoIdentity(stageClass,'String','System'))return unavailable('AutoChess stage identifier is not System.String');
  }
  const stageKey=[before.controller,before.native,before.logger,before.stats,before.level,stage].join(':');
  if(nativeAutoStage?.key!==stageKey){nativeAutoCache=null;nativeAutoBodyCache=null;}
  nativeAutoStage={key:stageKey,enabled,stageAddress,levelClass,stageClass,
   stagePointer:probeAdapter.autoChessReflected?stagePointer:stageAddress.readPointer()};
  const bodyKey=stageKey+'#'+nativeAutoStage.stagePointer.toString();
  if(!enabled)nativeAutoBodyCache=null;
  const auto=enabled?readAutoChessBody(before,frame,fixed,bodyKey):null;
  const viewMode=probeAdapter.autoChessReflected&&auto?.header?.phase===4?readAutoChessViewMode(auto.header.manager):null;
  step='closing_guard';
  if(probeAdapter.autoChessReflected&&(!level.readPointer().equals(levelClass)||
     !stageAddress.readPointer().equals(stagePointer)||str(stagePointer)!==stage||
     enabled&&!stagePointer.readPointer().equals(stageClass)))
   return unavailable('LevelData or stage identity changed during binding');
  const after=currentNativeIdentity(rejectRoot);
  if(after===null)return null;
  if(clock.readU32()!==frame||clock.add(4).readS64().toString()!==fixed
     ||Object.keys(before).some(key=>before[key]!==after[key])
     ||stateAddress.readS32()!==state||!probeAdapter.autoChessReflected&&str(stageAddress.readPointer())!==stage)
   return unavailable('battle roots, clock, state or stage changed during binding');
  step='clock';
  if(BigInt(fixed)<0n)return unavailable('negative game clock');
  step='clock_stamp';
  const stamped=damageSettlementStamp(frame,Number(BigInt(fixed))/4294967296);
  const eventScope=stamped.native_autochess_scope;
  if(enabled&&!auto.error&&eventScope?.status==='available'){
   const prefix=['controller','native','logger','stats','level'].map(k=>ptr(before[k]).toString()).join(':')+':autochess:';
   const headerKey=[auto.header.manager,auto.header.status,auto.header.model,auto.header.machine,auto.header.center]
    .map(p=>p.toString()).join(':');
   // The stamp has its own closing guard and may observe a same-clock phase
   // transition after the body closed. Never commit that older body by moving
   // the scope backwards again. Unknown cold/retired caches are not evidence
   // against a newly verified complete body and still permit initial binding.
   if(eventScope.protocol_version!==1||eventScope.verified_same_frame!==true||
      eventScope.frame!==frame||eventScope.game_time!==Number(BigInt(fixed))/4294967296||
      typeof eventScope.scope_key!=='string'||!eventScope.scope_key.startsWith(prefix)||
      eventScope.phase!==auto.header.phase||eventScope.round!==auto.header.round||
      nativeAutoScope?.headerKey!==headerKey||nativeAutoScope?.fingerprint!==auto.fingerprint){
    nativeAutoCache=null;
    return unavailable('verified event stamp changed after the AutoChess body closing guard');
   }
  }
  const key=[before.controller,before.native,before.logger,before.stats,before.level,stage].join(':');
  if(nativeLastBinding&&(nativeLastBinding.key!==key||frame<nativeLastBinding.frame
     ||BigInt(fixed)<BigInt(nativeLastBinding.fixed)))nativeGeneration++;
  nativeLastBinding={key,frame,fixed};
  const result={native_process_pid:Process.id,native_identity:before,native_generation:nativeGeneration,
   native_frame:frame,native_fixed_time:fixed,native_stage_id:stage};
  if(enabled){
   result.native_autochess_context=finishAutoChess(auto,before,frame,fixed);
   if(viewMode)result.autochess_view_mode=viewMode;
   nativeAutoBodyCache=!auto.error&&auto.unknown.length===0?
    {key:bodyKey,generation:nativeGeneration,epoch:nativeAutoEpoch,frame,fixed,body:auto}:null;
  }
  return result;
 }catch(e){return unavailable(e);}
}
