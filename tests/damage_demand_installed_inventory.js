'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const entries=[];
const Module=require('node:module');
const fixturePath=path.resolve('tests/native_pc_frida_probe_fixture.js');
const originalFixture=fs.readFileSync(fixturePath,'utf8');
const entityMarker='{ApplyModifier: 2, OnAfterOutputDamage: 1,',unitMarker="{OnTakeDamage: 2});";
if(!originalFixture.includes(entityMarker)||!originalFixture.includes(unitMarker))throw Error('controlled metadata injection marker changed');
const injectedSource=originalFixture.replace(entityMarker,'{ApplyModifier: 2, OnAfterOutputDamage: 1, OnTakeDamage: 2,').replace(unitMarker,'{OnTakeDamage: 2, OnAfterOutputDamage: 1});');
const syntheticModule=new Module(path.resolve('tests/damage_demand_injected_fixture.js'),module);
syntheticModule.filename=path.resolve('tests/damage_demand_injected_fixture.js');syntheticModule.paths=module.paths;syntheticModule._compile(injectedSource,syntheticModule.filename);

const mapped=new Set(['Entity.ApplyModifier','Unit.OnTakeDamage','Entity.OnTakeDamage','Entity.OnAfterOutputDamage','Unit.OnAfterOutputDamage','Buff.OnTakeDamage','BlockDamage._AssignDynamicVar','BattleFormula.CalculateDamage','BattleFormula.CalculateDamageNoEvent','BattleFormula.CalculateDamageInternal','AttackInfo.GetFinalAtk','Modifier.NewDamageModifierAfterCalculate']);
for(const injectedDebugMetadata of [false,true])for(const trace of [false,true])for(const extended of [false,true]){
 const options={sourcePath:path.resolve('event_probe_native_pc.js'),researchHpReference:false,settlement:true,traceHpContext:trace,...(extended?{enemyHpState:true,skillCastState:true,characterLifecycle:true,roundResetMethod:true}:{})};
 const f=(injectedDebugMetadata?syntheticModule.exports.fixture:fixture)(options);const synthetic=f.installSyntheticMitigation({noEvent:true,factories:2});
 const installed=f.hookTrace.filter(x=>x.operation==='attach').map(x=>{const hook=f.hookAt(f.ptr(x.address));const name=hook.arkdpsName||null;
  return {address:x.address,name,metering_group:mapped.has(name)?name:null,debug_only:['Entity.OnTakeDamage','Unit.OnAfterOutputDamage'].includes(name),retention_reason:mapped.has(name)?'damage/formula/shield consumer audited in damage field ledger':name?.startsWith('hp.')?'independent HP writer settlement proof; product per-setter telemetry disabled':name?.includes('Skill')||name?.startsWith('Character.')?'skill/deployment lifecycle and actual cast boundaries':name?.startsWith('enemy entry ')||name?.startsWith('Enemy.')||name?.startsWith('BObject.')||name?.includes('OnFinish')?'enemy lifecycle admission/phase/removal and health identity':name?.startsWith('BattleController.')?'terminal/reset/character lifecycle boundary':'review explicit installed hook; no inference from metadata list'};});
 const slots=[...mapped].flatMap(name=>name==='Modifier.NewDamageModifierAfterCalculate'?[{name,overload:0},{name,overload:1}]:[{name}]).map(slot=>{const matches=installed.filter(i=>i.name===slot.name);const actual=matches[slot.overload??0];return {...slot,installed:!!actual,address:actual?.address??null,status:actual?'actual attach':'source trace-only selector supported; fixture metadata does not declare method'};});
 const cfg=f.vm.runInContext('cfg.methods.map(x=>({name:x.owner+"."+x.name,address:x.address}))',f.context);
 const notInstalled=cfg.filter(m=>!installed.some(i=>i.address===m.address));
 let debugConsumption=null;
 if(injectedDebugMetadata){
  assert.equal(installed.filter(i=>i.debug_only).length,trace?2:0);
  assert.equal(notInstalled.filter(i=>['Entity.OnTakeDamage','Unit.OnAfterOutputDamage'].includes(i.name)).length,trace?0:2);
  if(trace){
   f.clock(30,4294967296n);const source=f.unit('char_test',10),target=f.unit('enemy_test',12),mod=f.modifier(source,target,1,0,1);f.write(target.add(0x40),50n*4294967296n);
   for(const [name,owner,receiver]of [['OnTakeDamage',f.entity,target],['OnAfterOutputDamage',f.unitClass,source]]){
    const callback=f.callback(name,owner),call={threadId:1};callback.onEnter.call(call,[receiver,mod]);callback.onLeave.call(call);
   }
   const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
   const retained=f.vm.runInContext('hpContext.map(x=>x.context.hook)',f.context);
   assert(retained.includes('Entity.OnTakeDamage'));assert(retained.includes('Unit.OnAfterOutputDamage'));
   debugConsumption={actual_callbacks:4,diagnostic_hooks:[...new Set(retained)],retained_context_count:retained.length,source_owner_shared:true};
  }
 }

 entries.push({injectedDebugMetadata,metadata_provenance:injectedDebugMetadata?'controlled fixture-only Entity.OnTakeDamage(argc2) and Unit.OnAfterOutputDamage(argc1); not game/version evidence':'unchanged shared fixture metadata',mode:trace?'traceHpContext=true':'ordinary',extended,options,synthetic,debugConsumption,installed_count:installed.length,mapped_damage_formula_count:installed.filter(x=>x.metering_group).length,debug_only_count:installed.filter(x=>x.debug_only).length,other_retained_count:installed.filter(x=>!x.metering_group).length,installed,metering_slots:slots,enumerated_but_not_installed:notInstalled,metadata_absent_selector_examples:['Entity.OnTakeDamage','Unit.OnAfterOutputDamage','Entity.OnAppliedModifier','Entity.OnTakeHeal','Buff.OnBlockDamage','BlockDamage.Execute']});
}
const files=['event_probe_runtime.js','event_probe_native_pc.js','tests/native_pc_frida_probe_fixture.js',__filename];
console.log(JSON.stringify({schema:1,description:'Actual attach trace -> hookAt exact callbacks; owned fixture installation, not game metadata',injected_fixture_sha256:crypto.createHash('sha256').update(injectedSource).digest('hex'),source_sha256:Object.fromEntries(files.map(n=>[path.relative(process.cwd(),n).replaceAll('\\','/'),crypto.createHash('sha256').update(fs.readFileSync(n)).digest('hex')])),entries},null,2));
