// Actual production adapter/runtime, owned memory only; no live process or hooks.
const assert=require('node:assert/strict');
const path=require('node:path');
const vm=require('node:vm');
const {world}=require('./native_pc_autochess_context_fixture.js');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const plain=value=>JSON.parse(JSON.stringify(value));
const checks=[],counts=[];
function check(name,run){run();checks.push(name);}
function owned(mode='local',options={}){
 const f=world({...options,fixtureOptions:{globals:{arkdpsAutoChessMode:mode},...options.fixtureOptions}});
 // Entirely synthetic account text, including two distinct opaque zero aliases.
 ['091000001','91000001','083000003','004000004'].forEach((uid,index)=>
  f.write(f.players[index][1].add(0x18),f.text(uid)));
 f.mark();return f;
}
function request(f,enabled){
 const message={operation:'mark',id:'mode-fixture'};
 if(arguments.length>1)message.autochess_enabled=enabled;
 f.requests.get('arkdps-event-request')(message);
 return f.messages.at(-1).result;
}
function emit(f,kind,uid,extra={},source='char_shared',when=null){
 return f.context.record(source,kind,extra.amount??17,when||f.context.time(),
  extra.proof||{method:'owned fixture'},extra,uid);
}
function batch(f){const mark=f.mark();return plain(f.context.rpc.exports.readEvents(0,mark.sequence));}
function normalized(event){
 const copy=plain(event);delete copy.sequence;
 if(copy.native_autochess_scope)delete copy.native_autochess_scope.collection_mode;
 return copy;
}
function readCount(f,run){
 const proto=f.ptr(0).constructor.prototype,restore=[];let reads=0,depth=0;
 for(const name of ['readByteArray','readPointer','readCString','readUtf16String','readU8','readU16','readU32','readS32','readFloat','readS64','readU64']){
  const original=proto[name];restore.push([name,original]);
  proto[name]=function(...args){const outer=depth++===0;try{if(outer)reads++;return original.apply(this,args);}finally{depth--;}};
 }
 try{run();return reads;}finally{for(const [name,original]of restore)proto[name]=original;}
}
const enemy={target:'enemy_fixture',target_instance:'0xbeef',target_type:0,damage_type:1};

check('initial_mode_strict_mark_transport_scope_latching_and_terminal_retention',()=>{
 const f=owned();let mark=request(f);
 assert.equal(mark.native_autochess_context.collection_mode,'local');
 assert.equal(f.stamp().native_autochess_scope.collection_mode,'local');
 const before=plain(mark),all=request(f,true);
 assert.equal(all.native_autochess_context.collection_mode,'local','desired cannot change the current scope');
 for(const invalid of [null,0,1,'true',{},[]]){
  assert.match(request(f,invalid).error,/must be boolean/);
  assert.equal(vm.runInContext('nativeAutoDesiredMode',f.context),'all');
 }
 for(const phase of [7,8,3]){
  f.write(f.machine.add(0x5c),phase);
  assert.equal(request(f).native_autochess_context.collection_mode,'local');
 }
 f.write(f.machine.add(0x5c),5);
 assert.equal(request(f).native_autochess_context.collection_mode,'all');
 request(f,false);
 assert.equal(request(f).native_autochess_context.collection_mode,'all');
 f.write(f.machine.add(0x5c),6);
 assert.equal(request(f).native_autochess_context.collection_mode,'local');
 request(f,true);f.write(f.center.add(0x3c),2);
 assert.equal(request(f).native_autochess_context.collection_mode,'all');
 assert.equal(before.native_autochess_context.collection_mode,'local');
 assert.equal(f.mark().sequence,0);
});

