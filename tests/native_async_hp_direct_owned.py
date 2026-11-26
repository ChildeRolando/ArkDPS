"""Real DLL direct HP proof plus publication/parent fallback in owned children."""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture,export_offsets
from native_pc.native_hook_source import NativePipe
from native_async_hp_observation_owned import production_plan,source as ordinary_source,request_and_call


def source(binary,layout,plan,fault=False):
    text=ordinary_source(binary,layout,plan,False)
    text=text.replace('let entries=0,leaves=0,served=0;const records=[];',r'''
let entries=0,leaves=0,served=0,directEntries=0,directLeaves=0,parentEntries=0,parentLeaves=0,argsProxies=0;const records=[];
const originalArguments=__arkdpsArguments;__arkdpsArguments=function(a,b){argsProxies++;return originalArguments(a,b);};
''')
    text=text.replace("const hook=Interceptor.attach(address,{arkdpsName:","const callbacks={arkdpsName:")
    text=text.replace(' onEnter(args){',' onEnter(args,providedIo){').replace(' onLeave(value){',' onLeave(value,providedIo){')
    text=text.replace('const io=__arkdpsOwnedHpObservation(contract,args[0]);', 'const io=providedIo??__arkdpsOwnedHpObservation(contract,args[0]);')
    text=text.replace('const io=__arkdpsOwnedHpObservation(contract,this.receiver);', 'const io=providedIo??__arkdpsOwnedHpObservation(contract,this.receiver);')
    text=text.replace("records.push({edge:'entry',", "records.push({route:providedIo===undefined?'fallback':'direct',edge:'entry',")
    text=text.replace("records.push({edge:'leave',", "records.push({route:providedIo===undefined?'fallback':'direct',edge:'leave',")
    end="});\nglobalThis.__arkdpsPublicationServed"
    assert end in text
    direct=r'''};
callbacks.arkdpsDirectHpEntry=function(enemy,io){
 directEntries++;
 if(__arkdpsReplayRecord().edge!=='entry'||__arkdpsIsPreparedHpObservation(io,contract,enemy)!==true)__arkdpsReplayGap('unverified direct fixture IO');
 if(DIRECT_FAULT==='constructor'){
  const forged=new __ArkDpsHpObservation(__arkdpsReplay,[],{names:Object.create(null)},enemy,enemy.value,contract,true);
  try{if(__arkdpsIsPreparedHpObservation(forged,contract,enemy)!==true)__arkdpsReplayGap('unverified constructor-created HP IO');}catch(ignored){}
  // Old vulnerable constructors can certify this empty fabricated payload.
  // Keep ordinary assertions/callbacks safe so silent direct-disable cannot pass.
  this.receiver=enemy;this.mode=__arkdpsReplayRecord().facts.mode_epoch;entries++;
  records.push({route:'direct',edge:'entry',mode:this.mode,checkpoint:0,uid:12,hp:'429496729600',id:'enemy_owned'});return;
 }
 if(DIRECT_FAULT){try{if(DIRECT_FAULT==='root')__arkdpsOwnedHpObservation(contract,enemy.add(8));else io.closing(enemy);}catch(ignored){}return;}
 // A tiny test-only array feeds the shared raw-fact assertions, not an Arguments
 // Proxy and not an ordinary factory. There is no statistics reducer here.
 callbacks.onEnter.call(this,[enemy],io);
};
callbacks.arkdpsDirectHpLeave=function(io){
 directLeaves++;
 if(__arkdpsReplayRecord().edge!=='leave'||__arkdpsIsPreparedHpObservation(io,contract)!==true)__arkdpsReplayGap('unverified direct fixture IO');
 callbacks.onLeave.call(this,undefined,io);
};
const hook=Interceptor.attach(address,callbacks);
const nestedAddress=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(NESTED_OFFSET);
const parentPlan={schema_version:1,complete:true,raw_collection:'all',profile:'owned',name:'owned-parent',address:nestedAddress.toString(),
 abi:'win64-gp',arg_count:1,arg_mask:1,globals:[],fields:[],max_bytes:1024,max_reads:32,required_projections:[]};
const parentHook=Interceptor.attach(nestedAddress,{arkdpsCaptureContract:parentPlan,onEnter(){parentEntries++;},onLeave(){parentLeaves++;}});
globalThis.__arkdpsPublicationServed'''.replace('DIRECT_FAULT',json.dumps(fault)).replace('NESTED_OFFSET',str(export_offsets(binary)['ArkDpsTestNested']))
    text=text.replace(end,direct)
    text=text.replace('entries,leaves,records:records.slice()', 'entries,leaves,directEntries,directLeaves,parentEntries,parentLeaves,argsProxies,records:records.slice()')
    text=text.replace('stop(){hook.detach();', 'stop(){parentHook.detach();hook.detach();')
    return text


