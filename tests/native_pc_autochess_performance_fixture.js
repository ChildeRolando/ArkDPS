// Execute the actual adapter. The full-read oracle only empties its body cache;
// it does not contain another adapter, parser, scope rule or damage formula.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {world}=require('./native_pc_autochess_context_fixture.js');
const plain=value=>JSON.parse(JSON.stringify(value));
const checks=[],counts=[],androidScopeReads=[];
function check(name,run){run();checks.push(name);}
function state(f){return vm.runInContext('({body:nativeAutoBodyCache!==null,scope:nativeAutoCache!==null})',f.context);}
function owned(options={},full=false){
 const f=world(options),parse=f.context.readAutoChess;f.fullReads=0;
 f.context.readAutoChess=function(...args){f.fullReads++;return parse.apply(this,args);};
 if(full){
  const read=f.context.readAutoChessBody;
  f.context.readAutoChessBody=function(...args){vm.runInContext('nativeAutoBodyCache=null',f.context);return read.apply(this,args);};
 }
 f.mark();return f;
}
function equivalent(run,options={}){
 const values=[true,false].map(full=>plain(run(owned(options,full))));
 assert.deepEqual(values[1],values[0]);return values[0];
}
function readCount(f,call){
 let reads=0,depth=0;const proto=f.ptr(0).constructor.prototype,restore=[];
 for(const name of ['readByteArray','readPointer','readCString','readUtf16String','readU8','readU16','readU32','readS32','readFloat','readS64','readU64']){
  const original=proto[name];restore.push([name,original]);
  proto[name]=function(...args){const outer=depth++===0;try{if(outer)reads++;return original.apply(this,args);}finally{depth--;}};
 }
 let result;try{result=call();}finally{for(const [name,original]of restore)proto[name]=original;}
 return {reads,result};
}
function modifierPairs(f,hostile){
 const source=f.unit(hostile?'enemy_attacker':'char_shared',10),target=f.unit(hostile?'char_shared':'enemy_test',12);
 if(!hostile)f.write(source.add(0x538),f.roleData[0]);
 const mod=f.modifier(source,target,40,-40,1),apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 for(let i=0;i<10;i++){
  f.write(target.add(0x40),50n*4294967296n);const a={threadId:1},t={threadId:1};
  apply.onEnter.call(a,[target,mod,f.ptr(0)]);take.onEnter.call(t,[target,mod,f.ptr(0)]);
  f.write(target.add(0x40),10n*4294967296n);take.onLeave.call(t);apply.onLeave.call(a);
 }
 const events=vm.runInContext('queue',f.context);assert.equal(events.length,hostile?0:20);return events;
}
for(const roles of [32,40]){
 check('warm_'+roles+'_role_full_packet_record_and_ack_equivalence',()=>equivalent(f=>{
  const first=f.mark();assert.equal(first.native_autochess_context.memberships.length,roles);
  f.clock(30,4294967296n);
  vm.runInContext("record('char_shared','skill_start',1,time(),{method:'Character.SwitchToSkillState'},{},101)",f.context);
  const mark=f.mark(),drain=f.context.rpc.exports.readEvents(0,mark.sequence),ack=f.context.rpc.exports.ackEvents(mark.sequence);
  assert.equal(drain.events[0].frame,30);assert.equal(drain.events[0].game_time,1);
  assert.equal(drain.events[0].native_autochess_scope.phase,4);
  return {first,mark,drain,ack};
 },{rolesPerPlayer:roles/4}));
 check('warm_'+roles+'_read_budgets_and_callback_event_equivalence',()=>{
  for(const [name,budget,call]of [['scope',78,f=>f.stamp()],['mark',roles===32?974:1134,f=>f.mark()],
   ['10_output_pairs',2190,f=>modifierPairs(f,false)],['10_unlogged_pairs',1840,f=>modifierPairs(f,true)]]){
   const f=owned({rolesPerPlayer:roles/4}),measured=readCount(f,()=>call(f));
   assert.equal(measured.reads,budget,name+' native-read budget');
   assert.equal(f.fullReads,1,'unchanged warm body must avoid parsing another full roster');
   const oracle=owned({rolesPerPlayer:roles/4},true);
   assert.deepEqual(plain(measured.result),plain(call(oracle)));
   counts.push({roles,operation:name,reads:measured.reads});
  }
  const android=owned({platform:'mumu',rolesPerPlayer:roles/4});
  const scope=readCount(android,()=>android.stamp());
  assert.equal(scope.result.native_autochess_scope.status,'available');
  assert.equal(scope.reads,118,'Android scope native-read budget');
  androidScopeReads.push({roles,reads:scope.reads});
 });
}
check('native_busy_marks_leave_the_game_thread_and_accept_the_next_quiet_window',()=>{
 let pending=0,tick=0;
 const f=owned({rolesPerPlayer:8,fixtureOptions:{globals:{__nativeActive:()=>pending}}});
 modifierPairs(f,false);
 pending=1;
 const busy=readCount(f,()=>f.mark());
 assert.equal(busy.result.retry,true);
 assert.equal(busy.result.active,1);
 assert.equal(busy.result.mark_retry_reason,'callback_pending_at_start');
 assert.equal(busy.reads,0);
 const body=vm.runInContext('nativeAutoBodyCache.body',f.context);
 const validate=body.read.validate;
 let passes=0;
 body.read.validate=()=>{
  validate();
  if(++passes%2===1){tick++;f.clock(30*tick,4294967296n*BigInt(tick));}
 };
 pending=0;
 const mark=f.mark();
 assert.equal(mark.retry,undefined);
 assert.equal(mark.native_autochess_context.memberships.length,32);
 const batch=f.context.rpc.exports.readEvents(0,mark.sequence);
 assert.equal(batch.retry,false);
 assert.equal(batch.events.length,20);
 assert(batch.events.every(e=>e.native_autochess_scope?.verified_same_frame===true));
 assert.equal(f.context.rpc.exports.ackEvents(mark.sequence).acknowledged,20);
});
check('cold_roster_crossing_a_frame_can_progress_without_accepting_the_crossed_read',()=>{
 for(const transition of ['unchanged','owner','round']){
  const f=world({rolesPerPlayer:8,fixtureOptions:{globals:{arkdpsAutoChessMode:'local'}}});
  const parse=f.context.readAutoChess;
  let parses=0;
  // The expensive full roster always spans a game tick. Revalidating the
  // recorded fields fits within a tick. Waiting longer between calls cannot
  // make an implementation which always restarts the full read progress.
  f.context.readAutoChess=function(...args){
   const body=parse.apply(this,args);
   f.clock(++parses*30,BigInt(parses)*4294967296n);
   return body;
  };
  f.clock(0,0n);
  const rejected=f.mark();
  assert.equal(rejected.retry,true);
  assert.equal(rejected.autochess_binding_diagnostic.guard,'clock_changed');
  assert.equal(rejected.native_autochess_context,undefined);
  assert.equal(vm.runInContext('acknowledgedSequence',f.context),0);
  assert.equal(f.stamp({frame:30,game_time:1}).native_autochess_scope.status,'unknown');
  if(transition==='owner')f.write(f.players[2][1].add(0x18),f.text('replacement-owner'));
  if(transition==='round')f.write(f.center.add(0x3c),2);
  if(transition!=='unchanged')assert.equal(f.mark().retry,true,'changed scope requires a fresh roster');
  const next=f.mark();
  assert.equal(next.native_autochess_context?.status,'available','next guarded sample must progress');
  assert.equal(next.frame,parses*30);
  assert.equal(parses,transition==='unchanged'?1:2);
  assert.equal(next.native_autochess_context.round,transition==='round'?2:1);
  if(transition==='owner')assert.equal(next.native_autochess_context.players.find(p=>p.index===3).uid,'replacement-owner');
  modifierPairs(f,false);
  const closing=f.mark(),batch=f.context.rpc.exports.readEvents(0,closing.sequence);
  assert.equal(batch.events.length,20);
  assert.equal(f.context.rpc.exports.ackEvents(closing.sequence).acknowledged,closing.sequence);
 }
});
check('sustained_defense_marks_and_reads_progress_with_unresolved_token_hosts',()=>{
 for(const unknownToken of [false,true]){
  const f=world({rolesPerPlayer:4,unknownToken,tokenCount:16,fixtureOptions:{globals:{arkdpsAutoChessMode:'local'}}});
  const parse=f.context.readAutoChess;
  let tick=0,parses=0;
  const advance=()=>{tick++;f.clock(tick*30,BigInt(tick)*4294967296n);};
  f.clock(0,0n);
  f.context.readAutoChess=function(...args){
   const body=parse.apply(this,args);
   parses++;
   const validate=body.read.validate;
   let passes=0;
   body.read.validate=()=>{validate();if(++passes%2===1)advance();};
   advance();return body;
  };
  assert.equal(f.mark().retry,true);
  for(let i=0;i<4;i++){
   const mark=f.mark();
   assert.equal(mark.native_autochess_context?.status,'available','revalidation must finish at a proved clock');
   assert.equal(mark.frame,tick*30);
   assert.equal(mark.native_autochess_context.memberships.length,16);
   assert.equal(mark.native_autochess_context.unknown_memberships.length,unknownToken?16:0);
   const drain=f.context.rpc.exports.readEvents(0,mark.sequence);
   assert.equal(drain.retry,false,'reading the bounded prefix must progress as well');
   assert.equal(drain.native_frame,tick*30);
   assert.deepEqual(plain(drain.native_autochess_context.memberships),plain(mark.native_autochess_context.memberships));
   if(i===0)modifierPairs(f,false);
   else assert.equal(drain.events.length,20);
  }
  assert.equal(parses,1);
  assert.equal(f.context.rpc.exports.ackEvents(20).acknowledged,20);
  if(unknownToken){
   const prepare=f.adapter.prepareEventEnvelope;
   f.adapter.prepareEventEnvelope=()=>{
    const proof=prepare();
    f.write(f.tokenData[0].add(0xa0),101);
    return proof;
   };
   let changed;
   try{changed=f.mark();}finally{f.adapter.prepareEventEnvelope=prepare;}
   assert.equal(changed.native_autochess_context.status,'unknown','a changed input cannot authorize the prepared body');
   assert.equal(f.mark().retry,true,'rediscovery still crosses a tick');
   const recovered=f.mark().native_autochess_context;
   assert.equal(recovered.unknown_memberships.length,15);
   assert.equal(recovered.memberships.find(row=>row.data_uid===999)?.host_uid,101);
  }
 }
});
check('busy_defense_and_assist_can_mark_and_drain_when_one_scalar_sweep_outlasts_a_tick',()=>{
 for(const phase of [4,5]){
  const f=world({rolesPerPlayer:8});
  f.write(f.machine.add(0x5c),phase);
  f.mark();
  const body=vm.runInContext('nativeAutoBodyCache.body',f.context);
  const validate=body.read.validate;
  let tick=0;
  body.read.validate=()=>{
   if(readCount(f,validate).reads>550){
    tick++;
    f.clock(tick*30,BigInt(tick)*4294967296n);
   }
  };
  for(let i=0;i<3;i++){
   modifierPairs(f,false);
   const mark=f.mark();
   assert.equal(mark.native_autochess_context?.phase,phase);
   const batch=f.context.rpc.exports.readEvents(i*20,mark.sequence);
   assert.equal(batch.retry,false);
   assert.equal(batch.events.length,20);
   assert.equal(f.context.rpc.exports.ackEvents(mark.sequence).acknowledged,(i+1)*20);
  }
 }
});
check('first_body_guard_mismatch_falls_back_to_full_parse',()=>{
 const f=owned(),previous=vm.runInContext('nativeAutoBodyCache.body',f.context);
 const plan=vm.runInContext('nativeAutoCache.scopeGuards',f.context);
 assert.equal(f.mark().native_autochess_context.status,'available');
 assert.equal(vm.runInContext('nativeAutoCache.scopeGuards',f.context),plan,'same verified body retains its guard plan');
 f.write(f.roleList.add(0x1c),f.roleList.add(0x1c).readS32()+1);
 assert.equal(f.mark().native_autochess_context.status,'available');
 assert.equal(f.fullReads,2);assert(state(f).body);
 assert.notEqual(vm.runInContext('nativeAutoBodyCache.body',f.context),previous);
 assert.notEqual(vm.runInContext('nativeAutoCache.scopeGuards',f.context),plan);
});
check('second_body_guard_failure_clears_both_caches_without_retry',()=>{
 const f=owned(),proto=f.ptr(0).constructor.prototype,read=proto.readU32;let seen=0;
 proto.readU32=function(){if(this.equals(f.roleData[0].add(0x6c))&&++seen===2)f.write(this,501);return read.call(this);};
 let mark;try{mark=f.mark();}finally{proto.readU32=read;}
 assert.equal(mark.native_autochess_context.status,'unknown');assert.equal(f.fullReads,1);
 assert.deepEqual(plain(state(f)),{body:false,scope:false});
 assert.equal(f.stamp().native_autochess_scope.status,'unknown');
});
check('same_frame_late_owner_change_cannot_publish_single_pass_stale_owner',()=>equivalent(f=>{
 const proto=f.ptr(0).constructor.prototype,read=proto.readU32;let seen=0;
 proto.readU32=function(){
  if(this.equals(f.roleData[3].add(0x6c))&&++seen===1)f.write(f.players[2][1].add(0x18),f.text('replacement-owner'));
  return read.call(this);
 };
 let mark;try{mark=f.mark();}finally{proto.readU32=read;}
 assert.equal(mark.native_autochess_context.status,'unknown');assert.equal(state(f).body,false);
 const stamp=f.stamp();assert.equal(stamp.native_autochess_scope.status,'unknown');return {mark,stamp};
}));
check('same_frame_owner_uid_rebind_reparses_and_advances_epoch',()=>equivalent(f=>{
 const before=f.mark();f.write(f.players[2][1].add(0x18),f.text('replacement-owner'));
 assert.equal(f.stamp().native_autochess_scope.status,'unknown');
 const mark=f.mark();assert.equal(mark.native_autochess_context.players.find(x=>x.index===3).uid,'replacement-owner');
 assert.notEqual(mark.native_autochess_context.scope_key,before.native_autochess_context.scope_key);return mark;
}));
check('same_frame_owner_slot_rebind_is_unknown',()=>{
 const f=owned(),items=f.playerList.add(0x10).readPointer();f.write(items.add(0x28),f.players[1][1]);
 assert.equal(f.stamp().native_autochess_scope.status,'unknown');
 assert.equal(f.mark().native_autochess_context.status,'unknown');assert.equal(state(f).body,false);
});
check('same_frame_graph_replacement_uses_new_fully_read_center',()=>equivalent(f=>{
 const next=f.alloc();f.write(next,f.center.readPointer());
 for(const offset of [0x38,0x3c])f.write(next.add(offset),f.center.add(offset).readS32());
 for(const offset of [0xe8,0xf8])f.write(next.add(offset),f.center.add(offset).readPointer());
 f.write(f.manager.add(0x40),next);return f.mark();
}));
check('closing_root_mutation_retries_without_committing_body',()=>equivalent(f=>{
 f.clock(30,4294967296n);const proto=f.ptr(0).constructor.prototype,read=proto.readU32;let changed=false;
 proto.readU32=function(){if(!changed&&this.equals(f.roleData.at(-1).add(0x6c))){changed=true;f.write(f.controller.add(0x160),f.ptr(0));}return read.call(this);};
 let mark;try{mark=f.mark();}finally{proto.readU32=read;}
 assert(changed);assert.equal(mark.retry,true);assert.equal(mark.native_autochess_context,undefined);
 assert.equal(state(f).body,false);return mark;
}));
check('partial_and_unknown_results_never_leave_a_body_cache',()=>{
 for(const options of [{unknownToken:true},{duplicatePlayer:true},{conflictingUid:true}]){
  const f=owned(options);assert.equal(state(f).body,false);f.mark();assert.equal(state(f).body,false);assert.equal(f.fullReads,2);
 }
 const f=owned();f.write(f.roleData[1].add(0x6c),101);
 assert(f.mark().native_autochess_context.unknown_memberships.length>0);assert.equal(state(f).body,false);
});
check('stage_pointer_phase_round_and_clock_changes_do_not_inherit_old_body',()=>equivalent(f=>{
 const values=[];
 f.write(f.level.add(0x18),f.managedString('Activities/ACT3AUTOCHESS/level_act3autochess_m01'));values.push(f.mark());
 f.write(f.machine.add(0x5c),5);values.push(f.mark());
 f.write(f.center.add(0x3c),2);values.push(f.mark());
 f.clock(60,8589934592n);values.push(f.mark());f.clock(0,0n);values.push(f.mark());
 f.write(f.level.add(0x18),f.managedString('Obt/Main/level_main_01-07'));values.push(f.mark());assert.equal(state(f).body,false);
 return values;
}));
check('scope_bulk_closing_owner_and_root_changes_are_unknown',()=>{
 for(const rootChange of [false,true]){
  const f=owned(),target=f.players[3][1].add(0x10),proto=f.ptr(0).constructor.prototype,read=proto.readByteArray;let seen=0;
  proto.readByteArray=function(size){
   if(this.equals(target)&&++seen===2){
    if(rootChange)f.write(f.controller.add(0x160),f.ptr(0));else f.write(target.add(8),f.text('closing-owner'));
   }
   return read.call(this,size);
  };
  let stamp;try{stamp=f.stamp();}finally{proto.readByteArray=read;}
  assert.equal(seen,2);assert.equal(stamp.native_autochess_scope.status,'unknown');
 }
});
check('scope_compares_high_pointer_bits_and_ignores_only_padding',()=>{
 const f=owned(),old=f.players[2][1].add(0x18).readPointer();
 f.write(f.players[2][1].add(0x18),f.ptr(old.value+(1n<<48n)));
 assert.equal(f.stamp().native_autochess_scope.status,'unknown');
 const g=owned(),items=g.playerList.add(0x10).readPointer();
 for(let i=0;i<4;i++){g.write(items.add(0x20+16*i+4),0x7fffffff);g.write(g.players[i][1].add(0x14),0x12345678);}
 assert.equal(g.stamp().native_autochess_scope.status,'available');
});
check('cross_page_block_failure_and_short_reads_are_unknown',()=>{
 for(const failure of ['throw','null','short']){
  const f=world(),owner=f.ptr(0x40000fffe8n),old=f.players[2][1];
  f.write(owner,old.readPointer());f.write(owner.add(0x10),3);f.write(owner.add(0x18),old.add(0x18).readPointer());
  f.write(owner.add(0x40),old.add(0x40).readPointer());
  const items=f.playerList.add(0x10).readPointer();f.write(items.add(0x20+2*16+8),owner);
  assert.equal(f.mark().native_autochess_context.status,'available');
  const proto=f.ptr(0).constructor.prototype,read=proto.readByteArray;let triggered=false;
  proto.readByteArray=function(size){
   if((this.value&4095n)+BigInt(size)>4096n){triggered=true;if(failure==='throw')throw new Error('owned cross-page fault');return failure==='null'?null:new ArrayBuffer(size-1);}
   return read.call(this,size);
  };
  let stamp;try{stamp=f.stamp();}finally{proto.readByteArray=read;}
  assert(triggered);assert.equal(stamp.native_autochess_scope.status,'unknown');
 }
});
check('cross_page_player_slots_keep_existing_reads_and_closing_checks',()=>{
 const f=world(),items=f.playerList.add(0x10).readPointer(),next=f.ptr(0x4000fffc0n);
 f.write(next,items.readPointer());f.write(next.add(0x18),f.players.length);
 for(let index=0;index<f.players.length;index++){
  const slot=next.add(0x20+16*index);
  f.write(slot,f.players[index][0]);f.write(slot.add(8),f.players[index][1]);
 }
 f.write(f.playerList.add(0x10),next);
 assert.equal(f.mark().native_autochess_context.status,'available');
 const slot=next.add(0x40),proto=f.ptr(0).constructor.prototype,read=proto.readByteArray;
 let passes=0;
 proto.readByteArray=function(size){
  assert((this.value&4095n)+BigInt(size)<=4096n,'new slot range must not cross the page');
  if(this.equals(slot)&&++passes===2)f.write(slot.add(8),f.players[0][1]);
  return read.call(this,size);
 };
 let stamp;try{stamp=f.stamp();}finally{proto.readByteArray=read;}
 assert.equal(passes,2);assert.equal(stamp.native_autochess_scope.status,'unknown');
});
check('scope_checks_stage_pointer_after_guard_pass',()=>{
 for(const platform of ['native_pc','mumu']){
  const f=owned({platform}),old=f.level.add(0x18).readPointer(),host=f.controller.add(0x2e8);
  const next=f.text('Activities/ACT3AUTOCHESS/level_act3autochess_m01');
  assert(!next.equals(old));
  const proto=f.ptr(0).constructor.prototype,read=proto.readPointer;let passes=0;
  proto.readPointer=function(){if(this.equals(host)&&++passes===2)f.write(f.level.add(0x18),next);return read.call(this);};
  let stamp;try{stamp=f.stamp();}finally{proto.readPointer=read;}
  assert.equal(passes,2);assert.equal(stamp.native_autochess_scope.status,'unknown');
 }
});
check('dictionary_version_is_the_same_guarded_value_not_a_second_read',()=>{
 const f=world(),read=f.context.autoRead(),identity=f.context.currentNativeIdentity();
 const proto=f.ptr(0).constructor.prototype,scalar=proto.readS32;let versionReads=0;
 proto.readS32=function(){if(this.equals(f.dict.add(0x2c)))return ++versionReads===1?73:99;return scalar.call(this);};
 let header;try{header=f.context.autoHeader(identity,read);}finally{proto.readS32=scalar;}
 assert.equal(versionReads,1);assert.equal(header.dictVersion,73);
 const witness=read.guards.find(([kind,address])=>kind==='i'&&address.equals(f.dict.add(0x2c)));
 assert.equal(header.dictVersion,witness[2]);
});
check('post_closing_stamp_phase_and_round_changes_never_commit_an_older_body',()=>{
 const {verifyPostClosingStampConsistency}=require('./autochess_mark_consistency_fixture.js');
 assert.equal(verifyPostClosingStampConsistency().length,4);
});
console.log(JSON.stringify({status:'verified',classification:'owned_memory_not_live',checks,counts,androidScopeReads,
 note:'Production parser/adapter only. Full-read oracle clears body cache; no copied adapter or ignored prototype dependency. Read counts do not claim game FPS.'}));
