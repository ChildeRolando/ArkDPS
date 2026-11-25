"""Exercise the compiled DLL only in new, self-owned Python processes (no game)."""

from argparse import ArgumentParser
from collections import Counter
import ctypes
from ctypes import wintypes
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
from queue import Queue, Empty
import secrets
import shutil
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from native_pc.native_event_runtime.build import DEFAULT_OUTPUT


FULL_ARGUMENT_VALUES = (0, 2**64 - 1, 2**63, 2**32, *range(5, 15), 2**48 + 15, 2**63 + 16)


def export_offsets(binary):
    """Read PE export RVAs without mapping or executing the DLL in this process."""
    data = Path(binary).read_bytes()
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[pe:pe + 6] != b'PE\0\0\x64\x86':
        raise ValueError('smoke requires a Windows x64 DLL')
    count, optional_size = struct.unpack_from('<H', data, pe + 6)[0], struct.unpack_from('<H', data, pe + 20)[0]
    sections = pe + 24 + optional_size

    def offset(rva):
        for index in range(count):
            base = sections + 40 * index
            virtual_size, virtual, raw_size, raw = struct.unpack_from('<IIII', data, base + 8)
            if virtual <= rva < virtual + max(virtual_size, raw_size):
                result = raw + rva - virtual
                if not 0 <= result < len(data):
                    break
                return result
        raise ValueError('export RVA outside the file')

    exports, _ = struct.unpack_from('<II', data, pe + 24 + 112)
    table = offset(exports)
    names_count, addresses, names, ordinals = struct.unpack_from('<IIII', data, table + 24)
    if names_count > 65536:
        raise ValueError('oversized PE export table')
    result = {}
    for index in range(names_count):
        name_rva = struct.unpack_from('<I', data, offset(names) + 4 * index)[0]
        start = offset(name_rva)
        end = data.index(b'\0', start, min(start + 512, len(data)))
        name = data[start:end].decode('ascii')
        ordinal = struct.unpack_from('<H', data, offset(ordinals) + 2 * index)[0]
        result[name] = struct.unpack_from('<I', data, offset(addresses) + 4 * ordinal)[0]
    return result


def fixture_source(binary, *, slow=False):
    exports = export_offsets(binary)
    names = ('Integer', 'Float', 'Sret', 'Nested') + (('Slow',) if slow else ())
    if 'ArkDpsTestFullArguments' in exports:
        names += ('FullArguments',)
    offsets = {name: exports['ArkDpsTest' + name] for name in names}
    return r'''
'use strict';
const base = Process.getModuleByName('ArkDPS.NativeEvents.dll').base;
const records = [], hooks = [];
let savedArguments;
const activity = Memory.alloc(8);
activity.writeU64(0);
let serial = 0;
for (const [name, offset] of Object.entries(OFFSETS)) {
 hooks.push(Interceptor.attach(base.add(offset), {
  onEnter(args) {
   this.serial = ++serial;
   const values = [];
   if (name === 'Integer') for (let i=0;i<12;i++) values.push(args[i].value.toString());
   if (name === 'Sret') for (let i=1;i<3;i++) values.push(args[i].value.toString());
   if (name === 'FullArguments') savedArguments=args;
   records.push({phase:'enter',name,serial:this.serial,values});
   if(name==='Slow')activity.writeU64(1);
  },
  onLeave(value) {
   const record={phase:'leave',name,serial:this.serial};
   if(name==='Integer'||name==='Nested')record.value=value.value.toString();
   if(name==='Sret')record.values=[0,8,16,24].map(offset=>value.add(offset).readU64().toString());
   records.push(record);
   if(name==='Slow')activity.writeU64(0);
  }
 }));
}
function retainedArgumentProof(){
 if(savedArguments===undefined)return null;
 const args=savedArguments;
 const last=args[15];
 const values=Array.from(args,value=>(value.value&0xffffffffffffffffn).toString());
 const keys=Object.keys(args);
 const samePointer=last===args[15]&&args[0]===Reflect.getOwnPropertyDescriptor(args,'0').value;
 const first=args[0];
 args[0]=undefined;
 const shadowed=Object.hasOwn(args,'0')&&args[0]===undefined;
 delete args[0];
 const deleted=!Object.hasOwn(args,'0')&&!(0 in args)&&args[0]===undefined;
 args[0]=first;
 return {array:Array.isArray(args),length:args.length,values,keys,samePointer,shadowed,deleted};
}
rpc.exports={mark(){return {records:records.slice(),active:0,retained_arguments:retainedArgumentProof()};},
 readEvents(){return {events:records.slice()};},ackEvents(sequence){return {acknowledged:sequence};},
 stop(){for(const hook of hooks)hook.detach();return {stopped:true};}};
send(SLOW_FIXTURE?{ready:true,fixture:'self-owned-stop-concurrency',activity:activity.toString()}:
 {ready:true,fixture:'self-owned-abi'});
'''.replace('OFFSETS', json.dumps(offsets)).replace('SLOW_FIXTURE', json.dumps(slow))


