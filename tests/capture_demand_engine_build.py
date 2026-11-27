"""Build uninstrumented production engine/native_capture with an owned mixed driver."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--dependencies',type=Path,default=ROOT/'runtime/terminal-destroy-prefix/final-build/dependencies');p.add_argument('--compiler',default='C:/msys64/ucrt64/bin/g++.exe');a=p.parse_args();source=a.source_root.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);native=source/'native_pc/native_event_runtime'
 base=ROOT/'tests/production_hp_actual_engine.cpp';text=base.read_text(encoding='utf-8');assert text.count('int main(int argc,char** argv)')==1
 text=text.replace('int main(int argc,char** argv)','int demand_unused_base_main(int argc,char** argv)').replace('#include "../native_pc/native_event_runtime/engine.cpp"','#include "engine.cpp"')
 (out/'capture-demand-engine-base.inc').write_text(text,encoding='utf-8')
 native_files=[*sorted(native.glob('*.h')),native/'engine.cpp',native/'native_capture.cpp'];fixtures=[base,ROOT/'tests/production_hp_quickjs.cpp',ROOT/'tests/production_hp_paced_engine.h',ROOT/'tests/production_sampling_engine.h',ROOT/'tests/production_terminal_engine.h',ROOT/'tests/capture_demand_engine.cpp',Path(__file__)]
 before={str(f):sha(f) for f in native_files+fixtures};binary=out/'mixed-damage.exe';deps=a.dependencies.resolve()
 command=[a.compiler,'-std=c++17','-static','-O2','-pthread','-DNOMINMAX','-I'+str(native),'-I'+str(ROOT/'tests'),'-I'+str(out),'-isystem',str(deps),str(ROOT/'tests/capture_demand_engine.cpp'),str(native/'native_capture.cpp'),*[str(deps/name) for name in ['quickjs.o','dtoa.o','libregexp.o','libunicode.o','cutils.o']],'-lpsapi','-o',str(binary)]
 r=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PATH=str(Path(a.compiler).parent)+';'+os.environ['PATH']),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120);(out/'build.stdout').write_text(r.stdout,encoding='utf-8');(out/'build.stderr').write_text(r.stderr,encoding='utf-8')
 receipt={'kind':'uninstrumented_actual_mixed_owned_engine','command':command,'source_root':str(source),'returncode':r.returncode,'source_sha256':before,'dependency_sha256':{f.name:sha(f) for f in deps.iterdir() if f.is_file()}}
 if binary.exists():receipt['binary_sha256']=sha(binary)
 (out/'build-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
 assert r.returncode==0,r.stderr[-4000:]
 assert before=={str(f):sha(f) for f in native_files+fixtures},'source changed during build'
 print(binary)
if __name__=='__main__':main()
