// Export production plans and a real owned AutoChess body's guarded inputs.
'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const {world}=require('./native_pc_autochess_context_fixture.js');
const f=world({rolesPerPlayer:10,token:true,fixtureOptions:{enemyHpState:true}});
f.clock(30,4294967296n);const marked=f.mark();assert.equal(marked.native_autochess_context.status,'available');
const stage=vm.runInContext('nativeAutoStage',f.context),cache=vm.runInContext('nativeAutoCache',f.context),body=vm.runInContext('nativeAutoBodyCache.body',f.context);
const clock=vm.runInContext('clock',f.context),stringClass=stage.stagePointer.readPointer();
const enemy=f.unit('enemy_wire_budget',4194365);f.write(enemy,f.enemyClass);f.write(enemy.add(0x25),1);f.write(enemy.add(0x40),100n<<32n);f.write(enemy.add(0x148).readPointer(),stringClass);
const attributes=f.alloc(),cached=f.alloc();f.write(enemy.add(0xb0),attributes);f.write(attributes.add(0x68),cached);f.write(cached.add(0x18),8);
const key=0x123456789n;f.write(cached.add(0x20),key);f.write(cached.add(0x28),key^(200n<<32n));f.write(cached.add(0x30),1);
const cold=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const meta={clock,string_class:stringClass.toString(),classes:{BattleController:f.controllerClass.toString(),LevelData:f.level.readPointer().toString(),Enemy:f.enemyClass.toString(),BObject:f.bObject.toString(),Character:f.character.toString()},
 skill_offsets:vm.runInContext('skillF',f.context),skill_wait:null,attack_type_offset:0x30,factory_addresses:[],immutable_class_parents:true,coalesce_enemy_hp:true,cold_metadata_ready:true,cold_plan:cold};
f.context.wireMeta=meta;vm.runInContext('nativeCaptureMetadata=wireMeta',f.context);
const raw=f.context.nativeCapturePlan('hp.Entity.SetHpInternal',{toString:()=> '0x9000'},meta);
for(const name of ['__arkdpsReplayPoint','__arkdpsReplayWitness','__arkdpsReplayCheckpoint','__arkdpsReplayOptional','__arkdpsReplayEnd','__arkdpsOwnedRead'])f.context[name]=()=>{};
const capabilities={version:1,raw_collection:'all',lowered_projections:['live_scope_guards','same_point_class_ancestry','open_parent_checkpoint','token_host_and_character_form','full_native_binding','current_buff_and_blackboard_keys']};
const plan=f.context.finalizeNativeCapturePlan(raw,meta,capabilities);
const dirty=f.context.finalizeNativeCapturePlan(f.context.nativeCapturePlan('hp.Entity.OnAttributeDirty',{toString:()=> '0x9100'},meta),meta,capabilities);assert.equal(plan.complete,true);assert.deepEqual([...plan.required_projections],[...raw.required_projections]);
const scope=f.context.nativeCaptureScopeGraph(cache,stage,clock,1),publication=f.context.publicationGuardFields(body.read.guards,stringClass.toString(),1);
const memory=new Map();
function store(address,bytes){const a=BigInt(address);new Uint8Array(bytes).forEach((b,i)=>memory.set(a+BigInt(i),b));}
function binary(address,size){const out=new ArrayBuffer(size),v=new DataView(out);for(let i=0;i<size;i++){
 let cell;try{cell=f.ptr(address).add(i).cell();}catch{continue;}
 if(typeof cell==='string')continue;
 const n=typeof cell==='bigint'||cell&&typeof cell.value==='bigint'?8:4;
 const scratch=new ArrayBuffer(n),view=new DataView(scratch);
 if(n===8)view.setBigUint64(0,BigInt.asUintN(64,typeof cell==='bigint'?cell:cell.value),true);else view.setInt32(0,Number(cell),true);
 for(let j=0;j<n&&i+j<size;j++)v.setUint8(i+j,view.getUint8(j));
 }return out;}
function own(field){let a=BigInt(field.absolute_address??(field.root==='global_slot'?clock.toString():enemy.toString()));
 for(const offset of field.pointer_offsets){const at=a+BigInt(offset);store(at,binary(at,8));a=BigInt(f.ptr(at).readPointer().toString());}
 a+=BigInt(field.offset);
 if(field.kind==='utf16'){store(a,binary(a,8));store(a+16n,binary(a+16n,4));const n=f.ptr(a+16n).readS32(),text=f.ptr(a+20n).readUtf16String(n);assert.equal(typeof text,'string');assert.equal(text.length,n);const b=new ArrayBuffer(n*2),v=new DataView(b);for(let i=0;i<n;i++)v.setUint16(i*2,text.charCodeAt(i),true);store(a+20n,b);}
 else store(a,binary(a,field.bytes));}
for(const field of plan.fields)own(field);
for(const field of dirty.fields)own(field);
for(const field of scope.fields)own(field);
for(const chunk of publication.chunks)for(const field of chunk.fields)own(field);
const ordered=[...memory.entries()].sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0),ranges=[];
for(const [address,value]of ordered){const last=ranges.at(-1);if(last&&BigInt(last.address)+BigInt(last.bytes.length)===address)last.bytes.push(value);else ranges.push({address:address.toString(),bytes:[value]});}
const output={fixture_only:true,not_runtime_readiness:true,roster_members:marked.native_autochess_context.memberships.length,publication_guards:body.read.guards.length,
 plan,dirty,scope,publication,ranges,enemy:enemy.toString(),hp_address:enemy.add(0x40).toString(),maxhp_address:cached.add(0x28).toString(),maxhp_key:key.toString(),scope_round:cache.addresses.round.toString(),clock:clock.toString()};
fs.writeFileSync(process.argv[2],JSON.stringify(output));console.log(JSON.stringify({fields:plan.fields.length,scope_fields:scope.fields.length,pub_guards:body.read.guards.length,members:output.roster_members,memory_bytes:memory.size}));
