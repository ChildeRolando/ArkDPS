"""Real DLL HP-provider binding in a fresh self-owned child; never a game PID."""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event, Thread
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe


def production_plan(source_root, layout, directory):
    cold = directory / 'cold.json'
    completed = subprocess.run(['node', str(source_root/'tests/native_pc_cold_plan_fixture.js'), str(cold)],
        cwd=source_root, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)
    # Call the exact producer plan generator/finalizer. Fixture metadata points
    # only into the child allocations. The actual cold template is retained;
    # this narrow warm-graph test does not claim to exercise cold traversal.
    program = r'''
const fs=require('fs'),vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')),adapter=fs.readFileSync(input.adapter,'utf8');
const first=adapter.indexOf('function nativeCapturePlan('),last=adapter.indexOf('// copies both live observations',first);
if(first<0||last<0)throw Error('producer capture-plan boundaries missing');
const context={};context.globalThis=context;
for(const name of ['__arkdpsReplayPoint','__arkdpsReplayWitness','__arkdpsReplayCheckpoint','__arkdpsReplayOptional','__arkdpsReplayEnd','__arkdpsOwnedRead'])context[name]=()=>{};
vm.createContext(context);vm.runInContext(adapter.slice(first,last),context);
const a=input.layout.addresses;
const metadata={clock:a.clock,string_class:a.string_class,classes:{Enemy:a.enemy_class,BObject:a.bobject_class,Character:a.bobject_class},
 skill_offsets:{},skill_wait:null,attack_type_offset:48,factory_addresses:[],immutable_class_parents:true,
 coalesce_enemy_hp:true,cold_metadata_ready:true,cold_plan:input.cold};
const capabilities={version:1,raw_collection:'all',lowered_projections:['live_scope_guards','same_point_class_ancestry','open_parent_checkpoint','token_host_and_character_form','full_native_binding','current_buff_and_blackboard_keys']};
const raw=context.nativeCapturePlan('hp.Entity.SetHpInternal',{toString:()=> '0x1'},metadata);
const plan=context.finalizeNativeCapturePlan(raw,metadata,capabilities);
if(!plan.complete||plan.profile!=='hp'||!plan.cold_plan||!plan.fields.some(f=>f.observation))throw Error('normal production HP contract unavailable');
process.stdout.write(JSON.stringify(plan));
'''
    result = subprocess.run(['node', '-e', program], input=json.dumps(dict(
        adapter=str(source_root/'event_probe_native_pc.js'), layout=layout,
        cold=json.loads(cold.read_text(encoding='utf-8')))), cwd=source_root,
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def source(binary, layout, plan, hostile=False):
    return r'''
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET);
const contract=PLAN;contract.address=address.toString();contract.mode_epoch=0;
function freeze(value){if(value&&typeof value==='object'){for(const key of Object.keys(value))freeze(value[key]);Object.freeze(value);}return value;}
freeze(contract);
const graphWord=Memory.alloc(8);graphWord.writeU64(42);
const field=(id,role,side)=>({field_id:id,role,phase:'checkpoint',root:'absolute',absolute_address:graphWord.toString(),
 root_index:0,pointer_offsets:[],offset:0,bytes:8,kind:'bytes',guard_id:1,guard_side:side,required:true,
 projection:false,comparison:{version:1,words:[{offset:0,kind:'u32',value:42},{offset:4,kind:'u32',value:0}]}});
let mode={version:1,mode_epoch:0,desired_mode:'all',effective_mode:null},scope,publication;
function installGraphs(epoch){
 scope={version:1,revision:epoch+1,mode_epoch:epoch,raw_collection:'all',available:true,body_insertion_index:1,
  fields:[field(1,'scope.owned.open','before'),field(2,'scope.owned.close','after')]};
 publication={version:1,revision:epoch+1,mode_epoch:epoch,raw_collection:'all',available:true,chunks:[{fields:[
  field(1,'publication.owned.open','before'),field(2,'publication.owned.close','after')]}]};
 __publishCaptureScope(scope);__publishCapturePublication(publication);
}
installGraphs(0);
globalThis.__arkdpsCaptureModeState=()=>({...mode});
globalThis.__arkdpsInstallCaptureMode=(enabled,barrier)=>{
 if(typeof enabled!=='boolean'||barrier.version!==1||barrier.mode_epoch!==mode.mode_epoch+1)throw Error('invalid owned mode install');
 installGraphs(barrier.mode_epoch);mode={...mode,mode_epoch:barrier.mode_epoch,desired_mode:enabled?'all':'local'};return {...mode};
};
globalThis.__arkdpsRetireCaptureGraphs=()=>true;
let entries=0,leaves=0,served=0;const records=[];
const hook=Interceptor.attach(address,{arkdpsName:'hp.Entity.SetHpInternal',arkdpsCaptureContract:contract,
 onEnter(args){
  this.receiver=args[0];
  if(HOSTILE){try{if(HOSTILE==='root')__arkdpsOwnedHpObservation(contract,args[0].add(8));else __arkdpsOwnedHpObservation(contract,args[0]).closing(args[0]);}catch(ignored){}return;}
  const io=__arkdpsOwnedHpObservation(contract,args[0]);if(io===null)throw Error('native HP observation unavailable');
  const identity=io.identity(args[0]),clock=io.clock(),hp=io.hp(args[0]),id=io.id(args[0]);
  if(identity[0]!==BigInt(LAYOUT.addresses.enemy_class)||identity[1]!==12||io.born(args[0])!==1||hp!==100n*4294967296n||
    clock[0]!==30||clock[1]!==4294967296n||clock[2]!==30||id!=='enemy_owned')throw Error('owned HP entry facts differ');
  this.mode=__arkdpsReplayRecord().facts.mode_epoch;entries++;
  records.push({edge:'entry',mode:this.mode,checkpoint:__arkdpsReplayRecord().checkpoint,uid:identity[1],hp:hp.toString(),id});
 },
 onLeave(value){
  if(HOSTILE)return;
  const io=__arkdpsOwnedHpObservation(contract,this.receiver);if(io===null)throw Error('native HP observation unavailable');
  const closing=io.closing(this.receiver),clock=io.clock(),epoch=__arkdpsReplayRecord().facts.mode_epoch;
  if(closing[0]!==12||closing[1]!==100n*4294967296n||clock[0]!==30||clock[1]!==4294967296n||clock[2]!==30||epoch!==this.mode)
   throw Error('owned HP leave facts differ');
  leaves++;records.push({edge:'leave',mode:epoch,checkpoint:__arkdpsReplayRecord().checkpoint,uid:closing[0],hp:closing[1].toString()});
 }
});
globalThis.__arkdpsPublicationServed=n=>{served=Math.max(served,n);};
globalThis.__arkdpsOnClosedRecord=record=>{
 const proof=__arkdpsOwnedGraphCheck('publication',publication);
 if(__arkdpsVerifiedGraphProof(proof,'publication',publication)!==true)throw Error('owned publication proof missing');
 return {mark:{sequence:0,epoch:entries,frame:leaves,game_time:leaves/30,active:0,active_hooks:[],error:null,stopped:false,
  native_capture_scope_mode_epoch:record.facts.scope_mode_epoch,mode_epoch:mode.mode_epoch,entries,leaves,records:records.slice()},
 acknowledged:0,events_after:0,events_through:0,events:[],hp_context:[],read_fields:{sequence:0,epoch:entries},terminal_read:null};
};
rpc.exports={stop(){hook.detach();return {stopped:true};},ackEvents:()=>({acknowledged:0,error:'no owned fixture ACK permitted'})};
send({ready:true,fixture:'self-owned-production-HP-plan-binding',contract_mode_epoch:contract.mode_epoch});
'''.replace('OFFSET', str(export_offsets(binary)['ArkDpsTestInteger'])).replace('PLAN', json.dumps(plan)).replace(
        'LAYOUT', json.dumps(layout)).replace('HOSTILE', json.dumps(hostile))


def request_and_call(pipe, fixture):
    """No injected sleep: wait for actual transport submission and bounded replies."""
    answer, submitted = Queue(), Event()
    original_io = pipe._io
    def observed_io(operation, *args):
        count = original_io(operation, *args)
        if operation == 'WriteFile':
            submitted.set()
        return count
    pipe._io = observed_io
    def request():
        try: answer.put(pipe.request('mark'))
        except BaseException as error: answer.put(error)
    pending = Thread(target=request)
    pending.start()
    if not submitted.wait(2):
        raise AssertionError('owned mark was not submitted')
    calls = []
    try:
        # At most four real function invocations. Each extra is recorded rather
        # than silently claiming a publication from an earlier edge.
        for _ in range(4):
            call = fixture.command('call-owned-hp')
            assert call['results'] == [call['expected']], call
            calls.append(call)
            pending.join(timeout=.03)
            if not pending.is_alive():
                break
        pending.join(timeout=2)
        assert not pending.is_alive(), 'owned mark did not finish'
        return answer.get_nowait(), calls
    finally:
        pipe._io = original_io


def run(binary, source_root, hostile=False):
    with TemporaryDirectory(prefix='arkdps-hp-provider-') as directory:
        directory = Path(directory)
        fixture = Fixture(directory/'child', binary)
        pipe = None
        try:
            layout = fixture.command('owned-hp-buffer')
            plan = production_plan(source_root, layout, directory)
            text = source(binary, layout, plan, hostile)
            fixture.configure(source=text)
            fixture.command('load')
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'], timeout=5)
            hello = pipe.request('initialize', token=fixture.token)
            assert 'fixture_setup_error' not in hello['ready'], hello
            result, calls = request_and_call(pipe, fixture)
            if hostile:
                assert isinstance(result, RuntimeError) and 'native capture failed: code=6' in str(result), repr(result)
                assert pipe._poisoned is True, 'failure must close the authenticated request channel'
                ack_refusal=None
                try:
                    pipe.request('ack', sequence=1)
                except RuntimeError as error:
                    assert 'closed or has an incomplete request' in str(error), str(error)
                    ack_refusal=str(error)
                else:
                    raise AssertionError('hostile caught failure unexpectedly accepted ACK')
                return dict(hostile=hostile, identity=fixture.identity, original_abi_preserved=True,
                    error=str(result), ack_not_sent_after_poison=True, ack_refusal=ack_refusal, calls=calls, source_sha256=sha256(text.encode()).hexdigest(),
                    plan_sha256=sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest())
            if isinstance(result, BaseException):
                raise result
            # A very early target call may precede the worker publication claim.
            # Retry at most once, retaining every real call and reply.
            retries = []
            if result.get('retry'):
                retries.append(result)
                result, extra = request_and_call(pipe, fixture)
                calls.extend(extra)
                if isinstance(result, BaseException): raise result
            assert not result.get('retry') and result.get('error') is None, result
            assert result['entries'] == result['leaves'] == len(calls), result
            assert {row['mode'] for row in result['records']} == {0}, result
            initial_call_count=len(calls)
            # Deliberately install mode1 before producing new callbacks. A fresh
            # publication cannot be fabricated, so this no-callback request retries.
            switch = pipe.request('mark', autochess_enabled=False)
            assert switch.get('retry') and switch['native_async_capture']['desired_mode_epoch'] == 1, switch
            later, extra = request_and_call(pipe, fixture)
            calls.extend(extra)
            if isinstance(later, BaseException): raise later
            assert not later.get('retry') and later.get('error') is None, later
            assert later['entries'] == later['leaves'] == len(calls), later
            modes = {row['mode'] for row in later['records']}
            assert modes == {0, 1}, later
            assert all(row['checkpoint'] == 0 for row in later['records']), later
            assert later['native_async_capture']['failure_code'] == 0, later
            for reply, count, epoch in ((result, initial_call_count, 0), (later, len(calls), 1)):
                counters=reply['native_async_capture']
                assert counters['reserved_ticket']==counters['processed_ticket']==counters['closed_ticket']==2*count, reply
                assert counters['published_ticket']==counters['closed_ticket'], reply
                assert counters['desired_mode_epoch']==counters['published_mode_epoch']==epoch, reply
                assert counters['delivered_event_sequence']==0, reply
                fence=reply['native_publication']
                assert int(fence['closed_ticket'])==counters['published_ticket'] and fence['sequence']==0, reply
            return dict(hostile=False, identity=fixture.identity, original_abi_preserved=True,
                initial=result, switch=switch, mode1=later, retries=retries, calls=calls,
                source_sha256=sha256(text.encode()).hexdigest(),
                plan_sha256=sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest())
        finally:
            if pipe: pipe.close()
            fixture.close()


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    options = parser.parse_args()
    binary = options.binary.resolve()
    receipt = dict(scope=__doc__, binary_sha256=sha256(binary.read_bytes()).hexdigest(), cases=[])
    receipt['test_sha256']=sha256(Path(__file__).read_bytes()).hexdigest()
    receipt['source_root_head']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=options.source_root.resolve(),text=True).strip()
    build_metadata=binary.with_suffix('.build.json')
    if build_metadata.is_file():
        receipt['build_metadata_sha256']=sha256(build_metadata.read_bytes()).hexdigest()
        receipt['build_metadata']=json.loads(build_metadata.read_text(encoding='utf-8'))

    try:
        for hostile in (False, 'root', 'phase'):
            receipt['cases'].append(run(binary, options.source_root.resolve(), hostile))
        receipt['result'] = 'PASS'
    except BaseException as error:
        receipt.update(result='FAIL', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        options.receipt.parent.mkdir(parents=True, exist_ok=True)
        options.receipt.write_text(json.dumps(receipt,indent=2), encoding='utf-8')
    print('PASS real engine HP raw-observation binding mode0/1, caught wrongroot refusal and original ABI')
