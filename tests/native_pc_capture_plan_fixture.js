'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const text=fs.readFileSync('event_probe_native_pc.js','utf8');
const context=vm.createContext({});vm.runInContext(text.slice(text.indexOf('function nativeCapturePlan(')),context);
const meta={clock:{toString:()=> '0x1000'},string_class:'0x2000',skill_offsets:{charSkill:0x100,owner:0x20,data:0x28,prefab:0x10,id:0x18,name:0x20},skill_wait:0x50,attack_type_offset:0x30,classes:{Character:'0x3000'},factory_addresses:['0x7000','0x8000']};
const names=['Entity.ApplyModifier','Entity.OnAfterOutputDamage','Unit.OnAfterOutputDamage','Unit.OnTakeDamage','Entity.OnTakeDamage','hp.Entity.SetHpInternal','hp.Entity.SetHpInternalDirectly','hp.Entity.OnAttributeDirty','enemy entry Born','enemy entry _UpdatePlayerSideAndMapLayer','Enemy.DoFakeDeath','Enemy.DoReborn','Enemy.OnFinish','Entity.OnFinish','BObject.OnRecycle','Character.Born','BasicSkill.OnCastFinish','Character.OnSkillStart','Character.SwitchToSkillState','Character.OnSkillCastSucceed','Character.OnToggleSkillStart','Character.OnSkillRetriggered','Character.OnSkillFinish','Character.SwitchOutFromSkillState','Character.OnAfterSkill','BattleController.OnCharacterFinished','Buff.OnTakeDamage','BlockDamage._AssignDynamicVar','BattleFormula.CalculateDamage','BattleFormula.CalculateDamageNoEvent','BattleFormula.CalculateDamageInternal','AttackInfo.GetFinalAtk','Modifier.NewDamageModifierAfterCalculate','BattleController.set_state','BattleController.Destroy','BattleController.ClearRunTimeData'];
for(const name of names){
 const address=name==='Modifier.NewDamageModifierAfterCalculate'?'0x7000':'0x9000';
 const plan=context.nativeCapturePlan(name,{toString:()=>address},meta);
 assert.equal(plan.schema_version,1);assert(plan.fields.length>0&&plan.fields.length<=256,name);
 assert.equal(new Set(plan.fields.map(f=>f.field_id)).size,plan.fields.length);
 assert(plan.fields.some(f=>f.phase==='entry'));assert(plan.fields.some(f=>f.phase==='leave'));
 for(const f of plan.fields){assert(f.pointer_offsets.length<=12,name);assert(f.root_index<16);assert(f.field_id>0);if(f.kind==='utf16')assert.equal(f.expected_class,'0x2000');}
 const first=plan.fields.find(f=>f.role==='clock_open'),last=plan.fields.find(f=>f.role==='clock_close');
 assert(first.field_id!==last.field_id);assert.equal(first.guard_side,'before');assert.equal(last.guard_side,'after');
}
const hp=context.nativeCapturePlan('hp.Entity.SetHpInternal',{toString:()=> '0x9000'},meta);assert.equal(hp.arg_mask,1);
const shield=context.nativeCapturePlan('BlockDamage._AssignDynamicVar',{toString:()=> '0x9000'},meta);assert.equal(shield.arg_mask,22);
assert.throws(()=>context.nativeCapturePlan('unknown',{toString:()=> '0x9000'},meta),/unplanned/);
const shared=fs.readFileSync('event_probe_runtime.js','utf8');assert.equal((shared.match(/Interceptor\.attach\(/g)||[]).length,1);
console.log('PASS_CAPTURE_PLAN_INVENTORY '+names.length+' hook names; resolved metadata/independent guard fields/arg masks');
