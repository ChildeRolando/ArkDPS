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
const probeAdapter={
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
 prepareEventEnvelope:prepareAutoChessRetryBody,
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
