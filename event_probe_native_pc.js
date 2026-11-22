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
  if(before===null)return null;
  const state=ptr(before.controller).add(0x228).readS32();
  if(state!==2&&state!==3)return null;
  const stage=str(ptr(before.level).add(0x18).readPointer());
  if(typeof stage!=='string'||!stage.length)return null;
  const frame=clock.readU32(),fixed=clock.add(4).readS64().toString();
  const after=currentNativeIdentity();
  if(after===null||clock.readU32()!==frame||clock.add(4).readS64().toString()!==fixed
     ||Object.keys(before).some(key=>before[key]!==after[key])
     ||ptr(after.controller).add(0x228).readS32()!==state
     ||str(ptr(after.level).add(0x18).readPointer())!==stage)return null;
  if(BigInt(fixed)<0n)return null;
  damageSettlementStamp(frame,Number(BigInt(fixed))/4294967296);
  const key=[before.controller,before.native,before.logger,before.stats,before.level,stage].join(':');
  if(nativeLastBinding&&(nativeLastBinding.key!==key||frame<nativeLastBinding.frame
     ||BigInt(fixed)<BigInt(nativeLastBinding.fixed)))nativeGeneration++;
  nativeLastBinding={key,frame,fixed};
  return {native_process_pid:Process.id,native_identity:before,native_generation:nativeGeneration,
   native_frame:frame,native_fixed_time:fixed,native_stage_id:stage};
 }catch(e){return null;}
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
