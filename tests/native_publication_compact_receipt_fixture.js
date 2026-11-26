'use strict';
// One bounded publication pair. This isolates duplicate ownership/encoding;
// it is not a production callback throughput or game-FPS benchmark.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {performance}=require('node:perf_hooks');
const c=vm.createContext({});
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/publication.js','utf8')+'\nglobalThis.Journal=ArkdpsPublicationJournal;',c);
const plain=value=>JSON.parse(JSON.stringify(value));
function projection(){
 const memberships=Array.from({length:256},(_,i)=>({data_uid:i+1,cid:'char_owned_'+i,player_index:1+i%4,player_uid:'owned-'+i%4,is_token:false,host_uid:null}));
 const mark={sequence:1000,epoch:2000,frame:30,game_time:1,active:0,active_hooks:[],error:null,stopped:false,
  native_capture_scope_mode_epoch:0,native_autochess_context:{scope_key:'owned',memberships}};
 return {mark,acknowledged:0,events_after:0,events_through:1000,
  events:Array.from({length:1000},(_,i)=>({sequence:i+1,kind:'enemy_hp',amount:1,frame:30,target_uid:String(i%256)})),
  hp_context:[],read_fields:{...mark,enemy_damage_ready:true},terminal_read:null};
}
function run(compact){
 const j=new c.Journal(),p=projection();let returnedCopyCount=0,returnedCopyBytes=0;
 const copy=j.copy;
 j.copy=function(value){returnedCopyCount++;returnedCopyBytes+=JSON.stringify(value).length;return copy.call(this,value);};
 const record={version:1,edge:'leave',ticket:'2000',activity_begin:'3999',activity_end:'4000',closed_after:true,active_after:0};
 const start=performance.now(),receipt=j.publish(record,p,compact),elapsedMs=performance.now()-start;
 const encodedBytes=JSON.stringify(receipt).length;
 const publishCopies=returnedCopyCount,publishCopyBytes=returnedCopyBytes;
 const token=receipt.native_publication.token;
 // Mutating the caller's reducer data, returned receipt or host replies must
 // never modify the retained journal, including compact native publication.
 p.events[0].amount=999;p.mark.native_autochess_context.memberships[0].data_uid=999;
 receipt.native_publication.sequence=999;
 const mark=j.mark();assert.equal(mark.native_publication.sequence,1000);
 assert.equal(mark.native_autochess_context.memberships[0].data_uid,1);
 assert(j.ack(1000,()=>{throw Error('unserved ACK');}).error);
 let served=0;const read=j.read({operation:'read',publication_token:token,after:0,through:1000},n=>served=n);
 assert.equal(read.events[0].amount,1);assert.equal(served,1000);
 assert.equal(j.ack(1000,n=>({acknowledged:n,error:null})).acknowledged,1000);
 return {mark:plain(mark),read:plain(read),publishCopies,publishCopyBytes,encodedBytes,elapsedMs};
}
const full=run(false),compact=run(true);
assert.deepEqual(compact.mark,full.mark);assert.deepEqual(compact.read,full.read);
assert.equal(full.publishCopies,1);assert.equal(compact.publishCopies,0);
assert(compact.encodedBytes<full.encodedBytes/20);
// Host input cannot select native-only compact publication behaviour.
c.__arkdpsPublicationServed=()=>{};c.rpc={exports:{ackEvents:()=>{throw Error('ACK unavailable');}}};
vm.runInContext('globalThis.hostMark=__arkdpsPublicationRequest({operation:"mark",receiptOnly:true})',c);
assert.equal(c.hostMark.retry,true);
const report={classification:'offline bounded journal-only encoding comparison',events:1000,memberships:256,
 full:{copies:full.publishCopies,copyBytes:full.publishCopyBytes,engineEncodedBytes:full.encodedBytes,elapsedMs:full.elapsedMs},
 compact:{copies:compact.publishCopies,copyBytes:compact.publishCopyBytes,engineEncodedBytes:compact.encodedBytes,elapsedMs:compact.elapsedMs},
 identicalHostMarkRead:true,unservedAckRejected:true,callerAndReplyMutationIsolated:true};
process.stdout.write(JSON.stringify(report)+'\n');
