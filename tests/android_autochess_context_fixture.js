// Android field/type reflection and the real shared AutoChess producer.
// All memory, metadata API outputs and accounts below are owned synthetic data.
const assert=require('node:assert/strict');
const path=require('node:path');
const vm=require('node:vm');
const {world}=require('./native_pc_autochess_context_fixture.js');
const {fixture,LEGACY_ANDROID_FIXTURE_LAYOUT}=require('./native_pc_frida_probe_fixture.js');
const plain=value=>JSON.parse(JSON.stringify(value)),checks=[];
function check(name,run){run();checks.push(name);}
function owned(mode='all',options={}){
 const f=world({platform:'mumu',...options,fixtureOptions:{...options.fixtureOptions,
  globals:{...options.fixtureOptions?.globals,arkdpsAutoChessMode:mode}}});
 ['091000001','91000001','083000003','004000004'].forEach((uid,index)=>
  f.write(f.players[index][1].add(0x18),f.text(uid)));
 return f;
}
function field(f,object,name){
 let k=object.readPointer();
 for(let depth=0;depth<10&&!k.isNull();depth++){
  const table=k.add(0x80).readPointer(),count=k.add(0x124).readU16();
  for(let i=0;i<count;i++){const value=table.add(i*32);if(value.readPointer().readCString()===name)return value;}
  k=k.add(0x58).readPointer();
 }
 throw Error('missing owned field '+name);
}
function address(f,object,name){return object.add(field(f,object,name).add(24).readS32());}
function move(f,objects,name,offset){
 const metadata=field(f,objects[0],name),old=metadata.add(24).readS32();
 const kind=(metadata.add(8).readPointer().add(8).readU32()>>>16)&255;
 for(const object of objects){
  const value=[2,8,9,17].includes(kind)?object.add(old).readS32():object.add(old).readPointer();
  f.write(object.add(offset),value);f.write(object.add(old),[2,8,9,17].includes(kind)?-777:f.ptr(0));
 }
 f.write(metadata.add(24),offset);
}
function roles(f){return f.roles.flatMap(([,list])=>{
 const items=address(f,list,'_items').readPointer(),count=address(f,list,'_size').readS32();
 return Array.from({length:count},(_,i)=>items.add(0x20+i*8).readPointer());
});}
function literal(f,name){
 const table=f.phaseEnum.add(0x80).readPointer();
 for(let i=0;i<16;i++){const value=table.add(i*32);if(value.readPointer().readCString()===name)return value;}
 throw Error('missing owned enum literal');
}
function request(f,enabled){
 f.requests.get('arkdps-event-request')({operation:'mark',id:1,autochess_enabled:enabled});
 return f.messages.at(-1).result;
}
function emit(f,kind,uid=101,evidence={},source='char_shared',when=null){
 return f.context.record(source,kind,12,when||f.context.time(),{method:'owned Android'},evidence,uid);
}
function packet(f){return f.mark().native_autochess_context;}
function assertUnknown(f){
 const context=packet(f);assert.equal(context.status,'unknown');assert.equal(context.verified_same_frame,false);
 assert.equal(context.phase,undefined);assert.equal(context.round,undefined);return context;
}
// This current-layout witness matches the generated Android bundle, not the
// historical default constants. The primary host integration passes its own
// effective production layout through --effective-layout instead of this copy.
const CURRENT_ANDROID_LAYOUT=Object.freeze({native:16,nativeOwner:40,logger:224,loggerOwner:24,
 stats:48,level:352,state:552,levelId:24});
