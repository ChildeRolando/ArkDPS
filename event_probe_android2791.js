// Research observer for the measured Android 2.7.91 ARM64 layout.
// Interceptors observe calls only: no argument, return-value, or gameplay writes.
if(Process.arch!=='arm64')throw new Error('ARM64 emulated realm required');
const il=Process.getModuleByName('libil2cpp.so');
function api(n,r,a){return new NativeFunction(il.getExportByName(n),r,a);}
function className(k){return k.add(0x10).readPointer().readCString();}
function fieldOffsets(k,names){
 const a=k.add(0x80).readPointer(),n=k.add(0x124).readU16(),seen={};
 if(n>1000)throw new Error('invalid field metadata');
 for(let i=0;i<n;i++){const f=a.add(i*32);if(!f.add(16).readPointer().equals(k))throw new Error('field owner mismatch');seen[f.readPointer().readCString()]=f.add(24).readS32();}
 const out={};for(const name of names){if(!(name in seen))throw new Error('missing field '+className(k)+'.'+name);out[name]=seen[name];}
 return out;
}
function expectFields(k,expected){
 const a=k.add(0x80).readPointer(),n=k.add(0x124).readU16(),seen={};
 if(n>1000)throw new Error('invalid field metadata');
 for(let i=0;i<n;i++){const f=a.add(i*32);if(!f.add(16).readPointer().equals(k))throw new Error('field owner mismatch');seen[f.readPointer().readCString()]=f.add(24).readS32();}
 for(const [name,offset] of Object.entries(expected))if(seen[name]!==offset)throw new Error('unsupported layout '+className(k)+'.'+name);
}
const domain=api('il2cpp_domain_get','pointer',[])();
const thread=api('il2cpp_thread_attach','pointer',['pointer'])(domain);
const cfg={methods:[]};let clock;let skillF=null;
let enemyEntryConfig=null,enemyPhaseMethods=null,enemyStateMethods=null,enemyAttributesReady=false;
function uniqueMethod(klass,name,argCount) {
 const methods=klass.add(0x98).readPointer(),count=klass.add(0x120).readU16(),matches=[];
 const parameterCount=api('il2cpp_method_get_param_count','uint',['pointer']);
 for(let i=0;i<count;i++){
  const method=methods.add(i*8).readPointer();
  if(method.add(0x20).readPointer().equals(klass)&&method.add(0x18).readPointer().readCString()===name&&parameterCount(method)===argCount)
   matches.push(method.readPointer());
 }
 if(matches.length!==1||matches[0].isNull()||Process.findModuleByAddress(matches[0])?.name!==il.name)
  throw new Error('missing or ambiguous method '+name);
 return matches[0];
}
try{
 const size=Memory.alloc(8), assemblies=api('il2cpp_domain_get_assemblies','pointer',['pointer','pointer'])(domain,size);
 const imageOf=api('il2cpp_assembly_get_image','pointer',['pointer']), imageName=api('il2cpp_image_get_name','pointer',['pointer']);
 const classCount=api('il2cpp_image_get_class_count','ulong',['pointer']),classAt=api('il2cpp_image_get_class','pointer',['pointer','ulong']);
 let im;
 for(let i=0;i<size.readU64().toNumber();i++){const x=imageOf(assemblies.add(i*8).readPointer());if(imageName(x).readCString()==='Assembly-CSharp.dll'){im=x;break;}}
 if(!im)throw new Error('Assembly-CSharp not found');
 const wanted=new Set(['Entity','Unit','Buff','BuffContainer','BlockDamage','Modifier','BattleController',
  'Character','BasicSkill','SkillData']);const found={};
 const enemyClasses=new Set(['Enemy','BObject','Attributes']);
 for(let i=0,n=classCount(im);i<n;i++){
  const k=classAt(im,i),name=className(k);if(!wanted.has(name)&&!enemyClasses.has(name))continue;
  const ns=k.add(0x18).readPointer().readCString();
  const expectedNs=(['BuffContainer','BlockDamage'].includes(name)?'':
    (name==='SkillData'?'Torappu':'Torappu.Battle'));
  if(ns!==expectedNs)continue;
  if(name in found){
   if(wanted.has(name))throw new Error('ambiguous class '+name);
   found[name]=null;
  }else found[name]=k;
 }
 for(const name of wanted)if(!found[name])throw new Error('class not found '+name);
 expectFields(found.Entity,{'m_hp':0x40,'<id>k__BackingField':0x148});
 expectFields(found.Buff,{'m_source':0x18,'m_blackboard':0xb8,'<key>k__BackingField':0x208,'<instanceUid>k__BackingField':0x218,'<hasShield>k__BackingField':0x27c});
 expectFields(found.Modifier,{'m_source':0x10,'m_target':0x20,'m_originalSource':0x30,'m_value':0x40,'realDelta':0x58,'<targetType>k__BackingField':0x70,'<damageType>k__BackingField':0x78,'<cancelReason>k__BackingField':0x80});
 // FIXED_FRAME_COUNT is verified from the current class metadata, not an old battle object.
 expectFields(found.BattleController,{'s_fixedFrameCnt':0x14,'s_fixedPlayTime':0x18});
 const CF=fieldOffsets(found.Character,['m_skill']);
 const BF=fieldOffsets(found.BasicSkill,['m_owner','m_data']);
 const SF=fieldOffsets(found.SkillData,['prefabKey','skillId','name']);
 skillF={charSkill:CF.m_skill,owner:BF.m_owner,data:BF.m_data,
  prefab:SF.prefabKey,id:SF.skillId,name:SF.name};
 clock=found.BattleController.add(0xb8).readPointer().add(0x14);
 // Init and movement share the layer update, but Init can find a tile before
 // Born completes. Observe both transitions and emit once both are true.
 try{
  if(!found.Enemy||!found.BObject)throw new Error('enemy entry classes unavailable');
  expectFields(found.Enemy,{'m_currentTile':0x360});
  expectFields(found.BObject,{'<instanceUid>k__BackingField':0x18,
   '<borned>k__BackingField':0x25,'<isDisappeared>k__BackingField':0x26});
  enemyEntryConfig={enemy:found.Enemy,bObject:found.BObject,
   assignable:api('il2cpp_class_is_assignable_from','bool',['pointer','pointer']),
   layer:uniqueMethod(found.Enemy,'_UpdatePlayerSideAndMapLayer',0),
   born:uniqueMethod(found.BObject,'Born',0),
   recycle:uniqueMethod(found.BObject,'OnRecycle',0)};
 }catch(e){cfg.enemyEntryError=String(e);}
 if(enemyEntryConfig){
  try{
   if(!found.Attributes)throw new Error('enemy attribute class unavailable');
   expectFields(found.Entity,{'m_attributes':0xb0});
   expectFields(found.Attributes,{'m_cachedData':0x68});
   enemyAttributesReady=true;
  }catch(e){/* Enemy identity is still observable. */}
  try{enemyPhaseMethods={fakeDeath:uniqueMethod(found.Enemy,'DoFakeDeath',1),
    reborn:uniqueMethod(found.Enemy,'DoReborn',1)};}
  catch(e){cfg.enemyPhaseError=String(e);}
  try{
   expectFields(found.Entity,{'<finishReason>k__BackingField':0x158});
   enemyStateMethods={finish:uniqueMethod(found.Enemy,'OnFinish',1),
    hp:uniqueMethod(found.Entity,'SetHpInternal',4),
    directHp:uniqueMethod(found.Entity,'SetHpInternalDirectly',1)};
  }catch(e){cfg.enemyStateError=String(e);}
  if(enemyStateMethods){
   try{enemyStateMethods.attributes=uniqueMethod(found.Entity,'OnAttributeDirty',2);}
   catch(e){cfg.enemyMaxHpError=String(e);}
   try{enemyStateMethods.trapFinish=uniqueMethod(found.Entity,'OnFinish',1);}
   catch(e){cfg.stageObjectFinishError=String(e);}
  }
 }
 for(const [owner,k] of Object.entries(found)){
  if(['Modifier','BattleController'].includes(owner)||enemyClasses.has(owner))continue;
  const ma=k.add(0x98).readPointer(),mn=k.add(0x120).readU16();if(mn>2000)throw new Error('method count invalid');
  for(let i=0;i<mn;i++){
   const m=ma.add(i*8).readPointer();if(!m.add(0x20).readPointer().equals(k))continue;
   const name=m.add(0x18).readPointer().readCString();
   const skillStart=['OnSkillStart','SwitchToSkillState','OnSkillCastSucceed','OnToggleSkillStart','OnSkillRetriggered'];
   const skillEnd=['OnSkillFinish','SwitchOutFromSkillState','OnAfterSkill'];
   if(['OnTakeDamage','OnBlockDamage'].includes(name) || owner==='BlockDamage'&&['Execute','_AssignDynamicVar'].includes(name) || (owner==='Entity'||owner==='Unit')&&['ApplyModifier','OnAppliedModifier','OnTakeHeal','OnTakeDamage','OnAfterOutputDamage'].includes(name) || owner==='Character'&&(skillStart.includes(name)||skillEnd.includes(name))) cfg.methods.push({owner,name,address:m.readPointer().toString()});
  }
 }
 for(const key of ['Entity.ApplyModifier','Buff.OnTakeDamage','BlockDamage._AssignDynamicVar'])if(!cfg.methods.some(m=>m.owner+'.'+m.name===key))throw new Error('required method missing '+key);
 const skillStarts=cfg.methods.filter(m=>m.owner==='Character'&&['OnSkillStart','SwitchToSkillState','OnSkillCastSucceed','OnToggleSkillStart','OnSkillRetriggered'].includes(m.name));
 const skillEnds=cfg.methods.filter(m=>m.owner==='Character'&&['OnSkillFinish','SwitchOutFromSkillState','OnAfterSkill'].includes(m.name));
 cfg.skillReady=skillStarts.length>0&&skillEnds.length>0;
 try{
  const finished=uniqueMethod(found.BattleController,'OnCharacterFinished',2);
  cfg.methods.push({owner:'BattleController',name:'OnCharacterFinished',address:finished.toString()});
 }catch(e){cfg.skillFinishError=String(e);}
} finally {api('il2cpp_thread_detach','void',['pointer'])(thread);}
// A callback increments epoch on entry and remains active until its native
// invocation returns. RPC marks are serialized with these JS callbacks.
let sequence=0, hpSequence=0, epoch=0, active=0, fatal=null, stopped=false;
const activeHooks=new Map();
let enemyEntryReady=false,enemyEntryUnknown=false,enemyEntryFrame=null;
const awaitingEnemyTile=new Map(),enemyClassCache=new Map();
let enemyPhaseReady=false,enemyPhaseUnknown=false;
const enemyHealthPhases=new Map(),pendingEnemyRebirth=new Map();
let enemyStateReady=false,enemyStateUnknown=false;
let enemyMaxHpReady=false;
const enemyHpWrites=new Map();
// Recoverable per-event gaps do not close the whole event stream.
const unknownSources=new Set();
const engagementUnknownSources=new Set();
let shieldUnknown=false;
let damageUnknown=false;
let engagementUnknown=false;
let skillUnknown=false;
// Recoverable unattributable callbacks are counted, not escalated globally.
let skippedUnattributed=0;
const EVENT_BUFFER_LIMIT=100000,HP_CONTEXT_BUFFER_LIMIT=10000;
const queue=[],hpContext=[];
let acknowledgedSequence=0,readThroughSequence=0,hpContextDropped=0;
const stacks=new Map(), hooks=[];
function str(p){if(p.isNull())return null;const n=p.add(0x10).readS32();if(n<0||n>200)throw new Error('bad string');return p.add(0x14).readUtf16String(n);}
function fp(p){return p.readS64().toNumber()/4294967296;}
function entityId(p){if(p.isNull())throw new Error('missing source entity');const id=str(p.add(0x148).readPointer());if(!id)throw new Error('missing source identity');return id;}
function time(allowRetry = false) {
 const frame = clock.readU32();
 const game_time = fp(clock.add(4));
 if (clock.readU32() !== frame) {
  if (allowRetry) return null;
  throw new Error('unstable callback clock');
 }
 if (!Number.isFinite(game_time) || game_time < 0) throw new Error('invalid clock');
 return {frame, game_time};
}
function buff(p){return {key:str(p.add(0x208).readPointer()),source:p.add(0x18).readPointer(),hasShield:p.add(0x27c).readU8()};}
// Modifier callbacks receive a pointer to m_source; the remaining fields follow
// the verified class metadata shifted by that header (value, realDelta, ...).
function targetHp(p){if(!p||p.isNull())return null;const value=fp(p.add(0x40));return Number.isFinite(value)&&value>=0&&value<1e9?value:null;}
function targetKey(p){if(!p||p.isNull())return null;try{const id=str(p.add(0x148).readPointer());return id||null;}catch(e){return null;}}
const shieldByFrame=new Map();
// Modifier& is borrowed only during the same-thread ApplyModifier invocation.
// The pointer may be reused later, so retain no target evidence after return.
const activeModifiers=new Map();
function damageModifier(thread,modifier){
 const scopes=activeModifiers.get(thread)||[];
 for(let index=scopes.length-1;index>=0;index--){
  if(scopes[index].mod.equals(modifier))return scopes[index];
 }
 return null;
}
function enterModifier(scope){
 const scopes=activeModifiers.get(scope.tid)||[];
 scopes.push(scope);activeModifiers.set(scope.tid,scopes);scope.tracksModifier=true;
}
function leaveModifier(scope){
 const scopes=activeModifiers.get(scope.tid);
 if(!scopes)return;
 const index=scopes.lastIndexOf(scope);
 if(index!==-1)scopes.splice(index,1);
 if(!scopes.length)activeModifiers.delete(scope.tid);
}
// Absorption observed in a frame but not verifiable for a target cannot prove
// that a lethal hit's excess was not absorbed, so its overkill stays unknown.
const shieldUnknownByFrame=new Map();
function markShieldUnknown(frame,target){
 if(!Number.isFinite(frame))return; // an unreadable clock emits no damage event either
 let set=shieldUnknownByFrame.get(frame);
 if(!set){set=new Set();shieldUnknownByFrame.set(frame,set);if(shieldUnknownByFrame.size>300)for(const old of shieldUnknownByFrame.keys())if(old<frame-300)shieldUnknownByFrame.delete(old);}
 set.add(target&&!target.isNull()?target.toString():'*');
}
function shieldContextUnknown(frame,target){
 const set=shieldUnknownByFrame.get(frame);
 return !!set&&(set.has('*')||(!!target&&!target.isNull()&&set.has(target.toString())));
}
function noteShield(frame,target,amount){if(!target||target.isNull())return;let map=shieldByFrame.get(frame);if(!map){map=new Map();shieldByFrame.set(frame,map);}const key=target.toString();map.set(key,(map.get(key)||0)+amount);if(shieldByFrame.size>300)for(const old of shieldByFrame.keys())if(old<frame-300)shieldByFrame.delete(old);}
function shieldTotal(frame,target){const map=shieldByFrame.get(frame);return map&&target?(map.get(target.toString())||0):0;}
// Only a fully verifiable HP settlement yields overkill evidence. Any missing or
// contradictory field (shared with Python) leaves the metric unknown, never zero.
function overkillEvidence(mod,targetPtr,hpBefore,frame){
 try{
  if(hpBefore===null||hpBefore===undefined||!targetPtr||targetPtr.isNull())return null;
  const hpAfter=targetHp(targetPtr),key=targetKey(targetPtr),real=fp(mod.add(0x48));
  if(!key||hpAfter===null||!Number.isFinite(real)||real>0)return null;
  if(Math.abs((hpBefore-hpAfter)+real)>Math.max(0.01,Math.abs(real)*1e-6))return null;
  // m_value is the incoming value before shield/block settlement (captured
  // frame 697: value 14, realDelta 0, HP unchanged, BlockDamage absorbed 14).
  // Python subtracts the verified absorption; an unverified frame sends none.
  const absorbed=shieldContextUnknown(frame,targetPtr)?null:shieldTotal(frame,targetPtr);
  return {target:key,real_delta:real,hp_before:hpBefore,hp_after:hpAfter,killed:hpAfter<=1e-6,
          target_type:mod.add(0x60).readS32(),damage_type:mod.add(0x68).readS32(),
          cancel_reason:mod.add(0x70).readS32(),shield_absorbed:absorbed};
 }catch(e){return null;}
}
function nonlethalEvidence(targetPtr){
 // A target that is still alive after this hit proves overkill=0, even if a
 // same-frame concurrent hit made HP-before/real-delta reconstruction fail.
 try{
  const hp=targetHp(targetPtr);
  if(hp===null||hp<=0)return null;
  const key=targetKey(targetPtr)||targetPtr.toString();
  return {target:key,hp_after:hp,killed:false};
 }catch(e){return null;}
}
function dynamic(p){const n=p.add(0x18).readS32();if(n<0||n>128)throw new Error('bad blackboard');const a=p.add(0x10).readPointer().add(0x20);for(let i=0;i<n;i++)if(str(a.add(i*24).readPointer())==='dynamic')return a.add(i*24+8).readFloat();throw new Error('missing shield value');}
function fixedArg(p){return Number(BigInt.asIntN(64,BigInt(p.toString())))/4294967296;}
function own(id){return id!==null&&(id.startsWith('char_')||id.startsWith('token_'));}
function record(source,kind,amount,when,proof,evidence){
 if(stopped||fatal)return;
 if(!Number.isFinite(amount)||amount<0)throw new Error('invalid amount');
 if(queue.length>=EVENT_BUFFER_LIMIT){
  // A later read or mark must expose the gap even if the emitting hook only
  // degrades its own diagnostic quality on failure.
  fatal='event buffer overflow: too many unacknowledged events';
  return;
 }
 if(sequence>=Number.MAX_SAFE_INTEGER){
  fatal='event sequence exhausted';
  return;
 }
 const event={sequence:++sequence,frame:when.frame,game_time:when.game_time,source,kind,amount,proof};
 if(evidence)Object.assign(event,evidence);
 queue.push(event);
}
function recordHpContext(context){
 if(stopped||fatal)return;
 if(hpContext.length>=HP_CONTEXT_BUFFER_LIMIT){
  hpContextDropped++;
  return;
 }
 // Diagnostics do not advance the authoritative sequence. Attach late
 // diagnostics to the next event so repeating a read returns the same slice.
 hpContext.push({eventSequence:Math.max(sequence,readThroughSequence+1),context});
}
function readEvents(after,through){
 const valid=Number.isSafeInteger(after)&&Number.isSafeInteger(through)&&
  acknowledgedSequence<=after&&after<=through&&through<=sequence;
 const error=fatal||(valid?null:'event read range is unavailable');
 const result={events:[],hp_context:[],hp_context_dropped:hpContextDropped,error,stopped,...eventQuality()};
 if(error||stopped)return result;
 result.events=queue.slice(after-acknowledgedSequence,through-acknowledgedSequence);
 result.hp_context=hpContext.filter(item=>after<item.eventSequence&&item.eventSequence<=through)
  .map(item=>item.context);
 readThroughSequence=Math.max(readThroughSequence,through);
 return result;
}
function ackEvents(through){
 if(!Number.isSafeInteger(through)||through<0||through>readThroughSequence)
  return {acknowledged:acknowledgedSequence,error:fatal||'event acknowledgement exceeds the read watermark'};
 if(through>acknowledgedSequence){
  queue.splice(0,through-acknowledgedSequence);
  acknowledgedSequence=through;
  let count=0;
  while(count<hpContext.length&&hpContext[count].eventSequence<=through)count++;
  hpContext.splice(0,count);
 }
 return {acknowledged:acknowledgedSequence,error:fatal};
}
function refreshEnemyEntryFrame(frame) {
 if(enemyEntryFrame!==null&&frame<enemyEntryFrame){
  awaitingEnemyTile.clear();
  enemyEntryUnknown=false;
  enemyHealthPhases.clear();
  pendingEnemyRebirth.clear();
  enemyPhaseUnknown=false;
  enemyHpWrites.clear();
  enemyStateUnknown=false;
 }
 enemyEntryFrame=frame;
}
function isEnemy(object) {
 const klass=object.readPointer();
 if(klass.equals(enemyEntryConfig.enemy))return true;
 const key=klass.toString();
 if(!enemyClassCache.has(key))enemyClassCache.set(key,enemyEntryConfig.assignable(enemyEntryConfig.enemy,klass));
 return enemyClassCache.get(key);
}
function stageTrapId(object) {
 const id=targetKey(object);
 return id?.startsWith('trap_')&&enemyEntryConfig.assignable(enemyEntryConfig.bObject,object.readPointer())?id:null;
}
function beginEnemyHook(label) {
 epoch++;active++;activeHooks.set(label,(activeHooks.get(label)||0)+1);
}
function endEnemyHook(label) {
 active--;
 const left=activeHooks.get(label)-1;
 if(left)activeHooks.set(label,left);else activeHooks.delete(label);
}
function enemyIdentity(enemy,uid) {
 if(!uid)throw new Error('missing enemy instance UID');
 return {target:entityId(enemy),target_instance:String(uid)};
}
function includeEnemyHp(evidence,enemy) {
 try{
  const hp=targetHp(enemy);
  if(hp!==null)evidence.hp_after=hp;
 }catch(e){/* Identity and lifecycle remain observable without current HP. */}
}
function omitPendingHpWrite(enemy) {
 const write=enemyHpWrites.get(enemy.toString());
 if(write)write.superseded=true;
}
const MAX_HP_ATTRIBUTE=0;
function enemyCachedAttributes(enemy) {
 const attributes=enemy.add(0xb0).readPointer();
 if(attributes.isNull())return null;
 const cached=attributes.add(0x68).readPointer();
 if(cached.isNull())return null;
 const count=cached.add(0x18).readU32();
 return count&&count<=64?{cached,count}:null;
}
function enemyAttribute(data,index) {
 if(index>=data.count)return null;
 const slot=data.cached.add(0x20+index*0x28);
 if(slot.add(0x10).readU8()!==1)return null;
 const key=BigInt(slot.readU64().toString()),hidden=BigInt(slot.add(8).readU64().toString());
 const value=Number(BigInt.asIntN(64,hidden^key))/4294967296;
 return Math.abs(value)<1e10&&(index!==MAX_HP_ATTRIBUTE||value>0)?value:null;
}
function enemyMaxHp(enemy) {
 if(!enemyAttributesReady)return null;
 try{
  const data=enemyCachedAttributes(enemy);
  return data?enemyAttribute(data,MAX_HP_ATTRIBUTE):null;
 }catch(e){return null;}
}
function enemyEntryAttributes(enemy) {
 if(!enemyAttributesReady)return null;
 const result={};
 try{
  const data=enemyCachedAttributes(enemy);
  if(!data)return null;
  // AttributeType slots in the verified ARM64 ObscuredFP[] layout.
  for(const [name,index] of [['max_hp',MAX_HP_ATTRIBUTE],['atk',1],['def',2],['res',3],['move_speed',6],['attack_speed',7]]){
   const value=enemyAttribute(data,index);
   if(value!==null)result[name]=value;
  }
 }catch(e){/* Keep any fields that were read successfully. */}
 return Object.keys(result).length?result:null;
}
function recordEnemyEntry(enemy,uid,when,method) {
 const evidence=enemyIdentity(enemy,uid);
 includeEnemyHp(evidence,enemy);
 const attributes=enemyEntryAttributes(enemy);
 if(attributes)evidence.enemy_entry_attributes=attributes;
 awaitingEnemyTile.delete(uid);
 record(null,'enemy_enter',0,when,{method},evidence);
}
function installEnemyEntryHooks() {
 if(!enemyEntryConfig)return;
 const installed=[];
 try{
  for(const [method,address] of [['_UpdatePlayerSideAndMapLayer',enemyEntryConfig.layer],['Born',enemyEntryConfig.born]]){
   const label='enemy entry '+method;
   const isBorn=method==='Born';
   installed.push(Interceptor.attach(address,{
    onEnter(args){
     if(stopped||fatal||!enemyEntryReady)return;
     try{
      const enemy=args[0];
      if(enemy.isNull()||(isBorn&&(!isEnemy(enemy)||enemy.add(0x25).readU8())))return;
      const uid=enemy.add(0x18).readU32();
      // A tile change is only an entry if we saw this object Born without a
      // tile. Moving, reappearing or attaching mid-battle cannot start a life.
      if(!isBorn&&!awaitingEnemyTile.get(uid)?.equals(enemy))return;
      const when=time();
      refreshEnemyEntryFrame(when.frame);
      if(!isBorn&&!awaitingEnemyTile.has(uid))return;
      this.enemy=enemy;this.uid=uid;this.when=when;
      beginEnemyHook(label);
     }catch(e){enemyEntryUnknown=true;}
    },
    onLeave(){
     if(!this.when)return;
     try{
      if(stopped||fatal)return;
      const when=time();
      if(when.frame!==this.when.frame||when.game_time!==this.when.game_time)
       throw new Error('enemy entry callback crossed frame');
      const enemy=this.enemy,uid=this.uid;
      if(enemy.add(0x18).readU32()!==uid)throw new Error('enemy identity changed during entry');
      if(enemy.add(0x26).readU8()){
       awaitingEnemyTile.delete(uid);
       return;
      }
      if(!enemy.add(0x25).readU8())return;
      if(isBorn&&enemyPhaseReady){
       const hp=targetHp(enemy);
       if(hp>0)enemyHealthPhases.set(uid,{total:hp,count:1,fromBirth:true});
      }
      if(enemy.add(0x360).readPointer().isNull()){
       if(isBorn)awaitingEnemyTile.set(uid,enemy);
       return;
      }
      if(!isBorn&&!awaitingEnemyTile.has(uid))return;
      recordEnemyEntry(enemy,uid,when,method);
     }catch(e){enemyEntryUnknown=true;}
     finally{endEnemyHook(label);}
    }
   }));
  }
  installed.push(Interceptor.attach(enemyEntryConfig.recycle,{
   onEnter(args){
     if(!awaitingEnemyTile.size&&!enemyHealthPhases.size&&!pendingEnemyRebirth.size&&!enemyHpWrites.size)return;
    try{
     const object=args[0],uid=object.add(0x18).readU32();
      // Retire an in-flight setter before the pooled pointer can be reused.
      enemyHpWrites.delete(object.toString());
     if(awaitingEnemyTile.get(uid)?.equals(object))awaitingEnemyTile.delete(uid);
     enemyHealthPhases.delete(uid);
     pendingEnemyRebirth.delete(uid);
    }catch(e){enemyEntryUnknown=true;}
   }
  }));
  hooks.push(...installed);
  enemyEntryReady=true;
 }catch(e){
  for(const hook of installed)hook.detach();
  cfg.enemyEntryError=String(e);
 }
}
function installEnemyPhaseHook() {
 if(!enemyEntryReady||!enemyPhaseMethods)return;
 const label='enemy phase DoReborn';
 const installed=[];
 try{
  installed.push(Interceptor.attach(enemyPhaseMethods.fakeDeath,{
   onEnter(args){
    if(stopped||fatal)return;
    try{
      const enemy=args[0],when=time(),uid=enemy.add(0x18).readU32();
      refreshEnemyEntryFrame(when.frame);
      if(pendingEnemyRebirth.has(uid))return;
      pendingEnemyRebirth.set(uid,'waiting');
      omitPendingHpWrite(enemy);
      this.started=true;beginEnemyHook('Enemy.DoFakeDeath');
      try{
       const evidence=enemyIdentity(enemy,uid);
       includeEnemyHp(evidence,enemy);
       record(null,'enemy_fake_death',0,when,{method:'Enemy.DoFakeDeath'},evidence);
      }catch(e){enemyStateUnknown=true;}
    }catch(e){enemyPhaseUnknown=true;}
    },
    onLeave(){
     if(this.started)endEnemyHook('Enemy.DoFakeDeath');
   }
  }));
  installed.push(Interceptor.attach(enemyPhaseMethods.reborn,{
   onEnter(args){
    if(stopped||fatal)return;
    try{
     const enemy=args[0],when=time();
     refreshEnemyEntryFrame(when.frame);
     this.uid=enemy.add(0x18).readU32();
      if(pendingEnemyRebirth.get(this.uid)!=='waiting'){
      enemyPhaseUnknown=true;
      return;
     }
      omitPendingHpWrite(enemy);
     this.hpBefore=targetHp(enemy);
      pendingEnemyRebirth.set(this.uid,'restoring');
     this.enemy=enemy;this.when=when;
      beginEnemyHook(label);
    }catch(e){enemyPhaseUnknown=true;}
   },
   onLeave(){
    if(!this.when)return;
    try{
     if(stopped||fatal)return;
     const when=time(),enemy=this.enemy,uid=this.uid;
     if(when.frame!==this.when.frame||when.game_time!==this.when.game_time||enemy.add(0x18).readU32()!==uid)
      throw new Error('enemy rebirth changed identity or crossed a frame');
      if(pendingEnemyRebirth.get(uid)!=='restoring')throw new Error('enemy rebirth was interrupted');
     const hp=targetHp(enemy);
     if(this.hpBefore===null||!(hp>0))throw new Error('enemy rebirth HP could not be confirmed');
     const old=enemyHealthPhases.get(uid)||{total:0,count:0,fromBirth:false};
     const phase={total:old.total+hp,count:old.count+1,fromBirth:old.fromBirth};
     const eid=entityId(enemy);
     try{
      record(null,'enemy_reborn',hp,when,{method:'Enemy.DoReborn'},
       {target:eid,target_instance:String(uid),hp_before:this.hpBefore,hp_after:hp,
        phase_total_hp:phase.total,phase_count:phase.count,phase_from_birth:phase.fromBirth});
      enemyHealthPhases.set(uid,phase);
     }catch(e){fatal=String(e);}
    }catch(e){enemyPhaseUnknown=true;}
     finally{
      if(pendingEnemyRebirth.get(this.uid)==='restoring')pendingEnemyRebirth.delete(this.uid);
      endEnemyHook(label);
     }
   }
  }));
  hooks.push(...installed);
  enemyPhaseReady=true;
 }catch(e){
  for(const hook of installed)hook.detach();
  cfg.enemyPhaseError=String(e);
 }
}
function installEnemyStateHooks() {
 if(!enemyEntryReady||!enemyStateMethods)return;
 const installed=[];
 try{
  const writes=[['Entity.SetHpInternal',enemyStateMethods.hp],
   ['Entity.SetHpInternalDirectly',enemyStateMethods.directHp]];
  if(enemyStateMethods.attributes)writes.push(['Entity.OnAttributeDirty',enemyStateMethods.attributes]);
  for(const [label,address] of writes){
   const attributeChange=label==='Entity.OnAttributeDirty';
   const callbacks={
    onEnter(args){
     if(stopped||fatal||(attributeChange&&args[1].toInt32()!==MAX_HP_ATTRIBUTE))return;
     let enemyTarget=true;
     try{
      const enemy=args[0],key=enemy.toString();
      if(enemy.isNull())return;
      const existing=enemyHpWrites.get(key);
      if(existing){
       if(attributeChange&&existing.enemyTarget&&existing.maxHpBefore===null)
        existing.maxHpBefore=fixedArg(args[2]);
       return;
      }
      enemyTarget=isEnemy(enemy);
      // Before Born, enemy HP initialization is covered by enemy_enter.
      // Stage traps never create an entry; the host admits them via snapshot.
      if(enemyTarget&&!enemy.add(0x25).readU8())return;
      if(!enemyTarget&&(attributeChange||!stageTrapId(enemy)))return;
      const when=time(),uid=enemy.add(0x18).readU32();
      refreshEnemyEntryFrame(when.frame);
      if(awaitingEnemyTile.has(uid)||pendingEnemyRebirth.has(uid))return;
      const evidence=enemyIdentity(enemy,uid),hpBefore=targetHp(enemy);
      if(hpBefore===null)throw new Error('enemy HP unreadable before setter');
      const maxHpBefore=attributeChange?fixedArg(args[2]):null;
      this.write={enemy,key,uid,when,evidence,hpBefore,maxHpBefore,enemyTarget,superseded:false};
      enemyHpWrites.set(key,this.write);
      beginEnemyHook(label);
     }catch(e){if(enemyTarget)enemyStateUnknown=true;}
    },
    onLeave(){
     const write=this.write;
     if(!write)return;
     try{
      const {enemy,key,uid,when,evidence,hpBefore,maxHpBefore,superseded}=write;
      if(enemyHpWrites.get(key)!==write)return;
      enemyHpWrites.delete(key);
      if(stopped||fatal||superseded)return;
      const end=time();
      if(end.frame!==when.frame||end.game_time!==when.game_time||enemy.add(0x18).readU32()!==uid)
       throw new Error('enemy HP callback changed identity or crossed a frame');
      const hp=targetHp(enemy);
      if(hp===null)throw new Error('enemy HP unreadable after callback');
      if(hp===hpBefore&&maxHpBefore===null)return;
      const maxHp=write.enemyTarget?enemyMaxHp(enemy):null;
      if(hp===hpBefore&&(maxHp===null||maxHp===maxHpBefore))return;
      if(maxHp!==null)evidence.enemy_max_hp=maxHp;
      record(null,'enemy_hp',0,end,{method:label},{...evidence,hp_before:hpBefore,hp_after:hp});
     }catch(e){if(write.enemyTarget)enemyStateUnknown=true;}
     finally{endEnemyHook(label);}
    }
   };
   try{
    installed.push(Interceptor.attach(address,callbacks));
    if(attributeChange)enemyMaxHpReady=enemyAttributesReady;
   }catch(e){
    if(!attributeChange)throw e;
    cfg.enemyMaxHpError=String(e);
   }
  }
  const label='Enemy.OnFinish';
  installed.push(Interceptor.attach(enemyStateMethods.finish,{
   onEnter(args){
    if(stopped||fatal)return;
    try{
     const enemy=args[0];
     if(enemy.isNull()||!isEnemy(enemy))return;
     const when=time(),uid=enemy.add(0x18).readU32();
     refreshEnemyEntryFrame(when.frame);
     // FinishMe sets the backing field before invoking this callback. The
     // instance may be recycled or have its fields cleared during the call.
     const reason=args[1].toInt32();
     if(reason===0||enemy.add(0x158).readS32()!==reason)throw new Error('enemy finish reason unavailable');
     const evidence={...enemyIdentity(enemy,uid),finish_reason:reason};
     includeEnemyHp(evidence,enemy);
     if(awaitingEnemyTile.get(uid)?.equals(enemy)){
      // A delayed tile assignment can finish the unit inside the layer hook,
      // before its onLeave sees the already committed entry state.
      if(enemy.add(0x25).readU8()&&!enemy.add(0x360).readPointer().isNull()){
       if(enemy.add(0x26).readU8())enemyEntryUnknown=true;
       else recordEnemyEntry(enemy,uid,when,label);
      }
      awaitingEnemyTile.delete(uid);
     }
     pendingEnemyRebirth.delete(uid);
     omitPendingHpWrite(enemy);
     this.started=true;beginEnemyHook(label);
     record(null,'enemy_finish',0,when,{method:label},evidence);
    }catch(e){enemyStateUnknown=true;}
   },
   onLeave(){
    if(this.started)endEnemyHook(label);
   }
  }));
  if(enemyStateMethods.trapFinish){
   const label='Entity.OnFinish';
   try{
    installed.push(Interceptor.attach(enemyStateMethods.trapFinish,{
     onEnter(args){
      if(stopped||fatal)return;
      try{
       const trap=args[0];
       if(trap.isNull()||isEnemy(trap)||!stageTrapId(trap))return;
       const uid=trap.add(0x18).readU32(),reason=args[1].toInt32();
       if(!reason||trap.add(0x158).readS32()!==reason)return;
       const when=time();
       refreshEnemyEntryFrame(when.frame);
       const evidence={...enemyIdentity(trap,uid),finish_reason:reason};
       includeEnemyHp(evidence,trap);
       omitPendingHpWrite(trap);
       this.started=true;beginEnemyHook(label);
       record(null,'enemy_finish',0,when,{method:label},evidence);
      }catch(e){/* Optional stage-object evidence is independent of enemy coverage. */}
     },
     onLeave(){
      if(this.started)endEnemyHook(label);
     }
    }));
   }catch(e){cfg.stageObjectFinishError=String(e);}
  }
  hooks.push(...installed);
  enemyStateReady=true;
 }catch(e){
  for(const hook of installed)hook.detach();
  enemyMaxHpReady=false;
  cfg.enemyStateError=String(e);
 }
}
const SKILL_START_METHODS=new Set(['OnSkillStart','SwitchToSkillState','OnSkillCastSucceed','OnToggleSkillStart','OnSkillRetriggered']);
const SKILL_END_METHODS=new Set(['OnSkillFinish','SwitchOutFromSkillState','OnAfterSkill']);
function skillIdentity(charPtr,skillPtr){
 try{
  const sk=(skillPtr&&!skillPtr.isNull())?skillPtr:charPtr.add(skillF.charSkill).readPointer();
  if(!sk||sk.isNull())return {};
  const data=sk.add(skillF.data).readPointer();
  if(!data||data.isNull())return {};
  const key=str(data.add(skillF.prefab).readPointer());
  const id=str(data.add(skillF.id).readPointer());
  const name=str(data.add(skillF.name).readPointer());
  return {skill_key:key||id||null,skill_id:id||null,skill_name:name||null};
 }catch(e){return {};}
}
for(const m of cfg.methods.filter(x=>x.owner==='Buff'&&x.name==='OnTakeDamage'||x.owner==='BlockDamage'&&x.name==='_AssignDynamicVar'||x.owner==='Entity'&&x.name==='ApplyModifier'||(x.owner==='Entity'||x.owner==='Unit')&&(x.name==='OnTakeDamage'||x.name==='OnAfterOutputDamage')||x.owner==='Character'||x.owner==='BattleController'&&x.name==='OnCharacterFinished')){
 hooks.push(Interceptor.attach(ptr(m.address),{
  onEnter(args){
   active++;epoch++;this.started=true;this.name=m.owner+'.'+m.name;this.tid=this.threadId;
   activeHooks.set(this.name,(activeHooks.get(this.name)||0)+1);
   try{
    if(this.name==='BattleController.OnCharacterFinished'){
     this.skillEnd=true;
     this.source=entityId(args[1]);
     // A deployable's removal does not end its owner's skill.
     if(!this.source.startsWith('char_')){this.ignore=true;return;}
     this.skillReason=args[2].toInt32();
     return;
    }
    if(this.name.startsWith('Character.')){
     const method=this.name.slice('Character.'.length);
     if(SKILL_START_METHODS.has(method)||SKILL_END_METHODS.has(method)){
      this.skillStart=SKILL_START_METHODS.has(method);this.skillEnd=!this.skillStart;
      try{
       this.source=entityId(args[0]);
       this.skill=skillIdentity(args[0],method==='OnAfterSkill'?args[1]:null);
       this.skillReason=(method==='OnAfterSkill'&&args[2])?args[2].toInt32():null;
      }catch(e){skillUnknown=true;this.ignore=true;}
      return;
     }
    }
    this.when=time();
    if(this.name==='Buff.OnTakeDamage'){const s=stacks.get(this.tid)||[];s.push(args[0]);stacks.set(this.tid,s);this.pushed=true;return;}
    if(this.name.endsWith('.OnAfterOutputDamage')){
     // args[0] is the hidden IL2CPP `this` receiver; modifier is args[1].
     this.mod=args[1];
     try{this.target=this.mod.add(0x10).readPointer();this.hpBefore=targetHp(this.target);}catch(e){this.ignore=true;return;}
     try{this.source=entityId(this.mod.readPointer());}catch(e){this.source=null;}
     try{this.amount=fp(this.mod.add(0x30));}catch(e){this.amount=null;}
     try{this.real=fp(this.mod.add(0x48));}catch(e){this.real=null;}
     this.modifierScope=damageModifier(this.tid,this.mod);
     return;
    }
    if(this.name==='Entity.OnTakeDamage'||this.name==='Unit.OnTakeDamage'){
     // Signature: OnTakeDamage(Modifier& modifier, bool force), invoked on the
     // target entity. args[0] is hidden `this`; modifier is args[1].
     // Keep target/HP even if the source identity read fails; never discard
     // the whole target-side evidence for that reason.
     this.receiver=args[0];this.mod=args[1];
     try{
      this.target=this.receiver&&!this.receiver.isNull()?this.receiver:this.mod.add(0x10).readPointer();
      this.hpBefore=targetHp(this.target);
     }catch(e){this.ignore=true;return;}
     try{this.source=entityId(this.mod.readPointer());}catch(e){this.source=null;}
     try{this.amount=fp(this.mod.add(0x30));}catch(e){this.amount=null;}
     try{this.real=fp(this.mod.add(0x48));}catch(e){this.real=null;}
     if(this.name==='Unit.OnTakeDamage'&&this.source){
      const scope=damageModifier(this.tid,this.mod);
      if(scope&&scope.source===this.source&&scope.when.frame===this.when.frame){
       if(scope.targetHit)scope.targetHitAmbiguous=true;
       else scope.targetHit={target:this.target,hpBefore:this.hpBefore};
      }
     }
     return;
    }
    if(this.name==='Entity.ApplyModifier'){
     this.mod=args[1];
     try{this.target=this.mod.add(0x10).readPointer();this.hpBefore=targetHp(this.target);}catch(e){this.target=null;this.hpBefore=null;}
     try{this.source=entityId(this.mod.readPointer());}catch(e){this.source=null;}
     if(this.source&&own(this.source))enterModifier(this);
     return;
    }
    const s=stacks.get(this.tid)||[];if(!s.length){shieldUnknown=true;markShieldUnknown(this.when.frame,null);this.ignore=true;return;}
    this.buff=buff(s[s.length-1]);this.bb=args[1];
    try{this.damageTarget=args[2].add(0x10).readPointer();}catch(e){this.damageTarget=null;}
    try{this.source=entityId(this.buff.source);}catch(e){shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);this.ignore=true;return;}
    // A non-player shield still absorbs damage dealt to that target, so the
    // hit's overkill cannot claim the absorbed part as its own excess.
    if(!own(this.source)){markShieldUnknown(this.when.frame,this.damageTarget);this.ignore=true;return;}
    if(this.buff.hasShield!==1||!['shining_s_2','catsld_t_1[core]'].includes(this.buff.key)){shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);this.ignore=true;return;}
    this.before=dynamic(this.bb);this.amount=fixedArg(args[4]);this.incoming=fp(args[2].add(0x30));
    if(!Number.isFinite(this.before)||this.before<0||!Number.isFinite(this.incoming)||this.incoming<0){shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);this.ignore=true;return;}
   }catch(e){
    if(this.skillStart||this.skillEnd){skillUnknown=true;}
    else if(this.name==='Entity.ApplyModifier'){if(this.source&&own(this.source))unknownSources.add(this.source);else{skippedUnattributed++;}}
    else{shieldUnknown=true;markShieldUnknown(this.when?this.when.frame:null,this.damageTarget);}
    this.ignore=true;
   }
  },
  onLeave(){
   try{
    if(this.pushed){const s=stacks.get(this.tid);s.pop();if(!s.length)stacks.delete(this.tid);return;}
    if(this.ignore&&this.name!=='Entity.ApplyModifier')return;
    const end=time();
    if(this.skillStart||this.skillEnd){
     if(this.ignore)return;
     if(!this.source){skillUnknown=true;return;}
     const info=this.skill||{};
     const evidence={skill_key:info.skill_key||null,skill_id:info.skill_id||null,skill_name:info.skill_name||null};
     if(this.skillEnd)evidence.skill_reason=this.skillReason;
     try{record(this.source,this.skillStart?'skill_start':'skill_end',1,end,{method:this.name},evidence);}catch(e){skillUnknown=true;}
     return;
    }
    if(end.frame!==this.when.frame||end.game_time!==this.when.game_time)throw new Error('callback crossed frame');
    if(this.name.endsWith('.OnAfterOutputDamage')||this.name==='Entity.OnTakeDamage'||this.name==='Unit.OnTakeDamage'){
     if(this.ignore)return;
     if(this.name==='Unit.OnTakeDamage'&&this.source&&own(this.source)&&this.target&&!this.target.isNull()&&Number.isFinite(this.amount)){
      const targetKeyValue=targetKey(this.target)||this.target.toString();
      const shield=shieldContextUnknown(end.frame,this.target)?null:shieldTotal(end.frame,this.target);
      try{record(this.source,'hp_damage',this.amount,end,{method:this.name,type:this.mod.add(0x68).readS32(),target:this.mod.add(0x60).readS32()},{target:targetKeyValue,hp_before:this.hpBefore,shield_absorbed:shield,target_instance:this.target.toString(),target_type:this.mod.add(0x60).readS32(),damage_type:this.mod.add(0x68).readS32()});}catch(e){fatal=String(e);}
     }
     const hpAfter=targetHp(this.target);
     const scope=this.modifierScope;
     if(this.name==='Entity.OnAfterOutputDamage'&&scope&&scope.targetHit
        &&!scope.targetHitAmbiguous&&scope.source===this.source
        &&scope.when.frame===end.frame&&scope.targetHit.target.equals(this.target)){
      if(scope.outputSeen)scope.outputAmbiguous=true;
      scope.outputSeen=true;
      const settled=overkillEvidence(this.mod,this.target,scope.targetHit.hpBefore,end.frame);
      if(settled&&Number.isFinite(this.amount)&&!scope.outputAmbiguous)
       scope.settlement={amount:this.amount,evidence:settled,method:this.name};
     }
     recordHpContext({sequence:++hpSequence,hook:this.name,frame:end.frame,game_time:end.game_time,
      source:this.source||null,target:targetKey(this.target)||this.target.toString(),
      target_instance:this.target.toString(),modifier_instance:this.mod.toString(),amount:this.amount,
      real_delta:this.real,hp_before:this.hpBefore,hp_after:hpAfter,
      killed:hpAfter!==null&&hpAfter<=1e-6,
      target_type:this.mod.add(0x60).readS32(),damage_type:this.mod.add(0x68).readS32(),
      cancel_reason:this.mod.add(0x70).readS32()});
     return;
    }
    if(this.name==='Entity.ApplyModifier'){
     const targetType=this.mod.add(0x60).readS32(),type=this.mod.add(0x68).readS32();
     const instance=this.target&&!this.target.isNull()?this.target.toString():null;
     const owned=this.source&&own(this.source);
     const amount=fp(this.mod.add(0x30));
     if(owned&&type===0&&targetType===5){
      try{record(this.source,'elemental_bar',amount,end,{method:this.name,type,target:targetType},{target_instance:instance});}catch(e){fatal=String(e);}
      return;
     }
     if(type===5){
      const evidence=overkillEvidence(this.mod,this.target,this.hpBefore,end.frame);
      if(evidence&&instance){
       evidence.target_instance=instance;
       try{record(owned?this.source:null,'elemental_burst',amount,end,{method:this.name,type,target:targetType},evidence);}catch(e){fatal=String(e);}
      }
      return;
     }
     if(!owned)return;
     if(targetType!==0)return;
     if(![1,2,3,4].includes(type)){unknownSources.add(this.source);engagementUnknownSources.add(this.source);return;}
     const kind=type===4?'healing':'damage';
     let damageInstance=instance,evidence=null;
     const proof={method:this.name,type,target:targetType};
     if(kind==='damage'){
      const hit=this.targetHitAmbiguous||this.outputAmbiguous?null:this.targetHit;
      const settlement=hit&&this.settlement;
      // Prefer the settled callback for this exact Modifier& and target. A
      // different hit or same-frame elemental bar cannot supply its HP.
      if(settlement&&settlement.amount===amount){
       evidence=settlement.evidence;
       damageInstance=hit.target.toString();
       proof.evidence_method=settlement.method;
      }else if(hit){
       evidence=overkillEvidence(this.mod,hit.target,hit.hpBefore,end.frame);
       damageInstance=hit.target.toString();
      }else{
       evidence=overkillEvidence(this.mod,this.target,this.hpBefore,end.frame);
      }
      if(!evidence){
       const target=hit?hit.target:this.target;
       if(target&&!target.isNull())evidence=nonlethalEvidence(target);
      }
      if(evidence&&damageInstance)evidence.target_instance=damageInstance;
      if(evidence&&evidence.real_delta===0&&evidence.hp_before===evidence.hp_after
         &&(evidence.hp_before===0&&evidence.killed||evidence.cancel_reason===1))return;
     }
     try{record(this.source,kind,amount,end,proof,evidence);}catch(e){fatal=String(e);}
    }else{
     const after=dynamic(this.bb);
     if(!Number.isFinite(after)||after<0||Math.abs(this.before-after-this.amount)>0.01){shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);return;}
     try{record(this.source,'shield_absorbed',this.amount,end,{method:this.name,buff:this.buff.key,before:this.before,after,incoming:this.incoming});}catch(e){fatal=String(e);}
     noteShield(this.when.frame,this.damageTarget,this.amount);
    }
   }catch(e){
    if(this.skillStart||this.skillEnd){skillUnknown=true;}
    else if(this.name==='Entity.ApplyModifier'){if(this.source)unknownSources.add(this.source);else{skippedUnattributed++;}}
    else{shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);}
   }finally{
    if(this.tracksModifier)leaveModifier(this);
    if(this.started){active--;const left=(activeHooks.get(this.name)||1)-1;if(left>0)activeHooks.set(this.name,left);else activeHooks.delete(this.name);}
   }
  }
 }));
}
installEnemyEntryHooks();
installEnemyPhaseHook();
installEnemyStateHooks();
const enemyStateError=cfg.enemyStateError||cfg.enemyPhaseError||cfg.enemyEntryError||null;
function enemyEntryQuality(frame){
 if(enemyEntryReady&&frame!==undefined)refreshEnemyEntryFrame(frame);
 return {enemy_entry_ready:enemyEntryReady,enemy_entry_unknown:enemyEntryUnknown,
  enemy_phase_ready:enemyPhaseReady,enemy_phase_unknown:enemyPhaseUnknown,
  enemy_state_ready:enemyStateReady&&enemyPhaseReady,
  enemy_state_unknown:enemyStateUnknown||enemyPhaseUnknown||enemyEntryUnknown,
  enemy_state_error:enemyStateError,
  enemy_max_hp_ready:enemyMaxHpReady,enemy_max_hp_error:cfg.enemyMaxHpError||null,
  stage_object_finish_error:cfg.stageObjectFinishError||null};
}
function eventQuality(frame){
 return {unknown_sources:[...unknownSources],engagement_unknown_sources:[...engagementUnknownSources],
  shield_unknown:shieldUnknown,damage_unknown:damageUnknown,engagement_unknown:engagementUnknown,
  skill_unknown:skillUnknown||!cfg.skillReady,skill_ready:!skillUnknown&&cfg.skillReady,...enemyEntryQuality(frame)};
}
function markEvents(){
 const current=time(true);
 const quality=eventQuality(current?.frame);
  if (current === null) return {retry:true,error:fatal,stopped,...quality};
  return {...current,sequence,epoch,active,active_hooks:[...activeHooks.keys()],error:fatal,stopped,...quality};
}
function receiveEventRequest(){
 // Frida recv is one-shot. Rearm before dispatch; a late reply can be safely
 // ignored by the host because reads are replayable and ACKs idempotent.
 recv('arkdps-event-request',request=>{
  receiveEventRequest();
  let result;
  try{
   switch(request.operation){
    case 'mark':result=markEvents();break;
    case 'read':result=readEvents(request.after,request.through);break;
    case 'ack':result=ackEvents(request.sequence);break;
    default:result={error:'unknown event transport operation'};
   }
  }catch(e){result={error:String(e)};}
  send({kind:'arkdps-event-response',id:request.id,result});
 });
}
rpc.exports={
 mark:markEvents,
 readEvents,
 ackEvents,
 stop(){stopped=true;for(const h of hooks)h.detach();return {active,error:fatal};}
};
receiveEventRequest();
send({ready:true,arch:Process.arch,skill_ready:!skillUnknown&&cfg.skillReady,
 skill_finish_error:cfg.skillFinishError||null,
 enemy_entry_ready:enemyEntryReady,enemy_entry_error:cfg.enemyEntryError||null,
 enemy_phase_ready:enemyPhaseReady,enemy_phase_error:cfg.enemyPhaseError||null,
 enemy_state_ready:enemyStateReady&&enemyPhaseReady,enemy_state_error:enemyStateError,
 enemy_max_hp_ready:enemyMaxHpReady,enemy_max_hp_error:cfg.enemyMaxHpError||null,
 stage_object_finish_error:cfg.stageObjectFinishError||null});
