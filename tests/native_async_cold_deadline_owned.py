"""Self-owned real DLL regression for cold publication across mark deadlines.

The source deliberately rejects one warm and one cold candidate. All native
reads/closed certificates are real owned fixture memory, never a game process.
"""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Thread
import json
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe


def source(binary):
    return r'''
const owned=Memory.alloc(8);owned.writeU64(42);
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET);
function field(id,role,phase,side='none',guard=0){return {field_id:id,role,phase,root:'absolute',absolute_address:owned.toString(),root_index:0,pointer_offsets:[],offset:0,bytes:8,kind:'bytes',guard_id:guard,guard_side:side,required:true};}
const cold={version:1,max_bytes:1024,max_reads:32,fields:[field(1,'cold.root_open','checkpoint','before',1),field(2,'cold.body','checkpoint'),field(3,'cold.root_close','checkpoint','after',1)]};
const plan={schema_version:1,complete:true,profile:'owned',name:'owned-deadline-cold',address:address.toString(),abi:'win64-gp',arg_count:1,arg_mask:1,globals:[],max_bytes:1024,max_reads:32,required_projections:[],fields:[field(1,'body','entry'),field(2,'body','leave')],cold_plan:cold};
// This fixture retains no JS graph-revision cache; native retirement has no JS ownership to release.
globalThis.__arkdpsRetireCaptureGraphs=bounds=>{for(const key of Object.keys(bounds))if(!['scope_before','publication_before','mode_before'].includes(key)||!Number.isSafeInteger(bounds[key])||bounds[key]<0)throw Error('retirement bound');return true;};
__publishCaptureScope({version:1,revision:1,mode_epoch:0,raw_collection:'all',available:true,body_insertion_index:1,fields:[field(1,'scope.open','checkpoint','before',1),field(2,'scope.close','checkpoint','after',1)]});
__publishCapturePublication({version:1,revision:1,mode_epoch:0,raw_collection:'all',available:true,chunks:[{fields:[field(1,'publication.open','checkpoint','before',1),field(2,'publication.close','checkpoint','after',1)]}]});
let entries=0,coldAttempts=0,warmFailures=0,acceptedCold=false,served=0;
const candidates=[];
const hook=Interceptor.attach(address,{arkdpsName:'owned.deadline-cold',arkdpsCaptureContract:plan,
 onEnter(){entries++;if(entries===1){const end=Date.now()+220;while(Date.now()<end){}}},onLeave(){}});
globalThis.__arkdpsPublicationServed=n=>{served=Math.max(served,n);};
globalThis.__arkdpsOnClosedRecord=record=>{
 const isCold=record.facts.cold_revision!==undefined;
 candidates.push({request:record.publication_request,ticket:record.ticket,cold:isCold});
 if(isCold){
  for(const role of ['cold.root_open','cold.body','cold.root_close']){
   const value=__arkdpsReplayWitness(role,()=>owned.readU64().toString());
   if(value!=='42')throw Error('cold owned proof changed');
  }
  coldAttempts++;
  if(coldAttempts<2)return null; // A failed cold publication cannot clear need.
  acceptedCold=true;
 }else if(!acceptedCold){warmFailures++;return null;}
 return {mark:{sequence:0,epoch:entries,frame:entries,game_time:entries/30,active:0,active_hooks:[],error:null,stopped:false,
  native_capture_scope_mode_epoch:record.facts.scope_mode_epoch,coldAttempts,warmFailures,current_cold:isCold,candidates:candidates.slice()},
 acknowledged:0,events_after:0,events_through:0,events:[],hp_context:[],read_fields:{sequence:0,epoch:entries},terminal_read:null};
};
rpc.exports={stop(){hook.detach();return {stopped:true};},ackEvents:()=>({acknowledged:0,error:'no fixture ACK permitted'})};
send({ready:true,fixture:'self-owned-cold-deadline'});
'''.replace('OFFSET',str(export_offsets(binary)['ArkDpsTestInteger']))


def run(binary):
    attempts=[]
    with TemporaryDirectory(prefix='arkdps-cold-deadline-') as temporary:
        fixture=Fixture(Path(temporary),binary);pipe=None
        try:
            text=source(binary);fixture.configure(source=text);fixture.command('load')
            pipe=NativePipe(fixture.pipe_name,fixture.identity['pid'],timeout=5)
            hello=pipe.request('initialize',token=fixture.token)
            assert 'fixture_setup_error' not in hello['ready'],hello
            def mark_with_one_call():
                result=Queue()
                def request():
                    try:result.put(pipe.request('mark'))
                    except BaseException as error:result.put(error)
                pending=Thread(target=request);pending.start();time.sleep(.01)
                call=fixture.command('benchmark',count=1)
                assert call['total']==650 and call['count']==1,call
                pending.join(timeout=5);assert not pending.is_alive()
                answer=result.get_nowait()
                if isinstance(answer,BaseException):raise answer
                attempts.append(answer)
                time.sleep(.03) # Let the old candidate replay after host timeout.
                return answer
            first=mark_with_one_call();assert first.get('retry') is True,first
            second=mark_with_one_call();assert second.get('retry') is True,second
            third=mark_with_one_call()
            assert not third.get('retry') and third.get('error') is None,third
            assert third['coldAttempts']==2 and third['warmFailures']==1,third
            candidates=third['candidates'];assert [x['cold'] for x in candidates]==[False,True,True],candidates
            assert len({x['request'] for x in candidates})==3,candidates
            assert third['native_async_capture']['failure_code']==0,third
            fourth=mark_with_one_call()
            assert not fourth.get('retry') and fourth['current_cold'] is False,fourth
            assert [x['cold'] for x in fourth['candidates']]==[False,True,True,False],fourth
            assert fourth['native_async_capture']['failure_code']==0,fourth
            return dict(result='PASS',scope=__doc__,identity=fixture.identity,
                binary_sha256=sha256(binary.read_bytes()).hexdigest(),source_sha256=sha256(text.encode()).hexdigest(),
                attempts=attempts,claims_per_request=1,no_ack=True)
        except BaseException as error:
            error.owned_attempts=attempts
            raise
        finally:
            if pipe:pipe.close()
            fixture.close()


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    try:receipt=run(args.binary.resolve())
    except BaseException as error:
        receipt=dict(result='FAIL',error_type=type(error).__name__,error=str(error),attempts=getattr(error,'owned_attempts',[]))
        args.receipt.parent.mkdir(parents=True,exist_ok=True);args.receipt.write_text(json.dumps(receipt,indent=2),encoding='utf-8');raise
    args.receipt.parent.mkdir(parents=True,exist_ok=True);args.receipt.write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(receipt['result'])
