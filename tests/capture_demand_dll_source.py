"""Adapt the existing full mixed fixture to the delivered DLL's real bridge."""
import json
from capture_demand_engine_bundle import damage_bundle
from native_pc.native_event_runtime.smoke import export_offsets

def source(source_root,helper,missing=False,mismatch=False):
    text=damage_bundle(source_root,{'count':1,'route':0,'profile':False})
    start=text.index('\n// Narrow compatibility surface for the unmodified PC event probe.')
    end=text.index('\n// Execute the production probe with an owned, in-memory IL2CPP fixture.',start)
    text=text[:start]+text[end:]
    # Initialization helpers are replaced with actual DLL graph publication;
    # no helper is callable by a business callback during replay.
    prelude=(source_root/'event_probe_store.js').read_text(encoding='utf-8')+'\n'
    prelude+='const dllScopePublisher=__publishCaptureScope,dllPublicationPublisher=__publishCapturePublication;\n'
    prelude+='globalThis.__ownedGraphPublish=(publication,graph)=>(publication?dllPublicationPublisher:dllScopePublisher)(graph);\n'
    offsets=export_offsets(helper);prelude+='const dllHelper=Process.getModuleByName("CaptureDemandFixture.dll");\n'
    prelude+='const dllAddresses='+json.dumps([offsets['DemandHook'+str(i)] for i in range(12)])+'.map(offset=>dllHelper.base.add(offset));\n'
    # Give actual producer metadata the real factory method addresses, while
    # retaining the existing fixture's callback lookup handles.
    needle='f.installSyntheticMitigation({factories:2,noEvent:true});'
    assert text.count(needle)==1
    points=['__arkdpsReplayPoint','__arkdpsReplayWitness','__arkdpsReplayCheckpoint','__arkdpsReplayOptional','__arkdpsReplayEnd','__arkdpsOwnedRead']
    text=text.replace(needle,needle+"for(const name of "+repr(points)+")f.context[name]=globalThis[name];f.context.dllFactories=[f.ptr(dllAddresses[9].toString()),f.ptr(dllAddresses[10].toString())];f.vm.runInContext('mitigationMethods={...mitigationMethods,factories:globalThis.dllFactories}',f.context);")
    text=text.replace('f.context[k]=()=>{};','f.context[k]=globalThis[k];')
    text='\n'.join(line for line in text.splitlines() if not line.startswith('__ownedConfigure(JSON.stringify('))
    text=text.replace('globalThis.__nativeCaptureCapabilities=()=>caps;','')
    text=text.replace('prepareNativeCaptureHook(ptr(0x9000),testSetter)','void 0').replace('__sealReadonlyPlans();','')
    old="const addresses=hooks.map((_,i)=>hookIds[i]===11||hookIds[i]===12?meta.factory_addresses[hookIds[i]-11]:'0x'+(0x9100+i*16).toString(16));"
    assert text.count(old)==1;text=text.replace(old,'const addresses=dllAddresses.map(p=>p.toString());')
    text=text.replace('if(hookIds[i]!==13){','{')
    marker='const setup={plans:hooks.map(h=>h.arkdpsCaptureContract),routes,ranges:demandRanges};'
    assert text.count(marker)==1;text=text.split(marker)[0]
    tail=r'''
const dllHooks=[];let removed=null;
if(MISMATCH){const divergent=JSON.parse(JSON.stringify(routes[0]));const unit=divergent.find(s=>s.hook===2&&s.edge==='entry');assertion(unit,'real Unit entry recipe required');unit.args[0]=demandSource.toString();routes.push(divergent);}
const dllDiagnostic=Memory.alloc(65536),dllOriginalPublish=__arkdpsPublishClosedRecord;
globalThis.__arkdpsPublishClosedRecord=function(record,projection,...args){
 const answer=dllOriginalPublish(record,projection,...args);
 if(answer?.error){const originalError=answer.error;
  // Preserve the exact failure receipt. Its JSON serialization happens after
  // native replay closes; only then write diagnostics to this owned sidecar.
  Object.defineProperty(answer,'error',{enumerable:true,get(){
   const message=JSON.stringify({error:originalError,sequence:projection?.mark?.sequence,epoch:projection?.mark?.epoch,
    active:projection?.mark?.active,after:projection?.events_after,through:projection?.events_through,events:projection?.events?.length,
    acknowledged:projection?.acknowledged,journalAcknowledged:__arkdpsPublicationJournal.acknowledged,
    journalThrough:__arkdpsPublicationJournal.publishedThrough,store:__arkdpsPublicationJournal.store?.state()});
   const bytes=new ArrayBuffer(4+message.length*2),view=new DataView(bytes);view.setUint32(0,message.length,true);for(let i=0;i<message.length;i++)view.setUint16(4+i*2,message.charCodeAt(i),true);__write(dllDiagnostic.value,bytes);return originalError;
  }});
 }
 return answer;
};
for(let i=0;i<hooks.length;i++){
 let callbacks=hooks[i];
 if(MISSING&&i===2){const contract=JSON.parse(JSON.stringify(callbacks.arkdpsCaptureContract));
  const field=contract.fields.find(f=>f.phase==='entry'&&f.name==='modifier'&&f.offset===48&&f.bytes===8);
  assertion(field,'required modifier amount field must exist');removed={hook:contract.name,field};contract.fields=contract.fields.filter(f=>f.field_id!==field.field_id);
  callbacks={...callbacks,arkdpsCaptureContract:contract};
 }
 dllHooks.push(Interceptor.attach(dllAddresses[i],callbacks));
}
for(const name of ['__arkdpsOnClosedRecord','__arkdpsCaptureModeState','__arkdpsInstallCaptureMode','__arkdpsRetireCaptureGraphs'])globalThis[name]=f.vm.runInContext(name,f.context);
Object.assign(rpc.exports,f.context.rpc.exports);
assertion(f.context.__arkdpsSamplingCapturePlan?.complete===true,'real sampler contract must be complete');
globalThis.__arkdpsSamplingCapturePlan=f.context.__arkdpsSamplingCapturePlan;
// The store was explicitly bound by the original mixed setup. No shadow store.
send({ready:true,fixture:'delivered-DLL-real-damage-formula-consumers',missing:removed,mismatch:MISMATCH,
 native_sampling_version:1,diagnostic_address:dllDiagnostic.toString(),world:{routes,ranges:demandRanges,diagnostic_address:dllDiagnostic.toString()},contracts:hooks.map(h=>h.arkdpsCaptureContract),
 expected_actors:{source:{cid:'char_shared',address:demandSource.toString(),instance_uid:301,character_data_uid:104},target:{cid:'enemy_mixed_demand',address:demandTarget.toString(),instance_uid:302}},
 initial_sequence:f.vm.runInContext('sequence',f.context),scope_fields:scope.fields.length,publication_fields:publication.chunks.reduce((n,c)=>n+c.fields.length,0)});
'''
    return prelude+text+tail.replace('MISSING',str(missing).lower()).replace('MISMATCH',str(mismatch).lower())