def quiet_mark_source(binary, result_buffer, *, autochess):
    return fixture_source(binary, slow=True) + r'''
const markWaiting=Memory.alloc(8), sretBuffer=ptr(RESULT_BUFFER);
markWaiting.writeU64(0);
let testedArrival=false, readThrough=0, acknowledged=0;
const completed=()=>records.filter(event=>event.name==='Sret'&&event.phase==='leave')
    .map((_,index)=>({sequence:index+1,frame:30*(index+1),game_time:index+1}));
rpc.exports.mark=()=>{
 const entry=__arkdpsMarkEntryActive(), sequence=completed().length;
 const frame=sequence*30,game_time=sequence;
 if(!testedArrival&&entry===0){
  testedArrival=true;markWaiting.writeU64(1);
  const deadline=Date.now()+3000;
  while(__nativeActive()===0&&Date.now()<deadline){}
  if(__nativeActive()===0)throw new Error('owned callback did not reach mark');
 }
 return {active:entry,active_hooks:[],retry:entry>0,error:null,stopped:false,
  sequence,epoch:1,frame,game_time,native_battle_state:2,
  result_buffer:[0,8,16,24].map(offset=>sretBuffer.add(offset).readU64().toString()),
  native_autochess_context:AUTOCHESS?{protocol_version:1,status:'available',
   verified_same_frame:true,phase:4,round:12,scope_key:'fixture:autochess:1',
   collection_mode:'all',frame,game_time}:null};
};
rpc.exports.readEvents=(after,through)=>{
 const events=completed();
 if(after<acknowledged||through>events.length||after>through)return {error:'range unavailable'};
 readThrough=Math.max(readThrough,through);
 return {events:events.slice(after,through)};
};
rpc.exports.ackEvents=through=>{
 if(through>readThrough)return {error:'ack beyond read',acknowledged};
 acknowledged=Math.max(acknowledged,through);
 return {acknowledged};
};
rpc.exports.readTerminalEvents=()=>({events:[1],hp_context:[2]});
send({ready:true,fixture:'self-owned-quiet-mark-concurrency', markWaiting:markWaiting.toString(),
 activity:activity.toString()});
'''.replace('RESULT_BUFFER', json.dumps(result_buffer)).replace('AUTOCHESS', json.dumps(autochess))


