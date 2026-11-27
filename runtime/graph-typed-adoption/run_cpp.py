from pathlib import Path
import subprocess,json,hashlib
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
STAGE=ROOT.parent/'arkdps-native-async-engine/runtime/async-engine-build/stage'
NATIVE=ROOT/'native_pc/native_event_runtime'
objects=[STAGE/f'object-{i}.o' for i in range(5)]
assert hashlib.sha256((STAGE/'json.hpp').read_bytes()).hexdigest()=='9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'
mut=OUT/'mutation';mut.mkdir(exist_ok=True)
header=(NATIVE/'owned_replay_js.h').read_text(encoding='utf8')
before='||JS_VALUE_GET_PTR(args[4])!=JS_VALUE_GET_PTR(owner.graph_packet)';assert header.count(before)==1
(mut/'owned_replay_js.h').write_text(header.replace(before,'||false /* deliberate exact-identity regression */'),encoding='utf8')
report={'runs':{},'inputs_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [*objects,ROOT/'tests/owned_read_plan_test.cpp',NATIVE/'owned_replay_js.h',NATIVE/'engine_capture_config.h',NATIVE/'engine_capture_plan.h',NATIVE/'bridge.js']}}
for label in ['control','identity-mutant']:
 binary=OUT/(label+'.exe')
 command=['g++','-std=c++17','-static','-O2','-pthread','-Wall','-Wextra','-Werror',*(['-I',str(mut)] if label!='control' else []),'-I',str(NATIVE),'-isystem',str(STAGE/'quickjs-headers'),'-isystem',str(STAGE),str(ROOT/'tests/owned_read_plan_test.cpp'),str(NATIVE/'native_capture.cpp'),*[str(p) for p in objects],'-o',str(binary)]
 built=subprocess.run(command,capture_output=True,text=True,timeout=50)
 (OUT/(label+'-build.txt')).write_text(built.stdout+built.stderr,encoding='utf8');assert built.returncode==0,built.stderr
 run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
 (OUT/(label+'-stdout.txt')).write_text(run.stdout,encoding='utf8');(OUT/(label+'-stderr.txt')).write_text(run.stderr,encoding='utf8')
 assert (run.returncode==0)==(label=='control'),(label,run.returncode,run.stderr)
 report['runs'][label]={'command':command,'returncode':run.returncode,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'stderr':run.stderr,'stdout':run.stdout}
report['driver_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(OUT/'cpp-receipt.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('actual CPP passed; exact packet identity mutation killed')
