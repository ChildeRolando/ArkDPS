'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),crypto=require('node:crypto');
const root=process.env.ARKDPS_PROBE_FIXTURE_ROOT||path.join(__dirname,'..');
const files=['event_probe_store.js','native_pc/native_event_runtime/publication.js'];
const context=vm.createContext({});
vm.runInContext(files.map(f=>fs.readFileSync(path.join(root,f),'utf8')).join('\n')+
 '\nglobalThis.Store=ArkdpsEventStore;globalThis.Journal=ArkdpsPublicationJournal;',context);
const cases=[];
function record(n){return {version:1,edge:'leave',ticket:String(n),activity_begin:String(n*2),activity_end:String(n*2+1),closed_after:true,active_after:0};}
function pair(options={},eventCount=1){
 const store=new context.Store();for(let i=1;i<=eventCount;i++)store.append({sequence:i,amount:40,payload:'x'.repeat(80)});
 return {store,j:new context.Journal({...options,store:store.view()})};
}
function projection({store,j},extra={}){
 const {events,hp_context}=store.snapshot(j.publishedThrough,store.sequence);
 return {mark:{sequence:store.sequence,epoch:1,frame:1,game_time:1/30,active:0,active_hooks:[],error:null,stopped:false},
  acknowledged:store.acknowledged,events_after:j.publishedThrough,events_through:store.sequence,
  events,hp_context,read_fields:{frame:1,...extra},terminal_read:null};
}
function publish(p,n=1,extra={}){return p.j.publish(record(n),projection(p,extra));}
{
 const p=pair({maxMarks:2}),a=publish(p,1);publish(p,2);publish(p,3);
 const expired=p.j.read({operation:'read',publication_token:a.native_publication.token,after:0,through:1},()=>assert.fail('expired token served'));
 assert.equal(expired.mark_retry_reason,'native_publication_token_expired');assert.equal(p.j.failure,null);assert.equal(p.j.marks.length,2);
 assert.equal(p.store.acknowledged,0);const m=p.j.mark();
 const read=p.j.read({operation:'read',publication_token:m.native_publication.token,after:0,through:1},n=>p.store.served(n));
 assert.equal(read.events[0].amount,40);assert.equal(p.j.ack(1,n=>p.store.ack(n)).acknowledged,1);
 cases.push({case:'maxmarks_expiry_is_retry_not_fatal',marks:p.j.marks.length,acknowledged:p.store.acknowledged});
}
const probe=pair();publish(probe);const oneCost=probe.j.metadataBytes,retained=probe.store.retainedBytes;
{
 const p=pair({maxBytes:retained+Math.floor(oneCost*1.5)});
 for(let i=1;i<=30;i++){const result=publish(p,i);assert.equal(result.error,null);assert.ok(p.j.bytes<=p.j.maxBytes);}
 assert.equal(p.j.failure,null);assert.equal(p.j.marks.length,1);assert.equal(p.store.acknowledged,0);
 cases.push({case:'old_metadata_evicted_before_budget_rejection',metadata_bytes:p.j.metadataBytes,retained_bytes:p.store.retainedBytes,max_bytes:p.j.maxBytes,marks:p.j.marks.length});
}
{
 const p=pair({maxBytes:retained+oneCost-1});const result=publish(p);
 assert.equal(result.native_publication_failure,true);assert.equal(result.native_publication_diagnostic.reason_code,19);
 assert.equal(result.native_publication_diagnostic.store_retained_bytes,retained);assert.equal(result.native_publication_diagnostic.max_bytes,retained+oneCost-1);
 assert.equal(result.native_publication_diagnostic.acknowledged,0);assert.equal(p.j.publishedThrough,0);assert.equal(p.store.sequence,1);
 const first=JSON.stringify(result.native_publication_diagnostic);p.j.fail('publication event sequence gap');
 assert.equal(JSON.stringify(p.j.failed().native_publication_diagnostic),first);
 assert.ok(p.j.ack(1,()=>assert.fail('fatal must not ACK')).error);
 cases.push({case:'canonical_plus_minimum_certificate_over_budget_is_fatal',diagnostic:result.native_publication_diagnostic});
}
{
 const p=pair({maxEvents:1},2),result=publish(p);
 assert.equal(result.native_publication_diagnostic.reason_code,19);assert.equal(result.native_publication_diagnostic.store_event_count,2);
 assert.equal(result.native_publication_diagnostic.max_events,1);
 cases.push({case:'event_count_over_budget_is_fatal',diagnostic:result.native_publication_diagnostic});
}
{
 const p=pair({maxBytes:1000}),result=publish(p,1,{payload:'x'.repeat(1000)});
 assert.equal(result.native_publication_diagnostic.reason_code,18);assert.equal(p.store.readThrough,0);
 cases.push({case:'single_owned_metadata_value_over_budget',diagnostic:result.native_publication_diagnostic});
}
{
 const store=new context.Store({maxBytes:100});assert.throws(()=>store.append({sequence:1,payload:'x'.repeat(100)}),/event store byte budget exceeded/);
 assert.equal(store.sequence,0);assert.equal(store.retainedBytes,0);
 cases.push({case:'canonical_byte_budget_is_distinct_append_failure',sequence:store.sequence,retained_bytes:store.retainedBytes});
}
{
 const p=pair();p.j.fail('C:/private/player-secret');
 assert.equal(p.j.failureDiagnostic.reason_code,255);assert.ok(!JSON.stringify(p.j.failureDiagnostic).includes('private'));
 cases.push({case:'unknown_error_text_absent_from_numeric_diagnostic',reason_code:255});
}
process.stdout.write(JSON.stringify({cases,source_sha256:Object.fromEntries(files.map(f=>[f,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,f))).digest('hex')])),live_reads:0,live_calls:0})+'\n');
