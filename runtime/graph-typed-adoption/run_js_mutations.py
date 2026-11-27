from pathlib import Path
import subprocess,json
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
source=(ROOT/'native_pc/native_event_runtime/bridge.js').read_text(encoding='utf8')
cases=[('layout',"if(packet.__arkdpsGraphLayout!==geometry.layout)","if(false)"),('recognition',"if(issued!==packet)","if(false)")]
report={}
for label,before,after in cases:
 assert source.count(before)==1
 js="const fs=require('fs'),path=require('path'),old=fs.readFileSync;fs.readFileSync=function(file,...args){const value=old.call(this,file,...args);return path.resolve(String(file))===path.resolve('native_pc/native_event_runtime/bridge.js')?value.replace("+json.dumps(before)+","+json.dumps(after)+"):value;};require('./tests/native_pc_graph_typed_adoption_fixture.js');"
 run=subprocess.run(['node','-e',js],cwd=ROOT,capture_output=True,text=True)
 assert run.returncode!=0,label
 (OUT/(label+'-mutation-stderr.txt')).write_text(run.stderr,encoding='utf8');report[label]={'returncode':run.returncode,'stderr':run.stderr}
(OUT/'js-mutations.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('exact-layout and exact-native-recognition JS mutations killed')
