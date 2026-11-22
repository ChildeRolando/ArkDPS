// ArkDPS event probe adapter v1; loaded with event_probe_runtime.js.
if(Process.arch!=='arm64')throw new Error('ARM64 emulated realm required');
function classFieldOffsets(k){
 const a=k.add(0x80).readPointer(),n=k.add(0x124).readU16(),seen={};
 if(n>1000)throw new Error('invalid field metadata');
 for(let i=0;i<n;i++){const f=a.add(i*32);if(!f.add(16).readPointer().equals(k))throw new Error('field owner mismatch');seen[f.readPointer().readCString()]=f.add(24).readS32();}
 return seen;
}
function classMethods(k,error){
 const a=k.add(0x98).readPointer(),n=k.add(0x120).readU16();
 if(n>2000)throw new Error(error);
 return Array.from({length:n},(_,i)=>a.add(i*8).readPointer());
}
let nativeControllerClass=null,nativeGeneration=0,nativeLastBinding=null;
function androidAutoChessEnvelope(current){
 const binding=nativeBinding();
 if(binding&&/AUTOCHESS/i.test(binding.native_stage_id))return {fields:binding,
  ready:!current||binding.native_frame===current.frame&&Number(BigInt(binding.native_fixed_time))/4294967296===current.game_time};
 return {fields:binding===null&&nativeAutoBindingDiagnostic?
  {autochess_binding_diagnostic:{...nativeAutoBindingDiagnostic}}:{},
  ready:binding!==null||!nativeAutoStage?.enabled};
}
const probeAdapter={
 moduleName:'libil2cpp.so',imageSizeType:'ulong',requiredClasses:[],allowNullClassRows:false,
 controllerFields:{s_fixedFrameCnt:0x14,s_fixedPlayTime:0x18},
 validateClasses(found){nativeControllerClass=found.BattleController;},
 validateTerminalController(k){expectFields(k,{m_logger:0xd8,m_levelData:0x158});},
 terminalRequired:false,loggerOffset:0xd8,levelOffset:0x158,
 clockRegressed(previous,frame,game_time){return frame<previous.frame&&game_time<previous.game_time;},
 onClockRegression(){nativeGeneration++;nativeLastBinding=null;},
 factoryArgument(args,index){return args[index];},
 factoryStorage(args,invocation){return invocation.context.x8;},
 scopedModifierTrace:false,directHealingProof:false,
 autoChessReflected:true,autoChessStateOffset:0x220,autoChessStageOffset:0x18,
 setAutoChessMode,eventCollectionOwner:autoEventOwner,
 stampClock(when){stampAutoChessClock(when);},
 baselineEnvelope(fields,records,acknowledged,through){return discardedAutoChessSkills(fields,records,acknowledged,through);},
 readEnvelope(){return androidAutoChessEnvelope();},
 markEnvelope(current){return androidAutoChessEnvelope(current);},
 enterTerminalStateHook(){},observeTerminalState(){},leaveTerminalStateHook(){},
 terminalInstallFailed(error){cfg.terminalError=String(error);}
};
