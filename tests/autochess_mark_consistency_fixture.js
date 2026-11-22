// Owned-memory race through the real mark -> nativeBinding -> event stamp.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {world}=require('./native_pc_autochess_context_fixture.js');
const plain=value=>JSON.parse(JSON.stringify(value));

function verifyPostClosingStampConsistency(){
 const cases=[];
 for(const platform of ['native_pc','mumu'])for(const transition of ['phase','round']){
  const f=world({platform,fixtureOptions:{globals:{arkdpsAutoChessMode:'local'}}});
  const first=f.mark(),oldContext=plain(first.native_autochess_context);
  assert.equal(oldContext.status,'available');assert.equal(oldContext.phase,4);
  f.context.record('char_shared','damage_settlement',40,f.context.time(),{method:'owned proof'},
   {target:'enemy_owned',target_uid:'77',target_instance:'0xbeef',damage_type:1,settlement_complete:true},101);
  const original=plain(vm.runInContext('queue',f.context)),stamp=f.context.damageSettlementStamp;
  f.adapter.setAutoChessMode(true);let calls=0,observed=null;
  f.context.damageSettlementStamp=function(frame,time){
   // markEvents' first clock comes before the full body. The second stamp
   // happens after that body's closing guard, at the same frame and time.
   if(++calls===2){
    if(transition==='phase')f.write(f.machine.add(0x5c),5);
    else f.write(f.center.add(0x3c),2);
   }
   const result=stamp(frame,time);
   if(calls===2)observed=plain(result.native_autochess_scope);
   return result;
  };
  let rejected;try{rejected=f.mark();}finally{f.context.damageSettlementStamp=stamp;}
  assert.equal(calls,2);assert.equal(observed.status,'available');
  assert.equal(rejected.retry,true,'the older full body cannot overwrite a later verified scope');
  assert.equal(rejected.native_autochess_context,undefined);
  assert.equal(vm.runInContext('nativeAutoBodyCache',f.context),null);
  assert.equal(vm.runInContext('nativeAutoCache',f.context),null);
  assert.equal(vm.runInContext('nativeAutoScope.phase',f.context),observed.phase);
  assert.equal(vm.runInContext('nativeAutoScope.round',f.context),observed.round);
  assert.equal(vm.runInContext('nativeAutoEpoch',f.context),Number(observed.scope_key.split(':').at(-1)));
  assert.equal(vm.runInContext('sequence',f.context),1);
  assert.equal(vm.runInContext('acknowledgedSequence',f.context),0);
  assert.deepEqual(plain(vm.runInContext('queue',f.context)),original);
  if(platform==='mumu'){
   assert.equal(rejected.autochess_binding_diagnostic.step,'clock_stamp');
   assert.match(rejected.autochess_binding_diagnostic.reason,/verified event stamp changed/);
  }
  const next=f.mark(),context=next.native_autochess_context;
  assert.equal(context.status,'available');assert.equal(context.scope_key,observed.scope_key,
   'retry must not manufacture another epoch by moving back to the old body');
  assert.equal(context.phase,transition==='phase'?5:4);assert.equal(context.round,transition==='round'?2:1);
  assert.equal(context.collection_mode,platform==='mumu'&&transition==='round'?'local':'all');
  assert.equal(oldContext.collection_mode,'local');
  const batch=f.context.rpc.exports.readEvents(0,1);
  assert.deepEqual(plain(batch.events),original,'old records retain their original scope and amount');
  assert.equal(f.context.rpc.exports.ackEvents(1).acknowledged,1);
  cases.push({platform,transition,retry:true,recovered:true,old_scope_unchanged:true,sequence:1,acknowledged:1});
 }
 return cases;
}
module.exports={verifyPostClosingStampConsistency};
if(require.main===module)process.stdout.write(JSON.stringify({status:'verified',cases:verifyPostClosingStampConsistency()}));
