from pathlib import Path
import subprocess,json,hashlib
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
report={'base':'d107f11e883adbc6d4209c8be9c3279a4ef1c6ef','tests':'112 passed in 4.79s; PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, --assert=plain','mutations':{}}
cases=[('fixed_equality','event_probe_runtime.js',"(closedFixed!==undefined&&(typeof closedFixed!=='bigint'||closedFixed!==fixed))","false",'tests/event_probe_hp_observation_fixture.js'),('same_item_token','event_probe_native_pc.js','saved.token===__arkdpsReplayModeRecord()','true','tests/native_pc_captured_publication_fixture.js')]
for label,file,before,after,test in cases:
 source=(ROOT/file).read_text(encoding='utf8');assert source.count(before)==1
 js="const fs=require('fs'),path=require('path');const original=fs.readFileSync;fs.readFileSync=function(file,...args){const value=original.call(this,file,...args);if(path.resolve(String(file))===path.resolve("+json.dumps(file)+")){if(typeof value!=='string')throw Error('unexpected byte source');return value.replace("+json.dumps(before)+","+json.dumps(after)+");}return value;};require("+json.dumps('./'+test)+");"
 result=subprocess.run(['node','-e',js],cwd=ROOT,capture_output=True,text=True)
 assert result.returncode!=0,label
 (OUT/(label+'-stderr.txt')).write_text(result.stderr,encoding='utf8');report['mutations'][label]={'returncode':result.returncode,'failure':result.stderr[:1600]}
report['files_sha256']={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['event_probe_native_pc.js','event_probe_runtime.js','native_pc/native_event_runtime/bridge.js']}
report['review']={'evidence':'full 12-byte clock_close field already captured with unique/expected-ID/guard; extra codec reads no new memory','owner':'same shared validatedClockValues and applyHpEntry/Leave; no amount/state rules added','lease':'private sample WeakMap uses exact bridge-owned immutable per-item ModeRecord identity, plus sample frame/time; no publicRecord creation or claimed manual ticket read','fallback':'old IO three tuple and missing mode helper preserve original captureClockFixed; cold/mark/nondeferred stamping before cache registration unchanged','freshness':'scope graph all independent clocks/roots/phase/state guards still execute before tail reuse','coverage':'independent closing fixed mismatch/allowRetry; old and new provider tuple; same-item reuse; old IO; absent mode helper; cross-record fallback; changed raw scope close; no publicRecord remember'}
(OUT/'receipt.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('2 strict mutations killed; receipt saved')