function configuredWorld(layout,mode='all',sourceLayout=layout,options={}){
 const f=owned(mode,{fixtureOptions:{...options,androidBattleLayout:layout,unitManagerOffset:0x2c0,
  globals:{arkdpsAndroidBattleLayout:sourceLayout}}});
 const list=f.alloc();f.write(list,f.klass('ListDict`2','Torappu',0));
 if(layout.logger!==LEGACY_ANDROID_FIXTURE_LAYOUT.logger)f.write(f.controller.add(LEGACY_ANDROID_FIXTURE_LAYOUT.logger),list);
 if(layout.level!==LEGACY_ANDROID_FIXTURE_LAYOUT.level)f.write(f.controller.add(LEGACY_ANDROID_FIXTURE_LAYOUT.level),list);
 if(layout.state!==LEGACY_ANDROID_FIXTURE_LAYOUT.state)f.write(f.controller.add(LEGACY_ANDROID_FIXTURE_LAYOUT.state),1);
 return f;
}
function independentSample(f,layout){
 const native=f.controller.add(layout.native).readPointer(),logger=f.controller.add(layout.logger).readPointer(),
  stats=logger.add(layout.stats).readPointer(),level=f.controller.add(layout.level).readPointer();
 const clock=f.controllerClass.add(0xb8).readPointer(),frame=clock.add(0x14).readU32(),
  fixed=clock.add(0x18).readS64().toString(),stage=level.add(layout.levelId).readPointer();
 const session=['owned-fixture-boot',567,f.context.Process.id,Number(f.controller.value),Number(stats.value),Number(level.value)].join(':');
 return {sample:{session,native_session:session,frame,game_time:Number(BigInt(fixed))/4294967296,
  battle_identity:[Number(f.controller.value),Number(native.value)],observer_logger:Number(logger.value),
  state:f.controller.add(layout.state).readS32(),rows:[],guard:{complete:true,start:frame,end:frame},
  battle_metadata:{platform:'android',source_build:'android:2.7.91',
   stage_id:stage.add(0x14).readUtf16String(stage.add(0x10).readS32())}},
  fixture_roots:{controller:f.controller.toString(),native:native.toString(),logger:logger.toString(),
   stats:stats.toString(),level:level.toString()}};
}
if(process.argv[2]==='--effective-layout'){
 const layout=JSON.parse(process.argv[3]),mode=process.argv.includes('--mode')?process.argv[process.argv.indexOf('--mode')+1]:'all';
 assert(['all','local'].includes(mode));const f=configuredWorld(layout,mode);
 const before=f.mark(),after=f.mark(),drain=f.context.rpc.exports.readEvents(0,0),independent=independentSample(f,layout);
 assert.equal(before.native_autochess_context.status,'available');assert.equal(before.native_autochess_context.collection_mode,mode);
 assert.deepEqual(plain(before.native_identity),independent.fixture_roots);assert.equal(drain.events.length,0);
 process.stdout.write(JSON.stringify({status:'verified',classification:'owned_effective_layout_not_live',
  effective_layout:layout,...independent,before,after,drain}));process.exit(0);
}

