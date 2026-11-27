"""Actual DLL first publication-failure diagnostic, only a self-owned child.

No game attach, business simulation, or product budget changes: the custom
source deliberately chooses tiny journal budgets to challenge real failures.
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
from native_pc.native_event_runtime.smoke import Fixture,export_offsets
from native_pc.native_hook_source import NativePipe
from native_owned_event_store import empty_store_source


def source(binary,case):
    text=empty_store_source()+r'''
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET);
const plan={schema_version:1,complete:true,name:'owned.publication.failure',address:address.toString(),
 abi:'win64-gp',profile:'owned',arg_count:1,arg_mask:1,globals:[],fields:[],max_bytes:1024,max_reads:16,required_projections:[]};
const hook=Interceptor.attach(address,{arkdpsName:'owned.publication.failure',arkdpsCaptureContract:plan,onEnter(){},onLeave(){}});
ownedCanonicalStore.append({sequence:1,amount:40,payload:'fixture-owned-value'});
globalThis.__arkdpsOnClosedRecord=record=>{
 const mark={sequence:1,epoch:1,frame:1,game_time:1/30,active:0,active_hooks:[],error:null,stopped:false,
  native_capture_scope_mode_epoch:record.facts.scope_mode_epoch};
 const fields={frame:1},snapshot=ownedCanonicalStore.snapshot(0,1);
 if(CASE==='value'){
  mark.payload='PRIVATE_FIXTURE_VALUE'.repeat(50);__arkdpsPublicationJournal.maxBytes=300;
 }else{
  const fence={version:1,token:record.ticket+':1',closed_ticket:record.ticket,activity_begin:record.activity_begin,
   activity_end:record.activity_end,sequence:1,epoch:1,terminal_verified:false};
  const cost=ownProbeValue({...mark,native_publication:fence}).bytes+ownProbeValue(fields).bytes;
  __arkdpsPublicationJournal.maxBytes=ownedCanonicalStore.retainedBytes+cost-1;
 }
 return {mark,acknowledged:0,events_after:0,events_through:1,events:snapshot.events,
  hp_context:snapshot.hp_context,read_fields:fields,terminal_read:null};
};
rpc.exports={stop(){hook.detach();return {stopped:true};},ackEvents:n=>ownedCanonicalStore.ack(n)};
globalThis.__arkdpsPublicationServed=n=>ownedCanonicalStore.served(n);
send({ready:true,fixture:'owned-publication-failure'});
'''
    return text.replace('OFFSET',str(export_offsets(binary)['ArkDpsTestInteger'])).replace('CASE',json.dumps(case))


def run(binary):
    cases=[]
    for case,reason in [('value',18),('retention',19)]:
        with TemporaryDirectory(prefix='arkdps-publication-failure-') as temp:
            fixture=Fixture(Path(temp),binary);pipe=None
            try:
                text=source(binary,case);fixture.configure(source=text);fixture.command('load')
                pipe=NativePipe(fixture.pipe_name,fixture.identity['pid'],timeout=5)
                ready=pipe.request('initialize',token=fixture.token)
                assert 'fixture_setup_error' not in ready['ready'],ready
                replies=Queue()
                def mark():
                    try:replies.put(pipe.request('mark'))
                    except BaseException as error:replies.put(error)
                thread=Thread(target=mark);thread.start();time.sleep(.02)
                call=fixture.command('benchmark',count=1)
                assert call['count']==1 and call['total']==650,call
                thread.join(timeout=5);assert not thread.is_alive()
                error=replies.get_nowait()
                assert isinstance(error,RuntimeError),type(error).__name__
                assert 'code=9' in str(error) and 'PRIVATE' not in str(error),str(error)
                diagnostic=getattr(error,'publication_failure',None)
                assert diagnostic is not None,dict(error=str(error),missing='publication_failure')
                assert diagnostic['reason_code']==reason and diagnostic['origin']==1,diagnostic
                assert diagnostic['store_state_available'] and diagnostic['store_sequence']==1,diagnostic
                assert diagnostic['acknowledged']==diagnostic['read_through']==0,diagnostic
                assert diagnostic['published_through']==0 and diagnostic['marks_count']==0,diagnostic
                assert pipe._poisoned and 'PRIVATE' not in json.dumps(diagnostic)
                cases.append(dict(case=case,passed=True,identity=fixture.identity,
                    source_sha256=sha256(text.encode()).hexdigest(),diagnostic=diagnostic,
                    error_type=type(error).__name__,pipe_poisoned=True,original_call=call))
            except BaseException as error:
                error.owned_cases=cases
                error.owned_active=dict(case=case,identity=fixture.identity,source_sha256=sha256(text.encode()).hexdigest())
                raise
            finally:
                if pipe:pipe.close()
                fixture.close()
    return dict(passed=True,cases=cases,binary_sha256=sha256(binary.read_bytes()).hexdigest(),
        scope=__doc__,script_sha256=sha256(Path(__file__).read_bytes()).hexdigest())


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__);parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args();args.receipt.parent.mkdir(parents=True,exist_ok=True)
    try:receipt=run(args.binary.resolve())
    except BaseException as error:
        args.receipt.write_text(json.dumps(dict(passed=False,error_type=type(error).__name__,error=str(error),
            binary_sha256=sha256(args.binary.read_bytes()).hexdigest(),script_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
            active=getattr(error,'owned_active',None),cases=getattr(error,'owned_cases',[])),indent=2),encoding='utf-8');raise
    args.receipt.write_text(json.dumps(receipt,indent=2),encoding='utf-8');print('PASS_NATIVE_PUBLICATION_FAILURE_DIAGNOSTICS')
