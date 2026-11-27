"""Adapt the existing full mixed fixture to the delivered DLL's real bridge."""
import json
from capture_demand_engine_bundle import damage_bundle
from native_pc.native_event_runtime.smoke import export_offsets

def source(source_root,helper,missing=False,mismatch=False,ordinary=False):
    text=damage_bundle(source_root,{'count':1,'route':0,'profile':False})
    start=text.index('\n// Narrow compatibility surface for the unmodified PC event probe.')
    end=text.index('\n// Execute the production probe with an owned, in-memory IL2CPP fixture.',start)
    text=text[:start]+text[end:]
    # Initialization helpers are replaced with actual DLL graph publication;
    # no helper is callable by a business callback during replay.
    prelude=(source_root/'event_probe_store.js').read_text(encoding='utf-8')+'\n'
    prelude+='const dllScopePublisher=__publishCaptureScope,dllPublicationPublisher=__publishCapturePublication;\n'
    prelude+='const dllGraphReceipts=[];globalThis.__ownedGraphPublish=(publication,graph)=>{dllGraphReceipts.push({publication,revision:graph.revision,active:__arkdpsReplayActive(),facts:__arkdpsReplayActive()?JSON.parse(JSON.stringify(__arkdpsReplayRecord().facts)):null});return (publication?dllPublicationPublisher:dllScopePublisher)(graph);};\n'
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
    if ordinary:
        # The first binding and both future graphs must come from the native
        # sampler's actual cold snapshot, never from a setup-time live mark.
        warm=[line for line in text.splitlines() if line.startswith('if(true){const warm=f.mark();')]
        assert len(warm)==1
        text=text.replace(warm[0],"f.write(f.level.add(0x18),f.text('Obt/Hard/level_hard_07-04'));f.vm.runInContext('nativeAutoStage=null;nativeAutoCache=null;nativeLastBinding=null;discardAutoChessBody();',f.context);")
        marker='// Serialize the same owned fixture\'s explicit cells.'
        assert text.count(marker)==1
        # Match the independently reviewed nested normal/bar/elemental
        # topology with exact fixed-point values and explicit native writes.
        ordinary_route=r'''
const ordinaryChild=f.modifier(f.ptr(0),demandTarget,60,0,5),ordinaryBar=f.modifier(demandSource,demandTarget,5,0,0);
f.write(ordinaryBar.add(0x60),5);f.write(demandMod.add(0x68),2);
for(const [mod,before,after]of [[demandMod,40n,40n],[ordinaryChild,60n,60n]]){f.write(mod.add(0x80),before<<32n);f.write(mod.add(0x88),after<<32n);}
routes.length=0;
routes.push([
 en(0,A,[w(demandTarget.add(0x40),100n<<32n),w(demandMod.add(0x48),-40n<<32n)]),en(3,A),lv(3),
 en(0,[demandTarget,ordinaryBar],[w(demandTarget.add(0x40),60n<<32n)]),en(0,[demandTarget,ordinaryChild]),
 lv(0,0n,[w(ordinaryChild.add(0x48),-60n<<32n),w(demandTarget.add(0x40),0n)]),lv(0),lv(0)
]);
'''
        text=text.replace(marker,ordinary_route+'\n'+marker)
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
const dllPrefixReceipts=[],dllRealPrefix=globalThis.__arkdpsOnClosedRecord;
globalThis.__arkdpsOnClosedRecord=function(record){
 const beforeFacts=ORDINARY?JSON.stringify(record.facts):null;
 const projection=dllRealPrefix(record);
 if(ORDINARY){assertion.equal(JSON.stringify(record.facts),beforeFacts,'future graph publication cannot retrofit this record');dllPrefixReceipts.push({facts:JSON.parse(beforeFacts),ticket:record.ticket,kind:record.kind,frame:projection?.mark?.frame,accepted:!!projection});
 }
 return projection;
};
Object.assign(rpc.exports,f.context.rpc.exports);
if(ORDINARY){const status=rpc.exports.captureControlStatus;rpc.exports.captureControlStatus=function(){return {...status(),dll_audit:{records:dllPrefixReceipts.slice(),graphs:dllGraphReceipts.slice()}};};}
assertion(f.context.__arkdpsSamplingCapturePlan?.complete===true,'real sampler contract must be complete');
globalThis.__arkdpsSamplingCapturePlan=f.context.__arkdpsSamplingCapturePlan;
// The store was explicitly bound by the original mixed setup. No shadow store.
if(ORDINARY)assertion.equal(dllGraphReceipts.length,0,'ordinary setup must not prepublish any graph');
send({ready:true,fixture:'delivered-DLL-real-damage-formula-consumers',missing:removed,mismatch:MISMATCH,ordinary:ORDINARY,setup_graph_publications:dllGraphReceipts.length,
 native_sampling_version:1,diagnostic_address:dllDiagnostic.toString(),world:{routes,ranges:demandRanges,diagnostic_address:dllDiagnostic.toString()},contracts:hooks.map(h=>h.arkdpsCaptureContract),
 expected_actors:{source:{cid:'char_shared',address:demandSource.toString(),instance_uid:301,character_data_uid:104},target:{cid:'enemy_mixed_demand',address:demandTarget.toString(),instance_uid:302}},
 initial_sequence:f.vm.runInContext('sequence',f.context),scope_fields:scope?.fields.length??0,publication_fields:publication?.chunks.reduce((n,c)=>n+c.fields.length,0)??0});
'''
    return prelude+text+tail.replace('MISSING',str(missing).lower()).replace('MISMATCH',str(mismatch).lower()).replace('ORDINARY',str(ordinary).lower())
