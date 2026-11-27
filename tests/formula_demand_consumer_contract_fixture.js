'use strict';
// Consumer-edge demand contract: actual source compiler, no copied reducer or game reads.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const text=fs.readFileSync('event_probe_native_pc.js','utf8'),context=vm.createContext({});
vm.runInContext(text.slice(text.indexOf('function nativeCapturePlan(')),context);
const meta={clock:{toString:()=> '0x1000'},string_class:'0x2000',immutable_class_parents:true,attack_type_offset:0x18,classes:{},skill_offsets:{},factory_addresses:['0x7000','0x8000']};
const cold={version:1,max_bytes:1024,max_reads:32,projections:[{kind:'object',gate:'exact',expected_class:'0x3000',bytes:8}],fields:['cold.root_open','cold.body','cold.root_close'].map((role,i)=>({field_id:i+1,role,phase:'checkpoint',root:'absolute',absolute_address:'0x4000',root_index:0,pointer_offsets:[],offset:0,bytes:0,kind:'projection_tree',projection_root:0,guard_id:i===1?0:1,guard_side:i===0?'before':i===2?'after':'none',required:i!==1}))};
meta.cold_plan=cold;meta.cold_metadata_ready=true;
const required=['live_scope_guards','same_point_class_ancestry','open_parent_checkpoint','token_host_and_character_form','full_native_binding','current_buff_and_blackboard_keys','consumer_edge_evidence'];
const caps={version:1,raw_collection:'all',lowered_projections:required};
for(const key of ['__arkdpsReplayPoint','__arkdpsReplayWitness','__arkdpsReplayCheckpoint','__arkdpsReplayOptional','__arkdpsReplayEnd','__arkdpsOwnedRead'])context[key]=()=>{};
const specs=[['Buff.OnTakeDamage','0x1'],['BattleFormula.CalculateDamage','0x2'],['BattleFormula.CalculateDamageNoEvent','0x3'],['BattleFormula.CalculateDamageInternal','0x4'],['AttackInfo.GetFinalAtk','0x5'],['Modifier.NewDamageModifierAfterCalculate','0x7000'],['Modifier.NewDamageModifierAfterCalculate','0x8000'],['Unit.OnTakeDamage','0x6'],['Entity.OnTakeDamage','0x7'],['BlockDamage._AssignDynamicVar','0x8']];
const plans=specs.map(([name,address])=>context.nativeCapturePlan(name,{toString:()=>address},meta));
for(const plan of plans){
 assert.equal(context.finalizeNativeCapturePlan(plan,meta,caps).complete,true);
 assert.equal(context.finalizeNativeCapturePlan(plan,meta,{...caps,lowered_projections:required.filter(x=>x!=='consumer_edge_evidence')}).complete,false,'old six-capability runtime must reject new contract');
 const extra=plan.profile==='modifier'?['open_parent_checkpoint']:plan.profile==='shield'?['current_buff_and_blackboard_keys']:[];
 assert.deepEqual([...plan.required_projections],['consumer_edge_evidence','same_point_class_ancestry',...extra],plan.name);
 const clocks=plan.fields.filter(f=>f.name==='clock_open'||f.name==='clock_close');
 assert.equal(clocks.length,4,'four declared independent clock witnesses remain');
 for(const f of clocks){
  const publicationOnly=plan.name==='BattleFormula.CalculateDamageInternal'||plan.name==='Buff.OnTakeDamage'&&f.phase==='leave';
  assert.equal(f.tier??'',publicationOnly?'publication':'',plan.name+':'+f.phase+':'+f.name);
 }
 if(plan.profile==='formula'||plan.profile==='buff')assert(!plan.fields.some(f=>f.name.includes('.data')||f.name.includes('.lineage')),plan.name+' has no consumer of actor data or lineage');
}
const byName=name=>plans.find(p=>p.name===name);
assert.equal(byName('BattleFormula.CalculateDamage').arg_mask,31);
assert.equal(byName('BattleFormula.CalculateDamageNoEvent').arg_mask,7);
assert.equal(byName('BattleFormula.CalculateDamageInternal').arg_mask,9);
assert.equal(byName('AttackInfo.GetFinalAtk').arg_mask,1);
assert.equal(plans[5].arg_mask,183);assert.equal(plans[6].arg_mask,367);
assert.equal(byName('BattleFormula.CalculateDamageInternal').fields.filter(f=>!f.name.startsWith('clock_')).length,0);
assert.equal(byName('AttackInfo.GetFinalAtk').fields.filter(f=>f.name==='attack.type'&&f.phase==='entry').length,1);
assert.equal(byName('AttackInfo.GetFinalAtk').fields.filter(f=>f.name==='attack.type'&&f.phase==='leave').length,0);
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(plans.map(p=>({...p,complete:true})),null,2)+'\n');
console.log('PASS_FORMULA_CONSUMER_CONTRACT ten exact profiles, clock tiers and argument masks');
