'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('event_probe_native_pc.js','utf8');
const context=vm.createContext({});vm.runInContext(source.slice(source.indexOf('function captureModeState('),source.indexOf('const probeAdapter=')),context);
vm.runInContext(`
nativeCaptureModeEpoch=0;nativeCaptureModeBarrier=0n;nativeAutoDesiredMode='all';nativeAutoEffective={scope_key:'old',mode:'all'};
nativeCaptureMetadata={};nativeCapturePublications=new Map();active=0;activeHooks=new Map();
__arkdpsReplayActive=()=>false;setAutoChessMode=enabled=>nativeAutoDesiredMode=enabled?'all':'local';
publishNativeCaptureScope=()=>{};publishNativeCapturePublication=()=>{};
function rejected(body){try{body();return false;}catch(e){return true;}}
globalThis.pendingRejected=rejected(()=>installCaptureMode(false,{version:1,mode_epoch:1,active:0,processed_ticket:'1',produced_ticket:'2'}));
globalThis.installed=installCaptureMode(false,{version:1,mode_epoch:1,active:0,processed_ticket:'2',produced_ticket:'2'});
globalThis.oldEffective=nativeAutoEffective.mode;
active=1;globalThis.activeRejected=rejected(()=>installCaptureMode(true,{version:1,mode_epoch:2,active:0,processed_ticket:'2',produced_ticket:'2'}));active=0;
globalThis.regressedRejected=rejected(()=>installCaptureMode(true,{version:1,mode_epoch:2,active:0,processed_ticket:'1',produced_ticket:'1'}));
globalThis.duplicateEpochRejected=rejected(()=>installCaptureMode(true,{version:1,mode_epoch:1,active:0,processed_ticket:'3',produced_ticket:'3'}));
arkdpsNativeAsyncCapture=true;nativeCapturePlan=()=>({});__arkdpsReplayActive=()=>true;__arkdpsReplayRecord=()=>({facts:{mode_epoch:0}});__arkdpsReplayGap=reason=>{globalThis.gap=reason;throw Error(reason);};__arkdpsReplayPoint=(_role,body)=>body();
const callback={arkdpsName:'Entity.ApplyModifier',onEnter(){globalThis.ran=true;}};prepareNativeCaptureHook({toString:()=> '0x1'},callback);
globalThis.oldRecordRejected=rejected(()=>callback.onEnter());
__arkdpsReplayRecord=()=>({facts:{mode_epoch:1}});callback.onEnter();
`,context);
assert.equal(context.pendingRejected,true);assert.equal(context.installed.mode_epoch,1);assert.equal(context.installed.desired_mode,'local');assert.equal(context.oldEffective,'all');
for(const key of ['activeRejected','regressedRejected','duplicateEpochRejected','oldRecordRejected'])assert.equal(context[key],true,key);
assert.equal(context.ran,true);assert.equal(context.gap,'captured mode epoch mismatch');
console.log('PASS_CAPTURE_MODE_BARRIER desired preference/control ticket/epoch pin; no live access');
