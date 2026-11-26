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
 if([4,5,6].includes(phase)&&nativeAutoEffective?.scope_key!==scope_key){
  let mode=nativeAutoDesiredMode;
  // The assisted battlefield may have only other accounts' deployed actors.
  if(phase===5)mode='all';
  else if(phase===4&&probeAdapter.supportsAutoChessAllDefense!==true)mode='local';
  nativeAutoEffective={scope_key,mode};
 }
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
 if(typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()&&typeof probeAdapter.captureClassIdentity==='function')return probeAdapter.captureClassIdentity(k,name,namespace);
 return !k.isNull()&&className(k)===name&&classNamespace(k)===namespace;
}
function autoLayout(k,name,namespace,count,specs,check=null){
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
    check?.(24);
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
function scopeBlock(address,size,fields,mismatch){
 const bytes=address.readByteArray(size);
 if(bytes===null||bytes.byteLength!==size)throw new Error('AutoChess block read unavailable');
 const view=new DataView(bytes);
 for(const [kind,offset,expected,high]of fields){
  if(kind==='p'){
   if(view.getUint32(offset,true)!==expected||view.getUint32(offset+4,true)!==high)
    throw new Error(mismatch??'AutoChess scope block pointer changed');
  }else if(view.getInt32(offset,true)!==expected)throw new Error(mismatch??'AutoChess scope block integer changed');
 }
}
function scopeGuardGroup(fields,existingBlock=false){
 if(fields.length<2)return fields;
 const addresses=fields.map(([,address])=>BigInt(address.toString()));
 let first=0,last=addresses[0];
 for(let index=0;index<fields.length;index++){
  const address=addresses[index],end=address+(fields[index][0]==='p'?8n:4n);
  if(address<addresses[first])first=index;
  if(end>last)last=end;
 }
 const start=addresses[first],size=last-start,pageSize=BigInt(Process.pageSize??4096);
 // New reads stay in one page. Existing PC blocks retain their fault behavior.
 if(size>128n||!existingBlock&&start/pageSize!==(last-1n)/pageSize)return fields;
 const words=fields.map(([kind,,expected],index)=>{
  const offset=Number(addresses[index]-start);
  if(kind==='i')return [kind,offset,expected];
  const value=BigInt(expected.toString());
  return [kind,offset,Number(BigInt.asUintN(32,value)),Number(BigInt.asUintN(32,value>>32n))];
 });
 return [['block',fields[first][1],words,Number(size)]];
}
function scopeGuardPlan(body){
 const p=(address,value)=>['p',address,value],i=(address,value)=>['i',address,value];
 const {header,model,playerList,playerItems,playerVersion,ownerRefs,playerAddresses:list}=body;
 const a=header.addresses,reflected=probeAdapter.autoChessReflected;
 const group=fields=>reflected?fields:scopeGuardGroup(fields,true);
 const plan=[p(a.controllerHost,header.host),p(a.hostDict,header.dict),p(a.statusModel,header.model),
  p(a.modelMachine,header.machine),p(header.managerSlot,header.manager),p(a.centerPlayers,model),
  p(list.list,playerList),
  ...group([p(a.managerStatus,header.status),p(a.managerCenter,header.center)]),
  ...group([p(a.dictEntries,header.dictEntries),i(a.dictCount,header.dictCount),
   i(a.dictFree,header.dictFree),i(a.dictVersion,header.dictVersion)]),
  ...group([p(list.items,playerItems),i(list.count,ownerRefs.length),i(list.version,playerVersion)])];
 const slots=ownerRefs.map(owner=>[i(owner.slot,owner.index),p(owner.slot.add(8),owner.value)]);
 if(reflected)plan.push(...slots.flat());
 else{
  const slotBlock=scopeGuardGroup(slots.flat());
  plan.push(...(slotBlock.length===1?slotBlock:slots.flatMap(group)));
 }
 for(const owner of ownerRefs)
  plan.push(...group([i(owner.indexAddress,owner.index),p(owner.uidAddress,owner.uidRef)]));
 if(reflected)plan.push(i(a.self,header.self));
 return plan;
}
function validateScopeGuards(plan){
 for(const [kind,address,expected,size]of plan){
  if(kind==='block'){scopeBlock(address,size,expected);continue;}
  if(kind==='p'?!address.readPointer().equals(expected):address.readS32()!==expected)
   throw new Error('AutoChess scope guard changed');
 }
}
function validateScopeStage(stage,level,stagePointer){
 if(probeAdapter.autoChessReflected&&(!ptr(level).readPointer().equals(stage.levelClass)||
    !stagePointer.readPointer().equals(stage.stageClass)))throw new Error('AutoChess stage class changed');
}
// Preserve discovery order. Adjacent pointer/int witnesses in the same
// 128-byte region can be compared from one copy on each independent pass.
function rosterGuardPlan(guards){
 const plan=[];
 let fields=[],region=null;
 for(const field of guards){
  const [kind,address]=field;
  const next=kind==='p'||kind==='i'?BigInt(address.toString())>>7n:null;
  if(next!==region||next===null){
   plan.push(...scopeGuardGroup(fields));
   fields=[];
  }
  if(next===null)plan.push(field);
  else fields.push(field);
  region=next;
 }
 plan.push(...scopeGuardGroup(fields));
 return plan;
}
function autoRead(check=null){
 const guards=[],layouts=new Map();
 let previousGuardCount=0,plan=null;
 function value(kind,address){
  check?.(kind==='b'?1:kind==='i'||kind==='u'?4:8);
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
  return read.layout(address,autoLayout(read.p(address),name,namespace,count,specs,check));
 };
 read.text=address=>{
  if(address.isNull())return null;
  if(!autoIdentity(read.p(address),'String','System'))throw new Error('AutoChess string class');
  const count=read.i(address.add(0x10));
  if(count<0||count>128)throw new Error('AutoChess string length');
  check?.(count*2);
  const result=address.add(0x14).readUtf16String(count);
  if(typeof result!=='string'||/[\u0000-\u001f\u007f]/.test(result))throw new Error('AutoChess string value');
  guards.push(['text',address,result]);return result;
 };
 read.validate=()=>{
  // The discovery pass is scalar. Build a plan only for repeated checks
  // of a complete PC roster. Strings and unsigned fields stay scalar.
  if(previousGuardCount===guards.length&&!probeAdapter.autoChessReflected)
   plan??=rosterGuardPlan(guards);
  else plan=null;
  previousGuardCount=guards.length;
  for(const [kind,address,before,size] of plan??guards){
   check?.(kind==='text'?4+before.length*2:kind==='block'?size:kind==='b'?1:kind==='u'||kind==='i'?4:8);
   if(kind==='block'){scopeBlock(address,size,before,'AutoChess changed during read');continue;}
   if(kind==='text'&&address.add(0x10).readS32()!==before.length)
    throw new Error('AutoChess string length changed during read');
   const after=kind==='p'?address.readPointer():kind==='i'?address.readS32():
    kind==='u'?address.readU32():kind==='b'?address.readU8():kind==='n'?address.readU64().toNumber():
    address.add(0x14).readUtf16String(before.length);
   if(kind==='p'?!after.equals(before):after!==before)throw new Error('AutoChess changed during read');
  }
 };
 return read;
}
function autoGeneric(data,name,namespace,read){
 if(data.isNull())throw new Error('AutoChess generic metadata');
 const k=autoMetadataPointer(data.add(24),read);
 if(!autoIdentity(k,name,namespace))throw new Error('AutoChess generic class '+name);
 return k;
}
// These helpers are used only for setup-verified, immutable IL2CPP metadata.
// Gameplay object fields still require the current owned capture record.
function autoMetadataView(address,size){
 if(typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()){
  if(typeof probeAdapter.captureImmutableMetadata!=='function')__arkdpsReplayGap('immutable metadata closure unavailable');
  return new DataView(probeAdapter.captureImmutableMetadata(address,size));
 }
 return new DataView(address.readByteArray(size));
}
function autoMetadataPointer(address,read=null){return typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()?
 ptr(autoMetadataView(address,8).getBigUint64(0,true)):read?read.p(address):address.readPointer();}
function autoMetadataU16(address){return typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()?
 autoMetadataView(address,2).getUint16(0,true):address.readU16();}
function autoMetadataU32(address){return typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()?
 autoMetadataView(address,4).getUint32(0,true):address.readU32();}
function autoList(address,maximum,read,listdict=false){
 let k=read.p(address);
 if(listdict){
  if(!autoIdentity(k,'ListDict`2','Torappu')||!probeAdapter.autoChessReflected&&autoMetadataU16(k.add(0x124))!==1)
   throw new Error('AutoChess ListDict class');
  k=autoMetadataPointer(k.add(0x58),read);
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
 if(element.isNull()||((autoMetadataU32(element.add(8))>>>16)&255)!==21)throw new Error('AutoChess pair metadata');
 const pair=autoGeneric(autoMetadataPointer(element),'KeyValuePair`2','System.Collections.Generic',read);
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
 if(element.isNull()||((autoMetadataU32(element.add(8))>>>16)&255)!==21)throw new Error('AutoChess dictionary metadata');
 const entry=autoGeneric(autoMetadataPointer(element),'Entry','',read);
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
function captureAutoCharacterFormPoint(scope,when,stamp){
 if(typeof probeAdapter.captureCharacterFormPoint==='function'&&typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive())return probeAdapter.captureCharacterFormPoint(scope,when,stamp);
 if(scope.source!=='char_002_amiya'||!scope.sourceCharacterUid)return;
 const cache=nativeAutoCache,stage=nativeAutoStage;
 if(!scope.mod||!scope.nativeModifierSource||!sourceCharacterClasses||!enemyEntryConfig||!cache||!stage?.enabled||
    stamp?.status!=='available'||![4,5,6].includes(stamp.phase)||!['all','local'].includes(stamp.collection_mode)||
    stamp.frame!==when.frame||stamp.game_time!==when.game_time)return;
 try{
  const fixed=captureClockFixed().toString();
  const sameScopeAndClock=()=>{
   const current=currentNativeIdentity(),observed=nativeAutoScope;
   return cache===nativeAutoCache&&stage===nativeAutoStage&&
    observed?.roots===cache.roots&&observed.phase===stamp.phase&&observed.round===stamp.round&&
    observed.headerKey===cache.headerKey&&observed.fingerprint===cache.fingerprint&&
    stamp.scope_key===cache.roots+':autochess:'+nativeAutoEpoch&&
    nativeAutoEffective?.scope_key===stamp.scope_key&&nativeAutoEffective.mode===stamp.collection_mode&&
    !!current&&Object.keys(current).every(key=>current[key]===cache.identity[key])&&
    stage.stageAddress.readPointer().equals(stage.stagePointer)&&
    cache.addresses.phase.readS32()===stamp.phase&&cache.addresses.round.readS32()===stamp.round&&
    captureClockFrame()===when.frame&&captureClockFixed().toString()===fixed;
  };
  // These optional reads must not update or invalidate the production scope cache.
  if(Number(BigInt(fixed))/4294967296!==when.game_time||!sameScopeAndClock())return;
  validateScopeStage(stage,cache.identity.level,stage.stagePointer);
  validateScopeGuards(cache.scopeGuards);
  const read=autoRead(),source=read.p(scope.mod);
  if(source.isNull()||!scope.nativeModifierSource.equals(source))return;
  const klass=read.p(source);
  if(!isCharacterSourceClass(klass)||!enemyEntryConfig.assignable(enemyEntryConfig.bObject,klass))return;
  const entity_cid=read.text(read.p(source.add(0x148))),instance_uid=read.u(source.add(0x18));
  if(entity_cid!==scope.source||!instance_uid)return;
  const data=read.p(source.add(0x538));
  if(data.isNull())return;
  const dataClass=read.p(data);
  if(!dataClass.equals(sourceCharacterClasses.data))return;
  const {cid:data_cid,data_uid,is_token,is_predefined}=autoData(data,read);
  if(is_token||is_predefined||data_uid!==scope.sourceCharacterUid||data_cid===entity_cid||
     !/^char_[A-Za-z0-9_#.-]{1,123}$/.test(data_cid))return;
  validateScopeGuards(cache.scopeGuards);
  validateScopeStage(stage,cache.identity.level,stage.stagePointer);
  if(!sameScopeAndClock())return;
  read.validate();
  if(sameScopeAndClock())return {source,klass,data,dataClass,entity_cid,data_cid,data_uid,instance_uid};
 }catch(e){/* Keep the original identity when a different character form cannot be proved. */}
}
function beginAutoCharacterForm(scope){
 const cached=nativeAutoScope,effective=nativeAutoEffective,when=scope.when;
 if(!cached||!effective)return;
 const stamp={protocol_version:1,status:'available',verified_same_frame:true,frame:when.frame,
  game_time:when.game_time,scope_key:effective.scope_key,phase:cached.phase,round:cached.round,
  collection_mode:effective.mode};
 const identity=captureAutoCharacterFormPoint(scope,when,stamp);
 if(identity)scope.characterFormEntry={identity,stamp};
}
function autoCharacterFormEvidence(scope,when){
 const witness=scope.characterFormEntry,stamp=when.native_autochess_scope;
 if(!witness||!sameMitigationTime(scope.when,when)||
    !sameNativeEventScope({native_autochess_scope:witness.stamp},when)||
    stamp?.collection_mode!==witness.stamp.collection_mode)return;
 const entry=witness.identity;
 const owner=scope.tracksModifier?scope:scope.modifierScope??null;
 if(scope.mitigationModifierReused||owner?.mitigationModifierReused||
    damageModifier(scope.tid,scope.mod)!==owner)return;
 const closing=captureAutoCharacterFormPoint(scope,when,stamp);
 if(!closing||!['source','klass','data','dataClass'].every(key=>entry[key].equals(closing[key]))||
    !['entity_cid','data_cid','data_uid','instance_uid'].every(key=>entry[key]===closing[key])||
    damageModifier(scope.tid,scope.mod)!==owner)return;
 const {entity_cid,data_cid,data_uid,instance_uid}=closing;
 const {frame,game_time,scope_key,phase,round,collection_mode}=stamp;
 return {version:1,entity_cid,data_cid,data_uid,instance_uid,frame,game_time,scope_key,phase,round,collection_mode};
}
function autoTokenHostEvidence(scope,when){
 if(typeof probeAdapter.captureTokenHostEvidence==='function'&&typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive())return probeAdapter.captureTokenHostEvidence(scope,when);
 // A short-lived token can disappear before the next membership sample.
 const stamp=when.native_autochess_scope,entry=scope.when,cache=nativeAutoCache;
 if(stamp?.status!=='available'||![4,5,6].includes(stamp.phase)||!entry||
    !scope.source?.startsWith('token_')||!scope.sourceCharacterUid||
    !sourceCharacterClasses||!enemyEntryConfig||!cache||
    !sameMitigationTime(entry,when)||!sameNativeEventScope(entry,when))return;
 try{
  const fixed=captureClockFixed().toString(),{phase,round}=cache.addresses;
  const samePoint=()=>captureClockFrame()===when.frame&&captureClockFixed().toString()===fixed&&
                      phase.readS32()===stamp.phase&&round.readS32()===stamp.round;
  if(Number(BigInt(fixed))/4294967296!==when.game_time||!samePoint())return;
  const read=autoRead(),source=read.p(scope.mod);
  if(source.isNull()||!scope.nativeModifierSource?.equals(source))return;
  const klass=read.p(source);
  if(!isCharacterSourceClass(klass)||!enemyEntryConfig.assignable(enemyEntryConfig.bObject,klass))return;
  const data=read.p(source.add(0x538));
  if(data.isNull()||!read.p(data).equals(sourceCharacterClasses.data))return;
  const {cid,data_uid,is_token,host_key,host_uid}=autoData(data,read);
  if(!is_token||cid!==scope.source||data_uid!==scope.sourceCharacterUid||
     !host_key||!/^[A-Za-z0-9_#.-]{1,128}$/.test(host_key)||!host_uid||
     host_uid===data_uid)return;
  const layout=autoLayout(enemyEntryConfig.bObject,'BObject','Torappu.Battle',9,
   [[3,'<playerSide>k__BackingField',0x20,[17]]]);
  const player_side=read.i(source.add(layout['<playerSide>k__BackingField'].offset));
  if(player_side<0||player_side>4)return;
  read.validate();
  const current=currentNativeIdentity();
  if(current&&Object.keys(current).every(key=>current[key]===cache.identity[key])&&samePoint())
   return {host_key,host_uid,player_side};
 }catch(e){/* The event retains its original source and amount without this witness. */}
}
function autoScope(roots,phase,round,frame,fixed,fingerprint,headerKey){
 const previous=nativeAutoScope;
 if(previous&&(previous.roots!==roots||previous.phase!==phase||previous.round!==round||
   frame<previous.frame||(fixed!==previous.fixed&&BigInt(fixed)<BigInt(previous.fixed))||
   headerKey!==undefined&&previous.headerKey!==undefined&&headerKey!==previous.headerKey||
   fingerprint!==undefined&&previous.fingerprint!==undefined&&fingerprint!==previous.fingerprint))nativeAutoEpoch++;
 nativeAutoScope={roots,phase,round,frame,fixed,fingerprint:fingerprint===undefined?previous?.fingerprint:fingerprint,
  headerKey:headerKey===undefined?previous?.headerKey:headerKey};
 return roots+':autochess:'+nativeAutoEpoch;
}
function readAutoChess(identity,frame,fixed){
 const read=autoRead(),unknown=[];
 let unboundHosts=0,liveCharacterCollectionEmpty=null;
 let liveCharacterDataUids=frame===0&&fixed==='0'?new Set():null;
 let lifecycleInstances=frame===0&&fixed==='0'?[]:null;const lifecycleUids=new Set(),lifecycleBodies=new Set();
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
   liveCharacterCollectionEmpty=count===0;
   for(let i=0;i<count;i++){
    const unit=read.p(items.add(0x20+i*8));
    try{
     let k=read.p(unit),character=null,bObject=null;const seen=new Set();
     for(let depth=0;depth<10&&!k.isNull();depth++){
      if(seen.has(k.toString()))throw new Error('AutoChess token lineage cycle');seen.add(k.toString());
      if(autoIdentity(k,'Character','Torappu.Battle'))character=k;
      if(autoIdentity(k,'BObject','Torappu.Battle')){bObject=k;break;}
      k=autoMetadataPointer(k.add(0x58),read);
     }
     if(!character){liveCharacterDataUids=null;lifecycleInstances=null;continue;}
     read.layout(unit,autoLayout(character,'Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,[18]]]));
     const row=autoData(read.p(read.at(unit,'<data>k__BackingField')),read);
     if(liveCharacterDataUids){
      if(!bObject||liveCharacterDataUids.has(row.data_uid))liveCharacterDataUids=null;
      else liveCharacterDataUids.add(row.data_uid);
     }
     if(lifecycleInstances){
      try{
       if(!bObject)throw new Error('lifecycle BObject missing');
       read.layout(unit,autoLayout(bObject,'BObject','Torappu.Battle',9,[
        [1,'<instanceUid>k__BackingField',0x18,[9]],[3,'<playerSide>k__BackingField',0x20,[17]],
        [4,'<inited>k__BackingField',0x24,[2]],[5,'<borned>k__BackingField',0x25,[2]],
        [6,'<isDisappeared>k__BackingField',0x26,[2]]]));
       const inited=read.b(read.at(unit,'<inited>k__BackingField')),borned=read.b(read.at(unit,'<borned>k__BackingField')),
        disappeared=read.b(read.at(unit,'<isDisappeared>k__BackingField'));
       if(inited>1||borned>1||disappeared>1)throw new Error('lifecycle invalid flags');
       if(inited===1&&borned===1&&disappeared===0&&!row.is_token){
        const instance_uid=read.u(read.at(unit,'<instanceUid>k__BackingField')),
         player_side=read.i(read.at(unit,'<playerSide>k__BackingField')),known=byUid.get(row.data_uid);
        if(!instance_uid||lifecycleUids.has(row.data_uid)||lifecycleBodies.has(instance_uid)||!known||conflicts.has(row.data_uid)
           ||known.cid!==row.cid||known.player_side!==player_side||known.is_token)throw new Error('lifecycle body identity');
        lifecycleUids.add(row.data_uid);lifecycleBodies.add(instance_uid);
        lifecycleInstances.push({cid:row.cid,data_uid:row.data_uid,instance_uid,player_side,
         player_index:known.player_index,player_uid:players.find(p=>p.index===known.player_index)?.uid,
         inited:true,borned:true,disappeared:false});
       }
      }catch(e){lifecycleInstances=null;}
     }
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
      unboundHosts++;
      unknown.push({cid:row.cid,data_uid:row.data_uid,host_uid:row.host_uid,reason:'token host unavailable or conflicting'});continue;
     }
     add({...row,player_index:host.player_index,player_side:side});
    }catch(e){liveCharacterDataUids=null;lifecycleInstances=null;unknown.push({reason:String(e)});}
   }
  }catch(e){liveCharacterDataUids=null;lifecycleInstances=null;unknown.push({reason:'token collection unavailable: '+String(e)});}
  read.validate();
  const fingerprint=JSON.stringify(players.map(p=>[p.index,p.uid]).sort((a,b)=>a[0]-b[0]));
  return {read,header,model,playerList,playerItems,playerVersion,ownerRefs,fingerprint,players,
   playerAddresses:{list:read.at(model,'m_allPlayerDatas'),items:read.at(playerList,'_items'),
    count:read.at(playerList,'_size'),version:read.at(playerList,'_version')},
   memberships:[...byUid.values()].filter(row=>!conflicts.has(row.data_uid)),unknown,
   allInputsGuarded:unknown.length===unboundHosts,liveCharacterCollectionEmpty,
   lifecycleBaseline:lifecycleInstances&&conflicts.size===0?{version:1,complete:true,frame:0,game_time:0,fixed:'0',units:lifecycleInstances}:null,
   liveCharacterDataUids:liveCharacterDataUids&&conflicts.size===0?
    [...liveCharacterDataUids].sort((left,right)=>left-right):null};
 }catch(e){return {error:String(e)};}
}
function finishAutoChess(result,identity,frame,fixed){
 if(result.error)return {protocol_version:1,status:'unknown',verified_same_frame:false,frame,
  game_time:Number(BigInt(fixed))/4294967296,reason:result.error};
 const {header,players,memberships,unknown,fingerprint}=result;
 const headerKey=[header.manager,header.status,header.model,header.machine,header.center].map(p=>p.toString()).join(':');
 const roots=[identity.controller,identity.native,identity.logger,identity.stats,identity.level].join(':');
 const scope_key=autoScope(roots,header.phase,header.round,frame,fixed,fingerprint,headerKey);
 const collection_mode=autoCollectionMode(scope_key,header.phase);
 // A reused body has passed both live guard passes. Keep the plan of what to
 // read on it, while each event must still read and compare every guard.
 result.scopeGuards??=scopeGuardPlan(result);
 nativeAutoCache={addresses:header.addresses,identity:{...identity},fingerprint,headerKey,roots,scopeGuards:result.scopeGuards,scopeWitness:{header:result.header,playerList:result.playerList,playerItems:result.playerItems,ownerRefs:result.ownerRefs}};
 autoCollectionOwners(result,scope_key,collection_mode,frame,Number(BigInt(fixed))/4294967296);
 probeAdapter.publishCaptureScope?.();
 return {protocol_version:1,status:'available',verified_same_frame:true,frame,
  game_time:Number(BigInt(fixed))/4294967296,scope_key,phase:header.phase,round:header.round,
  self_player_index:header.self,players,memberships,unknown_memberships:unknown,collection_mode,
  match_key:header.manager.toString()+':'+header.center.toString()};
}
// A container version can change without changing the battle/account graph.
// Re-prove just that graph once, without walking character/token membership.
// This slow path never publishes/replaces a body or its actor ownership map.
function refreshAutoChessScope(cached,when){
 const before=currentNativeIdentity(),stage=nativeAutoStage;
 if(!before||Object.keys(before).some(k=>before[k]!==cached.identity[k]))return false;
 const frame=captureClockFrame(),fixed=captureClockFixed().toString();
 if(frame!==when.frame||Number(BigInt(fixed))/4294967296!==when.game_time)return false;
 if(!stage?.enabled||!stage.stageAddress.readPointer().equals(stage.stagePointer))return false;
 validateScopeStage(stage,before.level,stage.stagePointer);
 const read=autoRead(),header=autoHeader(before,read),model=read.p(read.at(header.center,'playerDataModel'));
 read.object(model,'AutoChessPlayerDataModel',AUTO_NS,17,[[0,'m_allPlayerDatas',0x18,[21]]]);
 const playerList=read.p(read.at(model,'m_allPlayerDatas')),pairs=autoPairs(playerList,8,read,[18]);
 if(!pairs.length)return false;
 const playerItems=read.p(read.at(playerList,'_items')),playerVersion=read.i(read.at(playerList,'_version'));
 const players=[],ownerRefs=[],indexes=new Set(),uids=new Set();
 for(const [index,value]of pairs){
  read.object(value,'ScenePlayerData','',37,[[0,'index',0x10,[8]],[1,'uid',0x18,[14]]]);
  const actual=read.i(read.at(value,'index')),uidRef=read.p(read.at(value,'uid')),uid=read.text(uidRef);
  if(actual!==index||index<0||indexes.has(index)||!uid||uids.has(uid))return false;
  indexes.add(index);uids.add(uid);players.push({index,uid});
  ownerRefs.push({value,index,uidRef,slot:playerItems.add(0x20+16*ownerRefs.length),
   indexAddress:read.at(value,'index'),uidAddress:read.at(value,'uid')});
 }
 const fingerprint=JSON.stringify(players.map(p=>[p.index,p.uid]).sort((a,b)=>a[0]-b[0]));
 const old=cached.scopeWitness;
 if(!old||fingerprint!==cached.fingerprint||!playerList.equals(old.playerList)||!playerItems.equals(old.playerItems)||
    ownerRefs.length!==old.ownerRefs.length||ownerRefs.some((owner,index)=>{
     const prior=old.ownerRefs[index];
     return owner.index!==prior.index||!owner.value.equals(prior.value)||!owner.uidRef.equals(prior.uidRef)||!owner.slot.equals(prior.slot);
    })||['host','dict','dictEntries','managerSlot','manager','status','model','machine','center'].some(k=>!header[k].equals(old.header[k])))return false;
 // Independent live closing witnesses, including text/slot/account values.
 read.validate();read.validate();
 validateScopeStage(stage,before.level,stage.stagePointer);
 const after=currentNativeIdentity();
 if(!after||Object.keys(before).some(k=>before[k]!==after[k])||
    !stage.stageAddress.readPointer().equals(stage.stagePointer)||captureClockFrame()!==frame||
    captureClockFixed().toString()!==fixed)return false;
 const result={header,model,playerList,playerItems,playerVersion,ownerRefs,
  playerAddresses:{list:read.at(model,'m_allPlayerDatas'),items:read.at(playerList,'_items'),
   count:read.at(playerList,'_size'),version:read.at(playerList,'_version')}};
 nativeAutoCache={...cached,addresses:header.addresses,scopeGuards:scopeGuardPlan(result),scopeWitness:result};
 probeAdapter.publishCaptureScope?.();
 // The full body was not revalidated here; no old actor map may prune events.
 nativeAutoOwners=null;
 return true;
}
function stampAutoChessClock(when,allowRefresh=true){
 if(typeof probeAdapter.captureScopePoint==='function'&&typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()){
  const scope=probeAdapter.captureScopePoint(when);if(scope===undefined)return;
  when.native_autochess_scope=scope??{protocol_version:1,status:'unknown',verified_same_frame:false,frame:when.frame,game_time:when.game_time,reason:'captured native scope unavailable'};return;
 }
 if(!nativeAutoStage?.enabled)return;
 const unknown=reason=>{when.native_autochess_scope={protocol_version:1,status:'unknown',
  verified_same_frame:false,frame:when.frame,game_time:when.game_time,reason};};
 let guardFailed=false;
 const cached=nativeAutoCache;
 const guard=()=>{try{validateScopeGuards(cached.scopeGuards);}catch(e){guardFailed=true;throw e;}};
 try{
  const before=currentNativeIdentity();
  if(!before)throw new Error('AutoChess scope cache unavailable');
  const stagePointer=nativeAutoStage.stageAddress.readPointer();
  if(!stagePointer.equals(nativeAutoStage.stagePointer)){
   nativeAutoCache=null;
   if(!/AUTOCHESS/i.test(str(stagePointer)||'')){nativeAutoStage.enabled=false;return;}
   throw new Error('AutoChess stage cache changed');
  }
  if(!cached||Object.keys(before).some(k=>before[k]!==cached.identity[k]))throw new Error('AutoChess scope cache unavailable');
  const openingClock=captureRead('scope.clock_open',()=>checkedNativeView(clock,12)),frame=openingClock.getUint32(0,true),fixed=openingClock.getBigInt64(4,true).toString();
  if(frame!==when.frame||Number(BigInt(fixed))/4294967296!==when.game_time){
   unknown('AutoChess event clock mismatch');return;
  }
  const {phase:phaseAddress,round:roundAddress}=cached.addresses;
  validateScopeStage(nativeAutoStage,before.level,stagePointer);
  guard();
  const phase=phaseAddress.readS32(),round=roundAddress.readS32();
  if(![0,1,2,3,4,5,6,7,8,9,100,101,102,103,104].includes(phase)||round<0)throw new Error('AutoChess event phase/round');
  guard();
  validateScopeStage(nativeAutoStage,before.level,stagePointer);
  if(phaseAddress.readS32()!==phase||roundAddress.readS32()!==round||
     !nativeAutoStage.stageAddress.readPointer().equals(stagePointer))throw new Error('AutoChess changed during read');
  const after=currentNativeIdentity();
  if(!after||Object.keys(before).some(k=>before[k]!==after[k]))throw new Error('AutoChess event scope changed');
  const closingClock=captureRead('scope.clock_close',()=>checkedNativeView(clock,12));
  if(closingClock.getUint32(0,true)!==frame||closingClock.getBigInt64(4,true).toString()!==fixed){
   unknown('AutoChess event scope changed');return;
  }
  const scope_key=autoScope(cached.roots,phase,round,frame,fixed,cached.fingerprint,cached.headerKey);
  when.native_autochess_scope={protocol_version:1,status:'available',verified_same_frame:true,
   frame,game_time:when.game_time,scope_key,phase,round,collection_mode:autoCollectionMode(scope_key,phase)};
 }catch(e){
  if(allowRefresh&&guardFailed&&cached){
   try{if(refreshAutoChessScope(cached,when))return stampAutoChessClock(when,false);}catch(refreshError){/* Preserve the original failure. */}
  }
  nativeAutoCache=null;nativeAutoOwners=null;probeAdapter.publishCaptureScope?.();unknown(String(e));
 }
}
const AUTO_PRESENCE_SLOT_LIMIT=4096,AUTO_PRESENCE_CHARACTER_LIMIT=128;
const autoPresenceLineages=new Map();
function autoPresenceLineage(klass,check){
 const key=klass.toString();
 if(autoPresenceLineages.has(key))return autoPresenceLineages.get(key);
 let character=null,current=klass;
 for(let depth=0;depth<10&&!current.isNull();depth++){
  check(24);
  if(autoIdentity(current,'Character','Torappu.Battle'))character=current;
  if(autoIdentity(current,'BObject','Torappu.Battle')){
   const lineage={character,bObject:current};
   if(autoPresenceLineages.size<128)autoPresenceLineages.set(key,lineage);
   return lineage;
  }
  current=autoMetadataPointer(current.add(0x58));
 }
 throw new Error('AutoChess presence lineage');
}
function captureAutoChessPresence(mark){
 const context=mark.native_autochess_context,cache=nativeAutoCache,stage=nativeAutoStage;
 if(!cache||!stage?.enabled||context?.status!=='available'||![4,5,6].includes(context.phase)||
    mark.native_battle_state!==2||mark.active!==0)return;
 const {frame,game_time,scope_key,phase,round,collection_mode}=context;
 const point={version:1,status:'unavailable',frame,game_time,native_fixed_time:mark.native_fixed_time,
  scope_key,phase,round,collection_mode,units:null};
 const deadline=Date.now()+12;
 let reads=0,bytes=0;
 function check(size=0,operations=1){
  reads+=operations;bytes+=size;
  if(reads>12000||bytes>256*1024||Date.now()>deadline)throw new Error('AutoChess presence budget');
 }
 function verifyBinding(){
  check(768+cache.scopeGuards.length*32,60+cache.scopeGuards.length);
  const roots=currentNativeIdentity();
  if(!roots||Object.keys(roots).some(key=>roots[key]!==mark.native_identity[key])||
     cache!==nativeAutoCache||stage!==nativeAutoStage||
     !stage.stageAddress.readPointer().equals(stage.stagePointer)||
     nativeAutoEffective?.scope_key!==scope_key||nativeAutoEffective.mode!==collection_mode||
     scope_key!==cache.roots+':autochess:'+nativeAutoEpoch||
     cache.addresses.phase.readS32()!==phase||cache.addresses.round.readS32()!==round||
     ptr(roots.controller).add(probeAdapter.autoChessStateOffset).readS32()!==2||
     captureClockFrame()!==frame||captureClockFixed().toString()!==point.native_fixed_time)
   throw new Error('AutoChess presence binding changed');
  validateScopeStage(stage,roots.level,stage.stagePointer);
  validateScopeGuards(cache.scopeGuards);
  const closing=currentNativeIdentity();
  if(!closing||Object.keys(roots).some(key=>closing[key]!==roots[key])||
     cache.addresses.phase.readS32()!==phase||cache.addresses.round.readS32()!==round||
     !stage.stageAddress.readPointer().equals(stage.stagePointer)||
     ptr(roots.controller).add(probeAdapter.autoChessStateOffset).readS32()!==2||
     captureClockFrame()!==frame||captureClockFixed().toString()!==point.native_fixed_time)
   throw new Error('AutoChess presence scope or clock changed');
 }
 try{
  verifyBinding();
  const read=autoRead(check),controller=ptr(mark.native_identity.controller);
  read.object(controller,'BattleController','Torappu.Battle',515,[[100,'<unitManager>k__BackingField',0x2c0,[18]]]);
  const manager=read.p(read.at(controller,'<unitManager>k__BackingField'));
  read.object(manager,'UnitManager','Torappu.Battle',7,[[1,'<characters>k__BackingField',0x18,[21]]]);
  const container=read.p(read.at(manager,'<characters>k__BackingField'));
  read.object(container,'UnorderedArray`1','Torappu',3,[[0,'m_items',0x10,[29]],[2,'<count>k__BackingField',0x20,[8]]]);
  const items=read.p(read.at(container,'m_items')),count=read.i(read.at(container,'<count>k__BackingField'));
  if(count<0||count>AUTO_PRESENCE_SLOT_LIMIT||count&&items.isNull())return point;
  if(!items.isNull()){
   if(!autoIdentity(read.p(items),'Unit[]','Torappu.Battle'))return point;
   const capacity=read.n(items.add(0x18));
   if(capacity<count||capacity>AUTO_PRESENCE_SLOT_LIMIT)return point;
  }
  const units=[],chunks=[],objects=new Set(),instances=new Set(),members=new Map();
  for(const member of context.memberships){
   if(!member.is_token&&(collection_mode!=='local'||member.player_index===context.self_player_index))
    members.set(member.data_uid,member);
  }
  let characters=0;
  function readSlots(address,size){
   check(size);
   const raw=address.readByteArray(size);
   if(raw===null||raw.byteLength!==size)throw new Error('AutoChess presence slot read');
   return new DataView(raw);
  }
  for(let index=0;index<count;index+=16){
   const address=items.add(0x20+8*index),size=Math.min(16,count-index)*8,slots=readSlots(address,size);
   chunks.push([address,size,slots]);
   for(let offset=0;offset<size;offset+=8){
    const object=slots.getBigUint64(offset,true);
    if(!object||objects.has(object))return point;
    objects.add(object);
    const unit=ptr('0x'+object.toString(16)),lineage=autoPresenceLineage(read.p(unit),check);
    if(!lineage.character)continue;
    if(++characters>AUTO_PRESENCE_CHARACTER_LIMIT)return point;
    read.layout(unit,autoLayout(lineage.bObject,'BObject','Torappu.Battle',9,[
     [1,'<instanceUid>k__BackingField',0x18,[9]],[3,'<playerSide>k__BackingField',0x20,[17]],
     [4,'<inited>k__BackingField',0x24,[2]],[5,'<borned>k__BackingField',0x25,[2]],
     [6,'<isDisappeared>k__BackingField',0x26,[2]]],check));
    const inited=read.b(read.at(unit,'<inited>k__BackingField')),borned=read.b(read.at(unit,'<borned>k__BackingField')),
     disappeared=read.b(read.at(unit,'<isDisappeared>k__BackingField'));
    if(inited>1||borned>1||disappeared>1)return point;
    if(inited!==1||borned!==1||disappeared!==0)continue;
    const instance_uid=read.u(read.at(unit,'<instanceUid>k__BackingField')),
     player_side=read.i(read.at(unit,'<playerSide>k__BackingField'));
    if(!instance_uid||instances.has(instance_uid)||player_side<0||player_side>4)return point;
    instances.add(instance_uid);
    read.layout(unit,autoLayout(lineage.character,'Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,[18]]],check));
    const {cid,data_uid,is_token}=autoData(read.p(read.at(unit,'<data>k__BackingField')),read),member=members.get(data_uid);
    if(!is_token&&member?.cid===cid&&member.player_side===player_side)
     units.push({cid,data_uid,instance_uid,player_side});
   }
  }
  read.validate();
  for(const [address,size,before] of chunks){
   const after=readSlots(address,size);
   for(let offset=0;offset<size;offset+=8)
    if(before.getBigUint64(offset,true)!==after.getBigUint64(offset,true))return point;
  }
  verifyBinding();check();
  return {...point,status:'available',units};
 }catch(e){return point;}
}
let nativeAutoBodyCache=null;
function discardAutoChessBody(){
 nativeAutoBodyCache=null;
 // Only a full body validates the owner map used to omit unrelated events.
 // Event scope is separate and will run both of its live guards on each hit.
 nativeAutoOwners=null;
}
function prepareAutoChessRetryBody(prepareWarm=false){
 const cached=nativeAutoBodyCache;
 if(!cached||(!cached.prepareBeforeClock&&!prepareWarm)||!nativeAutoStage?.enabled)return null;
 try{
  const roots=currentNativeIdentity(),stage=nativeAutoStage;
  if(!roots||cached.generation!==nativeGeneration||cached.epoch!==nativeAutoEpoch)return null;
  const prefix=[roots.controller,roots.native,roots.logger,roots.stats,roots.level].join(':')+':';
  if(!stage.key.startsWith(prefix)||!stage.stageAddress.readPointer().equals(stage.stagePointer)
     ||cached.key!==stage.key+'#'+stage.stagePointer.toString())return null;
  // This pass just selects a candidate. The next pass reads every witness
  // again inside its OWN original clock and root bracket, including every
  // input to a failed token-host lookup. No amount or owner is published here.
  cached.body.read.validate();
  return cached;
 }catch(e){
  discardAutoChessBody();return null;
 }
}
function readAutoChessBody(identity,frame,fixed,key,prepared){
 const cached=nativeAutoBodyCache;
 if(cached&&cached.key===key&&cached.generation===nativeGeneration&&cached.epoch===nativeAutoEpoch
    &&frame>=cached.frame&&BigInt(fixed)>=BigInt(cached.fixed)){
  if(prepared!==cached){
   try{cached.body.read.validate();}
   catch(e){discardAutoChessBody();return readAutoChess(identity,frame,fixed);}
  }
  try{cached.body.read.validate();}
  catch(e){discardAutoChessBody();return {error:String(e)};}
  return cached.body;
 }
 discardAutoChessBody();return readAutoChess(identity,frame,fixed);
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
function captureRootRead(side,read){return typeof probeAdapter.captureRootRead==='function'?probeAdapter.captureRootRead(side,read):read();}
function currentNativeIdentity(reject,side='open'){return captureRootRead(side,()=>currentNativeIdentityPoint(reject));}
function currentNativeIdentityPoint(reject){
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
function nativeBinding(prepared=null,captureRoundStart=false){
 return typeof probeAdapter.captureBindingRead==='function'?probeAdapter.captureBindingRead(()=>nativeBindingPoint(prepared,captureRoundStart)):nativeBindingPoint(prepared,captureRoundStart);
}
function nativeBindingPoint(prepared=null,captureRoundStart=false){
 // Read the same five ownership roots and exact Q32.32 clock as the guarded
 // Windows sampler. The host binds this evidence to its battle serial.
 let step='root_identity';nativeAutoBindingDiagnostic=null;
 const unavailable=(reason,guard)=>{
  discardAutoChessBody();
  nativeAutoBindingDiagnostic={status:'unavailable',step,reason:String(reason).slice(0,300)};
  if(guard)nativeAutoBindingDiagnostic.guard=guard;
  return null;
 };
 const rejectRoot=(guard,actualClass)=>{
  const observed=typeof actualClass==='string'&&/^[A-Za-z0-9_`.<>+]{1,96}$/.test(actualClass)?
   ' (observed '+actualClass+')':'';
  nativeAutoCache=null;
  return unavailable(nativeRootFailures[guard]+observed,guard);
 };
 try{
  const before=currentNativeIdentity(rejectRoot);
  if(before===null)return null;
  // Android supplies the same effective state slot as the guarded host.
  // Both reads still bracket the ownership roots and exact game clock.
  step='controller_state';
  const stateOffset=probeAdapter.autoChessStateOffset??0x228;
  const stateAddress=ptr(before.controller).add(stateOffset),state=captureRootRead('open',()=>stateAddress.readS32());
  if(state!==2&&state!==3){
   nativeAutoCache=null;
   return unavailable('BattleController is not PLAYING or FINISHED');
  }
  if(state!==2)prepared=null;
  step='stage_metadata';
  const level=ptr(before.level),levelClass=probeAdapter.autoChessReflected?level.readPointer():null;
  const stageAddress=level.add(probeAdapter.autoChessStageOffset??0x18),stagePointer=captureRootRead('open',()=>stageAddress.readPointer()),stage=captureRootRead('open',()=>str(stagePointer));
  if(typeof stage!=='string'||!stage.length)return unavailable('LevelData stage identifier unavailable');
  const frame=captureClockFrame(),fixed=captureClockFixed().toString();
  const enabled=/AUTOCHESS/i.test(stage);
  let stageClass=null;
  if(enabled&&probeAdapter.autoChessReflected){
   stageClass=stagePointer.readPointer();
   if(!autoIdentity(stageClass,'String','System'))return unavailable('AutoChess stage identifier is not System.String');
  }
  const stageKey=[before.controller,before.native,before.logger,before.stats,before.level,stage].join(':');
  if(nativeAutoStage?.key!==stageKey||!nativeAutoStage.stagePointer.equals(stagePointer)){
   nativeAutoCache=null;discardAutoChessBody();
  }
  nativeAutoStage={key:stageKey,enabled,stageAddress,levelClass,stageClass,stagePointer};
  const bodyKey=stageKey+'#'+nativeAutoStage.stagePointer.toString();
  if(!enabled)discardAutoChessBody();
  const auto=enabled?readAutoChessBody(before,frame,fixed,bodyKey,prepared):null;
  const viewMode=probeAdapter.autoChessReflected&&auto?.header?.phase===4?readAutoChessViewMode(auto.header.manager):null;
  step='closing_guard';
  if(probeAdapter.autoChessReflected&&(!level.readPointer().equals(levelClass)||
     !stageAddress.readPointer().equals(stagePointer)||str(stagePointer)!==stage||
     enabled&&!stagePointer.readPointer().equals(stageClass))){
   nativeAutoCache=null;
   return unavailable('LevelData or stage identity changed during binding');
  }
  const after=currentNativeIdentity(rejectRoot,'close');
  if(after===null)return null;
  const endFrame=captureClockFrame(),endFixed=captureClockFixed().toString();
  const endStagePointer=probeAdapter.autoChessReflected?stagePointer:captureRootRead('close',()=>stageAddress.readPointer());
  if(Object.keys(before).some(key=>before[key]!==after[key])
     ||captureRootRead('close',()=>stateAddress.readS32())!==state||!endStagePointer.equals(stagePointer)||
     !probeAdapter.autoChessReflected&&captureRootRead('close',()=>str(endStagePointer))!==stage){
   nativeAutoCache=null;
   return unavailable('battle roots, clock, state or stage changed during binding');
  }
  if(endFrame!==frame||endFixed!==fixed){
   const rejected=unavailable('battle clock changed during binding','clock_changed');
   const advanced=endFrame>frame&&BigInt(fixed)>=0n&&BigInt(endFixed)>BigInt(fixed);
   if(!advanced)nativeAutoCache=null;
   if(state===2&&!probeAdapter.autoChessReflected&&auto?.allInputsGuarded&&advanced){
    // Save only a candidate for readAutoChessBody's TWO fresh validation
    // passes. A slow discovery spanning a tick cannot publish members or
    // filter events. Repeating the full discovery would starve every retry.
    // A crossed tick rejects this sample. Later events can use only the
    // previous independently committed plan, with both live scope checks.
    nativeAutoBodyCache={key:bodyKey,generation:nativeGeneration,epoch:nativeAutoEpoch,
     frame:endFrame,fixed:endFixed,body:auto,prepareBeforeClock:true};
   }
   return rejected;
  }
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
   native_frame:frame,native_fixed_time:fixed,native_stage_id:stage,native_battle_state:state};
  if(enabled){
   result.native_autochess_context=finishAutoChess(auto,before,frame,fixed);
   if(captureRoundStart){
    result.live_character_collection_empty=auto.liveCharacterCollectionEmpty??null;
    result.live_character_data_uids=auto.liveCharacterDataUids??null;
    result.lifecycle_baseline=characterLifecycleReady()&&auto.lifecycleBaseline
     ?{...auto.lifecycleBaseline,character_lifecycle_ready:true}:null;
   }
   if(viewMode)result.autochess_view_mode=viewMode;
   const preparedHere=prepared!==null&&prepared===nativeAutoBodyCache&&prepared.body===auto;
   // A missing host can be a guarded negative result. Keep it only as a
   // candidate for two fresh passes, just like the forward-clock retry body.
   const prepareBeforeClock=preparedHere||!probeAdapter.autoChessReflected&&auto.allInputsGuarded&&auto.unknown.length>0;
   nativeAutoBodyCache=auto.allInputsGuarded&&(auto.unknown.length===0||prepareBeforeClock)?
    {key:bodyKey,generation:nativeGeneration,epoch:nativeAutoEpoch,frame,fixed,body:auto,
     prepareBeforeClock}:null;
  }
  probeAdapter.publishCapturePublication?.(result);
  return result;
 }catch(e){return unavailable(e);}
}
