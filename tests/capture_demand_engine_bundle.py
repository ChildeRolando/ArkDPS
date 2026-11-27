"""Uninstrumented mixed callbacks in the existing actual engine environment."""
from pathlib import Path
import argparse,json
from production_hp_actual_engine_bundle import engine_bundle,ROOT

def damage_bundle(source_root=ROOT,options=None):
    options=options or {'count':5,'pub_every':1}
    text=engine_bundle(count=1,source_root=source_root,light=True).split('\n__ownedSetCallbacks(setter,sourcePrefix,')[0]
    # The HP helper carries diagnostic counters/timers. Ordinary mixed gates
    # must execute the real callbacks without those measurement wrappers.
    skipped=['if(true){const originalNativeCall=','if(true){const originalArguments=']
    if not options.get('profile'):
        skipped+=['const realStamp=','if(true){const originalScope=']
    text='\n'.join(line for line in text.splitlines() if not any(line.startswith(marker) for marker in skipped) and (options.get('profile') or "f.vm.runInContext('probeAdapter.stampClock=globalThis.observeScope'" not in line))
    if not options.get('profile'):
        wrapper='const sourcePrefix=true?record=>{const start=__monotonicMs();pubActive=true;try{return rawPrefix(record);}finally{pubActive=false;prefixMs+=__monotonicMs()-start;}}:undefined;'
        assert text.count(wrapper)==1
        text=text.replace(wrapper,'const sourcePrefix=rawPrefix;')
    needle='return {context, messages, requests, write, text, managedString, alloc, ptr, clock, callback, unit, modifier,'
    assert text.count(needle)==1
    text=text.replace(needle,'return {ownedMemory:memory,context, messages, requests, write, text, managedString, alloc, ptr, clock, callback, unit, modifier,')
    # The synthetic method addresses are resolved before actual metadata prepare.
    needle="const methods=['readByteArray'";assert text.count(needle)==1
    text=text.replace(needle,"f.installSyntheticMitigation({factories:2,noEvent:true});const methods=['readByteArray'")
    # Fixture adapter methods route every replay read into the actual native lease.
    tail=r'''
for(const name of Object.keys(original).filter(name=>name.startsWith('read'))){const before=original[name];P.prototype[name]=function(...args){return (__arkdpsReplayActive()?NativePointer.prototype[name]:before).apply(this,args);};}
const demandSource=f.unit('char_shared',301),demandTarget=f.unit('enemy_mixed_demand',302);
for(const actor of [demandSource,demandTarget]){f.write(actor.add(0x148).readPointer(),f.ptr(meta.string_class));f.write(actor.add(0x24),1);f.write(actor.add(0x25),1);f.write(actor.add(0x26),0);f.write(actor.add(0x20),actor===demandSource?1:5);f.write(actor.add(0x40),50n<<32n);f.write(actor.add(0x538),f.roleData[30]);}
f.write(demandTarget,f.enemyClass);f.write(demandTarget.add(0xb0),attrs);f.write(demandTarget.add(0x360),f.alloc());
const demandMod=f.modifier(demandSource,demandTarget,40,-40,1);f.write(demandMod.add(0x80),80n<<32n);f.write(demandMod.add(0x88),40n<<32n);
const demandAttack=f.alloc();f.write(demandAttack.add(meta.attack_type_offset),1);
const demandBuff=f.alloc(),demandBB=f.alloc(),demandArray=f.alloc(),demandKey=f.managedString('shining_s_2'),demandDynamic=f.managedString('dynamic');
for(const s of [demandKey,demandDynamic])f.write(s,f.ptr(meta.string_class));
f.write(demandBuff,f.buffClass);f.write(demandBuff.add(0x18),demandSource);f.write(demandBuff.add(0x208),demandKey);f.write(demandBuff.add(0x27c),1);f.write(demandBB.add(0x18),1);f.write(demandBB.add(0x10),demandArray);f.write(demandArray.add(0x20),demandDynamic);f.write(demandArray.add(0x28),15);
const demandShieldMod=f.modifier(f.ptr(0),demandTarget,40,0,1);f.write(demandShieldMod.add(0x80),0n);f.write(demandShieldMod.add(0x88),0n);
const methods=f.context.syntheticMitigationPlan;
const hookIds=[0,1,3,5,6,7,8,9,10,11,12,13];
const hooks=[f.callback('ApplyModifier',f.entity),f.callback('OnAfterOutputDamage',f.entity),f.callback('OnTakeDamage',f.unitClass),f.callback('OnTakeDamage',f.buffClass),f.callback('_AssignDynamicVar',f.blockDamage),f.hookAt(methods.outer),f.hookAt(methods.noEvent),f.hookAt(methods.internal),f.hookAt(methods.final),...methods.factories.map(p=>f.hookAt(p)),setter];
const addresses=hooks.map((_,i)=>hookIds[i]===11||hookIds[i]===12?meta.factory_addresses[hookIds[i]-11]:'0x'+(0x9100+i*16).toString(16));
for(let i=0;i<hooks.length;i++){if(hookIds[i]!==13){f.context.demandHook=hooks[i];f.context.demandAddress=f.ptr(addresses[i]);f.vm.runInContext('prepareNativeCaptureHook(demandAddress,demandHook)',f.context);}assertion(hooks[i].arkdpsCaptureContract.complete,'mixed actual contract incomplete '+i);}
const w=(at,value,bytes=8)=>({address:at.toString(),value:'0x'+__arkdpsUnsigned64(BigInt(value)).toString(16),bytes});
const floatBits=value=>{const b=new ArrayBuffer(4),v=new DataView(b);v.setFloat32(0,value,true);return BigInt(v.getUint32(0,true));};
const step=(hook,edge,args=[],result=0n,writes=[])=>({hook:hookIds.indexOf(hook),edge,args:args.map(p=>p.toString()),return_value:'0x'+BigInt.asUintN(64,result).toString(16),writes});
const en=(h,args,ws=[])=>step(h,'entry',args,0n,ws),lv=(h,result=0n,ws=[])=>step(h,'leave',[],result,ws);
const A=[demandTarget,demandMod],O=[demandSource,demandMod],outer=[demandTarget,demandSource,f.ptr(1),f.ptr(0),demandAttack],inner=[demandAttack,f.ptr(0),f.ptr(0),demandTarget],final=[demandAttack];
const reset=[w(demandTarget.add(0x40),50n<<32n),w(demandMod.add(0x48),-40n<<32n)];
const formula=()=>[en(7,outer),en(9,inner),en(10,final),lv(10,80n<<32n),lv(9),lv(7,40n<<32n)];
const settle=(take,outputs=[])=>[en(0,A,reset),en(take,A),en(13,[demandTarget]),lv(13,0n,[w(demandTarget.add(0x40),10n<<32n)]),lv(take),...outputs.flatMap(i=>[en(i,O),lv(i)]),lv(0)];
const routes=[
 [...formula(),...settle(3,[1])],
 [en(8,[demandTarget,demandSource,demandAttack]),en(10,final),lv(10,80n<<32n),lv(8,40n<<32n),...settle(3,[1])],
 [en(5,[demandBuff,demandShieldMod],[w(demandBuff.add(0x18),demandSource.value),w(demandArray.add(0x28),floatBits(15),4)]),en(6,[f.ptr(0),demandBB,demandShieldMod,f.ptr(0),f.ptr(10n<<32n)]),lv(6,0n,[w(demandArray.add(0x28),floatBits(5),4)]),lv(5)],
];
for(const overload of [0,1]){const args=[demandMod,f.ptr(40n<<32n)];if(overload)args.push(f.ptr(60n<<32n));args.push(f.ptr(1),f.ptr(0),demandSource,demandTarget,f.ptr(0),f.ptr(80n<<32n));
 routes.push([en(8,[demandTarget,f.ptr(0),demandAttack]),en(10,final),lv(10,80n<<32n),lv(8,40n<<32n),en(11+overload,args,[w(demandMod.add(0x88),(overload?60n:40n)<<32n)]),lv(11+overload),...settle(3)]);
}
// Independent true parent settlement: a non-player shield produces no legacy
// shield event; its numeric absorption must still certify the enclosing hit.
routes.push([...formula(),en(0,A,[...reset,w(demandMod.add(0x48),-30n<<32n)]),en(3,A),
 en(5,[demandBuff,demandMod],[w(demandBuff.add(0x18),demandTarget.value),w(demandArray.add(0x28),floatBits(15),4)]),
 en(6,[f.ptr(0),demandBB,demandMod,f.ptr(0),f.ptr(10n<<32n)]),lv(6,0n,[w(demandArray.add(0x28),floatBits(5),4)]),lv(5),
 en(13,[demandTarget]),lv(13,0n,[w(demandTarget.add(0x40),20n<<32n)]),lv(3),en(1,O),lv(1),lv(0)]);
const hpPadding=OPTIONS_OBJECT.hp_padding??0;assertion(Number.isInteger(hpPadding)&&hpPadding>=0&&hpPadding<=100,'owned HP padding budget');
if(hpPadding)for(const route of routes)route.unshift(...Array.from({length:hpPadding},()=>[en(13,[demandTarget]),lv(13)]).flat());
// Serialize the same owned fixture's explicit cells. Keep scalar Boolean widths.
const blocks=new Map();function demandBlock(at){const base=at&~511n,k=base.toString();if(!blocks.has(k))blocks.set(k,new Uint8Array(512));return [blocks.get(k),Number(at-base)];}
for(const [key,value]of f.ownedMemory){const at=BigInt(key);let bytes;
 if(typeof value==='string'){bytes=new Uint8Array(value.length*2);const v=new DataView(bytes.buffer);for(let i=0;i<value.length;i++)v.setUint16(i*2,value.charCodeAt(i),true);}
 else{const size=typeof value==='bigint'||value&&typeof value.value==='bigint'?8:Number(at%4n)?1:4;bytes=new Uint8Array(size);const v=new DataView(bytes.buffer);if(size===8)v.setBigUint64(0,BigInt.asUintN(64,typeof value==='bigint'?value:value.value),true);else if(size===1)v.setUint8(0,Number(value));else v.setInt32(0,Number(value),true);}
 for(let i=0;i<bytes.length;i++){const [b,off]=demandBlock(at+BigInt(i));b[off]=bytes[i];}
}
for(const [at,value]of memory){const [b,off]=demandBlock(at);b[off]=value;}
const demandRanges=[];for(const [address,bytes]of [...blocks].sort((a,b)=>BigInt(a[0])<BigInt(b[0])?-1:1)){const last=demandRanges.at(-1);if(last&&BigInt(last.address)+BigInt(last.bytes.length)===BigInt(address))last.bytes.push(...bytes);else demandRanges.push({address,bytes:[...bytes]});}
const setup={plans:hooks.map(h=>h.arkdpsCaptureContract),routes,ranges:demandRanges};
let demandJournalMs=0,demandJournalCalls=0;if(OPTIONS_OBJECT.profile){const originalJournal=globalThis.__arkdpsPublishClosedRecord;globalThis.__arkdpsPublishClosedRecord=function(...args){const start=__monotonicMs();try{return originalJournal.apply(this,args);}finally{demandJournalMs+=__monotonicMs()-start;demandJournalCalls++;}};}
__ownedDemandConfigure(JSON.stringify(setup),hooks,sourcePrefix,f.vm.runInContext('__arkdpsRetireCaptureGraphs',f.context));
const before=f.vm.runInContext('sequence',f.context),report=JSON.parse(__ownedDemandRun(OPTIONS));
const outcome=f.vm.runInContext('({active,fatal,sequence,events:queue.slice(),mitigation:mitigationDiagnostics(),damage:damageSettlementQuality(),shieldUnknown})',f.context);
outcome.events=outcome.events.filter(event=>event.sequence>before);
print(JSON.stringify({kind:'actual_mixed_damage_formula_buff',producer_reader:'capture_read/ReadProcessMemory(GetCurrentProcess)',options:OPTIONS_OBJECT,report,outcome,source_profile:OPTIONS_OBJECT.profile?{scope_ms:scopeMs,scope_inside_publication_ms:scopeInPubMs,prefix_ms:prefixMs,journal_ms:demandJournalMs,journal_calls:demandJournalCalls}:null,scope_fields:scope.fields.length,publication_fields:publication.chunks.reduce((n,c)=>n+c.fields.length,0),excluded:['Game','detours','assembly ingress','IPC','FPS'],initial_sequence:before}));
assertion.equal(report.failure,0,'native transport must remain healthy');assertion.equal(report.fatal,'');assertion.equal(report.producer_error,'');assertion.equal(report.cycles,OPTIONS_OBJECT.count);assertion.equal(report.raw,report.processed);assertion.equal(report.raw,report.closed);assertion.equal(report.raw,report.published,'final publication retained');
assertion.equal(outcome.active,0);assertion.equal(outcome.fatal,null);assertion.equal(outcome.damage.enemy_damage_unknown,false);assertion.equal(outcome.shieldUnknown,false);
const expected={damage:0,shield:0,factory:0,numeric:0};for(let cycle=0;cycle<OPTIONS_OBJECT.count;cycle++){const route=OPTIONS_OBJECT.route>=0?OPTIONS_OBJECT.route:cycle%(OPTIONS_OBJECT.route_count??5);if(route===2)expected.shield++;else{expected.damage++;if(route===3||route===4)expected.factory++;if(route===5)expected.numeric++;}}
const damage=outcome.events.filter(e=>e.kind==='damage_settlement'),shield=outcome.events.filter(e=>e.kind==='shield_absorbed');
assertion.equal(damage.length,expected.damage,'nonzero complete damage settlements');assertion.equal(shield.length,expected.shield,'nonzero numeric shield events');
for(const event of damage){const numeric=event.shield_absorbed===10;assertion.equal(event.amount,numeric?30:40);assertion.equal(event.hp_before,50);assertion.equal(event.hp_after,numeric?20:10);assertion.equal(event.nominal_amount,40);assertion.equal(event.settlement_complete,true);assertion.equal(event.mitigation_before,80);assertion.equal(event.mitigation_after,40);}
assertion.equal(damage.filter(event=>event.shield_absorbed===10).length,expected.numeric,'numeric parent absorption certificate');
for(const event of shield){assertion.equal(event.amount,10);assertion.equal(event.proof.before,15);assertion.equal(event.proof.after,5);assertion.equal(event.proof.incoming,40);}
assertion.equal(outcome.mitigation.formula_candidates,expected.damage);assertion.equal(outcome.mitigation.emitted,expected.damage);assertion.equal(outcome.mitigation.factory_candidates,expected.factory);assertion.equal(outcome.mitigation.factory_emitted,expected.factory);
if(hpPadding)assertion.equal(report.planned_hp_edges,2*hpPadding*OPTIONS_OBJECT.count,'real native planned omissions only');
'''
    return text+tail.replace('OPTIONS_OBJECT',json.dumps(options)).replace('OPTIONS',json.dumps(json.dumps(options)))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,default=ROOT);p.add_argument('--output',type=Path,required=True);p.add_argument('--count',type=int,default=5);p.add_argument('--rate',type=int,default=0);p.add_argument('--pause-ms',type=int,default=0);p.add_argument('--route',type=int,default=-1);a=p.parse_args();a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(damage_bundle(a.source_root,{'count':a.count,'rate':a.rate,'pause_ms':a.pause_ms,'route':a.route,'pub_every':100 if a.rate else 1}),encoding='utf-8')
