const fs=require('fs'),path=require('path'),Module=require('module'),assert=require('assert');
const original=fs.readFileSync;
fs.readFileSync=function(file,...args){let value=original.call(this,file,...args);if(String(file).replaceAll('\\','/').endsWith('native_pc/native_event_runtime/bridge.js')&&process.env.ARKDPS_MUTATE_DIRECT_MASK==='1'){
 const needle="if(record.edge==='entry'&&argumentMask!==1)";assert(typeof value==='string'&&value.includes(needle));value=value.replace(needle,"if(false&&record.edge==='entry'&&argumentMask!==1)");}return value;};
const origin=path.resolve('tests/native_pc_hp_observation_fixture.js');const code=original.call(fs,origin,'utf8');const loaded=new Module(origin,module);loaded.filename=origin;loaded.paths=Module._nodeModulePaths(path.dirname(origin));try{loaded._compile(code,origin);}finally{fs.readFileSync=original;}