check('current_effective_android_layout_uses_e0_160_228_and_rejects_legacy_listdict_slot',()=>{
 const f=configuredWorld(CURRENT_ANDROID_LAYOUT),p=packet(f);
 assert.equal(p.status,'available');assert.equal(p.phase,4);assert.equal(p.round,1);
 assert.equal(address(f,f.controller,'m_logger').toString(),f.controller.add(0xe0).toString());
 assert.equal(address(f,f.controller,'m_levelData').toString(),f.controller.add(0x160).toString());
 assert.equal(address(f,f.controller,'m_state').toString(),f.controller.add(0x228).toString());
 assert.equal(f.controller.add(0x220).readS32(),1,'the legacy slot contains a state mismatch sentinel');
 assert.equal(f.context.className(f.controller.add(0x158).readPointer().readPointer()),'ListDict`2');
 assert.equal(address(f,f.controller,'<unitManager>k__BackingField').toString(),f.controller.add(0x2c0).toString());
 assert.equal(p.players.length,4);assert.equal(p.memberships.length,4);assert.equal(p.self_player_index,4);
 assert.equal(p.match_key,f.manager.toString()+':'+f.center.toString());
 assert.equal(p.players[0].uid,'091000001');assert.equal(p.players[1].uid,'91000001');
 const mark=f.mark();assert.deepEqual(Object.keys(mark.native_identity),['controller','native','logger','stats','level']);
 assert.equal(mark.native_frame,0);assert.equal(mark.native_fixed_time,'0');
 assert(/AUTOCHESS/.test(mark.native_stage_id));
 const constants=f.apiCalls.filter(name=>name==='il2cpp_field_static_get_value').length;
 assert.equal(constants,15,'only the fifteen proved enum literal defaults were read');
 packet(f);assert.equal(f.apiCalls.filter(name=>name==='il2cpp_field_static_get_value').length,15,'immutable enum metadata cached');
 const old=configuredWorld(CURRENT_ANDROID_LAYOUT,'all',LEGACY_ANDROID_FIXTURE_LAYOUT),failure=old.mark();
 assert.equal(failure.native_identity,undefined);assert.equal(failure.native_autochess_context,undefined);
 assert.equal(failure.autochess_binding_diagnostic.guard,'logger_class_mismatch');
 assert.match(failure.autochess_binding_diagnostic.reason,/ListDict`2/);
 // Keep the configured current layout's runtime owner checks fully effective.
 const logger=f.controller.add(CURRENT_ANDROID_LAYOUT.logger).readPointer();
 f.write(logger.add(CURRENT_ANDROID_LAYOUT.loggerOwner),f.ptr(0));const broken=f.mark();
 assert.equal(broken.autochess_binding_diagnostic.guard,'logger_owner_mismatch');assert.equal(broken.native_identity,undefined);
 f.write(logger.add(CURRENT_ANDROID_LAYOUT.loggerOwner),f.controller);assert.equal(packet(f).status,'available');
});

check('optional_android_view_mode_is_typed_fresh_and_never_identity',()=>{
 const stepField='<inStepMode>k__BackingField',viewField='<enableViewMultiBattle>k__BackingField';
 function withViews(view){return configuredWorld(CURRENT_ANDROID_LAYOUT,'all',CURRENT_ANDROID_LAYOUT,{autoChessViewMode:view});}
 for(const step of [false,true])for(const multi of [false,true]){
  const expected={in_step_mode:step,enable_view_multi_battle:multi},f=withViews(expected),first=f.mark();
  assert.equal(first.native_autochess_context.status,'available');assert.deepEqual(plain(first.autochess_view_mode),expected);
  const scope=first.native_autochess_context.scope_key,changed={in_step_mode:!step,enable_view_multi_battle:!multi};
  f.write(address(f,f.manager,stepField),Number(changed.in_step_mode));
  f.write(address(f,f.manager,viewField),Number(changed.enable_view_multi_battle));
  const read=f.context.rpc.exports.readEvents(0,0);
  assert.deepEqual(plain(read.autochess_view_mode),changed,'bounded read observes fresh Boolean values');
  assert.deepEqual(plain(read.events),[]);assert.equal(read.native_autochess_context.status,'available');
  const mark=f.mark();assert.deepEqual(plain(mark.autochess_view_mode),changed,'mark cannot reuse parsed old Boolean values');
  assert.equal(mark.native_autochess_context.scope_key,scope,'optional presentation capability is not battle identity');
  assert.deepEqual(plain(first.autochess_view_mode),expected,'prior DTO remains owned');
 }
 for(const view of [undefined,{in_step_mode:false},{enable_view_multi_battle:true}]){
  const f=withViews(view),mark=f.mark();assert.equal(mark.native_autochess_context.status,'available');
  assert.equal(mark.autochess_view_mode,undefined);assert.equal(mark.autochess_binding_diagnostic,undefined);
 }
 for(const mutate of [
  f=>f.write(field(f,f.manager,stepField).add(8).readPointer().add(8),8<<16),
  f=>f.write(field(f,f.manager,viewField).add(8).readPointer().add(8),(2<<16)|16),
  f=>f.write(field(f,f.manager,stepField).add(16),f.controllerClass),
  f=>f.write(field(f,f.manager,viewField),f.text('renamed_view_mode')),
  f=>f.write(address(f,f.manager,stepField),2),
 ]){
  const f=withViews({in_step_mode:false,enable_view_multi_battle:true});mutate(f);const mark=f.mark();
  assert.equal(mark.native_autochess_context.status,'available');assert.equal(mark.autochess_view_mode,undefined);
  assert.equal(mark.autochess_binding_diagnostic,undefined,'an optional bad field cannot reject valid combat identity');
 }
 const changing=withViews({in_step_mode:false,enable_view_multi_battle:true}),target=address(changing,changing.manager,stepField);
 const proto=changing.ptr(0).constructor.prototype,read=proto.readU8;let reads=0;
 proto.readU8=function(){if(this.equals(target)&&++reads===2)changing.write(target,1);return read.call(this);};
 let raced;try{raced=changing.mark();}finally{proto.readU8=read;}
 assert.equal(reads,2);assert.equal(raced.native_autochess_context.status,'available');
 assert.equal(raced.autochess_view_mode,undefined,'a changing optional pair never becomes a false/zero capability');
 assert.deepEqual(plain(changing.mark().autochess_view_mode),{in_step_mode:true,enable_view_multi_battle:true});
 const pc=world();assert.equal(pc.mark().autochess_view_mode,undefined);
});

check('historic_compatibility_layout_can_explicitly_differ_from_dump_metadata',()=>{
 const f=owned('all',{fixtureOptions:{loggerMetadataOffset:0xe0,stateMetadataOffset:0x228,stateMetadataKind:17}});
 const ready=f.messages.find(message=>message.ready===true);
 assert.equal(ready.terminal_error,'Error: unsupported layout BattleController.m_logger',
  'the observed optional terminal setup failure remains unchanged');
 assert.equal(field(f,f.controller,'m_logger').add(24).readS32(),0xe0);
 assert.equal(field(f,f.controller,'m_state').add(24).readS32(),0x228);
 f.write(field(f,f.level,'levelId').add(24),0x28); // Conflicting optional dump metadata, runtime remains verified +0x18.
 f.write(f.controller.add(0x228),1); // Actual Android speed, not battle state.
 const mark=f.mark();assert.equal(mark.native_autochess_context.status,'available');
 assert.equal(mark.native_identity.logger,f.controller.add(0xd8).readPointer().toString());
 assert.equal(mark.native_autochess_context.phase,4);assert.equal(mark.autochess_binding_diagnostic,undefined);
 assert.equal(f.controller.add(0x220).readS32(),2);
});

check('wrong_runtime_owner_backreference_class_or_state_never_borrow_metadata_roots',()=>{
 function replace(f,address,value,scalar=false){
  const previous=scalar?address.readS32():address.readPointer();f.write(address,value);
  return ()=>f.write(address,previous);
 }
 const cases=[
  ['controller_class_unavailable',f=>{
   f.context.guardFixtureClass=f.controllerClass;vm.runInContext('nativeControllerClass=null',f.context);
   return ()=>vm.runInContext('nativeControllerClass=guardFixtureClass;delete globalThis.guardFixtureClass',f.context);
  }],
  ['singleton_parent_missing',f=>replace(f,f.controllerClass.add(0x58),f.ptr(0))],
  ['singleton_statics_missing',f=>replace(f,f.controllerClass.add(0x58).readPointer().add(0xb8),f.ptr(0))],
  ['singleton_empty',f=>replace(f,f.controllerClass.add(0x58).readPointer().add(0xb8).readPointer(),f.ptr(0))],
  ['controller_class_mismatch',f=>replace(f,f.controller,f.center.readPointer())],
  ['native_missing',f=>replace(f,f.controller.add(0x10),f.ptr(0))],
  ['native_owner_mismatch',f=>replace(f,f.controller.add(0x10).readPointer().add(0x28),f.ptr(0))],
  ['logger_missing',f=>replace(f,f.controller.add(0xd8),f.ptr(0))],
  ['logger_class_mismatch',f=>replace(f,f.controller.add(0xd8).readPointer(),f.controllerClass)],
  ['logger_owner_mismatch',f=>replace(f,f.controller.add(0xd8).readPointer().add(0x18),f.ptr(0))],
  ['level_missing',f=>replace(f,f.controller.add(0x158),f.ptr(0))],
  ['level_class_mismatch',f=>replace(f,f.level,f.controllerClass)],
  ['stats_missing',f=>replace(f,f.controller.add(0xd8).readPointer().add(0x30),f.ptr(0))],
  ['stats_class_mismatch',f=>replace(f,f.controller.add(0xd8).readPointer().add(0x30).readPointer(),f.controllerClass)],
  [null,f=>replace(f,f.controller.add(0x220),1,true)],
 ];
 for(const [guard,mutate]of cases){
  const f=owned('all',{fixtureOptions:{loggerMetadataOffset:0xe0,stateMetadataOffset:0x228,stateMetadataKind:17}});
  const original=plain(packet(f));assert.equal(original.status,'available');
  const queue=plain(vm.runInContext('({sequence,acknowledgedSequence,queue})',f.context));
  const restore=mutate(f);const mark=f.mark();
  assert.equal(mark.retry,true);assert.equal(mark.native_identity,undefined);assert.equal(mark.native_autochess_context,undefined);
  assert.equal(mark.error,null);assert.equal(mark.stopped,false);
  const diagnostic=mark.autochess_binding_diagnostic;
  assert.equal(diagnostic.status,'unavailable');assert.equal(diagnostic.step,guard?'root_identity':'controller_state');
  assert.equal(diagnostic.guard,guard??undefined,guard||'state has its own diagnostic step');
  assert(diagnostic.reason.length>0&&diagnostic.reason.length<=300);
  assert.deepEqual(plain(vm.runInContext('({sequence,acknowledgedSequence,queue})',f.context)),queue);
  restore();const recovered=f.mark();
  assert.equal(recovered.retry,undefined);assert.equal(recovered.native_autochess_context.status,'available');
  assert.equal(recovered.native_autochess_context.match_key,original.match_key);
  assert.deepEqual(plain(recovered.native_autochess_context.memberships),original.memberships);
  assert.equal(recovered.autochess_binding_diagnostic,undefined,'a successful recovery cannot carry an older guard failure');
 }
 // Both owner links must be rechecked at the closing boundary too. Do not
 // rely on the opening singleton or let an acquired body certify a new root.
 for(const guard of ['native_owner_mismatch','logger_owner_mismatch']){
  const f=owned();assert.equal(packet(f).status,'available');
  const target=guard==='native_owner_mismatch'?f.controller.add(0x10).readPointer().add(0x28):
   f.controller.add(0xd8).readPointer().add(0x18);
  const previous=target.readPointer(),prototype=f.ptr(0).constructor.prototype,read=prototype.readPointer;let calls=0;
  prototype.readPointer=function(){if(this.equals(target)&&++calls===2)f.write(target,f.ptr(0));return read.call(this);};
  let envelope;try{envelope=f.adapter.readEnvelope();}finally{prototype.readPointer=read;}
  assert.equal(calls,2);assert.equal(envelope.ready,false);assert.equal(envelope.fields.native_identity,undefined);
  assert.equal(envelope.fields.native_autochess_context,undefined);
  assert.equal(envelope.fields.autochess_binding_diagnostic.step,'closing_guard');
  assert.equal(envelope.fields.autochess_binding_diagnostic.guard,guard);
  f.write(target,previous);assert.equal(packet(f).status,'available');
 }
});

check('runtime_stage_class_failure_is_diagnosed_without_fabricating_phase_or_weakening_body',()=>{
 const f=owned();f.write(f.level.add(0x18).readPointer(),f.controllerClass);
 const mark=f.mark();assert.equal(mark.native_identity,undefined);assert.equal(mark.native_autochess_context,undefined);
 assert.equal(mark.autochess_binding_diagnostic.step,'stage_metadata');
 assert.match(mark.autochess_binding_diagnostic.reason,/stage identifier is not System.String/);
 const body=owned();body.write(field(body,body.manager,'dataCenter').add(8).readPointer().add(8),8<<16);
 const failed=body.mark();assert(failed.native_identity);assert.equal(failed.native_autochess_context.status,'unknown');
 assert.match(failed.native_autochess_context.reason,/AutoChess field type AutoChessGameManager.dataCenter/);
});

check('actual_reflected_non_pc_field_offsets_and_noncontiguous_scope_guards',()=>{
 const f=owned('local');
 move(f,[f.controller],'m_singletonHost',0x2e0);
 move(f,[f.manager],'gameStatus',0x58);move(f,[f.manager],'dataCenter',0x78);
 move(f,[f.machine],'<gameCurrentStateId>k__BackingField',0x74);
 move(f,[f.center],'<selfPlayerIndex>k__BackingField',0x44);move(f,[f.center],'<currentRound>k__BackingField',0x4c);
 move(f,[f.center],'battleData',0x108);move(f,[f.center],'playerDataModel',0x118);
 move(f,[f.playerList],'_items',0x30);move(f,[f.playerList],'_size',0x38);move(f,[f.playerList],'_version',0x3c);
 const accounts=f.players.map(([,player])=>player);
 move(f,accounts,'index',0x20);move(f,accounts,'uid',0x28);move(f,accounts,'playerCard',0x50);
 const chess=roles(f);move(f,chess,'playerIndex',0x60);move(f,chess,'side',0x68);move(f,chess,'battleCharacterData',0x70);
 move(f,f.roleData,'prefabKey',0xb8);
 const p=packet(f);assert.equal(p.status,'available');assert.equal(p.phase,4);assert.equal(p.memberships.length,4);
 let stamp=f.stamp();assert.equal(stamp.native_autochess_scope.status,'available');
 assert.equal(stamp.native_autochess_scope.collection_mode,'local');
 emit(f,'skill_start',101);emit(f,'skill_start',104);assert.equal(f.mark().sequence,1);
 // Closing per-field guards follow the actual Android layout, not PC bulk offsets.
 f.write(address(f,accounts[0],'uid'),f.text('009999999'));
 stamp=f.stamp();assert.equal(stamp.native_autochess_scope.status,'unknown');
});

check('phase_enum_names_literal_type_value_and_export_fail_closed',()=>{
 for(const mutate of [
  f=>f.enumValues.set(literal(f,'HELP_BATTLE').toString(),6),
  f=>f.write(literal(f,'BATTLE'),f.text('FAKE_BATTLE')),
  f=>f.write(literal(f,'BATTLE').add(16),f.controllerClass),
  f=>f.write(literal(f,'BATTLE').add(8).readPointer().add(8),(17<<16)|0x10),
  f=>f.write(literal(f,'BATTLE').add(8).readPointer(),f.controllerClass),
  f=>{delete f.metadataExports.il2cpp_field_static_get_value;},
  f=>{delete f.metadataExports.il2cpp_class_from_type;},
 ]){
  const f=owned();mutate(f);assertUnknown(f);
 }
 const f=owned();f.write(literal(f,'BATTLE').add(8).readPointer().add(8),(17<<16)|0x10);
 assertUnknown(f);assert.equal(f.apiCalls.filter(name=>name==='il2cpp_field_static_get_value').length,0,
  'a nonliteral must fail before any static value API can be called');
 assert(!f.apiCalls.some(name=>/runtime_class_init|runtime_invoke|object_new/.test(name)));
});

check('unsupported_class_field_type_container_stride_and_short_members_are_unknown',()=>{
 for(const mutate of [
  f=>f.write(field(f,f.controller,'m_singletonHost').add(16),f.center.readPointer()),
  f=>f.write(field(f,f.manager,'dataCenter').add(8).readPointer().add(8),8<<16),
  f=>f.write(field(f,f.manager,'gameStatus'),f.text('renamedStatus')),
  f=>{
   const entries=address(f,f.dict,'_entries').readPointer(),generic=field(f,f.dict,'_entries').add(8).readPointer().readPointer().readPointer();
   const entryClass=generic.add(24).readPointer(),table=entryClass.add(0x80).readPointer();
   f.write(table.add(3*32+24),0x28);assert(entries);
  },
 ]){const f=owned();mutate(f);assertUnknown(f);}
 const partial=owned();partial.write(partial.roleData[0].add(0x6c),0);
 const p=packet(partial);assert.equal(p.status,'available');assert.equal(p.memberships.length,3);
 assert.equal(p.unknown_memberships.length,1);assert.equal(p.unknown_memberships[0].candidate_player_index,1);
});

check('help_player_index_and_side_are_distinct_and_modes_latch_only_next_verified_scope',()=>{
 const f=owned('local');f.write(address(f,f.center,'<selfPlayerIndex>k__BackingField'),3);
 f.write(address(f,roles(f)[2],'side'),1);
 let p=packet(f);assert.equal(p.collection_mode,'local');
 assert.equal(p.memberships.find(row=>row.data_uid===103).player_side,1);
 assert.equal(p.memberships.find(row=>row.data_uid===103).player_index,3);
 assert.equal(request(f,true).native_autochess_context.collection_mode,'local');
 f.write(address(f,f.machine,'<gameCurrentStateId>k__BackingField'),5);
 p=packet(f);assert.equal(p.phase,5);assert.equal(p.collection_mode,'all');
 request(f,false);assert.equal(packet(f).collection_mode,'all');
 f.write(address(f,f.machine,'<gameCurrentStateId>k__BackingField'),6);
 assert.equal(packet(f).collection_mode,'local');
 request(f,true);
 for(const phase of [7,8,3]){
  f.write(address(f,f.machine,'<gameCurrentStateId>k__BackingField'),phase);
  assert.equal(packet(f).collection_mode,'local');
 }
 f.write(address(f,f.machine,'<gameCurrentStateId>k__BackingField'),4);
 f.write(address(f,f.center,'<currentRound>k__BackingField'),2);
 assert.equal(packet(f).collection_mode,'all');
});

check('event_original_scope_clock_proof_local_pruning_and_single_ack_retained',()=>{
 const f=owned('local');packet(f);const before=f.stamp();
 emit(f,'skill_start',101);emit(f,'skill_start',104);
 for(const kind of ['damage_settlement','elemental_bar','elemental_burst','healing','shield_absorbed'])
  emit(f,kind,101,{target:'enemy_fixture',target_uid:'77',target_instance:'0xbeef',damage_type:1});
 const mark=f.mark(),batch=plain(f.context.rpc.exports.readEvents(0,mark.sequence));
 assert.equal(batch.events.length,6);assert.deepEqual(batch.events.map(event=>event.sequence),[1,2,3,4,5,6]);
 assert(batch.events.every(event=>event.native_autochess_scope.collection_mode==='local'));
 assert.equal(batch.events[0].source_character_uid,104);assert.equal(batch.events[0].amount,12);
 assert.equal(f.context.rpc.exports.ackEvents(6).acknowledged,6);
 f.write(address(f,f.machine,'<gameCurrentStateId>k__BackingField'),5);f.clock(30,4294967296n);packet(f);
 emit(f,'enemy_hp',undefined,{target:'enemy_fixture',target_instance:'77',hp_after:80},null,before);
 const tail=f.context.rpc.exports.readEvents(6,7).events[0];
 assert.equal(tail.frame,0);assert.equal(tail.game_time,0);assert.equal(tail.native_autochess_scope.phase,4);
 assert.equal(tail.native_autochess_scope.scope_key,before.native_autochess_scope.scope_key);
 assert.equal(tail.native_autochess_scope.collection_mode,'local');
});

check('body_clock_root_and_closing_owner_guards_remain_mandatory',()=>{
 const crossing=owned(),proto=crossing.ptr(0).constructor.prototype,readU32=proto.readU32;
 let changed=false;
 proto.readU32=function(){if(!changed&&this.equals(crossing.roleData[0].add(0x6c))){changed=true;crossing.clock(1,143165576n);}return readU32.call(this);};
 let result;try{result=crossing.mark();}finally{proto.readU32=readU32;}
 assert.equal(result.retry,true);assert.equal(result.native_autochess_context,undefined);
 const f=owned();packet(f);const owner=f.players[0][1],uid=address(f,owner,'uid');
 const p=f.ptr(0).constructor.prototype,read=p.readPointer;let reads=0;
 p.readPointer=function(){if(this.equals(uid)&&++reads===2)f.write(uid,f.text('009999999'));return read.call(this);};
 let stamp;try{stamp=f.stamp();}finally{p.readPointer=read;}
 assert.equal(stamp.native_autochess_scope.status,'unknown');
 const bad=owned();packet(bad);bad.write(bad.controller.add(0x158),bad.ptr(0));
 assert.equal(bad.stamp().native_autochess_scope.status,'unknown');
});

check('match_key_survives_child_controller_rotation_but_not_manager_replacement',()=>{
 const f=owned(),old=packet(f),oldController=f.controller,newController=f.alloc(),newNative=f.alloc(),
  oldLogger=oldController.add(0xd8).readPointer(),logger=f.alloc(),stats=f.alloc(),level=f.alloc();
 f.write(newController,f.controllerClass);f.write(newController.add(0x10),newNative);f.write(newNative.add(0x28),newController);
 f.write(newController.add(0x220),2);f.write(newController.add(0xd8),logger);f.write(newController.add(0x158),level);
 f.write(logger,oldLogger.readPointer());f.write(logger.add(0x18),newController);
 f.write(logger.add(0x30),stats);f.write(stats,oldLogger.add(0x30).readPointer().readPointer());
 f.write(level,f.level.readPointer());f.write(level.add(0x18),f.level.add(0x18).readPointer());
 f.write(newController.add(0x2e8),oldController.add(0x2e8).readPointer());
 f.write(newController.add(0x2b8),oldController.add(0x2b8).readPointer());
 f.write(f.controllerClass.add(0x58).readPointer().add(0xb8).readPointer(),newController);
 const next=packet(f);assert.equal(next.status,'available');assert.notEqual(next.scope_key,old.scope_key);
 assert.equal(next.match_key,old.match_key,'parent match references are independent of the five child roots');
 const manager=f.alloc();f.write(manager,f.manager.readPointer());
 f.write(address(f,manager,'gameStatus'),address(f,f.manager,'gameStatus').readPointer());
 f.write(address(f,manager,'dataCenter'),address(f,f.manager,'dataCenter').readPointer());
 const entries=address(f,f.dict,'_entries').readPointer();f.write(entries.add(0x30),manager);
 const newMatch=packet(f);assert.equal(newMatch.status,'available');assert.notEqual(newMatch.match_key,old.match_key);
});

check('ordinary_android_does_not_require_autochess_metadata_or_change_legacy_payload',()=>{
 const f=fixture({platform:'mumu',sourcePath:path.join(__dirname,'..','event_probe_android2791.js')});
 assert.equal(f.messages.at(-1).ready,true);const mark=f.context.rpc.exports.mark();
 assert.equal(mark.native_autochess_context,undefined);assert.equal(mark.native_identity,undefined);assert.equal(mark.retry,undefined);
 assert(!f.apiCalls.includes('il2cpp_field_static_get_value'));
 emit(f,'damage',101,{target:'enemy_fixture',target_type:0,damage_type:1});
 const event=f.context.rpc.exports.readEvents(0,1).events[0];
 assert.equal(event.native_autochess_scope,undefined);assert.equal(event.amount,12);
});

process.stdout.write(JSON.stringify({status:'verified',classification:'owned_metadata_and_memory_not_live',checks}));
