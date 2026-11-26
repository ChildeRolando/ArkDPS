// Shared ArkDPS event runtime; platform adapters own metadata, ABI and binding.
const il=Process.getModuleByName(probeAdapter.moduleName);
const traceHpContext=globalThis.arkdpsTraceHpContext===true;
const traceNestedParent=globalThis.arkdpsTraceNestedParent===true;
const coalesceEnemyHp=globalThis.arkdpsCoalesceEnemyHp!==false;
const observeSourceCharacterUid=globalThis.arkdpsSourceCharacterUid!==false;
function api(n,r,a){
 const native=new NativeFunction(il.getExportByName(n),r,a);
 if(n==='il2cpp_class_is_assignable_from'&&typeof __arkdpsCapturedAssignable==='function')
  return function(base,klass){return __arkdpsReplayActive()?__arkdpsCapturedAssignable(base,klass):native(base,klass);};
 return native;
}
function captureRead(role,read){return typeof probeAdapter.captureRead==='function'?probeAdapter.captureRead(role,read):read();}
function immutableClassParent(klass){
 if(typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()&&typeof probeAdapter.captureImmutableMetadata==='function'){
  const bytes=probeAdapter.captureImmutableMetadata(klass.add(0x58),8);
  if(bytes===null||bytes.byteLength!==8)throw new Error('immutable class parent unavailable');
  return ptr(new DataView(bytes).getBigUint64(0,true));
 }
 return klass.add(0x58).readPointer();
}
const immutableClassLineages=new Map();
function immutableClassLineage(klass,check=null){
 if(typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()&&typeof probeAdapter.captureClassLineage==='function')
  return probeAdapter.captureClassLineage(klass);
 const key=klass.toString();
 if(immutableClassLineages.has(key))return immutableClassLineages.get(key);
 const lineage=[],seen=new Set();
 for(let current=klass;!current.isNull();current=immutableClassParent(current)){
  if(check)check(8);
  const address=current.toString();
  if(seen.has(address))throw new Error('class lineage cycle');
  // This is a work budget, never evidence that a class is unrelated.
  if(lineage.length>=4096)throw new Error('class lineage metadata budget');
  seen.add(address);lineage.push(current);
 }
 const complete=Object.freeze(lineage);
 if(immutableClassLineages.size<4096)immutableClassLineages.set(key,complete);
 return complete;
}
function captureInvocationRead(scope,read){return typeof probeAdapter.captureInvocationRead==='function'?probeAdapter.captureInvocationRead(scope,read):read();}
function captureClockFrame(){return captureRead('clock_close',()=>clock.readU32());}
function captureClockFixed(){return captureRead('clock_close',()=>clock.add(4).readS64());}
function registerProbeHook(address,callbacks){
 if(typeof probeAdapter.prepareCaptureHook==='function')probeAdapter.prepareCaptureHook(address,callbacks);
 return Interceptor.attach(address,callbacks);
}
const immutableClassNames=new Map(),immutableClassNamespaces=new Map();
function className(k){
 const key=k.toString();if(immutableClassNames.has(key))return immutableClassNames.get(key);
 const name=k.add(0x10).readPointer().readCString();immutableClassNames.set(key,name);return name;
}
function classNamespace(k){
 const key=k.toString();if(immutableClassNamespaces.has(key))return immutableClassNamespaces.get(key);
 const value=k.add(0x18).readPointer().readCString();immutableClassNamespaces.set(key,value);return value;
}
function fieldOffsets(k,names){
 const seen=classFieldOffsets(k);
 const out={};for(const name of names){if(!(name in seen))throw new Error('missing field '+className(k)+'.'+name);out[name]=seen[name];}
 return out;
}
function expectFields(k,expected){
 const seen=classFieldOffsets(k);
 for(const [name,offset] of Object.entries(expected))if(seen[name]!==offset)throw new Error('unsupported layout '+className(k)+'.'+name);
}
const domain=api('il2cpp_domain_get','pointer',[])();
if(domain.isNull())throw new Error('IL2CPP domain is not initialized');
const thread=api('il2cpp_thread_attach','pointer',['pointer'])(domain);
if(thread.isNull())throw new Error('IL2CPP thread could not attach');
const cfg={methods:[]};let clock;let skillF=null;
let probeHooksReady=false;
let enemyEntryConfig=null,enemyPhaseMethods=null,enemyStateMethods=null,enemyAttributesReady=false;
let mitigationMethods=null;
let terminalMethods=null;
let autoChessRoundResetMethod=null;
let sourceCharacterClasses=null,settlementCharacterClass=null;
let skillCastStateMethods=null,skillCastStateReady=false;
let characterLifecycleMethods=null,characterLifecycleBornReady=false,characterLifecycleFinishedReady=false;
function characterLifecycleReady(){return !!characterLifecycleMethods&&characterLifecycleBornReady&&characterLifecycleFinishedReady;}
let nativeSkillCastInvocation=0;const nativeSkillCastCalls=new Map();
function uniqueMethod(klass,name,argCount) {
 const matches=[];
 const parameterCount=api('il2cpp_method_get_param_count','uint',['pointer']);
 for(const method of classMethods(klass,'invalid method metadata')){
  if(method.add(0x20).readPointer().equals(klass)&&method.add(0x18).readPointer().readCString()===name&&parameterCount(method)===argCount)
   matches.push(method.readPointer());
 }
 if(matches.length!==1||matches[0].isNull()||Process.findModuleByAddress(matches[0])?.name!==il.name)
  throw new Error('missing or ambiguous method '+name);
 return matches[0];
}
function resolveAutoChessRoundReset(controller){
 const name='ClearRunTimeData',argc=api('il2cpp_method_get_param_count','uint',['pointer']);
 const flags=api('il2cpp_method_get_flags','uint',['pointer','pointer']);
 const result=api('il2cpp_method_get_return_type','pointer',['pointer']);
 const classFrom=api('il2cpp_class_from_type','pointer',['pointer']);
 const byRef=api('il2cpp_type_is_byref','bool',['pointer']),out=Memory.alloc(4);
 const matches=[];
 for(const method of classMethods(controller)){
  if(!method.add(0x20).readPointer().equals(controller)||
     method.add(0x18).readPointer().readCString()!==name||argc(method)!==0)continue;
  const type=result(method),klass=classFrom(type),address=method.readPointer();
  if((flags(method,out)&0x10)===0&&!byRef(type)&&!klass.isNull()&&className(klass)==='Void'&&
     klass.add(0x18).readPointer().readCString()==='System'&&!address.isNull()&&
     Process.findModuleByAddress(address)?.name===il.name)matches.push(address);
 }
 if(matches.length!==1)throw new Error('unsupported BattleController.ClearRunTimeData signature');
 return matches[0];
}
function resolveSkillCastState(basicSkill,character,skillData,ability,bObject,entity){
 if(!ability||!bObject||!sourceCharacterClasses)throw new Error('native skill state identity metadata unavailable');
 const classFrom=api('il2cpp_class_from_type','pointer',['pointer']);
 const byRef=api('il2cpp_type_is_byref','bool',['pointer']);
 const namespaceOf=api('il2cpp_class_get_namespace','pointer',['pointer']);
 const fieldType=api('il2cpp_field_get_type','pointer',['pointer']);
 const fieldFlags=api('il2cpp_field_get_flags','uint',['pointer']);
 const isValue=api('il2cpp_class_is_valuetype','bool',['pointer']);
 const isEnum=api('il2cpp_class_is_enum','bool',['pointer']);
 const declaring=api('il2cpp_class_get_declaring_type','pointer',['pointer']);
 const valueSize=api('il2cpp_class_value_size','int',['pointer','pointer']);
 const argc=api('il2cpp_method_get_param_count','uint',['pointer']);
 const param=api('il2cpp_method_get_param','pointer',['pointer','uint']);
 const result=api('il2cpp_method_get_return_type','pointer',['pointer']);
 const flags=api('il2cpp_method_get_flags','uint',['pointer','pointer']);
 const out=Memory.alloc(4);
 function named(t,name,ns){const k=classFrom(t);return !byRef(t)&&!k.isNull()&&className(k)===name&&namespaceOf(k).readCString()===ns;}
 function boolean(t){const k=classFrom(t);return named(t,'Boolean','System')&&isValue(k)&&valueSize(k,out)===1;}
 const fields=fieldOffsets(basicSkill,['m_waitForSkillEnd']);
 const table=basicSkill.add(0x80).readPointer(),fieldCount=basicSkill.add(0x124).readU16();
 const waits=[];
 for(let i=0;i<fieldCount;i++){const f=table.add(i*32);if(f.readPointer().readCString()==='m_waitForSkillEnd'&&f.add(16).readPointer().equals(basicSkill))waits.push(f);}
 if(waits.length!==1||fieldFlags(waits[0])&0x10||!boolean(fieldType(waits[0]))
    ||fields.m_waitForSkillEnd<0x10||fields.m_waitForSkillEnd>0x10000)throw new Error('unsupported BasicSkill.m_waitForSkillEnd Boolean');
 expectFields(bObject,{'<instanceUid>k__BackingField':0x18});
 const matches=[];
 for(const m of classMethods(basicSkill)){
  if(!m.add(0x20).readPointer().equals(basicSkill)||m.add(0x18).readPointer().readCString()!=='OnCastFinish'||argc(m)!==3||flags(m,out)&0x10||!named(result(m),'Void','System'))continue;
  const first=param(m,0),reason=param(m,1),last=param(m,2),reasonClass=classFrom(reason);
  if(byRef(first)||!classFrom(first).equals(ability)||!named(reason,'FinishReason','')||!isEnum(reasonClass)||!declaring(reasonClass).equals(ability)||valueSize(reasonClass,out)!==4||!boolean(last))continue;
  const address=m.readPointer();if(!address.isNull()&&Process.findModuleByAddress(address)?.name===il.name)matches.push({address,reasonClass});
 }
 if(matches.length!==1)throw new Error('unsupported BasicSkill.OnCastFinish signature');
 const afters=[];
 for(const m of classMethods(character)){
  if(!m.add(0x20).readPointer().equals(character)||m.add(0x18).readPointer().readCString()!=='OnAfterSkill'||argc(m)!==2||flags(m,out)&0x10||!named(result(m),'Void','System'))continue;
  const skill=param(m,0),reason=param(m,1);
  if(byRef(skill)||!classFrom(skill).equals(basicSkill)||byRef(reason)||!classFrom(reason).equals(matches[0].reasonClass))continue;
  const address=m.readPointer();if(!address.isNull()&&Process.findModuleByAddress(address)?.name===il.name)afters.push(address);
 }
 if(afters.length!==1)throw new Error('unsupported Character.OnAfterSkill signature');
 return {address:matches[0].address,afterSkill:afters[0],wait:fields.m_waitForSkillEnd,basicSkill,character,skillData,bObject,entity,
  characterData:sourceCharacterClasses.data,assignable:api('il2cpp_class_is_assignable_from','bool',['pointer','pointer'])};
}
// Optional lifecycle capability: verify the enum owner and literal values in live metadata.
function resolveCharacterLifecycle(character,entity,bObject,controller){
 const from=api('il2cpp_class_from_type','pointer',['pointer']);
 const parent=api('il2cpp_class_get_declaring_type','pointer',['pointer']);
 const byref=api('il2cpp_type_is_byref','bool',['pointer']);
 const argc=api('il2cpp_method_get_param_count','uint',['pointer']);
 const param=api('il2cpp_method_get_param','pointer',['pointer','uint']);
 const result=api('il2cpp_method_get_return_type','pointer',['pointer']);
 const flags=api('il2cpp_method_get_flags','uint',['pointer','pointer']);
 const isEnum=api('il2cpp_class_is_enum','bool',['pointer']);
 const size=api('il2cpp_class_value_size','int',['pointer','pointer']);
 const fieldFlags=api('il2cpp_field_get_flags','uint',['pointer']);
 const literal=api('il2cpp_field_static_get_value','void',['pointer','pointer']);
 const scratch=Memory.alloc(8);
 function signature(owner,name,parameters){
  const matches=[];
  for(const m of classMethods(owner)){
   if(!m.add(0x20).readPointer().equals(owner)||m.add(0x18).readPointer().readCString()!==name||argc(m)!==parameters.length||(flags(m,scratch)&0x10))continue;
   const r=result(m),rk=from(r);if(byref(r)||className(rk)!=='Void'||rk.add(0x18).readPointer().readCString()!=='System')continue;
   const types=parameters.map((_,i)=>param(m,i));
   if(types.some((t,i)=>byref(t)||!parameters[i](from(t))))continue;
   const address=m.readPointer();if(!address.isNull()&&Process.findModuleByAddress(address)?.name===il.name)matches.push({address,types});
  }
  if(matches.length!==1)throw new Error('unsupported lifecycle signature '+name);
  return matches[0];
 }
 let reasonClass=null;
 const finish=signature(controller,'OnCharacterFinished',[k=>k.equals(character),k=>{
  if(className(k)!=='FinishReason'||!parent(k).equals(entity)||!isEnum(k)||size(k,scratch)!==4)return false;
  reasonClass=k;return true;
 }]);
 const expected={NONE:0,REACH_EXIT:1,HP_ZERO:2,FALLDOWN:3,WITHDRAW:4,DEADLIKE_WITHDRAW:5,SILENT_WITHDRAW:6,OTHER:7,HP_ZERO_WITH_NO_SOURCE:8,REPLACED:9,RESPAWN_SELF:10,MOVE_LIKE_RESPAWN_SELF:11,MOVE_LIKE_RESPAWN_EXTERNAL:12};
 const table=reasonClass.add(0x80).readPointer(),count=reasonClass.add(0x124).readU16(),seen=new Set();
 if(count>64)throw new Error('unbounded finish enum');
 for(let i=0;i<count;i++){
  const f=table.add(i*32),name=f.readPointer().readCString();if(!(name in expected))continue;
  if(!f.add(16).readPointer().equals(reasonClass)||(fieldFlags(f)&0x40)===0)throw new Error('nonliteral finish enum');
  literal(f,scratch);if(scratch.readS32()!==expected[name])throw new Error('unsupported finish enum value');seen.add(name);
 }
 if(seen.size!==Object.keys(expected).length)throw new Error('incomplete finish enum');
 expectFields(bObject,{'<instanceUid>k__BackingField':0x18,'<borned>k__BackingField':0x25,'<isDisappeared>k__BackingField':0x26});
 const born=signature(character,'Born',[]).address,baseBorn=signature(bObject,'Born',[]).address;
 if(born.equals(baseBorn))throw new Error('Character.Born aliases the BObject entry hook');
 return {character,bObject,finish:finish.address,born,
  assignable:api('il2cpp_class_is_assignable_from','bool',['pointer','pointer']),reasons:expected};
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
  const matches=[];
  for(const method of classMethods(klass,'invalid mitigation method metadata')){
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
 const classCount=api('il2cpp_image_get_class_count',probeAdapter.imageSizeType,['pointer']),classAt=api('il2cpp_image_get_class','pointer',['pointer',probeAdapter.imageSizeType]);
 let im;
 for(let i=0;i<size.readU64().toNumber();i++){const x=imageOf(assemblies.add(i*8).readPointer());if(imageName(x).readCString()==='Assembly-CSharp.dll'){im=x;break;}}
 if(!im)throw new Error('Assembly-CSharp not found');
 const wanted=new Set(['Entity','Unit','Buff','BuffContainer','BlockDamage','Modifier','BattleController',
  'Character','BasicSkill','SkillData',...probeAdapter.requiredClasses]);const found={};
 const enemyClasses=new Set(['Enemy','BObject','Attributes']);
 const optionalClasses=new Set([...enemyClasses,'BattleFormula','Ability']);
 if(observeSourceCharacterUid)optionalClasses.add('BattleCharacterData');
 for(let i=0,n=classCount(im);i<n;i++){
  // A null row cannot be named. Required classes are still checked below;
  // null is neither a readiness signal nor permission to ignore bad metadata.
  const k=classAt(im,i);if(probeAdapter.allowNullClassRows&&k.isNull())continue;
  const name=className(k);if(!wanted.has(name)&&!optionalClasses.has(name))continue;
  const ns=classNamespace(k);
  const expectedNs=(['BuffContainer','BlockDamage'].includes(name)?'':
    (name==='SkillData'||name==='LevelData'?'Torappu':'Torappu.Battle'));
  if(ns!==expectedNs)continue;
  if(name in found){
   if(wanted.has(name))throw new Error('ambiguous class '+name);
   found[name]=null;
  }else found[name]=k;
 }
 for(const name of wanted)if(!found[name])throw new Error('class not found '+name);
 settlementCharacterClass=found.Character;
 expectFields(found.Entity,{'m_hp':0x40,'<id>k__BackingField':0x148});
 expectFields(found.Buff,{'m_source':0x18,'m_blackboard':0xb8,'<key>k__BackingField':0x208,'<instanceUid>k__BackingField':0x218,'<hasShield>k__BackingField':0x27c});
 expectFields(found.Modifier,{'m_source':0x10,'m_target':0x20,'m_originalSource':0x30,'m_value':0x40,'realDelta':0x58,'<targetType>k__BackingField':0x70,'<damageType>k__BackingField':0x78,'<cancelReason>k__BackingField':0x80});
 // FIXED_FRAME_COUNT is verified from the current class metadata, not an old battle object.
 expectFields(found.BattleController,probeAdapter.controllerFields);
 probeAdapter.validateClasses(found);
 if(observeSourceCharacterUid){
  try{
   expectFields(found.Character,{'<data>k__BackingField':0x538});
   expectFields(found.BattleCharacterData,{uniqueId:0x6c});
   sourceCharacterClasses={character:found.Character,data:found.BattleCharacterData};
  }catch(e){cfg.sourceCharacterUidError=String(e);}
 }
 try{mitigationMethods=resolveMitigationMethods(found.BattleFormula,found.Entity,found.Modifier,found.BObject);}
 catch(e){cfg.mitigationError=String(e);}
 const CF=fieldOffsets(found.Character,['m_skill']);
 const BF=fieldOffsets(found.BasicSkill,['m_owner','m_data']);
 const SF=fieldOffsets(found.SkillData,['prefabKey','skillId','name']);
 skillF={charSkill:CF.m_skill,owner:BF.m_owner,data:BF.m_data,
  prefab:SF.prefabKey,id:SF.skillId,name:SF.name};
 try{characterLifecycleMethods=resolveCharacterLifecycle(found.Character,found.Entity,found.BObject,found.BattleController);}
 catch(e){cfg.characterLifecycleError=String(e);}
 try{skillCastStateMethods=resolveSkillCastState(found.BasicSkill,found.Character,found.SkillData,found.Ability,found.BObject,found.Entity);}
 catch(e){cfg.skillCastStateError=String(e);}
 clock=found.BattleController.add(0xb8).readPointer().add(0x14);
 try{
  probeAdapter.validateTerminalController(found.BattleController);
  terminalMethods={controller:found.BattleController,
   destroy:uniqueMethod(found.BattleController,'OnDestroy',0),
   state:uniqueMethod(found.BattleController,'set_state',1)};
 }catch(e){if(probeAdapter.terminalRequired)throw e;cfg.terminalError=String(e);}
 try{autoChessRoundResetMethod=resolveAutoChessRoundReset(found.BattleController);}
 catch(e){cfg.autoChessRoundResetError=String(e);}
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
  for(const m of classMethods(k,'method count invalid')){
   if(!m.add(0x20).readPointer().equals(k))continue;
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
 if(typeof probeAdapter.prepareCaptureMetadata==='function')probeAdapter.prepareCaptureMetadata({found,clock,skillF,mitigationMethods,skillCastStateMethods,characterLifecycleMethods,enemyEntryConfig,enemyStateMethods,terminalMethods});
} finally {api('il2cpp_thread_detach','void',['pointer'])(thread);}
// A callback increments epoch on entry and remains active until its native
// invocation returns. RPC marks are serialized with these JS callbacks.
let sequence=0, hpSequence=0, epoch=0, active=0, fatal=null, stopped=false;
const activeHooks=new Map();
let enemyEntryReady=false,enemyEntryUnknown=false,enemyEntryFrame=null;
const awaitingEnemyTile=new Map(),enemyClassCache=new Map(),enemyBirthProofs=new Map();
let enemyPhaseReady=false,enemyPhaseUnknown=false;
const enemyHealthPhases=new Map(),pendingEnemyRebirth=new Map();
let enemyStateReady=false,enemyStateUnknown=false;
let enemyMaxHpReady=false;
const enemyDamageReady=!!enemyEntryConfig
 &&cfg.methods.filter(m=>m.owner==='Entity'&&m.name==='OnAfterOutputDamage').length===1;
let enemyDamageUnknown=false;
let damageSettlementClock=null,damageSettlementScope=0,damageSettlementIssue=null,damageSettlementController=null;
let damageSettlementUnlocalizedUnknown=false;
const damageSettlementSourceIssues=new Map(),DAMAGE_SETTLEMENT_SOURCE_ISSUE_LIMIT=128;
const damageSettlementFailureSamples=new Map(),DAMAGE_SETTLEMENT_FAILURE_SAMPLE_LIMIT=16;
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
// Internal provenance only; never a new or rewritten field on the wire event.
const queueSettlementScopes=new WeakMap();
let acknowledgedSequence=0,readThroughSequence=0,hpContextDropped=0;
let autoChessRoundStart=null,autoChessUnclaimedZero=null;
const AUTOCHESS_ROUND_START_EVENT_LIMIT=2048;
const stacks=new Map(), hooks=[];
function str(p){if(p.isNull())return null;const n=p.add(0x10).readS32();if(n<0||n>200)throw new Error('bad string');return p.add(0x14).readUtf16String(n);}
function fp(p){return p.readS64().toNumber()/4294967296;}
function entityId(p){if(p.isNull())throw new Error('missing source entity');const id=str(p.add(0x148).readPointer());if(!id)throw new Error('missing source identity');return id;}
const sourceCharacterLineage=new Map();
function isCharacterSourceClass(klass){
 const base=sourceCharacterClasses.character;
 const captured=typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()&&typeof probeAdapter.captureClassLineage==='function'
  ?immutableClassLineage(klass):null;
 if(klass.equals(base))return true;
 const key=klass.toString();
 if(sourceCharacterLineage.has(key))return sourceCharacterLineage.get(key);
 for(const current of captured??immutableClassLineage(klass)){
  if(current.equals(base)){
   sourceCharacterLineage.set(key,true);
   return true;
  }
 }
 // Only a complete, closed lineage can certify a negative result.
 sourceCharacterLineage.set(key,false);
 return false;
}
function sourceCharacterUid(source,sourceId){
 if(!sourceCharacterClasses||!sourceId||!own(sourceId))return;
 try{
  // The verified Character.data field is inherited by Token, including
  // enemy-side tokens. Its data UID belongs to the roster identity domain.
  if(source.isNull()||!isCharacterSourceClass(source.readPointer()))return;
  const data=source.add(0x538).readPointer();
  if(data.isNull()||!data.readPointer().equals(sourceCharacterClasses.data))return;
  const uid=data.add(0x6c).readU32();
  if(uid>0)return uid;
 }catch(e){/* Native identity unavailable; keep the original event. */}
}
function resetDamageSettlementScope(){
 if(typeof probeAdapter.discardInitialBoundary==='function')probeAdapter.discardInitialBoundary('settlement_scope_changed');
 damageSettlementClock=null;
 damageSettlementScope++;
 enemyDamageUnknown=false;
 damageSettlementIssue=null;
 damageSettlementUnlocalizedUnknown=false;
 damageSettlementSourceIssues.clear();
 damageSettlementFailureSamples.clear();
 damageSettlementController=null;
}
function damageSettlementStamp(frame,game_time,withEventScope=true){
 // A Defense Protocol round can reuse the whole Unity battle object. Check
 // both clock readings before any callback can report uncertainty for it.
 if(damageSettlementClock&&probeAdapter.clockRegressed(damageSettlementClock,frame,game_time)){
  probeAdapter.onClockRegression();
  resetDamageSettlementScope();
 }
 const sample={frame,game_time,settlementScope:damageSettlementScope};
 damageSettlementClock=sample;
 return withEventScope?stampEventScope(sample):sample;
}
function stampEventScope(sample){
 if(typeof probeAdapter.stampClock==='function'){
  try{
   probeAdapter.stampClock(sample);
   if(sample.native_autochess_scope&&typeof sample.native_autochess_scope==='object')
    sample.native_autochess_scope={...sample.native_autochess_scope};
  }catch(e){
   sample.native_autochess_scope={protocol_version:1,status:'unknown',verified_same_frame:false,
    frame:sample.frame,game_time:sample.game_time,reason:'native scope observation failed'};
  }
 }
 return sample;
}
function observeDamageSettlementController(identity,{starting=false}={}){
 const key=[identity.controller,identity.native,identity.stats,identity.level].join(':');
 if(starting&&damageSettlementController!==null&&damageSettlementController!==key)
  resetDamageSettlementScope();
 damageSettlementController??=key;
}
function markDamageSettlementUnknown(when,reason,scope){
 if(!enemyDamageReady)return;
 if(when?.settlementScope!==damageSettlementScope)return;
 enemyDamageUnknown=true;
 damageSettlementIssue??={frame:when.frame,game_time:when.game_time,sequence,reason};
 if(damageSettlementUnlocalizedUnknown)return;
 const issue=verifiedSettlementIssueSource(scope,when);
 if(!issue){damageSettlementUnlocalizedUnknown=true;return;}
 const key=JSON.stringify([issue.native_autochess_scope.scope_key,issue.source,issue.source_character_uid]);
 if(damageSettlementSourceIssues.has(key))return;
 if(damageSettlementSourceIssues.size>=DAMAGE_SETTLEMENT_SOURCE_ISSUE_LIMIT){
  damageSettlementUnlocalizedUnknown=true;return;
 }
 damageSettlementSourceIssues.set(key,issue);
}
// An actor-local proof gap needs more than a cached character id. Preserve
// the exact ApplyModifier/owner/target scope; this anchor never supplies HP.
// Qualify the enemy-HP ledger independently from proving a hit's HP amount.
// Character/token targets may legitimately have BObject UID zero before Born;
// their stable native class/identity excludes them without an enemy certificate.
function readSettlementDomain(target,modifier,when){
 if(!enemyDamageReady)return {kind:'disabled'};
 const point={kind:'unknown',target,modifier,when};
 try{
  point.type=modifier.add(0x68).readS32();point.targetType=modifier.add(0x60).readS32();
  if(!target||target.isNull())return point;
  point.id=targetKey(target);point.klass=target.readPointer();point.uid=target.add(0x18).readU32();
  if(!point.id||point.klass.isNull()||!Number.isInteger(point.uid)||point.uid<0||point.uid>0xffffffff||
     !enemyEntryConfig?.assignable(enemyEntryConfig.bObject,point.klass))return point;
  if(point.id.startsWith('enemy_')||point.id.startsWith('trap_'))point.kind='potential';
  else if(!enemyEntryConfig.assignable(enemyEntryConfig.enemy,point.klass)){
   if(point.id.startsWith('token_'))point.kind='unrelated';
   else if(point.id.startsWith('char_')&&settlementCharacterClass&&
           enemyEntryConfig.assignable(settlementCharacterClass,point.klass)){
    point.kind='unrelated';point.nonEnemyCharacter=true;
   }
  }
 }catch(e){/* Unreadable or unknown identity is never proof of an unrelated target. */}
 return point;
}
function settlementDamageOperation(point){
 return !!point&&point.targetType===0&&[1,2,3,5].includes(point.type);
}
function sameSettlementDomain(a,b,allowRetiredUid=false){
 return !!a&&!!b&&a.kind!=='unknown'&&b.kind!=='unknown'&&
  a.modifier.equals(b.modifier)&&a.target.equals(b.target)&&a.klass.equals(b.klass)&&
  a.id===b.id&&(allowRetiredUid||a.uid===b.uid)&&a.type===b.type&&a.targetType===b.targetType&&
  !!a.when&&!!b.when&&sameMitigationTime(a.when,b.when);
}
function retiredCharacterDomain(entry,current){
 return entry?.nonEnemyCharacter===true&&current?.nonEnemyCharacter===true&&
  entry.uid>0&&current.uid===0&&settlementDamageOperation(entry)&&sameSettlementDomain(entry,current,true);
}
function settlementDomainAmbiguous(scope){
 return scope?.settlementDomainPotential||scope?.settlementDomainNested||scope?.targetSettlementNested||scope?.mitigationModifierReused||
  scope?.targetHitAmbiguous||scope?.outputAmbiguous||scope?.settlementAmbiguous;
}
function observeSettlementDomain(scope,point){
 if(!enemyDamageReady)return;
 if(!scope)return;
 const entry=scope.settlementDomainEntry;
 if(settlementDamageOperation(point)&&point.kind==='potential')scope.settlementDomainPotential=true;
 // A typed non-enemy Character may release its instance UID during removal.
 // Keep the observation sticky so a restored or changed UID is still a gap.
 if(point.kind==='unknown')scope.settlementDomainUncertain=true;
 else if(scope.settlementDomainRetiredCharacter||entry&&!sameSettlementDomain(entry,point)){
  if(retiredCharacterDomain(entry,point)&&!settlementDomainAmbiguous(scope))
   scope.settlementDomainRetiredCharacter=true;
  else scope.settlementDomainUncertain=true;
 }
 if(scope.tracksModifier&&scope.source&&own(scope.source)&&
    (settlementDamageOperation(entry)||settlementDamageOperation(point))&&
    (scope.settlementDomainPotential||scope.settlementDomainUncertain))scope.settlementCandidate=true;
}
function captureSettlementDomain(scope){
 if(!enemyDamageReady)return;
 scope.settlementDomainEntry=readSettlementDomain(scope.target,scope.mod,scope.when);
 observeSettlementDomain(scope,scope.settlementDomainEntry);
}
function captureSettlementIssueSource(scope){
 // A friendly target can disappear in a settlement callback without proving
 // any enemy HP change. Certify its actor independently of the target domain
 // only in unusual candidate paths, without adding reads to ordinary hits.
 if(!nativeAutoStage?.enabled||!scope.sourceCharacterUid||
    !settlementDamageOperation(scope.settlementDomainEntry)||scope.settlementDomainEntry.kind==='potential')return;
 try{
  const entry=scope.when.native_autochess_scope?scope.when:stampEventScope({...scope.when});
  const stamp=entry.native_autochess_scope;
  if(stamp?.status!=='available'||![4,5,6].includes(stamp.phase)||
     !['all','local'].includes(stamp.collection_mode))return;
  const source=scope.nativeModifierSource,instance=source.add(0x18).readU32();
  if(!instance)return;
  scope.settlementIssueSource={entry,source,instance,klass:source.readPointer(),
   data:source.add(0x538).readPointer(),cid:scope.source,uid:scope.sourceCharacterUid};
 }catch(e){/* Without an actor certificate a gap still belongs to the world. */}
}
function verifiedSettlementIssueSource(scope,when){
 const a=scope?.settlementIssueSource;
 if(!a||scope.mitigationModifierReused||damageModifier(scope.tid,scope.mod)!==scope||
    !sameMitigationTime(a.entry,when))return null;
 try{
  const source=scope.mod.readPointer();
  if(!source.equals(a.source)||source.add(0x18).readU32()!==a.instance||
     !source.readPointer().equals(a.klass)||!source.add(0x538).readPointer().equals(a.data)||
     entityId(source)!==a.cid||sourceCharacterUid(source,a.cid)!==a.uid)return null;
  const closing=stampEventScope({...when});
  if(!sameNativeEventScope(a.entry,closing))return null;
  const stamp=closing.native_autochess_scope;
  if(captureClockFrame()!==when.frame||captureClockFixed().toNumber()/4294967296!==when.game_time)return null;
  return {source:a.cid,source_character_uid:a.uid,frame:when.frame,game_time:when.game_time,
   native_autochess_scope:{...stamp}};
 }catch(e){return null;}
}
function settlementScopeQualification(scope,when,target=scope.target){
 if(!enemyDamageReady)return 'disabled';
 let current;
 try{current=readSettlementDomain(scope.mod.add(0x10).readPointer(),scope.mod,when);}
 catch(e){current={kind:'unknown'};}
 observeSettlementDomain(scope,current);
 if(target&&(!current.target||!target.equals(current.target)))
  observeSettlementDomain(scope,readSettlementDomain(target,scope.mod,when));
 const parent=scope.modifierScope;
 if(parent&&parent!==scope)observeSettlementDomain(parent,current);
 if(scope.settlementDomainUncertain||parent?.settlementDomainUncertain)return 'unknown';
 const retired=scope.settlementDomainRetiredCharacter?scope:parent?.settlementDomainRetiredCharacter?parent:null;
 if(retired){
  return !settlementDomainAmbiguous(scope)&&!settlementDomainAmbiguous(parent)&&
   retiredCharacterDomain(retired.settlementDomainEntry,current)?'outside':'unknown';
 }
 if(scope.settlementDomainPotential||parent?.settlementDomainPotential)return 'potential';
 const entry=scope.settlementDomainEntry;
 if(!sameSettlementDomain(entry,current))return 'unknown';
 if(current.kind==='unrelated')return 'outside';
 // Known non-HP operations do not require enemy damage certificates.
 if(current.type===4&&current.targetType===0||current.type===0&&current.targetType===5)return 'outside';
 return 'unknown';
}
function captureSettlementAnchor(scope,type){
 if(!enemyDamageReady||![1,2,3].includes(type)||!scope.source||!own(scope.source)||
    scope.mod.add(0x60).readS32()!==0||settlementScopeQualification(scope,scope.when)==='outside')return;
 scope.settlementCandidate=true;
 try{
  const source=scope.nativeModifierSource,target=scope.target;
  scope.settlementAnchor={source,sourceClass:source.readPointer(),sourceUid:source.add(0x18).readU32(),
   sourceId:scope.source,sourceCharacterUid:scope.sourceCharacterUid,
   target,targetClass:target.readPointer(),targetId:targetKey(target),
   targetUid:damageTargetUid(target),modifier:scope.mod,type,when:scope.when,
   identity:enemyDamageIdentity(target),entryAmount:fp(scope.mod.add(0x30))};
 }catch(e){/* An unreadable identity cannot authorize actor-local coverage. */}
}
function stableSettlementAnchor(scope,when,requireActor=true){
 const a=scope.settlementAnchor;
 try{return captureInvocationRead(scope,()=>{
  return !!a&&scope.tracksModifier&&!scope.mitigationModifierReused&&
   sameMitigationTime(scope.when,when)&&sameMitigationTime(a.when,when)&&
   damageModifier(scope.tid,scope.mod)===scope&&scope.mod.equals(a.modifier)&&
   (!requireActor||Number.isSafeInteger(a.sourceCharacterUid)&&a.sourceCharacterUid>0)&&a.sourceUid>0&&a.targetUid>0&&
   scope.source===a.sourceId&&scope.sourceCharacterUid===a.sourceCharacterUid&&
   scope.nativeModifierSource.equals(a.source)&&scope.mod.readPointer().equals(a.source)&&
   a.source.readPointer().equals(a.sourceClass)&&a.source.add(0x18).readU32()===a.sourceUid&&
   entityId(a.source)===a.sourceId&&sourceCharacterUid(a.source,a.sourceId)===a.sourceCharacterUid&&
   scope.target.equals(a.target)&&scope.mod.add(0x10).readPointer().equals(a.target)&&
   a.target.readPointer().equals(a.targetClass)&&targetKey(a.target)===a.targetId&&
   damageTargetUid(a.target)===a.targetUid&&scope.settlementTargetUid===a.targetUid&&
   scope.mod.add(0x60).readS32()===0&&scope.mod.add(0x68).readS32()===a.type&&!!a.identity&&
   a.identity.target===a.targetId&&a.identity.target_uid===String(a.targetUid)&&
   a.identity.target_instance===a.target.toString();
 });}catch(e){return false;}
}
function noteSettlementProofGap(scope,when,reason){
 if(!enemyDamageReady)return;
 const qualification=settlementScopeQualification(scope,when);
 if(qualification==='outside')return;
 scope.settlementFailureReason??=reason;
 if(qualification==='unknown'||!stableSettlementAnchor(scope,when))
  markDamageSettlementUnknown(when||scope.when,reason,scope);
}
function captureSettlementFailureSample(scope,when,reason,evidence={}){
 // Diagnostic copies only: never read native memory or authorize a settlement.
 if(!scope||!when||when.settlementScope!==damageSettlementScope)return;
 const anchor=scope.settlementAnchor,point=scope.settlementFailureCheckpoint??{},
  hp=scope.settlementObservedHp??scope.targetSettlementEvidence??scope.settlement?.evidence??{},
  identity=anchor?.identity??evidence,stamp=when.native_autochess_scope;
 const source=typeof scope.source==='string'?scope.source:null,
  sourceUid=Number.isSafeInteger(scope.sourceCharacterUid)?scope.sourceCharacterUid:null,
  target=typeof identity?.target==='string'?identity.target:null,
  targetUid=typeof identity?.target_uid==='string'?identity.target_uid:null,
  targetInstance=typeof identity?.target_instance==='string'?identity.target_instance:null;
 const text=value=>typeof value==='string'?value.slice(0,200):null,
  number=value=>Number.isFinite(value)?value:null;
 const key=JSON.stringify([damageSettlementScope,stamp?.scope_key??null,source,sourceUid,target,targetUid,reason]);
 if(damageSettlementFailureSamples.has(key)||damageSettlementFailureSamples.size>=DAMAGE_SETTLEMENT_FAILURE_SAMPLE_LIMIT)return;
 damageSettlementFailureSamples.set(key,{sequence,frame:when.frame,game_time:when.game_time,
  damage_settlement_scope:damageSettlementScope,reason:text(reason),method:text(scope.name),
  source,source_character_uid:sourceUid,target,target_uid:targetUid,target_instance:targetInstance,
  native_autochess_scope:stamp?{...stamp}:null,
  hp_before:number(point.hpBefore??hp.hp_before??scope.targetHit?.hpBefore??scope.hpBefore),
  hp_after:number(point.hpAfter??hp.hp_after),
  native_signed_delta:number(hp.real_delta??point.real??scope.outputSettlement?.real??scope.real),
  native_amount:number(point.nativeAmount??scope.outputSettlement?.amount??scope.amount),
  shield_absorbed:number(point.absorbed??scope.numericShield),shield_unknown:scope.numericShieldUnknown===true,
  cancel_reason:Number.isSafeInteger(point.cancel)?point.cancel:null,
  nested:point.nested===true||scope.targetSettlementNested===true||!!scope.nestedParentSettlementProof||!!scope.nestedElementalChainProof,
  settlement_ambiguous:scope.settlementAmbiguous===true,target_commit_rejected:scope.targetCommitRejected===true,
  output_ambiguous:scope.outputAmbiguous===true,
  nested_detail:scope.nestedSettlementFailure?text(JSON.stringify(scope.nestedSettlementFailure)):null});
}
function finalizeUnverifiedSettlement(scope,when){
 if(!enemyDamageReady)return;
 if(scope.settlementDomainEntry&&settlementScopeQualification(scope,when)==='outside')return;
 if(!scope.settlementCandidate||scope.settlementRecordEmitted||scope.settlementNoConsumption)return;
 let amount=null;
 try{amount=fp(scope.mod.add(0x30));}catch(e){/* Entry evidence still requires an explicit gap. */}
 const a=scope.settlementAnchor;
 if(!scope.settlementFailureReason&&!(amount>0)&&!(a?.entryAmount>0))return;
 const reason=scope.settlementFailureReason||'missing same-hit target or output settlement proof';
 noteSettlementProofGap(scope,when,reason);
 if(settlementScopeQualification(scope,when)!=='potential'||!stableSettlementAnchor(scope,when)){
  captureSettlementFailureSample(scope,when,reason);return;
 }
 // Required amount zero is the pre-existing incomplete-record placeholder,
 // never a certificate of zero HP consumption. Actual and nominal stay absent.
 damageSettlement(scope,a.target,0,when,{identity:a.identity,type:a.type,unverified:true,nativeModifierAmount:amount});
}
function finalizeModifierSettlement(scope,when){
 if(scope.settlementFinalizationAttempted)return;
 scope.settlementFinalizationAttempted=true;
 if(scope.outputSettlement){
  if(when&&sameMitigationTime(when,scope.when)&&!scope.mitigationModifierReused)
   flushOutputSettlement(scope,when);
  else noteSettlementProofGap(scope,when,'output did not share the modifier clock');
 }
 finalizeUnverifiedSettlement(scope,when);
}
function damageSettlementQuality(){
 return {damage_settlement_quality_version:1,enemy_damage_ready:enemyDamageReady,
  damage_settlement_scope:damageSettlementScope,enemy_damage_unknown:enemyDamageUnknown,
  damage_settlement_first_issue:damageSettlementIssue,damage_settlement_source_quality_version:1,
  damage_settlement_unlocalized_unknown:damageSettlementUnlocalizedUnknown,
  damage_settlement_source_issues:[...damageSettlementSourceIssues.values()],
  damage_settlement_failure_samples_version:1,
  damage_settlement_failure_samples:[...damageSettlementFailureSamples.values()]};
}
function checkedNativeView(address,size){
 const bytes=address.readByteArray(size);
 if(bytes===null||bytes.byteLength!==size)throw new Error('native block read unavailable');
 return new DataView(bytes);
}
function validatedClockStamp(view,closingFrame,allowRetry,withEventScope){
 const frame=view.getUint32(0,true),game_time=Number(view.getBigInt64(4,true))/4294967296;
 if(closingFrame!==frame){if(allowRetry)return null;throw new Error('unstable callback clock');}
 if(!Number.isFinite(game_time)||game_time<0)throw new Error('invalid clock');
 return damageSettlementStamp(frame,game_time,withEventScope);
}
function time(allowRetry = false, withEventScope = true) {
 // The closing frame remains a separate, independently captured witness.
 const bytes=captureRead('clock_open',()=>clock.readByteArray(12));
 if(bytes===null||bytes.byteLength!==12)throw new Error('callback clock unavailable');
 return validatedClockStamp(new DataView(bytes),captureClockFrame(),allowRetry,withEventScope);
}
function capturedUtf16(view){
 if(view.byteLength<12)throw new Error('bad string');
 const n=view.getInt32(8,true);
 if(n<0||n>200||view.byteLength!==12+2*n)throw new Error('bad string');
 let text='';for(let i=0;i<n;i++)text+=String.fromCharCode(view.getUint16(12+2*i,true));
 return text;
}

function buff(p){return {key:str(p.add(0x208).readPointer()),source:p.add(0x18).readPointer(),hasShield:p.add(0x27c).readU8()};}
// Modifier callbacks receive a pointer to m_source; the remaining fields follow
// the verified class metadata shifted by that header (value, realDelta, ...).
function targetHp(p){if(!p||p.isNull())return null;const value=fp(p.add(0x40));return Number.isFinite(value)&&value>=0&&value<1e9?value:null;}
function targetKey(p){if(!p||p.isNull())return null;try{const id=str(p.add(0x148).readPointer());return id||null;}catch(e){return null;}}
function directHealingProof(scope,amount){
 const result={amount,modifier_actual_delta:null,hp_before:null,hp_after:null,
  target:null,target_instance:null,target_uid:null};
 try{
  const delta=fp(scope.mod.add(0x48));
  if(Number.isFinite(delta))result.modifier_actual_delta=delta;
  const before=scope.healingTarget;
  const target=scope.mod.add(0x10).readPointer();
  if(!before||!before.id||!scope.target||target.isNull()||!scope.target.equals(target)
     ||targetKey(target)!==before.id
     ||before.uid!==null&&damageTargetUid(target)!==before.uid)return result;
  result.target=before.id;
  result.target_instance=target.toString();
  result.target_uid=before.uid===null?null:String(before.uid);
  if(Number.isFinite(scope.hpBefore))result.hp_before=scope.hpBefore;
  result.hp_after=targetHp(target);
 }catch(e){/* Raw settlement proof can retain partial observations. */}
 return result;
}
const shieldByFrame=new Map();
// Modifier& is borrowed only during the same-thread ApplyModifier invocation.
// The pointer may be reused later, so retain no target evidence after return.
const activeModifiers=new Map();
const activeApplyScopes=new Map();
let tracedModifierSequence=0,scopedTraceCount=0,scopedTraceDropped=0;
let nestedParentTraceCount=0,nestedParentTraceDropped=0;
const SCOPED_TRACE_LIMIT=4096;
// Optional, bounded research trace. It never changes event sequence or amount.
function traceModifierPhase(invocation,phase){
 if(!probeAdapter.scopedModifierTrace||!traceHpContext||!invocation.mod)return;
 if(scopedTraceCount>=SCOPED_TRACE_LIMIT){scopedTraceDropped++;return;}
 const diagnostic={diagnostic_version:1,phase,hook:invocation.name,thread:invocation.tid,
  sequence:++hpSequence,frame:invocation.when?.frame,game_time:invocation.when?.game_time};
 try{
  const scope=invocation.tracksModifier?invocation:damageModifier(invocation.tid,invocation.mod);
  const target=invocation.mod.add(0x10).readPointer(),source=invocation.mod.readPointer();
  diagnostic.modifier_scope=scope?.traceModifierId??null;
  diagnostic.modifier_instance=invocation.mod.toString();
  diagnostic.source=invocation.source??null;
  diagnostic.source_instance=source.toString();
  diagnostic.target=targetKey(target);diagnostic.target_instance=target.toString();
  diagnostic.target_uid=damageTargetUid(target);
  diagnostic.real_delta_entry=invocation.real??null;
  diagnostic.real_delta_current=fp(invocation.mod.add(0x48));
  diagnostic.amount_current=fp(invocation.mod.add(0x30));
  diagnostic.hp_before=invocation.hpBefore??null;diagnostic.hp_current=targetHp(target);
  diagnostic.output_real=scope?.outputSettlement?.real??null;
  diagnostic.target_hit=!!scope?.targetHit;
  diagnostic.target_hit_ambiguous=!!scope?.targetHitAmbiguous;
  diagnostic.output_ambiguous=!!scope?.outputAmbiguous;
  diagnostic.modifier_reused=!!scope?.mitigationModifierReused;
  diagnostic.source_matches_scope=!!scope?.nativeModifierSource?.equals(source);
  diagnostic.target_matches_scope=!!scope?.target?.equals(target);
  diagnostic.scope_target_uid=scope?.settlementTargetUid??null;
  diagnostic.target_entry_amount=scope?.targetHit?.commit?.entryAmount??null;
  diagnostic.target_return_amount=scope?.targetHit?.commit?.returnAmount??null;
 }catch(error){diagnostic.read_error=String(error);}
 scopedTraceCount++;
 recordHpContext(diagnostic,false);
}
function traceNestedParents(child,phase){
 if(!traceHpContext||!traceNestedParent||!child.mod||!child.target)return;
 for(const parent of activeModifiers.get(child.tid)||[]){
  if(parent===child||!parent.target?.equals(child.target)||!sameMitigationTime(parent.when,child.when))continue;
  if(nestedParentTraceCount>=SCOPED_TRACE_LIMIT){nestedParentTraceDropped++;continue;}
  const diagnostic={diagnostic_version:2,kind:'nested_parent_checkpoint',phase,
   sequence:++hpSequence,frame:child.when.frame,game_time:child.when.game_time,
   thread:child.tid,parent_scope:parent.traceModifierId??null,child_scope:child.traceModifierId??null,
   parent_modifier:parent.mod.toString(),child_modifier:child.mod.toString(),
   parent_source:parent.source??null,parent_source_character_uid:parent.sourceCharacterUid??null};
  try{
   diagnostic.target=targetKey(child.target);diagnostic.target_instance=child.target.toString();
   diagnostic.target_uid=damageTargetUid(child.target);diagnostic.target_hp=targetHp(child.target);
   diagnostic.parent_hp_before=parent.hpBefore??null;
   diagnostic.parent_target_hit_hp_before=parent.targetHit?.hpBefore??null;
   diagnostic.parent_target_hit_uid=parent.targetHit?.uid??null;
   diagnostic.parent_real_delta=captureInvocationRead(parent,()=>fp(parent.mod.add(0x48)));diagnostic.parent_amount=captureInvocationRead(parent,()=>fp(parent.mod.add(0x30)));
   diagnostic.parent_source_instance=captureInvocationRead(parent,()=>parent.mod.readPointer()).toString();
   diagnostic.parent_source_matches=!!parent.nativeModifierSource?.equals(captureInvocationRead(parent,()=>parent.mod.readPointer()));
   diagnostic.parent_target_matches=captureInvocationRead(parent,()=>parent.mod.add(0x10).readPointer()).equals(child.target);
   diagnostic.parent_target_hit_ambiguous=!!parent.targetHitAmbiguous;
   diagnostic.parent_modifier_reused=!!parent.mitigationModifierReused;
   diagnostic.parent_numeric_shield=parent.numericShield??null;
   diagnostic.parent_numeric_shield_unknown=!!parent.numericShieldUnknown;
   diagnostic.child_hp_before=child.hpBefore??null;
   diagnostic.child_real_delta=fp(child.mod.add(0x48));diagnostic.child_amount=fp(child.mod.add(0x30));
   diagnostic.child_type=child.mod.add(0x68).readS32();
   diagnostic.child_source_instance=child.mod.readPointer().toString();
  }catch(error){diagnostic.read_error=String(error);}
  nestedParentTraceCount++;
  recordHpContext(diagnostic,false);
 }
}
function traceNestedParentFinal(parent){
 if(!traceHpContext||!traceNestedParent||!parent.targetSettlementNested)return;
 if(nestedParentTraceCount>=SCOPED_TRACE_LIMIT){nestedParentTraceDropped++;return;}
 const diagnostic={diagnostic_version:2,kind:'nested_parent_checkpoint',phase:'parent_return',
  sequence:++hpSequence,frame:parent.when.frame,game_time:parent.when.game_time,thread:parent.tid,
  parent_scope:parent.traceModifierId??null,parent_modifier:parent.mod.toString(),
  parent_source:parent.source??null,parent_source_character_uid:parent.sourceCharacterUid??null};
 try{
  diagnostic.target=targetKey(parent.target);diagnostic.target_instance=parent.target.toString();
  diagnostic.target_uid=damageTargetUid(parent.target);diagnostic.target_hp=targetHp(parent.target);
  diagnostic.parent_hp_before=parent.hpBefore??null;
  diagnostic.parent_target_hit_hp_before=parent.targetHit?.hpBefore??null;
  diagnostic.parent_real_delta=captureInvocationRead(parent,()=>fp(parent.mod.add(0x48)));diagnostic.parent_amount=captureInvocationRead(parent,()=>fp(parent.mod.add(0x30)));
  diagnostic.parent_source_matches=!!parent.nativeModifierSource?.equals(captureInvocationRead(parent,()=>parent.mod.readPointer()));
  diagnostic.parent_target_matches=captureInvocationRead(parent,()=>parent.mod.add(0x10).readPointer()).equals(parent.target);
  diagnostic.parent_target_hit_ambiguous=!!parent.targetHitAmbiguous;
  diagnostic.parent_modifier_reused=!!parent.mitigationModifierReused;
 }catch(error){diagnostic.read_error=String(error);}
 nestedParentTraceCount++;
 recordHpContext(diagnostic,false);
}
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
 if(probeAdapter.scopedModifierTrace&&traceHpContext)scope.traceModifierId=++tracedModifierSequence;
}
function leaveModifier(scope){
 const scopes=activeModifiers.get(scope.tid);
 if(!scopes)return;
 const index=scopes.lastIndexOf(scope);
 if(index!==-1)scopes.splice(index,1);
 if(!scopes.length)activeModifiers.delete(scope.tid);
}
function possiblySameSettlementTarget(left,right){
 return !left||left.isNull()||!right||right.isNull()||left.equals(right);
}
function activeTargetSettlementOverlap(scope,target){
 const local=activeApplyScopes.get(scope.tid);
 if(activeApplyScopes.size===1&&local?.length===1)return false;
 for(const [thread,calls] of activeApplyScopes){
  let afterParent=thread!==scope.tid;
  for(const call of calls){
   if(call===scope){afterParent=true;continue;}
   // Other targets on this thread can be real ancestors. Only later calls,
   // foreign-thread writers, and any shared Modifier can overlap this hit.
   if(call.mod?.equals(scope.mod)||afterParent&&possiblySameSettlementTarget(call.target,target))return true;
  }
 }
 return false;
}
function beginTargetSettlement(invocation,scope){
 try{
  if(knownNonEnemyTarget(invocation.target))return;
  const hit=scope.targetHit,source=invocation.mod.readPointer();
  if(!hit||!Number.isFinite(hit.hpBefore)||scope.targetHitAmbiguous||!scope.target?.equals(invocation.target)||
     !scope.mod.equals(invocation.mod)||!scope.nativeModifierSource?.equals(source)||
     !hit.uid||hit.uid!==scope.settlementTargetUid||!sameMitigationTime(scope.when,invocation.when))return;
  const type=invocation.mod.add(0x68).readS32();
  if(![1,2,3].includes(type)||invocation.mod.add(0x60).readS32()!==0)return;
  const sourceUid=source.add(0x18).readU32();if(!sourceUid)return;
  hit.commit={source,sourceUid,sourceClass:source.readPointer(),targetClass:hit.target.readPointer(),
    targetKey:targetKey(hit.target),modifier:invocation.mod,entryAmount:invocation.amount,type,
    when:invocation.when,returned:false,invalid:false};
  if(scope.nestedModifierConflict||scope.mitigationModifierReused||activeTargetSettlementOverlap(scope,hit.target)){
   scope.targetSettlementNested=true;
   noteNestedSettlementFailure(scope,'target_entry','overlapping_target_or_modifier');
  }else{
   // The first independent HP observation starts this settlement interval.
   // An already ended writer cannot participate in its subsequent debit.
   if(scope.nestedCrossThreadActivity)scope.nestedCrossThreadActivity=false;
   if(scope.nestedBeforeTarget){
    scope.nestedBeforeTarget=false;
    scope.targetSettlementNested=false;
   }
  }
  invocation.targetSettlementScope=scope;invocation.targetSettlementHit=hit;
 }catch(e){/* Missing target proof keeps strict settlement partial. */}
}
function targetSettlementIdentity(scope,hit,when,allowShieldCancel=false){
 return captureInvocationRead(scope,()=>{
 const commit=hit?.commit;
 return !!commit&&!commit.invalid&&scope.targetHit===hit&&!scope.targetHitAmbiguous&&
  !scope.nestedModifierConflict&&!scope.targetObservationCrossThreadActivity&&
  !scope.outputAmbiguous&&!scope.settlementAmbiguous&&!scope.mitigationModifierReused&&
  sameMitigationTime(scope.when,when)&&sameMitigationTime(commit.when,when)&&
  damageModifier(scope.tid,scope.mod)===scope&&scope.mod.equals(commit.modifier)&&
  scope.nativeModifierSource?.equals(commit.source)&&scope.mod.readPointer().equals(commit.source)&&
  commit.source.add(0x18).readU32()===commit.sourceUid&&entityId(commit.source)===scope.source&&
  commit.source.readPointer().equals(commit.sourceClass)&&hit.target.readPointer().equals(commit.targetClass)&&
  targetKey(hit.target)===commit.targetKey&&scope.target?.equals(hit.target)&&
  scope.mod.add(0x10).readPointer().equals(hit.target)&&damageTargetUid(hit.target)===hit.uid&&
  hit.uid===scope.settlementTargetUid&&scope.mod.add(0x60).readS32()===0&&
  scope.mod.add(0x68).readS32()===commit.type&&(allowShieldCancel||scope.mod.add(0x70).readS32()===0); });
}
function numericFullBlockSettlement(scope,hit,when,amount){
 return captureInvocationRead(scope,()=>{
 const absorbed=scope.numericShield,incoming=scope.firstShieldIncoming;
 return !scope.targetSettlementNested&&amount===0&&!scope.numericShieldUnknown&&!scope.unmatchedShield&&
  Number.isFinite(absorbed)&&absorbed>0&&Number.isFinite(incoming)&&
  Math.abs(absorbed-incoming)<=Math.max(0.01,incoming*1e-6)&&
  targetSettlementIdentity(scope,hit,when,true)&&fp(scope.mod.add(0x30))===0&&
  fp(scope.mod.add(0x48))===0&&targetHp(hit.target)===hit.hpBefore; });
}
function observeNestedTargetSettlement(invocation){
 traceNestedParents(invocation,'child_entry');
 try{
  const target=invocation.target,hasTarget=target&&!target.isNull();
  const raw=activeApplyScopes.get(invocation.tid);
  if(activeApplyScopes.size>1||raw?.length>1){
   // The money stack excludes foreign/unknown sources. Borrowed modifier
   // aliases and concurrent target writers must still be visible here.
   for(const [thread,other] of activeApplyScopes){
    for(const concurrent of other){
     if(concurrent===invocation)continue;
     if(concurrent.mod?.equals(invocation.mod)){
      concurrent.nestedModifierConflict=true;invocation.nestedModifierConflict=true;
     }
     if(thread!==invocation.tid&&possiblySameSettlementTarget(concurrent.target,target)){
      concurrent.nestedCrossThreadActivity=true;invocation.nestedCrossThreadActivity=true;
      if(concurrent.targetHit)concurrent.targetObservationCrossThreadActivity=true;
     }
    }
   }
  }
  const scopes=activeModifiers.get(invocation.tid);
  if(!hasTarget||!scopes?.length)return;
  const type=invocation.mod.add(0x68).readS32();
  if(![1,2,3,4,5].includes(type))return;
  for(const previous of scopes){
   if(previous.target?.equals(invocation.target)&&sameMitigationTime(previous.when,invocation.when)){
    if(!previous.targetHit){
     // Keep the original conservative state until the first target read can
     // prove that no child is still in flight. A missing read never clears it.
     previous.nestedBeforeTarget=true;previous.targetSettlementNested=true;
     continue;
    }
    if(previous.targetSettlementNested){
     previous.nestedTargetCheckpoint=null;
     const chain=previous.nestedElementalChain,last=chain&&chain.steps[chain.steps.length-1];
     if(chain){
      const pending=type===5&&!chain.pending&&last&&chain.steps.length<NESTED_ELEMENTAL_CHAIN_LIMIT?
       captureNestedElementalStep(previous,invocation,chain.checkpoint,last.hpAfter):null;
      if(pending)chain.pending=pending;
      else{
       noteNestedSettlementFailure(previous,'child_entry',chain.pending?'overlapping_child':
        chain.steps.length>=NESTED_ELEMENTAL_CHAIN_LIMIT?'chain_limit':'discontinuous_or_unsupported_child',
        type,invocation.settlementDomainEntry?.targetType);
       previous.nestedElementalChain=null;
      }
     }
    }else{
     const checkpoint=captureParentBeforeNestedSettlement(previous,invocation);
     previous.nestedTargetCheckpoint=checkpoint;
     if(checkpoint&&!checkpoint.ordinary){
      const pending=captureNestedElementalStep(previous,invocation,checkpoint,checkpoint.hpAfter);
      if(pending)previous.nestedElementalChain={checkpoint,pending,steps:[]};
      else noteNestedSettlementFailure(previous,'child_entry','unverified_elemental_child_entry',
       type,invocation.settlementDomainEntry?.targetType);
     }
     if(!checkpoint)noteNestedSettlementFailure(previous,'child_entry','missing_parent_checkpoint',
      type,invocation.settlementDomainEntry?.targetType);
    }
    previous.targetSettlementNested=true;
   }
  }
 }catch(e){/* No target certificate is added from unreadable callbacks. */}
}
function finishTargetSettlement(invocation,when){
 const scope=invocation.targetSettlementScope,hit=invocation.targetSettlementHit;
 if(!scope||!hit?.commit)return;
 try{
  const amount=fp(scope.mod.add(0x30));
  if(!targetSettlementIdentity(scope,hit,when)&&
     !numericFullBlockSettlement(scope,hit,when,amount)){
   // Keep this identity witness separate from a normal settlement return.
   // The enclosing modifier still needs to prove zero consumption.
   if(scope.mod.add(0x70).readS32()===3&&stableSettlementAnchor(scope,when)&&
      targetSettlementIdentity(scope,hit,when,true)){
    hit.commit.cancelReturnWhen=when;
    return;
   }
   hit.commit.invalid=true;return;
  }
  // Native listeners may adjust the amount before or after this return.
  // Keep the measured stage value, never use it as an identity invariant.
  hit.commit.returnAmount=amount;
  hit.commit.returned=true;hit.commit.returnWhen=when;
 }catch(e){hit.commit.invalid=true;}
}
function verifiedZeroCancellation(scope,hit,when,amount){
 try{
  const commit=hit?.commit;
  const returned=commit?.returned?commit.returnWhen:commit?.cancelReturnWhen;
  if(amount!==0||!returned||!sameMitigationTime(returned,when)||
     scope.targetSettlementNested||scope.outputSeen||scope.outputSettlement||scope.settlementRecordEmitted||
     scope.settlementFailureReason||scope.numericShieldUnknown||scope.unmatchedShield||
     scope.unclockedShield||(scope.numericShield??0)!==0||
     !stableSettlementAnchor(scope,when)||!targetSettlementIdentity(scope,hit,when,true)||
     settlementScopeQualification(scope,when,hit.target)!=='potential')return false;
  const evidence=overkillEvidence(scope.mod,hit.target,hit.hpBefore,when.frame);
  // The target return anchors identity; its amount can still be a proposal.
  // A late cancellation must prove the final native amount and HP instead.
  return evidence?.cancel_reason===3&&fp(scope.mod.add(0x30))===0&&evidence.real_delta===0&&
   evidence.hp_before>0&&evidence.hp_before===evidence.hp_after&&evidence.shield_absorbed===0;
 }catch(e){return false;}
}
// One independent native HP writer may close the same modifier when the
// target/output notifications are absent. This supplies witnesses to the
// canonical verifier; it never computes damage from the nominal amount.
function noteModifierHpWriterAttempt(target){
 if(!activeApplyScopes.size)return;
 for(const calls of activeApplyScopes.values())for(const scope of calls)
  if(scope.target?.equals(target))scope.hpWriterCount=(scope.hpWriterCount||0)+1;
}
function beginModifierHpWriter(write,klass,label){
 const calls=activeApplyScopes.get(write.thread);
 if(activeApplyScopes.size!==1||calls?.length!==1)return;
 const scope=calls[0],a=scope.settlementAnchor;
 if(!a||!scope.target?.equals(write.enemy)||a.targetUid!==write.uid||
    !a.targetClass.equals(klass)||!sameMitigationTime(scope.when,write.when))return;
 if(scope.hpWriterCount!==1)return;
 write.modifierScope=scope;write.modifierMethod=label;
}
function finishModifierHpWriter(write,end,hp){
 const scope=write.modifierScope;
 if(!scope||scope.hpWriterCount!==1||write.maxHpBefore!==null||
    scope.hpWriterEvidence||!sameMitigationTime(write.when,end)||
    activeApplyScopes.size!==1||activeApplyScopes.get(write.thread)?.length!==1||
    activeApplyScopes.get(write.thread)[0]!==scope)return;
 scope.hpWriterEvidence={target:write.enemy,uid:write.uid,hpBefore:write.hpBefore,hpAfter:hp,
  when:end,method:write.modifierMethod};
}
function verifiedTargetSettlement(scope,target,when,amount,type){
 if(!enemyDamageReady)return null;
 const qualification=settlementScopeQualification(scope,when,target);
 if(qualification==='outside')return null;
 function reject(reason){
  scope.targetCommitRejected=true;
  noteSettlementProofGap(scope,when,reason);
  return null;
 }
 try{
  const hit=scope.targetHit,commit=hit?.commit;
  if(qualification==='unknown')return reject('unverified enemy settlement domain');
  if(scope.settlementAnchor&&!stableSettlementAnchor(scope,when,false))
   return reject('modifier scope identity or clock changed');
  if(!commit){
   const writer=scope.hpWriterEvidence;
   if(!writer||scope.hpWriterCount!==1||scope.targetHit||scope.outputSeen||scope.outputSettlement||
      scope.hpWriterUncertain||scope.targetSettlementNested||scope.nestedBeforeTarget||scope.nestedCrossThreadActivity||
      scope.targetObservationCrossThreadActivity||scope.nestedModifierConflict||
      scope.targetHitAmbiguous||scope.outputAmbiguous||scope.settlementAmbiguous||
      activeTargetSettlementOverlap(scope,target)||!stableSettlementAnchor(scope,when)||
      !writer.target.equals(target)||writer.uid!==scope.settlementTargetUid||
      !sameMitigationTime(writer.when,when)||writer.hpBefore!==scope.hpBefore||
      targetHp(target)!==writer.hpAfter)return null;
   const evidence=overkillEvidence(scope.mod,target,writer.hpBefore,when.frame,scope);
   if(!evidence||evidence.hp_after!==writer.hpAfter||evidence.cancel_reason!==0||
      !Number.isFinite(amount)||amount<0||amount+0.01<-evidence.real_delta+(scope.numericShield||0)||
      fp(scope.mod.add(0x30))!==amount)return null;
   scope.hpWriterSettlementMethod=writer.method;
   scope.targetSettlementEvidence=evidence;
   return evidence;
  }
  if(scope.targetSettlementNested){
   const nested=verifiedParentBeforeNestedSettlement(scope,target,when,amount,type);
   if(nested){scope.targetSettlementEvidence=nested;return nested;}
   noteNestedSettlementFailure(scope,'parent_close','unverified_parent_or_incomplete_chain');
   return reject('nested modifier changed target settlement scope');
  }
  // A fully witnessed block can zero m_value during the target callback.
  // Its existing numeric-shield certificate is independent of this HP fallback.
  if(commit.returned&&sameMitigationTime(commit.returnWhen,when)&&
     numericFullBlockSettlement(scope,hit,when,amount))return null;
  if(!commit.returned||commit.type!==type||
     !target?.equals(hit.target)||!sameMitigationTime(commit.returnWhen,when)||
     !targetSettlementIdentity(scope,hit,when))return reject('target settlement identity or clock changed');
  // OnTakeDamage can return before the engine commits HP and realDelta.
  // Its unique same-scope target/HP-before proof is checked again against
  // the final signed Modifier result and actual HP at ApplyModifier return.
  const evidence=overkillEvidence(scope.mod,target,hit.hpBefore,when.frame,scope);
  if(evidence)scope.settlementObservedHp=evidence;
  if(!evidence||!targetSettlementIdentity(scope,hit,when)||
     fp(scope.mod.add(0x30))!==amount)return reject('target settlement final HP proof unavailable');
  // Final m_value is the nominal settlement amount. Its earlier stage values
  // can differ; only the final value must cover the actually witnessed HP and
  // same-hit numeric shield components. Never infer absorption from a delta.
  const shield=scope.numericShield>0?scope.numericShield:0;
  if(!Number.isFinite(amount)||amount<0||amount+0.01<-evidence.real_delta+shield)
   return reject('final nominal amount contradicts HP or numeric shield consumption');
  if(amount===0&&evidence.real_delta===0&&!(shield>0)&&
     (commit.entryAmount>0||commit.returnAmount>0||scope.mitigation?.after>0))
   return reject('zeroed positive modifier lacks numeric absorption proof');
  scope.targetSettlementEvidence=evidence;
  return evidence;
 }catch(e){return reject('target settlement proof could not be read');}
}
function shieldModifier(thread,modifier,target,when){
 if(!modifier)return null;
 const scope=damageModifier(thread,modifier);
 if(!scope||scope.mitigationModifierReused||!scope.target?.equals(target)||
    !sameMitigationTime(scope.when,when)||!scope.settlementTargetUid||
    damageTargetUid(target)!==scope.settlementTargetUid)return null;
 return scope;
}
function markUnmatchedShield(thread,modifier,target,when){
 // A copied Modifier& on this thread or the same reference on another
 // thread can flag uncertainty, but cannot supply a shield amount.
 for(const [ownerThread,scopes] of activeModifiers){
  for(const scope of scopes){
   if(!scope.settlementTargetUid||!sameMitigationTime(scope.when,when))continue;
   const sameModifier=scope.mod.equals(modifier);
   const sameThreadTarget=ownerThread===thread&&target&&!target.isNull()&&scope.target?.equals(target);
   if(sameModifier||sameThreadTarget){
    scope.numericShieldUnknown=true;
    scope.unmatchedShield=true;
   }
  }
 }
}
function recordElementalBar(scope,when,amount){
 if(scope.elementalBarRecorded)return;
 const target=scope.elementalBarTarget;
 if(!target)return;
 const evidence={target_instance:target.pointer.toString()};
 if(target.uid&&damageTargetUid(target.pointer)===target.uid)evidence.target_uid=String(target.uid);
 const proof={method:scope.name,type:0,target:5},tokenHost=autoTokenHostEvidence(scope,when);
 if(tokenHost)proof.native_autochess_token_host=tokenHost;
 const characterForm=autoCharacterFormEvidence(scope,when);
 if(characterForm)proof.native_autochess_character_form=characterForm;
 if(scope.elementalBarBirthReturn)proof.native_enemy_bar_birth=scope.elementalBarBirthReturn;
 record(scope.source,'elemental_bar',amount,when,proof,evidence,scope.sourceCharacterUid);
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
 if(bar.mitigationModifierReused||captureInvocationRead(bar,()=>bar.mod.add(0x60).readS32())!==5||
    captureInvocationRead(bar,()=>bar.mod.add(0x68).readS32())!==0||
    !captureInvocationRead(bar,()=>bar.mod.add(0x10).readPointer()).equals(target)||captureInvocationRead(bar,()=>entityId(bar.mod.readPointer()))!==bar.source)return;
 // A full bar can invoke the HP settlement before its own ApplyModifier
 // returns. Publish the verified triggering bar first, once per invocation.
 recordElementalBar(bar,when,captureInvocationRead(bar,()=>fp(bar.mod.add(0x30))));
}
// Absorption observed in a frame but not verifiable for a target cannot prove
// that a lethal hit's excess was not absorbed, so its overkill stays unknown.
const shieldUnknownByFrame=new Map();
function markShieldUnknown(frame,target){
 if(!Number.isFinite(frame)){
  // A shield callback with no clock cannot certify zero for an open modifier.
  for(const modifiers of activeModifiers.values())
   for(const scope of modifiers)scope.unclockedShield=true;
  return;
 }
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
function overkillEvidence(mod,targetPtr,hpBefore,frame,diagnosticScope=null){
 try{
  if(hpBefore===null||hpBefore===undefined||!targetPtr||targetPtr.isNull())return null;
  const hpAfter=targetHp(targetPtr),key=targetKey(targetPtr),real=fp(mod.add(0x48));
  if(!key||hpAfter===null||!Number.isFinite(real)||real>0||
     Math.abs((hpBefore-hpAfter)+real)>Math.max(0.01,Math.abs(real)*1e-6)){
   if(diagnosticScope)diagnosticScope.settlementObservedHp={hp_before:hpBefore,hp_after:hpAfter,real_delta:real};
   return null;
  }
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
function damageTargetUid(target){
 if(!enemyEntryConfig||!target||target.isNull())return null;
 try{return target.add(0x18).readU32()||null;}catch(e){return null;}
}
function captureParentBeforeNestedSettlement(parent,child){
 try{
  const hit=parent.targetHit,commit=hit?.commit;
  if(!commit||commit.returned&&!sameMitigationTime(commit.returnWhen,child.when)||
     !targetSettlementIdentity(parent,hit,child.when))return null;
  const childType=child.mod.add(0x68).readS32();
  const ordinary=[1,2,3].includes(childType);
  if((!ordinary&&childType!==5)||
     child.mod.add(0x60).readS32()!==0||child.mod.add(0x70).readS32()!==0||
     damageTargetUid(child.target)!==hit.uid||!child.mod.add(0x10).readPointer().equals(hit.target)||
     parent.numericShieldUnknown||parent.unmatchedShield||parent.numericShield>0||
     shieldContextUnknown(child.when.frame,hit.target)||shieldTotal(child.when.frame,hit.target)!==0)return null;
  const {real,hp,amount}=captureInvocationRead(parent,()=>({real:fp(parent.mod.add(0x48)),hp:targetHp(hit.target),amount:fp(parent.mod.add(0x30))}));
  // A same-target child can settle inside the parent's active Unit callback.
  // Anchor the untouched parent HP before the child and verify its own signed
  // delta when it closes. Ordinary children use the existing single-child proof.
  if(!commit.returned&&real===0&&hp>0&&hp===hit.hpBefore&&hp===child.hpBefore)
   return {child,ordinary,ordering:'children_before_parent',when:child.when,uid:hit.uid,
    real,hpBefore:hp,hpAfter:hp};
  if(!commit.returned)return null;
  if(!Number.isFinite(real)||!(real<0)||!Number.isFinite(hp)||!(hp>0)||
     hp!==child.hpBefore||!Number.isFinite(hit.hpBefore)||
     Math.abs(hit.hpBefore-hp+real)>Math.max(0.01,Math.abs(real)*1e-6)||
     !Number.isFinite(amount)||amount+0.01<-real)return null;
  // This is the parent's signed native result at the child's entry, paired
  // with the target-side HP-before. Nominal damage never supplies its HP.
  return {child,ordinary,when:child.when,uid:hit.uid,real,hpBefore:hit.hpBefore,hpAfter:hp};
 }catch(e){return null;}
}
function rememberNestedOrdinarySettlement(child,when,evidence,event){
 // The child supplies its own checked target callback and signed result.
 // Never recover either hit by subtracting nominal values or final HP totals.
 // A fully certified child kill is also a closed HP interval: its zero
 // endpoint cannot erase the parent's independently captured prior debit.
 if(event?.settlement_complete!==true||!child.targetSettlementEvidence||
    child.targetSettlementEvidence!==evidence||child.targetSettlementNested||
    evidence.shield_absorbed!==0||!(evidence.real_delta<0)||
    !(evidence.hp_before>evidence.hp_after)||!(evidence.hp_after>=0)||
    evidence.killed!==(evidence.hp_after===0)||
    event.amount!==-evidence.real_delta)return;
 const scopes=activeModifiers.get(child.tid);
 if(!scopes||scopes.length<2||scopes[scopes.length-1]!==child)return;
 const parents=scopes.slice(0,-1).filter(parent=>
  parent.nestedTargetCheckpoint?.ordinary&&parent.nestedTargetCheckpoint.child===child);
 for(const parent of parents){
  const checkpoint=parent.nestedTargetCheckpoint;
  parent.nestedOrdinarySettlement=parents.length===1&&parent.nestedOrdinarySettlement===undefined&&
    checkpoint.hpAfter===evidence.hp_before&&sameMitigationTime(checkpoint.when,when)?
    {child,when,event,uid:checkpoint.uid,hpBefore:evidence.hp_before,
     hpAfter:evidence.hp_after,real:evidence.real_delta}:null;
 }
}
const NESTED_ELEMENTAL_CHAIN_LIMIT=16;
function noteNestedSettlementFailure(scope,stage,reason,childType,childTargetType){
 const commit=scope.targetHit?.commit;
 scope.nestedSettlementFailure??={stage,reason,verified_children:scope.nestedElementalChain?.steps.length??0,
  parent_target_stage:commit?(commit.returned?'returned':'in_progress'):'unavailable',
  ...(Number.isInteger(childType)?{child_type:childType}:{}),
  ...(Number.isInteger(childTargetType)?{child_target_type:childTargetType}:{})};
}
function matchesOpeningAutoChessScope(scope,when){
 const stamp=when.native_autochess_scope;
 return scope.nativeScopeKey===undefined?stamp===undefined:
  !!scope.nativeScopeKey&&stamp?.status==='available'&&stamp.scope_key===scope.nativeScopeKey;
}
function captureNestedElementalStep(parent,child,checkpoint,hpBefore){
 try{
  const commit=parent.targetHit.commit;
  if(parent.nestedCrossThreadActivity||child.nestedCrossThreadActivity||
     parent.nestedModifierConflict||child.nestedModifierConflict||
     !(hpBefore>0)||child.hpBefore!==hpBefore||targetHp(child.target)!==hpBefore||
     !sameMitigationTime(checkpoint.when,child.when)||
     !targetSettlementIdentity(parent,parent.targetHit,child.when)||captureInvocationRead(parent,()=>fp(parent.mod.add(0x48)))!==checkpoint.real||
     child.mod.add(0x60).readS32()!==0||child.mod.add(0x70).readS32()!==0||
     damageTargetUid(child.target)!==checkpoint.uid||!child.mod.add(0x10).readPointer().equals(child.target)||
     parent.numericShieldUnknown||parent.unmatchedShield||parent.numericShield>0||
     shieldContextUnknown(child.when.frame,child.target)||shieldTotal(child.when.frame,child.target)!==0)return null;
  const opening=stampEventScope({...child.when});
  if(!matchesOpeningAutoChessScope(parent,opening))return null;
  const targetId=commit.targetKey,targetClass=commit.targetClass;
  const source=child.mod.readPointer();
  if(source.isNull())return {child,source,cid:null,hpBefore,opening,targetId,targetClass};
  const cid=entityId(source),uid=sourceCharacterUid(source,cid),instance=source.add(0x18).readU32();
  if(!own(cid)||!uid||!instance)return null;
  return {child,source,cid,uid,instance,klass:source.readPointer(),data:source.add(0x538).readPointer(),
   hpBefore,opening,targetId,targetClass};
 }catch(e){return null;}
}
function matchesNestedElementalRecords(step,uid,target,when){
 const {child,cid,settlement,hpBefore,hpAfter,real}=step,killed=hpAfter===0;
 return child.settlementRecordEmitted&&settlement===child.settlementRecord&&
  settlement?.kind==='damage_settlement'&&settlement.source===cid&&settlement.source_character_uid===step.uid&&
  settlement.damage_type===5&&settlement.settlement_complete===true&&settlement.shield_absorbed===0&&
  settlement.killed===killed&&settlement.amount===-real&&settlement.target_uid===String(uid)&&
  settlement.target===step.targetId&&settlement.target_instance===target.toString()&&
  settlement.frame===when.frame&&settlement.game_time===when.game_time&&
  settlement.hp_before===hpBefore&&settlement.hp_after===hpAfter&&settlement.real_delta===real&&
  settlement.cancel_reason===0&&Number.isSafeInteger(settlement.sequence)&&
  sameNativeEventScope(settlement,when);
}
function closeNestedElementalStep(step,uid,when,evidence){
 const {child,source,cid,hpBefore}=step,target=child.target;
 try{
  if(child.sourceReadFailed||child.targetSettlementNested||child.mitigationModifierReused||
     child.nestedCrossThreadActivity||child.nestedModifierConflict||
     !sameMitigationTime(child.when,when)||!sameNativeEventScope(step.opening,when)||child.elementalTargetUid!==uid||
     !child.nativeModifierSource?.equals(source)||!child.mod.readPointer().equals(source)||
     child.source!==cid||child.sourceCharacterUid!==step.uid||
     (cid===null?!child.nativeSourceNull:source.add(0x18).readU32()!==step.instance||
      !source.readPointer().equals(step.klass)||!source.add(0x538).readPointer().equals(step.data)||
      entityId(source)!==cid||sourceCharacterUid(source,cid)!==step.uid)||
     damageModifier(child.tid,child.mod)!==child||damageTargetUid(target)!==uid||
     targetKey(target)!==step.targetId||!target.readPointer().equals(step.targetClass)||
     !target.equals(child.mod.add(0x10).readPointer())||child.mod.add(0x60).readS32()!==0||
     child.mod.add(0x68).readS32()!==5||child.mod.add(0x70).readS32()!==0||
     evidence?.cancel_reason!==0||evidence.shield_absorbed!==0||evidence.hp_before!==hpBefore||
     !(evidence.real_delta<0)||!(evidence.hp_after>=0)||!(hpBefore>evidence.hp_after)||
     evidence.killed!==(evidence.hp_after===0)||targetHp(target)!==evidence.hp_after||
     Math.abs(hpBefore-evidence.hp_after+evidence.real_delta)>Math.max(0.01,Math.abs(evidence.real_delta)*1e-6)||
     fp(child.mod.add(0x48))!==evidence.real_delta||
     shieldContextUnknown(when.frame,target)||shieldTotal(when.frame,target)!==0)return null;
  const closed={...step,settlement:child.settlementRecord,hpAfter:evidence.hp_after,real:evidence.real_delta};
  return matchesNestedElementalRecords(closed,uid,target,when)?closed:null;
 }catch(e){return null;}
}
function rememberNestedElementalSettlement(child,when,evidence){
 const scopes=activeModifiers.get(child.tid);
 if(!scopes||scopes.length<2||scopes[scopes.length-1]!==child)return;
 const parents=scopes.slice(0,-1).filter(parent=>parent.nestedElementalChain?.pending?.child===child);
 for(const parent of parents){
  const chain=parent.nestedElementalChain;
  const closed=parents.length===1?closeNestedElementalStep(chain.pending,chain.checkpoint.uid,when,evidence):null;
  if(!closed){
   noteNestedSettlementFailure(parent,'child_close','unverified_child_return',5,child.settlementDomainEntry?.targetType);
   parent.nestedElementalChain=null;continue;
  }
  chain.steps.push(closed);chain.pending=null;
 }
}
function verifiedParentBeforeNestedSettlement(scope,target,when,amount,type){
 try{
  const chain=scope.nestedElementalChain,checkpoint=chain?.checkpoint??scope.nestedTargetCheckpoint,hit=scope.targetHit;
  const childrenFirst=checkpoint?.ordering==='children_before_parent';
  const parentReal=fp(scope.mod.add(0x48));
  if(!checkpoint||!sameMitigationTime(checkpoint.when,when)||
     !hit?.commit?.returned||hit.commit.type!==type||!sameMitigationTime(hit.commit.returnWhen,when)||
     !target?.equals(hit.target)||!targetSettlementIdentity(scope,hit,when)||
     (childrenFirst?!Number.isFinite(parentReal)||parentReal>0:parentReal!==checkpoint.real)||
     fp(scope.mod.add(0x30))!==amount||
     !Number.isFinite(amount)||amount+0.01<-parentReal||
     scope.numericShieldUnknown||scope.unmatchedShield||scope.numericShield>0||
     shieldContextUnknown(when.frame,target)||shieldTotal(when.frame,target)!==0)return null;
  let parentBefore=checkpoint.hpBefore,parentAfter=checkpoint.hpAfter,zeroAfterChildren=false;
  const proof={target_uid:String(checkpoint.uid),parent_real_delta:parentReal,
   parent_hp_before:checkpoint.hpBefore,parent_hp_after:checkpoint.hpAfter};
  if(checkpoint.ordinary){
   const child=scope.nestedOrdinarySettlement;
   if(!child||child.child!==checkpoint.child||!child.child.settlementRecordEmitted||
      child.event!==child.child.settlementRecord||child.event.target_uid!==String(checkpoint.uid)||
      child.event.target_instance!==target.toString()||
      checkpoint.uid!==child.uid||child.hpBefore!==checkpoint.hpAfter||
      !sameMitigationTime(child.when,when))return null;
   const finalHp=targetHp(target);
   if(childrenFirst){
    if(scope.unclockedShield||child.child.unclockedShield||!matchesOpeningAutoChessScope(scope,when)||
       !sameNativeEventScope(child.event,when))return null;
    if(parentReal===0){
     if(!(amount>0)||child.hpAfter!==0||finalHp!==0)return null;
     zeroAfterChildren=true;
    }else if(finalHp===null||!(child.hpAfter>finalHp)||
             Math.abs(child.hpAfter-finalHp+parentReal)>Math.max(0.01,Math.abs(parentReal)*1e-6))return null;
    parentBefore=proof.parent_hp_before=child.hpAfter;
    parentAfter=proof.parent_hp_after=finalHp;
    proof.parent_entry_hp_before=checkpoint.hpBefore;
    proof.ordering=zeroAfterChildren?'child_before_parent_zero':'child_before_parent';
   }else if(finalHp!==child.hpAfter)return null;
   scope.nestedParentSettlementProof={...proof,child_hp_before:child.hpBefore,child_hp_after:child.hpAfter,
    child_real_delta:child.real};
  }else{
   if(!chain||chain.pending||!chain.steps.length||scope.nestedCrossThreadActivity||scope.nestedModifierConflict)return null;
   let hp=checkpoint.hpAfter,sequence=0;
   for(const step of chain.steps){
    if(step.hpBefore!==hp||step.settlement.sequence<=sequence||
       !matchesNestedElementalRecords(step,checkpoint.uid,target,when))return null;
    hp=step.hpAfter;sequence=step.settlement.sequence;
   }
   const finalHp=targetHp(target);
   if(childrenFirst){
    if(parentReal===0){
     // The same independent chain can exhaust the target before the parent
     // applies anything. Keep the observed nominal hit as full overkill.
     if(chain.steps.length<2||!(amount>0)||hp!==0||finalHp!==0)return null;
     zeroAfterChildren=true;proof.parent_entry_hp_before=checkpoint.hpBefore;
    }else if(finalHp===null||!(hp>finalHp)||
             Math.abs(hp-finalHp+parentReal)>Math.max(0.01,Math.abs(parentReal)*1e-6))return null;
    parentBefore=proof.parent_hp_before=hp;parentAfter=proof.parent_hp_after=finalHp;
   }else if(finalHp!==hp)return null;
   if(chain.steps.length===1&&!childrenFirst){
    const [step]=chain.steps;
    scope.nestedParentSettlementProof={...proof,child_hp_before:step.hpBefore,
     child_hp_after:step.hpAfter,child_real_delta:step.real};
   }else{
    scope.nestedElementalChainProof={version:2,ordering:childrenFirst?'children_before_parent':'parent_before_children',
     ...proof,children:chain.steps.map(step=>({
     source:step.cid,source_character_uid:step.uid,settlement_sequence:step.settlement.sequence,
     hp_before:step.hpBefore,hp_after:step.hpAfter,real_delta:step.real}))};
   }
  }
  return {target:hit.commit.targetKey,real_delta:parentReal,
   hp_before:parentBefore,hp_after:parentAfter,killed:parentAfter===0,
   target_type:0,damage_type:type,cancel_reason:0,shield_absorbed:0,
   ...(zeroAfterChildren?{settled_after_nested_kill:true}:{})};
 }catch(e){return null;}
}
function rememberNestedElementalKill(child,when,evidence){
 const uid=child.elementalTargetUid;
 if(!uid||child.mitigationModifierReused||!evidence?.killed||evidence.cancel_reason!==0||
    evidence.shield_absorbed!==0||!(evidence.real_delta<0)||evidence.hp_after!==0||
    damageTargetUid(child.target)!==uid||!child.target.equals(child.mod.add(0x10).readPointer())||
    !child.nativeModifierSource?.equals(child.mod.readPointer()))return;
 const scopes=activeModifiers.get(child.tid);
 if(!scopes||scopes[scopes.length-1]!==child)return;
 const parents=scopes.slice(0,-1).filter(scope=>{
  const hit=scope.targetHit;
  return hit?.uid===uid&&hit.target.equals(child.target)&&scope.target?.equals(child.target)&&
         hit.hpBefore>=evidence.hp_before&&sameMitigationTime(scope.when,when);
 });
 for(const parent of parents){
  parent.nestedElementalKill=parents.length===1&&parent.nestedElementalKill===undefined?
    {uid,when,child,hpBefore:evidence.hp_before,hpAfter:evidence.hp_after,real:evidence.real_delta,
     settlementRecord:child.settlementRecord,observedAfterChild:targetHp(child.target)}:null;
 }
}
function damageSettledAfterNestedKill(scope,when,amount){
 const kill=scope.nestedElementalKill,hit=scope.targetHit;
 if(!kill||kill.hpBefore!==hit?.hpBefore||scope.targetHitAmbiguous||scope.outputAmbiguous||scope.mitigationModifierReused||
    !scope.nativeModifierSource||!(amount>0)||!sameMitigationTime(kill.when,when))return null;
 const target=hit.target,mod=scope.mod,child=kill.child,settlement=kill.settlementRecord;
 if(!child?.settlementRecordEmitted||!settlement||settlement!==child.settlementRecord||
    settlement.kind!=='damage_settlement'||settlement.settlement_complete!==true||settlement.damage_type!==5||
    settlement.target_uid!==String(kill.uid)||settlement.target_instance!==target.toString()||
    settlement.shield_absorbed!==0||settlement.amount!==-kill.real||settlement.killed!==true||
    settlement.real_delta!==kill.real||settlement.hp_before!==kill.hpBefore||settlement.hp_after!==kill.hpAfter||
    settlement.cancel_reason!==0||!Number.isSafeInteger(settlement.sequence)||
    !sameMitigationTime(child.when,when)||settlement.frame!==when.frame||settlement.game_time!==when.game_time||
    !sameNativeEventScope(settlement,when)||
    kill.hpAfter!==0||kill.observedAfterChild!==kill.hpAfter)return null;
 const parentAfter=targetHp(target),parentReal=fp(mod.add(0x48));
 if(damageTargetUid(target)!==kill.uid||!target.equals(mod.add(0x10).readPointer())||
    !scope.nativeModifierSource.equals(mod.readPointer())||
    mod.add(0x70).readS32()!==0||parentReal!==0||parentAfter!==0||fp(mod.add(0x30))!==amount||
    shieldContextUnknown(when.frame,target)||shieldTotal(when.frame,target)!==0)return null;
 // The outer modifier still has a settlement amount, but only the child's
 // HP changed. Do not attach the parent's pre-nesting target HP to this hit.
 scope.nestedParentSettlementProof={target_uid:String(kill.uid),ordering:'child_before_parent_zero',
  parent_entry_hp_before:hit.hpBefore,parent_real_delta:parentReal,
  parent_hp_before:kill.observedAfterChild,parent_hp_after:parentAfter,
  child_hp_before:kill.hpBefore,child_hp_after:kill.hpAfter,child_real_delta:kill.real};
 return {real_delta:0,hp_before:0,hp_after:0,killed:true,cancel_reason:0,shield_absorbed:0,
         settled_after_nested_kill:true};
}
function dynamic(p){
 const n=captureRead('blackboard.count',()=>p.add(0x18).readS32());
 if(n<0||n>128)throw new Error('bad blackboard');
 return captureRead('blackboard.entries',()=>{
  const a=p.add(0x10).readPointer().add(0x20);
  for(let i=0;i<n;i++)if(str(a.add(i*24).readPointer())==='dynamic')return a.add(i*24+8).readFloat();
  throw new Error('missing shield value');
 });
}
function fixedArg(p){return Number(BigInt.asIntN(64,BigInt(p.toString())))/4294967296;}
function own(id){return id!==null&&(id.startsWith('char_')||id.startsWith('token_'));}
function record(source,kind,amount,when,proof,evidence,sourceUid){
 if(coalesceEnemyHp)flushCoalescedEnemyHp();
 if(source!==null&&sourceUid!==undefined)evidence={...evidence,source_character_uid:sourceUid};
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
 const event={sequence:undefined,frame:when.frame,game_time:when.game_time,source,kind,amount,proof};
 if(evidence)Object.assign(event,evidence);
 // A delayed/coalesced record retains the scope observed with its own clock.
 // Reading the current scope here would move an old tail into the next round.
 if(Object.prototype.hasOwnProperty.call(when,'native_autochess_scope'))
  event.native_autochess_scope=when.native_autochess_scope&&typeof when.native_autochess_scope==='object'
   ?{...when.native_autochess_scope}:when.native_autochess_scope;
 if(omitUnrelatedAutoChessEvent(event)){
  // Callers still receive their assembled proof, so nested settlement and
  // quality bookkeeping run normally. No sequence was allocated or queued.
  delete event.sequence;return event;
 }
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
 event.sequence=++sequence;
 queueSettlementScopes.set(event,when.settlementScope);
 queue.push(event);
 return event;
}
function omitUnrelatedAutoChessEvent(event){
 if(event.native_autochess_scope?.collection_mode!=='local')return false;
 if(typeof probeAdapter.eventCollectionOwner!=='function')return false;
 // Shared enemy/elemental settlements and target-dependent healing/shields
 // remain authoritative facts even when the source belongs to another player.
 const skill=event.kind==='skill_start'||event.kind==='skill_end';
 const legacyDamage=(event.kind==='damage'||event.kind==='hp_damage')&&
  [1,2,3].includes(event.damage_type)&&event.target_type===0&&
  typeof event.target==='string'&&/^(enemy_|trap_)/.test(event.target);
 if(!skill&&!legacyDamage)return false;
 try{return probeAdapter.eventCollectionOwner(event)==='foreign';}
 catch(e){return false;}
}
// Keep the first heal intact. It may establish the HP and MaxHP needed to
// prove subsequent healing. An exactly contiguous, increasing tail with the
// same verified MaxHP then contributes its last HP minus its first HP.
const enemyHpBirths=new Map();
const ENEMY_HP_RUN_LIMIT=128;
const enemyHpRuns=new Map();
let enemyHpWindow=null;
function flushCoalescedEnemyHp(){
 // Only unpublished tails are mutable. Every transport/authoritative boundary
 // drains this bounded window before exposing a sequence. Targets are independent;
 // within each target the first witness and all damage boundaries stay ordered.
 const runs=[...enemyHpRuns.values()];
 enemyHpRuns.clear();enemyHpWindow=null;
 for(const {tail} of runs)if(tail)enqueueEvent(null,'enemy_hp',0,tail.when,{method:tail.method},tail.evidence);
}
function resetEnemyHpCoalescing(){
 if(!coalesceEnemyHp)return;
 flushCoalescedEnemyHp();
 enemyHpBirths.clear();
}
// Baseline evidence describes only already observed, unacknowledged queue
// records. Platform adapters opt in through their envelope metadata; this
// helper performs no native reads, event replay, clock or cursor updates.
function discardedAutoChessSkills(fields,records,acknowledged,through){
 if(!fields||!Object.prototype.hasOwnProperty.call(fields,'native_autochess_context'))return {};
 const result={baseline_discarded_skill_events:[],baseline_discarded_skill_events_complete:true};
 if(!Array.isArray(records)||records.length>100000||!Number.isSafeInteger(acknowledged)||acknowledged<0||
    !Number.isSafeInteger(through)||through<acknowledged){result.baseline_discarded_skill_events_complete=false;return result;}
 // These are identities of already observed queue records, not native reads.
 function scopeKey(scope,frame,game_time){
  if(!scope||scope.protocol_version!==1||scope.status!=='available'||scope.verified_same_frame!==true||
     scope.frame!==frame||scope.game_time!==game_time||!Number.isSafeInteger(frame)||frame<0||
     !Number.isFinite(game_time)||game_time<0||![0,1,2,3,4,5,6,7,8,9,100,101,102,103,104].includes(scope.phase)||
     !Number.isSafeInteger(scope.round)||scope.round<0||typeof scope.scope_key!=='string')return null;
  const parts=scope.scope_key.split(':');
  if(parts.length!==7||parts[5]!=='autochess'||!/^(0|[1-9][0-9]{0,15})$/.test(parts[6])||
     !Number.isSafeInteger(Number(parts[6])))return null;
  for(const root of parts.slice(0,5)){
   if(!/^0x[0-9a-f]{1,16}$/.test(root))return null;
   const number=BigInt(root);if(number<0x10000n||number>0x7fffffffffffn||number%8n)return null;
   if('0x'+number.toString(16)!==root)return null;
  }
  // Epoch changes alone do not prove a foreign actor: the same roots and
  // phase/round may have discarded an earlier start during a clock reset.
  return JSON.stringify([parts.slice(0,5),scope.phase,scope.round]);
 }
 const current=fields.native_autochess_context;
 let currentKey=scopeKey(current,current?.frame,current?.game_time);
 if(currentKey!==null&&(fields.native_frame!==current.frame||typeof fields.native_fixed_time!=='string'||
    !/^[0-9]{1,20}$/.test(fields.native_fixed_time)||
    Number(BigInt(fields.native_fixed_time))/4294967296!==current.game_time))currentKey=null;
 const roots=fields.native_identity;
 if(currentKey!==null&&(!roots||['controller','native','logger','stats','level'].map(name=>roots[name]).join(':')+
    ':autochess:'!==current.scope_key.slice(0,current.scope_key.lastIndexOf(':')+1)))currentKey=null;
 const seen=new Set();let expected=acknowledged+1;
 for(const event of records){
  if(!event||!Number.isSafeInteger(event.sequence)){result.baseline_discarded_skill_events_complete=false;continue;}
  if(event.sequence<=acknowledged||event.sequence>through)continue;
  if(event.sequence!==expected)result.baseline_discarded_skill_events_complete=false;
  expected=Math.max(expected,event.sequence+1);
  if(event.kind!=='skill_start'||event.proof?.method==='BasicSkill.OnCastFinish')continue;
  const scope=event.native_autochess_scope,eventKey=scopeKey(scope,event.frame,event.game_time);
  if(currentKey!==null&&eventKey!==null&&currentKey!==eventKey)continue;
  const identity=scope&&typeof scope==='object'?
   [scope.protocol_version,scope.status,scope.verified_same_frame,scope.scope_key,scope.phase,scope.round]:null;
  const key=JSON.stringify([event.source,event.source_character_uid,identity]);
  if(seen.has(key))continue;
  if(result.baseline_discarded_skill_events.length>=512){result.baseline_discarded_skill_events_complete=false;break;}
  seen.add(key);
  const evidence={};
  for(const field of ['sequence','frame','game_time','source','source_character_uid','proof','native_autochess_scope']){
   if(Object.prototype.hasOwnProperty.call(event,field)&&event[field]!==undefined)
    evidence[field]=event[field]&&typeof event[field]==='object'?JSON.parse(JSON.stringify(event[field])):event[field];
  }
  result.baseline_discarded_skill_events.push(evidence);
 }
 if(expected<=through)result.baseline_discarded_skill_events_complete=false;
 return result;
}
function sameAutoChessRoundStartBinding(point,fields){
 if(!point||!fields||point.native_generation!==fields.native_generation||
    point.native_process_pid!==fields.native_process_pid||point.native_stage_id!==fields.native_stage_id||
    !['controller','native','logger','stats','level'].every(key=>point.native_identity[key]===fields.native_identity?.[key]))return false;
 return sameNativeEventScope({native_autochess_scope:point.native_autochess_context},
                            {native_autochess_scope:fields.native_autochess_context});
}
function autoChessRoundStartEnvelope(fields,records,acknowledged,through){
 const point=autoChessRoundStart;
 if(!sameAutoChessRoundStartBinding(point,fields))return {};
 // An already released origin prefix cannot be rebuilt after a host rebind.
 // Keep the historical source point internally, without offering a bootstrap.
 if(acknowledged>point.sequence)return {autochess_round_start_unavailable:'prefix_already_acknowledged'};
 const result={autochess_round_start:point,autochess_round_start_events:[],autochess_round_start_events_complete:false};
 const count=through-point.sequence,offset=point.sequence-acknowledged;
 // Validate the retained prefix before offering it, including its paged form.
 // Only a single transport-sized prefix needs a copy for the baseline reply.
 if(count<0||count>EVENT_BUFFER_LIMIT||offset+count>records.length)return result;
 const start={native_autochess_scope:point.native_autochess_context};
 const current=fields.native_autochess_context;
 let previousFrame=0,previousTime=0;
 for(let index=0;index<count;index++){
  const event=records[offset+index],scope=event?.native_autochess_scope;
  if(event?.sequence!==point.sequence+index+1)return result;
  if(!sameNativeEventScope(start,event)||scope.frame!==event.frame||scope.game_time!==event.game_time||
     !Number.isSafeInteger(event.frame)||event.frame<previousFrame||event.frame>current.frame||
     !Number.isFinite(event.game_time)||event.game_time<previousTime||event.game_time>current.game_time||
     event.frame===previousFrame&&event.game_time!==previousTime||
     event.frame===current.frame&&event.game_time!==current.game_time)
   return {autochess_round_start_unavailable:'invalid_prefix_scope_or_clock'};
  previousFrame=event.frame;previousTime=event.game_time;
 }
 if(count>AUTOCHESS_ROUND_START_EVENT_LIMIT)return result;
 result.autochess_round_start_events=records.slice(offset,offset+count);
 result.autochess_round_start_events_complete=true;
 return result;
}
function sameNativeEventScope(left,right){
 const a=left.native_autochess_scope,b=right.native_autochess_scope;
 if(a===undefined&&b===undefined)return true;
 return !!a&&!!b&&a.protocol_version===1&&b.protocol_version===1
  &&a.status==='available'&&b.status==='available'
  &&a.verified_same_frame===true&&b.verified_same_frame===true
  &&typeof a.scope_key==='string'&&a.scope_key.length>0&&a.scope_key===b.scope_key
  &&a.phase===b.phase&&a.round===b.round&&a.collection_mode===b.collection_mode;
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
   // Preserve the first +1 as an explicit continuity witness in the compact record.
   const unitTick=(eid==='enemy_1061_zomshd'||eid==='enemy_1061_zomshd_2')&&hp-hpBefore===1;
   const window=enemyHpWindow;
   if(window&&(window.thread!==write.thread||window.when.frame!==when.frame||
      window.when.game_time!==when.game_time||window.when.settlementScope!==when.settlementScope||
      !sameNativeEventScope(window.when,when)))flushCoalescedEnemyHp();
   const run=enemyHpRuns.get(key);
   if(run&&run.unitTick===unitTick&&run.key===key&&run.uid===uid&&run.eid===eid&&run.maxHp===maxHp
      &&run.when.frame===when.frame&&run.when.game_time===when.game_time
      &&run.when.settlementScope===when.settlementScope
      &&sameNativeEventScope(run.when,when)&&run.after===hpBefore){
    if(unitTick){
     run.tail.evidence.enemy_regen_ticks=(run.tail.evidence.enemy_regen_ticks??1)+1;
    }
    if(run.tail)run.tail.evidence.hp_after=hp;
    else run.tail={when,method,evidence};
    run.after=hp;
    return;
   }
   // A discontinuity for one target is a boundary for the entire window.
   if(run||enemyHpRuns.size>=ENEMY_HP_RUN_LIMIT)flushCoalescedEnemyHp();
   const witnessed=birth.healWitness;
   const first=!witnessed||witnessed.maxHp!==maxHp||
    witnessed.when.settlementScope!==when.settlementScope||!sameNativeEventScope(witnessed.when,when);
   enemyHpWindow={thread:write.thread,when};
   if(unitTick||!first){
    // Stage even the first tick so an already published sequence is immutable.
    enemyHpRuns.set(key,{key,uid,eid,when,maxHp,after:hp,unitTick,tail:{when,method,evidence}});
   }else if(enqueueEvent(null,'enemy_hp',0,when,{method},evidence)){
    enemyHpRuns.set(key,{key,uid,eid,when,maxHp,after:hp,unitTick,tail:null});
    birth.healWitness={when,maxHp};
   }
   return;
  }
 }
 record(null,'enemy_hp',0,when,{method},evidence);
}
function recordHpContext(context,flush=true){
 if(stopped||fatal)return;
 if(hpContext.length>=HP_CONTEXT_BUFFER_LIMIT){
  hpContextDropped++;
  return;
 }
 if(flush&&coalesceEnemyHp)flushCoalescedEnemyHp();
 // Diagnostics do not advance the authoritative sequence. Attach late
 // diagnostics to the next event so repeating a read returns the same slice.
 const published=typeof __arkdpsPublicationJournal==='object'&&Number.isSafeInteger(__arkdpsPublicationJournal.publishedThrough)?__arkdpsPublicationJournal.publishedThrough:0;
 hpContext.push({eventSequence:Math.max(sequence,readThroughSequence+1,published+1),context});
}
function readEvents(after,through){
 const valid=Number.isSafeInteger(after)&&Number.isSafeInteger(through)&&
  acknowledgedSequence<=after&&after<=through&&through<=sequence;
 const error=fatal||(valid?null:'event read range is unavailable');
 const prepared=error||stopped?null:probeAdapter.prepareEventEnvelope?.();
 const envelope=probeAdapter.readEnvelope(prepared);
 const result={events:[],hp_context:[],hp_context_dropped:hpContextDropped,error,stopped,
  ...(probeAdapter.terminalReadVersion===1?{terminal_read_version:1}:{}),
  ...eventQuality(),terminal_boundary_diagnostics:{...terminalDiagnostics},...envelope.fields};
 if(traceNestedParent){
  result.nested_parent_trace_count=nestedParentTraceCount;
  result.nested_parent_trace_dropped=nestedParentTraceDropped;
 }
 if(!envelope.ready)return result;
 if(error||stopped)return result;
 result.events=queue.slice(after-acknowledgedSequence,through-acknowledgedSequence);
 if(after===through&&typeof probeAdapter.baselineEnvelope==='function'){
  try{
   Object.assign(result,probeAdapter.baselineEnvelope(envelope.fields,queue,acknowledgedSequence,through),
                 autoChessRoundStartEnvelope(envelope.fields,queue,acknowledgedSequence,through));
  }catch(e){
   result.baseline_discarded_skill_events=[];
   result.baseline_discarded_skill_events_complete=false;
   result.baseline_discarded_skill_events_error=String(e);
  }
 }
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
function readTerminalEvents(after,through,advanceReadWatermark=true){
 // The ordinary read requires a live native binding. Only this explicit API
 // may drain the retained queue after a real OnDestroy has removed it.
 const fence={sequence,epoch,active,active_hooks:[...activeHooks.keys()]};
 const result={events:[],hp_context:[],hp_context_dropped:hpContextDropped,
  error:fatal,stopped,...eventQuality(),...fence,after,through,terminal_only:true,
  terminal_read_version:probeAdapter.terminalReadVersion??null,
  terminal_boundary_diagnostics:{...terminalDiagnostics},retry:true};
 const reject=reason=>({...result,terminal_read_rejection:reason});
 if(probeAdapter.terminalReadVersion!==1)return reject('unsupported');
 const boundary=currentTerminalBoundary();
 if(!boundary)return reject('terminal_boundary_unavailable');
 if(!probeHooksReady||fatal||stopped||active||activeHooks.size)return reject('producer_not_quiet');
 if(!Number.isSafeInteger(after)||!Number.isSafeInteger(through)||acknowledgedSequence>after
    ||after<0||after>through||through!==boundary.sequence||through!==sequence)
  return reject('terminal_read_range_unavailable');
 const invalidate=reason=>{if(terminalBoundary===boundary)terminalBoundary=null;
  countTerminalDiagnostic('invalidated');terminalDiagnostics.last_rejection=reason;return reject(reason);};
 try{if(!probeAdapter.validateTerminalBinding(boundary))return invalidate('terminal_binding_changed');}
 catch(e){return invalidate('terminal_binding_unreadable');}
 // No synthesized prefix or old acknowledged record can fill a lost tail.
 const records=queue.slice(after-acknowledgedSequence,through-acknowledgedSequence);
 if(records.length!==through-after||records.some((record,index)=>record.sequence!==after+index+1
    ||queueSettlementScopes.get(record)!==boundary.damage_settlement_scope
    ||!Number.isSafeInteger(record.frame)||record.frame>boundary.frame
    ||!Number.isFinite(record.game_time)||record.game_time>boundary.game_time))
  return reject('terminal_queue_incomplete');
 if(currentTerminalBoundary(boundary)!==boundary||sequence!==fence.sequence||epoch!==fence.epoch
    ||active||activeHooks.size)return reject('terminal_fence_changed');
 Object.assign(result,{events:records,hp_context:hpContext.filter(item=>after<item.eventSequence&&item.eventSequence<=through)
  .map(item=>item.context),retry:false,frame:boundary.frame,game_time:boundary.game_time,
  terminal_boundary:boundary});
 for(const key of Object.keys(boundary))if(key.startsWith('native_'))result[key]=boundary[key];
 if(advanceReadWatermark)readThroughSequence=Math.max(readThroughSequence,through);
 return result;
}
function refreshEnemyEntryFrame(frame) {
 if(enemyEntryFrame!==null&&frame<enemyEntryFrame){
  resetEnemyHpCoalescing();
  awaitingEnemyTile.clear();enemyBirthProofs.clear();
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
function isEnemy(object,klass=object.readPointer()) {
 if(klass.equals(enemyEntryConfig.enemy))return true;
 const key=klass.toString();
 if(!enemyClassCache.has(key))enemyClassCache.set(key,enemyEntryConfig.assignable(enemyEntryConfig.enemy,klass));
 return enemyClassCache.get(key);
}
function stageTrapId(object,id=targetKey(object)) {
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
function enemyDamageIdentity(target,confirmedUid) {
 const key=targetKey(target);
 if(!key)throw new Error('missing settled target');
 // Some targetable stage objects enter the HP pool, others do not. Keep
 // the UID for both and let the host match the exact admitted instance.
 if(!key.startsWith('enemy_')&&!key.startsWith('trap_'))return null;
 if(!enemyEntryConfig.assignable(enemyEntryConfig.bObject,target.readPointer()))
  throw new Error('settled target is not a battle object');
 const uid=confirmedUid??target.add(0x18).readU32();
 if(!uid)throw new Error('missing settled target UID');
 return {target:key,target_uid:String(uid),target_instance:target.toString()};
}
function knownNonEnemyTarget(target){
 const key=targetKey(target);
 return key&&!key.startsWith('enemy_')&&!key.startsWith('trap_');
}
function damageSettlement(scope,target,real,when,{nominal,shield,complete=false,reduction,identity,type,nested=false,unverified=false,killed,evidenceMethod,hpEvidence,nativeModifierAmount,targetCheckpoint}={}) {
 if(!enemyDamageReady||scope.source&&!own(scope.source))return;
 const qualification=settlementScopeQualification(scope,when,target);
 if(qualification==='outside')return;
 if(qualification==='unknown'){noteSettlementProofGap(scope,when,'unverified enemy settlement domain');return;}
 try{
  const damageType=type??scope.mod.add(0x68).readS32();
  if(![1,2,3,5].includes(damageType))return;
  if(scope.sourceReadFailed)throw new Error('missing settled source identity');
  if(!scope.source&&!(damageType===5&&scope.nativeSourceNull))return;
  if(!Number.isFinite(real)||real>0)throw new Error('invalid settled HP delta');
  if(real===0&&!(shield>0)&&!nested&&!unverified)return;
  const actualTarget=identity??enemyDamageIdentity(target);
  if(!actualTarget)return;
  const evidence={...actualTarget,damage_type:damageType,shield_absorbed:shield??null,
                  settlement_complete:complete};
  if(!complete)captureSettlementFailureSample(scope,when,
   scope.settlementFailureReason??'incomplete HP or shield settlement proof',evidence);
  if(Number.isFinite(nominal)&&nominal+0.01>=-real+(shield??0))
   evidence.nominal_amount=nominal;
  if(nested)evidence.settled_after_nested_kill=true;
  if(typeof killed==='boolean')evidence.killed=killed;
  // Only the same-hit checked HP closure may enter this certificate. These
  // endpoints replace the legacy burst record as the nested chain witness.
  if(hpEvidence){
   if(hpEvidence.real_delta!==real||hpEvidence.cancel_reason!==0||
      !Number.isFinite(hpEvidence.hp_before)||!Number.isFinite(hpEvidence.hp_after)||
      hpEvidence.hp_before<0||hpEvidence.hp_after<0||
      Math.abs(hpEvidence.hp_before-hpEvidence.hp_after+real)>Math.max(0.01,Math.abs(real)*1e-6))
    throw new Error('invalid checked settlement HP checkpoint');
   Object.assign(evidence,{hp_before:hpEvidence.hp_before,hp_after:hpEvidence.hp_after,
    real_delta:real,cancel_reason:0,target_type:0});
  }
  const proof={method:scope.name,settlement_wire_version:2};if(evidenceMethod)proof.evidence_method=evidenceMethod;
  if(hpEvidence)Object.assign(proof,{type:damageType,target:0});
  if(Number.isFinite(nativeModifierAmount)&&nativeModifierAmount>=0)
   proof.native_modifier_observation={version:1,method:'Entity.ApplyModifier',amount:nativeModifierAmount};
  if(targetCheckpoint)proof.target_callback_checkpoint=targetCheckpoint;
  const tokenHost=autoTokenHostEvidence(scope,when);
  if(tokenHost)proof.native_autochess_token_host=tokenHost;
  const characterForm=autoCharacterFormEvidence(scope,when);
  if(characterForm)proof.native_autochess_character_form=characterForm;
  if(scope.nestedParentSettlementProof)proof.nested_settlement=scope.nestedParentSettlementProof;
  if(scope.nestedElementalChainProof)proof.nested_elemental_chain=scope.nestedElementalChainProof;
  if(!complete&&scope.nestedSettlementFailure)proof.nested_settlement_detail=scope.nestedSettlementFailure;
  if(!complete&&scope.settlementFailureReason)proof.settlement_unknown_reason=scope.settlementFailureReason;
  const instance=verifiedSourceInstance(scope);if(instance)evidence.source_instance_uid=instance;
  const event=record(scope.source,'damage_settlement',real===0?0:-real,when,proof,
         {...evidence,...reduction},scope.sourceCharacterUid);
  if(event){scope.settlementRecordEmitted=true;scope.settlementRecord=event;}
  return event;
 }catch(e){
  const owner=scope.tracksModifier?scope:scope.modifierScope;
  if(owner)noteSettlementProofGap(owner,when,String(e));
  else markDamageSettlementUnknown(when,String(e));
 }
}
function flushOutputSettlement(scope,when) {
 const output=scope.outputSettlement;
 if(output){
  const event=damageSettlement(output,output.target,output.real,when,{identity:output.settlementIdentity,
                   type:output.settlementType});
  if(event)scope.settlementRecordEmitted=true;
 }
 scope.outputSettlement=null;
}
function matchOutputSettlement(output,scope,source) {
 if(!enemyDamageReady)return false;
 const qualification=settlementScopeQualification(output,output.when);
 if(qualification==='outside')return false;
 if(qualification==='unknown'){noteSettlementProofGap(scope||output,output.when,'unverified output settlement domain');return false;}
 if(!source||!scope||scope.mitigationModifierReused||!scope.nativeModifierSource?.equals(source)||
    !scope.target?.equals(output.target)||!sameMitigationTime(scope.when,output.when))return false;
 const uid=damageTargetUid(output.target);
 if(!uid||uid!==scope.settlementTargetUid)return false;
 try{
  output.settlementType=output.mod.add(0x68).readS32();
  output.settlementIdentity=enemyDamageIdentity(output.target,uid);
 }
 catch(e){noteSettlementProofGap(scope||output,output.when,String(e));return false;}
 if(!output.settlementIdentity)return false;
 if(scope.settlementAmbiguous)return true;
 const previous=scope.outputSettlement;
 if(previous){
  // A single ApplyModifier is the deduplication boundary. More than one
  // incompatible output without distinct target-side commits is ambiguous.
  if(scope.targetHitAmbiguous||output.real!==previous.real||output.amount!==previous.amount||
     output.settlementType!==previous.settlementType||
     output.settlementIdentity.target!==previous.settlementIdentity.target||
     output.sourceCharacterUid!==previous.sourceCharacterUid){
   scope.outputSettlement=null;
   scope.settlementAmbiguous=true;
   if(output.settlementType!==previous.settlementType||
      output.settlementIdentity.target!==previous.settlementIdentity.target||
      output.sourceCharacterUid!==previous.sourceCharacterUid)
    markDamageSettlementUnknown(output.when,'ambiguous output identity for one modifier');
   else noteSettlementProofGap(scope,output.when,'ambiguous output for one modifier');
  }
 }else scope.outputSettlement=output;
 return true;
}
function verifiedNoConsumption(scope,output,evidence,killed,real) {
 return !!output&&real===0&&killed===false&&
        evidence.hp_before===evidence.hp_after&&evidence.shield_absorbed===0&&
        !scope.numericShieldUnknown&&!scope.unmatchedShield&&!(scope.numericShield>0);
}
function settleDamage(scope,target,when,amount,type,reduction,exactEvidence=null) {
 if(!enemyDamageReady)return false;
 const qualification=settlementScopeQualification(scope,when,target);
 if(qualification==='outside')return false;
 if(qualification==='unknown'){noteSettlementProofGap(scope,when,'unverified enemy settlement domain');return false;}
 if(scope.settlementAnchor&&!stableSettlementAnchor(scope,when,false)){
  scope.targetCommitRejected=true;noteSettlementProofGap(scope,when,'modifier scope identity or clock changed');return false;
 }
 if(!enemyDamageReady||!scope.tracksModifier||!target||target.isNull()||
    !scope.nativeModifierSource||!scope.nativeModifierSource.equals(scope.mod.readPointer())||
    !scope.target?.equals(target)||!scope.settlementTargetUid||
    damageTargetUid(target)!==scope.settlementTargetUid)return false;
 if(scope.mitigationModifierReused){
  noteSettlementProofGap(scope,when,'reused damage modifier');
  return false;
 }
 const output=scope.outputSettlement;
 const targetCommit=!!exactEvidence&&scope.targetSettlementEvidence===exactEvidence;
 const hpWriterCommit=targetCommit&&!!scope.hpWriterSettlementMethod;
 // Signed result from the exact output callback or a checked elemental
 // ApplyModifier. Do not promote the nominal value of an unmatched callback.
 const real=scope.settlementAmbiguous||
  (scope.targetCommitRejected||scope.targetHit?.commit?.invalid)&&!exactEvidence?.settled_after_nested_kill?null:
  targetCommit?exactEvidence.real_delta:output?.real??exactEvidence?.real_delta;
 const shield=scope.numericShield;
 const shieldIncoming=scope.firstShieldIncoming;
 // The shield hook witnessed incoming damage before the engine zeroed
 // m_value on a full block. Keep that measured value within this hit.
 const shieldedZero=shield>0&&amount===0&&Number.isFinite(shieldIncoming)&&
    (real===0||real==null&&fp(scope.mod.add(0x48))===0&&
     Math.abs(shield-shieldIncoming)<=Math.max(0.01,shieldIncoming*1e-6));
 const settledAmount=shieldedZero?shieldIncoming:amount;
 const nativeAmount=shieldedZero?shieldIncoming:targetCommit?amount:output?.amount??amount;
 const tolerance=Math.max(0.01,Math.abs(settledAmount)*1e-6);
 if(output&&output.settlementType!==type){
  flushOutputSettlement(scope,when);
  return false;
 }
 const nested=exactEvidence?.settled_after_nested_kill===true;
 const cancel=scope.mod.add(0x70).readS32();
 const exact=shieldedZero||Number.isFinite(real)&&real<=0&&Number.isFinite(settledAmount)&&
             Math.abs(nativeAmount-settledAmount)<=tolerance;
 // HP may be capped after the formula even while the target is alive. Use
 // measured HP endpoints and the numeric shield callbacks for this hit.
 const hit=scope.targetHit;
 const nestedParent=scope.nestedParentSettlementProof,elementalChain=scope.nestedElementalChainProof;
 // A certified child-first hit starts at the child's closing HP. The target
 // callback checkpoint still carries the earlier HP at parent entry.
 let checkedHpBefore=hit?.hpBefore;
 if(targetCommit&&elementalChain?.ordering==='children_before_parent')checkedHpBefore=elementalChain.parent_hp_before;
 if(targetCommit&&(nestedParent?.ordering==='child_before_parent'||nestedParent?.ordering==='child_before_parent_zero'))
  checkedHpBefore=nestedParent.parent_hp_before;
 const capturedHp=exact&&Number.isFinite(real)&&real<=0&&cancel===0&&exactEvidence?.real_delta===real&&
    Number.isFinite(exactEvidence.hp_before)&&Number.isFinite(exactEvidence.hp_after)&&
    Math.abs(exactEvidence.hp_before-exactEvidence.hp_after+real)<=Math.max(0.01,Math.abs(real)*1e-6)&&
    (type===5&&exactEvidence.hp_before===scope.hpBefore||
     hpWriterCommit&&exactEvidence.hp_before===scope.hpBefore||
     (targetCommit||output)&&hit?.target.equals(target)&&hit.uid===scope.settlementTargetUid&&
     !scope.targetHitAmbiguous&&!scope.outputAmbiguous&&exactEvidence.hp_before===checkedHpBefore);
 let killed;
 if(capturedHp&&exactEvidence.killed===true&&exactEvidence.hp_after===0&&
    (real<0||nested&&targetCommit))killed=true;
 if(capturedHp&&exactEvidence.killed===false&&exactEvidence.hp_after>0)killed=false;
 // A matched output can leave HP and shield unchanged during a target
 // transition. Keep the ordinary damage event without adding a zero hit.
 if(!nested&&verifiedNoConsumption(scope,output,exactEvidence,killed,real)){
  scope.outputSettlement=null;
  scope.settlementNoConsumption=true;
  return true;
 }
 const absorbed=shield>0?shield:!scope.numericShieldUnknown&&
      (nested||real<0&&(capturedHp||exact&&Math.abs(settledAmount+real)<=tolerance))?0:null;
 const unverified=scope.settlementAmbiguous||scope.unmatchedShield&&!(settledAmount>0)&&!(shield>0)||
    amount>0&&cancel===0&&!nested&&(!Number.isFinite(real)||(real===0&&!(absorbed>0)));
 if(!exact&&!(absorbed>0)&&!unverified)return false;
 const settledReal=Number.isFinite(real)&&real<=0?real:0;
 const consistent=exact&&settledAmount+0.01>=-settledReal+(absorbed??0);
 const nominal=(consistent&&(cancel===0||absorbed>0)&&settledAmount>0?settledAmount:undefined);
 // Existing formula evidence was matched inside this same modifier scope.
 const pair=consistent&&shieldedZero?shieldMitigationEvidence(scope,type,shieldIncoming):
       consistent&&(real<0||absorbed>0||nested)?reduction:undefined;
 scope.outputSettlement=null;
 scope.settlementFailureCheckpoint={real,nativeAmount,absorbed,cancel,nested,
  hpBefore:exactEvidence?.hp_before??hit?.hpBefore,hpAfter:exactEvidence?.hp_after};
 if(unverified)noteSettlementProofGap(scope,when,scope.settlementFailureReason||'missing same-hit target or output settlement proof');
 const recipient=targetCommit?scope:output||scope;
 recipient.settlementFailureCheckpoint=scope.settlementFailureCheckpoint;
 if(scope.nestedParentSettlementProof)recipient.nestedParentSettlementProof=scope.nestedParentSettlementProof;
 if(scope.settlementFailureReason)recipient.settlementFailureReason=scope.settlementFailureReason;
 if(scope.nestedSettlementFailure)recipient.nestedSettlementFailure=scope.nestedSettlementFailure;
 const event=damageSettlement(recipient,target,settledReal,when,{nominal,shield:absorbed,type,nested,unverified,killed,
                   identity:output?.settlementIdentity,reduction:pair,hpEvidence:capturedHp||nested&&scope.nestedParentSettlementProof?exactEvidence:undefined,nativeModifierAmount:amount,
                   targetCheckpoint:(targetCommit||nested&&scope.nestedParentSettlementProof)&&hit?.commit?.returned?
                    {version:1,method:'Unit.OnTakeDamage',returned:true,source:scope.source,source_character_uid:scope.sourceCharacterUid,
                     target_uid:String(hit.uid),hp_before:hit.hpBefore,frame:hit.commit.when.frame,game_time:hit.commit.when.game_time}:undefined,
                   evidenceMethod:hpWriterCommit?scope.hpWriterSettlementMethod+'+Entity.ApplyModifier':targetCommit?'Unit.OnTakeDamage+Entity.ApplyModifier':undefined,
                   complete:consistent&&absorbed!==null&&!scope.numericShieldUnknown&&!unverified});
 if(event){scope.settlementRecordEmitted=true;scope.settlementRecord=event;}
 if(event&&targetCommit&&!hpWriterCommit&&capturedHp&&[1,2,3].includes(type))
  rememberNestedOrdinarySettlement(scope,when,exactEvidence,event);
 return true;
}
function settleSourcelessBurst(scope,evidence,when,amount,reduction) {
 if(!enemyDamageReady||scope.source||scope.burstTarget===null)return;
 try{
  if(settleDamage(scope,scope.target,when,amount,5,reduction,evidence))return;
  const real=evidence?.real_delta??fp(scope.mod.add(0x48));
  if(real===0&&!scope.numericShield)return;
  const target=scope.burstTarget;
  if(!scope.nativeSourceNull||!evidence||!target||!Number.isFinite(real)||real>0
     ||target.target!==evidence.target||target.target_uid!==String(scope.target.add(0x18).readU32()))
   throw new Error('unverified sourceless burst settlement');
  // The host must attribute the HP deduction before the burst consumes its
  // elemental bar owner. Both records stay within this callback's barrier.
  damageSettlement(scope,scope.target,real,when,{identity:target});
 }catch(e){markDamageSettlementUnknown(when,String(e));}
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
function sameMitigationTime(a,b){
 return a.frame===b.frame&&a.game_time===b.game_time&&a.settlementScope===b.settlementScope;
}
function mitigationTargetId(target){
 const id=targetKey(target);
 return id&&!id.startsWith('char_')&&!id.startsWith('token_')?id:null;
}
function noteUnpairedSettlement(scope,when,type,amount,evidence){
 // Diagnostics describe this measured native modifier, not whether a second
 // legacy wire record happened to be emitted. Formula/amount provenance stays
 // unchanged when modern consumers no longer need that record.
 if(stopped||fatal||!scope.source||!(amount>0)||evidence?.mitigation_before!==undefined)return;
 try{
  const target=mitigationTargetId(scope.target);
  if(!target||scope.target.equals(scope.mod.readPointer()))return;
  mitigationCounts.unpaired_damage++;
  let callerRva=null;
  try{
   const caller=scope.returnAddress;
   if(caller&&Process.findModuleByAddress(caller)?.name===il.name)callerRva=caller.sub(il.base).toString();
  }catch(e){/* The sample remains useful without a caller address. */}
  const prior=unpairedDamageSamples.findIndex(sample=>sample.source===scope.source&&sample.target===target&&
   sample.damage_type===type&&sample.caller_rva===callerRva);
  if(prior!==-1)unpairedDamageSamples.splice(prior,1);
  else if(unpairedDamageSamples.length>=UNPAIRED_DAMAGE_SAMPLE_LIMIT)unpairedDamageSamples.shift();
  unpairedDamageSamples.push({source:scope.source,target,damage_type:type,frame:when.frame,
   amount,caller_rva:callerRva});
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
function mitigationEvidence(scope,type,amount,verifiedShield=false){
 const result=scope.mitigation;
 if(!result)return null;
 if(scope.mitigationModifierReused||result.type!==type||scope.mod.add(0x60).readS32()!==0||
    !Number.isFinite(amount)||amount<0||
    !result.source.equals(scope.mod.readPointer())||!result.target.equals(scope.mod.add(0x10).readPointer())||
    (!verifiedShield&&scope.mod.add(0x70).readS32()!==0)){
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
function shieldMitigationEvidence(scope,type,incoming){
 // Take only the pending formula matched to this same modifier. Its
 // pre-shield result must equal the numeric shield hook's incoming value.
 if(scope.mitigation?.after!==incoming)return null;
 return mitigationEvidence(scope,type,incoming,true);
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
 const scope={tid:invocation.tid,source,sourceId,target,targetUid,attack,when:time(false,false),outer:!nested};
 invocation.formula=scope;
 formulaScopes.set(invocation.tid,scope);
 mitigationCounts.formula_scopes++;
}
function finishDamageFormula(result){
 const scope=this.formula;
 if(!scope||!sameMitigationTime(scope.when,time(false,false)))return;
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
function enterDamageFactory(nativeArgs,index){
 if(activeDamageFactories.has(this.tid))return; // seven-argument wrapper can delegate to eight
 activeDamageFactories.add(this.tid);
 this.enteredFactory=true;
 const batch=pendingFormulaDamage.get(this.tid);
 if(!batch||(batch.unowned.size===0&&batch.postFormulaHits.size===0))return;
 const when=time(false,false);
 if(!sameMitigationTime(batch,when))return;
 const type=probeAdapter.factoryArgument(nativeArgs,index).toInt32(),target=probeAdapter.factoryArgument(nativeArgs,index+3);
 if(!mitigationDamageType(type)||target.isNull()||!mitigationTargetId(target))return;
 const targetUid=target.add(0x18).readU32();
 if(!targetUid)return;
 const raw=fixedBits(probeAdapter.factoryArgument(nativeArgs,0)),originValue=index===2?fixedBits(probeAdapter.factoryArgument(nativeArgs,1)):raw;
 const beforeRaw=fixedBits(probeAdapter.factoryArgument(nativeArgs,index+5));
 const source=probeAdapter.factoryArgument(nativeArgs,index+2),unowned=batch.unowned;
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
 const sourceUid=source.isNull()?0:source.add(0x18).readU32(),storage=probeAdapter.factoryStorage(nativeArgs,this);
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
 if(!factory||!sameMitigationTime(factory.when,time(false,false))||
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
  attached.push(registerProbeHook(address,{
   arkdpsName:name,
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
   // The verified AttackInfo is indirect in both supported 64-bit ABIs; each passes
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
    if(!batch?.postFormulaAttacks.size)return;
    const key=args[0].toString(),candidate=batch.postFormulaAttacks.get(key);
    if(candidate===undefined||!sameMitigationTime(batch,time(false,false)))return;
    // Same-frame claims consume even ambiguous candidates or a mismatched type.
    batch.postFormulaAttacks.delete(key);
    if(candidate&&candidate.type===args[0].add(mitigationMethods.damageType).readS32()){
     this.postFormula=candidate;
     this.postFormulaBatch=batch;
    }
   }
  },function(result){
   if(this.postFormula){
    const candidate=this.postFormula,batch=this.postFormulaBatch;
    const beforeRaw=fixedBits(result);
    if(beforeRaw<=0n||!sameMitigationTime(batch,time(false,false)))return;
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
function decodeEnemyMaxHp(view){
 if(view.byteLength!==0x14)throw new Error('native block read unavailable');
 if(view.getUint8(0x10)!==1)return null;
 const value=Number(BigInt.asIntN(64,view.getBigUint64(0,true)^view.getBigUint64(8,true)))/4294967296;
 return value>0&&value<1e10?value:null;
}
function enemyMaxHp(enemy) {
 if(!enemyAttributesReady)return null;
 try{
  const attributes=enemy.add(0xb0).readPointer();
  if(attributes.isNull())return null;
  const cached=attributes.add(0x68).readPointer();
  if(cached.isNull())return null;
  const count=cached.add(0x18).readU32();
  if(!count||count>64)return null;
  // Only after the live length admits slot zero, copy its obscuring key,
  // current value and ready flag. No live field survives this invocation.
  const view=checkedNativeView(cached.add(0x20),0x14);
  return decodeEnemyMaxHp(view);
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
 const birth=enemyBirthProofs.get(uid),proof={method};
 try{if(birth&&birth.target_instance===enemy.toString()&&birth.target===entityId(enemy)&&
    birth.klass===enemy.readPointer().toString()&&sameNativeEventScope(birth,when)){
  const {klass,...certificate}=birth;proof.native_enemy_birth=certificate;
 }}catch(e){/* Optional birth evidence cannot reject the existing admission. */}
 awaitingEnemyTile.delete(uid);enemyBirthProofs.delete(uid);
 record(null,'enemy_enter',0,when,proof,evidence);
}
function installEnemyEntryHooks() {
 if(!enemyEntryConfig)return;
 const installed=[];
 try{
  for(const [method,address] of [['_UpdatePlayerSideAndMapLayer',enemyEntryConfig.layer],['Born',enemyEntryConfig.born]]){
   const label='enemy entry '+method;
   const isBorn=method==='Born';
   installed.push(registerProbeHook(address,{
    arkdpsName:label,
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
      if(isBorn){
       enemyBirthProofs.delete(uid);
       try{this.birth={version:1,source_method:'BObject.Born',target:entityId(enemy),target_uid:String(uid),
        target_instance:enemy.toString(),klass:enemy.readPointer().toString(),frame:when.frame,
        game_time:when.game_time,fixed:captureClockFixed().toString(),sequence_before_born:sequence,
        ...(when.native_autochess_scope!==undefined?{native_autochess_scope:{...when.native_autochess_scope}}:{})};
       }catch(e){/* Keep the ordinary guarded enemy entry without this option. */}
      }
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
       awaitingEnemyTile.delete(uid);enemyBirthProofs.delete(uid);
       return;
      }
      if(!enemy.add(0x25).readU8())return;
      if(isBorn&&this.birth)try{
       if(uid>0&&this.birth.target===entityId(enemy)&&this.birth.klass===enemy.readPointer().toString()
          &&this.birth.fixed===captureClockFixed().toString()&&sameNativeEventScope(this.birth,when))
        enemyBirthProofs.set(uid,{...this.birth,sequence_after_born:sequence,epoch_after_born:epoch});
      }catch(e){enemyBirthProofs.delete(uid);}
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
     }catch(e){enemyBirthProofs.delete(this.uid);enemyEntryUnknown=true;}
     finally{endEnemyHook(label);}
    }
   }));
  }
  installed.push(registerProbeHook(enemyEntryConfig.recycle,{arkdpsName:'BObject.OnRecycle',
   onEnter(args){
     if(!awaitingEnemyTile.size&&!enemyBirthProofs.size&&!enemyHealthPhases.size&&!pendingEnemyRebirth.size&&!enemyHpWrites.size
        &&!enemyHpBirths.size)return;
    try{
     const object=args[0],uid=object.add(0x18).readU32();
      if(coalesceEnemyHp){
       const instance=String(uid);
       if([...enemyHpRuns.values()].some(run=>run.uid===instance))flushCoalescedEnemyHp();
       enemyHpBirths.delete(instance);
      }
      // Retire an in-flight setter before the pooled pointer can be reused.
      enemyHpWrites.delete(object.toString());
     if(awaitingEnemyTile.get(uid)?.equals(object))awaitingEnemyTile.delete(uid);
     enemyBirthProofs.delete(uid);
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
  installed.push(registerProbeHook(enemyPhaseMethods.fakeDeath,{arkdpsName:'Enemy.DoFakeDeath',
   onEnter(args){
    if(stopped||fatal)return;
    try{
      const enemy=args[0],when=time(),uid=enemy.add(0x18).readU32();
      refreshEnemyEntryFrame(when.frame);
      if(pendingEnemyRebirth.has(uid))return;
      pendingEnemyRebirth.set(uid,'waiting');enemyBirthProofs.delete(uid);
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
  installed.push(registerProbeHook(enemyPhaseMethods.reborn,{arkdpsName:'Enemy.DoReborn',
   onEnter(args){
    if(stopped||fatal)return;
    try{
     const enemy=args[0],when=time();
     refreshEnemyEntryFrame(when.frame);
     this.uid=enemy.add(0x18).readU32();enemyBirthProofs.delete(this.uid);
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
      noteModifierHpWriterAttempt(enemy);
      const existing=enemyHpWrites.get(key);
      if(existing){
       // Same-thread nested setters may implement the outer write. A concurrent
       // writer cannot share its before/after certificate or coalescing tail.
       if(existing.thread!==undefined&&this.threadId!==undefined&&existing.thread!==this.threadId){
        if(coalesceEnemyHp)flushCoalescedEnemyHp();
        existing.superseded=true;enemyStateUnknown=true;return;
       }
       if(attributeChange&&existing.enemyTarget&&existing.maxHpBefore===null){
        if(coalesceEnemyHp)flushCoalescedEnemyHp();
        existing.maxHpBefore=fixedArg(args[2]);
       }
       return;
      }
      const identity=checkedNativeView(enemy,0x1c);
      const klass=ptr(identity.getBigUint64(0,true)),uid=identity.getUint32(0x18,true);
      enemyTarget=isEnemy(enemy,klass);
      // Before Born, enemy HP initialization is covered by enemy_enter.
      // Stage traps never create an entry; the host admits them via snapshot.
      if(enemyTarget&&!(enemy.add(0x25).readU8()))return;
      let capturedId;
      if(!enemyTarget){
       if(attributeChange)return;
       try{capturedId=targetKey(enemy);}catch(error){return;}
       if(!stageTrapId(enemy,capturedId))return;
      }
      // This setter publishes only the closing scope. The opening clock still
      // detects a new round and brackets the write with the closing clock.
      const deferScope=coalesceEnemyHp&&enemyTarget&&label==='Entity.SetHpInternal';
      const when=time(false,!deferScope);
      refreshEnemyEntryFrame(when.frame);
      if(awaitingEnemyTile.has(uid)||pendingEnemyRebirth.has(uid))return;
      if(coalesceEnemyHp&&attributeChange)flushCoalescedEnemyHp();
      if(!uid)throw new Error('missing enemy instance UID');
      const evidence=enemyIdentity(enemy,uid);
      const rawHp=targetHp(enemy);
      const hpBefore=Number.isFinite(rawHp)&&rawHp>=0&&rawHp<1e9?rawHp:null;
      if(hpBefore===null)throw new Error('enemy HP unreadable before setter');
      const maxHpBefore=attributeChange?fixedArg(args[2]):null;
      this.write={enemy,key,uid,when,evidence,hpBefore,maxHpBefore,enemyTarget,deferScope,thread:this.threadId,superseded:false};
      beginModifierHpWriter(this.write,klass,label);
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
      const {enemy,key,uid,when,evidence,hpBefore,maxHpBefore,deferScope,superseded}=write;
      if(enemyHpWrites.get(key)!==write)return;
      enemyHpWrites.delete(key);
      if(stopped||fatal||superseded)return;
      const end=time(false,!deferScope);
      const closing=checkedNativeView(enemy.add(0x18),0x30);
      if(end.frame!==when.frame||end.game_time!==when.game_time||closing.getUint32(0,true)!==uid)
       throw new Error('enemy HP callback changed identity or crossed a frame');
      const hp=Number(closing.getBigInt64(0x28,true))/4294967296;
      if(!Number.isFinite(hp)||hp<0||hp>=1e9)throw new Error('enemy HP unreadable after callback');
      finishModifierHpWriter(write,end,hp);
      if(hp===hpBefore&&maxHpBefore===null)return;
      const maxHp=write.enemyTarget?enemyMaxHp(enemy):null;
      if(hp===hpBefore&&(maxHp===null||maxHp===maxHpBefore))return;
      if(deferScope)stampEventScope(end);
      recordEnemyHp(write,end,label,hp,maxHp);
     }catch(e){
      if(coalesceEnemyHp)flushCoalescedEnemyHp();
      if(write.enemyTarget)enemyStateUnknown=true;
     }
     finally{endEnemyHook(label);}
    }
   };
   try{
    callbacks.arkdpsName='hp.'+label;
    installed.push(registerProbeHook(address,callbacks));
    if(attributeChange)enemyMaxHpReady=enemyAttributesReady;
   }catch(e){
    if(!attributeChange)throw e;
    cfg.enemyMaxHpError=String(e);
   }
  }
  const label='Enemy.OnFinish';
  installed.push(registerProbeHook(enemyStateMethods.finish,{arkdpsName:label,
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
      awaitingEnemyTile.delete(uid);enemyBirthProofs.delete(uid);
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
    installed.push(registerProbeHook(enemyStateMethods.trapFinish,{arkdpsName:label,
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
function verifiedSourceInstance(scope){
 if(!enemyEntryConfig&&!characterLifecycleMethods)return null;
 try{
  const p=scope.nativeModifierSource,uid=scope.nativeSourceInstanceUid;
  if(!p||p.isNull()||!Number.isSafeInteger(uid)||uid<=0||p.add(0x18).readU32()!==uid
    ||entityId(p)!==scope.source||sourceCharacterUid(p,scope.source)!==scope.sourceCharacterUid)return null;
  return uid;
 }catch(e){return null;}
}
function beginCharacterLifecycle(actor){
 try{
 if(!characterLifecycleMethods||!actor||actor.isNull()||!actor.readPointer().equals(characterLifecycleMethods.character))return null;
 const source=entityId(actor),uid=sourceCharacterUid(actor,source),instance=actor.add(0x18).readU32();
 if(!source?.startsWith('char_')||!uid||!instance)return null;
 const when=time(),fixed=captureClockFixed().toString();
 if(captureClockFrame()!==when.frame)return null;
 return {actor,source,uid,instance,when,fixed};
 }catch(e){return null;}
}
function endCharacterLifecycle(start,end){
 const current=beginCharacterLifecycle(start.actor);
 if(!current||!sameMitigationTime(start.when,end)||!sameMitigationTime(current.when,end)||current.fixed!==start.fixed
    ||current.source!==start.source||current.uid!==start.uid||current.instance!==start.instance)return null;
 return {version:1,instance_uid:current.instance,data_uid:current.uid,frame:end.frame,game_time:end.game_time,fixed:current.fixed};
}
function installCharacterBornHook(){
 if(!characterLifecycleMethods)return;
 const label='Character.Born';
 try{
 const hook=registerProbeHook(characterLifecycleMethods.born,{arkdpsName:label,
  onEnter(args){
   if(stopped||fatal)return;
   // Born may allocate instanceUid inside the call. Capture receiver/data and the raw clock first.
   if(!args[0].readPointer().equals(characterLifecycleMethods.character))return;
   if(args[0].add(0x25).readU8()!==0)return;
   this.started=true;beginEnemyHook(label);this.actor=args[0];this.when=time();
   this.fixed=captureClockFixed().toString();this.source=entityId(this.actor);
   this.uid=sourceCharacterUid(this.actor,this.source);
  },
  onLeave(){if(!this.started)return;try{
   const end=time(),identity=beginCharacterLifecycle(this.actor);
   if(!identity||!this.uid||identity.uid!==this.uid||identity.source!==this.source||!sameMitigationTime(this.when,end)
      ||identity.fixed!==this.fixed||this.actor.add(0x25).readU8()!==1||this.actor.add(0x26).readU8()!==0)return;
   const proof=endCharacterLifecycle(identity,end);if(!proof)return;
   record(identity.source,'character_born',0,end,{method:'Character.Born'},
    {source_instance_uid:identity.instance,actor_lifecycle:{...proof,state:'born'}},identity.uid);
  }finally{endEnemyHook(label);}}
 });hooks.push(hook);characterLifecycleBornReady=true;
 }catch(e){cfg.characterLifecycleError=String(e);}
}
function nativeSkillCastIdentity(skill,observation=null){
 const config=skillCastStateMethods;
 if(!config||!skill||skill.isNull())return null;
 const klass=skill.readPointer();
 if(klass.isNull()||!config.assignable(config.basicSkill,klass))return null;
 const owner=skill.add(skillF.owner).readPointer();
 if(owner.isNull())return null;
 const ownerClass=owner.readPointer();
 if(ownerClass.isNull())return null;
 // Only Entity/BObject receivers have the verified id/instance UID layout.
 // Classify a known foreign owner before requiring player Character fields.
 if(!config.assignable(config.entity,ownerClass)||!config.assignable(config.bObject,ownerClass))return null;
 const source=entityId(owner);
 if(!source.startsWith('char_'))return null;
 if(observation)observation.confirmedLocal=true;
 if(!ownerClass.equals(config.character)||!owner.add(skillF.charSkill).readPointer().equals(skill))throw new Error('native skill owner/current skill mismatch');
 const ownerUid=owner.add(0x18).readU32(),data=owner.add(0x538).readPointer();
 if(!ownerUid||data.isNull()||!data.readPointer().equals(config.characterData))throw new Error('native skill character identity unavailable');
 const sourceUid=data.add(0x6c).readU32(),skillData=skill.add(skillF.data).readPointer();
 if(!sourceUid||skillData.isNull()||!skillData.readPointer().equals(config.skillData))throw new Error('native skill data identity unavailable');
 const info=skillIdentity(owner,skill);
 if(!info.skill_key)throw new Error('native skill key unavailable');
 return {skill,klass,owner,ownerUid,data,sourceUid,skillData,source,info};
}
function sameNativeSkillCastIdentity(a,b){
 return a&&b&&a.skill.equals(b.skill)&&a.klass.equals(b.klass)&&a.owner.equals(b.owner)
  &&a.ownerUid===b.ownerUid&&a.data.equals(b.data)&&a.sourceUid===b.sourceUid
  &&a.skillData.equals(b.skillData)&&a.source===b.source
  &&a.info.skill_key===b.info.skill_key&&a.info.skill_id===b.info.skill_id;
}
function nativeSkillCastParent(actor,skill,thread){
 const stack=nativeSkillCastCalls.get(thread),parent=stack?.[stack.length-1];
 if(!parent||!parent.identity||!parent.identity.owner.equals(actor)||skill&&!parent.identity.skill.equals(skill))return null;
 return parent;
}
function installSkillCastStateHook(){
 if(!skillCastStateMethods)return;
 const label='BasicSkill.OnCastFinish';
 try{
  const hook=registerProbeHook(skillCastStateMethods.address,{
   arkdpsName:label,
   onEnter(args){
    if(stopped||fatal)return;
    this.started=true;beginEnemyHook(label);
    try{this.skill=args[0];this.identity=nativeSkillCastIdentity(this.skill,this);
     if(!this.identity)return;
     this.fixed=captureClockFixed().toString();this.when=time();
     if(captureClockFrame()!==this.when.frame||captureClockFixed().toString()!==this.fixed)throw new Error('unstable native skill entry clock');
     this.reason=args[2].toInt32();this.invocation=++nativeSkillCastInvocation;this.tid=this.threadId;
     if(!Number.isSafeInteger(this.invocation))throw new Error('native skill invocation exhausted');
     let stack=nativeSkillCastCalls.get(this.tid);if(!stack){stack=[];nativeSkillCastCalls.set(this.tid,stack);}stack.push(this);
    }
    catch(e){this.invalid=true;if(this.confirmedLocal)skillUnknown=true;}
   },
   onLeave(){
    if(!this.started)return;
    try{
     if(this.invalid||!this.identity)return;
     const end=time(),current=nativeSkillCastIdentity(this.skill);
     if(!sameMitigationTime(this.when,end)||!sameNativeSkillCastIdentity(this.identity,current))throw new Error('native skill state identity/clock changed');
     const wait=this.skill.add(skillCastStateMethods.wait).readU8();
     if(wait!==0&&wait!==1)throw new Error('invalid native skill wait Boolean');
     if(captureClockFrame()!==end.frame||captureClockFixed().toString()!==this.fixed)throw new Error('native skill fixed clock changed');
     record(current.source,'skill_end',1,end,{method:label},
      {...current.info,skill_reason:this.reason,skill_wait_for_end:wait===1,source_instance_uid:current.ownerUid,skill_cast_invocation:this.invocation},current.sourceUid);
    }catch(e){if(this.confirmedLocal)skillUnknown=true;}
    finally{
     if(this.invocation){const stack=nativeSkillCastCalls.get(this.tid);if(stack?.[stack.length-1]===this)stack.pop();else skillUnknown=true;
      if(!stack?.length)nativeSkillCastCalls.delete(this.tid);}
     endEnemyHook(label);
    }
   }
  });
  hooks.push(hook);skillCastStateReady=true;
 }catch(e){cfg.skillCastStateError=String(e);}
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
 hooks.push(registerProbeHook(ptr(m.address),{
  arkdpsName:m.owner+'.'+m.name,
  onEnter(args){
   active++;epoch++;this.callbackEpoch=epoch;this.started=true;this.name=m.owner+'.'+m.name;this.tid=this.threadId;
   activeHooks.set(this.name,(activeHooks.get(this.name)||0)+1);
   if(this.name==='Entity.ApplyModifier'){
    let raw=activeApplyScopes.get(this.tid);
    if(raw){
     // The independent-writer certificate requires an unnested raw call,
     // including foreign/unknown calls absent from the money stack.
     this.hpWriterUncertain=true;for(const parent of raw)parent.hpWriterUncertain=true;
     this.settlementDomainNested=true;
     for(const parent of activeModifiers.get(this.tid)||[])
      if(parent.settlementDomainEntry?.nonEnemyCharacter)parent.settlementDomainNested=true;
    }else{raw=[];activeApplyScopes.set(this.tid,raw);}
    raw.push(this);
   }
   try{
    if(this.name==='BattleController.OnCharacterFinished'){
     this.skillEnd=true;
     this.source=entityId(args[1]);
     // A deployable's removal does not end its owner's skill.
     if(!this.source.startsWith('char_')){this.ignore=true;return;}
     this.sourceCharacterUid=sourceCharacterUid(args[1],this.source);
     this.lifecycle=beginCharacterLifecycle(args[1]);
     this.skillReason=args[2].toInt32();
     return;
    }
    if(this.name.startsWith('Character.')){
     const method=this.name.slice('Character.'.length);
     if(SKILL_START_METHODS.has(method)||SKILL_END_METHODS.has(method)){
      this.skillStart=SKILL_START_METHODS.has(method);this.skillEnd=!this.skillStart;
      try{
       this.source=entityId(args[0]);
       this.sourceCharacterUid=sourceCharacterUid(args[0],this.source);
       this.lifecycle=beginCharacterLifecycle(args[0]);
       this.nativeCastParent=method!=='OnAfterSkill'||skillCastStateMethods&&ptr(m.address).equals(skillCastStateMethods.afterSkill)
        ?nativeSkillCastParent(args[0],method==='OnAfterSkill'?args[1]:null,this.tid):null;
       this.skill=skillIdentity(args[0],method==='OnAfterSkill'?args[1]:null);
       this.skillReason=(method==='OnAfterSkill'&&args[2])?args[2].toInt32():null;
      }catch(e){skillUnknown=true;this.ignore=true;}
      return;
     }
    }
    // Nested modifier bookkeeping compares the clock and settlement generation.
    // The published hit receives its own freshly verified scope at settlement.
    this.when=time(false,false);
    if(this.name==='Buff.OnTakeDamage'){const s=stacks.get(this.tid)||[];s.push(args[0]);stacks.set(this.tid,s);this.pushed=true;return;}
    if(this.name.endsWith('.OnAfterOutputDamage')){
     // args[0] is the hidden IL2CPP `this` receiver; modifier is args[1].
     this.mod=args[1];
     try{this.target=this.mod.add(0x10).readPointer();if(traceHpContext)this.hpBefore=targetHp(this.target);}catch(e){this.target=null;this.hpBefore=null;}
     captureSettlementDomain(this);
     try{
      const source=this.mod.readPointer();
      this.nativeModifierSource=source;
      this.nativeSourceInstanceUid=(!enemyEntryConfig&&!characterLifecycleMethods)||source.isNull()?null:source.add(0x18).readU32();
      this.source=source.isNull()?null:entityId(source);
      this.sourceCharacterUid=sourceCharacterUid(source,this.source);
     }catch(e){this.source=null;this.sourceReadFailed=true;}
     // An independently observed source form or token host needs a same-scope return.
     if(this.sourceCharacterUid&&this.source?.startsWith('token_'))stampEventScope(this.when);
     if(this.sourceCharacterUid&&this.source==='char_002_amiya')beginAutoCharacterForm(this);
     try{this.amount=fp(this.mod.add(0x30));}catch(e){this.amount=null;}
     try{this.real=fp(this.mod.add(0x48));}catch(e){this.real=null;}
     this.modifierScope=damageModifier(this.tid,this.mod);
     if(this.modifierScope)observeSettlementDomain(this.modifierScope,this.settlementDomainEntry);
     traceModifierPhase(this,'enter');
     if(this.name==='Entity.OnAfterOutputDamage'&&enemyDamageReady){
      if(this.sourceReadFailed)noteSettlementProofGap(this,this.when,'missing output source identity');
      if(this.source&&own(this.source)){
       if(this.modifierScope?.mitigationModifierReused){
        noteSettlementProofGap(this.modifierScope||this,this.when,'reused output modifier');
       }else if(!matchOutputSettlement(this,this.modifierScope,this.nativeModifierSource))
        damageSettlement(this,this.target,this.real,this.when);
      }
     }
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
     try{
      const source=this.mod.readPointer();
      this.source=entityId(source);
      this.sourceCharacterUid=sourceCharacterUid(source,this.source);
     }catch(e){this.source=null;}
     try{this.amount=fp(this.mod.add(0x30));}catch(e){this.amount=null;}
     if(traceHpContext)try{this.real=fp(this.mod.add(0x48));}catch(e){this.real=null;}
     if(this.name==='Unit.OnTakeDamage'&&this.source){
      const scope=damageModifier(this.tid,this.mod);
      this.modifierScope=scope;
      if(scope)observeSettlementDomain(scope,readSettlementDomain(this.target,this.mod,this.when));
      if(scope&&scope.source===this.source&&sameMitigationTime(scope.when,this.when)){
       if(scope.targetHit)scope.targetHitAmbiguous=true;
       else{
        scope.targetHit={target:this.target,hpBefore:this.hpBefore,uid:damageTargetUid(this.target)};
        beginTargetSettlement(this,scope);
       }
      }
     }
     traceModifierPhase(this,'enter');
     return;
    }
    if(this.name==='Entity.ApplyModifier'){
     this.nativeScopeKey=nativeAutoStage?.enabled?(nativeAutoEffective?.scope_key??null):undefined;
     this.mod=args[1];
     try{this.target=this.mod.add(0x10).readPointer();this.hpBefore=targetHp(this.target);}catch(e){this.target=null;this.hpBefore=null;}
     captureSettlementDomain(this);
     observeNestedTargetSettlement(this);
     try{
      const source=this.mod.readPointer();
      this.nativeModifierSource=source;
      this.nativeSourceInstanceUid=(!enemyEntryConfig&&!characterLifecycleMethods)||source.isNull()?null:source.add(0x18).readU32();
      this.nativeSourceNull=source.isNull();
      this.source=this.nativeSourceNull?null:entityId(source);
      this.sourceCharacterUid=sourceCharacterUid(source,this.source);
    }catch(e){this.source=null;this.sourceReadFailed=true;}
     // An independently observed source form or token host needs a same-scope return.
     if(this.sourceCharacterUid&&this.source?.startsWith('token_'))stampEventScope(this.when);
     if(this.sourceCharacterUid&&this.source==='char_002_amiya')beginAutoCharacterForm(this);
    if(enemyDamageReady&&this.sourceReadFailed)
     noteSettlementProofGap(this,this.when,'missing modifier source identity');
    if(enemyDamageReady&&this.nativeSourceNull){
      try{
       if(this.mod.add(0x68).readS32()===5)this.burstTarget=enemyDamageIdentity(this.target);
      }catch(e){/* An unverified target only affects an actual settlement. */}
     }
     if((this.source&&own(this.source))||
        (this.nativeSourceNull&&this.mod.add(0x68).readS32()===5)){
      enterModifier(this);
      const type=this.mod.add(0x68).readS32();
      if(probeAdapter.directHealingProof&&type===4&&this.target&&!this.target.isNull())
       this.healingTarget={id:targetKey(this.target),uid:damageTargetUid(this.target)};
      if(enemyDamageReady&&[1,2,3,5].includes(type))this.settlementTargetUid=damageTargetUid(this.target);
      captureSettlementAnchor(this,type);
      captureSettlementIssueSource(this);
      try{captureMitigation(this,this.mod.readPointer());}catch(e){/* Existing damage remains observable. */}
      if(type===5)this.elementalTargetUid=this.settlementTargetUid??damageTargetUid(this.target);
      if(this.source&&this.target&&!this.target.isNull()&&this.mod.add(0x60).readS32()===5&&
         type===0){
       this.elementalBarTarget={pointer:this.target,uid:null};
       try{
        this.elementalBarTarget.uid=this.target.add(0x18).readU32();
        const birth=enemyBirthProofs.get(this.elementalBarTarget.uid);
        if(birth&&birth.target_instance===this.target.toString()&&birth.klass===this.target.readPointer().toString()
           &&birth.target===entityId(this.target)&&this.callbackEpoch>birth.epoch_after_born&&sameNativeEventScope(birth,this.when))
         this.elementalBarBirthReturn={version:1,sequence_after_born:birth.sequence_after_born,
          epoch_after_born:birth.epoch_after_born,modifier_enter_epoch:this.callbackEpoch};
       }catch(e){/* The normal bar record still has its target pointer. */}
      }
     }
     traceModifierPhase(this,'enter');
     return;
    }
    this.bb=args[1];this.shieldModifierRef=args[2];
    try{this.damageTarget=this.shieldModifierRef.add(0x10).readPointer();}catch(e){this.damageTarget=null;}
    this.shieldScope=enemyDamageReady?shieldModifier(this.tid,this.shieldModifierRef,this.damageTarget,this.when):null;
    const s=stacks.get(this.tid)||[];
    if(!s.length){
     shieldUnknown=true;markShieldUnknown(this.when.frame,null);
     if(enemyDamageReady)markUnmatchedShield(this.tid,this.shieldModifierRef,this.damageTarget,this.when);
     this.ignore=true;return;
    }
    this.buff=buff(s[s.length-1]);
    if(enemyDamageReady&&!this.shieldScope&&this.buff.hasShield===1)
     markUnmatchedShield(this.tid,this.shieldModifierRef,this.damageTarget,this.when);
    if(this.shieldScope&&this.buff.hasShield===1){
     this.before=dynamic(this.bb);this.amount=fixedArg(args[4]);this.incoming=fp(this.shieldModifierRef.add(0x30));
     if([this.before,this.amount,this.incoming].every(value=>Number.isFinite(value)&&value>=0)&&
        this.amount<=this.incoming+0.01&&this.amount<=this.before+0.01)
      this.settlementShield=true;
     else this.shieldScope.numericShieldUnknown=true;
    }
    try{this.source=entityId(this.buff.source);}catch(e){this.source=null;}
    // A non-player shield still absorbs damage dealt to that target, so the
    // hit's overkill cannot claim the absorbed part as its own excess.
    if(!own(this.source)){
     if(!this.source)shieldUnknown=true;
     markShieldUnknown(this.when.frame,this.damageTarget);this.ignore=!this.settlementShield;return;
    }
    if(this.buff.hasShield!==1||!['shining_s_2','catsld_t_1[core]'].includes(this.buff.key)){shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);this.ignore=!this.settlementShield;return;}
    this.legacyShield=true;
    this.sourceCharacterUid=sourceCharacterUid(this.buff.source,this.source);
    if(!this.settlementShield){this.before=dynamic(this.bb);this.amount=fixedArg(args[4]);this.incoming=fp(args[2].add(0x30));}
    if(!Number.isFinite(this.before)||this.before<0||!Number.isFinite(this.incoming)||this.incoming<0){shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);this.ignore=true;return;}
   }catch(e){
    if(this.skillStart||this.skillEnd){skillUnknown=true;}
    else if(this.name==='Entity.ApplyModifier'){if(this.source&&own(this.source))unknownSources.add(this.source);else{skippedUnattributed++;}}
    else{
     shieldUnknown=true;markShieldUnknown(this.when?this.when.frame:null,this.damageTarget);
     if(enemyDamageReady&&this.shieldModifierRef&&this.when)
      markUnmatchedShield(this.tid,this.shieldModifierRef,this.damageTarget,this.when);
    }
    this.ignore=true;
   }
  },
  onLeave(){
   let endStamp;
   try{
    if(this.pushed){const s=stacks.get(this.tid);s.pop();if(!s.length)stacks.delete(this.tid);return;}
    if(this.ignore&&this.name!=='Entity.ApplyModifier')return;
    const end=endStamp=time();
    if(this.skillStart||this.skillEnd){
     if(this.ignore)return;
     if(!this.source){skillUnknown=true;return;}
     const info=this.skill||{};
     const evidence={skill_key:info.skill_key||null,skill_id:info.skill_id||null,skill_name:info.skill_name||null};
     if(this.skillEnd)evidence.skill_reason=this.skillReason;
     const parent=this.nativeCastParent;
     if(parent&&parent.identity.source===this.source&&parent.identity.sourceUid===this.sourceCharacterUid
        &&sameMitigationTime(parent.when,end)&&nativeSkillCastCalls.get(parent.tid)?.includes(parent)){
      evidence.source_instance_uid=parent.identity.ownerUid;
      if(['Character.OnAfterSkill','Character.OnSkillFinish','Character.SwitchOutFromSkillState'].includes(this.name))
       evidence.skill_cast_invocation=parent.invocation;
     }
     if(this.lifecycle){
      const identity=endCharacterLifecycle(this.lifecycle,end);
      if(identity){
       evidence.source_instance_uid=identity.instance_uid;
       if(this.name==='BattleController.OnCharacterFinished')evidence.actor_lifecycle={...identity,
        state:'finish',reason_type:'Torappu.Battle.Entity.FinishReason',reason:this.skillReason};
      }
     }
     try{record(this.source,this.skillStart?'skill_start':'skill_end',1,end,{method:this.name},evidence,this.sourceCharacterUid);}catch(e){skillUnknown=true;}
     return;
    }
     if(!sameMitigationTime(end,this.when))throw new Error('callback crossed frame');
    if(this.name==='Unit.OnTakeDamage')finishTargetSettlement(this,end);
    traceModifierPhase(this,'leave');
    if(this.name==='Entity.ApplyModifier'){
     traceNestedParents(this,'child_return');
     traceNestedParentFinal(this);
    }
    if(this.name.endsWith('.OnAfterOutputDamage')||this.name==='Entity.OnTakeDamage'||this.name==='Unit.OnTakeDamage'){
     if(this.ignore)return;
     if(!this.target||this.target.isNull())return;
     if((!enemyDamageReady||this.modifierScope?.settlementDomainEntry?.kind!=='potential')&&this.name==='Unit.OnTakeDamage'&&this.source&&own(this.source)&&this.target&&!this.target.isNull()&&Number.isFinite(this.amount)){
      const targetKeyValue=targetKey(this.target)||this.target.toString();
      const shield=shieldContextUnknown(end.frame,this.target)?null:shieldTotal(end.frame,this.target);
      try{record(this.source,'hp_damage',this.amount,end,{method:this.name,type:this.mod.add(0x68).readS32(),target:this.mod.add(0x60).readS32()},{target:targetKeyValue,hp_before:this.hpBefore,shield_absorbed:shield,target_instance:this.target.toString(),target_type:this.mod.add(0x60).readS32(),damage_type:this.mod.add(0x68).readS32()},this.sourceCharacterUid);}catch(e){fatal=String(e);}
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
      if(evidence&&instance){
       evidence.target_instance=instance;
       let reduction;
       try{
        reduction=mitigationEvidence(this,type,amount);
        if(reduction)Object.assign(evidence,reduction);
       }catch(e){/* Missing formula evidence affects only mitigation. */}
       try{
        if(owned)settleDamage(this,this.target,end,amount,type,reduction,evidence);
        else settleSourcelessBurst(this,evidence,end,amount,reduction);
        if(!this.settlementRecordEmitted)record(owned?this.source:null,'elemental_burst',amount,end,{method:this.name,type,target:targetType},evidence,this.sourceCharacterUid);
        if(this.settlementRecord)try{
         rememberNestedElementalSettlement(this,end,evidence);
         rememberNestedElementalKill(this,end,evidence);
        }catch(e){/* Independently verified burst remains queued. */}
        noteUnpairedSettlement(this,end,type,amount,evidence);
       }catch(e){fatal=String(e);}
      }else{
       if(owned)settleDamage(this,this.target,end,amount,type);
       else settleSourcelessBurst(this,evidence,end,amount);
       if(amount>0&&fp(this.mod.add(0x48))!==0)bossDamageUnknown=true;
      }
      return;
     }
     if(!owned)return;
     if(targetType!==0)return;
     if(![1,2,3,4].includes(type)){unknownSources.add(this.source);engagementUnknownSources.add(this.source);return;}
     const kind=type===4?'healing':'damage';
     let damageInstance=instance,evidence=null;
     const proof={method:this.name,type,target:targetType};
     if(probeAdapter.directHealingProof&&kind==='healing')proof.direct_healing=directHealingProof(this,amount);
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
      if(!evidence)try{evidence=damageSettledAfterNestedKill(this,end,amount);}catch(e){/* Keep the nominal settlement. */}
      const damageTarget=hit?hit.target:this.target;
      evidence={target:targetKey(damageTarget),target_instance:damageInstance,
       target_type:targetType,damage_type:type,...evidence};
      // Modifier.CancelReason.MISS can retain its nominal damage value. A
      // verified miss does not enter the native damage counter.
      const missed=evidence.cancel_reason===2&&evidence.shield_absorbed===0;
      if(evidence&&evidence.real_delta===0&&evidence.hp_before===evidence.hp_after
         &&!evidence.settled_after_nested_kill
         &&(evidence.hp_before===0&&evidence.killed||evidence.cancel_reason===1||missed)){this.settlementNoConsumption=true;return;}
      if(evidence.cancel_reason===3&&verifiedZeroCancellation(this,hit,end,amount)){
       this.settlementNoConsumption=true;return;
      }
      let reduction;
      try{
       reduction=mitigationEvidence(this,type,amount);
       if(reduction)evidence=Object.assign(evidence||{},reduction);
      }catch(e){/* Missing formula evidence affects only mitigation. */}
      const targetEvidence=verifiedTargetSettlement(this,damageTarget,end,amount,type);
      if(targetEvidence){
       evidence={...evidence,...targetEvidence};
       proof.evidence_method='Unit.OnTakeDamage+Entity.ApplyModifier';
      }
      if(this.nestedParentSettlementProof)proof.nested_settlement=this.nestedParentSettlementProof;
      if(this.nestedElementalChainProof)proof.nested_elemental_chain=this.nestedElementalChainProof;
      settleDamage(this,damageTarget,end,amount,type,reduction,targetEvidence||
                   (evidence.settled_after_nested_kill||proof.evidence_method==='Entity.OnAfterOutputDamage'?evidence:null));
     }
     if(kind==='damage'&&enemyDamageReady){
      finalizeModifierSettlement(this,end);
      if(this.settlementRecordEmitted){
       noteUnpairedSettlement(this,end,type,amount,evidence);
       return;
      }
      // This independent native observation has no certificate replacement.
      // Keep it with its existing quality failure/outside-domain semantics.
     }
     try{
      const instance=verifiedSourceInstance(this);if(instance)evidence={...evidence,source_instance_uid:instance};
      const event=record(this.source,kind,amount,end,proof,evidence,this.sourceCharacterUid);
      if(kind==='damage')noteUnpairedSettlement(this,end,type,amount,evidence);
     }catch(e){fatal=String(e);}
    }else{
     const after=dynamic(this.bb);
     if(!Number.isFinite(after)||after<0||Math.abs(this.before-after-this.amount)>0.01){shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);if(this.shieldScope)this.shieldScope.numericShieldUnknown=true;return;}
     if(this.settlementShield){
      this.shieldScope.firstShieldIncoming??=this.incoming;
      this.shieldScope.numericShield=(this.shieldScope.numericShield||0)+this.amount;
     }
     if(!this.legacyShield)return;
     try{record(this.source,'shield_absorbed',this.amount,end,{method:this.name,buff:this.buff.key,before:this.before,after,incoming:this.incoming},undefined,this.sourceCharacterUid);}catch(e){fatal=String(e);}
     noteShield(this.when.frame,this.damageTarget,this.amount);
    }
   }catch(e){
    if(this.skillStart||this.skillEnd){skillUnknown=true;}
    else if(this.name==='Entity.ApplyModifier'){
     if(enemyDamageReady&&this.tracksModifier)
      noteSettlementProofGap(this,endStamp,'modifier callback could not verify final settlement');
     if(this.source)unknownSources.add(this.source);else{skippedUnattributed++;}
    }
    else{shieldUnknown=true;markShieldUnknown(this.when.frame,this.damageTarget);if(this.shieldScope)this.shieldScope.numericShieldUnknown=true;}
   }finally{
    if(this.name==='Entity.ApplyModifier'){
     const raw=activeApplyScopes.get(this.tid);
     const index=raw?.lastIndexOf(this)??-1;
     if(index>=0)raw.splice(index,1);
     if(raw?.length===0)activeApplyScopes.delete(this.tid);
    }
    if(this.tracksModifier){
     finalizeModifierSettlement(this,endStamp);
     leaveModifier(this);
    }
    if(this.started){active--;const left=(activeHooks.get(this.name)||1)-1;if(left>0)activeHooks.set(this.name,left);else activeHooks.delete(this.name);}
   }
  }
 }));
 if(characterLifecycleMethods&&m.owner==='BattleController'&&m.name==='OnCharacterFinished'
    &&ptr(m.address).equals(characterLifecycleMethods.finish))characterLifecycleFinishedReady=true;
}
installEnemyEntryHooks();
installCharacterBornHook();
installEnemyPhaseHook();
installEnemyStateHooks();
installMitigationHooks();
installSkillCastStateHook();
let terminalBoundary=null,terminalGeneration=0;
const terminalDiagnostics={destroy_entered:0,destroy_left:0,candidates:0,captured:0,rejected:0,invalidated:0,last_rejection:null};
function countTerminalDiagnostic(key){terminalDiagnostics[key]=Math.min(1000000,terminalDiagnostics[key]+1);}
function rejectTerminal(reason){countTerminalDiagnostic('rejected');terminalDiagnostics.last_rejection=reason;}
// IL2CPP class metadata is immutable for this attached process. Cache only
// its name, never object class pointers, owners, roots or gameplay fields.
const terminalClassNames=new Map();
function terminalClassName(klass){
 const key=klass.toString();
 if(terminalClassNames.has(key))return terminalClassNames.get(key);
 const name=className(klass);
 if(terminalClassNames.size<128)terminalClassNames.set(key,name);
 return name;
}
function terminalIdentity(controller,controllerClass,reject){
 if(controller.isNull())return reject?.('singleton_empty')??null;
 const nativeOffset=probeAdapter.nativeOffset??0x10;
 const controllerFields=nativeOffset===0x10?checkedNativeView(controller,0x18):null;
 const klass=controllerFields?ptr(controllerFields.getBigUint64(0,true)):controller.readPointer();
 if(!klass.equals(controllerClass)){
  if(!reject)return null;
  let observed=null;
  try{if(!klass.isNull())observed=className(klass);}catch(e){/* The mismatch is already proved. */}
  return reject('controller_class_mismatch',observed);
 }
 // Use the same two ownership references as the guarded native snapshot.
 const native=controllerFields?ptr(controllerFields.getBigUint64(0x10,true)):controller.add(nativeOffset).readPointer();
 const logger=controller.add(probeAdapter.loggerOffset).readPointer();
 const level=controller.add(probeAdapter.levelOffset).readPointer();
 if(native.isNull())return reject?.('native_missing')??null;
 if(logger.isNull())return reject?.('logger_missing')??null;
 if(level.isNull())return reject?.('level_missing')??null;
 if(!native.add(probeAdapter.nativeOwnerOffset??0x28).readPointer().equals(controller))return reject?.('native_owner_mismatch')??null;
 const loggerFields=(probeAdapter.loggerOwnerOffset??0x18)===0x18&&(probeAdapter.statsOffset??0x30)===0x30?
  checkedNativeView(logger,0x38):null;
 const loggerClass=terminalClassName(loggerFields?ptr(loggerFields.getBigUint64(0,true)):logger.readPointer());
 if(loggerClass!=='BattleLogger')return reject?.('logger_class_mismatch',loggerClass)??null;
 if(!(loggerFields?ptr(loggerFields.getBigUint64(0x18,true)):logger.add(probeAdapter.loggerOwnerOffset??0x18).readPointer()).equals(controller))return reject?.('logger_owner_mismatch')??null;
 const levelClass=terminalClassName(level.readPointer());
 if(levelClass!=='LevelData')return reject?.('level_class_mismatch',levelClass)??null;
 const stats=loggerFields?ptr(loggerFields.getBigUint64(0x30,true)):logger.add(probeAdapter.statsOffset??0x30).readPointer();
 if(stats.isNull())return reject?.('stats_missing')??null;
 const statsClass=terminalClassName(stats.readPointer());
 if(statsClass!=='BattleStats')return reject?.('stats_class_mismatch',statsClass)??null;
 return {controller:controller.toString(),native:native.toString(),logger:logger.toString(),
  stats:stats.toString(),level:level.toString()};
}
function currentTerminalBoundary(candidate=terminalBoundary){
 if(!candidate)return null;
 const reason=active?'callbacks_active':fatal?'fatal':stopped?'stopped':candidate.sequence!==sequence?'sequence_changed':
  candidate.epoch!==epoch?'epoch_changed':candidate.damage_settlement_scope!==damageSettlementScope?'settlement_scope_changed':null;
 if(reason){
  countTerminalDiagnostic('invalidated');terminalDiagnostics.last_rejection=reason;
  if(candidate===terminalBoundary)terminalBoundary=null;
  return null;
 }
 return candidate;
}
function installTerminalHooks(){
 if(!terminalMethods)return;
 try{
  hooks.push(registerProbeHook(terminalMethods.state,{arkdpsName:'BattleController.set_state',
   onEnter(args){
    probeAdapter.enterTerminalStateHook(this,args);
    const state=args[1].toInt32();
    this.nativeStateValue=state;this.nativeStateController=args[0];
    if(state===1||state===2){
     resetEnemyHpCoalescing();
     terminalGeneration++;terminalBoundary=null;
      try{
       const identity=terminalIdentity(args[0],terminalMethods.controller);
       probeAdapter.observeTerminalState(args,state,identity);
       if(identity)observeDamageSettlementController(identity,{starting:true});
      }catch(e){/* The checked battle clock can still establish a new round. */}
    }
   },
   onLeave(){
    probeAdapter.leaveTerminalStateHook(this);
   }
  }));
  hooks.push(registerProbeHook(terminalMethods.destroy,{arkdpsName:'BattleController.Destroy',
   onEnter(args){
    countTerminalDiagnostic('destroy_entered');
    this.generation=++terminalGeneration;
    terminalBoundary=null;
    if(active||fatal||stopped){rejectTerminal(active?'callbacks_active':fatal?'fatal':'stopped');return;}
    if(coalesceEnemyHp)flushCoalescedEnemyHp();
    try{
     const identity=terminalIdentity(args[0],terminalMethods.controller),stamp=time(true);
     if(identity&&stamp){
      observeDamageSettlementController(identity);
      let binding=null;
      try{if(typeof probeAdapter.captureTerminalBinding==='function')binding=probeAdapter.captureTerminalBinding(identity,stamp);}
      catch(e){/* A missing tail binding must not erase the existing raw fence. */}
      this.boundary={...identity,...binding,frame:stamp.frame,game_time:stamp.game_time,sequence,epoch,active,
        damage_settlement_scope:stamp.settlementScope};
      countTerminalDiagnostic('candidates');
     }else rejectTerminal(!identity?'identity_unavailable':'clock_unstable');
    }catch(e){rejectTerminal('destroy_entry_unreadable');}
   },
   onLeave(){
    countTerminalDiagnostic('destroy_left');
    if(!this.boundary)return;
    if(this.generation!==terminalGeneration){rejectTerminal('generation_changed');return;}
    const candidate=currentTerminalBoundary(this.boundary);
    if(!candidate)return;
    try{
     const stamp=time(true);
     if(stamp?.frame===candidate.frame&&stamp.game_time===candidate.game_time&&
        stamp.settlementScope===candidate.damage_settlement_scope){
       terminalBoundary=candidate;countTerminalDiagnostic('captured');terminalDiagnostics.last_rejection=null;
      }else rejectTerminal('destroy_clock_changed');
    }catch(e){rejectTerminal('destroy_leave_unreadable');}
   }
  }));
 }catch(e){probeAdapter.terminalInstallFailed(e);}
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
 return {quality_contract_version:1,
  autochess_round_start_ready:!!autoChessRoundResetMethod&&!cfg.autoChessRoundResetError,
  character_lifecycle_ready:characterLifecycleReady(),
  unknown_sources:[...unknownSources],engagement_unknown_sources:[...engagementUnknownSources],
  source_attribution_version:1,
  partial_damage_version:7,
  source_character_uid_ready:sourceCharacterClasses!==null,
  boss_damage_ready:true,boss_damage_unknown:bossDamageUnknown,
  shield_unknown:shieldUnknown,damage_unknown:damageUnknown,engagement_unknown:engagementUnknown,
  skill_unknown:skillUnknown||!cfg.skillReady,skill_ready:!skillUnknown&&cfg.skillReady,
  ...damageSettlementQuality(),
  // Global hook availability only; each hit still needs its own matched pair.
  mitigation_ready:mitigationReady,mitigation_unknown:!mitigationReady,mitigation_error:cfg.mitigationError||null,
  mitigation_diagnostics:mitigationDiagnostics(),...enemyEntryQuality(frame)};
}
function autoChessResetOwner(controller){
 const identity=currentNativeIdentity();
 if(!identity||identity.controller!==controller.toString())return null;
 const stageAddress=ptr(identity.level).add(probeAdapter.autoChessStageOffset??0x18);
 const stagePointer=stageAddress.readPointer(),stage=str(stagePointer);
 if(typeof stage!=='string'||!/AUTOCHESS/i.test(stage))return null;
 return {identity,stagePointer,stage};
}
function sameAutoChessResetOwner(left,right){
 return !!left&&!!right&&left.stage===right.stage&&left.stagePointer.equals(right.stagePointer)&&
  Object.keys(left.identity).every(key=>left.identity[key]===right.identity[key]);
}
function clearAutoChessRoundQuality(previousScope){
 if(damageSettlementScope===previousScope){
  resetDamageSettlementScope();
  damageSettlementStamp(0,0,false);
 }
 unknownSources.clear();engagementUnknownSources.clear();
 shieldUnknown=false;damageUnknown=false;engagementUnknown=false;skillUnknown=false;
 shieldByFrame.clear();shieldUnknownByFrame.clear();
 awaitingEnemyTile.clear();enemyBirthProofs.clear();enemyHealthPhases.clear();pendingEnemyRebirth.clear();enemyHpWrites.clear();
 enemyEntryFrame=0;enemyEntryUnknown=false;enemyPhaseUnknown=false;enemyStateUnknown=false;bossDamageUnknown=false;
}
function installAutoChessRoundResetHook(){
 if(!autoChessRoundResetMethod)return;
 const label='BattleController.ClearRunTimeData';
 const otherCallbacks=()=>active>0||(probeAdapter.pendingMarkCallbacks?.()??0)>1;
 try{
  hooks.push(registerProbeHook(autoChessRoundResetMethod,{arkdpsName:label,
   onEnter(args){
    if(!probeHooksReady||stopped||fatal)return;
    const unclaimed=autoChessUnclaimedZero;
    autoChessUnclaimedZero=null;
    const idle=!otherCallbacks();
    beginEnemyHook(label);this.roundResetStarted=true;
    if(!idle)return;
    try{
     const controller=args[0],owner=autoChessResetOwner(controller);
     if(!owner)return;
     const opening=time(true,false);
     if(!opening)return;
     // The loader can reset in PREPARE, then StartGame finds the same zero.
     // Only a previously guarded zero with no intervening callback can carry
     // that start forward. A second clear cannot erase a first clear's gap.
     if(opening.frame===0&&opening.game_time===0&&epoch!==1&&
        (!sameAutoChessResetOwner(unclaimed,owner)||unclaimed.sequence!==sequence||unclaimed.epoch!==epoch-1))return;
     // A pending HP tail belongs to the old clock. Queue it before taking the
     // immutable per-round sequence origin, never at the new game clock.
     if(coalesceEnemyHp)resetEnemyHpCoalescing();
     this.roundReset={...owner,controller,sequence,epoch,settlementScope:damageSettlementScope};
    }catch(e){/* An unreadable opening owner cannot prove a new origin. */}
   },
   onLeave(){
    if(!this.roundResetStarted)return;
    endEnemyHook(label);
    const entry=this.roundReset;
    if(!entry||stopped||fatal||otherCallbacks()||sequence!==entry.sequence||epoch!==entry.epoch)return;
    try{
     if(captureClockFrame()!==0||captureClockFixed().toString()!=='0')return;
     const binding=nativeBinding(null,true),context=binding?.native_autochess_context;
     const closing=autoChessResetOwner(entry.controller);
     if(!sameAutoChessResetOwner(entry,closing)||otherCallbacks()||sequence!==entry.sequence||
        epoch!==entry.epoch||captureClockFrame()!==0||captureClockFixed().toString()!=='0')return;
     autoChessUnclaimedZero={...closing,sequence,epoch};
     if(!binding||context?.status!=='available'||context.verified_same_frame!==true||
        ![4,5,6].includes(context.phase)||binding.native_frame!==0||binding.native_fixed_time!=='0'||
        binding.native_stage_id!==entry.stage||Object.keys(entry.identity).some(key=>
          binding.native_identity[key]!==entry.identity[key])||sameAutoChessRoundStartBinding(autoChessRoundStart,binding))return;
     autoChessUnclaimedZero=null;
     clearAutoChessRoundQuality(entry.settlementScope);
     const {native_process_pid,native_generation,native_stage_id,native_identity,native_autochess_context,
            live_character_collection_empty,live_character_data_uids,lifecycle_baseline}=binding;
     autoChessRoundStart=JSON.parse(JSON.stringify({version:2,source_method:label,
      frame:0,game_time:0,native_fixed_time:'0',sequence,epoch,active:0,native_process_pid,native_generation,
      native_stage_id,native_identity,native_autochess_context,quality:eventQuality(0),
      live_character_collection_empty,live_character_data_uids,...(lifecycle_baseline?{lifecycle_baseline}:{})}));
    }catch(e){/* The ordinary guarded sample remains available without the origin. */}
   }
  }));
 }catch(e){cfg.autoChessRoundResetError=String(e);}
}
installAutoChessRoundResetHook();
function markEvents(withPresence=false){
 const pending=probeAdapter.markEntryCallbacks?.()??probeAdapter.pendingMarkCallbacks?.()??0;
 if(pending>0&&!fatal&&!stopped)
  return {sequence,epoch,active:pending,active_hooks:[...activeHooks.keys()],retry:true,error:null,stopped:false,
   mark_retry_reason:'callback_pending_at_start'};
 if(coalesceEnemyHp&&!active)flushCoalescedEnemyHp();
 const terminal_boundary=currentTerminalBoundary();
 const prepared=probeAdapter.prepareEventEnvelope?.();
 let current;
 // Only the clock is consumed here. The adapter constructs and validates the
 // scope attached to the mark inside its independently guarded envelope.
 try{current=time(true,false);}
 catch(e){if(!terminal_boundary)throw e;current=null;}
 const envelope=probeAdapter.markEnvelope(current,prepared);
 const quality={...eventQuality(current?.frame),terminal_boundary:currentTerminalBoundary(terminal_boundary),
  terminal_boundary_diagnostics:{...terminalDiagnostics},
  ...(probeAdapter.terminalReadVersion===1?{terminal_read_version:1}:{}),
  ...envelope.fields};
 const fence={sequence,epoch,active,active_hooks:[...activeHooks.keys()]};
 if(!envelope.ready)return {...fence,retry:true,error:fatal,stopped,...quality};
 if(current===null)return {...fence,retry:true,error:fatal,stopped,...quality};
 const mark={frame:current.frame,game_time:current.game_time,...fence,error:fatal,stopped,...quality};
 // The optional Android endpoint has its own bracket. Its failure cannot
 // change the original mark, which remains authoritative for event admission.
 if(withPresence===true&&probeAdapter.autoChessReflected&&!fatal&&!stopped){
  const point=captureAutoChessPresence(mark);
  if(point)mark.native_autochess_presence=point;
 }
 return mark;
}
// Called by the single worker only while this exact closed native record is
// the sealed replay owner. New producer activity cannot invalidate an already
// proved historical prefix; every binding input below comes from owned roles.
function __arkdpsRetireCaptureGraphs(bounds){
 if(typeof probeAdapter.retireCaptureGraphs!=='function')throw new Error('capture retirement unsupported');
 return probeAdapter.retireCaptureGraphs(bounds);
}
function __arkdpsCaptureModeState(){
 if(typeof probeAdapter.captureModeState!=='function')throw new Error('capture mode control unsupported');
 return probeAdapter.captureModeState();
}
function __arkdpsInstallCaptureMode(enabled,barrier){
 if(typeof probeAdapter.installCaptureMode!=='function')throw new Error('capture mode control unsupported');
 return probeAdapter.installCaptureMode(enabled,barrier);
}
function __arkdpsPublicationServed(through){
 if(!Number.isSafeInteger(through)||through<acknowledgedSequence||through>sequence)throw new Error('served publication range unavailable');
 readThroughSequence=Math.max(readThroughSequence,through);
}
function __arkdpsSnapshotPublication(fence,terminalProof=null){
 if(!fence||fence.sequence!==sequence||fence.epoch!==epoch||active!==0||activeHooks.size!==0)throw new Error('publication reducer fence changed');
 const published=typeof __arkdpsPublicationJournal==='object'?__arkdpsPublicationJournal.publishedThrough:acknowledgedSequence;
 if(!Number.isSafeInteger(published)||published<acknowledgedSequence||published>sequence)throw new Error('publication delta origin unavailable');
 const records=queue.slice(published-acknowledgedSequence);
 if(records.length!==sequence-published||records.some((event,index)=>event.sequence!==published+index+1))throw new Error('publication delta incomplete');
 const baseline={};
 if(typeof probeAdapter.baselineEnvelope==='function')Object.assign(baseline,probeAdapter.baselineEnvelope(fence,queue,acknowledgedSequence,sequence));
 Object.assign(baseline,autoChessRoundStartEnvelope(fence,queue,acknowledgedSequence,sequence));
 const mark={...fence,...baseline};
 const read_fields={...eventQuality(),...fence,...baseline,hp_context_dropped:hpContextDropped};
 let terminal_read=terminalProof;
 if(terminal_read===null&&currentTerminalBoundary()){
  const terminal=readTerminalEvents(acknowledgedSequence,sequence,false);
  if(terminal.retry===false)terminal_read=terminal;
 }
 const projection={mark,acknowledged:acknowledgedSequence,events_after:published,events_through:sequence,events:records,
  hp_context:hpContext.filter(item=>item.eventSequence>published&&item.eventSequence<=sequence).map(item=>({eventSequence:item.eventSequence,context:item.context})),read_fields,terminal_read};
 const owned=JSON.parse(JSON.stringify(projection));
 const freeze=value=>{if(value&&typeof value==='object'){for(const child of Object.values(value))freeze(child);Object.freeze(value);}return value;};
 return freeze(owned);
}
function __arkdpsOnClosedRecord(record){
 return typeof probeAdapter.captureRecordMode==='function'?probeAdapter.captureRecordMode(record,()=>capturedClosedRecordProjection(record)):capturedClosedRecordProjection(record);
}
function capturedClosedRecordProjection(record){
 if(typeof __arkdpsReplayActive!=='function'||!__arkdpsReplayActive()||record!==__arkdpsReplayRecord()||
    record.edge!=='leave'||record.closed_after!==true||active!==0||!Number.isSafeInteger(record.facts?.scope_mode_epoch)||record.facts.scope_mode_epoch<0||typeof probeAdapter.capturedPublicationBinding!=='function')return null;
 try{
 const boundary=currentTerminalBoundary();
 if(boundary){
  const rawTerminal=readTerminalEvents(acknowledgedSequence,sequence,false);
  if(rawTerminal.retry!==false)return null;
  const terminal={...rawTerminal,native_capture_scope_mode_epoch:record.facts.scope_mode_epoch};
  const {events:_events,hp_context:_context,after:_after,through:_through,terminal_only:_terminal,...fence}=terminal;
  return __arkdpsSnapshotPublication(fence,terminal);
 }
 const binding=probeAdapter.capturedPublicationBinding();if(!binding)return null;
 if(coalesceEnemyHp)flushCoalescedEnemyHp();
 const current=time(true,false);if(!current)return null;
 if(binding.native_frame!==current.frame||Number(BigInt(binding.native_fixed_time))/4294967296!==current.game_time)return null;
 const value={frame:current.frame,game_time:current.game_time,sequence,epoch,active:0,active_hooks:[],error:fatal,stopped,native_capture_scope_mode_epoch:record.facts.scope_mode_epoch,
  ...eventQuality(current.frame),...binding,terminal_boundary:currentTerminalBoundary(),
  terminal_boundary_diagnostics:{...terminalDiagnostics},terminal_read_version:1};
 return __arkdpsSnapshotPublication(value);
 }catch(error){return null;}
}

function receiveEventRequest(){
 // Frida recv is one-shot. Rearm before dispatch; a late reply can be safely
 // ignored by the host because reads are replayable and ACKs idempotent.
 recv('arkdps-event-request',request=>{
  receiveEventRequest();
  let result;
  try{
   switch(request.operation){
    case 'mark':
     if(Object.prototype.hasOwnProperty.call(request,'autochess_enabled')&&typeof probeAdapter.setAutoChessMode==='function')
      probeAdapter.setAutoChessMode(request.autochess_enabled);
     result=markEvents(request.with_presence===true);break;
    case 'read':result=readEvents(request.after,request.through);break;
    case 'terminal_read':result=readTerminalEvents(request.after,request.through);break;
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
 readTerminalEvents,
 ackEvents,
 stop(){stopped=true;if(typeof probeAdapter.discardInitialBoundary==='function')probeAdapter.discardInitialBoundary('stopped');
  for(const h of hooks)h.detach();return {active,error:fatal};}
};
probeHooksReady=true;
receiveEventRequest();
send({ready:true,arch:Process.arch,skill_ready:!skillUnknown&&cfg.skillReady,
 ...(probeAdapter.terminalReadVersion===1?{terminal_read_version:1}:{}),
 autochess_round_start_ready:!!autoChessRoundResetMethod&&!cfg.autoChessRoundResetError,
 autochess_round_start_error:cfg.autoChessRoundResetError||null,
 coalesce_enemy_hp:coalesceEnemyHp,trace_hp_context:traceHpContext,
 source_character_uid_ready:sourceCharacterClasses!==null,source_character_uid_error:cfg.sourceCharacterUidError||null,
 skill_finish_error:cfg.skillFinishError||null,
 character_lifecycle_ready:characterLifecycleReady(),character_lifecycle_error:cfg.characterLifecycleError||null,
 skill_cast_state_ready:skillCastStateReady,skill_cast_state_error:cfg.skillCastStateError||null,
 mitigation_ready:mitigationReady,mitigation_error:cfg.mitigationError||null,
 mitigation_diagnostics:mitigationDiagnostics(),
 mitigation_no_event_ready:mitigationNoEventReady,mitigation_no_event_error:cfg.mitigationNoEventError||null,
 mitigation_factory_ready:mitigationFactoryReady,mitigation_factory_error:cfg.mitigationFactoryError||null,
 terminal_error:cfg.terminalError||null,
 enemy_entry_ready:enemyEntryReady,enemy_entry_error:cfg.enemyEntryError||null,
 enemy_phase_ready:enemyPhaseReady,enemy_phase_error:cfg.enemyPhaseError||null,
 enemy_state_ready:enemyStateReady&&enemyPhaseReady,enemy_state_error:enemyStateError,
 enemy_max_hp_ready:enemyMaxHpReady,enemy_max_hp_error:cfg.enemyMaxHpError||null,
 stage_object_finish_error:cfg.stageObjectFinishError||null});
