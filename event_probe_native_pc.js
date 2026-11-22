// ArkDPS event probe adapter v1; loaded with event_probe_runtime.js.
if(Process.platform!=='windows'||Process.arch!=='x64')throw new Error('Windows x64 process required');
function classFieldOffsets(k){
 const n=k.add(0x124).readU16(),seen={};
 if(n>1000)throw new Error('invalid field metadata');
 // GetFields expands lazy field metadata without running a class constructor.
 // Do not dereference Class.fields until the official metadata iterator has
 // completed; even a fully initialized VM can leave BasicSkill.fields null.
 const getField=api('il2cpp_class_get_fields','pointer',['pointer','pointer']);
 const iterator=Memory.alloc(8),fields=[];iterator.writeU64(0);
 for(;;){
  const f=getField(k,iterator);if(f.isNull())break;
  if(fields.length>=n)throw new Error('field metadata count mismatch');
  fields.push(f);
 }
 if(fields.length!==n||k.add(0x124).readU16()!==n)throw new Error('field metadata count mismatch');
 const a=k.add(0x80).readPointer();
 if(n&&a.isNull())throw new Error('field metadata table missing');
 for(let i=0;i<n;i++){
  const f=fields[i];
  if(!f.equals(a.add(i*32)))throw new Error('field metadata iterator mismatch');
  if(!f.add(16).readPointer().equals(k))throw new Error('field owner mismatch');
  seen[f.readPointer().readCString()]=f.add(24).readS32();
 }
 return seen;
}
function classMethods(k){
 const n=k.add(0x120).readU16();
 if(n>2000)throw new Error('invalid method metadata');
 const getMethod=api('il2cpp_class_get_methods','pointer',['pointer','pointer']);
 const iterator=Memory.alloc(8),methods=[];iterator.writeU64(0);
 for(;;){
  const method=getMethod(k,iterator);if(method.isNull())break;
  if(methods.length>=n)throw new Error('method metadata count mismatch');
  methods.push(method);
 }
 if(methods.length!==n||k.add(0x120).readU16()!==n)throw new Error('method metadata count mismatch');
 const array=k.add(0x98).readPointer();
 if(n&&array.isNull())throw new Error('method metadata table missing');
 for(let i=0;i<n;i++)if(!methods[i].equals(array.add(i*8).readPointer()))
  throw new Error('method metadata iterator mismatch');
 return methods;
}
let nativeControllerClass=null,nativeGeneration=0,nativeLastBinding=null;
// AutoChess identities come from the scene's own index/UID tables. These are
// managed metadata reads only; no game getter, class initializer or new hook.
const nativeAutoLayouts=new Map();
let nativeAutoStage=null,nativeAutoCache=null,nativeAutoEpoch=0,nativeAutoScope=null;
const AUTO_NS='Torappu.Battle.AutoChess';
function autoIdentity(k,name,namespace){
 return !k.isNull()&&className(k)===name&&k.add(0x18).readPointer().readCString()===namespace;
}
function autoLayout(k,name,namespace,count,specs){
 if(!autoIdentity(k,name,namespace))throw new Error('AutoChess class '+name);
 const cacheKey=k.toString()+':'+JSON.stringify(specs);
 if(nativeAutoLayouts.has(cacheKey))return nativeAutoLayouts.get(cacheKey);
 if(k.add(0x124).readU16()!==count)throw new Error('AutoChess field count '+name);
 const table=k.add(0x80).readPointer(),out={};
 if(table.isNull())throw new Error('AutoChess field metadata unavailable '+name);
 for(const [index,field,offset,kinds] of specs){
  const f=table.add(index*32),t=f.add(8).readPointer();
  if(index>=count||!f.add(16).readPointer().equals(k)||f.readPointer().readCString()!==field
     ||f.add(24).readS32()!==offset||t.isNull())throw new Error('AutoChess field '+name+'.'+field);
  const bits=t.add(8).readU32(),kind=(bits>>>16)&255;
  if(bits&16||!kinds.includes(kind))throw new Error('AutoChess field type '+name+'.'+field);
  out[field]={data:t.readPointer(),kind};
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
 const guards=[];
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
 read.object=(address,name,namespace,count,specs)=>{
  if(!address||address.isNull())throw new Error('AutoChess missing '+name);
  return autoLayout(read.p(address),name,namespace,count,specs);
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
  if(!autoIdentity(k,'ListDict`2','Torappu')||k.add(0x124).readU16()!==1)
   throw new Error('AutoChess ListDict class');
  k=read.p(k.add(0x58));
 }
 const typed=autoLayout(k,'List`1','System.Collections.Generic',6,
  [[1,'_items',0x10,[29]],[2,'_size',0x18,[8]],[3,'_version',0x1c,[8]]]);
 const items=read.p(address.add(0x10)),count=read.i(address.add(0x18));read.i(address.add(0x1c));
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
 const host=read.p(controller.add(0x2e8));
 read.object(host,'SingletonHost','',1,[[0,'m_instStore',0x10,[21]]]);
 const dict=read.p(host.add(0x10));
 const typed=read.object(dict,'Dictionary`2','System.Collections.Generic',14,
  [[1,'_entries',0x18,[29]],[2,'_count',0x20,[8]],[4,'_freeCount',0x28,[8]],[5,'_version',0x2c,[8]]]);
 const element=typed._entries.data;
 if(element.isNull()||((element.add(8).readU32()>>>16)&255)!==21)throw new Error('AutoChess dictionary metadata');
 const entry=autoGeneric(element.readPointer(),'Entry','',read);
 autoLayout(entry,'Entry','',4,[[0,'hashCode',0x10,[8]],[1,'next',0x14,[8]],[2,'key',0x18,[18]],[3,'value',0x20,[28,18]]]);
 const entries=read.p(dict.add(0x18)),count=read.i(dict.add(0x20)),free=read.i(dict.add(0x28));
 const dictVersion=read.i(dict.add(0x2c));
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
 const status=read.p(manager.add(0x38)),center=read.p(manager.add(0x40));
 read.object(status,'AutoChessGameStatus',AUTO_NS,62,[[0,'m_stateModel',0x10,[18]]]);
 const model=read.p(status.add(0x10));
 read.object(model,'AutoChessGameStateModel','',13,[[0,'m_stateMachine',0x18,[18]]]);
 const machine=read.p(model.add(0x18));
 read.object(machine,'AutoChessGameStateMachine','',13,[[1,'<gameCurrentStateId>k__BackingField',0x5c,[17]]]);
 read.object(center,'AutoChessDataCenter','Torappu.Battle.DataCenter',151,
  [[5,'<selfPlayerIndex>k__BackingField',0x38,[8]],[6,'<currentRound>k__BackingField',0x3c,[8]],
   [28,'battleData',0xe8,[18]],[30,'playerDataModel',0xf8,[18]]]);
 const phase=read.i(machine.add(0x5c)),round=read.i(center.add(0x3c));
 if(![0,1,2,3,4,5,6,7,8,9,100,101,102,103,104].includes(phase)||round<0)throw new Error('AutoChess phase/round');
 return {controller,host,dict,dictEntries:entries,dictCount:count,dictFree:free,managerSlot,
  manager,status,model,machine,center,phase,round,
  self:read.i(center.add(0x38)),dictVersion};
}
function autoData(address,read){
 read.object(address,'BattleCharacterData','Torappu.Battle',36,
  [[4,'prefabKey',0x50,[14]],[8,'uniqueId',0x6c,[9]],[14,'isToken',0x90,[2]],
   [15,'isPredefined',0x91,[2]],[18,'tokenOrHostKey',0x98,[14]],[19,'tokenOrHostUniqueId',0xa0,[9]]]);
 const cid=read.text(read.p(address.add(0x50))),data_uid=read.u(address.add(0x6c));
 const token=read.b(address.add(0x90)),predefined=read.b(address.add(0x91));
 if(!cid||!data_uid||token>1||predefined>1)throw new Error('AutoChess character identity');
 const host=read.p(address.add(0x98)),hostUid=read.u(address.add(0xa0));
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
  const header=autoHeader(identity,read),model=read.p(header.center.add(0xf8));
  read.object(model,'AutoChessPlayerDataModel',AUTO_NS,17,[[0,'m_allPlayerDatas',0x18,[21]]]);
  const playerList=read.p(model.add(0x18)),pairs=autoPairs(playerList,8,read,[18]);
  if(!pairs.length)throw new Error('AutoChess players unavailable');
  const playerItems=read.p(playerList.add(0x10)),playerVersion=read.i(playerList.add(0x1c));
  const players=[],indexes=new Set(),uids=new Set(),ownerRefs=[];let playerOrdinal=0;
  for(const [index,value]of pairs){
   read.object(value,'ScenePlayerData','',37,[[0,'index',0x10,[8]],[1,'uid',0x18,[14]],[6,'playerCard',0x40,[18]]]);
   const actual=read.i(value.add(0x10)),uidRef=read.p(value.add(0x18)),uid=read.text(uidRef);
   if(actual!==index||index<0||indexes.has(index)||!uid||uids.has(uid))throw new Error('AutoChess player bijection');
   indexes.add(index);uids.add(uid);ownerRefs.push({value,index,uidRef,slot:playerItems.add(0x20+16*playerOrdinal++)});
   const card=read.p(value.add(0x40));let nick_name=null,nick_number=null;
   if(!card.isNull()){
    read.object(card,'PlayerCard',AUTO_NS,6,[[0,'nickName',0x10,[14]],[1,'nickNumber',0x18,[14]]]);
    nick_name=read.text(read.p(card.add(0x10)));nick_number=read.text(read.p(card.add(0x18)));
   }
   players.push({index,uid,nick_name,nick_number});
  }
  if(!indexes.has(header.self))throw new Error('AutoChess self player missing');
  const battle=read.p(header.center.add(0xe8));
  read.object(battle,'AutoChessBattleDataModel',AUTO_NS,53,[[1,'battleChessInsts',0x20,[21]]]);
  const groups=autoPairs(read.p(battle.add(0x20)),8,read,[21]),rows=[];let total=0;
  for(const [index,list]of groups){
   if(!indexes.has(index))throw new Error('AutoChess role player missing');
   for(const value of autoReferences(list,64,'ChessInst',AUTO_NS,read)){
    if(++total>256)throw new Error('AutoChess role total bound');
    try{
     read.object(value,'ChessInst',AUTO_NS,27,
      [[6,'playerIndex',0x30,[8]],[8,'side',0x40,[17]],[10,'battleCharacterData',0x48,[18]]]);
     const player_index=read.i(value.add(0x30)),player_side=read.i(value.add(0x40));
     if(player_index!==index||player_side<0||player_side>4)throw new Error('AutoChess role index/side');
     rows.push({...autoData(read.p(value.add(0x48)),read),player_index,player_side});
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
   const manager=read.p(header.controller.add(0x2c0));
   read.object(manager,'UnitManager','Torappu.Battle',7,[[1,'<characters>k__BackingField',0x18,[21]]]);
   const container=read.p(manager.add(0x18));
   read.object(container,'UnorderedArray`1','Torappu',3,[[0,'m_items',0x10,[29]],[2,'<count>k__BackingField',0x20,[8]]]);
   const items=read.p(container.add(0x10)),count=read.i(container.add(0x20));
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
     autoLayout(character,'Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,[18]]]);
     const row=autoData(read.p(unit.add(0x538)),read);
     if(!row.is_token){
      const known=byUid.get(row.data_uid);
      if(!known||conflicts.has(row.data_uid))unknown.push({cid:row.cid,data_uid:row.data_uid,reason:'live role absent or conflicting in ChessInst'});
      else if(known.cid!==row.cid){conflicts.add(row.data_uid);unknown.push({cid:row.cid,data_uid:row.data_uid,reason:'conflicting live role UID'});}
      continue;
     }
     if(!bObject)throw new Error('AutoChess token BObject missing');
     autoLayout(bObject,'BObject','Torappu.Battle',9,[[3,'<playerSide>k__BackingField',0x20,[17]],
      [4,'<inited>k__BackingField',0x24,[2]],[5,'<borned>k__BackingField',0x25,[2]],
      [6,'<isDisappeared>k__BackingField',0x26,[2]]]);
     const inited=read.b(unit.add(0x24)),borned=read.b(unit.add(0x25)),disappeared=read.b(unit.add(0x26));
     if(inited!==1||borned!==1||disappeared!==0){unknown.push({cid:row.cid,data_uid:row.data_uid,reason:'token lifecycle unavailable or inactive'});continue;}
     const side=read.i(unit.add(0x20)),host=byUid.get(row.host_uid);
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
   memberships:[...byUid.values()].filter(row=>!conflicts.has(row.data_uid)),unknown};
 }catch(e){nativeAutoCache=null;return {error:String(e)};}
}
function finishAutoChess(result,identity,frame,fixed){
 if(result.error)return {protocol_version:1,status:'unknown',verified_same_frame:false,frame,
  game_time:Number(BigInt(fixed))/4294967296,reason:result.error};
 const {header,model,playerList,playerItems,playerVersion,ownerRefs,players,memberships,unknown,fingerprint}=result;
 const headerKey=[header.manager,header.status,header.model,header.machine,header.center].map(p=>p.toString()).join(':');
 const scope_key=autoScope(identity,header.phase,header.round,frame,fixed,fingerprint,headerKey);
 nativeAutoCache={...header,playerModel:model,playerList,playerItems,playerVersion,
  ownerRefs,identity:{...identity},fingerprint,headerKey};
 nativeAutoCache.blocks=scopeBlocks(nativeAutoCache);
 return {protocol_version:1,status:'available',verified_same_frame:true,frame,
  game_time:Number(BigInt(fixed))/4294967296,scope_key,phase:header.phase,round:header.round,
  self_player_index:header.self,players,memberships,unknown_memberships:unknown};
}
function stampAutoChessClock(when){
 if(!nativeAutoStage?.enabled)return;
 const unknown=reason=>{when.native_autochess_scope={protocol_version:1,status:'unknown',
  verified_same_frame:false,frame:when.frame,game_time:when.game_time,reason};};
 try{
  const cached=nativeAutoCache,before=currentNativeIdentity();
  if(!before)throw new Error('AutoChess scope cache unavailable');
  const stagePointer=ptr(before.level).add(0x18).readPointer();
  if(!stagePointer.equals(nativeAutoStage.stagePointer)){
   nativeAutoCache=null;
   if(!/AUTOCHESS/i.test(str(stagePointer)||'')){nativeAutoStage.enabled=false;return;}
   throw new Error('AutoChess stage cache changed');
  }
  if(!cached||Object.keys(before).some(k=>before[k]!==cached.identity[k]))throw new Error('AutoChess scope cache unavailable');
  const frame=clock.readU32(),fixed=clock.add(4).readS64().toString();
  if(frame!==when.frame||Number(BigInt(fixed))/4294967296!==when.game_time)throw new Error('AutoChess event clock mismatch');
  const read=autoRead();
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
  const phase=read.i(cached.machine.add(0x5c)),round=read.i(cached.center.add(0x3c));
  if(![0,1,2,3,4,5,6,7,8,9,100,101,102,103,104].includes(phase)||round<0)throw new Error('AutoChess event phase/round');
  read.validate();const after=currentNativeIdentity();
  if(!after||Object.keys(before).some(k=>before[k]!==after[k])||clock.readU32()!==frame||clock.add(4).readS64().toString()!==fixed)
   throw new Error('AutoChess event scope changed');
  const scope_key=autoScope(before,phase,round,frame,fixed,cached.fingerprint,cached.headerKey);
  when.native_autochess_scope={protocol_version:1,status:'available',verified_same_frame:true,
   frame,game_time:when.game_time,scope_key,phase,round};
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
function currentNativeIdentity(){
 if(!nativeControllerClass)return null;
 const parent=nativeControllerClass.add(0x58).readPointer();
 if(parent.isNull())return null;
 const statics=parent.add(0xb8).readPointer();
 if(statics.isNull())return null;
 const controller=statics.readPointer();
 return terminalIdentity(controller,nativeControllerClass);
}
function nativeBinding(){
 // Read the same five ownership roots and exact Q32.32 clock as the guarded
 // Windows sampler. The host binds this evidence to its battle serial.
 try{
  const before=currentNativeIdentity();
  if(before===null){nativeAutoBodyCache=null;return null;}
  const state=ptr(before.controller).add(0x228).readS32();
  if(state!==2&&state!==3){nativeAutoBodyCache=null;return null;}
  const stage=str(ptr(before.level).add(0x18).readPointer());
  if(typeof stage!=='string'||!stage.length){nativeAutoBodyCache=null;return null;}
  const frame=clock.readU32(),fixed=clock.add(4).readS64().toString();
  const enabled=/AUTOCHESS/i.test(stage);
  const stageKey=[before.controller,before.native,before.logger,before.stats,before.level,stage].join(':');
  if(nativeAutoStage?.key!==stageKey){nativeAutoCache=null;nativeAutoBodyCache=null;}
  nativeAutoStage={key:stageKey,enabled,stagePointer:ptr(before.level).add(0x18).readPointer()};
  const bodyKey=stageKey+'#'+nativeAutoStage.stagePointer.toString();
  if(!enabled)nativeAutoBodyCache=null;
  const auto=enabled?readAutoChessBody(before,frame,fixed,bodyKey):null;
  const after=currentNativeIdentity();
  if(after===null||clock.readU32()!==frame||clock.add(4).readS64().toString()!==fixed
     ||Object.keys(before).some(key=>before[key]!==after[key])
     ||ptr(after.controller).add(0x228).readS32()!==state
     ||str(ptr(after.level).add(0x18).readPointer())!==stage){nativeAutoBodyCache=null;return null;}
  if(BigInt(fixed)<0n){nativeAutoBodyCache=null;return null;}
  damageSettlementStamp(frame,Number(BigInt(fixed))/4294967296);
  const key=[before.controller,before.native,before.logger,before.stats,before.level,stage].join(':');
  if(nativeLastBinding&&(nativeLastBinding.key!==key||frame<nativeLastBinding.frame
     ||BigInt(fixed)<BigInt(nativeLastBinding.fixed)))nativeGeneration++;
  nativeLastBinding={key,frame,fixed};
  const result={native_process_pid:Process.id,native_identity:before,native_generation:nativeGeneration,
   native_frame:frame,native_fixed_time:fixed,native_stage_id:stage};
  if(enabled){
   result.native_autochess_context=finishAutoChess(auto,before,frame,fixed);
   nativeAutoBodyCache=!auto.error&&auto.unknown.length===0?
    {key:bodyKey,generation:nativeGeneration,epoch:nativeAutoEpoch,frame,fixed,body:auto}:null;
  }
  return result;
 }catch(e){nativeAutoBodyCache=null;return null;}
}
const probeAdapter={
 moduleName:'GameAssembly.dll',imageSizeType:'size_t',requiredClasses:['LevelData'],allowNullClassRows:true,
 controllerFields:{s_fixedFrameCnt:0x14,s_fixedPlayTime:0x18,m_logger:0xe0,m_levelData:0x160,m_state:0x228},
 validateClasses(found){expectFields(found.LevelData,{levelId:0x18});nativeControllerClass=found.BattleController;},
 validateTerminalController(){},terminalRequired:true,loggerOffset:0xe0,levelOffset:0x160,
 clockRegressed(previous,frame,game_time){return frame<previous.frame||game_time<previous.game_time;},
 onClockRegression(){nativeGeneration++;nativeLastBinding=null;},
 // Windows x64 puts the large return buffer in RCX and shifts explicit arguments.
 factoryArgument(args,index){return args[index+1];},
 factoryStorage(args){return args[0];},
 scopedModifierTrace:true,directHealingProof:true,
 stampClock(when){stampAutoChessClock(when);},
 baselineEnvelope(fields,records,acknowledged,through){return discardedAutoChessSkills(fields,records,acknowledged,through);},
 readEnvelope(){
  const binding=nativeBinding();
  return {fields:{scoped_trace_count:scopedTraceCount,scoped_trace_dropped:scopedTraceDropped,
   ...binding,retry:binding===null},ready:binding!==null};
 },
 markEnvelope(current){
  const binding=nativeBinding();
  return {fields:{...binding},ready:!(binding===null||current&&binding.native_frame!==current.frame
   ||current&&Number(BigInt(binding.native_fixed_time))/4294967296!==current.game_time)};
 },
 enterTerminalStateHook(invocation){beginEnemyHook('BattleController.set_state');invocation.nativeStateHook=true;},
 observeTerminalState(args,state,identity){
  const current=currentNativeIdentity();
  if(state===2&&args[0].add(0x228).readS32()!==2&&identity&&current&&identity.controller===current.controller){
   nativeGeneration++;nativeLastBinding=null;resetDamageSettlementScope();
  }
 },
 leaveTerminalStateHook(invocation){if(invocation.nativeStateHook)endEnemyHook('BattleController.set_state');},
 terminalInstallFailed(error){throw new Error('native battle transition observation failed: '+String(error));}
};
