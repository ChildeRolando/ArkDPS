"""Owned HP inclusive profiler; wraps existing functions, never changes production sources."""
from pathlib import Path
from hashlib import sha256
import argparse,json,subprocess
from production_hp_quickjs_bundle import bundle
from run_production_hp_quickjs import seal_business_events
SETUP = 'let sampledFunctions={};let profileEdge="outside";\nfunction sampleFunction(fn,name){return function(){const label=name===\'withCapturedMode\'?name+\'.\'+arguments[0].edge:name===\'nativeIO\'?name+\'.\'+arguments[0]+\'.\'+profileEdge:name+\'.\'+profileEdge;let t=sampledFunctions[label];if(!t)t=sampledFunctions[label]={calls:0,samples:0,sampled_us:0,max_us:0};const take=(t.calls++&127)===0;if(!take)return fn.apply(this,arguments);const start=__monotonicMs();try{return fn.apply(this,arguments);}finally{const us=(__monotonicMs()-start)*1000;t.samples++;t.sampled_us+=us;t.max_us=Math.max(t.max_us,us);}};}\nf.context.sampleFunction=sampleFunction;\nfor(const name of [\'time\',\'hpPointClock\',\'entityId\',\'hpPointId\',\'enemyMaxHp\',\'capturedNativeScopePoint\',\'recordEnemyHp\',\'withCapturedMode\',\'applyHpEntry\',\'applyHpLeave\'])f.vm.runInContext(\'if(typeof \'+name+\'==="function")\'+name+\'=globalThis.sampleFunction(\'+name+\',\'+JSON.stringify(name)+\')\',f.context);\nf.vm.runInContext("probeAdapter.captureScopePoint=globalThis.sampleFunction(probeAdapter.captureScopePoint,\'scope.adapter\')",f.context);\nfor(const name of [\'__arkdpsReplayDispatch\',\'__arkdpsPackedCaptureRecord\',\'__arkdpsReplayBegin\',\'__arkdpsReplayEnd\',\'__arkdpsPreparedHpObservation\',\'__arkdpsOwnedGraphPrimitives\',\'__arkdpsOwnedGraphCheck\'])if(typeof globalThis[name]===\'function\')globalThis[name]=sampleFunction(globalThis[name],name);\n__arkdpsNativeCall=sampleFunction(__arkdpsNativeCall,\'nativeIO\');__ownedPrepareHp=sampleFunction(__ownedPrepareHp,\'native.prepareHp\');const sampledDispatch=__arkdpsReplayDispatch;__arkdpsReplayDispatch=function(){const previous=profileEdge;profileEdge=arguments[2]===undefined?\'entry\':\'leave\';try{return sampledDispatch.apply(this,arguments);}finally{profileEdge=previous;}};\nsetter.arkdpsDirectHpEntry=sampleFunction(setter.arkdpsDirectHpEntry,\'HP.direct.entry\');setter.arkdpsDirectHpLeave=sampleFunction(setter.arkdpsDirectHpLeave,\'HP.direct.leave\');\n\n'
def run(source,binary,output):
    output.mkdir(parents=True,exist_ok=True)
    script=bundle(5000,True,source,source,True,8571)
    script=script.replace('const results=[];',SETUP+'const results=[];').replace('__ownedProfile(true);nativeRequests=', '__ownedProfile(true);sampledFunctions={};nativeRequests=').replace('results.push({business_events_json:', 'results.push({sampled_functions:sampledFunctions,business_events_json:')
    target=output/'sampled-phase-correct.js';target.write_text(script,encoding='utf-8')
    command=[str(binary),str(target),'--direct-hp','--sample-functions']
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    (output/'stdout.json').write_text(result.stdout,encoding='utf-8');(output/'stderr.txt').write_text(result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError(result.stderr)
    report=seal_business_events(json.loads(result.stdout));report['command']=command
    report['provenance']={'binary_sha256':sha256(binary.read_bytes()).hexdigest(),'executed_script_sha256':sha256(target.read_bytes()).hexdigest(),'unmodified_source_files':{str(p.relative_to(source)):sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()},'instrumentation':'Runtime wrappers of actual functions, independent entry/leave counters, 1/128 inclusive; includes nested wrapper overhead'}
    (output/'receipt.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.source.resolve(),a.binary.resolve(),a.output.resolve())
