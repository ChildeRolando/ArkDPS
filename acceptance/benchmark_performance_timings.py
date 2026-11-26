"""Bounded profiler overhead comparison in newly created owned processes only.

No game attachment. The 64-read case is a synthetic callback, not the HP hook.
"""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
import time
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe


def run(binary, directory, *, count, repeats, reads, profiled):
    fixture = Fixture(directory, binary)
    pipe = None
    offset = export_offsets(binary)['ArkDpsTestInteger']
    source = '''
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET);
const buffer=Memory.alloc(64); buffer.writeU64(7);
let entries=0,leaves=0,checksum=0;
const hook=Interceptor.attach(address,{arkdpsName:'owned.synthetic',
 onEnter(args){entries++;for(let i=0;i<READS;i++)checksum+=buffer.add((i&7)*4).readU32();},
 onLeave(){leaves++;}});
rpc.exports={mark(){return {entries,leaves,checksum,active:0};},
 readEvents(){return {events:[]};},ackEvents(sequence){return {acknowledged:sequence};},
 stop(){hook.detach();return {stopped:true};}};
send({ready:true,fixture:'owned-timing-overhead'});
'''.replace('OFFSET', str(offset)).replace('READS', str(reads))
    result = dict(binary_sha256=sha256(binary.read_bytes()).hexdigest(), reads_per_enter=reads,
                  process=fixture.identity, count=count, phases=[])
    try:
        fixture.configure(source=source)
        original = fixture.command('load')['code']
        pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
        pipe.request('initialize', token=fixture.token)
        fixture.command('benchmark', count=256)  # bounded warm-up
        # Alternate the ordering within the same process to reduce drift.
        modes = ([False, True, True, False] * repeats) if profiled else [False] * (repeats*4)
        for enabled in modes:
            mark = pipe.request('mark', native_timing=dict(version=1,enabled=enabled,snapshot=False))
            if profiled:
                assert mark['native_timing_control']['enabled'] == enabled
            phase = fixture.command('benchmark', count=count)
            assert phase['total'] == 650*count
            result['phases'].append(dict(enabled=enabled, **phase))
        start = time.perf_counter_ns()
        final = pipe.request('mark', native_timing=dict(version=1,enabled=False,snapshot=profiled))
        result['summary_roundtrip_ns'] = time.perf_counter_ns()-start
        assert final['entries'] == final['leaves'] == 256 + len(modes)*count
        result['final'] = final
        if profiled:
            native = final['native_timing']
            assert native['enabled'] is False and not native['thread_overflow']
            rows = [row for row in native['rows'] if row['kind'].startswith('hook_')]
            assert len(rows) == 2
            assert all(row['calls'] == modes.count(True)*count for row in rows)
            assert all(0 < row['samples'] < row['calls']//4 for row in rows)
            assert all(0 <= row['exclusive_ticks'] <= row['ticks'] for row in rows)
            assert all(row['vm_hold_ticks'] > 0 and row['js_ticks'] > 0 for row in rows)
            if reads:
                assert next(row for row in rows if row['kind']=='hook_enter')['read_calls'] > 0
        result['stop'] = pipe.request('stop')
        assert fixture.command('snapshot')['code'] == original
        result['original_restored'] = True
        for mode in (False, True):
            values = [item['elapsed_ns']/count for item in result['phases'] if item['enabled']==mode]
            if values:
                result['on' if mode else 'off'] = dict(median_ns_per_call=statistics.median(values),
                                                       min_ns=min(values), max_ns=max(values))
    finally:
        if pipe:
            pipe.close()
        fixture.close()
    return result


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=4096)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if not 128 <= args.count <= 50000 or not 1 <= args.repeats <= 8:
        parser.error('benchmark budget outside bounds')
    result = dict(schema=1, classification='self_owned_synthetic_not_game_fps', cases=[])
    with TemporaryDirectory(prefix='arkdps-timing-') as directory:
        for reads in (0,64):
            for label,binary in [('current',args.binary)]+([('baseline',args.baseline)] if args.baseline else []):
                case = run(binary.resolve(), Path(directory)/f'{label}-{reads}', count=args.count,
                           repeats=args.repeats,reads=reads,profiled=label=='current')
                case['build'] = label
                result['cases'].append(case)
        result['contention_and_coverage'] = check_contention(args.binary.resolve(),Path(directory)/'contention')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps([{k:v for k,v in case.items() if k in ('build','reads_per_enter','off','on','summary_roundtrip_ns')}
                      for case in result['cases']]))


def check_contention(binary, directory):
    """Hold JS for 20ms in owned callbacks; host mark must expose lock wait."""
    fixture, pipe = Fixture(directory,binary), None
    offset = export_offsets(binary)['ArkDpsTestInteger']
    source = '''
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET);
const hook=Interceptor.attach(address,{arkdpsName:'owned.deliberate_js_hold',
 onEnter(){const until=Date.now()+20;while(Date.now()<until){}},onLeave(){}});
rpc.exports={mark(){return {active:0};},readEvents(){return {events:[]};},
 ackEvents(sequence){return {acknowledged:sequence};},stop(){hook.detach();return {};}};
send({ready:true,fixture:'owned-deliberate-lock-contention'});
'''.replace('OFFSET',str(offset))
    try:
        fixture.configure(source=source)
        original=fixture.command('load')['code']
        pipe=NativePipe(fixture.pipe_name,fixture.identity['pid'])
        pipe.request('initialize',token=fixture.token)
        pipe.request('mark',native_timing=dict(version=1,enabled=True,snapshot=False))
        pending=[]
        thread=Thread(target=lambda: pending.append(fixture.command('threads')))
        thread.start()
        time.sleep(.05)
        pipe.request('mark',native_timing=dict(version=1,enabled=True,snapshot=False))
        thread.join(timeout=5)
        assert not thread.is_alive() and len(pending[0]['results'])==4
        assert all(values == [650]*5 for values in pending[0]['results'])
        # New fixture threads cannot reclaim process-lifetime thread slots.
        for _ in range(4):
            assert fixture.command('threads')['results']==[[650]*5]*4
        snapshot=pipe.request('mark',native_timing=dict(version=1,enabled=False,snapshot=True))['native_timing']
        row=next(row for row in snapshot['rows'] if row['kind']=='request_mark')
        assert row['vm_wait_max_ticks'] > snapshot['qpc_frequency']*.001
        assert snapshot['thread_overflow'] is True
        pipe.request('stop')
        assert fixture.command('snapshot')['code']==original
        return dict(wait_max_ms=row['vm_wait_max_ticks']*1000/snapshot['qpc_frequency'],
                    thread_overflow_reported=True, original_restored=True, native=snapshot)
    finally:
        if pipe: pipe.close()
        fixture.close()


if __name__ == '__main__':
    main()
