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
 // Closed-record projection runs after the callback's body wrapper has
 // returned. Terminal root witnesses belong to this captured body, while the
 // clock and optional stage text keep their own independent roles below.
 if(typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()){
  if(!capturedNativeTerminalRecord(__arkdpsReplayRecord()))return false;
  return __arkdpsReplayPoint('body',()=>validateNativeTerminalBindingPoint(candidate));
 }
 return validateNativeTerminalBindingPoint(candidate);
}
function validateNativeTerminalBindingPoint(candidate){
 if(candidate.native_process_pid!==Process.id||candidate.native_generation!==nativeGeneration)return false;
 const keys=['controller','native','logger','stats','level'],identity=candidate.native_identity;
 if(!identity||keys.some(key=>candidate[key]!==identity[key]))return false;
 const key=[...keys.map(name=>identity[name]),candidate.native_stage_id].join(':');
 if(nativeLastBinding?.key!==key)return false;
 const frame=captureClockFrame(),fixed=captureClockFixed().toString();
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
  if(!stage.isNull()){
   const value=typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()?__arkdpsReplayOptional('terminal.stage_text',()=>str(stage)):{available:true,value:str(stage)};
   if(value.available&&value.value!==candidate.native_stage_id)return false;
  }}catch(e){}
 return captureClockFrame()===frame&&captureClockFixed().toString()===fixed
  &&statics.readPointer().equals(controller)&&candidate.native_generation===nativeGeneration;
}
let nativeCaptureMetadata=null,nativeCaptureModeEpoch=0;
const nativeCaptureModeHistory=new Map();
const nativeTerminalReplayRecords=new WeakMap();
function capturedNativeTerminalRecord(record){
 return typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()&&record===__arkdpsReplayRecord()
  &&record.edge==='leave'&&typeof __arkdpsReplayModeRecord==='function'
  &&nativeTerminalReplayRecords.get(record)===__arkdpsReplayModeRecord();
}
let nativeActiveHpContract=null;
function captureNativeHpObservation(edge,enemy){
 if(typeof __arkdpsReplayActive!=='function'||!__arkdpsReplayActive()||__arkdpsReplayRecord().native_view!==true)return null;
 if(typeof __arkdpsOwnedHpObservation!=='function')return null;
 if(!nativeActiveHpContract||__arkdpsReplayRecord().edge!==edge)__arkdpsReplayGap('HP observation contract unavailable');
 return __arkdpsOwnedHpObservation(nativeActiveHpContract,enemy);
}
function prepareNativeCaptureMetadata(meta){
 if(globalThis.arkdpsNativeAsyncCapture!==true)return;
 nativeCaptureModeHistory.set(0,nativeAutoDesiredMode);
 const fields=meta.found.SkillData.add(0x80).readPointer(),count=meta.found.SkillData.add(0x124).readU16();
 const fieldType=api('il2cpp_field_get_type','pointer',['pointer']),classFrom=api('il2cpp_class_from_type','pointer',['pointer']);
 let stringClass=null;
 for(let i=0;i<count;i++){
  const field=fields.add(i*32);
  if(field.readPointer().readCString()==='prefabKey')stringClass=classFrom(fieldType(field));
 }
 if(!stringClass||stringClass.isNull()||className(stringClass)!=='String'||stringClass.add(0x18).readPointer().readCString()!=='System')
  throw new Error('capture String metadata unavailable');
 nativeCaptureMetadata={clock:meta.clock,string_class:stringClass.toString(),coalesce_enemy_hp:coalesceEnemyHp,collect_enemy_hp_changes:collectEnemyHpChanges,skill_offsets:{...meta.skillF},
  skill_wait:meta.skillCastStateMethods?.wait??null,attack_type_offset:meta.mitigationMethods?.damageType??null,
  factory_addresses:meta.mitigationMethods?.factories?.map(x=>x.toString())??[],
  classes:Object.fromEntries(Object.entries(meta.found).filter(([,v])=>v&&!v.isNull()).map(([k,v])=>[k,v.toString()]))};
 nativeCaptureMetadata.hp_non_enemy_classes=[];
 const enemyClass=meta.found.Enemy;
 if(enemyClass&&!enemyClass.isNull()){
  const assignable=api('il2cpp_class_is_assignable_from','bool',['pointer','pointer']);
  for(const name of ['BObject','Entity','Unit','Character']){
   const candidate=meta.found[name];if(!candidate||candidate.isNull()||candidate.equals(enemyClass))continue;
   try{if(assignable(enemyClass,candidate)===false&&!nativeCaptureMetadata.hp_non_enemy_classes.includes(candidate.toString()))
    nativeCaptureMetadata.hp_non_enemy_classes.push(candidate.toString());}catch(error){/* No result is never exclusion evidence. */}
  }
 }
 Object.freeze(nativeCaptureMetadata.hp_non_enemy_classes);
 if(typeof __arkdpsPrepareColdMetadata==='function'){
  const extra=__arkdpsPrepareColdMetadata({...meta,native_capture:nativeCaptureMetadata});
  if(extra!==undefined){if(!extra||typeof extra!=='object')throw new Error('invalid cold metadata');Object.assign(nativeCaptureMetadata,extra);}
 }
 nativeCaptureMetadata.immutable_class_parents=typeof __arkdpsColdMetadataRead==='function';
 if(typeof __arkdpsBuildColdCapturePlan==='function')nativeCaptureMetadata.cold_plan=__arkdpsBuildColdCapturePlan(nativeCaptureMetadata);
 // Reuse only snapshots made and recursively authenticated by this owner.
 // Mutable setup objects are copied, never cached or frozen in place.
 for(const key of ['cold_plan','classes','skill_offsets','hp_non_enemy_classes'])
  if(nativeCaptureMetadata[key]!==undefined)nativeCaptureMetadata[key]=sealedCaptureContract(nativeCaptureMetadata[key]);
 const sampling=nativeSamplingCapturePlan(nativeCaptureMetadata,
  typeof __nativeCaptureCapabilities==='function'?__nativeCaptureCapabilities():null);
 const previous=globalThis.__arkdpsSamplingCapturePlan;
 if(previous!==undefined&&JSON.stringify(previous)!==JSON.stringify(sampling))
  throw new Error('native sampling capture plan changed during setup');
 if(previous===undefined)Object.defineProperty(globalThis,'__arkdpsSamplingCapturePlan',{
  value:sealedCaptureContract(sampling),writable:false,configurable:false});
}
function nativeSamplingCapturePlan(metadata,capabilities){
 // A worker observation point, never a method address or a game invocation.
 // Clock witnesses bracket the same scope/publication/cold capture machinery.
 const fields=['clock_open','clock_close'].map((name,index)=>({field_id:index+1,
  phase:'checkpoint',name,role:name,root:'global_slot',root_index:0,pointer_offsets:[],offset:0,
  bytes:12,kind:'bytes',guard_id:1,guard_side:index?'after':'before',required:true}));
 return finalizeNativeCapturePlan({schema_version:1,complete:false,raw_collection:'all',
  name:'sampling checkpoint',address:'0x0',abi:'win64-gp',profile:'sampling_checkpoint',
  arg_count:0,arg_mask:0,globals:[metadata.clock.toString()],fields,max_bytes:65536,max_reads:4096,
  cold_plan:metadata.cold_plan,required_projections:['live_scope_guards','same_point_class_ancestry','full_native_binding']},
  metadata,capabilities);
}
const nativeCaptureColdBindings=new WeakMap();
let nativeCaptureColdRebuilding=false;
let nativeCaptureScopeRevision=0;
const nativeCaptureScopes=new Map();
function publishNativeCaptureScope(){
 if(globalThis.arkdpsNativeAsyncCapture!==true)return;
 if(typeof __publishCaptureScope!=='function')throw new Error('native scope publisher unavailable');
 const revision=++nativeCaptureScopeRevision;
 if(!Number.isSafeInteger(revision))throw new Error('native capture scope revision exhausted');
 const graph=nativeCaptureScopeGraph(nativeAutoCache,nativeAutoStage,clock,revision);
 if(nativeCaptureScopes.size>=256)throw new Error('capture scope history budget exceeded');nativeCaptureScopes.set(revision,graph);
 __publishCaptureScope(graph);
}
function captureModeState(){return {version:1,mode_epoch:nativeCaptureModeEpoch,desired_mode:nativeCaptureModeHistory.get(nativeCaptureModeEpoch)??nativeAutoDesiredMode,effective_mode:nativeAutoEffective?.mode??null};}
function installCaptureMode(enabled,control){
 if(typeof enabled!=='boolean'||__arkdpsReplayActive())throw new Error('capture mode control during replay');
 if(!control||control.version!==1||!Number.isSafeInteger(control.mode_epoch)||control.mode_epoch!==nativeCaptureModeEpoch+1)throw new Error('invalid capture mode epoch');
 if(nativeCaptureModeHistory.size>=256)throw new Error('capture mode history budget exceeded');
 nativeCaptureModeHistory.set(control.mode_epoch,enabled?'all':'local');
 nativeCaptureModeEpoch=control.mode_epoch;nativeAutoDesiredMode=nativeCaptureModeHistory.get(nativeCaptureModeEpoch);
 publishNativeCaptureScope();const previous=[...nativeCapturePublications.values()].at(-1);if(previous)publishNativeCapturePublication(previous.binding);
 // A native invocation pins its entry epoch through its leave. A mode command
 // only installs immutable history; native producer swaps epoch after return.
 return captureModeState();
}
function retireCaptureGraphs(bounds){
 if(__arkdpsReplayActive()||!bounds||typeof bounds!=='object')throw new Error('invalid capture retirement');
 for(const [key,map,current]of [['scope_before',nativeCaptureScopes,nativeCaptureScopeRevision],['publication_before',nativeCapturePublications,nativeCapturePublicationRevision],['mode_before',nativeCaptureModeHistory,nativeCaptureModeEpoch]]){
  const bound=bounds[key];if(bound===undefined)continue;
  if(!Number.isSafeInteger(bound)||bound<0||bound>current)throw new Error('invalid capture retirement bound');
  for(const revision of map.keys())if(revision<bound)map.delete(revision);
 }
}
function withCapturedMode(record,body){
 if(!__arkdpsReplayActive())return body();
 const epoch=record.facts?.mode_epoch;
 if(!Number.isSafeInteger(epoch)||!nativeCaptureModeHistory.has(epoch))__arkdpsReplayGap('captured mode epoch unavailable');
 nativeAutoDesiredMode=nativeCaptureModeHistory.get(epoch);
 try{return body();}finally{nativeAutoDesiredMode=nativeCaptureModeHistory.get(nativeCaptureModeEpoch);}
}
function capturedCollectionMode(read){
 const record=__arkdpsReplayRecord(),epoch=record.facts?.scope_mode_epoch;
 if(!Number.isSafeInteger(epoch)||!nativeCaptureModeHistory.has(epoch))__arkdpsReplayGap('captured scope mode epoch unavailable');
 return read(nativeCaptureModeHistory.get(epoch));
}
function captureNativeInvocationRead(scope,read){
 if(typeof __arkdpsReplayActive!=='function'||!__arkdpsReplayActive())return read();
 const record=__arkdpsReplayRecord(),invocation=scope?.nativeCaptureInvocation;
 if(typeof invocation!=='string'||typeof record.invocation_id!=='string')__arkdpsReplayGap('missing native scope invocation');
 if(scope.tid!==record.thread_id)throw new Error('cross-thread parent checkpoint');
 if(invocation===record.invocation_id)return __arkdpsReplayCheckpoint('body',record.checkpoint??0,read);
 const matches=(record.facts?.parent_checkpoints??[]).filter(point=>point.invocation_id===invocation);
 if(matches.length!==1||!Number.isSafeInteger(matches[0].checkpoint))__arkdpsReplayGap('missing exact parent checkpoint');
 return __arkdpsReplayCheckpoint('parent.body',matches[0].checkpoint,read);
}
const nativeContractSnapshots=new WeakSet();
function sealedCaptureContract(value){
 const path=new Set();
 function copy(item){
  if(item===null||item===undefined||typeof item==='string'||typeof item==='boolean')return item;
  if(typeof item==='number'){if(!Number.isFinite(item))throw new Error('non-JSON capture contract');return item;}
  if(typeof item!=='object')throw new Error('non-JSON capture contract');
  if(nativeContractSnapshots.has(item))return item;
  if(path.has(item))throw new Error('cyclic capture contract');
  const array=Array.isArray(item),prototype=Object.getPrototypeOf(item);
  if(!array&&prototype!==null&&prototype.constructor?.name!=='Object')throw new Error('non-JSON capture contract');
  path.add(item);const result=array?new Array(item.length):{};
  for(const key of Object.keys(item)){
   const property=Object.getOwnPropertyDescriptor(item,key);
   if(!property||!Object.prototype.hasOwnProperty.call(property,'value'))throw new Error('non-JSON capture contract');
   Object.defineProperty(result,key,{value:copy(property.value),writable:true,enumerable:true,configurable:true});
  }
  path.delete(item);Object.freeze(result);nativeContractSnapshots.add(result);return result;
 }
 return copy(value);
}
function prepareNativeCaptureHook(address,callbacks){
 if(globalThis.arkdpsNativeAsyncCapture!==true)return;
 if(!nativeCaptureMetadata||typeof callbacks.arkdpsName!=='string')throw new Error('native hook capture metadata/name missing');
 const plan=nativeCapturePlan(callbacks.arkdpsName,address,nativeCaptureMetadata);
 const capabilities=typeof __nativeCaptureCapabilities==='function'?__nativeCaptureCapabilities():null;
 callbacks.arkdpsCaptureContract=sealedCaptureContract({...finalizeNativeCapturePlan(plan,nativeCaptureMetadata,capabilities),mode_epoch:nativeCaptureModeEpoch});
 const directContract=callbacks.arkdpsCaptureContract;
 const direct=plan.profile==='hp'&&plan.name==='Entity.SetHpInternal'&&typeof __arkdpsIsPreparedHpObservation==='function';
 for(const member of ['arkdpsDirectHpEntry','arkdpsDirectHpLeave']){
  const stateCallback=callbacks[member];
  if(!direct||typeof stateCallback!=='function'){delete callbacks[member];continue;}
  // The bridge has set body/current checkpoint/auto before this controlled
  // entry. Only an actual native-issued packet for this contract may enter.
  callbacks[member]=member==='arkdpsDirectHpEntry'?function(enemy,issuedIo){
   const modeRecord=typeof __arkdpsReplayModeRecord==='function'?__arkdpsReplayModeRecord():__arkdpsReplayRecord();
   return withCapturedMode(modeRecord,()=>{
    if(modeRecord.edge!=='entry'||__arkdpsIsPreparedHpObservation(issuedIo,directContract,enemy)!==true)__arkdpsReplayGap('unverified prepared HP observation');
    return stateCallback.call(this,enemy,issuedIo);
   });
  }:function(issuedIo){
   const modeRecord=typeof __arkdpsReplayModeRecord==='function'?__arkdpsReplayModeRecord():__arkdpsReplayRecord();
   return withCapturedMode(modeRecord,()=>{
    if(modeRecord.edge!=='leave'||__arkdpsIsPreparedHpObservation(issuedIo,directContract)!==true)__arkdpsReplayGap('unverified prepared HP observation');
    return stateCallback.call(this,issuedIo);
   });
  };
 }

 for(const edge of ['onEnter','onLeave'])if(typeof callbacks[edge]==='function'){
  const callback=callbacks[edge],contract=callbacks.arkdpsCaptureContract;
  callbacks[edge]=contract.profile==='hp'?function(...args){
   const previous=nativeActiveHpContract;nativeActiveHpContract=contract;
   try{return withCapturedMode(__arkdpsReplayActive()?__arkdpsReplayRecord():null,()=>__arkdpsReplayPoint('body',()=>callback.apply(this,args)));}
   finally{nativeActiveHpContract=previous;}
  }:function(...args){
   if(edge==='onLeave'&&contract.name==='BattleController.Destroy'&&contract.complete===true&&__arkdpsReplayActive()){
    const record=__arkdpsReplayRecord();
    if(record.edge==='leave'&&typeof __arkdpsReplayModeRecord==='function')
     nativeTerminalReplayRecords.set(record,__arkdpsReplayModeRecord());
   }
   return withCapturedMode(__arkdpsReplayActive()?__arkdpsReplayRecord():null,()=>__arkdpsReplayPoint('body',()=>callback.apply(this,args)));
  };
 }

}
const probeAdapter={
 capturedTerminalRecord:capturedNativeTerminalRecord,
 verifiedSamplingCheckpoint(record){
  return typeof __arkdpsVerifiedSamplingCheckpoint==='function'&&__arkdpsVerifiedSamplingCheckpoint(record)===true;
 },
 prepareCaptureMetadata:prepareNativeCaptureMetadata,prepareCaptureHook:prepareNativeCaptureHook,publishCaptureScope:publishNativeCaptureScope,publishCapturePublication:publishNativeCapturePublication,capturedPublicationBinding:nativeCapturedPublicationBinding,
 captureCharacterFormPoint:capturedCharacterFormPoint,captureTokenHostEvidence:capturedTokenHostEvidence,
 captureRootRead(side,read){
  if(typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive()&&__arkdpsReplayRecord().facts?.cold_revision!==undefined)
   return __arkdpsReplayWitness('cold.root_'+side,read);
  return read();
 },
 captureBindingRead(read,options={}){
  if(typeof __arkdpsReplayActive!=='function'||!__arkdpsReplayActive())return read();
  const record=__arkdpsReplayRecord();
  if(record.facts?.cold_revision!==undefined){
   const captured=prepareCapturedColdBinding(record);if(!captured)return null;
   const binding=JSON.parse(JSON.stringify(captured));
   if(options.captureRoundStart!==true){delete binding.lifecycle_baseline;delete binding.live_character_collection_empty;delete binding.live_character_data_uids;}
   return binding;
  }
  return nativeCapturedPublicationBinding(true);
 },
 captureValidatedClockSample,captureHpObservation:captureNativeHpObservation,captureScopePoint:capturedNativeScopePoint,captureModeState,installCaptureMode,retireCaptureGraphs,captureRecordMode:withCapturedMode,captureCollectionMode:capturedCollectionMode,captureInvocationRead:captureNativeInvocationRead,
 captureClassIdentity(klass,name,namespace){
  if(typeof __arkdpsColdClassIdentity!=='function')__arkdpsReplayGap('immutable class identity not frozen');
  return __arkdpsColdClassIdentity(klass,name,namespace);
 },
 captureClassLineage(klass){
  if(typeof __arkdpsColdClassLineage!=='function')__arkdpsReplayGap('immutable class lineage not frozen');
  return __arkdpsColdClassLineage(klass);
 },
 captureImmutableMetadata(address,size){
  if(typeof __arkdpsColdMetadataRead==='function')return __arkdpsColdMetadataRead(address,size);
  if(typeof __arkdpsReplayActive==='function'&&__arkdpsReplayActive())__arkdpsReplayGap('immutable metadata not frozen');
  return address.readByteArray(size);
 },
 captureRead(role,read){return typeof __arkdpsReplayWitness==='function'?__arkdpsReplayWitness(role,read):read();},
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
  const field={field_id:next++,phase,role:extra.guard_id?role:(phase==='checkpoint'?'parent.body':'body'),name:role,root,root_index:index,pointer_offsets:[...path],offset,bytes,kind:'bytes',guard_id:0,guard_side:'none',required:true,...extra};
  fields.push(field);return field.field_id;
 };
 const clock=phase=>{
  add(phase,'clock_open','global_slot',0,[],0,12,{guard_id:1,guard_side:'before'});
  add(phase,'clock_close','global_slot',0,[],0,12,{guard_id:1,guard_side:'after'});
 };
 const entity=(phase,role,index,path=[],optional=false,rootOverride=null,dataProof=true)=>{
  const root=rootOverride??(phase==='entry'?'arg':'entry_ref'),required=!optional;
  add(phase,role,root,index,path,0,8,{required});
  add(phase,role,root,index,path,0x18,4,{required});
  add(phase,role,root,index,path,0x25,2,{required});
  add(phase,role,root,index,path,0x40,8,{required});
  add(phase,role,root,index,[...path,0x148],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200,required});
  // Data UID is a distinct optional domain: foreign non-Characters must not
  // become attributed merely because this address happens to be readable.
  if(dataProof){
  add(phase,role+'.data',root,index,[...path,0x538],0,8,{required:false});
  add(phase,role+'.data',root,index,[...path,0x538],0x6c,4,{required:false});
  // Token/form fields are copied independently of actor attribution; only the
  // existing reducer may validate their same-scope membership and host proof.
  add(phase,role+'.data.flags',root,index,[...path,0x538],0x90,2,{required:false});
  add(phase,role+'.data.host_uid',root,index,[...path,0x538],0xa0,4,{required:false});
  add(phase,role+'.data.cid_pointer',root,index,[...path,0x538],0x50,8,{required:false});
  add(phase,role+'.data.host_pointer',root,index,[...path,0x538],0x98,8,{required:false});
  add(phase,role+'.data.cid',root,index,[...path,0x538,0x50],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200,required:false});
  add(phase,role+'.data.host',root,index,[...path,0x538,0x98],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200,required:false});
  add(phase,role+'.player_side',root,index,path,0x20,4,{required:false});
  }
  if(!metadata.immutable_class_parents)for(let depth=0;depth<10;depth++)
   add(phase,role+'.lineage',root,index,[...path,0,...Array(depth).fill(0x58)],0x58,8,{required:false});
 };
 const modifier=(phase,index=1)=>{
  const root=phase==='entry'?'arg':'entry_ref';
  // Distinct exact regions avoid copying unrelated padding on hot paths.
  for(const [offset,bytes] of [[0,8],[0x10,8],[0x30,8],[0x48,8],[0x60,4],[0x68,4],[0x70,4],[0x80,16]])
   add(phase,'modifier',root,index,[],offset,bytes);
  entity(phase,'source',index,[0],true);entity(phase,'target',index,[0x10],true,null,false);
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
  for(const field of ['prefab','id','name']){
   add(phase,'skill.'+field+'.pointer',root,index,[...path,f.data],f[field],8);
   add(phase,'skill.'+field,root,index,[...path,f.data,f[field]],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200,required:false});
  }
  if(metadata.skill_wait!==null)add(phase,'skill.wait',root,index,path,metadata.skill_wait,1);
 };
 let profile;
 if(['Entity.ApplyModifier','Entity.OnAfterOutputDamage','Unit.OnAfterOutputDamage','Unit.OnTakeDamage','Entity.OnTakeDamage'].includes(normalized)){
  profile='modifier';for(const phase of ['entry','leave']){clock(phase);modifier(phase);entity(phase,'receiver',0,[],true,null,false);}
  // The parent pointers come from native TLS, never the delayed JS stack.
  for(const [offset,bytes] of [[0,8],[0x10,8],[0x30,8],[0x48,8],[0x60,4],[0x68,4],[0x70,4]])add('checkpoint','parent.modifier','parent_ref',1,[],offset,bytes,{required:false});
  add('checkpoint','parent.hp','parent_ref',1,[0x10],0x40,8,{required:false});
  entity('checkpoint','parent.source',1,[0],true,'parent_ref');entity('checkpoint','parent.target',1,[0x10],true,'parent_ref',false);
 }else if(['Entity.SetHpInternal','Entity.SetHpInternalDirectly','Entity.OnAttributeDirty'].includes(normalized)){
  profile='hp';clock('entry');clock('leave');
  add('entry','hp.identity','arg',0,[],0,0x1c);
  add('entry','hp.born','arg',0,[],0x25,1);
  add('entry','hp.before','arg',0,[],0x40,8);
  add('entry','hp.id','arg',0,[0x148],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200});
  add('leave','hp.closing','entry_ref',0,[],0x18,0x30);
  const optional=fields.length;attr('leave',0);
  for(const field of fields.slice(optional))field.tier='changed';
  // Entry ID text is owned BEFORE the write; no-change cannot delay it.
  // The native profile may skip only changed-tier MaxHP/closing scope when
  // exact opening/closing HP agrees and this is not a max-HP dirty callback.
  // Mechanical raw projection descriptions belong to the very same capture
  // fields. They do not decode Q32, XOR, eligibility, identity or scope.
  const observationValues={clock_open:[['u32',0],['i64',4]],clock_close:[['u32',0],['i64',4]],
   'hp.identity':[['u64',0],['u32',0x18]],'hp.born':[['u8',0]],'hp.before':[['i64',0]],
   'hp.id':[['utf16',0]],'hp.closing':[['u32',0],['i64',0x28]],
   'attributes.count':[['u32',0]],'attributes.slot0':[['u64',0],['u64',8],['u8',0x10]]};
  for(const field of fields){const values=observationValues[field.name];if(values)field.observation={name:field.name,values:values.map(([kind,offset])=>({kind,offset}))};}
 }else if(['Enemy.Born','Enemy._UpdatePlayerSideAndMapLayer','Enemy.DoFakeDeath','Enemy.DoReborn','Enemy.OnFinish','Entity.OnFinish','BObject.OnRecycle','Character.Born'].includes(normalized)){
  profile='lifecycle';for(const phase of ['entry','leave']){clock(phase);entity(phase,'actor',0,[],false,null,normalized==='Character.Born');add(phase,'actor.tile',phase==='entry'?'arg':'entry_ref',0,[],0x360,8,{required:false});add(phase,'actor.finish_reason',phase==='entry'?'arg':'entry_ref',0,[],0x158,4,{required:false});}
  if(normalized==='Enemy.Born'||normalized==='Enemy._UpdatePlayerSideAndMapLayer')attr('leave',0,true);
 }else if(normalized==='BasicSkill.OnCastFinish'){
  profile='skill_cast';for(const phase of ['entry','leave']){clock(phase);skill(phase,0);}
 }else if(normalized.startsWith('Character.')||normalized==='BattleController.OnCharacterFinished'){
  profile='skill_transition';const actor=normalized==='BattleController.OnCharacterFinished'?1:0;
  for(const phase of ['entry','leave']){clock(phase);entity(phase,'actor',actor);skill(phase,actor,[metadata.skill_offsets.charSkill]);if(normalized==='Character.OnAfterSkill')skill(phase,1);}
 }else if(normalized==='Buff.OnTakeDamage'){
  profile='buff';for(const phase of ['entry','leave']){clock(phase);add(phase,'buff',phase==='entry'?'arg':'entry_ref',0,[],0x18,8);add(phase,'buff.shield',phase==='entry'?'arg':'entry_ref',0,[],0x27c,1);add(phase,'buff.key',phase==='entry'?'arg':'entry_ref',0,[0x208],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200});}
 }else if(normalized==='BlockDamage._AssignDynamicVar'){
  profile='shield';
  add('entry','buff.key_pointer','parent_ref',0,[],0x208,8,{required:false});
  add('entry','buff.key','parent_ref',0,[0x208],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200,required:false});
  add('entry','buff.source','parent_ref',0,[],0x18,8,{required:false});add('entry','buff.shield','parent_ref',0,[],0x27c,1,{required:false});
  entity('entry','buff.source_actor',0,[0x18],true,'parent_ref');
  for(const phase of ['entry','leave']){
   clock(phase);modifier(phase,2);const root=phase==='entry'?'arg':'entry_ref';
   const count=add(phase,'blackboard.count',root,1,[],0x18,4,{guard_id:2,guard_side:'before'});
   add(phase,'blackboard.entries',root,1,[0x10],0x20,0,{kind:'utf16_array',count_field_id:count,element_bytes:24,element_pointer_offset:0,max_count:128,expected_class:metadata.string_class,max_units:200,stop_utf16_literal:'dynamic',guard_id:2,guard_side:'member'});
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
   else{entity(phase,'target',0,[],false,null,false);entity(phase,'source',1,[],true,null,false);add(phase,'attack.type',root,normalized.endsWith('NoEvent')?2:4,[],metadata.attack_type_offset,4);}
  }
 }else if(['BattleController.set_state','BattleController.Destroy','BattleController.ClearRunTimeData'].includes(normalized)){
  profile='boundary';for(const phase of ['entry','leave']){clock(phase);
   const root=phase==='entry'?'arg':'entry_ref';
   for(const [role,path,offset,bytes] of [['controller',[],0,0x18],['controller.state',[],0x228,4],['native',[],0x10,8],['native.owner',[0x10],0x28,8],['logger',[],0xe0,8],['logger.owner',[0xe0],0x18,8],['stats',[0xe0],0x30,8],['level',[],0x160,8]])add(phase,role,root,0,path,offset,bytes,{required:!(normalized==='BattleController.Destroy'&&phase==='leave')});
   add(phase,'terminal.singleton','absolute',0,[0x58,0xb8],0,8,{absolute_address:metadata.classes.BattleController});
   const absolute={absolute_address:metadata.classes.BattleController,required:false};
   for(const [name,path,offset,bytes]of [['root.controller',[0x58,0xb8,0],0,0x18],['root.logger',[0x58,0xb8,0,0xe0],0,0x38],['root.level',[0x58,0xb8,0,0x160],0,8],['root.native_owner',[0x58,0xb8,0,0x10],0x28,8],['root.stats',[0x58,0xb8,0,0xe0,0x30],0,8],['root.state',[0x58,0xb8,0],0x228,4],['root.stage_pointer',[0x58,0xb8,0,0x160],0x18,8]])add(phase,name,'absolute',0,path,offset,bytes,absolute);
   add(phase,'root.stage_text','absolute',0,[0x58,0xb8,0,0x160,0x18],0,0,{...absolute,kind:'utf16',expected_class:metadata.string_class,max_units:200});
   add(phase,'receiver.logger_view',root,0,[0xe0],0,0x38,{required:false});
   add(phase,'receiver.level_class',root,0,[0x160],0,8,{required:false});
   add(phase,'receiver.stats_class',root,0,[0xe0,0x30],0,8,{required:false});

   if(normalized==='BattleController.Destroy'&&phase==='leave'){
    add(phase,'terminal.old_logger_stats','entry_ref',8,[],0x30,8,{required:false});
    add(phase,'terminal.old_stage_pointer','entry_ref',9,[],0x18,8,{required:false});
    add(phase,'terminal.stage_text','entry_ref',9,[0x18],0,0,{kind:'utf16',expected_class:metadata.string_class,max_units:200,required:false,role:'terminal.stage_text'});
   }

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
 return {schema_version:1,complete:false,raw_collection:'all',name:normalized,address:address.toString(),abi:'win64-gp',profile,arg_count:argCount,arg_mask:argMask,
  globals,fields,max_bytes:profile==='shield'?131072:65536,max_reads:4096,
  ...(profile==='hp'?{hp_filter:{snapshot_hp:metadata.collect_enemy_hp_changes===false,hp_non_enemy_classes:metadata.hp_non_enemy_classes??[],receiver_arg:0,attribute_index_arg:normalized==='Entity.OnAttributeDirty'?1:null,max_hp_attribute:0,enemy_class:metadata.classes.Enemy??null,enemy_base_class:metadata.classes.BObject??null,normal_setter:normalized==='Entity.SetHpInternal',defer_entry_scope:metadata.coalesce_enemy_hp===true&&normalized==='Entity.SetHpInternal',nochange_preserve_epoch:true,preserve_nested_max_hp_dirty:true}}:{}),
  metadata:{classes:metadata.classes,skill_offsets:metadata.skill_offsets,attack_type_offset:metadata.attack_type_offset},
  ...(profile==='boundary'?{entry_roots:[{slot:8,root:'arg',root_index:0,pointer_offsets:[0xe0]},{slot:9,root:'arg',root_index:0,pointer_offsets:[0x160]},{slot:10,root:'arg',root_index:0,pointer_offsets:[0x10]},{slot:11,root:'arg',root_index:0,pointer_offsets:[0xe0,0x30]}]}:{}),
  ...(metadata.cold_plan?{cold_plan:metadata.cold_plan}:{}),
  ...(normalized==='BattleController.set_state'?{force_cold_boundary:{phase:'leave',condition:'arg_equals',arg_index:1,value:2}}:{}),
  ...(normalized==='BattleController.ClearRunTimeData'?{force_cold_boundary:{phase:'leave',condition:'always'}}:{}),
  ...(normalized==='BattleController.Destroy'?{force_full_binding:{phase:'entry'}}:{}),
  // These producer checkpoints are mandatory profile operations. Engine must
  // reject installation until it can capture them, never ignore or sync-fallback.
  required_projections:['live_scope_guards','same_point_class_ancestry',...(profile==='modifier'?['open_parent_checkpoint','token_host_and_character_form']:[]),...(profile==='boundary'?['full_native_binding']:[]),...(profile==='shield'?['current_buff_and_blackboard_keys']:[])]};
}
// Publish only the already verified address/expected graph. The callback still
// This is a compatibility gate, not an assertion that native lowering exists.
// The engine independently checks every retained required_projections name.
function finalizeNativeCapturePlan(plan,metadata,capabilities){
 const required=['live_scope_guards','same_point_class_ancestry','open_parent_checkpoint','token_host_and_character_form','full_native_binding','current_buff_and_blackboard_keys'];
 if(capabilities===null||capabilities===undefined)return {...plan,complete:false};
 if(capabilities.version!==1||capabilities.raw_collection!=='all'||!Array.isArray(capabilities.lowered_projections)||
    capabilities.lowered_projections.some(name=>typeof name!=='string')||new Set(capabilities.lowered_projections).size!==capabilities.lowered_projections.length)
  throw new Error('unsupported native capture capabilities');
 const lowered=new Set(capabilities.lowered_projections);
 const cold=metadata.cold_plan,fields=cold?.fields;
 const coldReady=metadata.cold_metadata_ready===true&&metadata.immutable_class_parents===true&&
  Array.isArray(cold?.projections)&&cold.projections.length>0&&Array.isArray(fields)&&fields.length===3&&
  fields[0].role==='cold.root_open'&&fields[0].guard_side==='before'&&fields[0].required===true&&
  fields[1].role==='cold.body'&&fields[1].required===false&&fields[1].guard_side==='none'&&
  fields[2].role==='cold.root_close'&&fields[2].guard_side==='after'&&fields[2].required===true&&
  fields[0].guard_id>0&&fields[0].guard_id===fields[2].guard_id&&fields[0].projection_root===fields[2].projection_root;
 const ownedPoints=['__arkdpsReplayPoint','__arkdpsReplayWitness','__arkdpsReplayCheckpoint','__arkdpsReplayOptional','__arkdpsReplayEnd','__arkdpsOwnedRead']
  .every(name=>typeof globalThis[name]==='function');
 return {...plan,complete:plan.raw_collection==='all'&&coldReady&&ownedPoints&&required.every(name=>lowered.has(name))&&
  plan.required_projections.every(name=>lowered.has(name))};
}
// copies both live observations; no cached HP/phase/round value is evidence.
function rawGuardComparison(kind,expected,size){
 const words=[];const u32=value=>Number.isInteger(value)&&value>=0&&value<=0xffffffff;
 const word=(offset,type,value)=>{if(!Number.isInteger(offset)||offset<0||offset+(type==='u8'?1:type==='u64_number'?8:4)>size)return false;words.push(Object.freeze({offset,kind:type,value}));return words.length<=64;};
 try{
  if(kind==='text'){
   if(typeof expected!=='string'||expected.length>200)return null;
   const units=[];for(let i=0;i<expected.length;i++)units.push(expected.charCodeAt(i));
   return Object.freeze({version:1,words:Object.freeze([]),utf16:Object.freeze(units)});
  }
  if(kind==='block'){
   if(!Array.isArray(expected))return null;
   for(const [type,offset,low,high]of expected){
    if(type==='p'){if(!u32(low)||!u32(high)||!word(offset,'u32',low)||!word(offset+4,'u32',high))return null;}
    else if(type==='i'){if(!Number.isInteger(low)||low< -2147483648||low>2147483647||!word(offset,'u32',low>>>0))return null;}
    else return null;
   }
  }else if(kind==='p'){
   const value=BigInt(expected.toString());if(value<0n||value>0xffffffffffffffffn)return null;
   if(!word(0,'u32',Number(value&0xffffffffn))||!word(4,'u32',Number(value>>32n)))return null;
  }else if(kind==='i'){
   if(!Number.isInteger(expected)||expected< -2147483648||expected>2147483647||!word(0,'u32',expected>>>0))return null;
  }else if(kind==='u'){
   if(!u32(expected)||!word(0,'u32',expected))return null;
  }else if(kind==='b'){
   if(!Number.isInteger(expected)||expected<0||expected>255||!word(0,'u8',expected))return null;
  }else if(kind==='n'){
   if(!Number.isInteger(expected)||expected<0||expected>Number(0xffffffffffffffffn)||!word(0,'u64_number',expected))return null;
  }else return null;
  return words.length?Object.freeze({version:1,words:Object.freeze(words)}):null;
 }catch(error){return null;}
}
function matchesRawGuard(view,comparison){
 if(comparison?.version!==1||!Array.isArray(comparison.words))throw new Error('invalid raw guard comparison');
 for(const word of comparison.words){
  const after=word.kind==='u8'?view.getUint8(word.offset):word.kind==='u32'?view.getUint32(word.offset,true):word.kind==='u64_number'?Number(view.getBigUint64(word.offset,true)):NaN;
  if(after!==word.value)return false;
 }
 if(comparison.utf16!==undefined){
  const units=comparison.utf16;if(view.byteLength!==12+units.length*2||view.getInt32(8,true)!==units.length)return false;
  for(let i=0;i<units.length;i++)if(view.getUint16(12+i*2,true)!==units[i])return false;
 }
 return true;
}
function rawGuardView(address,kind,size){
 if(kind!=='utf16')return checkedNativeView(address,size);
 const n=address.add(16).readS32();if(n<0||n>200)throw new Error('bad string');
 const bytes=new ArrayBuffer(12+n*2),view=new DataView(bytes);view.setInt32(8,n,true);
 const text=new Uint8Array(address.add(20).readByteArray(n*2));new Uint8Array(bytes,12).set(text);return view;
}
function scopeFieldProjection(field){
 if(field.role==='scope.clock_open'||field.role==='scope.clock_close'||field.role==='scope.open.3'||field.role==='scope.open.4'||field.role==='scope.controller.state.open'||field.role.includes('logger.class')||field.role.includes('stats.class')||field.expected===undefined||field.expected===null)return {projection:true};
 const kind=field.kind==='utf16'?'text':Array.isArray(field.expected)?'block':field.bytes===4?'i':field.bytes===8?'p':null;
 const comparison=rawGuardComparison(kind,field.expected,field.bytes);
 return {comparison,projection:comparison===null};
}
function scopePrimitiveObservation(field,projection){
 if(!projection||typeof __arkdpsOwnedGraphPrimitives!=='function'||field.kind!=='bytes'||Array.isArray(field.expected))return {};
 const values=field.role==='scope.clock_open'||field.role==='scope.clock_close'
  ?(field.bytes===12?[{kind:'u32',offset:0},{kind:'i64',offset:4}]:null)
  :field.bytes===4?[{kind:'i32',offset:0}]:field.bytes===8?[{kind:'u64',offset:0}]:null;
 return values?{observation:{values}}:{};
}
function checkedScopePrimitives(graph){
 if(typeof __arkdpsOwnedGraphPrimitives!=='function'||!__arkdpsReplayActive()||__arkdpsReplayRecord().native_view!==true)return null;
 const io=__arkdpsOwnedGraphPrimitives('scope',graph);if(io===null)return null;
 if(typeof __arkdpsVerifiedGraphPrimitives!=='function'||__arkdpsVerifiedGraphPrimitives(io,'scope',graph)!==true)__arkdpsReplayGap('unverified native scope primitives');
 return io;
}
function checkedGraphProof(kind,graph){
 if(typeof __arkdpsOwnedGraphCheck!=='function'||!__arkdpsReplayActive()||__arkdpsReplayRecord().native_view!==true)return null;
 const proof=__arkdpsOwnedGraphCheck(kind,graph);if(proof===null)return null;
 if(typeof __arkdpsVerifiedGraphProof!=='function'||__arkdpsVerifiedGraphProof(proof,kind,graph)!==true)__arkdpsReplayGap('unverified native graph proof');
 return proof;
}
function sealRawComparison(comparison){
 if(comparison===null||comparison===undefined)return comparison;
 if(typeof comparison!=='object'||!Array.isArray(comparison.words)||(comparison.utf16!==undefined&&!Array.isArray(comparison.utf16)))throw new Error('invalid raw guard comparison');
 return Object.freeze({...comparison,words:Object.freeze(comparison.words.map(word=>Object.freeze({...word}))),
  ...(comparison.utf16!==undefined?{utf16:Object.freeze([...comparison.utf16])}:{})});
}
function scopeReadGeometry(fields){
 return Object.freeze(fields.map(field=>Object.freeze({...field,
  pointer_offsets:Object.freeze([...field.pointer_offsets]),
  ...(Object.prototype.hasOwnProperty.call(field,'comparison')?{comparison:sealRawComparison(field.comparison)}:{}),
  ...(Object.prototype.hasOwnProperty.call(field,'observation')?{observation:sealedCaptureContract(field.observation)}:{}),
  ...(Array.isArray(field.expected)?{expected:Object.freeze(field.expected.map(row=>Array.isArray(row)?Object.freeze([...row]):row))}:{})})));
}
function sealNativeScopeGraph(graph){
 return Object.freeze({...graph,fields:scopeReadGeometry(graph.fields.map(field=>{const classification=scopeFieldProjection(field);return {...field,...classification,...scopePrimitiveObservation(field,classification.projection)};})),
  ...(graph.context?{context:Object.freeze({...graph.context,identity:Object.freeze({...graph.context.identity})})}:{})});
}
function nativeCaptureScopeGraph(cache,stage,clockAddress,revision){
 if(!Number.isSafeInteger(revision)||revision<=0)throw new Error('invalid capture scope revision');
 if(!cache||!stage)return sealNativeScopeGraph({version:1,revision,mode_epoch:nativeCaptureModeEpoch,raw_collection:'all',available:false,fields:[]});
 const fields=[];let next=1;
 const add=(role,address,bytes,guard,side,expected)=>fields.push({field_id:next++,phase:'checkpoint',role,root:'absolute',root_index:0,absolute_address:address.toString(),pointer_offsets:[],offset:0,bytes,kind:'bytes',required:true,guard_id:guard,guard_side:side,...(expected!==undefined?{expected}:{} )});
 add('scope.clock_open',clockAddress,12,1,'before');
 let group=2;
 const enabled=cache.autochess!==false;
 const tuples=[['p',stage.stageAddress,stage.stagePointer],...(enabled?[['i',cache.addresses.phase,null],['i',cache.addresses.round,null]]:[]),...cache.scopeGuards];
 // Separate before/member/after fields retain same-address repeat witnesses.
 for(const [kind,address,expected,size]of tuples){
  if(group>63)throw new Error('capture scope guard capacity exceeded');
  const bytes=kind==='block'?size:kind==='p'?8:4;
  const value=kind==='p'?expected.toString():expected;
  add('scope.open.'+group,address,bytes,group,'before',value);
  add('scope.close.'+group,address,bytes,group,'after',value);group++;
 }
 const identity=cache.identity;
 if(!stage.key?.startsWith(cache.roots+':'))throw new Error('capture scope stage roots mismatch');
 const stageId=stage.key.slice(cache.roots.length+1);
 for(const side of ['before','after'])fields.push({field_id:next++,phase:'checkpoint',role:'scope.stage_text.'+side,root:'absolute',root_index:0,absolute_address:stage.stagePointer.toString(),pointer_offsets:[],offset:0,bytes:0,kind:'utf16',expected_class:nativeCaptureMetadata.string_class,max_units:200,required:true,guard_id:group,guard_side:side,expected:stageId});
 group++;

 if(!nativeCaptureMetadata)throw new Error('scope capture metadata missing');
 const controllerClass=ptr(nativeCaptureMetadata.classes.BattleController);
 for(const side of ['before','after'])fields.push({field_id:next++,phase:'checkpoint',role:'scope.singleton.'+side,root:'absolute',root_index:0,
  absolute_address:controllerClass.toString(),pointer_offsets:[0x58,0xb8],offset:0,bytes:8,kind:'bytes',required:true,guard_id:group,guard_side:side,expected:identity.controller});
 group++;

 for(const [role,address,offset,bytes] of [
  ['controller.class',identity.controller,0,8],['controller.state',identity.controller,0x228,4],['controller.native',identity.controller,0x10,8],
  ['controller.logger',identity.controller,0xe0,8],['controller.level',identity.controller,0x160,8],
  ['native.owner',identity.native,0x28,8],['logger.class',identity.logger,0,8],
  ['logger.owner',identity.logger,0x18,8],['logger.stats',identity.logger,0x30,8],
  ['stats.class',identity.stats,0,8],['level.class',identity.level,0,8]]){
  if(group>63)throw new Error('capture scope identity capacity exceeded');
  const expected={'controller.class':nativeCaptureMetadata.classes.BattleController,'controller.native':identity.native,'controller.logger':identity.logger,'controller.level':identity.level,'native.owner':identity.controller,'logger.owner':identity.controller,'logger.stats':identity.stats,'level.class':nativeCaptureMetadata.classes.LevelData}[role];
  const absolute=ptr(address).add(offset);add('scope.'+role+'.open',absolute,bytes,group,'before',expected);add('scope.'+role+'.close',absolute,bytes,group,'after',expected);group++;
 }
 add('scope.clock_close',clockAddress,12,1,'after');
 const before=fields.filter(f=>f.guard_side==='before'),after=fields.filter(f=>f.guard_side==='after');
 const ordered=[...before,...after];
 return sealNativeScopeGraph({version:1,revision,mode_epoch:nativeCaptureModeEpoch,raw_collection:'all',available:true,fields:ordered,body_insertion_index:before.length,max_bytes:65536,max_reads:4096,
  context:{autochess:enabled,stage_id:stageId,identity:{...identity},header_key:cache.headerKey,fingerprint:cache.fingerprint,roots:cache.roots,
   stage_pointer:stage.stagePointer.toString(),phase_address:cache.addresses.phase?.toString()??null,round_address:cache.addresses.round?.toString()??null}});
}
let nativeCapturePublicationRevision=0;
const nativeCapturePublications=new Map();
function sealNativePublicationGraph(graph){
 return Object.freeze({...graph,chunks:Object.freeze(graph.chunks.map(chunk=>Object.freeze({...chunk,fields:scopeReadGeometry(chunk.fields)})))});
}
const nativePublicationReadGeometry=new WeakMap();
function publicationReadGeometry(owned,revision){
 let geometry=nativePublicationReadGeometry.get(owned);if(geometry)return geometry;
 const graph=owned.graph??sealNativePublicationGraph({...publicationGuardFields(owned.guards,nativeCaptureMetadata.string_class,revision),mode_epoch:owned.mode_epoch});
 const slots=Object.create(null),fields=[];let index=0;
 for(const chunk of graph.chunks)for(const field of chunk.fields){
  if(slots[field.role]!==undefined)throw new Error('duplicate publication read role');slots[field.role]=index++;fields.push(field);
 }
 geometry=Object.freeze({graph,slots:Object.freeze(slots),fields:Object.freeze(fields)});nativePublicationReadGeometry.set(owned,geometry);return geometry;
}
function nativeGraphChunks(){
 return typeof __arkdpsOwnedGraphReadBatch==='function'&&__arkdpsReplayActive()&&__arkdpsReplayRecord().native_view===true?[]:null;
}
function nativeGraphSlot(kind,graph,index,chunks){
 if(chunks===null)return null;
 const chunk=Math.floor(index/32);if(chunks[chunk]===undefined)chunks[chunk]=__arkdpsOwnedGraphReadBatch(kind,graph,chunk);
 return chunks[chunk]===null?null:chunks[chunk].slot(index%32);
}
function capturedGraphField(kind,graph,index,chunks,role,read,proof=null){
 if(proof!==null)return __arkdpsReplayCheckpoint(role,__arkdpsReplayRecord().checkpoint??0,()=>read(proof.slot(index)));
 if(chunks===null)return __arkdpsReplayWitness(role,()=>read(null));
 // Native GraphBatch already checks the exact field/unique status. Retain the
 // JS role/checkpoint for errors and canonical comparisons without a second FFI.
 return __arkdpsReplayCheckpoint(role,__arkdpsReplayRecord().checkpoint??0,()=>{
  if(!Number.isSafeInteger(index)||index<0)__arkdpsReplayGap('missing graph read field');
  const raw=nativeGraphSlot(kind,graph,index,chunks);
  return raw?read(raw):__arkdpsReplayWitness(role,()=>read(null));
 });
}
function publicationGuardFields(guards,stringClass,revision){
 const chunks=[];
 for(let start=0;start<guards.length;start+=60){
  const fields=[],part=guards.slice(start,start+60);let id=1;
  for(const side of ['before','after'])for(let i=0;i<part.length;i++){
   const [kind,address,before,size]=part[i],text=kind==='text';
   fields.push({field_id:id++,phase:'checkpoint',role:'publication.'+(side==='before'?'open.':'close.')+(start+i),root:'absolute',root_index:0,absolute_address:address.toString(),pointer_offsets:[],offset:0,
    bytes:text?0:kind==='block'?size:kind==='b'?1:kind==='i'||kind==='u'?4:8,
    kind:text?'utf16':'bytes',...(text?{expected_class:stringClass,max_units:Math.max(1,before.length)}:{}),
    required:true,guard_id:i+1,guard_side:side,comparison:rawGuardComparison(kind,before,text?0:kind==='block'?size:kind==='b'?1:kind==='i'||kind==='u'?4:8),projection:false});
  }
  for(const field of fields)if(field.comparison===null)field.projection=true;
  chunks.push({schema_version:1,fields,max_bytes:131072,max_reads:4096});
 }
 return {version:1,revision,mode_epoch:nativeCaptureModeEpoch,raw_collection:'all',available:true,chunks};
}
function snapshotPublicationGuards(guards){
 return Object.freeze(guards.map(guard=>{
  const copy=[...guard];copy[1]=Object.freeze(ptr(guard[1].toString()));
  if(guard[0]==='p')copy[2]=Object.freeze(ptr(guard[2].toString()));
  else if(guard[0]==='block')copy[2]=Object.freeze(guard[2].map(row=>Object.freeze([...row])));
  return Object.freeze(copy);
 }));
}
function publishNativeCapturePublication(binding){
 if(globalThis.arkdpsNativeAsyncCapture!==true||__arkdpsReplayActive())return;
 if(typeof __publishCapturePublication!=='function')throw new Error('native publication publisher unavailable');
 const body=nativeAutoBodyCache?.body;
 if(!binding)return;
 if(binding.native_autochess_context&&(binding.native_autochess_context.status!=='available'||!body?.allInputsGuarded||!body.read?.guards))return;
 if(!binding.native_autochess_context){
  const roots=['controller','native','logger','stats','level'].map(k=>binding.native_identity[k]).join(':');
  const graph=nativeCaptureScopeGraph({autochess:false,identity:binding.native_identity,roots,scopeGuards:[],addresses:{}},nativeAutoStage,clock,++nativeCaptureScopeRevision);
  if(nativeCaptureScopes.size>=256)throw new Error('capture scope history budget exceeded');nativeCaptureScopes.set(graph.revision,graph);__publishCaptureScope(graph);
 }
 const revision=++nativeCapturePublicationRevision;
 if(!Number.isSafeInteger(revision))throw new Error('publication revision exhausted');
 const guards=snapshotPublicationGuards(binding.native_autochess_context?body.read.guards:[]);
 // These are membership/account/text witnesses, not cached mutable HP. Every
 // original input is re-read by the producer before this view can publish.
 const graph=sealNativePublicationGraph(publicationGuardFields(guards,nativeCaptureMetadata.string_class,revision));
 const value={mode_epoch:nativeCaptureModeEpoch,binding:JSON.parse(JSON.stringify(binding)),guards,graph};
 if(nativeCapturePublications.size>=64)throw new Error('capture publication history budget exceeded');
 nativeCapturePublications.set(revision,value);
 __publishCapturePublication(graph);
}
function capturedGuardValue(guard,role,geometry=null,chunks=null,proof=null){
 const slot=geometry?.slots[role],field=geometry?.fields[slot];
 if(proof!==null&&field?.projection===false)return true;
 return capturedGraphField('publication',geometry?.graph,slot,chunks,role,raw=>{
  const [kind,address,before,size]=guard;
  if(field?.comparison){if(!matchesRawGuard(raw??rawGuardView(address,field.kind,field.bytes),field.comparison))throw new Error('captured publication guard changed');return true;}
  if(kind==='block'){
   if(raw)scopeBlockView(raw,size,before,'captured publication guard changed');else scopeBlock(address,size,before,'captured publication guard changed');
   return true;
  }
  if(kind==='text'){
   const length=raw?raw.getInt32(8,true):address.add(0x10).readS32();
   if(length!==before.length)throw new Error('captured publication string length');
   const after=raw?capturedUtf16(raw):address.add(0x14).readUtf16String(before.length);
   if(after!==before)throw new Error('captured publication string changed');
  }else{
   const after=raw?(kind==='p'?ptr(raw.getBigUint64(0,true)):kind==='i'?raw.getInt32(0,true):kind==='u'?raw.getUint32(0,true):kind==='b'?raw.getUint8(0):Number(raw.getBigUint64(0,true))):
    kind==='p'?address.readPointer():kind==='i'?address.readS32():kind==='u'?address.readU32():kind==='b'?address.readU8():address.readU64().toNumber();
   if(kind==='p'?!after.equals(before):after!==before)throw new Error('captured publication input changed');
  }
  return true;
 },proof);
}
function nativeCapturedPublicationBinding(forCallback=false){
 if(__arkdpsReplayActive()&&__arkdpsReplayRecord().facts?.cold_revision!==undefined){
  const captured=prepareCapturedColdBinding(__arkdpsReplayRecord());
  return captured?{...JSON.parse(JSON.stringify(captured)),...nativeInitialEnvelope(captured)}:null;
 }
 if(!__arkdpsReplayActive())throw new Error('captured publication outside replay');
 const record=__arkdpsReplayRecord(),facts=record.facts;
 const sampling=record.kind==='sampling_checkpoint'&&record.edge==='checkpoint'&&probeAdapter.verifiedSamplingCheckpoint(record);
 if((!forCallback&&((record.edge!=='leave'&&!sampling)||record.closed_after!==true))||!facts||!Number.isSafeInteger(facts.publication_revision))return null;
 const owned=nativeCapturePublications.get(facts.publication_revision);
 if(!owned||owned.mode_epoch!==record.facts.mode_epoch)return null;
 const chunks=nativeGraphChunks(),geometry=publicationReadGeometry(owned,facts.publication_revision),proof=checkedGraphProof('publication',geometry.graph);
 for(const side of ['open','close'])owned.guards.forEach((guard,index)=>capturedGuardValue(guard,'publication.'+side+'.'+index,geometry,chunks,proof));
 // Scope/root/stage/state/clock proofs have their own roles. Membership graph
 // success does not replace them, and cached values never supply a new clock.
 const stamp=time(true,false);if(!stamp)return null;
 if(stamp.frame===0&&captureClockFixed().toString()==='0')return null;
 const binding=JSON.parse(JSON.stringify(owned.binding));
 if(binding.native_generation!==nativeGeneration)return null;
 const scopeGraph=nativeCaptureScopes.get(facts.scope_revision);
 if(!scopeGraph?.available||scopeGraph.context.stage_id!==binding.native_stage_id||
    Object.keys(binding.native_identity).some(key=>scopeGraph.context.identity[key]!==binding.native_identity[key]))return null;
 const scope=capturedNativeScopePoint(stamp);
 if(binding.native_autochess_context&&(!scope||scope.scope_key!==binding.native_autochess_context.scope_key||scope.phase!==binding.native_autochess_context.phase||scope.round!==binding.native_autochess_context.round))return null;
 if(!binding.native_autochess_context&&scope!==undefined)return null;
 binding.native_frame=stamp.frame;binding.native_fixed_time=captureClockFixed().toString();
 if(binding.native_autochess_context)binding.native_autochess_context={...binding.native_autochess_context,frame:stamp.frame,game_time:stamp.game_time};
 binding.native_battle_state=__arkdpsReplayWitness('scope.controller.state.open',()=>ptr(binding.native_identity.controller).add(0x228).readS32());
 delete binding.lifecycle_baseline;delete binding.live_character_collection_empty;delete binding.live_character_data_uids;
 return {...binding,...nativeInitialEnvelope(binding)};
}
const nativeCaptureScopeReads=new WeakMap();
function compileCapturedScopeReads(graph){
 let reads=nativeCaptureScopeReads.get(graph);if(reads)return reads;
 // Geometry/expected values are setup metadata. Copy nested block tuples so
 // sealing a graph never freezes a mutable AutoChess cache owned elsewhere.
 const fields=Object.isFrozen(graph.fields)?graph.fields:scopeReadGeometry(graph.fields);
 const all=fields.map((field,index)=>{
  const base=Object.freeze(ptr(field.absolute_address)),path=field.pointer_offsets;
  const kind=field.kind==='utf16'?0:(field.role==='scope.clock_open'||field.role==='scope.clock_close')?1:Array.isArray(field.expected)?2:field.bytes===4?3:4;
  const expectedPointer=kind===4&&field.expected!==undefined&&field.expected!==null?Object.freeze(ptr(field.expected)):null;
  const classification=Object.prototype.hasOwnProperty.call(field,'projection')?{comparison:field.comparison,projection:field.projection}:scopeFieldProjection(field);
  return Object.freeze({role:field.role,slot:index,...classification,base,path,offset:field.offset,
   address:path.length?null:Object.freeze(base.add(field.offset)),kind,bytes:field.bytes,expected:field.expected,expectedPointer,
   classCheck:field.role.includes('logger.class')?1:field.role.includes('stats.class')?2:0,
   output:field.role==='scope.open.3'?1:field.role==='scope.open.4'?2:field.role==='scope.controller.state.open'?3:0});
 });
 Object.defineProperty(all,'dynamic',{value:Object.freeze(all.filter(field=>field.projection===true))});reads=Object.freeze(all);
 nativeCaptureScopeReads.set(graph,reads);return reads;
}
function decodeCapturedScopeField(field,when,raw,primitive=null,primitiveOffset=0){
 let address=field.address;
 if(primitive===null&&!raw&&address===null){address=field.base;for(const offset of field.path)address=address.add(offset).readPointer();address=address.add(field.offset);}
 if(field.comparison){if(primitive===null&&!matchesRawGuard(raw??rawGuardView(address,field.kind===0?'utf16':'bytes',field.bytes),field.comparison))throw new Error('captured scope guard changed');return null;}
 if(field.kind===0){const value=raw?capturedUtf16(raw):str(address);if(value!==field.expected)throw new Error('captured stage text changed');return value;}
 if(field.kind===1){
  const view=primitive===null?(raw??checkedNativeView(address,12)):null;
  const frame=primitive===null?view.getUint32(0,true):primitive[primitiveOffset],fixed=primitive===null?view.getBigInt64(4,true):primitive[primitiveOffset+1];
  if(frame!==when.frame||Number(fixed)/4294967296!==when.game_time)throw new Error('captured scope clock mismatch');
  return null;
 }
 if(field.kind===2){if(raw)scopeBlockView(raw,field.bytes,field.expected,'captured scope guard changed');else scopeBlock(address,field.bytes,field.expected,'captured scope guard changed');return null;}
 if(field.kind===3){const current=primitive!==null?primitive[primitiveOffset]:raw?raw.getInt32(0,true):address.readS32();if(field.expected!==null&&field.expected!==undefined&&current!==field.expected)throw new Error('captured scope integer changed');return current;}
 const current=primitive!==null?ptr(primitive[primitiveOffset]):raw?ptr(raw.getBigUint64(0,true)):address.readPointer();
 if(field.expectedPointer!==null&&!current.equals(field.expectedPointer))throw new Error('captured scope pointer changed');
 if(field.classCheck===1&&className(current)!=='BattleLogger'||field.classCheck===2&&className(current)!=='BattleStats')throw new Error('captured scope class changed');
 return current;
}
const nativeCaptureValidatedClockSamples=new WeakMap();
function captureValidatedClockSample(sample,fixed){
 if(typeof __arkdpsReplayModeRecord!=='function'||!__arkdpsReplayActive())return;
 // The bridge owns this immutable, item-unique identity; it binds the native
 // lease/ticket/checkpoint without forcing a lazy public record on HP entry.
 const token=__arkdpsReplayModeRecord();
 nativeCaptureValidatedClockSamples.set(sample,{token,frame:sample.frame,game_time:sample.game_time,fixed:fixed.toString()});
}
function capturedSampleFixed(sample){
 const saved=nativeCaptureValidatedClockSamples.get(sample);
 if(saved&&typeof __arkdpsReplayModeRecord==='function'&&__arkdpsReplayActive()&&
    saved.token===__arkdpsReplayModeRecord()&&saved.frame===sample.frame&&saved.game_time===sample.game_time)return saved.fixed;
 return captureClockFixed().toString();
}
function capturedNativeScopePoint(when){
 if(__arkdpsReplayActive()&&__arkdpsReplayRecord().facts?.cold_revision!==undefined){
  if(nativeCaptureColdRebuilding)return null;
  const binding=prepareCapturedColdBinding(__arkdpsReplayRecord());
  if(!binding||binding.native_frame!==when.frame||Number(BigInt(binding.native_fixed_time))/4294967296!==when.game_time)return null;
  if(binding.native_autochess_context===undefined)return undefined;
  const {protocol_version,status,verified_same_frame,frame,game_time,scope_key,phase,round,collection_mode}=binding.native_autochess_context;
  return {protocol_version,status,verified_same_frame,frame,game_time,scope_key,phase,round,collection_mode};
 }
 if(!__arkdpsReplayActive())throw new Error('captured scope outside replay');
 const record=__arkdpsReplayRecord(),revision=record.facts?.scope_revision;
 const graph=nativeCaptureScopes.get(revision);
 if(!graph?.available||graph.mode_epoch!==record.facts?.mode_epoch)return null;
 let phase=null,round=null,state=null;const chunks=nativeGraphChunks(),primitives=checkedScopePrimitives(graph),proof=primitives===null?checkedGraphProof('scope',graph):null,compiled=compileCapturedScopeReads(graph);
 const flat=primitives!==null&&typeof primitives.flat==='function'?primitives.flat():null;
 for(const field of primitives===null&&proof===null?compiled:compiled.dynamic){
  const value=primitives!==null?(field.classCheck
   ?__arkdpsReplayCheckpoint(field.role,record.checkpoint??0,()=>decodeCapturedScopeField(field,when,null,flat===null?primitives.values(field.slot):flat.packet,flat===null?0:flat.offsets[field.slot]))
   :decodeCapturedScopeField(field,when,null,flat===null?primitives.values(field.slot):flat.packet,flat===null?0:flat.offsets[field.slot]))
   :capturedGraphField('scope',graph,field.slot,chunks,field.role,raw=>decodeCapturedScopeField(field,when,raw),proof);
  if(field.output===1)phase=value;
  if(field.output===2)round=value;
  if(field.output===3)state=value;
 }
 if(!graph.context.autochess){if(state!==2&&state!==3)return null;return undefined;}
 if(![0,1,2,3,4,5,6,7,8,9,100,101,102,103,104].includes(phase)||!Number.isInteger(round)||round<0)return null;
 if(state!==2&&state!==3)return null;
 const context=graph.context,fixed=capturedSampleFixed(when);
 const scope_key=autoScope(context.roots,phase,round,when.frame,fixed,context.fingerprint,context.header_key);
 return {protocol_version:1,status:'available',verified_same_frame:true,frame:when.frame,game_time:when.game_time,
  scope_key,phase,round,collection_mode:autoCollectionMode(scope_key,phase)};
}

function capturedActorDataPoint(scope){
 return captureNativeInvocationRead(scope,()=>{
  const source=scope.mod.readPointer();
  if(source.isNull()||!scope.nativeModifierSource?.equals(source))return null;
  const klass=source.readPointer();if(!isCharacterSourceClass(klass)||!__arkdpsCapturedAssignable(ptr(nativeCaptureMetadata.classes.BObject),klass))return null;
  const entity_cid=entityId(source),instance_uid=source.add(0x18).readU32(),data=source.add(0x538).readPointer();
  if(!instance_uid||data.isNull()||entity_cid!==scope.source)return null;
  const dataClass=data.readPointer();if(!dataClass.equals(sourceCharacterClasses.data))return null;
  const data_uid=data.add(0x6c).readU32(),token=data.add(0x90).readU8(),predefined=data.add(0x91).readU8();
  if(!data_uid||data_uid!==scope.sourceCharacterUid||token>1||predefined>1)return null;
  const data_cid=str(data.add(0x50).readPointer());
  if(typeof data_cid!=='string'||data_cid.length>128||/[\u0000-\u001f\u007f]/.test(data_cid))return null;
  return {source,klass,data,dataClass,entity_cid,data_cid,data_uid,instance_uid,is_token:token===1,is_predefined:predefined===1};
 });
}
function capturedCharacterFormPoint(scope,when,stamp){
 if(scope.source!=='char_002_amiya'||!scope.sourceCharacterUid||!sourceCharacterClasses||!enemyEntryConfig||stamp?.status!=='available'||![4,5,6].includes(stamp.phase)||!['all','local'].includes(stamp.collection_mode))return;
 const captured=capturedNativeScopePoint(when);
 if(!captured||!sameNativeEventScope({native_autochess_scope:captured},{native_autochess_scope:stamp}))return;
 const point=capturedActorDataPoint(scope);
 if(!point||point.is_token||point.is_predefined||point.data_cid===point.entity_cid||!/^char_[A-Za-z0-9_#.-]{1,123}$/.test(point.data_cid??''))return;
 const {is_token,is_predefined,...identity}=point;return identity;
}
function capturedTokenHostEvidence(scope,when){
 const stamp=when.native_autochess_scope;
 if(!scope.source?.startsWith('token_')||!scope.sourceCharacterUid||!sourceCharacterClasses||!enemyEntryConfig||stamp?.status!=='available'||![4,5,6].includes(stamp.phase)||!['all','local'].includes(stamp.collection_mode)||
    !sameMitigationTime(scope.when,when)||!sameNativeEventScope(scope.when,when))return;
 const captured=capturedNativeScopePoint(when);
 if(!captured||!sameNativeEventScope({native_autochess_scope:captured},when))return;
 const point=capturedActorDataPoint(scope);if(!point?.is_token||point.data_cid!==scope.source)return;
 return captureNativeInvocationRead(scope,()=>{
  const host_key=str(point.data.add(0x98).readPointer()),host_uid=point.data.add(0xa0).readU32(),player_side=point.source.add(0x20).readS32();
  if(!host_key||!/^[A-Za-z0-9_#.-]{1,128}$/.test(host_key)||!host_uid||host_uid===point.data_uid||player_side<0||player_side>4)return;
  return {host_key,host_uid,player_side};
 });
}

function prepareCapturedColdBinding(record){
 if(!__arkdpsReplayActive()||record!==__arkdpsReplayRecord()||record.facts?.cold_revision===undefined)__arkdpsReplayGap('cold binding owner mismatch');
 if(nativeCaptureColdBindings.has(record))return nativeCaptureColdBindings.get(record);
 if(nativeCaptureColdRebuilding)__arkdpsReplayGap('recursive cold binding');
 // Never validate an old body's addresses against a newly discovered scope.
 // Its complete tree was copied in THIS native record, under fresh roots/clock.
 discardAutoChessBody();nativeAutoCache=null;
 nativeCaptureColdRebuilding=true;
 try{
  const binding=__arkdpsReplayPoint('cold.body',()=>nativeBindingPoint(null,true));
  const owned=binding?JSON.parse(JSON.stringify(binding)):null;
  nativeCaptureColdBindings.set(record,owned);return owned;
 }finally{nativeCaptureColdRebuilding=false;}
}
