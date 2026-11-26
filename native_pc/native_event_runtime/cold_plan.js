// Setup-only metadata closure and a bounded layout tree. No player attribution,
// amount calculation or lifecycle decisions live in this module.
'use strict';
const __arkdpsColdMetadata=new Map();
function __arkdpsColdMetadataSeal(address,size){
 const key=address.toString()+':'+size,bytes=address.readByteArray(size);
 if(!(bytes instanceof ArrayBuffer)||bytes.byteLength!==size)throw new Error('cold metadata unreadable');
 const previous=__arkdpsColdMetadata.get(key);
 if(previous){
  const a=new Uint8Array(previous),b=new Uint8Array(bytes);
  if(a.some((value,i)=>value!==b[i]))throw new Error('cold metadata changed during setup');
 }else __arkdpsColdMetadata.set(key,bytes.slice(0));
}
function __arkdpsColdMetadataRead(address,size){
 const key=address.toString()+':'+size,bytes=__arkdpsColdMetadata.get(key);
 if(!bytes){
  if(typeof __arkdpsReplayGap==='function'&&__arkdpsReplayActive())__arkdpsReplayGap('unverified cold metadata '+key);
  throw new Error('unverified cold metadata '+key);
 }
 return bytes.slice(0);
}
function __arkdpsPrepareColdMetadata(meta){
 const byName=new Map(),parents=new Set();
 const size=Memory.alloc(8),assemblies=api('il2cpp_domain_get_assemblies','pointer',['pointer','pointer'])(domain,size);
 const imageOf=api('il2cpp_assembly_get_image','pointer',['pointer']),imageName=api('il2cpp_image_get_name','pointer',['pointer']);
 const countOf=api('il2cpp_image_get_class_count','size_t',['pointer']),classAt=api('il2cpp_image_get_class','pointer',['pointer','size_t']);
 const fromType=api('il2cpp_class_from_type','pointer',['pointer']);
 const warmName=k=>{
  if(!k||k.isNull())return;
  className(k);classNamespace(k);
  if(!parents.has(k.toString())){
   parents.add(k.toString());__arkdpsColdMetadataSeal(k.add(0x58),8);
   warmName(k.add(0x58).readPointer());
  }
 };
 for(let a=0,n=size.readU64().toNumber();a<n;a++){
  const image=imageOf(assemblies.add(a*8).readPointer());
  if(imageName(image).readCString()!=='Assembly-CSharp.dll')continue;
  for(let i=0,n=countOf(image);i<n;i++){
   const k=classAt(image,i);if(k.isNull())continue;
   warmName(k);const key=classNamespace(k)+':'+className(k);
   if(byName.has(key))byName.set(key,null);else byName.set(key,k);
  }
 }
 const need=(name,ns)=>{
  const k=byName.get(ns+':'+name);if(!k)throw new Error('cold metadata class missing or ambiguous '+ns+'.'+name);return k;
 };
 const layout=(k,name,ns,count,specs)=>{
  warmName(k);classFieldOffsets(k); // Expand lazy metadata before hooks enable.
  return autoLayout(k,name,ns,count,specs);
 };
 const concrete=field=>{const k=fromType(field.type);if(k.isNull())throw new Error('cold field type unavailable');warmName(k);return k;};
 const generic=element=>{
  const k=fromType(element);if(k.isNull())throw new Error('cold element type unavailable');warmName(k);
  __arkdpsColdMetadataSeal(element,8);__arkdpsColdMetadataSeal(element.add(8),4);
  const genericClass=element.readPointer();__arkdpsColdMetadataSeal(genericClass.add(24),8);
  if(!genericClass.add(24).readPointer().equals(k))throw new Error('cold generic cache mismatch');
  return k;
 };
 function list(k,listdict=false){
  warmName(k);
  if(listdict){
   if(!autoIdentity(k,'ListDict`2','Torappu'))throw new Error('cold ListDict type');
   classFieldOffsets(k);__arkdpsColdMetadataSeal(k.add(0x124),2);
   if(k.add(0x124).readU16()!==1)throw new Error('cold ListDict field count');
   __arkdpsColdMetadataSeal(k.add(0x58),8);k=k.add(0x58).readPointer();
  }
  const l=layout(k,'List`1','System.Collections.Generic',6,[[1,'_items',0x10,[29]],[2,'_size',0x18,[8]],[3,'_version',0x1c,[8]]]);
  concrete(l._items);return l;
 }
 function pairs(k,kinds){
  const l=list(k,true),pair=generic(l._items.data);
  return layout(pair,'KeyValuePair`2','System.Collections.Generic',2,[[0,'key',0x10,[8]],[1,'value',0x18,kinds]]);
 }
 const ns='Torappu.Battle.AutoChess',classes={...meta.found};
 const specs=[
  ['SingletonHost','',1,[[0,'m_instStore',0x10,[21]]]],
  ['AutoChessGameManager',ns,152,[[12,'gameStatus',0x38,[18]],[13,'dataCenter',0x40,[18]]]],
  ['AutoChessGameStatus',ns,62,[[0,'m_stateModel',0x10,[18]]]],
  ['AutoChessGameStateModel','',13,[[0,'m_stateMachine',0x18,[18]]]],
  ['AutoChessGameStateMachine','',13,[[1,'<gameCurrentStateId>k__BackingField',0x5c,[17]]]],
  ['AutoChessDataCenter','Torappu.Battle.DataCenter',151,[[5,'<selfPlayerIndex>k__BackingField',0x38,[8]],[6,'<currentRound>k__BackingField',0x3c,[8]],[28,'battleData',0xe8,[18]],[30,'playerDataModel',0xf8,[18]]]],
  ['AutoChessPlayerDataModel',ns,17,[[0,'m_allPlayerDatas',0x18,[21]]]],
  ['ScenePlayerData','',37,[[0,'index',0x10,[8]],[1,'uid',0x18,[14]],[6,'playerCard',0x40,[18]]]],
  ['PlayerCard',ns,6,[[0,'nickName',0x10,[14]],[1,'nickNumber',0x18,[14]]]],
  ['AutoChessBattleDataModel',ns,53,[[1,'battleChessInsts',0x20,[21]]]],
  ['ChessInst',ns,27,[[6,'playerIndex',0x30,[8]],[8,'side',0x40,[17]],[10,'battleCharacterData',0x48,[18]]]],
  ['UnitManager','Torappu.Battle',7,[[1,'<characters>k__BackingField',0x18,[21]]]],
  ['BattleCharacterData','Torappu.Battle',36,[[4,'prefabKey',0x50,[14]],[8,'uniqueId',0x6c,[9]],[14,'isToken',0x90,[2]],[15,'isPredefined',0x91,[2]],[18,'tokenOrHostKey',0x98,[14]],[19,'tokenOrHostUniqueId',0xa0,[9]]]],
 ];
 const layouts={};
 for(const [name,space,count,spec]of specs){classes[name]=need(name,space);layouts[name]=layout(classes[name],name,space,count,spec);}
 concrete(layouts.BattleCharacterData.prefabKey); // Also freeze System.String identity/parent.
 layout(classes.BattleController,'BattleController','Torappu.Battle',515,[[110,'m_singletonHost',0x2e8,[18]]]);
 layout(classes.BattleController,'BattleController','Torappu.Battle',515,[[100,'<unitManager>k__BackingField',0x2c0,[18]]]);
 layout(classes.Character,'Character','Torappu.Battle',356,[[78,'<data>k__BackingField',0x538,[18]]]);
 const bspec=[[1,'<instanceUid>k__BackingField',0x18,[9]],[3,'<playerSide>k__BackingField',0x20,[17]],
  [4,'<inited>k__BackingField',0x24,[2]],[5,'<borned>k__BackingField',0x25,[2]],[6,'<isDisappeared>k__BackingField',0x26,[2]]];
 layout(classes.BObject,'BObject','Torappu.Battle',9,bspec);
 layout(classes.BObject,'BObject','Torappu.Battle',9,bspec.slice(1));
 const dictionary=concrete(layouts.SingletonHost.m_instStore);
 const d=layout(dictionary,'Dictionary`2','System.Collections.Generic',14,[[1,'_entries',0x18,[29]],[2,'_count',0x20,[8]],[4,'_freeCount',0x28,[8]],[5,'_version',0x2c,[8]]]);
 concrete(d._entries);const entry=generic(d._entries.data);
 layout(entry,'Entry','',4,[[0,'hashCode',0x10,[8]],[1,'next',0x14,[8]],[2,'key',0x18,[18]],[3,'value',0x20,[28,18]]]);
 pairs(concrete(layouts.AutoChessPlayerDataModel.m_allPlayerDatas),[18]);
 const group=pairs(concrete(layouts.AutoChessBattleDataModel.battleChessInsts),[21]);
 list(concrete(group.value));
 const unordered=concrete(layouts.UnitManager['<characters>k__BackingField']);
 const u=layout(unordered,'UnorderedArray`1','Torappu',3,[[0,'m_items',0x10,[29]],[2,'<count>k__BackingField',0x20,[8]]]);concrete(u.m_items);
 return {classes:Object.fromEntries(Object.entries(classes).filter(([,k])=>k&&!k.isNull()).map(([name,k])=>[name,k.toString()])),
  cold_metadata_ready:true};
}
function __arkdpsBuildColdCapturePlan(metadata){
 if(metadata.cold_metadata_ready!==true)throw new Error('cold capture metadata was not verified');
 const klass=name=>{const k=metadata.classes[name];if(typeof k!=='string'||!/^0x[0-9a-f]+$/i.test(k))throw new Error('cold class '+name);return k;};
 const object=(path,bytes,children=[],extra={})=>({kind:'object',pointer_offsets:path,offset:0,bytes,max_visits:1,children,...extra});
 const scalar=(offset,bytes,max_visits=1)=>object([],bytes,[],{offset,max_visits});
 const text=(path,max_visits,allow_null=false)=>({kind:'utf16',pointer_offsets:path,offset:0,bytes:0,max_visits,
  expected_class:metadata.string_class,max_units:128,allow_null,children:[]});
 const array=(path,count_offset,items_offset,element_bytes,max_count,max_visits,children,extra={})=>({kind:'array',pointer_offsets:path,
  offset:0,bytes:0,count_offset,items_offset,data_offset:0x20,element_bytes,max_count,max_visits,children,...extra});
 const typed=(name,path,bytes,children,extra={})=>object(path,bytes,children,{gate:'exact',expected_class:klass(name),...extra});
 const arrayHeader=(max_visits)=>object([0x10],32,[],{max_visits,allow_null:true});
 const data=(path,max_visits)=>typed('BattleCharacterData',path,8,[scalar(0x50,32,max_visits),scalar(0x90,2,max_visits),scalar(0x98,12,max_visits),
  text([0x50],max_visits),text([0x98],max_visits,true)],{max_visits});
 const card=typed('PlayerCard',[0x40],32,[text([0x10],8,true),text([0x18],8,true)],{max_visits:8,allow_null:true});
 const scene=typed('ScenePlayerData',[8],32,[text([0x18],8),card],{max_visits:8});
 const playerList=object([0x18],32,[arrayHeader(1),array([],0x18,0x10,16,8,1,[scene])]);
 const playerModel=typed('AutoChessPlayerDataModel',[0xf8],8,[playerList]);
 const chess=typed('ChessInst',[0],80,[data([0x48],256)],{max_visits:256});
 const chessList=object([8],32,[arrayHeader(8),array([],0x18,0x10,8,64,8,[chess])],{max_visits:8});
 const groupList=object([0x20],32,[arrayHeader(1),array([],0x18,0x10,16,8,1,[chessList])]);
 const battle=typed('AutoChessBattleDataModel',[0xe8],8,[groupList]);
 const center=typed('AutoChessDataCenter',[0x40],8,[scalar(0x38,8),playerModel,battle]);
 const machine=typed('AutoChessGameStateMachine',[0x18],96,[]);
 const model=typed('AutoChessGameStateModel',[0x10],8,[machine]);
 const status=typed('AutoChessGameStatus',[0x38],8,[model]);
 // A mixed dictionary may contain 32 other singleton classes. The native
 // exact-class gate copies only their class, then skips the manager subtree.
 const manager=typed('AutoChessGameManager',[16],8,[status,center],{max_visits:32,allow_null:true});
 const dict=object([0x10],48,[object([0x18],32),array([],0x20,0x18,24,32,1,[manager],{skip_negative_i32_offset:0})]);
 const host=typed('SingletonHost',[0x2e8],8,[dict]);
 const character=object([0],40,[data([0x538],128)],{gate:'ancestor',expected_class:klass('Character'),ancestry_depth:10,max_visits:128});
 const container=object([0x18],36,[arrayHeader(1),array([],0x20,0x10,8,128,1,[character])]);
 const units=typed('UnitManager',[0x2c0],8,[container]);
 const root=typed('BattleController',[0x58,0xb8,0],8,[object([0x10],48),object([0xe0],56,[object([0x30],8)]),
  object([0x160],8,[text([0x18],1)]),scalar(0x228,4)]);
 const autoRoot=typed('BattleController',[0x58,0xb8,0],8,[host,units]);
 const projections=[];
 function flatten(node){
  const index=projections.length;projections.push(null);
  function fill(at,current){
   const {children,...definition}=current,begin=projections.length;
   for(const child of children)projections.push(null);
   projections[at]={...definition,child_begin:begin,child_count:children.length};
   children.forEach((child,i)=>fill(begin+i,child));
  }
  fill(index,node);return index;
 }
 const projection_root=flatten(root);
 const auto_root=flatten(autoRoot);
 const field=(field_id,name,projection_root,required)=>({field_id,phase:'checkpoint',role:'cold.body',name,root:'absolute',root_index:0,
  absolute_address:klass('BattleController'),pointer_offsets:[],offset:0,bytes:0,kind:'projection_tree',projection_root,
  guard_id:0,guard_side:'none',required});
 return {version:1,projections,fields:[field(1,'cold.native_binding',projection_root,true),field(2,'cold.autochess',auto_root,false)],
  max_bytes:1048576,max_reads:65536};
}
