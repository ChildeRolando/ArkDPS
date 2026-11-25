"""Bounded real-QuickJS primitive A/B in new self-owned processes only."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture, export_offsets
from native_pc.native_hook_source import NativePipe


def source(binary, runtime):
    clock = runtime[runtime.index('function time('):runtime.index('function buff(')]
    start = runtime.index('const terminalClassNames=') if 'const terminalClassNames=' in runtime else runtime.index('function terminalIdentity(')
    identity = runtime[start:runtime.index('function currentTerminalBoundary(')]
    offset = export_offsets(binary)['ArkDpsTestInteger']
    return r'''
const probeAdapter={nativeOffset:0x10,loggerOffset:0xe0,levelOffset:0x160,statsOffset:0x30,nativeOwnerOffset:0x28,loggerOwnerOffset:0x18};
const controller=Memory.alloc(0x180),native=Memory.alloc(0x40),logger=Memory.alloc(0x40),level=Memory.alloc(0x20),stats=Memory.alloc(0x20);
function ascii(value){const address=Memory.alloc(value.length+8);for(let i=0;i<value.length+1;i+=8){let word=0n;for(let j=0;j<8&&i+j<value.length;j++)word|=BigInt(value.charCodeAt(i+j))<<BigInt(j*8);address.add(i).writeU64(word);}return address;}
function klass(name){const address=Memory.alloc(0x20);address.add(0x10).writePointer(ascii(name));return address;}
const controllerClass=klass('BattleController');
controller.writePointer(controllerClass);controller.add(0x10).writePointer(native);controller.add(0xe0).writePointer(logger);controller.add(0x160).writePointer(level);
native.add(0x28).writePointer(controller);logger.writePointer(klass('BattleLogger'));logger.add(0x18).writePointer(controller);logger.add(0x30).writePointer(stats);
level.writePointer(klass('LevelData'));stats.writePointer(klass('BattleStats'));
const clock=Memory.alloc(16);clock.writeU64(30);clock.add(4).writeU64(4294967296n);
function fp(address){return Number(BigInt(address.readS64().toString()))/4294967296;}
function className(address){return address.add(0x10).readPointer().readCString();}
function damageSettlementStamp(frame,game_time){return {frame,game_time};}
''' + clock + identity + r'''
let entered=0,left=0,failures=0;
const hook=Interceptor.attach(Process.getModuleByName('ArkDPS.NativeEvents.dll').base.add(OFFSET),{
 onEnter(){entered++;const a=time(false,false),x=terminalIdentity(controller,controllerClass);if(a.frame!==30||a.game_time!==1||!x)failures++;},
 onLeave(){left++;const a=time(false,false),x=terminalIdentity(controller,controllerClass);if(a.frame!==30||a.game_time!==1||!x)failures++;}
});
rpc.exports.mark=()=>({entered,left,failures});
rpc.exports.stop=()=>{hook.detach();return {stopped:true};};
send({ready:true,fixture:'self-owned-hp-clock-identity-primitives'});
'''.replace('OFFSET', str(offset))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    binary=args.binary.resolve()
    expected='551cbc03224f0cc85bde790644e783edb8ae0457e9bb370c75332ff3d268879a'
    assert hashlib.sha256(binary.read_bytes()).hexdigest()==expected
    args.output.parent.mkdir(parents=True,exist_ok=True)
    results={}
    with TemporaryDirectory(prefix='owned-native-primitives-',dir=args.output.parent) as temporary:
        for name,runtime in [('baseline',subprocess.check_output(['git','show','a782091:event_probe_runtime.js'],cwd=ROOT,text=True,encoding='utf-8')),('candidate',(ROOT/'event_probe_runtime.js').read_text(encoding='utf-8'))]:
            fixture=Fixture(Path(temporary)/name,binary);pipe=None
            try:
                fixture.configure(source=source(binary,runtime));fixture.command('load')
                pipe=NativePipe(fixture.pipe_name,fixture.identity['pid']);pipe.request('initialize',token=fixture.token)
                cold=fixture.command('benchmark',count=1216)
                warm=fixture.command('benchmark',count=1216)
                marks=pipe.request('mark')
                assert marks=={'entered':2432,'left':2432,'failures':0},marks
                assert cold['total']==warm['total']==650*1216
                results[name]={'cold':cold,'warm':warm,'proof':marks}
                pipe.request('stop')
            finally:
                if pipe:pipe.close()
                fixture.close()
    result={'classification':'self_owned_native_quickjs_primitives_not_full_hp_callback_not_game','dll_sha256':expected,'count_per_phase':1216,'concurrency':1,'results':results,'lock_time':None,'limits':'Measured elapsed_ns includes ctypes, native trampoline and actual QuickJS enter/leave clock+identity reads. This does not measure full HP/scope callbacks, isolated lock occupancy or game FPS.'}
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
