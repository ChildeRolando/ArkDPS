"""Exercise exact graph binding through the real DLL in self-owned children."""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Thread
import json
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe


def source(binary, hostile=False):
    return r'''
const word=Memory.alloc(8);word.writeU64(42);
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET);
const field=(id,role,phase,side='none',guard=0)=>({field_id:id,role,phase,root:'absolute',absolute_address:word.toString(),
 root_index:0,pointer_offsets:[],offset:0,kind:'bytes',bytes:8,guard_side:side,guard_id:guard,required:true});
const scope={version:1,revision:1,mode_epoch:0,raw_collection:'all',available:true,body_insertion_index:1,
 fields:[field(1,'scope.open','checkpoint','before',1),field(2,'scope.close','checkpoint','after',1)]};
const publication={version:1,revision:1,mode_epoch:0,raw_collection:'all',available:true,chunks:[{fields:[
 field(1,'publication.open','checkpoint','before',1),field(2,'publication.close','checkpoint','after',1)]}]};
__publishCaptureScope(scope);__publishCapturePublication(publication);
globalThis.__arkdpsRetireCaptureGraphs=()=>true;
const plan={schema_version:1,complete:true,profile:'owned',name:'owned-graph-binding',address:address.toString(),
 abi:'win64-gp',arg_count:1,arg_mask:1,globals:[],max_bytes:1024,max_reads:32,required_projections:[],
 fields:[field(1,'body','entry'),field(2,'body','leave')]};
let entries=0,leaves=0,served=0;
function check(kind,graph){
 const batch=__arkdpsOwnedGraphReadBatch(kind,graph,0);
 if(!batch||batch.slot(0).getBigUint64(0,true)!==42n||batch.slot(1).getBigUint64(0,true)!==42n)
  throw Error('graph facts mismatch');
}
const hook=Interceptor.attach(address,{arkdpsName:'owned.graph-binding',arkdpsCaptureContract:plan,
 onEnter(){
  check('scope',scope);entries++;
  if(HOSTILE){try{check('scope',{...scope,revision:2});}catch(ignored){}}
 },
 onLeave(){check('scope',scope);leaves++;}
});
globalThis.__arkdpsPublicationServed=n=>{served=Math.max(served,n);};
globalThis.__arkdpsOnClosedRecord=record=>{
 check('publication',publication);
 return {mark:{sequence:0,epoch:entries,frame:leaves,game_time:leaves/30,active:0,active_hooks:[],error:null,stopped:false,
  native_capture_scope_mode_epoch:record.facts.scope_mode_epoch,entries,leaves},acknowledged:0,events_after:0,events_through:0,
  events:[],hp_context:[],read_fields:{sequence:0,epoch:entries},terminal_read:null};
};
rpc.exports={stop(){hook.detach();return {stopped:true};},ackEvents:()=>({acknowledged:0,error:'no fixture ACK permitted'})};
send({ready:true,fixture:'self-owned-native-graph-binding'});
'''.replace('OFFSET', str(export_offsets(binary)['ArkDpsTestInteger'])).replace('HOSTILE', str(hostile).lower())


def run(binary, hostile=False):
    with TemporaryDirectory(prefix='arkdps-graph-binding-') as directory:
        fixture=Fixture(Path(directory),binary)
        pipe=None
        try:
            text=source(binary,hostile)
            fixture.configure(source=text)
            fixture.command('load')
            pipe=NativePipe(fixture.pipe_name,fixture.identity['pid'],timeout=5)
            hello=pipe.request('initialize',token=fixture.token)
            assert 'fixture_setup_error' not in hello['ready'],hello
            answer=Queue()
            def request():
                try:answer.put(pipe.request('mark'))
                except BaseException as error:answer.put(error)
            pending=Thread(target=request)
            pending.start()
            time.sleep(.01)
            call=fixture.command('benchmark',count=1)
            assert call['total']==650 and call['count']==1,call
            pending.join(timeout=5)
            assert not pending.is_alive(),'owned graph mark did not finish'
            result=answer.get_nowait()
            if hostile:
                assert isinstance(result,RuntimeError) and 'native capture failed: code=6' in str(result),repr(result)
                observation=dict(rejected=True,error_type=type(result).__name__,error=str(result),original_abi_preserved=True)
            else:
                if isinstance(result,BaseException):raise result
                assert not result.get('retry') and result.get('error') is None,result
                assert result['entries']==result['leaves']==1,result
                counters=result['native_async_capture']
                assert counters['failure_code']==0 and counters['processed_ticket']==counters['reserved_ticket']==2,result
                observation=dict(rejected=False,reply=result,original_abi_preserved=True)
            return dict(identity=fixture.identity,hostile=hostile,observation=observation,
                        source_sha256=sha256(text.encode()).hexdigest())
        finally:
            if pipe:pipe.close()
            fixture.close()


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    binary=args.binary.resolve()
    receipt=dict(scope=__doc__,binary_sha256=sha256(binary.read_bytes()).hexdigest(),cases=[])
    try:
        for hostile in (False,True):receipt['cases'].append(run(binary,hostile))
        receipt['result']='PASS'
    except BaseException as error:
        receipt.update(result='FAIL',error_type=type(error).__name__,error=str(error))
        raise
    finally:
        args.receipt.parent.mkdir(parents=True,exist_ok=True)
        args.receipt.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print('PASS actual DLL graph binding and caught wrong-revision refusal; original ABI preserved')
