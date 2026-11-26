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
let nativeInitialBoundary=null;
const nativeInitialDiagnostics={attempts:0,captured:0,rejected:0,invalidated:0,last_rejection:null};
function countNativeInitial(key){nativeInitialDiagnostics[key]=Math.min(1000000,nativeInitialDiagnostics[key]+1);}
function discardNativeInitialBoundary(reason){
 if(nativeInitialBoundary){countNativeInitial('invalidated');nativeInitialBoundary=null;nativeInitialDiagnostics.last_rejection=reason;}
}
function sameNativeInitialScope(binding,boundary){
 return binding&&binding.native_process_pid===boundary.native_process_pid
  &&binding.native_generation===boundary.native_generation&&binding.native_stage_id===boundary.native_stage_id
  &&damageSettlementScope===boundary.damage_settlement_scope
  &&['controller','native','logger','stats','level'].every(key=>binding.native_identity?.[key]===boundary.native_identity[key]);
}
function captureNativeInitialBoundary(invocation){
 countNativeInitial('attempts');
 const reject=reason=>{countNativeInitial('rejected');nativeInitialDiagnostics.last_rejection=reason;};
 try{
  if(!probeHooksReady)return reject('hooks_not_ready');
  if(fatal||stopped)return reject(fatal?'fatal':'stopped');
  if(active)return reject('callbacks_active');
  const binding=nativeBinding();
  if(!binding)return reject('binding_unavailable');
  if(nativeInitialBoundary&&sameNativeInitialScope(binding,nativeInitialBoundary))return;
  if(![0,1,3].includes(invocation.nativePreviousState))return reject('not_playing_transition');
  if(!invocation.nativeStateController||binding.native_identity.controller!==invocation.nativeStateController.toString())
   return reject('controller_mismatch');
  if(invocation.nativeStateController.add(0x228).readS32()!==2)return reject('not_playing');
  if(binding.native_frame!==0||binding.native_fixed_time!=='0')return reject('not_frame_zero');
  if(sequence!==0)return reject('events_precede_initial_boundary');
  const observedEpoch=epoch,observedScope=damageSettlementScope;
  const after=nativeBinding();
  if(!after||after.native_frame!==0||after.native_fixed_time!=='0'||active||sequence!==0
     ||epoch!==observedEpoch||damageSettlementScope!==observedScope
     ||!sameNativeInitialScope(after,{...binding,damage_settlement_scope:observedScope})
     ||invocation.nativeStateController.add(0x228).readS32()!==2)
   return reject('initial_observation_changed');
  nativeInitialBoundary=Object.freeze({version:1,source:'native-pc-producer',method:'BattleController.set_state',
   phase:'leave',state:2,hooks_ready:true,native_identity:Object.freeze({...after.native_identity}),
   native_stage_id:after.native_stage_id,native_generation:after.native_generation,
   native_process_pid:after.native_process_pid,native_frame:0,native_fixed_time:'0',
   frame:0,game_time:0,sequence:0,epoch:observedEpoch,active:0,damage_settlement_scope:observedScope});
  countNativeInitial('captured');nativeInitialDiagnostics.last_rejection=null;
 }catch(e){reject('initial_observation_unreadable');}
}
function nativeInitialEnvelope(binding){
 if(fatal||stopped)discardNativeInitialBoundary(fatal?'fatal':'stopped');
 if(nativeInitialBoundary&&binding&&!sameNativeInitialScope(binding,nativeInitialBoundary))
  discardNativeInitialBoundary('battle_scope_changed');
 // This is a historical start witness, not a replacement for the live mark.
 // Later events/ACKs do not change it; prefix completeness is checked by the host.
 return {initial_boundary:binding&&nativeInitialBoundary?nativeInitialBoundary:null,
  initial_boundary_diagnostics:{...nativeInitialDiagnostics}};
}
function captureNativeTerminalBinding(identity,stamp){
 const binding=nativeBinding();
 if(!binding||binding.native_frame!==stamp.frame
    ||Number(BigInt(binding.native_fixed_time))/4294967296!==stamp.game_time
    ||['controller','native','logger','stats','level'].some(key=>binding.native_identity[key]!==identity[key]))return null;
 // This is evidence observed before destruction, not a later native snapshot.
 // Detach the AutoChess body too: its cache may be retired after OnDestroy.
 const frozen=JSON.parse(JSON.stringify(binding));
 Object.freeze(frozen.native_identity);return Object.freeze(frozen);
}
function validateNativeTerminalBinding(candidate){
 if(candidate.native_process_pid!==Process.id||candidate.native_generation!==nativeGeneration)return false;
 const keys=['controller','native','logger','stats','level'],identity=candidate.native_identity;
 if(!identity||keys.some(key=>candidate[key]!==identity[key]))return false;
 const key=[...keys.map(name=>identity[name]),candidate.native_stage_id].join(':');
 if(nativeLastBinding?.key!==key)return false;
 const frame=clock.readU32(),fixed=clock.add(4).readS64().toString();
 if(frame!==candidate.frame||frame!==candidate.native_frame||fixed!==candidate.native_fixed_time
    ||Number(BigInt(fixed))/4294967296!==candidate.game_time)return false;
 // An empty singleton after destruction is expected. An unreadable slot is
 // not an empty singleton, and a newly installed controller is never this tail.
 const parent=nativeControllerClass.add(0x58).readPointer();
 if(parent.isNull())return false;
 const statics=parent.add(0xb8).readPointer();if(statics.isNull())return false;
 const controller=statics.readPointer();
 if(!controller.isNull()){
  if(controller.toString()!==identity.controller)return false;
  // Cleared destruction references do not constitute a new live binding. If
  // a retained reference is still readable, any non-NULL replacement is a
  // conflict. Released objects are not dereferenced as new live snapshots.
  try{if(!controller.readPointer().equals(nativeControllerClass))return false;}catch(e){}
  for(const [name,offset]of [['native',0x10],['logger',0xe0],['level',0x160]]){
   try{const root=controller.add(offset).readPointer();if(!root.isNull()&&root.toString()!==identity[name])return false;}
   catch(e){}
  }
  try{const stats=ptr(identity.logger).add(0x30).readPointer();
   if(!stats.isNull()&&stats.toString()!==identity.stats)return false;}catch(e){}
 }
 // A successfully read replacement stage is a conflict, not a missing view.
 // The frozen stage remains historical evidence when its object was released.
 try{const stage=ptr(identity.level).add(0x18).readPointer();
  if(!stage.isNull()&&str(stage)!==candidate.native_stage_id)return false;}catch(e){}
 return clock.readU32()===frame&&clock.add(4).readS64().toString()===fixed
  &&statics.readPointer().equals(controller)&&candidate.native_generation===nativeGeneration;
}
let nativeCaptureMetadata=null;
function prepareNativeCaptureMetadata(meta){
 if(globalThis.arkdpsNativeAsyncCapture!==true)return;
 const fields=meta.found.SkillData.add(0x80).readPointer(),count=meta.found.SkillData.add(0x124).readU16();
 const fieldType=api('il2cpp_field_get_type','pointer',['pointer']),classFrom=api('il2cpp_class_from_type','pointer',['pointer']);
 let stringClass=null;
 for(let i=0;i<count;i++){
  const field=fields.add(i*32);
  if(field.readPointer().readCString()==='prefabKey')stringClass=classFrom(fieldType(field));
 }
 if(!stringClass||stringClass.isNull()||className(stringClass)!=='String'||stringClass.add(0x18).readPointer().readCString()!=='System')
  throw new Error('capture String metadata unavailable');
 nativeCaptureMetadata={clock:meta.clock,string_class:stringClass.toString(),skill_offsets:{...meta.skillF},
  skill_wait:meta.skillCastStateMethods?.wait??null,attack_type_offset:meta.mitigationMethods?.damageType??null,
  factory_addresses:meta.mitigationMethods?.factories?.map(x=>x.toString())??[],
  classes:Object.fromEntries(Object.entries(meta.found).filter(([,v])=>v&&!v.isNull()).map(([k,v])=>[k,v.toString()]))};
}
function prepareNativeCaptureHook(address,callbacks){
 if(globalThis.arkdpsNativeAsyncCapture!==true)return;
 if(!nativeCaptureMetadata||typeof callbacks.arkdpsName!=='string')throw new Error('native hook capture metadata/name missing');
 callbacks.arkdpsCaptureContract=nativeCapturePlan(callbacks.arkdpsName,address,nativeCaptureMetadata);
}
const probeAdapter={
 prepareCaptureMetadata:prepareNativeCaptureMetadata,prepareCaptureHook:prepareNativeCaptureHook,
 moduleName:'GameAssembly.dll',imageSizeType:'size_t',requiredClasses:['LevelData'],allowNullClassRows:true,
 controllerFields:{s_fixedFrameCnt:0x14,s_fixedPlayTime:0x18,m_logger:0xe0,m_levelData:0x160,m_state:0x228},
 validateClasses(found){expectFields(found.LevelData,{levelId:0x18});nativeControllerClass=found.BattleController;},
 validateTerminalController(){},terminalRequired:true,loggerOffset:0xe0,levelOffset:0x160,
 clockRegressed(previous,frame,game_time){return frame<previous.frame||game_time<previous.game_time;},
 onClockRegression(){discardNativeInitialBoundary('clock_regressed');nativeGeneration++;nativeLastBinding=null;},
 // Windows x64 puts the large return buffer in RCX and shifts explicit arguments.
 factoryArgument(args,index){return args[index+1];},
 factoryStorage(args){return args[0];},
 scopedModifierTrace:true,directHealingProof:true,
 terminalReadVersion:1,captureTerminalBinding:captureNativeTerminalBinding,
 validateTerminalBinding:validateNativeTerminalBinding,
 supportsAutoChessAllDefense:true,
 setAutoChessMode,
 eventCollectionOwner:autoEventOwner,
 stampClock(when){stampAutoChessClock(when);},
 baselineEnvelope(fields,records,acknowledged,through){return discardedAutoChessSkills(fields,records,acknowledged,through);},
 prepareEventEnvelope(){return prepareAutoChessRetryBody(typeof __nativeActive==='function');},
 pendingMarkCallbacks(){return typeof __nativeActive==='function'?__nativeActive():0;},
 markEntryCallbacks(){return typeof __arkdpsMarkEntryActive==='function'?__arkdpsMarkEntryActive():this.pendingMarkCallbacks();},
 readEnvelope(prepared){
  const binding=nativeBinding(prepared);
  return {fields:{scoped_trace_count:scopedTraceCount,scoped_trace_dropped:scopedTraceDropped,
   ...binding,...nativeInitialEnvelope(binding),retry:binding===null,
   ...(!binding&&nativeAutoBindingDiagnostic?{autochess_binding_diagnostic:nativeAutoBindingDiagnostic}:{})},ready:binding!==null};
 },
 markEnvelope(current,prepared){
  const binding=nativeBinding(prepared);
  return {fields:{...binding,...nativeInitialEnvelope(binding),
   ...(!binding&&nativeAutoBindingDiagnostic?{autochess_binding_diagnostic:nativeAutoBindingDiagnostic}:{})},ready:!(binding===null||current&&binding.native_frame!==current.frame
   ||current&&Number(BigInt(binding.native_fixed_time))/4294967296!==current.game_time)};
 },
 enterTerminalStateHook(invocation,args){
  beginEnemyHook('BattleController.set_state');invocation.nativeStateHook=true;
  try{invocation.nativePreviousState=args[0].add(0x228).readS32();}catch(e){invocation.nativePreviousState=null;}
 },
 observeTerminalState(args,state,identity){
  if(state===1)discardNativeInitialBoundary('battle_initializing');
  const current=currentNativeIdentity();
  if(state===2&&args[0].add(0x228).readS32()!==2&&identity&&current&&identity.controller===current.controller){
   discardNativeInitialBoundary('battle_started');
   nativeGeneration++;nativeLastBinding=null;resetDamageSettlementScope();
  }
 },
 leaveTerminalStateHook(invocation){
  if(invocation.nativeStateHook)endEnemyHook('BattleController.set_state');
  if(invocation.nativeStateValue===2)captureNativeInitialBoundary(invocation);
 },
 discardInitialBoundary:discardNativeInitialBoundary,
 terminalInstallFailed(error){throw new Error('native battle transition observation failed: '+String(error));}
};
// Capture plans are compiled before native hooks enable. No live values are
// inferred here: pointers below identify read roots, never ownership or amounts.
function nativeCapturePlan(name,address,metadata){
 const normalized=name.replace(/^hp\./,'').replace(/^enemy entry /,'Enemy.');
 const fields=[],globals=[metadata.clock.toString()];let next=1;
 const add=(phase,role,root,index,path,offset,bytes,extra={})=>{
  const field={field_id:next++,phase,role,root,root_index:index,pointer_offsets:[...path],offset,bytes,kind:'bytes',guard_id:0,guard_side:'none',required:true,...extra};
  fields.push(field);return field.field_id;
 };
 const clock=phase=>{
  add(phase,'clock_open','global_slot',0,[],0,12,{guard_id:1,guard_side:'before'});
  add(phase,'clock_close','global_slot',0,[],0,12,{guard_id:1,guard_side:'after'});
 };
 const entity=(phase,role,index,path=[],optional=false)=>{
  const root=phase==='entry'?'arg':'entry_ref',required=!optional;
  add(phase,role,root,index,path,0,8,{required});
  add(phase,role,root,index,path,0x18,4,{required});
  add(phase,role,root,index,path,0x25,2,{required});
  add(phase,role,root,index,path,0x40,8,{required});
  add(phase,role,root,index,[...path,0x148],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200,required});
  // Data UID is a distinct optional domain: foreign non-Characters must not
  // become attributed merely because this address happens to be readable.
  add(phase,role+'.data',root,index,[...path,0x538],0,8,{required:false});
  add(phase,role+'.data',root,index,[...path,0x538],0x6c,4,{required:false});
  for(let depth=0;depth<10;depth++)
   add(phase,role+'.lineage',root,index,[...path,0,...Array(depth).fill(0x58)],0x58,8,{required:false});
 };
 const modifier=(phase,index=1)=>{
  const root=phase==='entry'?'arg':'entry_ref';
  // Distinct exact regions avoid copying unrelated padding on hot paths.
  for(const [offset,bytes] of [[0,8],[0x10,8],[0x30,8],[0x48,8],[0x60,4],[0x68,4],[0x70,4],[0x80,16]])
   add(phase,'modifier',root,index,[],offset,bytes);
  entity(phase,'source',index,[0],true);entity(phase,'target',index,[0x10],true);
 };
 const attr=(phase,index,entryAll=false)=>{
  const root=phase==='entry'?'arg':'entry_ref',path=[0xb0,0x68];
  add(phase,'attributes.count',root,index,path,0x18,4,{required:false});
  for(const slot of entryAll?[0,1,2,3,6,7]:[0])
   add(phase,'attributes.slot'+slot,root,index,path,0x20+slot*0x28,0x14,{required:false});
 };
 const skill=(phase,index,path=[])=>{
  const root=phase==='entry'?'arg':'entry_ref',f=metadata.skill_offsets;
  add(phase,'skill',root,index,path,0,8);
  add(phase,'skill.owner',root,index,path,f.owner,8);
  add(phase,'skill.owner.current',root,index,[...path,f.owner],f.charSkill,8);
  entity(phase,'skill.actor',index,[...path,f.owner],true);
  add(phase,'skill.data',root,index,[...path,f.data],0,8);
  for(const field of ['prefab','id','name'])add(phase,'skill.'+field,root,index,[...path,f.data,f[field]],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200});
  if(metadata.skill_wait!==null)add(phase,'skill.wait',root,index,path,metadata.skill_wait,1);
 };
 let profile;
 if(['Entity.ApplyModifier','Entity.OnAfterOutputDamage','Unit.OnAfterOutputDamage','Unit.OnTakeDamage','Entity.OnTakeDamage'].includes(normalized)){
  profile='modifier';for(const phase of ['entry','leave']){clock(phase);modifier(phase);entity(phase,'receiver',0,[],true);}
  // The parent pointers come from native TLS, never the delayed JS stack.
  for(const [offset,bytes] of [[0,8],[0x10,8],[0x30,8],[0x48,8],[0x60,4],[0x68,4],[0x70,4]])add('checkpoint','parent.modifier','parent_ref',1,[],offset,bytes,{required:false});
  add('checkpoint','parent.hp','parent_ref',1,[0x10],0x40,8,{required:false});
 }else if(['Entity.SetHpInternal','Entity.SetHpInternalDirectly','Entity.OnAttributeDirty'].includes(normalized)){
  profile='hp';for(const phase of ['entry','leave']){clock(phase);entity(phase,'target',0);}
  attr('leave',0); // Native profile filters non-enemy/unborn/index!=0 and no-change before optional scope/MaxHP reads.
 }else if(['Enemy.Born','Enemy._UpdatePlayerSideAndMapLayer','Enemy.DoFakeDeath','Enemy.DoReborn','Enemy.OnFinish','Entity.OnFinish','BObject.OnRecycle','Character.Born'].includes(normalized)){
  profile='lifecycle';for(const phase of ['entry','leave']){clock(phase);entity(phase,'actor',0);add(phase,'actor.tile',phase==='entry'?'arg':'entry_ref',0,[],0x360,8,{required:false});add(phase,'actor.finish_reason',phase==='entry'?'arg':'entry_ref',0,[],0x158,4,{required:false});}
  if(normalized==='Enemy.Born'||normalized==='Enemy._UpdatePlayerSideAndMapLayer')attr('leave',0,true);
 }else if(normalized==='BasicSkill.OnCastFinish'){
  profile='skill_cast';for(const phase of ['entry','leave']){clock(phase);skill(phase,0);}
 }else if(normalized.startsWith('Character.')||normalized==='BattleController.OnCharacterFinished'){
  profile='skill_transition';const actor=normalized==='BattleController.OnCharacterFinished'?1:0;
  for(const phase of ['entry','leave']){clock(phase);entity(phase,'actor',actor);skill(phase,actor,[metadata.skill_offsets.charSkill]);if(normalized==='Character.OnAfterSkill')skill(phase,1);}
 }else if(normalized==='Buff.OnTakeDamage'){
  profile='buff';for(const phase of ['entry','leave']){clock(phase);add(phase,'buff',phase==='entry'?'arg':'entry_ref',0,[],0x18,8);add(phase,'buff.shield',phase==='entry'?'arg':'entry_ref',0,[],0x27c,1);add(phase,'buff.key',phase==='entry'?'arg':'entry_ref',0,[0x208],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200});}
 }else if(normalized==='BlockDamage._AssignDynamicVar'){
  profile='shield';for(const phase of ['entry','leave']){
   clock(phase);modifier(phase,2);const root=phase==='entry'?'arg':'entry_ref';
   const count=add(phase,'blackboard.count',root,1,[],0x18,4,{guard_id:2,guard_side:'before'});
   add(phase,'blackboard.entries',root,1,[0x10],0x20,0,{kind:'counted_bytes',count_field_id:count,element_bytes:24,max_count:128,guard_id:2,guard_side:'member'});
   add(phase,'blackboard.close',root,1,[],0x18,4,{guard_id:2,guard_side:'after'});
  }
 }else if(normalized.startsWith('BattleFormula.')||normalized==='AttackInfo.GetFinalAtk'||normalized==='Modifier.NewDamageModifierAfterCalculate'){
  profile='formula';for(const phase of ['entry','leave']){clock(phase);
   const root=phase==='entry'?'arg':'entry_ref';
   if(normalized==='AttackInfo.GetFinalAtk')add(phase,'attack.type',root,0,[],metadata.attack_type_offset,4);
   else if(normalized==='BattleFormula.CalculateDamageInternal')add(phase,'attack.type',root,0,[],metadata.attack_type_offset,4);
   else if(normalized==='Modifier.NewDamageModifierAfterCalculate'){
    const overload=metadata.factory_addresses.indexOf(address.toString());if(overload<0)throw new Error('unverified factory address');
    if(phase==='leave')modifier(phase,0);
    const index=overload+1;entity(phase,'factory.source',index+3,[],true);entity(phase,'factory.target',index+4);
   }
   else{entity(phase,'target',0);entity(phase,'source',1,[],true);add(phase,'attack.type',root,normalized.endsWith('NoEvent')?2:4,[],metadata.attack_type_offset,4);}
  }
 }else if(['BattleController.set_state','BattleController.Destroy','BattleController.ClearRunTimeData'].includes(normalized)){
  profile='boundary';for(const phase of ['entry','leave']){clock(phase);
   const root=phase==='entry'?'arg':'entry_ref';
   for(const [role,path,offset,bytes] of [['controller',[],0,8],['controller.state',[],0x228,4],['native',[],0x10,8],['native.owner',[0x10],0x28,8],['logger',[],0xe0,8],['logger.owner',[0xe0],0x18,8],['stats',[0xe0],0x30,8],['level',[],0x160,8]])add(phase,role,root,0,path,offset,bytes);
  }
 }else throw new Error('unplanned native hook '+name);
 let argMask=1,argCount=1;
 const argumentsFor=(count,mask=(1<<count)-1)=>{argCount=count;argMask=mask;};
 if(profile==='modifier')argumentsFor(2);
 else if(normalized==='Entity.OnAttributeDirty')argumentsFor(3);
 else if(['Enemy.OnFinish','Entity.OnFinish','BattleController.set_state'].includes(normalized))argumentsFor(2);
 else if(profile==='skill_cast')argumentsFor(3,5);
 else if(normalized==='Character.OnAfterSkill')argumentsFor(3);
 else if(normalized==='BattleController.OnCharacterFinished')argumentsFor(3,6);
 else if(profile==='shield')argumentsFor(5,22);
 else if(normalized==='BattleFormula.CalculateDamage')argumentsFor(5);
 else if(normalized==='BattleFormula.CalculateDamageNoEvent')argumentsFor(3);
 else if(normalized==='BattleFormula.CalculateDamageInternal')argumentsFor(4,9);
 else if(normalized==='Modifier.NewDamageModifierAfterCalculate'){
  const overload=metadata.factory_addresses.indexOf(address.toString());
  if(overload<0)throw new Error('unverified factory overload address');argumentsFor(overload+8);
 }
 return {schema_version:1,complete:false,name:normalized,address:address.toString(),abi:'win64-gp',profile,arg_count:argCount,arg_mask:argMask,
  globals,fields,max_bytes:65536,max_reads:4096,
  metadata:{classes:metadata.classes,skill_offsets:metadata.skill_offsets,attack_type_offset:metadata.attack_type_offset},
  // These producer checkpoints are mandatory profile operations. Engine must
  // reject installation until it can capture them, never ignore or sync-fallback.
  required_projections:['live_scope_guards','same_point_class_ancestry',...(profile==='modifier'?['open_parent_checkpoint','token_host_and_character_form']:[]),...(profile==='boundary'?['full_native_binding']:[]),...(profile==='shield'?['current_buff_and_blackboard_keys']:[])]};
}