def child(binary):
    """Child owns its own test functions, memory and LoadLibrary invocation."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, exited, cpu_kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                  ctypes.byref(cpu_kernel), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    print(json.dumps(dict(pid=__import__('os').getpid(), created=(created.dwHighDateTime << 32) | created.dwLowDateTime)), flush=True)
    libraries = []
    background = []
    background_results = Queue()
    result_type = ctypes.c_uint64 * 4
    result_buffer = result_type()

    def owned_sret():
        # The Windows x64 hidden return buffer is the first native argument.
        function = libraries[0].ArkDpsTestSret
        function.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64]
        function.restype = ctypes.c_void_p
        function(ctypes.addressof(result_buffer), 17, 29)
        return list(result_buffer)

    def code_digest():
        return {name: sha256(ctypes.string_at(ctypes.cast(getattr(libraries[0], 'ArkDpsTest' + name), ctypes.c_void_p).value, 32)).hexdigest()
                for name in ('Integer', 'Float', 'Sret', 'Nested')}

    for line in sys.stdin:
        command = json.loads(line)
        operation = command['operation']
        if operation == 'exit':
            return
        if operation == 'load':
            libraries.append(ctypes.WinDLL(command.get('binary', str(binary))))
            print(json.dumps(dict(loaded=True, code=code_digest())), flush=True)
        elif operation == 'snapshot':
            print(json.dumps(dict(code=code_digest())), flush=True)
        elif operation == 'memory':
            address = int(command['address'], 16)
            print(json.dumps(dict(value=ctypes.c_uint64.from_address(address).value)), flush=True)
        elif operation == 'owned-sret-buffer':
            result_buffer[:] = (0, 0, 0, 0)
            print(json.dumps(dict(address=hex(ctypes.addressof(result_buffer)))), flush=True)
        elif operation == 'call-owned-sret':
            print(json.dumps(dict(result=owned_sret())), flush=True)
        elif operation == 'start-owned-sret':
            background = [Thread(target=lambda: background_results.put(owned_sret()))]
            background[0].start()
            print(json.dumps(dict(started=1)), flush=True)
        elif operation == 'start-threads':
            function_name = command.get('function', 'Integer')
            function = getattr(libraries[0], 'ArkDpsTest' + function_name)
            values = [400] if function_name == 'Slow' else list(range(1, 13))
            function.argtypes, function.restype = [ctypes.c_uint64] * len(values), ctypes.c_uint64

            def invoke_background():
                background_results.put(function(*values))

            background = [Thread(target=invoke_background) for _ in range(4)]
            for thread in background:
                thread.start()
            print(json.dumps(dict(started=len(background))), flush=True)
        elif operation == 'join-threads':
            for thread in background:
                thread.join(timeout=10)
            if any(thread.is_alive() for thread in background):
                raise RuntimeError('self-owned background callback failed to drain')
            print(json.dumps(dict(results=[background_results.get_nowait() for _ in background])), flush=True)
        elif operation == 'call':
            library = libraries[0]
            integer = library.ArkDpsTestInteger
            integer.argtypes, integer.restype = [ctypes.c_uint64] * 12, ctypes.c_uint64
            floating = library.ArkDpsTestFloat
            floating.argtypes, floating.restype = [ctypes.c_double] * 5, ctypes.c_double

            class Large(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in 'abcd']

            sret = library.ArkDpsTestSret
            sret.argtypes, sret.restype = [ctypes.c_uint64] * 2, Large
            nested = library.ArkDpsTestNested
            nested.argtypes, nested.restype = [ctypes.c_uint64], ctypes.c_uint64
            integer_result = integer(*range(1, 13))
            floating_result = floating(1.5, 2.5, 3.5, 4.5, 5.5)
            large = sret(17, 29)
            nested_result = nested(19)
            print(json.dumps(dict(integer=integer_result, floating=floating_result,
                                  sret=[getattr(large, name) for name in 'abcd'], nested=nested_result)), flush=True)
        elif operation == 'full-arguments':
            full = libraries[0].ArkDpsTestFullArguments
            full.argtypes, full.restype = [ctypes.c_uint64] * len(FULL_ARGUMENT_VALUES), ctypes.c_uint64
            print(json.dumps(dict(result=full(*FULL_ARGUMENT_VALUES))), flush=True)
        elif operation == 'benchmark':
            count = command['count']
            if type(count) is not int or not 1 <= count <= 50_000:
                raise ValueError('invalid self-owned benchmark count')
            function = libraries[0].ArkDpsTestInteger
            function.argtypes, function.restype = [ctypes.c_uint64] * 12, ctypes.c_uint64
            arguments = tuple(range(1, 13))
            start = time.perf_counter_ns()
            total = sum(function(*arguments) for _ in range(count))
            print(json.dumps(dict(count=count, elapsed_ns=time.perf_counter_ns() - start, total=total)), flush=True)
        elif operation == 'threads':
            integer = libraries[0].ArkDpsTestInteger
            integer.argtypes, integer.restype = [ctypes.c_uint64] * 12, ctypes.c_uint64
            results = Queue()

            def invoke():
                results.put([integer(*range(1, 13)) for _ in range(5)])

            threads = [Thread(target=invoke) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError('self-owned ABI callbacks did not return')
            print(json.dumps(dict(results=[results.get_nowait() for _ in threads])), flush=True)
        else:
            raise ValueError('unknown self-owned fixture command')


class Fixture:
    def __init__(self, root, binary):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.binary = self.root / binary.name
        shutil.copyfile(binary, self.binary)
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child', str(self.binary)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8')
        self.lines = Queue()

        def reader():
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put(None)

        Thread(target=reader, daemon=True).start()
        self.identity = self.receive()
        self.token = secrets.token_urlsafe(48)
        self.pipe_name = r'\\.\pipe\ArkDPS.NativeEvents.' + secrets.token_hex(16)

    def receive(self):
        try:
            line = self.lines.get(timeout=15)
        except Empty as exc:
            raise RuntimeError('self-owned fixture timed out') from exc
        if line is None:
            raise RuntimeError(f'self-owned fixture exited ({self.process.poll()})')
        return json.loads(line)

    def command(self, operation, **fields):
        self.process.stdin.write(json.dumps(dict(operation=operation, **fields)) + '\n')
        self.process.stdin.flush()
        return self.receive()

    def configure(self, *, binary=None, pipe=None, token=None, source=None, created=None):
        binary = binary or self.binary
        config = dict(pipe=pipe or self.pipe_name, token=token or self.token,
                      process_instance=[self.identity['pid'], self.identity['created'] if created is None else created],
                      source=source or fixture_source(self.binary))
        binary.with_suffix('.json').write_text(json.dumps(config), encoding='utf-8')

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write('{"operation":"exit"}\n')
            self.process.stdin.flush()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.stderr.close()


def check_quiet_mark_boundaries(binary):
    from native_pc.native_hook_source import NativePipe
    binary = Path(binary).resolve()
    checks = {}
    with TemporaryDirectory(prefix='arkdps-quiet-mark-') as temporary:
        for case in ('autochess', 'ordinary', 'terminal-mark'):
            fixture = Fixture(Path(temporary) / case, binary)
            pipe = None
            try:
                buffer = fixture.command('owned-sret-buffer')['address']
                fixture.configure(source=quiet_mark_source(binary, buffer, autochess=case != 'ordinary'))
                fixture.command('load')
                pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
                ready = pipe.request('initialize', token=fixture.token)['ready']
                assert fixture.command('call-owned-sret')['result'] == [17, 29, 46, 12]
                fixture.command('owned-sret-buffer')
                results = Queue()

                def request_mark():
                    try:
                        options = {} if case == 'terminal-mark' else {'native_quiet_boundary_version': 1}
                        results.put(pipe.request('mark', **options))
                    except Exception as exc:
                        results.put(exc)

                pending = Thread(target=request_mark, daemon=True)
                pending.start()
                deadline = time.monotonic() + 5
                while fixture.command('memory', address=ready['markWaiting'])['value'] != 1:
                    if time.monotonic() >= deadline:
                        raise AssertionError('owned slow mark did not start')
                    time.sleep(.005)
                assert fixture.command('start-owned-sret')['started'] == 1
                mark = results.get(timeout=5)
                pending.join(timeout=5)
                if isinstance(mark, Exception):
                    raise mark
                assert mark['result_buffer'] == ['0'] * 4, mark
                assert (mark['sequence'], mark['frame'], mark['game_time']) == (1, 30, 1), mark
                assert fixture.command('join-threads')['results'] == [[17, 29, 46, 12]]
                if case == 'autochess':
                    assert mark['active'] == 0 and mark['retry'] is False, mark
                    assert mark['native_quiet_boundary_version'] == 1 and mark['native_deferred_callbacks'] == 1
                    assert pipe.request('read', after=0, through=1)['events'] == [
                        {'sequence': 1, 'frame': 30, 'game_time': 1}]
                    denied = pipe.request('ack', sequence=2)
                    assert denied.get('error') == 'ack beyond read' and denied['acknowledged'] == 0
                    assert pipe.request('ack', sequence=1)['acknowledged'] == 1
                    following = pipe.request('mark', native_quiet_boundary_version=1)
                    assert (following['sequence'], following['frame'], following['game_time']) == (2, 60, 2)
                    assert pipe.request('read', after=1, through=2)['events'] == [
                        {'sequence': 2, 'frame': 60, 'game_time': 2}]
                    assert pipe.request('ack', sequence=2)['acknowledged'] == 2
                    fixture.command('start-threads', function='Slow')
                    deadline = time.monotonic() + 5
                    while fixture.command('memory', address=ready['activity'])['value'] != 1:
                        if time.monotonic() >= deadline:
                            raise AssertionError('did not enter owned slow originals')
                        time.sleep(.005)
                    prior = pipe.request('mark', native_quiet_boundary_version=1)
                    assert prior['active'] > 0 and prior['retry'] is True, prior
                    assert 'native_quiet_boundary_version' not in prior
                    terminal = pipe.request('terminal_read', after=1, through=2)
                    assert terminal['events'] == [] and terminal['hp_context'] == [] and terminal['retry'] is True
                    assert fixture.command('join-threads')['results'] == [523] * 4
                else:
                    assert mark['active'] == 1 and mark['retry'] is True, mark
                    assert 'native_quiet_boundary_version' not in mark
                stopped = pipe.request('stop')
                assert stopped['active'] == 0 and stopped['native_quiet'] is True, stopped
                checks[case] = {'mark_sequence': mark['sequence'], 'active': mark['active'],
                                'retry': mark['retry'], 'original_waited': True,
                                'deferred_callbacks': mark.get('native_deferred_callbacks', 0)}
            finally:
                if pipe is not None:
                    pipe.close()
                fixture.close()
    return checks


def check_smoke(binary=DEFAULT_OUTPUT):
    from native_pc.native_hook_source import NativePipe
    binary = Path(binary).resolve()
    receipt = dict(game_connected=False, fixture='new-self-owned-python-processes', dll_sha256=sha256(binary.read_bytes()).hexdigest())
    # Persist scratch data under runtime, not a randomly selected user directory.
    with TemporaryDirectory(prefix='native-smoke-', dir=binary.parent) as temporary:
        root = Path(temporary)
        fixture = Fixture(root / 'unauthorized', binary)
        pipe = None
        try:
            fixture.configure()
            original = fixture.command('load')['code']
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            try:
                pipe.request('initialize', token='x' * 64)
                raise AssertionError('unauthorized initialization succeeded')
            except RuntimeError as exc:
                assert str(exc) == 'runtime authentication failed', str(exc)
            assert fixture.command('snapshot')['code'] == original
            receipt['unauthorized_hooks_not_installed'] = True
        finally:
            if pipe is not None:
                pipe.close()
            fixture.close()

        fixture = Fixture(root / 'abi', binary)
        pipe = second = None
        try:
            fixture.configure()
            original = fixture.command('load')['code']
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            ready = pipe.request('initialize', token=fixture.token)
            assert ready == dict(provider='arkdps-native-hooks', protocol=1, ready=dict(ready=True, fixture='self-owned-abi'))
            assert fixture.command('snapshot')['code'] != original
            called = fixture.command('call')
            assert called == dict(integer=650, floating=62.5, sret=[17, 29, 46, 12], nested=687), called
            multithread = fixture.command('threads')
            assert multithread['results'] == [[650] * 5] * 4
            full_arguments = 'ArkDpsTestFullArguments' in export_offsets(binary)
            if full_arguments:
                full_result = 0
                for value in FULL_ARGUMENT_VALUES:
                    full_result ^= value
                assert fixture.command('full-arguments') == {'result': full_result}
            mark = pipe.request('mark')
            assert mark['active'] == 0
            records = mark['records']
            expected = Counter({
                ('Integer', 'enter'): 22, ('Integer', 'leave'): 22,
                ('Float', 'enter'): 1, ('Float', 'leave'): 1,
                ('Sret', 'enter'): 1, ('Sret', 'leave'): 1,
                ('Nested', 'enter'): 1, ('Nested', 'leave'): 1,
            })
            if full_arguments:
                expected.update({('FullArguments', 'enter'): 1, ('FullArguments', 'leave'): 1})
                assert mark['retained_arguments'] == dict(
                    array=True, length=16, values=list(map(str, FULL_ARGUMENT_VALUES)),
                    keys=list(map(str, range(16))), samePointer=True, shadowed=True, deleted=True)
                receipt['retained_all_16_arguments_preserve_array_and_uint64'] = True
            assert Counter((record['name'], record['phase']) for record in records) == expected
            starts = {record['serial']: record for record in records if record['phase'] == 'enter'}
            ends = {record['serial']: record for record in records if record['phase'] == 'leave'}
            assert starts.keys() == ends.keys()
            assert all(starts[key]['name'] == ends[key]['name'] for key in starts)
            integer_starts = [record for record in starts.values() if record['name'] == 'Integer']
            assert sum(record['values'] == list(map(str, range(1, 13))) for record in integer_starts) == 21
            assert any(record['values'] == ['19', *map(str, range(2, 13))] for record in integer_starts)
            assert [record['values'] for record in ends.values() if record['name'] == 'Sret'] == [['17', '29', '46', '12']]
            nested_index = next(index for index, record in enumerate(records)
                                if record['name'] == 'Nested' and record['phase'] == 'enter')
            assert [(record['name'], record['phase']) for record in records[nested_index:nested_index + 4]] == [
                ('Nested', 'enter'), ('Integer', 'enter'), ('Integer', 'leave'), ('Nested', 'leave')]
            assert pipe.request('ack', sequence=7) == {'acknowledged': 7}
            receipt.update(abi_results=called, hooked_calls=len(starts), multithread_calls=20,
                           stack_arguments_preserved=True, floating_result_preserved=True,
                           sret_preserved=True, nested_callbacks_paired=True)

            second_binary = root / 'second' / binary.name
            second_binary.parent.mkdir()
            shutil.copyfile(binary, second_binary)
            second_name = r'\\.\pipe\ArkDPS.NativeEvents.' + secrets.token_hex(16)
            second_token = secrets.token_urlsafe(48)
            fixture.configure(binary=second_binary, pipe=second_name, token=second_token,
                              source="throw new Error('second owner source must never run');")
            fixture.command('load', binary=str(second_binary))
            second = NativePipe(second_name, fixture.identity['pid'])
            try:
                second.request('initialize', token=second_token)
                raise AssertionError('second native hook owner was admitted')
            except RuntimeError as exc:
                assert 'already active' in str(exc), str(exc)
            receipt['second_owner_rejected_before_source'] = True
            stopped = pipe.request('stop')
            assert stopped.get('stopped') is True and stopped.get('active') == 0 and stopped.get('native_quiet') is True
            # Stopped hooks no longer produce callbacks; the pinned DLL still executes normally.
            assert fixture.command('call') == called
            assert fixture.command('snapshot')['code'] == original
            receipt['stop_preserves_callable_dll'] = True
        finally:
            if second is not None:
                second.close()
            if pipe is not None:
                pipe.close()
            fixture.close()

        fixture = Fixture(root / 'wrong-instance', binary)
        unexpected = None
        try:
            fixture.configure(created=fixture.identity['created'] + 1)
            original = fixture.command('load')['code']
            try:
                unexpected = NativePipe(fixture.pipe_name, fixture.identity['pid'], timeout=.5)
                raise AssertionError('wrong process creation instance opened a transport')
            except OSError:
                pass
            assert fixture.command('snapshot')['code'] == original
            receipt['wrong_process_instance_rejected'] = True
        finally:
            if unexpected is not None:
                unexpected.close()
            fixture.close()

        fixture = Fixture(root / 'disconnect', binary)
        pipe = None
        try:
            fixture.configure()
            original = fixture.command('load')['code']
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            pipe.request('initialize', token=fixture.token)
            assert fixture.command('snapshot')['code'] != original
            pipe.close()
            pipe = None
            deadline = time.monotonic() + 5
            while fixture.command('snapshot')['code'] != original:
                if time.monotonic() >= deadline:
                    raise AssertionError('disconnect did not stop native hooks')
                time.sleep(.01)
            assert fixture.command('call')['integer'] == 650
            receipt['disconnect_stops_hooks'] = True
        finally:
            if pipe is not None:
                pipe.close()
            fixture.close()

        fixture = Fixture(root / 'stop-concurrency', binary)
        pipe = None
        try:
            fixture.configure(source=fixture_source(binary, slow=True))
            original = fixture.command('load')['code']
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            ready = pipe.request('initialize', token=fixture.token)
            address = ready['ready']['activity']
            assert fixture.command('start-threads', function='Slow') == {'started': 4}
            deadline = time.monotonic() + 5
            while fixture.command('memory', address=address)['value'] != 1:
                if time.monotonic() >= deadline:
                    raise AssertionError('did not observe an in-flight native JS callback')
                time.sleep(.005)
            stopped = pipe.request('stop')
            assert stopped.get('stopped') is True and stopped.get('active') == 0 and stopped.get('native_quiet') is True, stopped
            assert fixture.command('join-threads') == {'results': [523] * 4}
            assert fixture.command('snapshot')['code'] == original
            assert fixture.command('memory', address=address)['value'] == 0
            receipt['stop_drains_observed_concurrent_callbacks'] = True
        finally:
            if pipe is not None:
                pipe.close()
            fixture.close()

        fixture = Fixture(root / 'fatal-callback', binary)
        pipe = None
        try:
            source = fixture_source(binary).replace('this.serial = ++serial;',
                "if(name==='Integer')throw new Error('owned-callback-failure');this.serial = ++serial;")
            fixture.configure(source=source)
            original = fixture.command('load')['code']
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            pipe.request('initialize', token=fixture.token)
            assert fixture.command('call')['integer'] == 650
            try:
                pipe.request('mark')
                raise AssertionError('fatal native callback was reported as a valid event mark')
            except RuntimeError as exc:
                assert str(exc).startswith('native runtime mark failed; error_type=Error'), str(exc)
                assert 'owned-callback-failure' not in str(exc)
            pipe.close()
            pipe = None
            deadline = time.monotonic() + 5
            while fixture.command('snapshot')['code'] != original:
                if time.monotonic() >= deadline:
                    raise AssertionError('fatal callback cleanup did not disable native hooks')
                time.sleep(.01)
            assert fixture.command('call')['integer'] == 650
            receipt['fatal_callback_reported_and_hooks_disabled'] = True
        finally:
            if pipe is not None:
                pipe.close()
            fixture.close()
    return receipt


def check_oversized_response(binary=DEFAULT_OUTPUT):
    """Send one oversized battle-like reply through the real authenticated pipe."""
    from native_pc.native_hook_source import NativePipe
    binary = Path(binary).resolve()
    event_count = 20_000
    event = dict(frame=420, game_time=14, source=None, kind='enemy_hp', amount=0,
                 proof={'method': 'Entity.SetHpInternal'}, target='enemy_fixture_guard', target_instance='12345',
                 enemy_max_hp=38_818, hp_before=30_000, hp_after=30_001,
                 native_autochess_scope=dict(protocol_version=1, status='available', verified_same_frame=True,
                     frame=420, game_time=14, scope_key='0x12341234:0x23452345:0x34563456:0x45674567:0x56785678:autochess:1',
                     phase=4, round=8, collection_mode='all'))
    wire_limit = 8 * 1024 ** 2
    bytes_ = len(json.dumps(dict(id=3, result={'events': [dict(event, sequence=sequence)
                            for sequence in range(1, event_count + 1)]}), separators=(',', ':')).encode())
    assert bytes_ > wire_limit
    source = fixture_source(binary) + '\nconst event=' + json.dumps(event) + ''';
rpc.exports.readEvents=(after,through)=>{
 const events=[];
 for(let sequence=after+1;sequence<=through;sequence++)events.push({...event,sequence});
 return {events};
};
'''
    with TemporaryDirectory(prefix='native-oversized-response-', dir=binary.parent) as temporary:
        fixture = Fixture(Path(temporary), binary)
        pipe = None
        try:
            fixture.configure(source=source)
            original = fixture.command('load')['code']
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            pipe.request('initialize', token=fixture.token)
            assert fixture.command('snapshot')['code'] != original
            assert pipe.request('read', after=0, through=1) == {'events': [dict(event, sequence=1)]}
            native_io = pipe._io
            read_after_stop = False

            def delay_read_until_stopped(operation, buffer, size, deadline):
                nonlocal read_after_stop
                if operation == 'ReadFile' and not read_after_stop:
                    while fixture.command('snapshot')['code'] != original:
                        if time.monotonic() >= deadline:
                            raise AssertionError('oversized response did not release the native hooks')
                        time.sleep(.01)
                    read_after_stop = True
                return native_io(operation, buffer, size, deadline)

            pipe._io = delay_read_until_stopped
            try:
                pipe.request('read', after=0, through=event_count)
                raise AssertionError('oversized reply was accepted')
            except RuntimeError as exc:
                assert str(exc) == (f'response_too_large; bytes={bytes_}; limit={wire_limit}; '
                                    f'requested_event_count={event_count}'), str(exc)
            assert read_after_stop and pipe.handle is not None and fixture.command('call')['integer'] == 650
            return dict(bytes=bytes_, wire_limit=wire_limit, requested_event_count=event_count,
                        error='response_too_large', hooks_disabled=True, delayed_read_delivered=True)
        finally:
            if pipe is not None:
                pipe.close()
            fixture.close()


def check_diagnostics(binary=DEFAULT_OUTPUT):
    """Only fixed diagnostic vocabulary may survive an authenticated failure."""
    from native_pc.native_hook_source import NativePipe
    binary = Path(binary).resolve()
    cases = [
        ("throw new Error('unsupported layout BattleController.m_logger');", 'unsupported layout BattleController.m_logger'),
        ("throw new Error('class not found LevelData');", 'class not found LevelData'),
        ("throw new Error('required method missing Entity.ApplyModifier');", 'required method missing Entity.ApplyModifier'),
        ("throw new Error('native hook creation failed: MH_ERROR_UNSUPPORTED_FUNCTION');", 'MH_ERROR_UNSUPPORTED_FUNCTION'),
        ("throw new TypeError('not a function');", 'TypeError: not a function'),
        ("Memory.alloc(8).readDouble();", 'TypeError: not a function'),
        ("throw new Error('RAW_SOURCE_HIDDEN C:/private/config.json 0xdeadbeef TOKEN_VALUE_HIDDEN');", 'error_type=Error'),
        ("throw new Error('class not found TOKEN_VALUE_HIDDEN');", 'class not found'),
    ]
    with TemporaryDirectory(prefix='native-diagnostic-smoke-', dir=binary.parent) as temporary:
        for index, (source, expected) in enumerate(cases):
            fixture = Fixture(Path(temporary) / str(index), binary)
            pipe = None
            try:
                fixture.configure(source=source)
                fixture.command('load')
                pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
                try:
                    pipe.request('initialize', token=fixture.token)
                    raise AssertionError('throwing self-owned initialization was accepted')
                except RuntimeError as exc:
                    message = str(exc)
                    assert message.startswith('native runtime initialize failed;'), message
                    assert expected in message, message
                    assert len(message) <= 256 and '\n' not in message
                    for forbidden in ('RAW_SOURCE_HIDDEN', 'TOKEN_VALUE_HIDDEN', 'C:/private', '0xdeadbeef', fixture.token):
                        assert forbidden not in message, message
            finally:
                if pipe is not None:
                    pipe.close()
                fixture.close()
    return dict(cases=len(cases), bounded_to_256=True, fixed_vocabulary_only=True, game_connected=False)


def check_dual_hook(binary=DEFAULT_OUTPUT):
    """Optional comparator coexistence; never install Frida or attach a game."""
    try:
        installed = version('frida')
    except PackageNotFoundError:
        return dict(status='not_run', reason='Frida is not installed in this interpreter', game_connected=False)
    if installed != '17.26.1':
        return dict(status='not_run', reason='Frida version is not the pinned comparator', frida_version=installed,
                    game_connected=False)
    from native_pc.native_hook_source import NativePipe
    binary = Path(binary).resolve()
    with TemporaryDirectory(prefix='native-dual-smoke-', dir=binary.parent) as temporary:
        fixture = Fixture(Path(temporary) / 'owned', binary)
        pipe = session = script = None
        native_stopped = False
        stage = 'attach-comparator'
        try:
            import frida
            assert fixture.identity['pid'] == fixture.process.pid
            fixture.configure()
            original = fixture.command('load')['code']
            session = frida.get_local_device().attach(fixture.process.pid)
            script = session.create_script(r'''
const records=[];
const module=Process.getModuleByName('ArkDPS.NativeEvents.dll');
for(const name of ['Integer','Float'])Interceptor.attach(module.getExportByName('ArkDpsTest'+name),{
 onEnter(){records.push({name,phase:'enter'});},onLeave(){records.push({name,phase:'leave'});}
});
rpc.exports={take(){return records.splice(0);}};
''')
            script.load()
            frida_code = fixture.command('snapshot')['code']
            assert frida_code != original
            stage = 'initialize-native'
            pipe = NativePipe(fixture.pipe_name, fixture.identity['pid'])
            pipe.request('initialize', token=fixture.token)
            stage = 'compare-callbacks'
            called = fixture.command('call')
            assert called == dict(integer=650, floating=62.5, sret=[17, 29, 46, 12], nested=687)
            native = pipe.request('mark')['records']
            expected = Counter({('Integer', 'enter'): 2, ('Integer', 'leave'): 2,
                                ('Float', 'enter'): 1, ('Float', 'leave'): 1})
            comparator = script.exports_sync.take()
            assert Counter((event['name'], event['phase']) for event in comparator) == expected
            assert Counter((event['name'], event['phase']) for event in native
                           if event['name'] in ('Integer', 'Float')) == expected
            stage = 'stop-native'
            stopped = pipe.request('stop')
            assert stopped.get('active') == 0 and stopped.get('native_quiet') is True
            native_stopped = True
            assert fixture.command('snapshot')['code'] == frida_code
            assert fixture.command('call') == called
            assert Counter((event['name'], event['phase']) for event in script.exports_sync.take()) == expected
            stage = 'unload-comparator'
            script.unload()
            script = None
            session.detach()
            session = None
            assert fixture.command('snapshot')['code'] == original
            assert fixture.command('call') == called
            return dict(status='passed', frida_version=installed, game_connected=False,
                        fixture='new-self-owned-python-process', compared_callbacks=len(comparator),
                        native_stopped_before_frida_unloaded=True, original_code_restored=True,
                        abi_results=called)
        except Exception as exc:
            return dict(status='failed', frida_version=installed, game_connected=False,
                        fixture='new-self-owned-python-process', stage=stage, error_type=type(exc).__name__)
        finally:
            if pipe is not None:
                # A failed native stop must not be followed by comparator unpatching.
                if not native_stopped:
                    try:
                        stopped = pipe.request('stop')
                        if stopped.get('active') != 0 or stopped.get('native_quiet') is not True:
                            raise RuntimeError('native callback fence unavailable')
                    except Exception:
                        # Terminate only the self-owned fixture; do not unload a live hook chain.
                        if fixture.process.poll() is None:
                            fixture.process.kill()
                            fixture.process.wait(timeout=5)
                pipe.close()
            if fixture.process.poll() is None:
                if script is not None:
                    script.unload()
                if session is not None:
                    session.detach()
            fixture.close()


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--dual-hook', action='store_true', help='also compare installed Frida in self-owned processes only')
    parser.add_argument('--child', type=Path, help=__import__('argparse').SUPPRESS)
    args = parser.parse_args()
    if args.child is not None:
        child(args.child)
        return
    result = check_smoke(args.binary)
    result['authenticated_diagnostics'] = check_diagnostics(args.binary)
    result['oversized_response'] = check_oversized_response(args.binary)
    result['quiet_mark_boundaries'] = check_quiet_mark_boundaries(args.binary)
    if args.dual_hook:
        result['dual_hook_comparator'] = check_dual_hook(args.binary)
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
