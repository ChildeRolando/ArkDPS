const fs=require('fs'),path=require('path'),Module=require('module'),assert=require('assert');
const originalRead=fs.readFileSync;
fs.readFileSync=function(file,...args){let value=originalRead.call(this,file,...args);if(process.env.ARKDPS_MUTATE_CONSTRUCTOR_BRAND==='1'&&String(file).replaceAll('\\','/').endsWith('native_pc/native_event_runtime/bridge.js')){
 value=value.replace('constructor(replay,packet,geometry,receiver,root){','constructor(replay,packet,geometry,receiver,root,contract,prepared){').replace('this.#root=root;Object.freeze(this);}','this.#root=root;if(prepared)__arkdpsPreparedHpObservations.set(this,{replay,contract,root});Object.freeze(this);}');}return value;};
const origin=path.resolve('tests/native_pc_hp_observation_fixture.js');let code=fs.readFileSync(origin,'utf8');
const injection=`
mode='ok';__arkdpsReplayBegin(record());
const publicNames=Object.create(null),fakePacket=[1,contract.fields.filter(f=>f.phase==='entry').length,0,3,5];let publicAt=5,publicIndex=0;
for(const field of contract.fields.filter(f=>f.phase==='entry')){const kinds=field.observation.values.map(v=>v.kind);publicNames[field.name]={index:publicIndex++,at:publicAt,role:field.role,kinds};fakePacket.push(0);for(const kind of kinds)fakePacket.push(kind==='u8'||kind==='u32'?123456:kind==='utf16'?'forged':4096n);publicAt+=1+kinds.length;}
const forgedConstructor=new __ArkDpsHpObservation(__arkdpsReplay,Object.freeze(fakePacket),{names:publicNames},enemy,4096n,contract,true);
globalThis.constructorBrand=__arkdpsIsPreparedHpObservation(forgedConstructor,contract,enemy);globalThis.constructorUID=forgedConstructor.identity(enemy)[1];globalThis.constructorEnd=__arkdpsReplayEnd();
`;
code=code.replace('globalThis.liveCount=live;',injection+'\nglobalThis.liveCount=live;');
code += "\nconsole.log(JSON.stringify({brand:c.constructorBrand,UID:c.constructorUID,complete:c.constructorEnd.complete}));\n";
const loaded=new Module(origin,module);loaded.filename=origin;loaded.paths=Module._nodeModulePaths(path.dirname(origin));loaded._compile(code,origin);

assert.equal(loaded.exports?.unused,undefined);
