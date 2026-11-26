"""Real DLL/QuickJS complete-class-hierarchy semantics in an owned process only.

No build, game attach, timing benchmark or FPS claim. Requires the DLL containing
cold class hierarchy APIs, native class_set and finite hierarchy diagnostics.
"""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_async_owned_view import fresh_mark
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe


def source(binary, case):
    return r"""
const mode=CASE,parents=new Map([['0x1','0x0']]);
for(let i=2;i<=40;i++)parents.set(ptr(i).toString(),ptr(i-1).toString());
if(mode==='cycle')parents.set('0x1','0x28');
__arkdpsSealClassHierarchy(parents);
const owned=Memory.alloc(16);owned.writeU64(mode==='unknown'?99n:40n);owned.add(8).writeU64(73n);
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET);
const fields=['entry','leave'].map((phase,index)=>({field_id:index+1,phase,role:'body',root:'absolute',root_index:0,absolute_address:owned.toString(),pointer_offsets:[],offset:0,bytes:0,kind:'projection_tree',projection_root:0,guard_id:0,guard_side:'none',required:true}));
const plan={schema_version:1,complete:true,raw_collection:'all',name:'owned-class-hierarchy',address:address.toString(),abi:'win64-gp',profile:'owned',arg_count:1,arg_mask:1,globals:[],fields,max_bytes:128,max_reads:32,required_projections:[],projections:[{kind:'object',gate:'class_set',expected_classes:__arkdpsColdAssignableClasses(ptr(1)),child_begin:1,child_count:1},{kind:'object',offset:8,bytes:4}]};
let entries=0,leaves=0,childReads=0,served=0;const errors=[];
const hook=Interceptor.attach(address,{arkdpsName:'owned.class-hierarchy',arkdpsCaptureContract:plan,
 onEnter(args){entries++;try{__arkdpsReplayPoint('body',()=>{
  if(args[0].toInt32()!==1)errors.push('arg');
  if(__arkdpsCapturedAssignable(ptr(1),owned.readPointer())){childReads++;if(owned.add(8).readU32()!==73)errors.push('child');}
 });}catch(ignored){/* A caught hierarchy error MUST remain sticky. */}},
 onLeave(){leaves++;}});
globalThis.__arkdpsPublicationServed=through=>served=Math.max(served,through);
globalThis.__arkdpsOnClosedRecord=record=>({mark:{sequence:0,epoch:entries,frame:leaves,game_time:leaves/30,active:0,active_hooks:[],error:null,stopped:false,entries,leaves,childReads,source_assertion_errors:errors.slice(),native_capture_scope_mode_epoch:record.facts.scope_mode_epoch??record.facts.mode_epoch},acknowledged:0,events_after:0,events_through:0,events:[],hp_context:[],read_fields:{sequence:0,epoch:entries},terminal_read:null});
rpc.exports={mark(){throw Error('legacy mark');},readEvents(){throw Error('legacy read');},ackEvents:sequence=>({acknowledged:0,error:sequence<=served?null:'unserved'}),stop(){hook.detach();return {stopped:true};}};
send({ready:true,fixture:'owned-complete-class-hierarchy'});
""".replace('CASE', json.dumps(case)).replace('OFFSET', str(export_offsets(binary)['ArkDpsTestInteger']))


def run(binary, case):
    with TemporaryDirectory(prefix='arkdps-class-hierarchy-'+case+'-') as temporary:
        fixture = Fixture(Path(temporary)/case, binary)
        pipe = None
        try:
            fixture.configure(source=source(binary, case))
            fixture.command('load')
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            if case == 'cycle':
                try:
                    reply = pipe.request('initialize', token=fixture.token)
                except RuntimeError as error:
                    text = str(error)
                    assert 'initialize failed; error_type=Error' in text, text
                    assert 'cold class hierarchy cycle' in text, text
                    return {'result':'PASS','rejection':text,'ack_attempted':False}
                raise AssertionError(('cyclic setup accepted', reply))
            ready = pipe.request('initialize', token=fixture.token)
            if case == 'positive':
                mark = fresh_mark(pipe, fixture)
                assert mark.get('error') is None and not mark.get('retry'), mark
                assert mark['source_assertion_errors'] == [], mark
                assert mark['entries'] == mark['leaves'] == mark['childReads'] == fresh_mark.last_nudges >= 1, mark
                assert mark['native_async_capture']['failure_code'] == 0, mark
                token = mark['native_publication']['token']
                batch = pipe.request('read', publication_token=token, after=0, through=0)
                assert batch.get('error') is None and batch['events'] == [], batch
                ack = pipe.request('ack', sequence=0)
                assert ack.get('error') is None and ack['acknowledged'] == 0, ack
                return {'result':'PASS','identity':fixture.identity,'ready':ready,'mark':mark,'read':batch,'ack':ack,'original_integer_per_nudge':650}
            abi = fixture.command('benchmark', count=1)
            assert abi['count'] == 1 and abi['total'] == 650, abi
            try:
                reply = pipe.request('mark')
            except RuntimeError as error:
                text = str(error)
                assert 'native_replay_gap:v1' in text and re.search(r'(?:^|,)k=16(?:,|$)', text), text
                return {'result':'PASS','identity':fixture.identity,'original_abi':abi,'rejection':text,'ack_attempted':False}
            raise AssertionError(('caught unknown class did not seal replay', reply))
        finally:
            if pipe:
                pipe.close()
            fixture.close()


def main():
    parser = ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--case', choices=['all','positive','unknown','cycle'], default='all')
    options = parser.parse_args()
    binary = options.binary.resolve()
    receipt = {'kind':'real DLL self-owned class hierarchy semantics; no game or FPS claim', 'binary_sha256':sha256(binary.read_bytes()).hexdigest(), 'script_sha256':sha256(Path(__file__).read_bytes()).hexdigest(), 'cases':{}}
    cases = ['positive','unknown','cycle'] if options.case == 'all' else [options.case]
    try:
        for case in cases:
            receipt['cases'][case] = run(binary, case)
        receipt['result'] = 'PASS'
    except BaseException as error:
        receipt['result'] = 'FAIL'
        receipt['cases'][case] = {'result':'FAIL','error_type':type(error).__name__,'error':str(error)}
        raise
    finally:
        options.receipt.parent.mkdir(parents=True, exist_ok=True)
        options.receipt.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print('PASS self-owned class hierarchy:', ', '.join(cases))


if __name__ == '__main__':
    main()
