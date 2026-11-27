'use strict';
// Export production-generated contracts and an owned synthetic heap. No game access.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]||path.join(__dirname,'..')),out=path.resolve(process.argv[3]);
process.env.ARKDPS_PROBE_FIXTURE_ROOT=root;process.chdir(root);fs.mkdirSync(out,{recursive:true});
function load(filename,text,overrides={}){const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const ordinary=m.require.bind(m);m.require=name=>Object.hasOwn(overrides,name)?overrides[name]:ordinary(name);m._compile(text,filename);return m.exports;}
const fridaPath=path.join(root,'tests/native_pc_frida_probe_fixture.js');
let frida=fs.readFileSync(fridaPath,'utf8');assert.equal(frida.split('return {context, messages, requests, write,').length,2);
frida=frida.replace('return {context, messages, requests, write,','return {auditMemory:memory, context, messages, requests, write,');
const owned=load(fridaPath,frida),autoPath=path.join(root,'tests/native_pc_autochess_context_fixture.js');
const auto=load(autoPath,fs.readFileSync(autoPath,'utf8'),{'./native_pc_frida_probe_fixture.js':owned});
const preparePath=path.join(root,'tests/native_pc_first_cold_prepare_replay_fixture.js');
let prepare=fs.readFileSync(preparePath,'utf8').split('const plan=JSON.parse(vm.runInContext(')[0];
assert.equal(prepare.split('fixtureOptions:{roundResetMethod:true').length,2,'prepare fixture anchor changed');
prepare=prepare.replace('fixtureOptions:{roundResetMethod:true','fixtureOptions:{researchHpReference:false,roundResetMethod:true');
// Install verified synthetic method addresses before production metadata lowering.
assert.equal(prepare.split("const methods=['readByteArray'").length,2,'prepare methods anchor changed');
prepare=prepare.replace("const methods=['readByteArray'","f.installSyntheticMitigation({factories:2,noEvent:true});\nconst methods=['readByteArray'");
const hasConsumerEvidence=fs.readFileSync(path.join(root,'native_pc/native_event_runtime/engine_capture_plan.h'),'utf8').includes('"consumer_edge_evidence"');
const tail=String.raw`{
const warm=f.mark();assert.equal(warm.native_autochess_context.status,'available');
const clock=vm.runInContext('clock',c),meta=vm.runInContext('nativeCaptureMetadata',c);
const cache=vm.runInContext('nativeAutoCache',c),stage=vm.runInContext('nativeAutoStage',c);
c.graphClock=clock;const scope=vm.runInContext('nativeCaptureScopeGraph(nativeAutoCache,nativeAutoStage,graphClock,nativeCaptureScopeRevision+1)',c),publication=c.publishedPublication;
assert(scope.available&&publication.available);assert.equal(meta.factory_addresses.length,2);
const source=f.unit('char_capture_source',301),target=f.unit('enemy_capture_target',302);
for(const actor of [source,target]){f.write(actor.add(0x20),actor===source?1:5);f.write(actor.add(0x24),1);f.write(actor.add(0x25),1);f.write(actor.add(0x26),0);f.write(actor.add(0x40),100n<<32n);f.write(actor.add(0x148).readPointer(),f.ptr(meta.string_class));f.write(actor.add(0x538).readPointer(),f.characterData);}
f.write(target,f.enemyClass);const mod=f.modifier(source,target,20,-20,1);f.write(mod.add(0x80),0n);f.write(mod.add(0x88),0n);
const attack=f.alloc();f.write(attack.add(meta.attack_type_offset),1);
const buff=f.alloc(),key=f.managedString('capture_buff');f.write(key,f.ptr(meta.string_class));f.write(buff,f.buffClass);f.write(buff.add(0x18),source);f.write(buff.add(0x208),key);f.write(buff.add(0x27c),0);
const bb=f.alloc(),items=f.alloc(),dynamic=f.managedString('dynamic');f.write(dynamic,f.ptr(meta.string_class));f.write(bb.add(0x10),items);f.write(bb.add(0x18),1);f.write(items.add(0x20),dynamic);f.write(items.add(0x28),1n);f.write(items.add(0x30),0n);
const caps={version:1,raw_collection:'all',lowered_projections:['live_scope_guards','same_point_class_ancestry','open_parent_checkpoint','token_host_and_character_form','full_native_binding','current_buff_and_blackboard_keys',...(__AUDIT_CONSUMER_CAPABILITY__?['consumer_edge_evidence']:[])]};c.auditCaps=caps;
const names=['Entity.ApplyModifier','Entity.OnAfterOutputDamage','Unit.OnAfterOutputDamage','Unit.OnTakeDamage','Entity.OnTakeDamage','Buff.OnTakeDamage','BlockDamage._AssignDynamicVar','BattleFormula.CalculateDamage','BattleFormula.CalculateDamageNoEvent','BattleFormula.CalculateDamageInternal','AttackInfo.GetFinalAtk','Modifier.NewDamageModifierAfterCalculate'];
const hooks=[];
for(const name of names)for(const overload of name==='Modifier.NewDamageModifierAfterCalculate'?[0,1]:[-1]){
 const address=overload<0?'0x9000':meta.factory_addresses[overload];c.auditName=name;c.auditAddress=f.ptr(address);
 const plan=vm.runInContext('finalizeNativeCapturePlan(nativeCapturePlan(auditName,auditAddress,nativeCaptureMetadata),nativeCaptureMetadata,auditCaps)',c);assert(plan.complete);
 let args=[target,mod];
 if(name==='Buff.OnTakeDamage')args=[buff,mod];
 else if(name==='BlockDamage._AssignDynamicVar')args=[f.ptr(0),bb,mod,f.ptr(0),f.ptr(20n<<32n)];
 else if(name==='BattleFormula.CalculateDamage')args=[target,source,f.ptr(0),f.ptr(0),attack];
 else if(name==='BattleFormula.CalculateDamageNoEvent')args=[target,source,attack];
 else if(name==='BattleFormula.CalculateDamageInternal')args=[attack,f.ptr(0),f.ptr(0),f.ptr(0)];
 else if(name==='AttackInfo.GetFinalAtk')args=[attack];
 else if(overload>=0){args=Array.from({length:overload+8},()=>f.ptr(0));args[0]=mod;args[overload+4]=source;args[overload+5]=target;}
 hooks.push({name,overload,plan,args:args.map(x=>x.toString()),parent_refs:[buff.toString(),mod.toString()]});
}
// Cells are an owned sparse IL2CPP model. Serialize allocated heap padding as
// zeros and explicit values as bytes; unaligned Boolean cells remain bytes.
const cells=[...f.auditMemory].map(([a,v])=>[Number(BigInt(a)),v]).sort((a,b)=>a[0]-b[0]);
const base=0x10000,end=Math.max(...cells.map(([a])=>a))+0x4000,heap=Buffer.alloc(end-base);
for(const [address,value]of cells){let at=address-base;if(at<0)continue;
 if(typeof value==='string'){if(cells.some(([a,v])=>a===address-4&&v===value.length))heap.write(value,at,'utf16le');else heap.write(value,at,'utf8');}
 else if(value===undefined)throw Error('explicit unreadable cell in audit heap '+address);
 else if(value&&typeof value.value==='bigint'||typeof value==='bigint')heap.writeBigUInt64LE(BigInt.asUintN(64,typeof value==='bigint'?value:value.value),at);
 else if(address%4!==0)heap.writeUInt8(Number(value)&255,at);
 else heap.writeInt32LE(Number(value)|0,at);
}
module.exports={world:{schema:1,scope,publication,hooks,heap:{base,bytes:heap.length,file:'heap.bin'},labels:{source:source.toString(),target:target.toString(),modifier:mod.toString(),buff:buff.toString(),blackboard:bb.toString()}},heap};}
`;
const result=load(preparePath,prepare+tail.replace('__AUDIT_CONSUMER_CAPABILITY__',JSON.stringify(hasConsumerEvidence)),{'./native_pc_autochess_context_fixture.js':auto});
fs.writeFileSync(path.join(out,'heap.bin'),result.heap);fs.writeFileSync(path.join(out,'world.json'),JSON.stringify(result.world,null,2));
console.log(JSON.stringify({hooks:result.world.hooks.length,scope_fields:result.world.scope.fields.length,publication_fields:result.world.publication.chunks.reduce((n,c)=>n+c.fields.length,0),heap_bytes:result.heap.length}));
