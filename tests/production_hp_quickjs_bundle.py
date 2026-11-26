"""Generate a QuickJS execution of the real shared HP callbacks over owned fixture memory.

The optional full-scope stage uses the real prepare/layout/cache fixture and
publication journal. Bounded FIFO/IPC remain a separate required acceptance.
"""
from pathlib import Path
import argparse,json
ROOT=Path(__file__).resolve().parents[1]

def bundle(count, owned=False, source_root=ROOT, bridge_root=None, full_scope=False, pub_every=1000, queue=None):
    files={name:(source_root/name).read_text(encoding='utf-8') for name in ['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js']}
    fixture=(ROOT/'tests/native_pc_frida_probe_fixture.js').read_text(encoding='utf-8')
    if full_scope:
        files['event_probe_autochess.js']=(source_root/'native_pc/native_event_runtime/cold_plan.js').read_text(encoding='utf-8')+'\n'+files['event_probe_autochess.js']
    files.update({name:(source_root/name).read_text(encoding='utf-8') for name in ['native_pc/native_event_runtime/cold_plan.js','native_pc/native_event_runtime/bridge.js']})
    evaluator="\nconst __fixtureKeys=new Set(Object.keys(globalThis));return function(code){const aliases=Object.keys(globalThis).filter(k=>!__fixtureKeys.has(k)&&/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(k)).map(k=>'var '+k+'=globalThis['+JSON.stringify(k)+'];').join('');return eval(aliases+code)};"
    prelude=r'''
const __useOwned=USE_OWNED;const __files=FILES;
function assertion(v,message){if(!v)throw Error(message||'assertion failed');}
assertion.equal=(a,b,m)=>assertion(a===b,m||String(a)+' != '+String(b));
const fakePath={join:(...xs)=>{const out=[];for(const p of xs.join('/').split('/'))if(p==='..')out.pop();else if(p&&p!=='.')out.push(p);return out.join('/');},dirname:x=>x.includes('/')?x.slice(0,x.lastIndexOf('/')):'.'};
const fakeVM={createContext(c){c.globalThis=c;if(__useOwned){const originalPtr=c.ptr;c.ptr=v=>__arkdpsReplayActive()?ptr(v):originalPtr(v);}return c;},runInContext(source,c){
 if(!c.__evaluate){c.__evaluate=Function(...Object.keys(c),source+EVALUATOR)(...Object.values(c));return;}
 for(const name of ['__publishCaptureScope','__publishCapturePublication'])if(typeof c[name]==='function')globalThis[name]=c[name];
 return c.__evaluate(source);
}};
let __fixtureModule=null,__worldModule=null;
const require=name=>name.includes('native_pc_frida_probe_fixture')?__fixtureModule:name.includes('native_pc_autochess_context_fixture')?__worldModule:name.includes('assert')?assertion:name.endsWith('fs')?{readFileSync:n=>__files[n.replace(/^\.\//,'')]}:name.endsWith('vm')?fakeVM:name.endsWith('path')?fakePath:(()=>{throw Error('unimplemented require '+name)})();
const module={exports:{}};const process={argv:[],env:{}};
'''.replace('FILES',json.dumps(files)).replace('USE_OWNED','true' if owned else 'false').replace('EVALUATOR',json.dumps(evaluator))
    bench=r'''
const f=FULL_SCOPE?__preparedWorld():module.exports.fixture({platform:'native_pc',enemyHpState:true,sourcePath:'event_probe_native_pc.js'});
if(!FULL_SCOPE){f.write(f.enemyClass.add(0x58),f.entity);f.write(f.entity.add(0x58),f.bObject);f.write(f.bObject.add(0x58),f.ptr(0));}
let stamps=0;
const realStamp=FULL_SCOPE?f.vm.runInContext('probeAdapter.stampClock',f.context):null;f.context.observeScope=(when,...rest)=>{stamps++;if(FULL_SCOPE)return realStamp(when,...rest);when.native_autochess_scope={protocol_version:1,status:'available',verified_same_frame:true,frame:when.frame,game_time:when.game_time,scope_key:'owned:round:8',phase:4,round:8,collection_mode:'local'};};
f.vm.runInContext('probeAdapter.stampClock=globalThis.observeScope',f.context);
const fp=v=>BigInt(Math.round(v*4294967296));
const enemy=f.unit('enemy_owned_throughput',12),attrs=f.alloc(),cache=f.alloc();
f.write(enemy,f.enemyClass);f.write(enemy.add(0x40),fp(100));f.write(enemy.add(0x25),0);f.write(enemy.add(0x26),0);f.write(enemy.add(0xb0),attrs);f.write(attrs.add(0x68),cache);f.write(cache.add(0x18),1);f.write(cache.add(0x20),0n);f.write(cache.add(0x28),fp(1000000));f.write(cache.add(0x30),1);f.write(enemy.add(0x360),f.alloc());f.clock(1,0n);const born=f.callback('Born',f.bObject),bornCall={threadId:1};born.onEnter.call(bornCall,[enemy]);f.write(enemy.add(0x25),1);born.onLeave.call(bornCall);f.clock(30,fp(1));
const setter=f.callback('SetHpInternal',f.entity),dirty=f.callback('OnAttributeDirty',f.entity);

OWNED_SETUP
const results=[];
if(!QUEUE_MODE)for(const branch of ['input_only_baseline','unchanged','changed_damage','heal_coalesced','maxhp_dirty']){
 let hp=100000,dirtyCount=0;f.write(enemy.add(0x40),fp(hp));const initial=f.vm.runInContext('sequence',f.context);const stampsBefore=stamps;const publicationsBefore=OWNED?publicationCount:0;
 if(OWNED){__ownedProfile(true);scopeMs=scopeInPubMs=prefixMs=journalMs=0;}const start=__monotonicMs();
 for(let i=0;i<COUNT;i++){
  let call={threadId:1};if(branch!=='input_only_baseline'){if(OWNED)call=dispatch(0,undefined);else setter.onEnter.call(call,[enemy]);}
  if(branch!=='unchanged'){hp=branch==='changed_damage'?100000-i-1:100000+i+1;f.write(enemy.add(0x40),fp(hp));}
  if(branch==='maxhp_dirty'&&i%100===0){const nested={threadId:1};dirty.onEnter.call(nested,[enemy,f.ptr(0),f.ptr(cache.add(0x28).readS64().value??BigInt(cache.add(0x28).readS64().toString()))]);f.write(cache.add(0x28),fp(1000001));dirty.onLeave.call(nested);dirtyCount++;}
  if(branch!=='input_only_baseline'){if(OWNED)dispatch(1,call,FULL_SCOPE&&(i%PUB_EVERY===PUB_EVERY-1||i===COUNT-1));else setter.onLeave.call(call);}
 }
 const ms=__monotonicMs()-start;
 const mark=f.vm.runInContext('flushCoalescedEnemyHp();({sequence,active,error:fatal})',f.context);const events=f.vm.runInContext('queue',f.context).filter(e=>e.sequence>initial);
 assertion.equal(mark.active,0);assertion.equal(mark.error,null);
 assertion.equal(f.vm.runInContext('enemyStateUnknown',f.context),false,'HP unknown must not become benchmark success');
 if(branch==='changed_damage')assertion.equal(mark.sequence-initial,COUNT);if(branch==='heal_coalesced')assertion.equal(mark.sequence-initial,FULL_SCOPE?Math.ceil(COUNT/PUB_EVERY)+1:2);
 if(branch==='changed_damage'||branch==='heal_coalesced'){assertion.equal(events[0].hp_before,100000);assertion.equal(events.at(-1).hp_after,branch==='changed_damage'?100000-COUNT:100000+COUNT);}
 results.push({js_profile:OWNED?{scope_ms:scopeMs,scope_inside_pub_ms:scopeInPubMs,prefix_ms:prefixMs,journal_ms:journalMs}:null,native_profile:OWNED?JSON.parse(__ownedProfile(false)):null,branch,setters:COUNT,dirty:dirtyCount,elapsed_ms:ms,setters_per_second:COUNT*1000/ms,publication_count:OWNED?publicationCount-publicationsBefore:0,scope_stamps:stamps-stampsBefore,sequence_delta:mark.sequence-initial,event_kinds:events.filter(e=>e.sequence>initial).reduce((a,e)=>(a[e.kind]=(a[e.kind]||0)+1,a),{})});
}
QUEUE_CODE
print(JSON.stringify({queue:QUEUE_MODE?queueReport:null,kind:OWNED?'actual_shared_hp_callback_quickjs_native_owned':'actual_shared_hp_callback_quickjs_fixture_backend',includes:OWNED?['actual HP field plan and compile_plan','native capture and strict OwnedReplayView','actual bridge fused packed dispatch and argument proxy','actual adapter withCapturedMode wrapper','actual shared HP callback',...(QUEUE_MODE?['actual CaptureLanes private queue and four 32MiB spill banks','independent paced producer and actual serialized consumer']:[]),...(FULL_SCOPE?['real prepared full scope graph','785-guard publication graph and actual source prefix/journal']:[])]:['actual shared HP callback'],excluded:OWNED?[...(FULL_SCOPE?[]:['scope guard validation','production publication']),...(QUEUE_MODE?['IPC and native hook trampoline']:['IPC/queue']),'native dirty callback snapshots',...(FULL_SCOPE?[]:['full metadata discovery (fixture graph sealed at setup)'])]:['native capture','native owned view','scope guard validation','production publication','IPC/queue'],results}));
'''.replace('COUNT',str(count))

    setup=r"""
const P=enemy.constructor,original=Object.fromEntries(['add','readByteArray','readPointer','readU8','readU16','readU32','readS32','readU64','readS64','readFloat','readUtf16String','readCString'].map(k=>[k,P.prototype[k]]));
const clock=f.vm.runInContext('clock',f.context),prepared=FULL_SCOPE?f.vm.runInContext('nativeCaptureMetadata',f.context):null,stringClass=prepared?f.ptr(prepared.string_class):f.alloc();f.write(enemy.add(0x148).readPointer(),stringClass);
const meta=prepared??{clock,string_class:stringClass.toString(),classes:{BattleController:f.controllerClass.toString(),LevelData:f.level.readPointer().toString(),Enemy:f.enemyClass.toString(),BObject:f.bObject.toString(),Character:f.character.toString()},skill_offsets:f.vm.runInContext('skillF',f.context),skill_wait:null,attack_type_offset:0x30,factory_addresses:[],immutable_class_parents:true,coalesce_enemy_hp:true,cold_metadata_ready:true,cold_plan:COLD};
f.context.wireMeta=meta;f.vm.runInContext('nativeCaptureMetadata=wireMeta',f.context);
const rawPlan=f.vm.runInContext("nativeCapturePlan('hp.Entity.SetHpInternal',{toString:()=> '0x9000'},wireMeta)",f.context);
for(const k of ['__arkdpsReplayPoint','__arkdpsReplayWitness','__arkdpsReplayCheckpoint','__arkdpsReplayOptional','__arkdpsReplayEnd','__arkdpsOwnedRead'])f.context[k]=()=>{};
const caps={version:1,raw_collection:'all',lowered_projections:['live_scope_guards','same_point_class_ancestry','open_parent_checkpoint','token_host_and_character_form','full_native_binding','current_buff_and_blackboard_keys']};
f.context.rawPlan=rawPlan;f.context.caps=caps;const plan=f.vm.runInContext('finalizeNativeCapturePlan(rawPlan,wireMeta,caps)',f.context),memory=new Map();
function rawBytes(p,n){if(n===1||n===4){const b=new ArrayBuffer(n),v=new DataView(b);if(n===1)v.setUint8(0,original.readU8.call(p));else v.setInt32(0,original.readS32.call(p),true);return b;}const b=original.readByteArray.call(p,n),at=Number(enemy.add(0x25).value-p.value);if(at>=0&&at<n)new DataView(b).setUint8(at,original.readU8.call(enemy.add(0x25)));return b;}
function store(p,bytes){const a=BigInt(p.toString());new Uint8Array(bytes).forEach((b,i)=>memory.set(a+BigInt(i),b));}
let scope=null,publication=null;let scopeMs=0,scopeInPubMs=0,prefixMs=0,journalMs=0,pubActive=false;
if(FULL_SCOPE){const originalScope=f.vm.runInContext('capturedNativeScopePoint',f.context);f.context.profileScope=when=>{const t=__monotonicMs();try{return originalScope(when);}finally{const ms=__monotonicMs()-t;scopeMs+=ms;if(pubActive)scopeInPubMs+=ms;}};f.vm.runInContext('capturedNativeScopePoint=globalThis.profileScope;probeAdapter.captureScopePoint=globalThis.profileScope',f.context);}
if(FULL_SCOPE){const warm=f.mark();assertion.equal(warm.native_autochess_context.status,'available');f.context.graphClock=clock;scope=f.vm.runInContext('nativeCaptureScopeGraph(nativeAutoCache,nativeAutoStage,graphClock,101)',f.context);f.context.graphScope=scope;f.vm.runInContext('nativeCaptureScopes.set(101,graphScope)',f.context);publication=f.context.publishedPublication;assertion(publication&&publication.available,'actual publication graph missing');}
for(const field of [...plan.fields,...(scope?.fields??[]),...(publication?.chunks??[]).flatMap(c=>c.fields)]){let p=field.absolute_address?f.ptr(field.absolute_address):field.root==='global_slot'?clock:enemy;for(const off of field.pointer_offsets){const at=p.add(off);store(at,rawBytes(at,8));p=original.readPointer.call(at);}p=p.add(field.offset);
 if(field.kind==='utf16'){store(p,rawBytes(p,8));store(p.add(16),rawBytes(p.add(16),4));const n=original.readS32.call(p.add(16)),text=original.readUtf16String.call(p.add(20),n),b=new ArrayBuffer(n*2),v=new DataView(b);for(let i=0;i<n;i++)v.setUint16(i*2,text.charCodeAt(i),true);store(p.add(20),b);}else store(p,rawBytes(p,field.bytes));}
const ranges=[];for(const [at,b]of [...memory].sort((a,b)=>a[0]<b[0]?-1:1)){const last=ranges.at(-1);if(last&&BigInt(last.address)+BigInt(last.bytes.length)===at)last.bytes.push(b);else ranges.push({address:at.toString(),bytes:[b]});}
__ownedConfigure(JSON.stringify({plan,ranges,enemy:enemy.toString(),...(scope?{scope,publication}:{} )}));
let reading=false;
// Setup pointers have the same value ABI; ensure metadata operands do not
// invoke BigInt(object)->hex conversion during native pointer comparisons.
Object.setPrototypeOf(P.prototype,NativePointer.prototype);Object.setPrototypeOf(clock,NativePointer.prototype);
assertion(clock.constructor===NativePointer);const toString=P.prototype.toString;P.prototype.toString=()=>{throw Error('metadata pointer parsed as a string');};try{assertion.equal(__integer(f.enemyClass),f.enemyClass.value);assertion.equal(__integer(f.bObject),f.bObject.value);}finally{P.prototype.toString=toString;}
const write=f.write;f.write=(p,value)=>{write(p,value);if(p.equals(enemy.add(0x40))||p.equals(cache.add(0x28)))__ownedPut(Number(p.value),value);};
if(FULL_SCOPE){globalThis.__arkdpsColdAssignable=f.vm.runInContext('__arkdpsColdAssignable',f.context);globalThis.__arkdpsColdClassLineage=f.vm.runInContext('__arkdpsColdClassLineage',f.context);}else __arkdpsSealClassHierarchy(new Map([[f.enemyClass.toString(),f.entity.toString()],[f.entity.toString(),f.bObject.toString()],[f.bObject.toString(),'0x0']]));
f.context.arkdpsNativeAsyncCapture=true;globalThis.__nativeCaptureCapabilities=()=>caps;f.context.testSetter=setter;f.vm.runInContext('nativeCaptureModeHistory.set(0,nativeAutoDesiredMode);prepareNativeCaptureHook(ptr(0x9000),testSetter)',f.context);__sealReadonlyPlans();
const argBytes=new ArrayBuffer(8);new DataView(argBytes).setBigUint64(0,enemy.value,true);
if(FULL_SCOPE){globalThis.__arkdpsPublicationServed=f.vm.runInContext('__arkdpsPublicationServed',f.context);globalThis.rpc=f.context.rpc;}
let publicationCount=0;const rawPrefix=FULL_SCOPE?f.vm.runInContext('__arkdpsOnClosedRecord',f.context):undefined;const sourcePrefix=FULL_SCOPE?record=>{const start=__monotonicMs();pubActive=true;try{return rawPrefix(record);}finally{pubActive=false;prefixMs+=__monotonicMs()-start;}}:undefined;function dispatch(edge,receiver,claim=false){const header=__ownedEdge(edge,claim);reading=true;try{const result=__arkdpsReplayDispatch(header,edge?setter.onLeave:setter.onEnter,receiver,edge?0n:argBytes,1,claim?sourcePrefix:undefined,0x1234n);assertion.equal(result[2],true,'fused replay must be complete');assertion(result[3]&&typeof result[3]==='object');if(edge)assertion(result[3]===receiver,'receiver identity');if(claim){assertion(result[1],'actual captured publication unavailable');const journalStart=__monotonicMs();const journal=__arkdpsPublishClosedRecord(result[0],result[1],true);journalMs+=__monotonicMs()-journalStart;assertion(journal&&journal.retry!==true&&!journal.error&&journal.native_publication?.version===1&&journal.native_publication.closed_ticket===result[0].ticket,'actual journal rejected publication');publicationCount++;}return result[3];}finally{reading=false;}}

""".replace('COLD',json.dumps(json.loads((ROOT/'tests/fixtures/native_capture/cold-plan-450a0c9.json').read_text(encoding='utf-8')))) if owned else ''
    bench=bench.replace('OWNED_SETUP',setup).replace('OWNED','true' if owned else 'false').replace('FULL_SCOPE','true' if full_scope else 'false').replace('PUB_EVERY',str(pub_every)).replace('QUEUE_MODE','true' if queue else 'false')
    queue_code=''
    if queue:
        queue_code=r'''
let queuedReceiver;
const queueReport=JSON.parse(__ownedQueue(OPTIONS,header=>{
 const view=new DataView(header),edge=view.getUint32(18*8,true),claim=view.getBigUint64(6*8,true)!==0n;
 const result=__arkdpsReplayDispatch(header,edge?setter.onLeave:setter.onEnter,queuedReceiver,edge?0n:argBytes,1,claim?sourcePrefix:undefined,0x1234n);
 assertion.equal(result[2],true,'queued fused replay must be complete');
 if(edge){assertion(result[3]===queuedReceiver,'queued receiver identity');queuedReceiver=undefined;}else queuedReceiver=result[3];
 if(claim){assertion(result[1],'queued captured publication missing');const published=__arkdpsPublishClosedRecord(result[0],result[1],true);assertion(published&&published.retry!==true&&!published.error&&published.native_publication?.closed_ticket===result[0].ticket,'queued publication rejected');}
 return true;
}));
if(queueReport.failure_code===0){const state=f.vm.runInContext('flushCoalescedEnemyHp();({active,error:fatal,enemyStateUnknown})',f.context);assertion.equal(state.active,0);assertion.equal(state.error,null);assertion.equal(state.enemyStateUnknown,false);const events=f.vm.runInContext('queue.filter(e=>e.kind===\'enemy_hp\')',f.context);assertion.equal(events[0].hp_before,100000);assertion.equal(events.at(-1).hp_after,100000+queueReport.submitted_setters);}
'''.replace('OPTIONS',json.dumps(json.dumps(queue)))
    bench=bench.replace('QUEUE_CODE',queue_code)
    bridge=((bridge_root or source_root)/'native_pc/native_event_runtime/bridge.js').read_text(encoding='utf-8')
    bridge=(bridge[:bridge.index('const Memory=')]+'\n'+(source_root/'native_pc/native_event_runtime/cold_plan.js').read_text(encoding='utf-8')+('\n'+(source_root/'native_pc/native_event_runtime/publication.js').read_text(encoding='utf-8') if full_scope else '')) if owned else ''
    factory=''
    if full_scope:
        world=(ROOT/'tests/native_pc_autochess_context_fixture.js').read_text(encoding='utf-8')
        first=(ROOT/'tests/native_pc_first_cold_prepare_replay_fixture.js').read_text(encoding='utf-8').split('function capture(){')[0]
        first='\n'.join(line for line in first.splitlines() if 'const bridge=fs.readFileSync' not in line and 'vm.runInContext(fs.readFileSync' not in line)
        first=first.replace('world({fixtureOptions:', 'world({rolesPerPlayer:10,fixtureOptions:').replace('f.write(f.character.add(0x58)', 'f.write(f.enemyClass.add(0x58),f.entity);f.write(f.character.add(0x58)')
        factory='__fixtureModule=module.exports;const worldModule={exports:{}};Function(\'require\',\'module\',\'__dirname\','+json.dumps(world)+')(require,worldModule,\'tests\');__worldModule=worldModule.exports;function __preparedWorld(){return Function(\'require\','+json.dumps(first+'\nreturn f;')+')(require);}'
    return prelude+'\n'+bridge+'\n'+fixture+'\n'+factory+'\n'+bench
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--count',type=int,default=30000);p.add_argument('--owned',action='store_true');p.add_argument('--source-root',type=Path,default=ROOT);p.add_argument('--bridge-root',type=Path);p.add_argument('--full-scope',action='store_true');p.add_argument('--pub-every',type=int,default=1000);p.add_argument('--queue-rate',type=int);p.add_argument('--pause-ms',type=int,default=0);a=p.parse_args();a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(bundle(a.count,a.owned,a.source_root,a.bridge_root,a.full_scope,a.pub_every,{'count':a.count,'rate':a.queue_rate,'pause_ms':a.pause_ms,'pub_every':a.pub_every} if a.queue_rate else None),encoding='utf-8')
