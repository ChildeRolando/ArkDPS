'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const {fixture}=require('./native_pc_frida_probe_fixture.js'),{ownedFields}=require('./native_pc_owned_field_fixture.js');
function run(owned,trace,missingParent=false){
 const f=fixture({settlement:true,sourcePath:path.resolve('event_probe_native_pc.js'),globals:{arkdpsTraceHpContext:trace,arkdpsTraceNestedParent:trace}});
 f.vm.runInContext('globalThis.demandStampCalls=0;const demandOriginalStampClock=probeAdapter.stampClock;probeAdapter.stampClock=function(...args){demandStampCalls++;return demandOriginalStampClock.apply(this,args);};',f.context);
 f.write(f.enemyClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));f.clock(30,4294967296n);
 const source=f.unit('char_147_shining',10),target=f.unit('enemy_test',12),mod=f.modifier(source,target,40,-40,1);f.write(target,f.enemyClass);f.write(target.add(0x40),50n*4294967296n);
 const stringClass=f.alloc();for(const p of [source.add(0x148).readPointer(),target.add(0x148).readPointer(),f.level.add(0x18).readPointer()])f.write(p,stringClass);
 let h,scope;const plans=new Map();
 if(owned){const meta={clock:f.vm.runInContext('clock',f.context),string_class:stringClass.toString(),skill_offsets:f.vm.runInContext('({...skillF})',f.context),skill_wait:null,immutable_class_parents:true,classes:{Character:f.character.toString(),BObject:f.bObject.toString(),Enemy:f.enemyClass.toString(),BattleController:f.controllerClass.toString(),LevelData:f.level.readPointer().toString()}};
  h=ownedFields(f,meta);const m=f.context.rpc.exports.mark();f.context.scopeCache={autochess:false,identity:m.native_identity,roots:Object.values(m.native_identity).join(':'),scopeGuards:[],addresses:{}};
  scope=JSON.parse(f.vm.runInContext('JSON.stringify(nativeCaptureScopeGraph(scopeCache,nativeAutoStage,clock,1))',f.context));f.context.scopeGraph=scope;f.vm.runInContext('nativeCaptureScopes.set(1,scopeGraph)',f.context);
 }
 function invoke(name,owner,edge,call,args){const cb=f.callback(name,owner),full=(owner===f.entity?'Entity.':'Unit.')+name;
  if(!owned){const before=f.context.demandStampCalls;const result=cb[edge==='entry'?'onEnter':'onLeave'].call(call,...(edge==='entry'?[args]:[]));if(full==='Unit.OnTakeDamage'){if(edge==='entry')assert.equal(f.context.demandStampCalls,before,'OnTake E must not stamp');else assert(f.context.demandStampCalls>before,'OnTake L must stamp');}return result;}
  if(!plans.has(full)){f.context.planName=full;plans.set(full,f.vm.runInContext('nativeCapturePlan(planName,ptr(1),nativeCaptureMetadata)',f.context));}
  const plan=plans.get(full),parentRefs=[];parentRefs[1]=mod;
  const body=h.captureFields(plan.fields.filter(x=>x.phase===edge),args,args,parentRefs),parent=h.captureFields(plan.fields.filter(x=>x.phase==='checkpoint'&&!(missingParent&&full==='Unit.OnTakeDamage'&&edge==='leave')),args,args,parentRefs,1),guards=h.captureFields(scope.fields.map((x,i)=>({...x,field_id:1000+i})),args,args,[]);
  const record={version:1,edge,checkpoint:0,invocation_id:call.nativeCaptureInvocation,thread_id:1,fields:[...body.fields,...parent.fields,...guards.fields],segments:[...body.segments,...parent.segments,...guards.segments],facts:{mode_epoch:0,scope_mode_epoch:0,scope_revision:1,parent_checkpoints:call.nativeCaptureInvocation==='1'?[]:[{invocation_id:'1',checkpoint:1}]}};
  const before=f.context.demandStampCalls;h.replay(record,()=>cb[edge==='entry'?'onEnter':'onLeave'].call(call,...(edge==='entry'?[args]:[])));if(full==='Unit.OnTakeDamage'){if(edge==='entry')assert.equal(f.context.demandStampCalls,before,'owned OnTake E must not stamp');else assert(f.context.demandStampCalls>before,'owned OnTake L must stamp');}assert.equal(f.context.fixtureSummary.complete,!(missingParent&&full==='Unit.OnTakeDamage'&&edge==='leave'),full+' '+edge+' '+JSON.stringify(f.context.fixtureSummary));
 }
 const a={threadId:1,nativeCaptureInvocation:'1'},t={threadId:1,nativeCaptureInvocation:'2'},o={threadId:1,nativeCaptureInvocation:'3'};
 invoke('ApplyModifier',f.entity,'entry',a,[target,mod]);invoke('OnTakeDamage',f.unitClass,'entry',t,[target,mod]);invoke('OnTakeDamage',f.unitClass,'leave',t,[target,mod]);if(missingParent){assert.equal(h.late(),0);h.restore();return {missingParentRejected:true};}
 f.write(target.add(0x40),10n*4294967296n);invoke('OnAfterOutputDamage',f.entity,'entry',o,[source,mod]);invoke('OnAfterOutputDamage',f.entity,'leave',o,[source,mod]);invoke('ApplyModifier',f.entity,'leave',a,[target,mod]);
 const m=f.context.rpc.exports.mark(),b=f.context.rpc.exports.readEvents(0,m.sequence);if(h){assert.equal(h.late(),0);h.restore();}
 return {events:JSON.parse(JSON.stringify(b.events)),contexts:JSON.parse(JSON.stringify(b.hp_context)),unknown:m.damage_settlement_unlocalized_unknown,traceCount:m.scoped_trace_count};
}
const results=[];for(const trace of [false,true]){const live=run(false,trace),owned=run(true,trace);assert.deepEqual(owned,live);results.push({trace,events:owned.events.length,contexts:owned.contexts.length,traceCount:owned.traceCount,equal:true});}assert.deepEqual(run(true,true,true),{missingParentRejected:true});console.log(JSON.stringify({results,missingParentRejected:true}));