def run(binary,source_root,case,require_batch=False):
    fault=case if case in ('root','phase','constructor') else False
    with TemporaryDirectory(prefix='arkdps-direct-hp-') as directory:
        directory=Path(directory);f=Fixture(directory/'child',binary);pipe=None
        try:
            layout=f.command('owned-hp-buffer');plan=production_plan(source_root,layout,directory)
            text=source(binary,layout,plan,fault);f.configure(source=text);f.command('load')
            pipe=NativePipe(f.pipe_name,f.identity['pid'],timeout=5)
            hello=pipe.request('initialize',token=f.token);assert 'fixture_setup_error' not in hello['ready'],hello
            # No host publication request: these HP edges must actually be direct.
            warm=f.command('call-owned-hp',nested=case=='parent')
            assert warm['results']==[warm['expected']],warm
            result,calls=request_and_call(pipe,f)
            if fault:
                assert isinstance(result,RuntimeError),repr(result)
                if case=='constructor':
                    # JS proof rejection is reported through the bounded replay
                    # audit; native boundary rejection uses capture code 6.
                    assert any(marker in str(result) for marker in (
                        'structural gaps','native_replay_gap:v1,','native capture failed: code=6')),str(result)
                else:
                    assert 'native capture failed: code=6' in str(result),str(result)
                assert pipe._poisoned is True
                return dict(case=case,rejected=True,error=str(result),warm=warm,calls=calls,
                    original_abi_preserved=True,source_sha256=sha256(text.encode()).hexdigest())
            if isinstance(result,BaseException):raise result
            if result.get('retry'):
                result,extra=request_and_call(pipe,f);calls.extend(extra)
                if isinstance(result,BaseException):raise result
            assert not result.get('retry') and result.get('error') is None,result
            hp_calls=1+len(calls)
            assert result['entries']==result['leaves']==hp_calls,result
            assert result['native_async_capture']['failure_code']==0,result
            if case=='normal':
                assert len(calls)==1,'fixture requires one warm and one publication call'
                assert result['directEntries']==2 and result['directLeaves']==1,result
                assert result['argsProxies']==0,result
                assert [r['route'] for r in result['records']]==['direct','direct','direct','fallback'],result
                raw_count=4
            else:
                assert result['parentEntries']==result['parentLeaves']==1,result
                assert result['records'][0]['route']==result['records'][1]['route']=='fallback',result
                assert result['directEntries']==len(calls) and result['directLeaves']==len(calls)-1,result
                assert result['argsProxies']==2,result # parent E plus nested HP E
                raw_count=4+len(calls)*2
            counters=result['native_async_capture']
            if require_batch:
                assert counters['batch_calls']>0 and counters['batch_issued']>0,counters
                assert counters['batch_issued']==counters['batch_finalized'],counters
                assert 1<=counters['maximum_batch_edges']<=32,counters
                assert counters['batch_issued']+counters['single_replayed']==raw_count,counters
                assert counters['single_replayed']>0,counters # publication is a hard barrier
                assert counters['batch_fallback_reasons']['cold_or_publication']>0,counters
            assert counters['reserved_ticket']==counters['processed_ticket']==counters['closed_ticket']==counters['published_ticket']==raw_count,result
            assert int(result['native_publication']['closed_ticket'])==raw_count,result
            assert counters['delivered_event_sequence']==0,result
            return dict(case=case,rejected=False,reply=result,warm=warm,calls=calls,original_abi_preserved=True,
                source_sha256=sha256(text.encode()).hexdigest(),plan_sha256=sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest())
        finally:
            if pipe:pipe.close()
            f.close()


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--source-root',type=Path,default=ROOT);parser.add_argument('--receipt',type=Path,required=True);parser.add_argument('--require-batch',action='store_true');a=parser.parse_args();binary=a.binary.resolve()
    receipt=dict(scope=__doc__,binary_sha256=sha256(binary.read_bytes()).hexdigest(),test_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),cases=[])
    receipt['source_root_head']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.source_root.resolve(),text=True).strip()
    build=binary.with_suffix('.build.json')
    if build.is_file():receipt.update(build_metadata_sha256=sha256(build.read_bytes()).hexdigest(),build_metadata=json.loads(build.read_text(encoding='utf-8')))
    try:
        for case in ('normal','parent','root','phase','constructor'):receipt['cases'].append(run(binary,a.source_root.resolve(),case,a.require_batch))
        receipt['result']='PASS'
    except BaseException as error:
        receipt.update(result='FAIL',error_type=type(error).__name__,error=str(error));raise
    finally:
        a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print('PASS actual direct HP, publication/parent fallback, strict fault and original ABI')
