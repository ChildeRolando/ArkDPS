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
try{
 const size=Memory.alloc(8), assemblies=api('il2cpp_domain_get_assemblies','pointer',['pointer','pointer'])(domain,size);
 const imageOf=api('il2cpp_assembly_get_image','pointer',['pointer']), imageName=api('il2cpp_image_get_name','pointer',['pointer']);
 const classCount=api('il2cpp_image_get_class_count','ulong',['pointer']),classAt=api('il2cpp_image_get_class','pointer',['pointer','ulong']);
 let im;
 for(let i=0;i<size.readU64().toNumber();i++){const x=imageOf(assemblies.add(i*8).readPointer());if(imageName(x).readCString()==='Assembly-CSharp.dll'){im=x;break;}}
 if(!im)throw new Error('Assembly-CSharp not found');
 const wanted=new Set(['Entity','Unit','Buff','BuffContainer','BlockDamage','Modifier','BattleController',
  'Character','BasicSkill','SkillData']);const found={};
 for(let i=0,n=classCount(im);i<n;i++){
  const k=classAt(im,i),name=className(k);if(!wanted.has(name))continue;
  const ns=k.add(0x18).readPointer().readCString();
  const expectedNs=(['BuffContainer','BlockDamage'].includes(name)?'':
    (name==='SkillData'?'Torappu':'Torappu.Battle'));
  if(ns!==expectedNs)continue;
  if(found[name])throw new Error('ambiguous class '+name);found[name]=k;
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
 for(const [owner,k] of Object.entries(found)){
  if(['Modifier','BattleController'].includes(owner))continue;
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
} finally {api('il2cpp_thread_detach','void',['pointer'])(thread);}
// A callback increments epoch on entry and remains active until its native
// invocation returns. RPC marks are serialized with these JS callbacks.
let sequence=0, hpSequence=0, epoch=0, active=0, fatal=null, stopped=false;
const activeHooks=new Map();
// Recoverable per-event gaps do not close the whole event stream.
const unknownSources=new Set();
const engagementUnknownSources=new Set();
let shieldUnknown=false;
let damageUnknown=false;
let engagementUnknown=false;
let skillUnknown=false;
// Recoverable unattributable callbacks are counted, not escalated globally.
let skippedUnattributed=0;
let queue=[];let hpContext=[];
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
// Some elemental-bar modifiers expose a null target on the following damage
// callback. Keep the most recent bar target for the same source/frame so the
// damage event can still offer verifiable target/HP evidence. The Python core
// re-checks HP consistency; a stale or mismatched fallback remains unknown.
const lastBarTarget=new Map();
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
function hpContextQueue(){const result=hpContext;hpContext=[];return result;}
function dynamic(p){const n=p.add(0x18).readS32();if(n<0||n>128)throw new Error('bad blackboard');const a=p.add(0x10).readPointer().add(0x20);for(let i=0;i<n;i++)if(str(a.add(i*24).readPointer())==='dynamic')return a.add(i*24+8).readFloat();throw new Error('missing shield value');}
function fixedArg(p){return Number(BigInt.asIntN(64,BigInt(p.toString())))/4294967296;}
function own(id){return id!==null&&(id.startsWith('char_')||id.startsWith('token_'));}
function record(source,kind,amount,when,proof,evidence){
 if(!Number.isFinite(amount)||amount<0)throw new Error('invalid amount');
 if(queue.length>=100000)throw new Error('event buffer overflow');
 const event={sequence:++sequence,frame:when.frame,game_time:when.game_time,source,kind,amount,proof};
 if(evidence)Object.assign(event,evidence);
 queue.push(event);
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
for(const m of cfg.methods.filter(x=>x.owner==='Buff'&&x.name==='OnTakeDamage'||x.owner==='BlockDamage'&&x.name==='_AssignDynamicVar'||x.owner==='Entity'&&x.name==='ApplyModifier'||(x.owner==='Entity'||x.owner==='Unit')&&(x.name==='OnTakeDamage'||x.name==='OnAfterOutputDamage')||x.owner==='Character')){
 hooks.push(Interceptor.attach(ptr(m.address),{
  onEnter(args){
   active++;epoch++;this.started=true;this.name=m.owner+'.'+m.name;this.tid=this.threadId;
   activeHooks.set(this.name,(activeHooks.get(this.name)||0)+1);
   try{
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
     return;
    }
    if(this.name==='Entity.ApplyModifier'){
     this.mod=args[1];
     try{this.target=this.mod.add(0x10).readPointer();this.hpBefore=targetHp(this.target);}catch(e){this.target=null;this.hpBefore=null;}
     try{this.source=entityId(this.mod.readPointer());}catch(e){this.source=null;}
     if(!this.source||!own(this.source))this.foreign=true;
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
     hpContext.push({sequence:++hpSequence,hook:this.name,frame:end.frame,game_time:end.game_time,
      source:this.source||null,target:targetKey(this.target)||this.target.toString(),
      target_instance:this.target.toString(),amount:this.amount,
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
      if(this.target&&!this.target.isNull())
       lastBarTarget.set(this.source,{frame:end.frame,target:this.target,hp_before:this.hpBefore,instance});
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
     let damageTarget=this.target, damageHpBefore=this.hpBefore, damageInstance=instance;
     let fallbackUsed=false, fallbackInfo=null, evidence=null;
     if(kind==='damage'){
      evidence=overkillEvidence(this.mod,damageTarget,damageHpBefore,end.frame);
      // Some periodic/area modifiers expose a non-null pointer that is not a
      // readable HP target. If the original evidence fails, retry with the
      // same-frame elemental-bar target as a candidate; the HP check still
      // decides whether it is valid.
      const fallback=lastBarTarget.get(this.source);
      fallbackInfo=fallback||null;
      if(!evidence&&fallback&&fallback.frame===end.frame){
       const alternative=overkillEvidence(this.mod,fallback.target,fallback.hp_before,end.frame);
       if(alternative){
        damageTarget=fallback.target;damageHpBefore=fallback.hp_before;damageInstance=fallback.instance;
        evidence=alternative;fallbackUsed=true;
       }
      }
      if(!evidence&&damageTarget&&!damageTarget.isNull()){
       evidence=nonlethalEvidence(damageTarget);
      }
     }
     if(evidence&&damageInstance) evidence.target_instance=damageInstance;
     const proof={method:this.name,type,target:targetType};
     if(kind==='damage'&&(!evidence||fallbackUsed||!damageInstance)){
      proof.target_recovered=fallbackUsed;
      proof.fallback_frame=fallbackInfo?fallbackInfo.frame:null;
      proof.raw_target_null=!this.target||this.target.isNull();
      proof.raw_target_instance=instance;
      proof.fallback_hp_before=fallbackInfo?fallbackInfo.hp_before:null;
      proof.evidence_recovered=!!evidence;
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
   }finally{if(this.started){active--;const left=(activeHooks.get(this.name)||1)-1;if(left>0)activeHooks.set(this.name,left);else activeHooks.delete(this.name);}}
  }
 }));
}
rpc.exports={
 mark() {
  const current = time(true);
  const quality={unknown_sources:[...unknownSources],engagement_unknown_sources:[...engagementUnknownSources],shield_unknown:shieldUnknown,damage_unknown:damageUnknown,engagement_unknown:engagementUnknown,skill_unknown:skillUnknown||!cfg.skillReady,skill_ready:!skillUnknown&&cfg.skillReady};
  if (current === null) return {retry:true,error:fatal,stopped,...quality};
  return {...current,sequence,epoch,active,active_hooks:[...activeHooks.keys()],error:fatal,stopped,...quality};
 },
 drain(){const result=queue,context=hpContextQueue();return {events:result,hp_context:context,error:fatal,stopped,unknown_sources:[...unknownSources],engagement_unknown_sources:[...engagementUnknownSources],shield_unknown:shieldUnknown,damage_unknown:damageUnknown,engagement_unknown:engagementUnknown,skill_unknown:skillUnknown||!cfg.skillReady,skill_ready:!skillUnknown&&cfg.skillReady};},
 stop(){stopped=true;for(const h of hooks)h.detach();return {active,error:fatal};}
};
send({ready:true,arch:Process.arch,skill_ready:!skillUnknown&&cfg.skillReady});
