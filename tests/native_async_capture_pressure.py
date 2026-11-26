"""Explicit owned-process pressure run; this is not an in-game FPS test.

The fixture measures real native wrapping, owned byte capture and one JS
consumer. It observes one argument, while all twelve original arguments must
still reach the native function. Its 128-byte payload is labelled; the HP
contract and branch semantics require their separate fixtures/live acceptance.
"""
from argparse import ArgumentParser
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event, Thread
from hashlib import sha256
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe


def source(binary, pause_ms, control=False):
    offset = export_offsets(binary)['ArkDpsTestInteger']
    return r'''
const base=Process.getModuleByName('ArkDPS.NativeEvents.dll').base,address=base.add(OFFSET);
const owned=Memory.alloc(128);for(let i=0;i<16;i++)owned.add(i*8).writeU64(i+1);
const fields=['entry','leave'].map((phase,index)=>({field_id:index+1,phase,role:'body',root:'absolute',root_index:0,
 absolute_address:owned.toString(),pointer_offsets:[],offset:0,bytes:128,kind:'bytes',guard_id:0,guard_side:'none',required:true}));
const plan={schema_version:1,complete:true,name:'owned-pressure-128-bytes',address:address.toString(),
 abi:'win64-gp',profile:'owned',arg_count:1,arg_mask:1,globals:[],fields,max_bytes:65536,max_reads:4096,required_projections:[]};
let entries=0,leaves=0,lastRequest=null,served=0;
const hook=Interceptor.attach(address,{arkdpsName:'owned.pressure',arkdpsCaptureContract:plan,
 onEnter(args){
  if(typeof __arkdpsReplayRole==='function')__arkdpsReplayRole('body',0,1);if(owned.readU64().toNumber()!==1||args[0].toInt32()!==1)throw Error('owned entry corrupted');
  entries++;if(entries===1){const end=Date.now()+PAUSE;while(Date.now()<end){}}
 },onLeave(){if(typeof __arkdpsReplayRole==='function')__arkdpsReplayRole('body',0,2);if(owned.readByteArray(128).byteLength!==128)throw Error('owned leave corrupted');leaves++;}});
globalThis.__arkdpsPublicationServed=through=>{served=Math.max(served,through);};
globalThis.__arkdpsOnClosedRecord=record=>{
 if(record.publication_request===lastRequest)return null;lastRequest=record.publication_request;
 return {mark:{sequence:0,epoch:entries,frame:leaves,game_time:leaves/30,active:0,active_hooks:[],
  error:null,stopped:false,entries,leaves,owned_payload_bytes_per_edge:128,
  native_capture_scope_mode_epoch:record.facts.scope_mode_epoch??record.facts.mode_epoch},acknowledged:0,events_after:0,events_through:0,events:[],hp_context:[],
  read_fields:{sequence:0,epoch:entries},terminal_read:null};
};
rpc.exports={mark(){if(CONTROL)return {entries,leaves,active:0};throw Error('legacy mark');},readEvents(){throw Error('legacy read');},
 ackEvents:sequence=>({acknowledged:0,error:sequence<=served?null:'unserved'}),stop(){hook.detach();return {stopped:true};}};
send({ready:true,fixture:'owned-native-pressure-128-bytes'});
'''.replace('OFFSET', str(offset)).replace('PAUSE', str(pause_ms)).replace('CONTROL', str(control).lower())


def fresh_mark(pipe, fixture):
    deadline, nudges = time.monotonic()+6, 0
    while time.monotonic() < deadline:
        answer = Queue()

        def request():
            try:
                answer.put(pipe.request('mark'))
            except BaseException as error:
                answer.put(error)

        thread = Thread(target=request)
        thread.start()
        time.sleep(.01)
        fixture.command('burst', count=1)
        nudges += 1
        thread.join(timeout=4)
        assert not thread.is_alive(), 'owned fresh mark timeout'
        result = answer.get_nowait()
        if isinstance(result, BaseException):
            raise result
        if not result.get('retry'):
            return result, nudges
    raise RuntimeError('owned consumer failed to catch up in six seconds')


