'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('event_probe_native_pc.js','utf8');
const context=vm.createContext({});vm.runInContext(source.slice(source.indexOf('function captureModeState('),source.indexOf('const probeAdapter=')),context);
vm.runInContext(source.slice(source.indexOf('function finalizeNativeCapturePlan('),source.indexOf('function nativeCaptureScopeGraph(')),context);
const auto=fs.readFileSync('event_probe_autochess.js','utf8');
vm.runInContext(auto.slice(auto.indexOf('function autoCollectionMode('),auto.indexOf('function autoEventOwner(')),context);
vm.runInContext(`
nativeCaptureModeEpoch=0;nativeAutoDesiredMode='all';nativeCaptureModeHistory=new Map([[0,'all']]);nativeAutoEffective={scope_key:'old',mode:'all'};
nativeCaptureMetadata={};nativeCapturePublications=new Map();active=0;activeHooks=new Map();
__arkdpsReplayActive=()=>false;setAutoChessMode=enabled=>nativeAutoDesiredMode=enabled?'all':'local';
publishNativeCaptureScope=()=>{};publishNativeCapturePublication=()=>{};
function rejected(body){try{body();return false;}catch(e){return true;}}
globalThis.installed=installCaptureMode(false,{version:1,mode_epoch:1});
globalThis.oldEffective=nativeAutoEffective.mode;
globalThis.duplicateEpochRejected=rejected(()=>installCaptureMode(true,{version:1,mode_epoch:1}));
globalThis.skippedEpochRejected=rejected(()=>installCaptureMode(true,{version:1,mode_epoch:3}));
arkdpsNativeAsyncCapture=true;nativeCapturePlan=()=>({});__arkdpsReplayActive=()=>true;__arkdpsReplayRecord=()=>({facts:{mode_epoch:0}});__arkdpsReplayGap=reason=>{globalThis.gap=reason;throw Error(reason);};__arkdpsReplayPoint=(_role,body)=>body();
const callback={arkdpsName:'Entity.ApplyModifier',onEnter(){globalThis.ran=true;globalThis.usedDesired=nativeAutoDesiredMode;}};prepareNativeCaptureHook({toString:()=> '0x1'},callback);
callback.onEnter();globalThis.oldDesired=usedDesired;globalThis.restoredDesired=nativeAutoDesiredMode;
__arkdpsReplayRecord=()=>({facts:{mode_epoch:1}});callback.onEnter();globalThis.newDesired=usedDesired;
probeAdapter={supportsAutoChessAllDefense:true,captureCollectionMode:capturedCollectionMode};
__arkdpsReplayRecord=()=>({facts:{mode_epoch:0,scope_mode_epoch:1}});
nativeAutoEffective={scope_key:'old',mode:'all'};
globalThis.sameScope=withCapturedMode(__arkdpsReplayRecord(),()=>autoCollectionMode('old',4));
globalThis.newScope=withCapturedMode(__arkdpsReplayRecord(),()=>autoCollectionMode('new',4));
globalThis.assistScope=withCapturedMode(__arkdpsReplayRecord(),()=>autoCollectionMode('assist',5));
// Reading a captured preference supplies a value; it must not temporarily
// mutate the surrounding callback's independently selected record mode.
withCapturedMode(__arkdpsReplayRecord(),()=>{
 globalThis.scopeArgument=capturedCollectionMode(desired=>{globalThis.scopeReadAmbient=nativeAutoDesiredMode;return desired;});
 globalThis.scopeReadThrew=rejected(()=>capturedCollectionMode(()=>{throw Error('read failed');}));
 globalThis.scopeAfterThrow=nativeAutoDesiredMode;
});
__arkdpsReplayRecord=()=>({facts:{mode_epoch:0,scope_mode_epoch:99}});
globalThis.latchedUnknownScopeRejected=rejected(()=>autoCollectionMode('assist',5));
__arkdpsReplayRecord=()=>({facts:{mode_epoch:99}});globalThis.unknownEpochRejected=rejected(()=>callback.onEnter());
__arkdpsReplayActive=()=>false;for(let i=2;i<256;i++)installCaptureMode(true,{version:1,mode_epoch:i});globalThis.historyBoundRejected=rejected(()=>installCaptureMode(false,{version:1,mode_epoch:256}));
`,context);
assert.equal(context.installed.mode_epoch,1);assert.equal(context.installed.desired_mode,'local');assert.equal(context.oldEffective,'all');
for(const key of ['duplicateEpochRejected','skippedEpochRejected','unknownEpochRejected','latchedUnknownScopeRejected','scopeReadThrew','historyBoundRejected'])assert.equal(context[key],true,key);
assert.equal(context.scopeArgument,'local');assert.equal(context.scopeReadAmbient,'all');assert.equal(context.scopeAfterThrow,'all');
assert.equal(context.sameScope,'all');assert.equal(context.newScope,'local');assert.equal(context.assistScope,'all');assert.equal(context.ran,true);assert.equal(context.oldDesired,'all');assert.equal(context.newDesired,'local');assert.equal(context.restoredDesired,'local');assert.equal(context.gap,'captured mode epoch unavailable');
console.log('PASS_CAPTURE_MODE_HISTORY entry-pinned old/new epoch + restore/latest and bounded history; no live access');