check('local_defense_keeps_other_accounts_output_on_the_verified_assist_battlefield',()=>{
 const f=owned();
 const defense=request(f,false).native_autochess_context;
 assert.equal(defense.phase,4);assert.equal(defense.collection_mode,'local');
 assert.equal(emit(f,'skill_start',101).sequence,undefined);
 emit(f,'skill_start',104);

 // Only the assisted accounts are deployed; the session's self account is
 // still known from the full account table, but has no role on this field.
 f.write(f.roleList.add(0x18),2);f.write(f.roleList.add(0x1c),2);
 f.write(f.machine.add(0x5c),5);f.clock(30,4294967296n);
 const assist=request(f,false).native_autochess_context;
 assert.equal(assist.status,'available');assert.equal(assist.phase,5);
 assert.notEqual(assist.scope_key,defense.scope_key);
 assert.equal(assist.players.length,4);assert.equal(assist.self_player_index,4);
 assert.deepEqual(Array.from(assist.memberships,row=>row.player_index),[1,2]);
 assert.equal(assist.collection_mode,'all');
 emit(f,'skill_start',101);
 const settlement=emit(f,'damage_settlement',102,{...enemy,target_uid:'77',settlement_complete:true});
 assert.deepEqual(plain(settlement.native_autochess_scope),plain(f.stamp({frame:30,game_time:1}).native_autochess_scope));
 assert.equal(settlement.native_autochess_scope.collection_mode,'all');
 assert.equal(request(f,true).native_autochess_context.collection_mode,'all');
 assert.equal(request(f,false).native_autochess_context.collection_mode,'all');
 f.write(f.machine.add(0x5c),7);
 assert.equal(request(f).native_autochess_context.collection_mode,'all','completed help retains its mode');

 f.write(f.roleList.add(0x18),4);f.write(f.roleList.add(0x1c),3);
 f.write(f.machine.add(0x5c),4);f.write(f.center.add(0x3c),2);f.clock(60,8589934592n);
 assert.equal(request(f).native_autochess_context.collection_mode,'local');
 assert.equal(emit(f,'skill_end',101).sequence,undefined);
 emit(f,'skill_end',104);
 const recorded=batch(f).events;
 assert.deepEqual(recorded.map(event=>event.sequence),[1,2,3,4]);
 assert.deepEqual(recorded.map(event=>event.source_character_uid),[104,101,102,104]);
 assert.deepEqual(recorded.map(event=>event.native_autochess_scope.collection_mode),['local','all','all','local']);
 assert.deepEqual(recorded.map(event=>event.native_autochess_scope.phase),[4,5,5,4]);
 assert.equal(f.context.rpc.exports.ackEvents(4).acknowledged,4);
});

check('default_all_and_invalid_initial_configuration',()=>{
 const f=world();assert.equal(f.mark().native_autochess_context.collection_mode,'all');
 assert.throws(()=>world({fixtureOptions:{globals:{arkdpsAutoChessMode:false}}}),/invalid AutoChess collection mode/);
});

check('same_cid_source_uid_local_and_foreign_filter_before_sequence_and_ack',()=>{
 const f=owned();
 for(const kind of ['skill_start','skill_end','damage','hp_damage']){
  const skipped=emit(f,kind,101,enemy);
  assert(skipped,'hook proof bookkeeping must still receive the assembled event');
  assert.equal(skipped.sequence,undefined,'omission does not even expose a provisional sequence');
  emit(f,kind,104,enemy);
 }
 const first=batch(f);
 assert.equal(first.events.length,4);
 assert.deepEqual(first.events.map(event=>event.sequence),[1,2,3,4]);
 assert(first.events.every(event=>event.source_character_uid===104&&event.amount===17));
 assert.deepEqual(plain(f.context.rpc.exports.readEvents(0,4)),first,'reads remain replayable');
 assert.equal(f.context.rpc.exports.ackEvents(4).acknowledged,4);
 assert.equal(f.context.rpc.exports.readEvents(4,4).events.length,0);
 emit(f,'skill_end',104);
 assert.equal(f.mark().sequence,5);
});

check('shared_elemental_healing_shield_and_ambiguous_targets_preserved_exactly',()=>{
 const all=owned('all'),local=owned();
 const samples=[
  ['damage_settlement',101,{...enemy,nominal_amount:20,shield_absorbed:3,settlement_complete:true}],
  ['elemental_bar',101,{...enemy,damage_type:0}],
  ['elemental_burst',101,{...enemy,damage_type:5}],
  ['damage_settlement',undefined,{...enemy,damage_type:5}],
  ['healing',101,{proof:{direct_healing:{target:'char_shared',target_uid:'104',hp_before:50,hp_after:67}}}],
  ['shield_absorbed',101,{...enemy}],
  ['damage',101,{...enemy,target:'char_shared'}],
  ['hp_damage',101,{...enemy,target:'token_shared'}],
  ['damage',101,{...enemy,target:'unknown_target'}],
  ['damage',101,{...enemy,damage_type:5}],
  ['damage',101,{...enemy,target_type:5}],
  ['damage',101,{damage_type:1}],
  ['skill_start',undefined,{}],
  ['skill_end',999,{}],
  ['enemy_enter',undefined,{target:'enemy_fixture',target_instance:'141',enemy_entry_attributes:{max_hp:100}}],
  ['enemy_hp',undefined,{target:'enemy_fixture',target_instance:'141',hp_before:100,hp_after:90}],
  ['enemy_finish',undefined,{target:'enemy_fixture',target_instance:'141'}],
 ];
 for(const f of [all,local])for(const [kind,uid,evidence]of samples)
  emit(f,kind,uid,evidence,kind.startsWith('enemy_')||kind==='damage_settlement'&&uid===undefined?null:'char_shared');
 const a=batch(all),b=batch(local);
 assert.equal(b.events.length,samples.length);
 assert.deepEqual(b.events.map(normalized),a.events.map(normalized));
 // The first foreign bar remains a negative attribution witness for a later
 // source-null burst; source-side preference never erases that causal fact.
 assert.equal(b.events[1].kind,'elemental_bar');
 assert.equal(b.events[3].source,null);
});

