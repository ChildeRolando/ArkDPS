"use strict";
// Source-wrapper contract test with real bridge record lifetime. The observation
// factory is a spy; native fact decoding/poison is covered by provider tests.
const vm=require('vm'),assert=require('assert'),path=require('path');
const {fixture}=require('./native_pc_frida_probe_fixture.js'),{ownedFields}=require('./native_pc_owned_field_fixture.js');
const f=fixture({platform:'native_pc',sourcePath:path.resolve('event_probe_native_pc.js'),enemyHpState:true});
f.context.contractMeta={clock:vm.runInContext('clock',f.context),string_class:'0x1000',classes:{Enemy:f.enemyClass.toString()},skill_offsets:{}};
f.context.actor=f.unit('enemy_test',12);
const owned=ownedFields(f,f.context.contractMeta);
vm.runInContext(`
arkdpsNativeAsyncCapture=true;nativeCaptureMetadata=contractMeta;nativeCaptureModeEpoch=0;
nativeCaptureModeHistory.set(0,'all');nativeCaptureModeHistory.set(5,'local');nativeCaptureModeHistory.set(6,'all');
const observed=[];let inside=false;
const inner={arkdpsName:'hp.Entity.SetHpInternalDirectly',onEnter(args){captureNativeHpObservation('entry',args[0]);throw Error('expected nested exception');}};
const outer={arkdpsName:'hp.Entity.SetHpInternal',onEnter(args){
 captureNativeHpObservation('entry',args[0]);
 try{inner.onEnter.call({threadId:1},args);}catch(error){if(error.message!=='expected nested exception')throw error;}
 captureNativeHpObservation('entry',args[0]);
}};
prepareNativeCaptureHook(ptr(0x7000),outer);prepareNativeCaptureHook(ptr(0x8000),inner);
nativeCaptureModeEpoch=6;nativeAutoDesiredMode='all';
globalThis.__arkdpsOwnedHpObservation=(contract,enemy)=>{observed.push({address:contract.address,installed:contract.mode_epoch,current:__arkdpsReplayRecord().facts.mode_epoch,desired:nativeAutoDesiredMode});return null;};
const observationRecord={version:1,edge:'entry',checkpoint:0,facts:{mode_epoch:5,scope_mode_epoch:6},fields:[{field_id:1,role:'body',checkpoint:0,status:'ok'}],segments:[]};
Object.defineProperty(observationRecord,'native_view',{get:()=>__arkdpsReplayActive()});
__arkdpsReplayBegin(observationRecord);outer.onEnter.call({threadId:1},[actor]);
globalThis.summary=__arkdpsReplayEnd();globalThis.observed=observed;
globalThis.contractRestored=nativeActiveHpContract===null;globalThis.latestModeRestored=nativeAutoDesiredMode==='all';
const edgeMismatch={arkdpsName:'hp.Entity.SetHpInternal',onEnter(args){try{captureNativeHpObservation('leave',args[0]);}catch(error){}}};
prepareNativeCaptureHook(ptr(0x9000),edgeMismatch);__arkdpsReplayBegin(observationRecord);edgeMismatch.onEnter.call({threadId:1},[actor]);globalThis.badSummary=__arkdpsReplayEnd();
globalThis.badRestored=nativeActiveHpContract===null;
// Mode-only source input is controlled by the bridge seam here. It must choose
// actual item mode5 rather than latest6, without forcing public Record/Facts.
const modeProbe={arkdpsName:'hp.Entity.SetHpInternal',arkdpsDirectHpEntry(){globalThis.directDesired=nativeAutoDesiredMode;},arkdpsDirectHpLeave(){}};
prepareNativeCaptureHook(ptr(0xa000),modeProbe);const token=Object.freeze({}),oldVerifier=__arkdpsIsPreparedHpObservation,oldRecordGetter=__arkdpsReplayRecord;
__arkdpsIsPreparedHpObservation=(io,contract)=>io===token&&contract===modeProbe.arkdpsCaptureContract;
__arkdpsReplayModeRecord=()=>Object.freeze({edge:__arkdpsReplay.record.edge,facts:Object.freeze({mode_epoch:__arkdpsReplay.record.facts.mode_epoch})});
__arkdpsReplayBegin(observationRecord);__arkdpsReplayRecord=()=>{throw Error('direct mode forced public record');};
try{modeProbe.arkdpsDirectHpEntry.call({threadId:1},actor,token);}finally{__arkdpsReplayRecord=oldRecordGetter;__arkdpsIsPreparedHpObservation=oldVerifier;globalThis.directModeEnd=__arkdpsReplayEnd();}
globalThis.directModeRestored=nativeAutoDesiredMode==='all';

`,f.context);
assert.equal(f.context.directDesired,'local');assert(f.context.directModeRestored);assert(f.context.directModeEnd.complete);assert.equal(f.context.summary.complete,true);assert(f.context.contractRestored);assert(f.context.latestModeRestored);assert(f.context.badRestored);assert.equal(f.context.badSummary.complete,false);
const seen=JSON.parse(JSON.stringify(f.context.observed));assert.deepEqual(seen.map(x=>x.address),['0x7000','0x8000','0x7000']);assert(seen.every(x=>x.installed===0&&x.current===5));assert.equal(seen[0].desired,'local');assert.equal(seen[1].desired,'local');
assert.equal(owned.late(),0);owned.restore();
console.log('PASS_HP_OBSERVATION_CONTRACT actual record mode5 vs install0; nested exception/edge restores');
