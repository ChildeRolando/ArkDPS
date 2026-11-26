"""One self-owned DLL/QuickJS scalar-validation A/B, no external target.

Measures two copies of the actual bridge scalar function over the same native
owned lease. Does not measure production HP throughput or game FPS.
"""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from native_async_owned_view import source, fresh_mark
from native_pc.native_event_runtime.smoke import Fixture
from native_pc.native_hook_source import NativePipe


def scalar_function(text,name):
    start=text.index('function __arkdpsOwnedScalar(')
    end=text.index('\nfunction ',start+1)
    return text[start:end].replace('function __arkdpsOwnedScalar(', 'function '+name+'(',1)


def run(binary):
    old=subprocess.run(['git','show','c2d91d0:native_pc/native_event_runtime/bridge.js'],cwd=ROOT,
                       capture_output=True,text=True,encoding='utf-8',check=True).stdout
    new=(ROOT/'native_pc/native_event_runtime/bridge.js').read_text(encoding='utf-8')
    baseline=scalar_function(old,'baselineScalar')
    candidate=scalar_function(new,'candidateScalar')
    script=source(binary,'positive').replace("const address=Process.getModuleByName", "owned.add(56).writeU64(0x7fc00000n);\nconst address=Process.getModuleByName")
    script+='\n'+baseline+'\n'+candidate+r'''
let compared=false;
function checkScalars(){
 if(compared)return;compared=true;
 __arkdpsReplayPoint('body',()=>{
  const offsets=[0,8,16,24,32,40,48],expected=[251,65000,0xfedcba98,-123456,0xfedcba9876543210n,-9223372036854775807n,-13.25];
  const addresses=offsets.map(offset=>owned.value+BigInt(offset));
  const rows=[];
  for(const [name,fn]of [['baseline',baselineScalar],['candidate',candidateScalar]]){
   const started=Date.now();
   for(let kind=0;kind<7;kind++){
    let value;for(let i=0;i<100000;i++)value=fn(addresses[kind],kind);
    equal(value,expected[kind]);
   }
   equal(Number.isNaN(fn(owned.value+56n,6)),true);
   rows.push({name,operations:700000,elapsed_ms:Date.now()-started});
  }
  scalarDebug={comparison:rows,all_seven_scalar_values_equal:true,f32_nan_preserved:true};
 });
}
'''
    with TemporaryDirectory(prefix='arkdps-scalar-validation-') as temp:
        fixture=Fixture(Path(temp),binary);pipe=None
        try:
            fixture.configure(source=script);fixture.command('load')
            pipe=NativePipe(fixture.pipe_name,fixture.identity['pid'],timeout=15)
            ready=pipe.request('initialize',token=fixture.token)
            mark=fresh_mark(pipe,fixture,deadline_seconds=15)
            assert not mark['source_assertion_errors'],mark
            assert mark['native_async_capture']['failure_code']==0,mark
            assert mark['scalar_debug']['f32_nan_preserved'],mark
            return dict(result='PASS',classification=__doc__,identity=fixture.identity,
                binary_sha256=sha256(binary.read_bytes()).hexdigest(),source_sha256=sha256(script.encode()).hexdigest(),
                baseline_function_sha256=sha256(baseline.encode()).hexdigest(),candidate_function_sha256=sha256(candidate.encode()).hexdigest(),
                ready=ready,mark=mark,nudges=fresh_mark.last_nudges)
        finally:
            if pipe:pipe.close()
            fixture.close()


if __name__=='__main__':
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.binary.resolve());args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result['mark']['scalar_debug']))