check('missing_conflicting_unknown_uid_and_bad_account_never_pruned',()=>{
 for(const options of [{conflictingUid:true},{unknownToken:true}]){
  const f=owned('local',options),uid=options.unknownToken?999:101;
  emit(f,'skill_start',uid,{},options.unknownToken?'token_shared':'char_shared');
  assert.equal(batch(f).events.length,1);
 }
 const f=owned();
 emit(f,'skill_start',undefined);emit(f,'skill_start',0);emit(f,'skill_start',-1);
 emit(f,'skill_start',4294967296);emit(f,'skill_start',101,{},'char_wrong');
 assert.equal(batch(f).events.length,5);
 const invalid=owned();invalid.write(invalid.players[0][1].add(0x18),invalid.text('000000000'));
 invalid.mark();emit(invalid,'skill_start',101);assert.equal(batch(invalid).events.length,1);
 // The producer excludes explicit native unknown UID claims, just as the
 // Python lookup does; unrelated known members are still independent.
 const unknown=owned();
 vm.runInContext("nativeAutoBodyCache.body.unknown.push({data_uid:101,reason:'owned unknown witness'});nativeAutoOwners=null",unknown.context);
 unknown.mark();emit(unknown,'skill_start',101);emit(unknown,'skill_start',102);
 assert.equal(batch(unknown).events.length,1);
});

check('proven_token_host_and_partial_neighbors_do_not_alias_players',()=>{
 const f=owned('local',{token:true});emit(f,'skill_start',999,{},'token_shared');
 assert.equal(f.mark().sequence,0);
 f.write(f.center.add(0x38),1);f.mark();emit(f,'skill_start',999,{},'token_shared');
 assert.equal(batch(f).events.length,1,'the very same real host becomes local only after a proven scope refresh');
});

check('new_frame_or_scope_without_full_membership_refresh_retains_and_failed_guard_does_not_latch',()=>{
 const f=owned();f.clock(30,4294967296n);
 emit(f,'skill_start',101);assert.equal(f.mark().sequence,1,'a header stamp does not reread old membership');
 emit(f,'skill_start',101);assert.equal(f.mark().sequence,1,'same-frame full refresh now proves the foreign actor');
 request(f,true);f.write(f.machine.add(0x5c),5);
 const stamp=f.stamp({frame:30,game_time:1});
 assert.equal(stamp.native_autochess_scope.collection_mode,'all');
 emit(f,'skill_end',101,{},'char_shared',stamp);
 assert.equal(f.mark().sequence,2,'new scope must not borrow the prior membership map');
 const old=owned();request(old,true);old.write(old.machine.add(0x5c),5);
 old.write(old.controller.add(0x2e8),old.ptr(0));
 emit(old,'skill_start',101);assert.equal(vm.runInContext('nativeAutoEffective.mode',old.context),'local');
 const queued=vm.runInContext('queue',old.context);
 assert.equal(queued[0].native_autochess_scope.status,'unknown');
});

check('old_timestamp_scope_and_coalesced_tail_are_not_retagged',()=>{
 const f=owned();const old=f.stamp();request(f,true);
 f.write(f.machine.add(0x5c),5);f.clock(30,4294967296n);f.mark();
 emit(f,'enemy_hp',undefined,{target:'enemy_fixture',target_instance:'77',hp_before:90,hp_after:80},null,old);
 emit(f,'skill_start',101,{},'char_shared',old);
 const events=batch(f).events;
 assert.equal(events.length,2,'old timestamp ownership cannot use a later lookup');
 assert.equal(events[0].frame,0);assert.equal(events[0].native_autochess_scope.phase,4);
 assert.equal(events[0].native_autochess_scope.collection_mode,'local');
 assert.equal(events[1].native_autochess_scope.scope_key,old.native_autochess_scope.scope_key);
});

