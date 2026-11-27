"""Compiled DLL omission, nesting and fresh publication; owned children only."""
from argparse import ArgumentParser
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from native_async_hp_observation_owned import production_plan, source as ordinary_source, request_and_call
from native_pc.native_event_runtime.smoke import Fixture
from native_pc.native_hook_source import NativePipe

ROOT=Path(__file__).resolve().parents[1]

def run(binary, nested):
    with TemporaryDirectory(prefix='arkdps-light-hp-') as directory:
        directory=Path(directory)
        f=Fixture(directory/'child',binary)
        pipe=None
        try:
            layout=f.command('owned-hp-buffer')
            plan=production_plan(ROOT,layout,directory)
            plan['hp_filter']['snapshot_hp']=True
            text=ordinary_source(binary,layout,plan)
            start=text.index('const hook=Interceptor.attach')
            end=text.index('globalThis.__arkdpsPublicationServed',start)
            text=text[:start]+"const hook=Interceptor.attach(address,{arkdpsName:'hp.Entity.SetHpInternal',arkdpsCaptureContract:contract,onEnter(){entries++;},onLeave(){leaves++;}});\n"+text[end:]
            if nested:
                # Retain a real physical parent. HP within it must not omit.
                from native_pc.native_event_runtime.smoke import export_offsets
                declaration="const parentAddress=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add("+str(export_offsets(binary)['ArkDpsTestNested'])+");const parentPlan={schema_version:1,complete:true,profile:'owned',name:'owned-parent',address:parentAddress.toString(),abi:'win64-gp',arg_count:1,arg_mask:1,globals:[],fields:[],max_bytes:1024,max_reads:32,required_projections:[]};const parent=Interceptor.attach(parentAddress,{arkdpsCaptureContract:parentPlan,onEnter(){},onLeave(){}});\n"
                text=text.replace("send({ready:true,",declaration+"send({ready:true,")
            f.configure(source=text);f.command('load')
            pipe=NativePipe(f.pipe_name,f.identity['pid'],timeout=5)
            hello=pipe.request('initialize',token=f.token)
            assert 'fixture_setup_error' not in hello['ready'],hello
            warm=f.command('call-owned-hp',nested=nested)
            assert warm['results']==[warm['expected']],warm
            reply,calls=request_and_call(pipe,f)
            if isinstance(reply,BaseException):raise reply
            assert not reply.get('retry') and reply.get('error') is None,reply
            assert len(calls)==1,calls
            metrics=reply['native_async_capture']
            total=6 if nested else 4
            assert metrics['failure_code']==0,reply
            assert metrics['reserved_ticket']==metrics['processed_ticket']==metrics['closed_ticket']==metrics['published_ticket']==total,reply
            assert metrics['delivered_event_sequence']==0 and reply['native_publication']['sequence']==0,reply
            # Only actual publication executes root onLeave; no HP entry is
            # fabricated to make that callback succeed. Nested HP retains both.
            assert reply['entries']==(1 if nested else 0),reply
            assert reply['leaves']==(2 if nested else 1),reply
            assert metrics['planned_hp_boundary_edges']==(1 if nested else 3),reply
            return dict(nested=nested,reply=reply,warm=warm,calls=calls,
                source_sha256=sha256(text.encode()).hexdigest(),original_abi_preserved=True)
        finally:
            if pipe:pipe.close()
            f.close()

if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    a=parser.parse_args();binary=a.binary.resolve()
    receipt=dict(scope=__doc__,excluded=['game','business settlement','FPS'],binary_sha256=sha256(binary.read_bytes()).hexdigest(),cases=[])
    try:
        for nested in (False,True):receipt['cases'].append(run(binary,nested))
        receipt['result']='PASS'
    except BaseException as error:
        receipt.update(result='FAIL',error_type=type(error).__name__,error=str(error));raise
    finally:
        a.receipt.parent.mkdir(parents=True,exist_ok=True)
        a.receipt.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print('PASS compiled DLL planned HP boundaries, nested retention and fresh publication')
