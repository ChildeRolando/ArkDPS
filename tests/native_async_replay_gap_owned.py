"""Real DLL failure-only diagnostics in a child we own; never attaches externally.

Run from the repository root with --binary <new DLL> --receipt <runtime JSON>.
This is an engine/provider/transport test, not a gameplay or FPS acceptance.
"""
from hashlib import sha256
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe, NativeHookEventProbe
from event_observer import ObserverStatus
from event_probe import ObserverReadRetry


def run(binary):
    offset = export_offsets(binary)['ArkDpsTestInteger']
    source = r'''
const address=Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET);
const contract={schema_version:1,complete:true,name:'DiagnosticOwned',address:address.toString(),
 abi:'win64-gp',profile:'owned',arg_count:12,arg_mask:4095,globals:[],
 fields:[{field_id:1,phase:'entry',role:'gap.test',root:'absolute',absolute_address:address.toString(),
 root_index:0,pointer_offsets:[],offset:0,bytes:1,kind:'bytes',guard_id:0,guard_side:'none',required:true}],
 max_bytes:64,max_reads:64,required_projections:[]};
const hook=Interceptor.attach(address,{arkdpsCaptureContract:contract,
 onEnter(){__arkdpsReplayRole('gap.test',0,1);
  try{__arkdpsReplayGap('unverified cold metadata 0x123:8');}catch(expected){}
 },onLeave(){}});
rpc.exports={stop(){hook.detach();return {stopped:true};}};
send({ready:true,fixture:'self-owned-replay-gap'});
'''.replace('OFFSET', str(offset))
    with tempfile.TemporaryDirectory(prefix='arkdps-owned-gap-') as temporary:
        fixture = Fixture(Path(temporary), binary)
        pipe = None
        try:
            fixture.configure(source=source)
            fixture.command('load')
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            ready = pipe.request('initialize', token=fixture.token)
            operations = []
            class CountedPipe:
                def request(self, operation, **arguments):
                    operations.append(operation)
                    return pipe.request(operation, **arguments)
            probe = NativeHookEventProbe(SimpleNamespace(process=SimpleNamespace(
                process_alive=lambda: fixture.process.poll() is None)))
            probe._pipe = CountedPipe()
            probe.status = ObserverStatus.READY
            audits = []
            probe.set_request_timing_audit(lambda kind, data: audits.append((kind, data)), enabled=lambda: False)
            # A single actual native function call; raw E/L still enter the real
            # engine, and the callback catches the real bridge gap itself.
            result = fixture.command('burst', count=1, rate=0)
            assert result['count'] == 1 and result['checksum'] == 650, result
            time.sleep(.05)
            error = None
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    mark = probe.mark()
                except ObserverReadRetry:
                    time.sleep(.01)
                    continue
                except RuntimeError as failure:
                    error = str(failure)
                    break
                assert mark.get('retry'), 'a structural gap published a mark'
                time.sleep(.01)
            assert error is not None, 'real DLL failed to report caught structural gap'
            failures = [data for kind, data in audits if kind == 'native_hook_request_failed']
            assert len(failures) == 1, audits
            diagnostic = failures[0]
            assert diagnostic['code'] == 'native_replay_gap', (error, diagnostic)
            assert diagnostic['replay_hook_id'] == 0 and diagnostic['replay_edge'] == 0, diagnostic
            # The sole declared role is interned as native role ID 1.
            assert diagnostic['replay_role_id'] == 1 and diagnostic['replay_checkpoint'] == 0, diagnostic
            assert diagnostic['replay_field_id'] == 1, diagnostic
            assert diagnostic['replay_reason_codes'] == '1', diagnostic
            assert diagnostic['metadata_read_size'] == 8 and diagnostic['replay_gap_count'] == 1, diagnostic
            assert diagnostic['replay_ticket'] > 0, diagnostic
            assert '0x123' not in error and 'unverified cold metadata' not in error, error
            before = len(operations)
            for _ in range(3):
                try:
                    probe.mark()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('failed observer became ready again')
            assert len(operations) == before and len(audits) == 1, (operations, audits)
            assert 'ack' not in operations and 'read' not in operations, operations
            return dict(result='PASS', scope='self-owned real DLL only', identity=fixture.identity,
                        ready=ready, dll_sha256=sha256(binary.read_bytes()).hexdigest(),
                        source_sha256=sha256(source.encode()).hexdigest(),
                        native_call=result, error=error, audit=diagnostic,
                        operations=operations, failed_observer_no_retries=True, no_ack=True)
        finally:
            if pipe is not None:
                pipe.close()
            fixture.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    options = parser.parse_args()
    result = run(options.binary.resolve())
    options.receipt.parent.mkdir(parents=True, exist_ok=True)
    options.receipt.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(result['result'])
