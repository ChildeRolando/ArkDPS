// Research observer for the measured Android 2.7.91 ARM64 layout.
// Interceptors observe calls only: no argument, return-value, or gameplay writes.
if(Process.arch!=='arm64')throw new Error('ARM64 emulated realm required');
const il=Process.getModuleByName('libil2cpp.so');
const traceHpContext=globalThis.arkdpsTraceHpContext===true;
const coalesceEnemyHp=globalThis.arkdpsCoalesceEnemyHp===true;
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
let mitigationMethods=null;
let terminalMethods=null;
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
function resolveMitigationMethods(formula,entity,modifier,bObject){
 if(!formula)throw new Error('BattleFormula missing');
 const classFrom=api('il2cpp_class_from_type','pointer',['pointer']);
 const declaringClass=api('il2cpp_class_get_declaring_type','pointer',['pointer']);
 const isByRef=api('il2cpp_type_is_byref','bool',['pointer']);
 const isValueType=api('il2cpp_class_is_valuetype','bool',['pointer']);
 const valueSize=api('il2cpp_class_value_size','int',['pointer','pointer']);
 const paramCount=api('il2cpp_method_get_param_count','uint',['pointer']);
 const paramAt=api('il2cpp_method_get_param','pointer',['pointer','uint']);
 const resultOf=api('il2cpp_method_get_return_type','pointer',['pointer']);
 const flagsOf=api('il2cpp_method_get_flags','uint',['pointer','pointer']);
 const metadataOut=Memory.alloc(4);
 function valueTypeSize(klass){
  return !klass.isNull()&&isValueType(klass)?valueSize(klass,metadataOut):0;
 }
 function fixedPoint(klass){return valueTypeSize(klass)===8&&className(klass)==='FP';}
 function signature(klass,name,count,isStatic,match,returnMatches=fixedPoint){
  const array=klass.add(0x98).readPointer(),length=klass.add(0x120).readU16(),matches=[];
  if(length>2000)throw new Error('invalid mitigation method metadata');
  for(let i=0;i<length;i++){
   const method=array.add(i*8).readPointer();
   if(!method.add(0x20).readPointer().equals(klass)||method.add(0x18).readPointer().readCString()!==name
      ||paramCount(method)!==count||!!(flagsOf(method,metadataOut)&0x10)!==isStatic
      ||isByRef(resultOf(method))||!returnMatches(classFrom(resultOf(method))))continue;
   const params=Array.from({length:count},(_,index)=>paramAt(method,index));
   if(match(params)){
    const address=method.readPointer();
    if(!address.isNull()&&Process.findModuleByAddress(address)?.name===il.name)
     matches.push({address,params});
   }
  }
  if(matches.length!==1)throw new Error('unsupported mitigation signature '+name);
  return matches[0];
 }
 function named(type,name){return !isByRef(type)&&className(classFrom(type))===name;}
 function entityParam(type){return !isByRef(type)&&classFrom(type).equals(entity);}
 const outer=signature(formula,'CalculateDamage',5,true,params=>{
  const attack=classFrom(params[4]);
  return entityParam(params[0])&&entityParam(params[1])&&named(params[2],'Boolean')&&named(params[3],'Boolean')
   &&isByRef(params[4])&&!attack.isNull()&&className(attack)==='AttackInfo'&&declaringClass(attack).equals(formula);
 });
 const attack=classFrom(outer.params[4]);
 const attackSize=valueTypeSize(attack);
 const damageType=fieldOffsets(attack,['damageType']).damageType-0x10;
 if(attackSize<=16||attackSize>256||damageType<0||damageType%4!==0||damageType+4>attackSize||!isValueType(modifier))
  throw new Error('unsupported mitigation value-type ABI');
 const internal=signature(formula,'CalculateDamageInternal',4,true,params=>
  !isByRef(params[0])&&classFrom(params[0]).equals(attack)
  &&params.slice(1,3).every(type=>!isByRef(type)&&fixedPoint(classFrom(type)))&&entityParam(params[3]));
 const final=signature(attack,'GetFinalAtk',0,false,()=>true);
 const resolved={outer:outer.address,internal:internal.address,final:final.address,damageType};
 try{
  resolved.noEvent=signature(formula,'CalculateDamageNoEvent',3,true,params=>
   entityParam(params[0])&&entityParam(params[1])&&!isByRef(params[2])&&classFrom(params[2]).equals(attack)).address;
 }catch(e){cfg.mitigationNoEventError=String(e);}
 try{
  if(!bObject||valueTypeSize(modifier)<0x90)throw new Error('unsupported damage Modifier ABI');
  expectFields(bObject,{'<instanceUid>k__BackingField':0x18});
  expectFields(modifier,{'<originAtk>k__BackingField':0x90,'<originValue>k__BackingField':0x98});
  resolved.factories=[7,8].map(count=>signature(modifier,'NewDamageModifierAfterCalculate',count,true,params=>{
   const index=count-6;
   return params.slice(0,index).every(type=>!isByRef(type)&&fixedPoint(classFrom(type)))&&
    named(params[index],'DamageType')&&named(params[index+1],'SourceApplyWay')&&
    entityParam(params[index+2])&&entityParam(params[index+3])&&
    named(params[index+4],'SourceAttackType')&&declaringClass(classFrom(params[index+4])).equals(modifier)&&
    !isByRef(params[index+5])&&fixedPoint(classFrom(params[index+5]));
  },result=>result.equals(modifier)).address);
  if(resolved.factories[0].equals(resolved.factories[1]))throw new Error('shared damage factory address');
 }catch(e){resolved.factories=null;cfg.mitigationFactoryError=String(e);}
 return resolved;
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
 const optionalClasses=new Set([...enemyClasses,'BattleFormula']);
 for(let i=0,n=classCount(im);i<n;i++){
  const k=classAt(im,i),name=className(k);if(!wanted.has(name)&&!optionalClasses.has(name))continue;
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
 try{mitigationMethods=resolveMitigationMethods(found.BattleFormula,found.Entity,found.Modifier,found.BObject);}
 catch(e){cfg.mitigationError=String(e);}
 const CF=fieldOffsets(found.Character,['m_skill']);
 const BF=fieldOffsets(found.BasicSkill,['m_owner','m_data']);
 const SF=fieldOffsets(found.SkillData,['prefabKey','skillId','name']);
 skillF={charSkill:CF.m_skill,owner:BF.m_owner,data:BF.m_data,
  prefab:SF.prefabKey,id:SF.skillId,name:SF.name};
 clock=found.BattleController.add(0xb8).readPointer().add(0x14);
 try{
  expectFields(found.BattleController,{'m_logger':0xd8,'m_levelData':0x158});
  terminalMethods={controller:found.BattleController,
   destroy:uniqueMethod(found.BattleController,'OnDestroy',0),
   state:uniqueMethod(found.BattleController,'set_state',1)};
 }catch(e){cfg.terminalError=String(e);}
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
  if(['Modifier','BattleController'].includes(owner)||optionalClasses.has(owner))continue;
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
const enemyDamageReady=!!enemyEntryConfig
 &&cfg.methods.filter(m=>m.owner==='Entity'&&m.name==='OnAfterOutputDamage').length===1;
let enemyDamageUnknown=false;
const enemyHpWrites=new Map();
// Recoverable per-event gaps do not close the whole event stream.
const unknownSources=new Set();
const engagementUnknownSources=new Set();
let shieldUnknown=false;
let damageUnknown=false;
let bossDamageUnknown=false;
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
 const overlapping=scopes.length?damageModifier(scope.tid,scope.mod):null;
 if(overlapping){
  // A recursive call on the same Modifier& can change either call's value.
  overlapping.mitigationModifierReused=true;
  scope.mitigationModifierReused=true;
 }
 scopes.push(scope);activeModifiers.set(scope.tid,scopes);scope.tracksModifier=true;
}
function leaveModifier(scope){
 const scopes=activeModifiers.get(scope.tid);
 if(!scopes)return;
 const index=scopes.lastIndexOf(scope);
 if(index!==-1)scopes.splice(index,1);
 if(!scopes.length)activeModifiers.delete(scope.tid);
}
function recordElementalBar(scope,when,amount){
 if(scope.elementalBarRecorded)return;
 const target=scope.elementalBarTarget;
 if(!target)return;
 record(scope.source,'elemental_bar',amount,when,{method:scope.name,type:0,target:5},
  {target_instance:target.pointer.toString()});
 scope.elementalBarRecorded=true;
}
function recordTriggeringElementalBar(burst,when){
 const target=burst.target;
 if(!target||target.isNull())return;
 const uid=target.add(0x18).readU32();
 if(!uid)return;
 const candidates=(activeModifiers.get(burst.tid)||[]).filter(scope=>
  scope.elementalBarTarget?.uid===uid&&scope.elementalBarTarget.pointer.equals(target)&&
  sameMitigationTime(scope.when,when));
 if(candidates.length!==1)return;
 const bar=candidates[0];
 if(bar.mitigationModifierReused||bar.mod.add(0x60).readS32()!==5||
    bar.mod.add(0x68).readS32()!==0||
    !bar.mod.add(0x10).readPointer().equals(target)||entityId(bar.mod.readPointer())!==bar.source)return;
 // A full bar can invoke the HP settlement before its own ApplyModifier
 // returns. Publish the verified triggering bar first, once per invocation.
 recordElementalBar(bar,when,fp(bar.mod.add(0x30)));
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
function nonlethalSettlement(target, before, real, amount) {
 // The output callback can run after a nested modifier has killed the
 // target. Its signed delta still belongs to this exact modifier.
 if(!Number.isFinite(before)||!Number.isFinite(real)||!Number.isFinite(amount)
    ||real>=0||-real>amount+0.01||before+real<=0.01)return null;
 const key=targetKey(target);
 return key?{target:key,hp_after:before+real,killed:false}:null;
}
function dynamic(p){const n=p.add(0x18).readS32();if(n<0||n>128)throw new Error('bad blackboard');const a=p.add(0x10).readPointer().add(0x20);for(let i=0;i<n;i++)if(str(a.add(i*24).readPointer())==='dynamic')return a.add(i*24+8).readFloat();throw new Error('missing shield value');}
function fixedArg(p){return Number(BigInt.asIntN(64,BigInt(p.toString())))/4294967296;}
function own(id){return id!==null&&(id.startsWith('char_')||id.startsWith('token_'));}
function record(source,kind,amount,when,proof,evidence){
 if(coalesceEnemyHp)flushCoalescedEnemyHp();
 const event=enqueueEvent(source,kind,amount,when,proof,evidence);
 if(coalesceEnemyHp&&event){
  if(kind==='enemy_enter'||kind==='enemy_reborn')
   enemyHpBirths.set(evidence.target_instance,{eid:evidence.target,frame:when.frame});
  else if(kind==='enemy_fake_death'||kind==='enemy_finish')enemyHpBirths.delete(evidence.target_instance);
 }
 return event;
}
function enqueueEvent(source,kind,amount,when,proof,evidence){
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
 return event;
}
// Keep the first heal intact. It may establish the HP and MaxHP needed to
// prove subsequent healing. An exactly contiguous, increasing tail with the
// same verified MaxHP then contributes its last HP minus its first HP.
const enemyHpBirths=new Map();
let enemyHpRun=null;
function flushCoalescedEnemyHp(){
 const tail=enemyHpRun?.tail;
 enemyHpRun=null;
 if(tail)enqueueEvent(null,'enemy_hp',0,tail.when,{method:tail.method},tail.evidence);
}
function resetEnemyHpCoalescing(){
 if(!coalesceEnemyHp)return;
 flushCoalescedEnemyHp();
 enemyHpBirths.clear();
}
function recordEnemyHp(write,when,method,hp,maxHp){
 const {key,evidence,hpBefore,maxHpBefore}=write;
 if(maxHp!==null)evidence.enemy_max_hp=maxHp;
 evidence.hp_before=hpBefore;
 evidence.hp_after=hp;
 if(coalesceEnemyHp){
  const {target:eid,target_instance:uid}=evidence;
  const birth=enemyHpBirths.get(uid);
  if(write.enemyTarget&&enemyMaxHpReady&&method==='Entity.SetHpInternal'&&maxHpBefore===null
     &&birth?.eid===eid&&when.frame>birth.frame+1&&Number.isFinite(maxHp)
     &&0<hpBefore&&hpBefore<hp&&hp<=maxHp){
   const run=enemyHpRun;
   if(run&&run.key===key&&run.uid===uid&&run.eid===eid&&run.maxHp===maxHp
      &&run.when.frame===when.frame&&run.when.game_time===when.game_time&&run.after===hpBefore){
    if(run.tail)run.tail.evidence.hp_after=hp;
    else run.tail={when,method,evidence};
    run.after=hp;
    return;
   }
   flushCoalescedEnemyHp();
   if(enqueueEvent(null,'enemy_hp',0,when,{method},evidence))
    enemyHpRun={key,uid,eid,when,maxHp,after:hp,tail:null};
   return;
  }
 }
 record(null,'enemy_hp',0,when,{method},evidence);
}
function recordHpContext(context){
 if(stopped||fatal)return;
 if(hpContext.length>=HP_CONTEXT_BUFFER_LIMIT){
  hpContextDropped++;
  return;
 }
 if(coalesceEnemyHp)flushCoalescedEnemyHp();
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
  resetEnemyHpCoalescing();
  awaitingEnemyTile.clear();
  enemyEntryUnknown=false;
  enemyHealthPhases.clear();
  pendingEnemyRebirth.clear();
  enemyPhaseUnknown=false;
  enemyHpWrites.clear();
  enemyStateUnknown=false;
  bossDamageUnknown=false;
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
function enemyDamageIdentity(target) {
 const key=targetKey(target);
 if(!key)throw new Error('missing settled target');
 // Some targetable stage objects enter the HP pool, others do not. Keep
 // the UID for both and let the host match the exact admitted instance.
 if(!key.startsWith('enemy_')&&!key.startsWith('trap_'))return null;
 if(!enemyEntryConfig.assignable(enemyEntryConfig.bObject,target.readPointer()))
  throw new Error('settled target is not a battle object');
 const uid=target.add(0x18).readU32();
 if(!uid)throw new Error('missing settled target UID');
 return {target:key,target_uid:String(uid),target_instance:target.toString()};
}
function recordEnemyDamage(scope,when) {
 if(!enemyDamageReady||scope.source&&!own(scope.source))return;
 try{
  const type=scope.mod.add(0x68).readS32();
  if(![1,2,3,5].includes(type))return;
  if(scope.sourceReadFailed)throw new Error('missing settled source identity');
  // ApplyModifier is the only scoped producer for a source-null burst.
  if(!scope.source)return;
  if(!Number.isFinite(scope.real)||scope.real>0)throw new Error('invalid settled HP delta');
  if(scope.real===0)return;
  const target=enemyDamageIdentity(scope.target);
  if(target)record(scope.source,'enemy_damage',-scope.real,when,{method:scope.name},
                   {...target,damage_type:type});
 }catch(e){enemyDamageUnknown=true;}
}
function recordSourcelessEnemyBurst(scope,evidence,when) {
 if(!enemyDamageReady||scope.source||scope.burstTarget===null)return;
 try{
  const real=evidence?.real_delta??fp(scope.mod.add(0x48));
  if(real===0)return;
  const target=scope.burstTarget;
  if(!scope.nativeSourceNull||!evidence||!target||!Number.isFinite(real)||real>=0
     ||target.target!==evidence.target||target.target_uid!==String(scope.target.add(0x18).readU32()))
   throw new Error('unverified sourceless burst settlement');
  // The host must attribute the HP deduction before the burst consumes its
  // elemental bar owner. Both records stay within this callback's barrier.
  record(null,'enemy_damage',-real,when,{method:scope.name},{...target,damage_type:5});
 }catch(e){enemyDamageUnknown=true;}
}
// One optional observation joins the existing ApplyModifier damage event.
let mitigationReady=false,mitigationNoEventReady=false,mitigationFactoryReady=false;
const formulaScopes=new Map(),pendingFormulaDamage=new Map(),activeDamageFactories=new Set();
const FORMULA_PENDING_LIMIT=256;
// Cumulative observer counters. Each sample reason has its own small budget.
const MITIGATION_SAMPLES_PER_REASON=2;
const UNPAIRED_DAMAGE_SAMPLE_LIMIT=12;
const mitigationCounts={
 formula_scopes:0,formula_candidates:0,formula_missing_pre:0,
 apply_missing_candidate:0,apply_ambiguous_candidate:0,apply_value_mismatch:0,
 apply_changed_at_leave:0,emitted:0,emitted_after_adjustment:0,
 factory_candidates:0,factory_ambiguous:0,factory_emitted:0,unpaired_damage:0,
};
const mitigationSamples=[],unpairedDamageSamples=[];
function noteMitigationDiagnostic(reason,source,when,formula,amount){
 mitigationCounts[reason]++;
 const sameReason=mitigationSamples.filter(sample=>sample.reason===reason);
 if(sameReason.length>=MITIGATION_SAMPLES_PER_REASON
    ||sameReason.some(sample=>sample.source===source))return;
 mitigationSamples.push({reason,source,frame:when.frame,
  formula_before:Number.isFinite(formula?.before)?formula.before:null,
  formula_after:Number.isFinite(formula?.after)?formula.after:null,
  modifier_amount:Number.isFinite(amount)?amount:null});
}
function mitigationDiagnostics(){return {...mitigationCounts,
 no_event_ready:mitigationNoEventReady,no_event_error:cfg.mitigationNoEventError||null,
 factory_ready:mitigationFactoryReady,factory_error:cfg.mitigationFactoryError||null,
 samples:mitigationSamples.slice(),unpaired_damage_samples:unpairedDamageSamples.slice()};}

function sameFormulaHit(scope,attack){
 return scope&&(scope.attack.equals(attack)||scope.internalAttack?.equals(attack));
}
function sameMitigationTime(a,b){return a.frame===b.frame&&a.game_time===b.game_time;}
function mitigationTargetId(target){
 const id=targetKey(target);
 return id&&!id.startsWith('char_')&&!id.startsWith('token_')?id:null;
}
function noteUnpairedDamage(scope,event,type){
 if(!event?.source||!(event.amount>0)||event.mitigation_before!==undefined)return;
 try{
  const target=mitigationTargetId(scope.target);
  if(!target||scope.target.equals(scope.mod.readPointer()))return;
  mitigationCounts.unpaired_damage++;
  let callerRva=null;
  try{
   const caller=scope.returnAddress;
   if(caller&&Process.findModuleByAddress(caller)?.name===il.name)callerRva=caller.sub(il.base).toString();
  }catch(e){/* The sample remains useful without a caller address. */}
  const prior=unpairedDamageSamples.findIndex(sample=>sample.source===event.source&&sample.target===target&&
   sample.damage_type===type&&sample.caller_rva===callerRva);
  if(prior!==-1)unpairedDamageSamples.splice(prior,1);
  else if(unpairedDamageSamples.length>=UNPAIRED_DAMAGE_SAMPLE_LIMIT)unpairedDamageSamples.shift();
  unpairedDamageSamples.push({source:event.source,target,damage_type:type,frame:event.frame,
   amount:event.amount,caller_rva:callerRva});
 }catch(e){/* A diagnostic must not change delivery of the queued event. */}
}
function formulaDamageKey(source,target,type){return [source,target,type].join('/');}
function unownedFormulaKey(target,uid,type,raw){return [target,uid,type,raw,''].join('/');}
function formulaBatch(thread,when,create=false){
 let batch=pendingFormulaDamage.get(thread);
 if(!batch||!sameMitigationTime(batch,when)){
  if(!create)return null;
  batch={...when,hits:new Map(),unowned:new Map(),factories:new Map(),
   postFormulaAttacks:new Map(),postFormulaHits:new Map()};
  pendingFormulaDamage.set(thread,batch);
 }
 return batch;
}
function rememberUnique(hits,key,candidate){
 if(hits.has(key))hits.set(key,null);
 else if(hits.size<FORMULA_PENDING_LIMIT)hits.set(key,candidate);
}
function rememberFormulaDamage(scope,raw,after){
 mitigationCounts.formula_candidates++;
 const batch=formulaBatch(scope.tid,scope.when,true);
 const candidate={raw,before:scope.before,beforeRaw:scope.beforeRaw,after,source:scope.source,target:scope.target,type:scope.type};
 if(scope.source.isNull()){
  const key=unownedFormulaKey(scope.target,scope.targetUid,scope.type,raw)+scope.beforeRaw;
  rememberUnique(batch.unowned,key,candidate);
  return;
 }
 rememberUnique(batch.hits,formulaDamageKey(scope.source,scope.target,scope.type),candidate);
}
function consumeFormula(hits,key){
 const result=hits?.get(key);
 if(result!==undefined)hits.delete(key);
 return result;
}
function verifiedFactoryModifier(result,modifier){
 return !modifier.isNull()&&result.source.equals(modifier.readPointer())&&
  result.target.equals(modifier.add(0x10).readPointer())&&
  result.sourceUid===(result.source.isNull()?0:result.source.add(0x18).readU32())&&
  result.targetUid===result.target.add(0x18).readU32()&&
  result.raw===fixedBits(modifier.add(0x30).readS64())&&modifier.add(0x60).readS32()===0&&
  result.type===modifier.add(0x68).readS32()&&result.beforeRaw===fixedBits(modifier.add(0x80).readS64())&&
  result.originValue===fixedBits(modifier.add(0x88).readS64());
}
function takeFormulaDamage(scope,source,type,raw){
 const batch=formulaBatch(scope.tid,scope.when);
 const when=scope.when,key=formulaDamageKey(source,scope.target,type);
 // Consume pending evidence on the first settlement, even if it is rejected.
 const ordinary=consumeFormula(batch?.hits,key),factory=consumeFormula(batch?.factories,key);
 batch?.postFormulaHits.delete(key);
 if(ordinary?.raw===raw)return ordinary;
 if(factory?.raw===raw&&verifiedFactoryModifier(factory,scope.mod))return factory;
 if(ordinary===undefined&&factory===undefined){
  if(mitigationTargetId(scope.target))mitigationCounts.apply_missing_candidate++;
  return null;
 }
 if(ordinary===null||factory===null){mitigationCounts.apply_ambiguous_candidate++;return null;}
 noteMitigationDiagnostic('apply_value_mismatch',scope.source,when,ordinary||factory,Number(raw)/4294967296);
 return null;
}
function fixedBits(value){return BigInt.asIntN(64,BigInt(value.toString()));}
function mitigationDamageType(type){return type===1||type===2||type===3||type===5;}
function captureMitigation(scope,source){
 if(!mitigationReady||!scope.target||scope.target.isNull()||source.equals(scope.target)||
    !mitigationTargetId(scope.target)||scope.mod.add(0x60).readS32()!==0)return;
 const type=scope.mod.add(0x68).readS32();
 if(!mitigationDamageType(type)||(source.isNull()&&type!==5))return;
 const raw=fixedBits(scope.mod.add(0x30).readS64());
 scope.mitigation=takeFormulaDamage(scope,source,type,raw);
}
function mitigationEvidence(scope,type,amount){
 const result=scope.mitigation;
 if(!result)return null;
 if(scope.mitigationModifierReused||result.type!==type||scope.mod.add(0x60).readS32()!==0||
    !Number.isFinite(amount)||amount<0||
    !result.source.equals(scope.mod.readPointer())||!result.target.equals(scope.mod.add(0x10).readPointer())||
    scope.mod.add(0x70).readS32()!==0){
  noteMitigationDiagnostic('apply_changed_at_leave',scope.source,scope.when,result,amount);
  return null;
 }
 // The unique formula result matched m_value exactly on entry. Damage
 // modifiers can adjust that value during this call before shield and HP
 // settlement, so keep the matched formula input and report final m_value.
 if(result.after!==amount)
  noteMitigationDiagnostic('emitted_after_adjustment',scope.source,scope.when,result,amount);
 mitigationCounts.emitted++;
 if(result.originValue!==undefined)mitigationCounts.factory_emitted++;
 return {mitigation_before:result.before,mitigation_after:amount};
}
function beginDamageFormula(invocation,target,source,attack,eligible,nested){
 const previous=formulaScopes.get(invocation.tid);
 // The eventful wrapper calls NoEvent with the same target/source and a
 // by-value copy. Its outer return is the one result for that calculation.
 if(nested&&previous?.outer&&previous.target.equals(target)&&previous.source.equals(source))return;
 invocation.previous=previous;
 invocation.entered=true;
 formulaScopes.set(invocation.tid,null);
 pendingFormulaDamage.get(invocation.tid)?.postFormulaAttacks.delete(attack.toString());
 if(!eligible||target.isNull()||attack.isNull()||source.equals(target)||!mitigationTargetId(target))return;
 let sourceId=null,targetUid=null;
 if(source.isNull()){
  if(!mitigationFactoryReady)return;
  targetUid=target.add(0x18).readU32();
  if(!targetUid)return;
 }else{
  sourceId=entityId(source);
  if(!own(sourceId))return;
 }
 const scope={tid:invocation.tid,source,sourceId,target,targetUid,attack,when:time(),outer:!nested};
 invocation.formula=scope;
 formulaScopes.set(invocation.tid,scope);
 mitigationCounts.formula_scopes++;
}
function finishDamageFormula(result){
 const scope=this.formula;
 if(!scope||!sameMitigationTime(scope.when,time()))return;
 const type=scope.attack.add(mitigationMethods.damageType).readS32(),raw=fixedBits(result);
 const after=Number(raw)/4294967296;
 if(!mitigationDamageType(type)||!Number.isFinite(after)||after<0)return;
 if(scope.source.isNull()&&raw===0n&&scope.beforeRaw===0n)return;
 // Type 5 HP settlements use the final GetFinalAtk result carried by the
 // damage factory. CalculateElementDamage also serves elemental buildup,
 // which is a separate event and must never supply this pair.
 if(type===5||scope.conflict||type!==scope.type||!Number.isFinite(scope.before)||scope.before<=0){
  if(scope.sourceId&&type!==5)
   noteMitigationDiagnostic('formula_missing_pre',scope.sourceId,scope.when,{after},null);
  // Some producers call GetFinalAtk on the original AttackInfo only after
  // the outer formula returns. Its nested calculations can change type.
  // Keep the final type and return value for the subsequent factory call.
  if(mitigationFactoryReady&&!this.previous&&(scope.sourceId||type===5)){
   const sourceUid=scope.source.isNull()?0:scope.source.add(0x18).readU32();
   const targetUid=scope.target.add(0x18).readU32();
   if((sourceUid||scope.source.isNull())&&targetUid){
    const batch=formulaBatch(scope.tid,scope.when,true);
    rememberUnique(batch.postFormulaAttacks,scope.attack.toString(),
     {raw,after,source:scope.source,target:scope.target,sourceUid,targetUid,type});
   }
  }
  return;
 }
 rememberFormulaDamage(scope,raw,after);
}
function restoreDamageFormula(){
 if(!this.entered)return;
 if(this.previous)formulaScopes.set(this.tid,this.previous);
 else formulaScopes.delete(this.tid);
}
function enterDamageFactory(args,index){
 if(activeDamageFactories.has(this.tid))return; // seven-argument wrapper can delegate to eight
 activeDamageFactories.add(this.tid);
 this.enteredFactory=true;
 const when=time(),batch=formulaBatch(this.tid,when);
 if(!batch||(batch.unowned.size===0&&batch.postFormulaHits.size===0))return;
 const type=args[index].toInt32(),target=args[index+3];
 if(!mitigationDamageType(type)||target.isNull()||!mitigationTargetId(target))return;
 const targetUid=target.add(0x18).readU32();
 if(!targetUid)return;
 const raw=fixedBits(args[0]),originValue=index===2?fixedBits(args[1]):raw;
 const beforeRaw=fixedBits(args[index+5]);
 const source=args[index+2],unowned=batch.unowned;
 const unownedFormula=consumeFormula(unowned,unownedFormulaKey(target,targetUid,type,raw)+beforeRaw);
 if(originValue!==raw){
  // The eight-argument factory carries an independent reference amount.
  // A matching source-null calculation belongs to that reference slot.
  const reference=unownedFormulaKey(target,targetUid,type,originValue);
  for(const key of unowned.keys())if(key.startsWith(reference))unowned.delete(key);
 }
 if(unownedFormula===null){mitigationCounts.factory_ambiguous++;return;}
 if(source.equals(target)||(source.isNull()?type!==5:!own(entityId(source))))return;
 const key=formulaDamageKey(source,target,type);
 const postFormula=consumeFormula(batch.postFormulaHits,key);
 const ordinary=batch.hits.get(key);
 if(ordinary?.raw===raw)return; // prefer the directly attributed calculation
 const sourceUid=source.isNull()?0:source.add(0x18).readU32(),storage=this.context.x8;
 if((!sourceUid&&!source.isNull())||!storage||storage.isNull())return;
 const matchesPost=postFormula?.raw===raw&&postFormula.beforeRaw===beforeRaw&&
  postFormula.sourceUid===sourceUid&&postFormula.targetUid===targetUid;
 if(!unownedFormula&&postFormula===null){mitigationCounts.factory_ambiguous++;return;}
 const formula=unownedFormula||(matchesPost&&ordinary===undefined?postFormula:null);
 if(!formula)return;
 this.factory={batch,when,key,storage,result:{...formula,source,sourceUid,targetUid,originValue}};
}
function finishDamageFactory(){
 const factory=this.factory;
 if(!factory||!sameMitigationTime(factory.when,time())||
    !verifiedFactoryModifier(factory.result,factory.storage))return;
 mitigationCounts.factory_candidates++;
 rememberUnique(factory.batch.factories,factory.key,factory.result);
}
function restoreDamageFactory(){
 if(this.enteredFactory)activeDamageFactories.delete(this.tid);
}
function installMitigationHooks(){
 if(!mitigationMethods)return;
 const attached=[];
 function observe(address,name,enter,leave,cleanup,barrier=false){
  attached.push(Interceptor.attach(address,{
   onEnter(args){
    this.tid=this.threadId;
    if(barrier){
     active++;epoch++;
     activeHooks.set(name,(activeHooks.get(name)||0)+1);
    }
    try{enter.call(this,args);}catch(e){this.failed=true;}
   },
   onLeave(result){
    try{if(!this.failed)leave.call(this,result);}catch(e){/* Unmatched hit has no mitigation fields. */}
    finally{
     if(cleanup)cleanup.call(this);
     if(barrier){
      active--;
      const left=(activeHooks.get(name)||1)-1;
      if(left>0)activeHooks.set(name,left);else activeHooks.delete(name);
     }
    }
   }
  }));
 }
 try{
  observe(mitigationMethods.outer,'BattleFormula.CalculateDamage',function(args){
   beginDamageFormula(this,args[0],args[1],args[4],!!(args[2].toInt32()||args[3].toInt32()),false);
  },finishDamageFormula,restoreDamageFormula,true);
  if(mitigationMethods.noEvent){
   try{
    observe(mitigationMethods.noEvent,'BattleFormula.CalculateDamageNoEvent',function(args){
     beginDamageFormula(this,args[0],args[1],args[2],true,true);
    },finishDamageFormula,restoreDamageFormula,true);
    mitigationNoEventReady=true;
   }catch(e){cfg.mitigationNoEventError=String(e);}
  }
  observe(mitigationMethods.internal,'BattleFormula.CalculateDamageInternal',function(args){
   const scope=formulaScopes.get(this.tid);
   if(!scope||!scope.target.equals(args[3]))return;
   // The verified AttackInfo is larger than 16 bytes, so AArch64 passes
   // its by-value argument indirectly. Borrow that copy only during this call.
   this.scope=scope;
   this.previousAttack=scope.internalAttack;
   scope.internalAttack=args[0];
  },function(){},function(){
   if(this.scope)this.scope.internalAttack=this.previousAttack;
  });
  observe(mitigationMethods.final,'AttackInfo.GetFinalAtk',function(args){
   const scope=formulaScopes.get(this.tid);
   if(sameFormulaHit(scope,args[0])){
    this.scope=scope;
    this.type=args[0].add(mitigationMethods.damageType).readS32();
   }else if(!formulaScopes.has(this.tid)){
    const batch=pendingFormulaDamage.get(this.tid);
    if(!batch?.postFormulaAttacks.size||!sameMitigationTime(batch,time()))return;
    const candidate=consumeFormula(batch.postFormulaAttacks,args[0].toString());
    if(candidate&&candidate.type===args[0].add(mitigationMethods.damageType).readS32()){
     this.postFormula=candidate;
     this.postFormulaBatch=batch;
    }
   }
  },function(result){
   if(this.postFormula){
    const candidate=this.postFormula,batch=this.postFormulaBatch;
    const beforeRaw=fixedBits(result);
    if(beforeRaw<=0n||!sameMitigationTime(batch,time()))return;
    const key=formulaDamageKey(candidate.source,candidate.target,candidate.type);
    rememberUnique(batch.postFormulaHits,key,{...candidate,beforeRaw,before:Number(beforeRaw)/4294967296});
    return;
   }
   const scope=this.scope;
   if(!scope||this.type<1||this.type>3)return;
   const beforeRaw=fixedBits(result);
   if(scope.beforeRaw!==undefined&&(scope.beforeRaw!==beforeRaw||scope.type!==this.type))scope.conflict=true;
   scope.before=Number(beforeRaw)/4294967296;
   scope.beforeRaw=beforeRaw;
   scope.type=this.type;
  });
  if(mitigationMethods.factories){
   const start=attached.length;
   try{
    for(let i=0;i<mitigationMethods.factories.length;i++)
     observe(mitigationMethods.factories[i],'Modifier.NewDamageModifierAfterCalculate',
      function(args){enterDamageFactory.call(this,args,i+1);},finishDamageFactory,restoreDamageFactory,true);
    mitigationFactoryReady=true;
   }catch(e){
    for(const handle of attached.splice(start))handle.detach();
    cfg.mitigationFactoryError=String(e);
   }
  }
  hooks.push(...attached);
  mitigationReady=true;
 }catch(e){
  for(const handle of attached)handle.detach();
  mitigationNoEventReady=false;
  mitigationFactoryReady=false;
  cfg.mitigationError=String(e);
 }
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
     if(!awaitingEnemyTile.size&&!enemyHealthPhases.size&&!pendingEnemyRebirth.size&&!enemyHpWrites.size
        &&!enemyHpBirths.size)return;
    try{
     const object=args[0],uid=object.add(0x18).readU32();
      if(coalesceEnemyHp){
       const instance=String(uid);
       if(enemyHpRun?.uid===instance)flushCoalescedEnemyHp();
       enemyHpBirths.delete(instance);
      }
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
       if(attributeChange&&existing.enemyTarget&&existing.maxHpBefore===null){
        if(coalesceEnemyHp)flushCoalescedEnemyHp();
        existing.maxHpBefore=fixedArg(args[2]);
       }
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
      if(coalesceEnemyHp&&attributeChange)flushCoalescedEnemyHp();
      const evidence=enemyIdentity(enemy,uid),hpBefore=targetHp(enemy);
      if(hpBefore===null)throw new Error('enemy HP unreadable before setter');
      const maxHpBefore=attributeChange?fixedArg(args[2]):null;
      this.write={enemy,key,uid,when,evidence,hpBefore,maxHpBefore,enemyTarget,superseded:false};
      enemyHpWrites.set(key,this.write);
      beginEnemyHook(label);
     }catch(e){
      if(coalesceEnemyHp)flushCoalescedEnemyHp();
      if(enemyTarget)enemyStateUnknown=true;
     }
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
      recordEnemyHp(write,end,label,hp,maxHp);
     }catch(e){
      if(coalesceEnemyHp)flushCoalescedEnemyHp();
      if(write.enemyTarget)enemyStateUnknown=true;
     }
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
function shouldObserveCallback({owner,name}){
 if(owner==='Entity'&&['ApplyModifier','OnAfterOutputDamage'].includes(name)
    ||owner==='Unit'&&name==='OnTakeDamage'
    ||owner==='Buff'&&name==='OnTakeDamage'
    ||owner==='BlockDamage'&&name==='_AssignDynamicVar'
    ||owner==='Character'||owner==='BattleController'&&name==='OnCharacterFinished')return true;
 return traceHpContext&&(owner==='Entity'&&name==='OnTakeDamage'
   ||owner==='Unit'&&name==='OnAfterOutputDamage');
}
for(const m of cfg.methods.filter(shouldObserveCallback)){
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
     try{this.target=this.mod.add(0x10).readPointer();if(traceHpContext)this.hpBefore=targetHp(this.target);}catch(e){this.target=null;this.hpBefore=null;}
     try{
      const source=this.mod.readPointer();
      this.source=source.isNull()?null:entityId(source);
     }catch(e){this.source=null;this.sourceReadFailed=true;}
     try{this.amount=fp(this.mod.add(0x30));}catch(e){this.amount=null;}
     try{this.real=fp(this.mod.add(0x48));}catch(e){this.real=null;}
     if(this.name==='Entity.OnAfterOutputDamage')recordEnemyDamage(this,this.when);
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
     if(traceHpContext)try{this.real=fp(this.mod.add(0x48));}catch(e){this.real=null;}
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
     try{
      const source=this.mod.readPointer();
      this.nativeSourceNull=source.isNull();
      this.source=this.nativeSourceNull?null:entityId(source);
     }catch(e){this.source=null;}
     if(enemyDamageReady&&this.nativeSourceNull){
      try{
       if(this.mod.add(0x68).readS32()===5)this.burstTarget=enemyDamageIdentity(this.target);
      }catch(e){/* An unverified target only affects an actual settlement. */}
     }
     if((this.source&&own(this.source))||
        (this.nativeSourceNull&&this.mod.add(0x68).readS32()===5)){
      enterModifier(this);
      try{captureMitigation(this,this.mod.readPointer());}catch(e){/* Existing damage remains observable. */}
      if(this.source&&this.target&&!this.target.isNull()&&this.mod.add(0x60).readS32()===5&&
         this.mod.add(0x68).readS32()===0){
       this.elementalBarTarget={pointer:this.target,uid:null};
       try{this.elementalBarTarget.uid=this.target.add(0x18).readU32();}
       catch(e){/* The normal bar record still has its target pointer. */}
      }
     }
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
     if(!this.target||this.target.isNull())return;
     if(this.name==='Unit.OnTakeDamage'&&this.source&&own(this.source)&&this.target&&!this.target.isNull()&&Number.isFinite(this.amount)){
      const targetKeyValue=targetKey(this.target)||this.target.toString();
      const shield=shieldContextUnknown(end.frame,this.target)?null:shieldTotal(end.frame,this.target);
      try{record(this.source,'hp_damage',this.amount,end,{method:this.name,type:this.mod.add(0x68).readS32(),target:this.mod.add(0x60).readS32()},{target:targetKeyValue,hp_before:this.hpBefore,shield_absorbed:shield,target_instance:this.target.toString(),target_type:this.mod.add(0x60).readS32(),damage_type:this.mod.add(0x68).readS32()});}catch(e){fatal=String(e);}
     }
     const scope=this.modifierScope;
     if(this.name==='Entity.OnAfterOutputDamage'&&scope&&scope.targetHit
        &&!scope.targetHitAmbiguous&&scope.source===this.source
        &&scope.when.frame===end.frame&&scope.targetHit.target.equals(this.target)){
      if(scope.outputSeen)scope.outputAmbiguous=true;
      scope.outputSeen=true;
      const settled=overkillEvidence(this.mod,this.target,scope.targetHit.hpBefore,end.frame)
       ||nonlethalSettlement(this.target,scope.targetHit.hpBefore,this.real,this.amount);
      if(settled&&Number.isFinite(this.amount)&&!scope.outputAmbiguous)
       scope.settlement={amount:this.amount,evidence:settled,method:this.name};
     }
     if(traceHpContext){
      const hpAfter=targetHp(this.target);
      recordHpContext({sequence:++hpSequence,hook:this.name,frame:end.frame,game_time:end.game_time,
       source:this.source||null,target:targetKey(this.target)||this.target.toString(),
       target_instance:this.target.toString(),modifier_instance:this.mod.toString(),amount:this.amount,
       real_delta:this.real,hp_before:this.hpBefore,hp_after:hpAfter,
       killed:hpAfter!==null&&hpAfter<=1e-6,
       target_type:this.mod.add(0x60).readS32(),damage_type:this.mod.add(0x68).readS32(),
       cancel_reason:this.mod.add(0x70).readS32()});
     }
     return;
    }
    if(this.name==='Entity.ApplyModifier'){
     const targetType=this.mod.add(0x60).readS32(),type=this.mod.add(0x68).readS32();
     const instance=this.target&&!this.target.isNull()?this.target.toString():null;
     const owned=this.source&&own(this.source);
     const amount=fp(this.mod.add(0x30));
     if(owned&&type===0&&targetType===5){
      try{recordElementalBar(this,end,amount);}catch(e){fatal=String(e);}
      return;
     }
     if(type===5){
      if(targetType!==0||(!owned&&!this.nativeSourceNull)||!mitigationTargetId(this.target))return;
      const evidence=overkillEvidence(this.mod,this.target,this.hpBefore,end.frame);
      if(this.nativeSourceNull&&evidence?.cancel_reason===0){
       try{recordTriggeringElementalBar(this,end);}catch(e){/* Unverified parent; keep the settlement. */}
      }
      recordSourcelessEnemyBurst(this,evidence,end);
      if(evidence&&instance){
       evidence.target_instance=instance;
       try{
        const reduction=mitigationEvidence(this,type,amount);
        if(reduction)Object.assign(evidence,reduction);
       }catch(e){/* Missing formula evidence affects only mitigation. */}
       try{
        const event=record(owned?this.source:null,'elemental_burst',amount,end,{method:this.name,type,target:targetType},evidence);
        noteUnpairedDamage(this,event,type);
       }catch(e){fatal=String(e);}
      }else if((!this.source||owned)&&amount>0&&fp(this.mod.add(0x48))!==0){
       bossDamageUnknown=true;
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
      const damageTarget=hit?hit.target:this.target;
      evidence={target:targetKey(damageTarget),target_instance:damageInstance,
       target_type:targetType,damage_type:type,...evidence};
      if(evidence&&evidence.real_delta===0&&evidence.hp_before===evidence.hp_after
         &&(evidence.hp_before===0&&evidence.killed||evidence.cancel_reason===1))return;
      try{
       const reduction=mitigationEvidence(this,type,amount);
       if(reduction)evidence=Object.assign(evidence||{},reduction);
      }catch(e){/* Missing formula evidence affects only mitigation. */}
     }
     try{
      const event=record(this.source,kind,amount,end,proof,evidence);
      if(kind==='damage')noteUnpairedDamage(this,event,type);
     }catch(e){fatal=String(e);}
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
installMitigationHooks();
let terminalBoundary=null,terminalGeneration=0;
function terminalIdentity(controller,controllerClass){
 if(controller.isNull()||!controller.readPointer().equals(controllerClass))return null;
 // Use the same two ownership references as the guarded native snapshot.
 const native=controller.add(0x10).readPointer(),logger=controller.add(0xd8).readPointer();
 const level=controller.add(0x158).readPointer();
 if(native.isNull()||logger.isNull()||level.isNull()
    ||!native.add(0x28).readPointer().equals(controller)
    ||className(logger.readPointer())!=='BattleLogger'
    ||!logger.add(0x18).readPointer().equals(controller)
    ||className(level.readPointer())!=='LevelData')return null;
 const stats=logger.add(0x30).readPointer();
 if(stats.isNull()||className(stats.readPointer())!=='BattleStats')return null;
 return {controller:controller.toString(),native:native.toString(),logger:logger.toString(),
  stats:stats.toString(),level:level.toString()};
}
function currentTerminalBoundary(candidate=terminalBoundary){
 return candidate&&!active&&!fatal&&!stopped&&candidate.sequence===sequence
  &&candidate.epoch===epoch?candidate:null;
}
function installTerminalHooks(){
 if(!terminalMethods)return;
 try{
  hooks.push(Interceptor.attach(terminalMethods.state,{
   onEnter(args){
    const state=args[1].toInt32();
    if(state===1||state===2){
     resetEnemyHpCoalescing();
     terminalGeneration++;terminalBoundary=null;
    }
   }
  }));
  hooks.push(Interceptor.attach(terminalMethods.destroy,{
   onEnter(args){
    this.generation=++terminalGeneration;
    terminalBoundary=null;
    if(active||fatal||stopped)return;
    if(coalesceEnemyHp)flushCoalescedEnemyHp();
    try{
     const identity=terminalIdentity(args[0],terminalMethods.controller),stamp=time(true);
     if(identity&&stamp)this.boundary={...identity,...stamp,sequence,epoch,active};
    }catch(e){/* A partially destroyed object cannot certify an endpoint. */}
   },
   onLeave(){
    if(!this.boundary||this.generation!==terminalGeneration)return;
    const candidate=currentTerminalBoundary(this.boundary);
    if(!candidate)return;
    try{
     const stamp=time(true);
     if(stamp?.frame===candidate.frame&&stamp.game_time===candidate.game_time)terminalBoundary=candidate;
    }catch(e){/* The clock disappeared during teardown. */}
   }
  }));
 }catch(e){cfg.terminalError=String(e);}
}
installTerminalHooks();
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
  boss_damage_ready:true,boss_damage_unknown:bossDamageUnknown,
  shield_unknown:shieldUnknown,damage_unknown:damageUnknown,engagement_unknown:engagementUnknown,
  skill_unknown:skillUnknown||!cfg.skillReady,skill_ready:!skillUnknown&&cfg.skillReady,
  enemy_damage_ready:enemyDamageReady,enemy_damage_unknown:enemyDamageUnknown,
  mitigation_ready:mitigationReady,mitigation_error:cfg.mitigationError||null,
  mitigation_diagnostics:mitigationDiagnostics(),...enemyEntryQuality(frame)};
}
function markEvents(){
 if(coalesceEnemyHp&&!active)flushCoalescedEnemyHp();
 const terminal_boundary=currentTerminalBoundary();
 let current;
 try{current=time(true);}
 catch(e){if(!terminal_boundary)throw e;current=null;}
 const quality={...eventQuality(current?.frame),terminal_boundary};
 if(current===null)return {retry:true,error:fatal,stopped,...quality};
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
 mitigation_ready:mitigationReady,mitigation_error:cfg.mitigationError||null,
 mitigation_no_event_ready:mitigationNoEventReady,mitigation_no_event_error:cfg.mitigationNoEventError||null,
 mitigation_factory_ready:mitigationFactoryReady,mitigation_factory_error:cfg.mitigationFactoryError||null,
 terminal_error:cfg.terminalError||null,
 enemy_entry_ready:enemyEntryReady,enemy_entry_error:cfg.enemyEntryError||null,
 enemy_phase_ready:enemyPhaseReady,enemy_phase_error:cfg.enemyPhaseError||null,
 enemy_state_ready:enemyStateReady&&enemyPhaseReady,enemy_state_error:enemyStateError,
 enemy_max_hp_ready:enemyMaxHpReady,enemy_max_hp_error:cfg.enemyMaxHpError||null,
 stage_object_finish_error:cfg.stageObjectFinishError||null});
