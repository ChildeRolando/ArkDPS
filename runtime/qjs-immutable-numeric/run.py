from pathlib import Path
import hashlib,json,subprocess,tarfile,statistics,os
OUT=Path(__file__).resolve().parent
BASE=OUT.parents[1]
STAGE=BASE.parent/'arkdps-native-async-engine/runtime/async-engine-build/stage'
CACHE=BASE.parent/'arkdps-native-event-backend/runtime/native-pc/native-event-runtime/dependencies/quickjs-2025-09-13.tar.xz'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert digest(CACHE)=='6f1f322aea3bb3a90858db85c9fe717013fde4df7dfcafe2f57e78f5bb4b4a0c'
with tarfile.open(CACHE) as tf:
 for name in ['quickjs.c','quickjs.h','dtoa.c','libregexp.c','libunicode.c','cutils.c']:
  data=tf.extractfile('quickjs-2025-09-13/'+name).read()
  assert data==(STAGE/'quickjs-2025-09-13'/name).read_bytes(),name
objects=[STAGE/f'object-{i}.o' for i in range(5)]
command=['g++','-std=c++17','-static','-O2','-I',str(STAGE/'quickjs-headers'),str(OUT/'micro.cpp'),*[str(o) for o in objects],'-o',str(OUT/'micro.exe')]
command=[os.path.relpath(x,OUT) if x.startswith(str(BASE.parent)) else x for x in command]
build=subprocess.run(command,cwd=OUT,capture_output=True,text=True)
(OUT/'build-stdout.txt').write_text(build.stdout);(OUT/'build-stderr.txt').write_text(build.stderr)
assert build.returncode==0,build.stderr
run=subprocess.run([str(OUT/'micro.exe')],capture_output=True,text=True,timeout=20)
(OUT/'stdout.json').write_text(run.stdout);(OUT/'stderr.txt').write_text(run.stderr)
assert run.returncode==0,run.stderr
result=json.loads(run.stdout)
summary={}
for mixed in [0,1]:
 summary[mixed]={}
 for type in range(4):
  rows=[r for r in result['rows'] if r['mixed']==mixed and r['type']==type]
  summary[mixed][type]={k:statistics.median(r[k] for r in rows) for k in ['read_ns','construct_ns']}
receipt={'kind':'owned QuickJS immutable numeric property microbenchmark only','archive_sha256':digest(CACHE),'archive_sources_exact':True,'command':command,'driver_sha256':digest(Path(__file__)),'inputs_sha256':{str(p):digest(p) for p in [OUT/'micro.cpp',*objects,STAGE/'quickjs-headers/quickjs.h',STAGE/'quickjs-2025-09-13/quickjs.c']},'binary_sha256':digest(OUT/'micro.exe'),'summary':summary,'types':{'0':'native enumerable-only readonly array + PreventExtensions + JS freeze','1':'C_W_E array + JS freeze','2':'custom immutable exotic numeric object fixed atom table','3':'native enumerable-only readonly array + PreventExtensions'},'limitations':['microbenchmark not shared source/worker/Game/FPS','custom object not Array-compatible drop-in','no native provider/source API change','fixed atom table is setup-owned; payload separately allocated and finalized','cached object source compile flags not independently rebuilt; actual object SHA preserved'],'result':result,'stderr':run.stderr}
(OUT/'receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(summary,indent=2));print(run.stderr)


