// Actual producer callbacks with owned Token -> Character metadata. No game.
const assert=require('node:assert/strict');
const path=require('node:path');
const {world}=require('./native_pc_autochess_context_fixture.js');
const {fixture}=require('./native_pc_frida_probe_fixture.js');
const plain=value=>JSON.parse(JSON.stringify(value)),FP=4294967296n;
const identities={token_cid:'token_10027_ironmn_pile3',host_cid:'char_4072_ironmn',
 token_uid:70027,host_uid:74072,peer_token_uid:70028,peer_host_uid:74073};
const androidLayout={native:16,nativeOwner:40,logger:224,loggerOwner:24,stats:48,level:352,state:552,levelId:24};

function derivedFactory(f){
 const parents=new Map(),original=f.metadataExports.il2cpp_class_is_assignable_from;
 function klass(name,parent){
  const k=f.alloc();f.write(k.add(0x10),f.text(name));f.write(k.add(0x18),f.text('Torappu.Battle'));
  f.write(k.add(0x58),parent);parents.set(k.toString(),parent);return k;
 }
 // Extend the owned stand-in for this existing metadata API to honor the
 // extra real inheritance edges declared by this fixture. No event/quality
 // result or data UID is supplied to the producer by this stand-in.
 f.metadataExports.il2cpp_class_is_assignable_from=(base,candidate)=>{
  const seen=new Set();let current=candidate;
  for(let depth=0;depth<16&&!current.isNull();depth++){
   if(original(base,current))return true;
   if(seen.has(current.toString())||!parents.has(current.toString()))return false;
   seen.add(current.toString());current=parents.get(current.toString());
  }
  return false;
 };
 return klass;
}
function damage(f,source,target,amount,before){
 const modifier=f.modifier(source,target,amount,0,1),apply=f.callback('ApplyModifier',f.entity),take=f.callback('OnTakeDamage',f.unitClass);
 const outer={threadId:1},inner={threadId:1};f.write(target.add(0x40),BigInt(before)*FP);
 apply.onEnter.call(outer,[target,modifier,f.ptr(0)]);take.onEnter.call(inner,[target,modifier,f.ptr(0)]);
 f.write(modifier.add(0x48),-BigInt(amount)*FP);f.write(target.add(0x40),BigInt(before-amount)*FP);
 take.onLeave.call(inner);apply.onLeave.call(outer);
}
function skill(f,source,name){
 const hook=f.callback(name,f.character),call={threadId:1};hook.onEnter.call(call,[source]);hook.onLeave.call(call);
}
function sourceUnit(f,klass,uid,nativeUid){
 const source=f.unit(identities.token_cid,nativeUid),data=source.add(0x538).readPointer();
 f.write(source,klass);f.write(data.add(0x6c),uid);
 const sk=f.alloc(),info=f.alloc();f.write(sk,f.basicSkill);f.write(info,f.skillData);
 f.write(source.add(0x3c8),sk);f.write(sk.add(0xb0),info);
 f.write(info.add(0x50),f.managedString('owned_ironmn_pile_skill'));
 f.write(info.add(0x18),f.managedString('owned_ironmn_pile_skill'));
 f.write(info.add(0x10),f.managedString('owned summon skill'));
 return source;
}
function positive(platform){
 const options={settlement:true,...(platform==='mumu'?{androidBattleLayout:androidLayout,unitManagerOffset:0x2c0,
  globals:{arkdpsAndroidBattleLayout:androidLayout}}:{})};
 const f=world({platform,fixtureOptions:options}),klass=derivedFactory(f),tokenClass=klass('Token',f.character);
 ['091000001','002000002','083000003','004000004'].forEach((uid,i)=>f.write(f.players[i][1].add(0x18),f.text(uid)));
 f.write(f.center.add(0x38),1);
 const pairs=f.roleList.add(0x10).readPointer();
 for(let i=0;i<2;i++){
  const hostUid=i?identities.peer_host_uid:identities.host_uid,tokenUid=i?identities.peer_token_uid:identities.token_uid;
  f.write(f.roleData[i].add(0x50),f.text(identities.host_cid));f.write(f.roleData[i].add(0x6c),hostUid);
  const list=f.roles[i][1],host=list.add(0x10).readPointer().add(0x20).readPointer();
  const token=f.alloc();f.write(token,host.readPointer());f.write(token.add(0x30),i+1);f.write(token.add(0x40),i+1);
  f.write(token.add(0x48),f.data(identities.token_cid,tokenUid,{isToken:true,hostKey:identities.host_cid,hostUid}));
  f.write(pairs.add(0x28+i*16),f.list([host,token],'ChessInst','Torappu.Battle.AutoChess'));
 }
 const before=f.mark(),baseline=f.context.rpc.exports.readEvents(0,0);
 assert.equal(before.native_autochess_context.status,'available');
 assert.equal(before.native_autochess_context.collection_mode,platform==='native_pc'?'all':'local');
 assert.equal(before.native_autochess_context.memberships.filter(row=>row.cid===identities.token_cid).length,2);
 f.clock(30,FP);f.mark();
 const sources=[sourceUnit(f,tokenClass,identities.token_uid,27),sourceUnit(f,tokenClass,identities.peer_token_uid,28)];
 for(let i=0;i<2;i++){
  const target=f.unit('enemy_owned_target',77+i);f.write(target,f.enemyClass);
  skill(f,sources[i],'OnSkillStart');damage(f,sources[i],target,i?10:40,i?80:50);skill(f,sources[i],'OnSkillFinish');
 }
 // The same summon spending HP on itself is still outside enemy settlement.
 damage(f,sources[0],sources[0],40,50);
 const mark=f.mark(),batch=plain(f.context.rpc.exports.readEvents(0,mark.sequence));
 const settlements=batch.events.filter(event=>event.kind==='damage_settlement');
 assert.equal(batch.enemy_damage_ready,true);assert.equal(batch.enemy_damage_unknown,false);
 assert.equal(settlements.length,2);assert.deepEqual(settlements.map(event=>event.source_character_uid),[70027,70028]);
 assert.deepEqual(settlements.map(event=>[event.amount,event.nominal_amount,event.shield_absorbed,event.settlement_complete]),
  [[40,40,0,true],[10,10,0,true]]);
 assert(settlements.every(event=>event.source===identities.token_cid&&event.target==='enemy_owned_target'));
 const actual=batch.events.filter(event=>event.kind==='damage'&&event.target==='enemy_owned_target');
 assert.deepEqual(actual.map(event=>[event.amount,event.real_delta,event.hp_before,event.hp_after]),
  platform==='native_pc'?[[40,-40,50,10],[10,-10,80,70]]:[[40,-40,50,10]]);
 const self=batch.events.filter(event=>event.kind==='damage'&&event.target===identities.token_cid);
 assert.equal(self.length,1);assert.equal(self[0].amount,40);assert.equal(self[0].source_character_uid,70027);
 const skillEvents=batch.events.filter(event=>event.kind==='skill_start'||event.kind==='skill_end');
 assert.equal(skillEvents.length,platform==='native_pc'?4:2);
 assert(skillEvents.every(event=>event.source===identities.token_cid&&[70027,70028].includes(event.source_character_uid)));
 if(platform==='mumu')assert(skillEvents.every(event=>event.source_character_uid===70027),
  'local defense can omit foreign skill and legacy rows; the two shared settlements above retain both actor UIDs');
 assert(!skillEvents.some(event=>event.source===identities.host_cid),'the producer never invents a host activation for a token skill');
 assert.deepEqual(batch.events.map(event=>event.sequence),Array.from({length:batch.events.length},(_,i)=>i+1));
 return {before:plain(before),baseline:plain(baseline),mark:plain(mark),batch,settlement:settlements[0],settlements,
  skill_events:skillEvents,identities};
}
function negatives(platform){
 const output={};
 for(const name of ['exact_enemy','unrelated_class','wrong_data_class','missing_data','zero_uid','class_cycle','truncated_ancestry']){
  const f=fixture({platform,settlement:true,sourcePath:path.join(__dirname,'..',
   platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js')});
  const klass=derivedFactory(f),token=klass('Token',f.character),source=sourceUnit(f,token,70027,27);
  if(name==='exact_enemy')f.write(source,f.enemyClass);
  if(name==='unrelated_class')f.write(source,klass('Unrelated',f.ptr(0)));
  if(name==='wrong_data_class')f.write(source.add(0x538).readPointer(),f.enemyClass);
  if(name==='missing_data')f.write(source.add(0x538),f.ptr(0));
  if(name==='zero_uid')f.write(source.add(0x538).readPointer().add(0x6c),0);
  if(name==='class_cycle'){const loop=klass('Loop',f.ptr(0));f.write(loop.add(0x58),loop);f.write(source,loop);}
  if(name==='truncated_ancestry'){let parent=f.character;for(let i=0;i<11;i++)parent=klass('Deep'+i,parent);f.write(source,parent);}
  assert.equal(f.context.sourceCharacterUid(source,identities.token_cid),undefined,name);
  f.clock(30,FP);const target=f.unit('enemy_owned_target',77);f.write(target,f.enemyClass);damage(f,source,target,40,50);
  const mark=f.context.rpc.exports.mark(),batch=f.context.rpc.exports.readEvents(0,mark.sequence);
  assert(batch.events.length>0,name+' must not discard its original source observation');
  assert(batch.events.every(event=>!Object.hasOwn(event,'source_character_uid')),name+' cannot receive a guessed actor UID');
  output[name]={uid:null,events:batch.events.length,settlements:batch.events.filter(event=>event.kind==='damage_settlement').length};
 }
 return output;
}
function ephemeral(platform){
 const f=world({platform,fixtureOptions:{settlement:true,...(platform==='mumu'?{
  androidBattleLayout:androidLayout,unitManagerOffset:0x2c0,globals:{arkdpsAndroidBattleLayout:androidLayout}}:{})}});
 for(const [index, player] of f.players)f.write(player.add(0x18),f.text(String(910000000+index)));
 f.klass('BObject','Torappu.Battle',9,[[2,'<instanceUid>k__BackingField',0x18,9],
  [3,'<playerSide>k__BackingField',0x20,17],[4,'<inited>k__BackingField',0x24,2],
  [5,'<borned>k__BackingField',0x25,2],[6,'<isDisappeared>k__BackingField',0x26,2]],f.ptr(0),f.bObject);
 f.klass('BattleCharacterData','Torappu.Battle',36,
  [[4,'prefabKey',0x50,14],[8,'uniqueId',0x6c,9],[14,'isToken',0x90,2],
   [15,'isPredefined',0x91,2],[18,'tokenOrHostKey',0x98,14],[19,'tokenOrHostUniqueId',0xa0,9]],f.ptr(0),f.characterData);
 const tokenClass=derivedFactory(f)('Token',f.character),uid=4194478,hostUid=4194477;
 const cid='token_10024_ebnhlz_rcube',host='char_4046_ebnhlz';
 f.write(f.center.add(0x38),1);
 f.write(f.roleData[0].add(0x50),f.text(host));f.write(f.roleData[0].add(0x6c),hostUid);
 const source=f.unit(cid,28),enemy=f.unit('enemy_owned_target',77);
 f.write(source,tokenClass);f.write(source.add(0x20),1);f.write(enemy,f.enemyClass);f.write(enemy.add(0x40),50n*FP);
 const data=f.data(cid,uid,{isToken:true,hostKey:host,hostUid});
 f.write(data,f.characterData);f.write(source.add(0x538),data);
 const before=f.mark(),baseline=f.context.rpc.exports.readEvents(0,0);
 assert.equal(before.native_autochess_context.status,'available');
 assert(!before.native_autochess_context.memberships.some(row=>row.data_uid===uid));
 f.clock(30,FP);f.mark();
 const modifier=f.modifier(source,enemy,17,0,0),apply=f.callback('ApplyModifier',f.entity),call={threadId:1};
 f.write(modifier.add(0x60),5);
 apply.onEnter.call(call,[enemy,modifier,f.ptr(0)]);apply.onLeave.call(call);
 damage(f,source,enemy,40,50);
 const mark=f.mark(),batch=plain(f.context.rpc.exports.readEvents(0,mark.sequence));
 assert(!mark.native_autochess_context.memberships.some(row=>row.data_uid===uid));
 const hits=batch.events.filter(event=>event.kind==='damage_settlement'||event.kind==='elemental_bar');
 assert.deepEqual(hits.map(event=>[event.kind,event.amount,event.source_character_uid]),
  [['elemental_bar',17,uid],['damage_settlement',40,uid]]);
 for(const event of hits)assert.deepEqual(event.proof.native_autochess_token_host,
  {host_key:host,host_uid:hostUid,player_side:1});
 assert(batch.events.filter(event=>!hits.includes(event)).every(event=>!event.proof.native_autochess_token_host));
 const first=mark.sequence;
 f.clock(60,2n*FP);f.mark();f.write(source.add(0x20),7);
 damage(f,source,enemy,40,50);
 const after=f.mark(),rejected=plain(f.context.rpc.exports.readEvents(first,after.sequence));
 const hit=rejected.events.find(event=>event.kind==='damage_settlement');
 assert.equal(hit.amount,40);assert.equal(hit.source_character_uid,uid);
 assert(!hit.proof.native_autochess_token_host,'invalid side cannot attach a host witness');
 assert.equal(rejected.enemy_damage_unknown,false);
 return {before:plain(before),baseline:plain(baseline),mark:plain(mark),batch};
}
const platforms={},rejected={},ephemeral_tokens={};
for(const platform of ['native_pc','mumu']){
 platforms[platform]=positive(platform);rejected[platform]=negatives(platform);ephemeral_tokens[platform]=ephemeral(platform);
}
process.stdout.write(JSON.stringify({status:'verified',classification:'owned_callback_not_live',platforms,rejected,ephemeral_tokens}));