function hookDamage(f,uid){
 const source=f.unit('char_shared',10000+uid),target=f.unit('enemy_fixture',20000+uid);
 const data=source.add(0x538).readPointer();f.write(data.add(0x6c),uid);
 const modifier=f.modifier(source,target,40,-40,1);
 f.write(target.add(0x40),50n*4294967296n);
 const apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 const outer={threadId:1},inner={threadId:1};
 apply.onEnter.call(outer,[target,modifier,f.ptr(0)]);take.onEnter.call(inner,[target,modifier,f.ptr(0)]);
 f.write(target.add(0x40),10n*4294967296n);take.onLeave.call(inner);apply.onLeave.call(outer);
}
check('actual_four_actor_hooks_keep_local_amount_and_quality_with_no_extra_native_reads',()=>{
 const modes=['all','local'].map(mode=>{
  const f=owned(mode),reads=readCount(f,()=>{for(const uid of [101,102,103,104])hookDamage(f,uid);});
  const mark=f.mark(),events=batch(f).events;
  counts.push({mode,synthetic:true,actors:4,hook_pairs:4,retained:events.length,
   native_reads:reads,kinds:events.map(event=>event.kind)});
  return {f,mark,events};
 });
 assert.equal(modes[0].events.length,8);assert.equal(modes[1].events.length,2);
 assert.equal(counts[0].native_reads,counts[1].native_reads,'pruning adds no native reads');
 assert.deepEqual(modes[1].events.map(normalized),modes[0].events.filter(event=>event.source_character_uid===104).map(normalized));
 for(const key of ['error','active','skill_unknown','shield_unknown','unknown_sources','damage_settlement_scope','enemy_damage_unknown'])
  assert.deepEqual(plain(modes[0].mark[key]??null),plain(modes[1].mark[key]??null),key);
 const f=modes[1].f,event={...modes[1].events[0],source_character_uid:101};
 const proto=f.ptr(0).constructor.prototype,read=proto.readPointer;
 proto.readPointer=function(){throw Error('no additional native read allowed');};
 try{for(let index=0;index<1000;index++)assert.equal(f.adapter.eventCollectionOwner(event),'foreign');}
 finally{proto.readPointer=read;}
 assert.equal(vm.runInContext('nativeAutoOwners',modes[0].f.context),null,'all mode needs no pruning membership index');
 const owners=vm.runInContext('nativeAutoOwners.byUid',f.context);f.mark();
 assert.equal(vm.runInContext('nativeAutoOwners.byUid',f.context),owners,'unchanged full body reuses its local index');
});

check('same_scope_reorder_and_new_round_uid_reassignment_need_fresh_evidence',()=>{
 const f=owned();const prior=f.stamp();
 // Same CID and same UID can be rebound only through a fresh native context.
 const firstData=f.roleData[0],lastData=f.roleData[3];
 f.write(firstData.add(0x6c),104);f.write(lastData.add(0x6c),101);
 f.write(f.center.add(0x3c),2);f.mark();
 emit(f,'skill_start',101);emit(f,'skill_start',104);
 const events=batch(f).events;
 assert.equal(events.length,1);assert.equal(events[0].source_character_uid,101);
 assert.notEqual(events[0].native_autochess_scope.scope_key,prior.native_autochess_scope.scope_key);
 emit(f,'skill_start',101,{},'char_shared',prior);
 assert.equal(batch(f).events.length,2,'old-scope evidence cannot use a reassigned actor');
 // The player account text is never parsed numerically or stripped of zeros.
 const players=f.mark().native_autochess_context.players;
 assert.equal(players[0].uid,'091000001');assert.equal(players[1].uid,'91000001');
 assert.notEqual(players[0].uid,players[1].uid);
});

check('ordinary_native_and_mumu_event_queue_semantics_unchanged',()=>{
 for(const platform of ['native_pc','mumu']){
  const sourcePath=path.join(__dirname,'..',platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js');
  const f=fixture({platform,sourcePath,globals:{arkdpsAutoChessMode:'local'}});
  request(f,false);
  emit(f,'skill_start',101);emit(f,'damage',101,enemy);
  const events=f.context.rpc.exports.readEvents(0,2).events;
  assert.equal(events.length,2);assert(events.every(event=>event.native_autochess_scope===undefined));
  assert.equal(f.context.rpc.exports.ackEvents(2).acknowledged,2);
 }
});

process.stdout.write(JSON.stringify({status:'verified',checks,counts}));