def run(binary, count, pause_ms, control=False, rate=30000, progress=None, monitor=False):
    progress = {} if progress is None else progress
    with TemporaryDirectory(prefix='arkdps-owned-pressure-') as directory:
        fixture = Fixture(Path(directory), binary)
        pipe = None
        monitoring = None
        monitor_stop = Event()
        try:
            fixture.configure(source=source(binary, pause_ms, control))
            before = fixture.command('load')
            progress.update(identity=fixture.identity, phase='unhooked')
            baseline = fixture.command('burst', count=min(count, 2000))
            progress.update(unhooked=baseline, phase='initializing')
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            ready = pipe.request('initialize', token=fixture.token)
            if monitor:
                assert not control, 'live lag monitoring requires the asynchronous candidate'
                pipe.request('mark', native_timing={'version':1, 'enabled':True})
            progress.update(ready=ready, phase='producing')
            started = time.perf_counter()
            live_marks = []
            progress['live_marks'] = live_marks
            if monitor:
                def observe():
                    while not monitor_stop.wait(.25):
                        try:
                            reply = pipe.request('mark')
                            live_marks.append(dict(seconds=time.perf_counter()-started,
                                retry=reply.get('retry',False), state=reply['native_async_capture']))
                        except BaseException as error:
                            live_marks.append(dict(seconds=time.perf_counter()-started,
                                error_type=type(error).__name__, error=str(error)))
                            break
                monitoring = Thread(target=observe)
                monitoring.start()
            burst = fixture.command('burst', count=count, rate=rate)
            wall_seconds = time.perf_counter()-started
            monitor_stop.set()
            if monitoring is not None:
                monitoring.join(timeout=4)
                assert not monitoring.is_alive(), 'live owned mark did not return'
            assert burst['checksum'] == count*650
            progress.update(captured=burst, producer_wall_seconds=wall_seconds, phase='catching_up')
            # Give the deliberately paused consumer its entire budget. This
            # wait is in the owned test driver, never a native game callback.
            time.sleep(pause_ms/1000+.25)
            mark, nudges = (pipe.request('mark'), 0) if control else fresh_mark(pipe, fixture)
            assert mark['entries'] == mark['leaves'] == count+nudges, mark
            if not control:
                assert mark['native_async_capture']['failure_code'] == 0, mark
            stopped = pipe.request('stop')
            assert stopped['native_quiet'] is True
            assert fixture.command('snapshot')['code'] == before['code']
            return dict(identity=fixture.identity, ready=ready, count=count, consumer_pause_ms=pause_ms,
                        payload_bytes_per_edge=128, unhooked=baseline, captured=burst,
                        producer_wall_seconds=wall_seconds, live_marks=live_marks, mark=mark, stopped=stopped)
        finally:
            monitor_stop.set()
            if monitoring is not None:
                monitoring.join(timeout=4)
            if pipe is not None:
                pipe.close()
            fixture.close()


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--count', type=int, default=16000)
    parser.add_argument('--pauses', type=int, nargs='+', default=[0, 300, 500])
    parser.add_argument('--synchronous-control', action='store_true', help='explicitly label a frozen old DLL control')
    parser.add_argument('--rate', type=int, default=30000, help='setter pairs per second; 0 is an unpaced burst')
    parser.add_argument('--monitor', action='store_true', help='record live publication lag during the owned burst')
    options = parser.parse_args()
    if (not 1 <= options.count <= 300000 or any(not 0 <= pause <= 500 for pause in options.pauses)
            or not 0 <= options.rate <= 200000):
        parser.error('owned count/pause budget exceeded')
    receipt = dict(game_processes_accessed=False, in_game_fps_measured=False,
                   fixture='128-byte native boundary, one observed argument, twelve original arguments, not the complete HP contract',
                   synchronous_control=options.synchronous_control,
                   pacing='deadline_sleep_100us_tail_outside_call_timing',
                   target_setters_per_second=options.rate,
                   binary_sha256=sha256(options.binary.read_bytes()).hexdigest(), cases=[])
    failed = False
    for pause in options.pauses:
        progress = {}
        try:
            case = run(options.binary.resolve(), options.count, pause, options.synchronous_control, options.rate, progress, options.monitor)
            case['status'] = 'passed'
            report = dict(pause_ms=pause, count=case['count'], captured=case['captured'],
                          wall_seconds=case['producer_wall_seconds'])
        except Exception as error:
            failed = True
            case = dict(progress, status='failed', consumer_pause_ms=pause, error_type=type(error).__name__, error=str(error))
            report = dict(pause_ms=pause, status='failed', error_type=type(error).__name__)
        receipt['cases'].append(case)
        options.receipt.parent.mkdir(parents=True, exist_ok=True)
        options.receipt.write_text(json.dumps(receipt, indent=2), 'utf-8')
        print(json.dumps(report), flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
