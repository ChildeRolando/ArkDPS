const fs=require('fs'),vm=require('vm'),assert=require('assert');const c=vm.createContext({});
vm.runInContext(`function __read(){throw Error('LIVE');}function __pid(){return 1;}function __nativeActive(){return 0;}
function __nativeOwnedLease(ticket){return ticket==='17';}function __nativeOwnedRead(){}function __nativeOwnedScalar(){}function __nativeOwnedPoint(){return 0;}function __nativeOwnedStatus(){return 0;}
function __nativeOwnedHpObservation(){return [1,1,0,0,5,0,4096n,12];}function __nativeOwnedConsumeHpFailure(){}`,c);
let bridge=fs.readFileSync('E:/文档/ChatGPT/ArkDPS/arkdps-hp-raw-observation/native_pc/native_event_runtime/bridge.js','utf8');
if(process.env.ARKDPS_REVIEW_MUTATE_RECEIVER==='1'){assert(bridge.includes('if(root!==this.#root)'));bridge=bridge.replace('if(root!==this.#root)','if(false&&root!==this.#root)');}
vm.runInContext(bridge,c);
vm.runInContext(`const record={version:1,native_view:true,ticket:'17',edge:'entry',checkpoint:0,facts:{mode_epoch:5}};
const contract={address:'0x7000',mode_epoch:0,fields:[{phase:'entry',name:'hp.identity',role:'body',kind:'bytes',bytes:28,observation:{name:'hp.identity',values:[{kind:'u64',offset:0},{kind:'u32',offset:24}]}}]};
__arkdpsReplayBegin(record);const receiver=ptr(0x1000),io=__arkdpsOwnedHpObservation(contract,receiver);globalThis.before=io.identity(receiver).map(String);receiver.value=0x9999n;try{globalThis.after=io.identity(receiver).map(String);}catch(e){globalThis.afterError=String(e);}globalThis.complete=__arkdpsReplayEnd().complete;`,c);
console.log(JSON.stringify({before:c.before,after:c.after,afterError:c.afterError,complete:c.complete}));
assert(c.afterError,'changed receiver must fail');assert.equal(c.complete,false,'caught receiver mismatch remains structural');
