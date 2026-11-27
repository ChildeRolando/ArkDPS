'use strict';
// Branch gaps missing from the baseline trace: exact current contracts, strict owned reads.
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),Module=require('node:module');
const {fixture}=require('./native_pc_frida_probe_fixture.js'),{ownedFields}=require('./native_pc_owned_field_fixture.js');
function fullWorld(trace){
 const filename=path.resolve('tests/native_pc_first_cold_prepare_replay_fixture.js');
 const text=fs.readFileSync(filename,'utf8').split('const plan=JSON.parse(vm.runInContext(')[0].replace('f.write(f.character.add(0x58),f.unitClass);','f.write(f.enemyClass.add(0x58),f.entity);f.write(f.character.add(0x58),f.unitClass);').replace('fixtureOptions:{roundResetMethod:true','fixtureOptions:{globals:{arkdpsTraceHpContext:'+trace+',arkdpsTraceNestedParent:'+trace+'},roundResetMethod:true');
 const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));m._compile(text+`;module.exports={f,c,live,token,meta:vm.runInContext('nativeCaptureMetadata',c)};`,filename);return m.exports;
}
function preparedHelper(){
 const filename=path.resolve('tests/native_pc_owned_field_fixture.js');let text=fs.readFileSync(filename,'utf8');
 for(const fragment of ["vm.runInContext(bridge.slice(bridge.indexOf('function __integer('),bridge.indexOf('class NativeInteger')),c);", "vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/cold_plan.js','utf8'),c);", "vm.runInContext('__arkdpsSealClassHierarchy(fixtureParentGraph)',c);"]){assert(text.includes(fragment));text=text.replace(fragment,'');}
 const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));m._compile(text,filename);return m.exports.ownedFields;
}
function scenario(kind,trace=false,direct=false){
 const auto=['token','amiya','token_nested','amiya_nested','nested_elemental','token_missing_host','amiya_changed_form','missing_guard'].includes(kind),w=auto?fullWorld(trace):null;
 const f=w?w.f:fixture({settlement:true,sourcePath:path.resolve('event_probe_native_pc.js'),globals:{arkdpsTraceHpContext:trace,arkdpsTraceNestedParent:trace}});

 f.write(f.enemyClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));f.clock(30,4294967296n);
 const source=w?(kind.startsWith('token')?w.token:w.live):f.unit('char_147_shining',10),target=kind==='self_healing'?source:f.unit('enemy_test',12),receiver=kind==='receiver_other'?f.unit('enemy_receiver',13):target;
 if(kind!=='self_healing')f.write(target,f.enemyClass);if(receiver!==target)f.write(receiver,f.enemyClass);
 const actualSource=kind==='source_target'?target:source,mod=f.modifier(actualSource,target,40,kind==='self_healing'?40:-40,kind==='self_healing'?4:1);
 f.write(target.add(0x40),50n*4294967296n);if(receiver!==target)f.write(receiver.add(0x40),50n*4294967296n);
 if(kind.startsWith('amiya')){const id=f.text('char_002_amiya');f.write(source.add(0x148),id);}
 const stringClass=w?f.ptr(w.meta.string_class):f.alloc();for(const actor of [source,target,receiver])f.write(actor.add(0x148).readPointer(),stringClass);f.write(f.level.add(0x18).readPointer(),stringClass);
 const meta={clock:f.vm.runInContext('clock',f.context),string_class:stringClass.toString(),skill_offsets:f.vm.runInContext('({...skillF})',f.context),skill_wait:null,immutable_class_parents:true,classes:{Character:f.character.toString(),BObject:f.bObject.toString(),Enemy:f.enemyClass.toString(),BattleController:f.controllerClass.toString(),LevelData:f.level.readPointer().toString()}};
 if(w)Object.assign(meta,w.meta);
 const h=(w?preparedHelper():ownedFields)(f,meta),m=f.context.rpc.exports.mark();f.context.scopeCache={autochess:false,identity:m.native_identity,roots:Object.values(m.native_identity).join(':'),scopeGuards:[],addresses:{}};
 const scope=JSON.parse(f.vm.runInContext(w?'JSON.stringify(nativeCaptureScopeGraph(nativeAutoCache,nativeAutoStage,clock,1))':'JSON.stringify(nativeCaptureScopeGraph(scopeCache,nativeAutoStage,clock,1))',f.context));f.context.scopeGraph=scope;f.vm.runInContext('nativeCaptureScopes.set(1,scopeGraph)',f.context);
 if(process.env.DEMAND_BASELINE){const base=fs.readFileSync('runtime/capture-demand/formula-baseline-native.js','utf8');f.vm.runInContext(base.slice(base.indexOf('function nativeCapturePlan('),base.indexOf('// Publish only',base.indexOf('function nativeCapturePlan('))),f.context);}
 const statuses=[],failedReads=[];let negativeSeen=false;
 f.context.captureDemandMissing=(address,size,stack)=>failedReads.push({address:String(address),size,stack});
 f.vm.runInContext(`const demandOwnedRead=__arkdpsOwnedRead;__arkdpsOwnedRead=function(address,size){const before=__arkdpsReplay.structural.length;try{return demandOwnedRead(address,size);}finally{if(__arkdpsReplay.structural.length>before)captureDemandMissing(address,size,new Error().stack.split('\\n').slice(2,9));}};`,f.context);
 function invoke(name,owner,edge,call,args,parentSpecs=call.nativeCaptureInvocation==='1'?[]:[{mod,invocation:'1'}]){
  const full=(owner===f.entity?'Entity.':'Unit.')+name,cb=f.callback(name,owner);f.context.planName=full;
  const plan=f.vm.runInContext('nativeCapturePlan(planName,ptr(1),nativeCaptureMetadata)',f.context),parentRefs=[];parentRefs[1]=mod;
  let fields=plan.fields.filter(x=>x.phase===edge&&x.tier!=='publication');
  if(kind==='missing_source_id'&&full==='Entity.ApplyModifier'&&edge==='entry')fields=fields.filter(x=>!(x.name==='source'&&x.kind==='utf16'));
  if(kind==='missing_target_uid'&&full==='Unit.OnTakeDamage'&&edge==='entry')fields=fields.filter(x=>!(['receiver','target'].includes(x.name)&&x.offset===0x18));
  if(kind==='token_missing_host'&&full==='Entity.ApplyModifier'&&edge==='leave')fields=fields.filter(x=>x.name!=='source.data.host');
  const body=h.captureFields(fields,args,args,parentRefs),parent={fields:[],segments:[]};
  for(let i=0;i<parentSpecs.length;i++){const refs=[];refs[1]=parentSpecs[i].mod;const p=h.captureFields(plan.fields.filter(x=>x.phase==='checkpoint'),args,args,refs,i+1);parent.fields.push(...p.fields);parent.segments.push(...p.segments);}
  let guards=scope.fields.map((x,i)=>({...x,field_id:1000+i}));
  if(full==='Unit.OnTakeDamage'&&edge==='entry')guards=[];
  if(kind==='missing_guard'&&full==='Unit.OnTakeDamage'&&edge==='leave')guards=[];
  const g=h.captureFields(guards,args,args,[]),record={version:1,edge,checkpoint:0,invocation_id:call.nativeCaptureInvocation,thread_id:1,fields:[...body.fields,...parent.fields,...g.fields],segments:[...body.segments,...parent.segments,...g.segments],facts:{mode_epoch:0,scope_mode_epoch:0,scope_revision:kind==='missing_guard'?1:guards.length?1:0,parent_checkpoints:parentSpecs.map((p,i)=>({invocation_id:p.invocation,checkpoint:i+1}))}};
  const execute=()=>cb[edge==='entry'?'onEnter':'onLeave'].call(call,...(edge==='entry'?[args]:[]));
  if(direct||process.env.DEMAND_DIRECT)execute();else h.replay(record,execute);
  const complete=direct||process.env.DEMAND_DIRECT?true:f.context.fixtureSummary.complete;statuses.push({full,edge,complete,summary:direct||process.env.DEMAND_DIRECT?null:JSON.parse(JSON.stringify(f.context.fixtureSummary))});if(!complete){negativeSeen=true;const e=Error('strict replay rejected');e.captureDemandRejected=true;throw e;}
 }
 try{
  const a={threadId:1,nativeCaptureInvocation:'1'},t={threadId:1,nativeCaptureInvocation:'2'},o={threadId:1,nativeCaptureInvocation:'3'};
  invoke('ApplyModifier',f.entity,'entry',a,[target,mod]);
  if(kind.includes('nested')){
   const childTarget=f.unit('enemy_nested',20);f.write(childTarget,f.enemyClass);f.write(childTarget.add(0x148).readPointer(),stringClass);f.write(childTarget.add(0x40),30n*4294967296n);
   const childMod=f.modifier(source,childTarget,10,-10,kind==='nested_elemental'?5:1),child={threadId:1,nativeCaptureInvocation:'4'},ct={threadId:1,nativeCaptureInvocation:'5'},co={threadId:1,nativeCaptureInvocation:'6'},parents=[{mod,invocation:'1'},{mod:childMod,invocation:'4'}];
   invoke('ApplyModifier',f.entity,'entry',child,[childTarget,childMod]);
   invoke('OnTakeDamage',f.unitClass,'entry',ct,[childTarget,childMod],parents);invoke('OnTakeDamage',f.unitClass,'leave',ct,[childTarget,childMod],parents);
   f.write(childTarget.add(0x40),20n*4294967296n);
   invoke('OnAfterOutputDamage',f.entity,'entry',co,[source,childMod],parents);invoke('OnAfterOutputDamage',f.entity,'leave',co,[source,childMod],parents);invoke('ApplyModifier',f.entity,'leave',child,[childTarget,childMod]);
  }
  invoke('OnTakeDamage',f.unitClass,'entry',t,[receiver,mod]);
  if(kind==='cross_frame')f.clock(31,4294967296n);
  invoke('OnTakeDamage',f.unitClass,'leave',t,[receiver,mod]);
  if(kind==='amiya_changed_form')f.write(source.add(0x538).readPointer().add(0x50),f.text('char_other_form'));
  if(receiver===target)f.write(target.add(0x40),(kind==='self_healing'?90n:10n)*4294967296n);if(receiver!==target)f.write(receiver.add(0x40),10n*4294967296n);
  invoke('OnAfterOutputDamage',f.entity,'entry',o,[actualSource,mod]);invoke('OnAfterOutputDamage',f.entity,'leave',o,[actualSource,mod]);invoke('ApplyModifier',f.entity,'leave',a,[target,mod]);
  if(kind==='receiver_other'&&!process.env.DEMAND_BASELINE){
   const next=f.modifier(source,target,10,-10,1),na={threadId:1,nativeCaptureInvocation:'7'},nt={threadId:1,nativeCaptureInvocation:'8'},no={threadId:1,nativeCaptureInvocation:'9'},parents=[{mod:next,invocation:'7'}];
   invoke('ApplyModifier',f.entity,'entry',na,[target,next],[]);invoke('OnTakeDamage',f.unitClass,'entry',nt,[target,next],parents);invoke('OnTakeDamage',f.unitClass,'leave',nt,[target,next],parents);
   f.write(target.add(0x40),40n*4294967296n);invoke('OnAfterOutputDamage',f.entity,'entry',no,[source,next],parents);invoke('OnAfterOutputDamage',f.entity,'leave',no,[source,next],parents);invoke('ApplyModifier',f.entity,'leave',na,[target,next],[]);
  }
  const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);assert.equal(h.late(),0,'all actual callback reads must stay owned');
  const events=JSON.parse(JSON.stringify(batch.events)),verified=events.filter(e=>e.kind==='damage_settlement'&&e.settlement_verified===true);
  return {kind,trace,statuses,negativeSeen,failedReads,events,verified:verified.length,unknown:mark.enemy_damage_unknown,firstIssue:mark.damage_settlement_first_issue};
 }catch(e){if(!e.captureDemandRejected)throw e;assert.equal(h.late(),0);return {kind,trace,statuses,negativeSeen,failedReads,rejected:true,events:[],unknown:null};}finally{h.restore();}
}
const results=[];
for(const trace of [false,true])for(const kind of ['normal','receiver_other','source_target','self_healing','token','amiya','token_nested','amiya_nested','nested_elemental','token_missing_host','amiya_changed_form','missing_source_id','missing_target_uid','missing_guard','cross_frame']){if(process.env.DEMAND_BASELINE&& !['normal','receiver_other'].includes(kind))continue;const result=scenario(kind,trace);if(kind==='self_healing'){const direct=scenario(kind,trace,true);assert.deepEqual(result.events,direct.events);result.direct_equal=true;}results.push(result);}
if(!process.env.DEMAND_BASELINE)for(const r of results){
 if(['missing_source_id','missing_target_uid','missing_guard','token_missing_host'].includes(r.kind)){assert.equal(r.rejected,true,r.kind+' must reject real demanded missing bytes');continue;}
 assert.equal(r.negativeSeen,false,r.kind+': no structural gaps');
 if(r.kind==='self_healing'){assert.equal(r.direct_equal,true);const e=r.events.find(e=>e.kind==='healing');assert(e);assert.equal(e.amount,40);assert.equal(r.negativeSeen,false);continue;}
 if(r.kind==='source_target'){assert.equal(r.events.length,0);continue;}
 if(r.kind==='cross_frame'){assert.equal(r.unknown,true);assert.equal(r.events.length,0);continue;}
 if(r.kind==='receiver_other'){
  assert.equal(r.unknown,true);assert.equal(r.events.length,2,'one nominal mismatch and one independently verified recovery');const e=r.events.find(e=>e.kind==='damage');assert(e);assert.equal(e.target,'enemy_receiver');assert.equal(e.amount,40);
  for(const field of ['hp_before','hp_after','real_delta','settlement_complete'])assert.equal(e[field],undefined,'foreign receiver must not be HP certificate: '+field);
  const next=r.events.find(e=>e.kind==='damage_settlement');assert(next&&next.settlement_complete);assert.equal(next.target,'enemy_test');assert.equal(next.amount,10);continue;
 }
 const events=r.events.filter(e=>e.kind==='damage_settlement'&&e.settlement_complete);assert(events.length>0,r.kind+' must produce a positive verified output');
 if(r.kind.includes('nested')){assert.equal(events.length,2);assert.deepEqual(events.map(e=>e.amount),[10,40]);if(r.kind==='nested_elemental')assert.deepEqual(events.map(e=>e.damage_type),[5,1]);}
 if(r.kind.startsWith('token'))for(const e of events){if(r.kind==='token_missing_host')assert.equal(e.proof.native_autochess_token_host,undefined);else assert.equal(e.proof.native_autochess_token_host.host_uid,101);}
 if(r.kind.startsWith('amiya'))for(const e of events){if(r.kind==='amiya_changed_form')assert.equal(e.proof.native_autochess_character_form,undefined);else assert(e.proof.native_autochess_character_form,'Amiya positive must actually enter form proof branch');}
}
const mumuResults=[];
for(const trace of [false,true]){
 const f=fixture({platform:'mumu',sourcePath:path.resolve('event_probe_android2791.js'),settlement:true,globals:{arkdpsTraceHpContext:trace,arkdpsTraceNestedParent:trace}});
 f.clock(30,4294967296n);const source=f.unit('char_147_shining',10),target=f.unit('enemy_test',12),foreign=f.unit('enemy_receiver',13);for(const actor of [target,foreign]){f.write(actor,f.enemyClass);f.write(actor.add(0x40),50n*4294967296n);}
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass),output=f.callback('OnAfterOutputDamage',f.entity);
 function hit(receiver,amount,closing){const mod=f.modifier(source,target,amount,-amount,1),a={threadId:1},t={threadId:1},o={threadId:1};apply.onEnter.call(a,[target,mod]);take.onEnter.call(t,[receiver,mod]);take.onLeave.call(t);f.write(receiver.add(0x40),BigInt(closing)*4294967296n);output.onEnter.call(o,[source,mod]);output.onLeave.call(o);apply.onLeave.call(a);}
 hit(foreign,40,10);hit(target,10,40);
 const mark=f.context.rpc.exports.mark(),events=f.context.rpc.exports.readEvents(0,mark.sequence).events;assert.equal(mark.enemy_damage_unknown,true);assert.equal(events.length,2);
 const mismatch=events[0],normal=events[1];assert.equal(mismatch.kind,'damage');assert.equal(mismatch.amount,40);assert.equal(mismatch.target,'enemy_receiver');for(const key of ['hp_before','hp_after','real_delta','settlement_complete'])assert.equal(mismatch[key],undefined,key);
 assert.equal(normal.kind,'damage_settlement');assert.equal(normal.settlement_complete,true);assert.equal(normal.amount,10);assert.equal(normal.target,'enemy_test');mumuResults.push({trace,mismatch_nominal:40,normal_trusted:10,unknown:true});
}
console.log(JSON.stringify({kind:'strict_damage_demand_branches',results,mumuResults}));
