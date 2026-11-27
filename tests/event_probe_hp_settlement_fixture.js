"use strict";
const assert=require('assert'),path=require('path'),vm=require('vm');
const {fixture}=require('./native_pc_frida_probe_fixture.js'),{ownedFields}=require('./native_pc_owned_field_fixture.js');
function run(mode,owned=false,raw=false,light=false,platform='native_pc'){
 const f=fixture({platform,sourcePath:path.resolve(platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js'),settlement:true,enemyHpState:true,globals:{arkdpsCollectEnemyHpChanges:!light}});
 f.write(f.enemyClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));
 f.context.rpc.exports.mark();f.clock(30,4294967296n);
 const source=f.unit('char_147_shining',10),target=f.unit('enemy_test',12),mod=f.modifier(source,target,40,-40,1);
 f.write(target,f.enemyClass);f.write(target.add(0x25),1);f.write(target.add(0x26),0);f.write(target.add(0x40),50n*4294967296n);
 const attributes=f.alloc(),cache=f.alloc();f.write(target.add(0xb0),attributes);f.write(attributes.add(0x68),cache);f.write(cache.add(0x18),1);f.write(cache.add(0x20),0n);f.write(cache.add(0x28),100n*4294967296n);f.write(cache.add(0x30),1);
 const apply=f.callback('ApplyModifier',f.entity),hp=f.callback('SetHpInternal',f.entity),outer={threadId:1,nativeCaptureInvocation:'1'},writer={threadId:1,nativeCaptureInvocation:'2'};
 let harness,plans,scope;
 if(owned){
  const stringClass=f.alloc();for(const actor of [source,target])f.write(actor.add(0x148).readPointer(),stringClass);f.write(f.level.add(0x18).readPointer(),stringClass);
  const meta={clock:vm.runInContext('clock',f.context),string_class:stringClass.toString(),skill_offsets:vm.runInContext('({...skillF})',f.context),skill_wait:null,immutable_class_parents:true,classes:{Character:f.character.toString(),BObject:f.bObject.toString(),Enemy:f.enemyClass.toString(),BattleController:f.controllerClass.toString(),LevelData:f.level.readPointer().toString()}};
  harness=ownedFields(f,meta);const baseline=f.context.rpc.exports.mark(),roots=Object.values(baseline.native_identity).join(':');
  f.context.scopeCache={autochess:false,identity:baseline.native_identity,roots,scopeGuards:[],addresses:{}};
  scope=JSON.parse(vm.runInContext('JSON.stringify(nativeCaptureScopeGraph(scopeCache,nativeAutoStage,clock,1))',f.context));f.context.scopeGraph=scope;vm.runInContext('nativeCaptureScopes.set(1,scopeGraph);',f.context);
  plans=['Entity.ApplyModifier','Entity.SetHpInternal'].map(name=>{f.context.planName=name;return vm.runInContext('nativeCapturePlan(planName,ptr(1),nativeCaptureMetadata)',f.context);});
 }
 function invoke(index,edge,call,args){
  const hook=index===0?apply:hp;
  if(!owned)return(edge==='entry'?hook.onEnter:hook.onLeave).call(call,...(edge==='entry'?[args]:[]));
  const body=harness.captureFields(plans[index].fields.filter(x=>x.phase===edge),args,args,[]),guard=harness.captureFields(scope.fields.map((x,i)=>({...x,field_id:1000+i})),args,args,[]);
  const record={version:1,edge,checkpoint:0,invocation_id:call.nativeCaptureInvocation,thread_id:1,fields:[...body.fields,...guard.fields],segments:[...body.segments,...guard.segments],facts:{mode_epoch:0,scope_mode_epoch:0,scope_revision:1,parent_checkpoints:[]}};

  if((mode==='changed-guard'&&edge==='leave'||mode==='changed-entry'&&edge==='entry')&&index===1)for(const field of record.fields)if(field.role==='body')field.status='changed_guard';
  harness.replay(record,()=> (edge==='entry'?hook.onEnter:hook.onLeave).call(call,...(edge==='entry'?[args]:[])));
  assert.equal(f.context.fixtureSummary.complete,true,JSON.stringify(f.context.fixtureSummary));
 }
 if(mode==='other-thread')writer.threadId=2;
 invoke(0,'entry',outer,[target,mod]);
 const takeCall={threadId:1,nativeCaptureInvocation:'4'};if(mode==='target-and-writer')f.callback('OnTakeDamage',f.unitClass).onEnter.call(takeCall,[target,mod]);
 if(mode.startsWith('shield')){
  const buff=f.alloc(),bb=f.alloc(),array=f.alloc();f.write(buff,f.buffClass);f.write(buff.add(0x18),source);f.write(buff.add(0x208),f.managedString('shining_s_2'));f.write(buff.add(0x27c),1);f.write(bb.add(0x18),1);f.write(bb.add(0x10),array);f.write(array.add(0x20),f.managedString('dynamic'));f.write(array.add(0x28),15);
  const buffHook=f.callback('OnTakeDamage',f.buffClass),block=f.callback('_AssignDynamicVar',f.blockDamage),bc={threadId:1},sc={threadId:1};
  buffHook.onEnter.call(bc,[buff,mod]);block.onEnter.call(sc,[f.ptr(0),bb,mod,f.ptr(0),f.ptr((mode==='shield-unknown'?100n:10n)*4294967296n)]);f.write(array.add(0x28),5);block.onLeave.call(sc);buffHook.onLeave.call(bc);
  f.write(mod.add(0x48),-30n*4294967296n);
 }
 if(mode!=='no-writer')invoke(1,'entry',writer,[target]);
 f.write(target.add(0x40),(mode==='overkill'?0n:mode.startsWith('shield')?20n:10n)*4294967296n);
 if(mode==='overkill'){f.write(mod.add(0x48),-50n*4294967296n);f.write(mod.add(0x30),80n*4294967296n);}
 if(['nested-normal','nested-direct','nested-transient-uid'].includes(mode)){
  if(mode==='nested-transient-uid')f.write(target.add(0x18),13);
  const nested=f.callback(mode==='nested-direct'?'SetHpInternalDirectly':'SetHpInternal',f.entity),call={threadId:1};
  nested.onEnter.call(call,[target]);f.write(target.add(0x40),10n*4294967296n);nested.onLeave.call(call);
  if(mode==='nested-transient-uid')f.write(target.add(0x18),12);
 }
 if(mode==='cross-thread-foreign'||mode==='cross-thread-unknown'){
  const foreign=f.unit('enemy_foreign',71),child=f.modifier(foreign,mode==='cross-thread-unknown'?f.ptr(0):target,0,0,1),call={threadId:2};
  apply.onEnter.call(call,[target,child]);apply.onLeave.call(call);
 }
 if(mode==='max-hp-dirty'){const dirty=f.callback('OnAttributeDirty',f.entity),call={threadId:1};dirty.onEnter.call(call,[target,f.ptr(0),f.ptr(100n*4294967296n)]);dirty.onLeave.call(call);}
 if(mode==='healing-write'){f.write(target.add(0x40),60n*4294967296n);f.write(mod.add(0x48),10n*4294967296n);}
 if(mode==='uid-change')f.write(target.add(0x18),13);
 if(mode==='cross-frame')f.clock(31,4294967296n);
 if(mode!=='no-writer')invoke(1,'leave',writer,[target]);
 if(mode==='multiple-writer'){const second={threadId:1,nativeCaptureInvocation:'3'};invoke(1,'entry',second,[target]);f.write(target.add(0x40),9n*4294967296n);invoke(1,'leave',second,[target]);f.write(target.add(0x40),10n*4294967296n);}
 if(mode==='cancel')f.write(mod.add(0x70),2);
 if(mode==='same-name-other-uid')f.write(target.add(0x18),13);
 if(mode==='source-uid')f.write(source.add(0x18),99);
 if(mode==='foreign-nested'||mode==='unknown-nested'){
  const foreign=f.unit('enemy_foreign',71),child=f.modifier(foreign,mode==='unknown-nested'?f.ptr(0):target,0,0,1),call={threadId:1};apply.onEnter.call(call,[target,child]);apply.onLeave.call(call);
 }
 if(mode==='unmatched-delta')f.write(mod.add(0x48),-39n*4294967296n);
 if(mode==='target-and-writer')f.callback('OnTakeDamage',f.unitClass).onLeave.call(takeCall);
 invoke(0,'leave',outer,[target,mod]);
 const events=JSON.parse(vm.runInContext('JSON.stringify(queue)',f.context));if(harness){assert.equal(harness.late(),0);harness.restore();}if(raw){const mark=f.context.rpc.exports.mark();return {mark,batch:f.context.rpc.exports.readEvents(0,mark.sequence)};}return events.filter(e=>e.kind==='damage_settlement');
}
for(const mode of ['positive','overkill']){const events=run(mode);assert.equal(events.length,1);assert.equal(events[0].settlement_complete,true,JSON.stringify(events));assert.equal(events[0].amount,mode==='overkill'?50:40);}
for(const mode of ['no-writer','multiple-writer','source-uid','foreign-nested','unknown-nested','unmatched-delta','uid-change','cross-frame','same-name-other-uid','cancel','shield-unknown','max-hp-dirty','other-thread','healing-write','nested-normal','nested-direct','nested-transient-uid','cross-thread-foreign','cross-thread-unknown'])assert(!run(mode).some(e=>e.settlement_complete),mode);
for(const mode of ['positive','overkill'])assert.deepEqual(run(mode,true),run(mode));
for(const mode of ['changed-guard','changed-entry'])assert(!run(mode,true).some(e=>e.settlement_complete));
const shield=run('shield');assert.equal(shield.length,1);assert.equal(shield[0].settlement_complete,true,JSON.stringify(shield));assert.equal(shield[0].amount,30);assert.equal(shield[0].shield_absorbed,10);
const existing=run('target-and-writer');assert.equal(existing.length,1);assert.equal(existing[0].settlement_complete,true);assert.equal(existing[0].proof.evidence_method,'Unit.OnTakeDamage+Entity.ApplyModifier');
// Product policy uses the same settlement owner and every original refusal.
for(const platform of ['native_pc','mumu']){
 for(const mode of ['positive','overkill','shield','target-and-writer']){
  const light=run(mode,false,true,true,platform).batch.events;
  const retained=run(mode,false,true,false,platform).batch.events.filter(e=>e.kind!=='enemy_hp');
  // The deliberately uncollected HP event has no business sequence. Verify
  // the entire retained stream with its newly contiguous sequence, not totals.
  assert.deepEqual(light,retained.map((e,i)=>({...e,sequence:i+1})),platform+':'+mode);
 }
 for(const mode of ['no-writer','multiple-writer','source-uid','foreign-nested','unknown-nested','unmatched-delta','uid-change','cross-frame','same-name-other-uid','cancel','shield-unknown','max-hp-dirty','other-thread','healing-write','nested-normal','nested-direct','nested-transient-uid','cross-thread-foreign','cross-thread-unknown'])assert(!run(mode,false,false,true,platform).some(e=>e.settlement_complete),platform+':'+mode);
}
console.log(JSON.stringify({positive:run('positive',true,true),overkill:run('overkill',true,true)}));

