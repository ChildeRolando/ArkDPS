"""Explicit real-DLL/QuickJS owned-view acceptance; never targets a game.

Run after engine integration/build, serially. This is a small semantic test,
not a throughput benchmark. It must not substitute for capture guard-race tests.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe


def source(binary, case):
    offset = export_offsets(binary)['ArkDpsTestInteger']
    return r'''
const mode=CASE,owned=Memory.alloc(64);
for(const [offset,bits]of [[0,251n],[8,65000n],[16,0xfedcba98n],[24,0xfffe1dc0n],
 [32,0xfedcba9876543210n],[40,0x8000000000000001n],[48,0xc1540000n]])owned.add(offset).writeU64(bits);
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET),fields=[];
for(const [phase,base]of [['entry',0],['leave',3]]){
 fields.push({field_id:base+1,phase,role:mode==='guard'?'guard':'before',root:'absolute',root_index:0,absolute_address:owned.toString(),pointer_offsets:[],offset:0,bytes:64,kind:'bytes',guard_id:1,guard_side:'before',required:true});
 fields.push({field_id:base+2,phase,role:'body',root:'absolute',root_index:0,absolute_address:owned.toString(),pointer_offsets:[],offset:0,bytes:64,kind:'bytes',guard_id:0,guard_side:'none',required:true});
 fields.push({field_id:base+3,phase,role:mode==='guard'?'guard':'after',root:'absolute',root_index:0,absolute_address:owned.toString(),pointer_offsets:[],offset:0,bytes:64,kind:'bytes',guard_id:1,guard_side:'after',required:true});
}
const plan={schema_version:1,complete:true,raw_collection:'all',name:'owned-native-view',address:address.toString(),abi:'win64-gp',profile:'owned',arg_count:1,arg_mask:1,globals:[],fields,max_bytes:65536,max_reads:4096,required_projections:[]};
let entries=0,leaves=0,savedTicket=null,checks=0,served=0;const assertionErrors=[];let scalarDebug=null;
function equal(actual,expected){if(actual!==expected)assertionErrors.push('owned scalar mismatch '+String(actual)+' vs '+String(expected));checks++;}
function checkScalars(){
 __arkdpsReplayPoint('body',()=>{
  equal(owned.readU8(),251);equal(owned.add(8).readU16(),65000);equal(owned.add(16).readU32(),0xfedcba98);
  equal(owned.add(24).readS32(),-123456);equal(owned.add(32).readU64().toString(),'18364758544493064720');
  equal(owned.add(40).readS64().toString(),'-9223372036854775807');equal(owned.add(48).readFloat(),-13.25);
  const raw=owned.add(32).readU64().value,pointer=owned.add(32).readPointer();scalarDebug={raw_decimal:raw.toString(),raw_hex:raw.toString(16),pointer_decimal:pointer.value.toString(),pointer_hex:pointer.value.toString(16),as_uint_decimal:BigInt.asUintN(64,raw).toString(),as_uint_hex:BigInt.asUintN(64,raw).toString(16)};equal(pointer.toString(),'0xfedcba9876543210');
  const copy=owned.readByteArray(64);equal(copy.byteLength,64);new Uint8Array(copy)[0]=0;equal(owned.readU8(),251);
 });
 const info=__nativeOwnedPoint('body',0);equal(info>>>24,1);equal((info>>>16)&255,0);
 equal(__arkdpsReplayWitness('before',()=>owned.readU8()),251);equal(__arkdpsReplayWitness('after',()=>owned.readU8()),251);
}
const hook=Interceptor.attach(address,{arkdpsName:'owned.native-view',arkdpsCaptureContract:plan,
 onEnter(args){
  const raw=__arkdpsReplayRecord();equal(raw.native_view,true);equal(__nativeOwnedLease(raw.ticket),true);
  equal(args[0].toInt32(),1);savedTicket=raw.ticket;entries++;
  if(mode==='positive')checkScalars();
  else if(mode==='invalid'){
   // Call C++ directly, swallow the error and clear LAST status with a good
   // read. Independent native poison must still seal this replay.
   try{__nativeOwnedRead('body',0,-1,owned.value+4096n,4);}catch(ignored){}
   __nativeOwnedScalar('body',0,-1,owned.value,0);
  }else if(mode.startsWith('numeric-')){
   const invalid=mode==='numeric-nan'?NaN:mode==='numeric-unsafe'?9007199254740992:4096.5;
   try{__nativeOwnedRead('body',0,-1,invalid,4);}catch(ignored){}
   __nativeOwnedScalar('body',0,-1,Number(owned.value),0);
  }else if(mode==='guard'){
   try{__nativeOwnedRead('guard',0,-1,owned.value,8);}catch(ignored){}
   __nativeOwnedScalar('body',0,-1,owned.value,0);
  }
 },onLeave(){
  leaves++;if(mode==='positive')checkScalars();
  if(mode==='late-lease'){
   try{__nativeOwnedLease(savedTicket);}catch(ignored){}
   __nativeOwnedScalar('body',0,-1,owned.value,0);
  }
 }});
globalThis.__arkdpsPublicationServed=through=>served=Math.max(served,through);
globalThis.__arkdpsOnClosedRecord=record=>({mark:{sequence:0,epoch:entries,frame:leaves,game_time:leaves/30,
 active:0,active_hooks:[],error:null,stopped:false,entries,leaves,checks,source_assertion_errors:assertionErrors.slice(),scalar_debug:scalarDebug,native_numeric_address:globalThis.__nativeOwnedNumericAddress===true,owned_native_view:record.native_view,
 native_capture_scope_mode_epoch:record.facts.scope_mode_epoch??record.facts.mode_epoch},acknowledged:0,
 events_after:0,events_through:0,events:[],hp_context:[],read_fields:{sequence:0,epoch:entries},terminal_read:null});
rpc.exports={mark(){throw Error('legacy live mark');},readEvents(){throw Error('legacy live read');},
 ackEvents:sequence=>({acknowledged:0,error:sequence<=served?null:'unserved'}),stop(){hook.detach();return {stopped:true};}};
send({ready:true,fixture:'owned-native-view-semantic',capture_capabilities:__nativeCaptureCapabilities()});
'''.replace('CASE', json.dumps(case)).replace('OFFSET', str(offset))


def fresh_mark(pipe, fixture):
    deadline = time.monotonic()+3
    fresh_mark.last_nudges = 0
    while time.monotonic() < deadline:
        results = Queue()
        def request():
            try:
                results.put(pipe.request('mark'))
            except BaseException as error:
                results.put(error)
        pending = Thread(target=request)
        pending.start()
        time.sleep(.01)
        # Existing driver operation, bounded to ONE original Integer call.
        # Ignore elapsed_ns: this is a semantic nudge, not a throughput test.
        result = fixture.command('benchmark', count=1)
        fresh_mark.last_nudges += 1
        assert result['count'] == 1 and result['total'] == 650, result
        pending.join(timeout=3)
        assert not pending.is_alive(), 'fresh mark did not finish'
        answer = results.get_nowait()
        if isinstance(answer, BaseException):
            raise answer
        if not answer.get('retry'):
            return answer
    raise AssertionError('later owned edges did not establish a fresh publication')


def run(binary, case):
    with TemporaryDirectory(prefix='arkdps-owned-view-'+case+'-') as temporary:
        fixture = Fixture(Path(temporary)/case, binary)
        pipe = None
        try:
            fixture.configure(source=source(binary, case))
            fixture.command('load')
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            ready = pipe.request('initialize', token=fixture.token)
            capabilities = ready['ready']['capture_capabilities']
            assert capabilities == dict(version=1, raw_collection='all', lowered_projections=[
                'live_scope_guards', 'same_point_class_ancestry', 'open_parent_checkpoint',
                'token_host_and_character_form', 'full_native_binding', 'current_buff_and_blackboard_keys']), capabilities
            if case == 'positive':
                mark = fresh_mark(pipe, fixture)
                assert mark.get('retry') is not True and mark.get('error') is None, mark
                assert mark.get('source_assertion_errors') == [], mark
                assert mark['owned_native_view'] is True and mark['native_numeric_address'] is True, mark
                assert mark['entries'] == mark['leaves'] == fresh_mark.last_nudges >= 1, mark
                assert mark['checks'] == 31*fresh_mark.last_nudges, mark
                assert int(mark['native_publication']['closed_ticket']) == 2*fresh_mark.last_nudges, mark
                assert mark['native_async_capture']['failure_code'] == 0, mark
                token = mark['native_publication']['token']
                batch = pipe.request('read', publication_token=token, after=0, through=0)
                assert batch.get('error') is None and batch['events'] == [], batch
                return {'result':'PASS','identity':fixture.identity,'ready':ready,'nudges':fresh_mark.last_nudges,'mark':mark}
            result = fixture.command('call')
            assert result['integer'] == 650 and result['nested'] == 687, result
            rejected = False
            reply = None
            try:
                reply = pipe.request('mark')
                rejected = bool(reply.get('error')) or reply.get('native_async_capture',{}).get('failure_code',0) != 0
            except Exception as error:
                rejected = True
                reply = {'exception':str(error)}
            assert rejected, ('native independent poison bypassed by caught operation', case, reply)
            return {'result':'PASS','identity':fixture.identity,'original_abi':result,'rejection':reply}
        finally:
            if pipe:
                pipe.close()
            fixture.close()


def main():
    parser = ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--case', choices=['all','positive','invalid','late-lease','guard','numeric-nan','numeric-unsafe','numeric-fractional'], default='all')
    options = parser.parse_args()
    binary = options.binary.resolve()
    cases = ['positive','invalid','late-lease','guard','numeric-nan','numeric-unsafe','numeric-fractional'] if options.case == 'all' else [options.case]
    receipt = {'kind':'real DLL/QuickJS self-owned semantic test; no game or throughput claim',
               'binary_sha256':sha256(binary.read_bytes()).hexdigest(),'cases':{}}
    try:
        for case in cases:
            receipt['cases'][case] = run(binary, case)
        receipt['result'] = 'PASS'
    except BaseException as error:
        receipt['result'] = 'FAIL'
        receipt['cases'][case] = {'result':'FAIL','error_type':type(error).__name__,'error':str(error)}
        if isinstance(error, AssertionError) and error.args and isinstance(error.args[0], dict):
            receipt['cases'][case]['reply'] = error.args[0]
        raise
    finally:
        options.receipt.parent.mkdir(parents=True, exist_ok=True)
        options.receipt.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print('PASS native owned-view DLL semantics:', ', '.join(cases))


if __name__ == '__main__':
    main()
