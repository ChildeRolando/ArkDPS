'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const {fixture}=require('./native_pc_frida_probe_fixture.js'),{ownedFields}=require('./native_pc_owned_field_fixture.js');
function run(owned){
 const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),settlement:true});
 f.write(f.character.add(0x58),f.unitClass);f.write(f.unitClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));f.write(f.enemyClass.add(0x58),f.entity);
 f.context.rpc.exports.mark();f.clock(30,4294967296n);
 const source=f.unit('char_147_shining',10),target=f.unit('enemy_test',12),mod=f.modifier(source,target,40,-40,1);
 f.write(target.add(0x40),50n*4294967296n);
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass),hooks=[apply,take,f.callback('OnSkillStart',f.character),f.callback('OnSkillFinish',f.character),f.callback('OnTakeDamage',f.buffClass),f.callback('_AssignDynamicVar',f.blockDamage)];
 const stringClass=f.alloc();for(const actor of [source,target])f.write(actor.add(0x148).readPointer(),stringClass);f.write(f.level.add(0x18).readPointer(),stringClass);let harness,meta,scope,plans;
 if(owned){
  meta={clock:vm.runInContext('clock',f.context),string_class:stringClass.toString(),skill_offsets:vm.runInContext('({...skillF})',f.context),skill_wait:null,immutable_class_parents:true,classes:{Character:f.character.toString(),BObject:f.bObject.toString(),Enemy:f.enemyClass.toString(),BattleController:f.controllerClass.toString(),LevelData:f.level.readPointer().toString()}};
  harness=ownedFields(f,meta);
  const baseline=f.context.rpc.exports.mark(),roots=Object.values(baseline.native_identity).join(':');
  f.context.scopeCache={autochess:false,identity:baseline.native_identity,roots,scopeGuards:[],addresses:{}};
  scope=JSON.parse(vm.runInContext('JSON.stringify(nativeCaptureScopeGraph(scopeCache,nativeAutoStage,clock,1))',f.context));
  f.context.scopeGraph=scope;vm.runInContext('nativeCaptureScopes.set(1,scopeGraph);',f.context);
  plans=['Entity.ApplyModifier','Unit.OnTakeDamage','Character.OnSkillStart','Character.OnSkillFinish','Buff.OnTakeDamage','BlockDamage._AssignDynamicVar'].map(name=>{f.context.hookName=name;return vm.runInContext('nativeCapturePlan(hookName,ptr(1),nativeCaptureMetadata)',f.context);});
 }
 const outer={threadId:1,nativeCaptureInvocation:'1'},inner={threadId:1,nativeCaptureInvocation:'2'};
 function invoke(index,edge,call,args,parents=[],refs=[]){
  if(!owned)return (edge==='entry'? hooks[index].onEnter:hooks[index].onLeave).call(call,...(edge==='entry'?[args]:[]));
  const plan=plans[index],body=harness.captureFields(plan.fields.filter(x=>x.phase===edge),args,args,refs);
  const guards=harness.captureFields(scope.fields.map((x,i)=>({...x,field_id:1000+i})),args,args,[]);
  const record={version:1,edge,checkpoint:0,invocation_id:call.nativeCaptureInvocation,thread_id:1,segments:[...body.segments,...guards.segments],fields:[...body.fields,...guards.fields],facts:{mode_epoch:0,scope_mode_epoch:0,scope_revision:1,parent_checkpoints:[]}};
  for(let i=0;i<parents.length;i++){const parent=parents[i],checkpoint=i+1,cp=harness.captureFields(plans[0].fields.filter(x=>x.phase==='checkpoint'),args,args,[null,parent.mod],checkpoint);record.segments.push(...cp.segments);record.fields.push(...cp.fields);record.facts.parent_checkpoints.push({invocation_id:parent.call.nativeCaptureInvocation,checkpoint});}
  harness.replay(record,()=> (edge==='entry'?hooks[index].onEnter:hooks[index].onLeave).call(call,...(edge==='entry'?[args]:[])));
  assert.equal(f.context.fixtureSummary.complete,true,JSON.stringify(f.context.fixtureSummary));
 }
 invoke(0,'entry',outer,[target,mod]);
 const childTarget=f.unit('enemy_child',13),childMod=f.modifier(source,childTarget,7,-7,1),child={threadId:1,nativeCaptureInvocation:'3'},childHit={threadId:1,nativeCaptureInvocation:'4'};
 f.write(childTarget.add(0x148).readPointer(),stringClass);
 f.write(childTarget.add(0x40),20n*4294967296n);
 invoke(0,'entry',child,[childTarget,childMod],[{call:outer,mod}]);
 invoke(1,'entry',childHit,[childTarget,childMod],[{call:outer,mod},{call:child,mod:childMod}]);
 f.write(childTarget.add(0x40),13n*4294967296n);
 invoke(1,'leave',childHit,[childTarget,childMod],[{call:outer,mod},{call:child,mod:childMod}]);
 invoke(0,'leave',child,[childTarget,childMod],[{call:outer,mod}]);
 invoke(1,'entry',inner,[target,mod],[{call:outer,mod}]);
 f.write(target.add(0x40),10n*4294967296n);
 invoke(1,'leave',inner,[target,mod],[{call:outer,mod}]);invoke(0,'leave',outer,[target,mod]);
 const skill=f.alloc(),info=f.alloc();f.write(skill,f.basicSkill);f.write(skill.add(0xa8),source);f.write(skill.add(0xb0),info);f.write(info,f.skillData);f.write(source.add(0x3c8),skill);
 for(const[offset,text]of [[0x50,'shining_s_2'],[0x18,'skill_2'],[0x10,'Shield']]){const p=f.managedString(text);f.write(p,stringClass);f.write(info.add(offset),p);}
 for(const index of [2,3]){const call={threadId:1,nativeCaptureInvocation:String(index+10)};invoke(index,'entry',call,[source]);invoke(index,'leave',call,[source]);}
 const buff=f.alloc(),bb=f.alloc(),array=f.alloc(),key=f.managedString('shining_s_2'),dynamic=f.managedString('dynamic'),shieldMod=f.modifier(f.ptr(0),target,10,0,1);
 f.write(key,stringClass);f.write(dynamic,stringClass);f.write(buff,f.buffClass);f.write(buff.add(0x18),source);f.write(buff.add(0x208),key);f.write(buff.add(0x27c),1);f.write(bb.add(0x18),1);f.write(bb.add(0x10),array);f.write(array.add(0x20),dynamic);f.write(array.add(0x28),15);
 const buffCall={threadId:1,nativeCaptureInvocation:'20'},blockCall={threadId:1,nativeCaptureInvocation:'21'},blockArgs=[f.ptr(0),bb,shieldMod,f.ptr(0),f.ptr(10n*4294967296n)];
 invoke(4,'entry',buffCall,[buff,shieldMod]);invoke(5,'entry',blockCall,blockArgs,[],[buff]);f.write(array.add(0x28),5);invoke(5,'leave',blockCall,blockArgs,[],[buff]);invoke(4,'leave',buffCall,[buff,shieldMod]);
 const events=JSON.parse(vm.runInContext('JSON.stringify(queue)',f.context));
 if(owned){assert.equal(harness.late(),0);harness.restore();}
 return events;
}
const expected=run(false),actual=run(true);assert.deepEqual(actual,expected);assert(actual.some(e=>e.kind==='damage_settlement'));
console.log(JSON.stringify({status:'PASS_MODIFIER_SOURCE_REPLAY',events:actual.length,kinds:actual.map(e=>e.kind),late_reads:0}));
