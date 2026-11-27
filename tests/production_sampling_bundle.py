"""Full production world sampling over self-owned bytes, without hook traffic."""
from pathlib import Path
import argparse
from production_hp_actual_engine_bundle import engine_bundle
ROOT = Path(__file__).resolve().parents[1]


def sampling_bundle(source_root=ROOT):
    text = engine_bundle(count=1, source_root=source_root).split('\n__ownedSetCallbacks(setter,sourcePrefix,')[0]
    original = 'return {context, messages, requests, write, text, managedString, alloc, ptr, clock, callback, unit, modifier,'
    assert text.count(original) == 1
    text = text.replace(original, 'return {ownedMemory:memory,context, messages, requests, write, text, managedString, alloc, ptr, clock, callback, unit, modifier,')
    # Sampling starts before any business callback; Born is benchmark setup in
    # the HP fixture and must not advance this independent stream.
    text = text.replace('born.onEnter.call(bornCall,[enemy]);f.write(enemy.add(0x25),1);born.onLeave.call(bornCall);', 'f.write(enemy.add(0x25),1);')
    marker = 'const ranges=[];for(const [at,b]of [...memory]'
    assert text.count(marker) == 1
    dense = r'''
const blocks=new Map();
function ownedBlock(at){const base=at&~511n,key=base.toString();if(!blocks.has(key))blocks.set(key,new Uint8Array(512));return [blocks.get(key),Number(at-base)];}
for(const [key,value]of f.ownedMemory){const at=BigInt(key);let bytes;
 if(typeof value==='string'){bytes=new Uint8Array(value.length*2);const view=new DataView(bytes.buffer);for(let i=0;i<value.length;i++)view.setUint16(i*2,value.charCodeAt(i),true);}
 else {bytes=new Uint8Array(typeof value==='bigint'||(value&&typeof value.value==='bigint')?8:4);const view=new DataView(bytes.buffer);if(bytes.length===8)view.setBigUint64(0,BigInt.asUintN(64,typeof value==='bigint'?value:value.value),true);else view.setInt32(0,Number(value),true);}
 for(let i=0;i<bytes.length;i++){const [block,offset]=ownedBlock(at+BigInt(i));block[offset]=bytes[i];}
}
for(const [at,b]of memory){const [block,offset]=ownedBlock(at);block[offset]=b;}
const ranges=[];for(const [address,bytes]of [...blocks].sort((a,b)=>BigInt(a[0])<BigInt(b[0])?-1:1)){const last=ranges.at(-1);if(last&&BigInt(last.address)+BigInt(last.bytes.length)===BigInt(address))last.bytes.push(...bytes);else ranges.push({address,bytes:[...bytes]});}
'''
    start=text.index(marker)
    end=text.index('__ownedConfigure(JSON.stringify(',start)
    text=text[:start]+dense+'\n'+text[end:]
    return text + r'''
__ownedSetCallbacks(setter,sourcePrefix,f.vm.runInContext('__arkdpsRetireCaptureGraphs',f.context));
const samplingPlan=f.vm.runInContext('nativeSamplingCapturePlan(wireMeta,caps)',f.context);
const rejectedPlans=[];
for(const fault of ['projection','clock_phase','clock_root','cold','cold_root']){const bad=JSON.parse(JSON.stringify(samplingPlan));
 if(fault==='projection')bad.required_projections=[];
 if(fault==='clock_phase')bad.fields[0].phase='leave';
 if(fault==='clock_root')bad.fields[0].root='arg';
 if(fault==='cold')delete bad.cold_plan;
 if(fault==='cold_root')bad.cold_plan.fields[1].absolute_address='0x'+(BigInt(bad.cold_plan.fields[1].absolute_address)+8n).toString(16);
 let rejected=false;try{__ownedConfigureSampler(JSON.stringify(bad));}catch(error){rejected=true;}
 assertion(rejected,'invalid production sampling plan accepted: '+fault);rejectedPlans.push(fault);
}
__ownedConfigureSampler(JSON.stringify(samplingPlan));
const rows=[];
function sample(frame,options={}){const result=JSON.parse(__ownedSamplingRun(JSON.stringify({frame,...options})));rows.push(result);return result;}
const first=sample(31);assertion(first.accepted,'first actual production checkpoint');
assertion.equal(first.mark.frame,31);assertion.equal(first.raw,0);assertion.equal(first.sequence,0);
const next=sample(32);assertion(next.accepted,'next production checkpoint');assertion.equal(next.mark.frame,32);
assertion(first.mark.native_publication.token!==next.mark.native_publication.token,'new frame must produce new token');
const openingReceipt=__arkdpsPublicationRequest({operation:'mark',sampling:'receipt',publication_token:first.mark.native_publication.token});
assertion.equal(openingReceipt.frame,31,'opening token retains opening bytes while closing captures current frame');
assertion.equal(next.mark.frame,32,'closing sample comes from new owned current bytes');
const paused=sample(32,{paused:true});assertion(paused.accepted,'paused same-frame production checkpoint');
assertion(paused.mark.native_publication.token!==next.mark.native_publication.token,'paused point is a new sample');
const cold=sample(33,{cold:true});assertion(cold.accepted,'full cold production checkpoint');
const serial=cold.sampling_serial;
const clockFault=sample(34,{fault:'clock'});assertion.equal(clockFault.accepted,false,'changed clock must reject');assertion.equal(clockFault.sampling_serial,serial);
const recoveredClock=sample(35);assertion(recoveredClock.accepted,'clock recovery with real cold path');
const modeFault=sample(36,{fault:'mode'});assertion.equal(modeFault.accepted,false,'mode race must reject');assertion.equal(modeFault.sampling_serial,recoveredClock.sampling_serial);assertion.equal(modeFault.token,recoveredClock.token);
const objectGuard=scope.fields.find(field=>field.comparison&&field.bytes===8);
const objectFault=sample(37,{fault:'object',address:objectGuard.absolute_address});assertion.equal(objectFault.accepted,false,'changed object guard must reject');assertion.equal(objectFault.token,recoveredClock.token,'rejected point cannot replace published token');
const recoveredObject=sample(38);assertion(recoveredObject.accepted,'restored object requires independent new point');
for(const row of rows){assertion.equal(row.raw,0);assertion.equal(row.processed,0);assertion.equal(row.closed,0);assertion.equal(row.sequence,0);assertion.equal(row.failure,0);}
print(JSON.stringify({kind:'production_sampling_owned_world',scope_fields:scope.fields.length,publication_fields:publication.chunks.reduce((n,c)=>n+c.fields.length,0),rejectedPlans,rows,excluded:['Game','detours','IPC','FPS']}));
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(sampling_bundle(), encoding='utf8')
