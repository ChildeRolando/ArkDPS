'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../native_pc/native_event_runtime/publication.js'),'utf8');
const context=vm.createContext({});
vm.runInContext(source+'\nglobalThis.Journal=ArkdpsPublicationJournal;',context);
const Journal=context.Journal;
let checks=0;
function check(condition){assert.ok(condition);checks++;}
function plain(value){return JSON.parse(JSON.stringify(value));}
function record(ticket){return {version:1,edge:'leave',ticket:String(ticket),activity_begin:String(ticket*2),
 activity_end:String(ticket*2+1),closed_after:true,active_after:0};}
function projection(sequence,ack=0,epoch=sequence+1){return {
 mark:{sequence,epoch,frame:sequence,game_time:sequence/30,active:0,active_hooks:[],error:null,stopped:false,
  native_generation:1,native_identity:{controller:'0x10000'},native_fixed_time:String(sequence),
  native_autochess_context:{scope_key:'round1',memberships:[{uid:7}]}},
 acknowledged:ack,events:Array.from({length:sequence-ack},(_,i)=>({sequence:ack+i+1,amount:ack+i+1,frame:ack+i+1})),
 hp_context:[],read_fields:{native_generation:1,frame:sequence,game_time:sequence/30,
  baseline_discarded_skill_events_complete:true,autochess_round_start:{sequence:0}},terminal_read:null};}
function read(j,p,after,through,operation='read',served=()=>{}){
 return j.read({operation,publication_token:p.native_publication.token,after,through},served);
}
{
 const j=new Journal(),p=projection(2);p.hp_context=[{eventSequence:2,context:{hp:10}}];
 const m=j.publish(record(1),p);
 check(!m.retry&&m.native_publication.closed_ticket==='1');
 check(j.ack(2,()=>{throw Error('must not commit');}).error!=null);
 // A hostile old mutable reducer buffer cannot alter an already published fact.
 p.events[1].amount=900;p.mark.native_autochess_context.memberships[0].uid=999;p.hp_context[0].context.hp=0;
 const served=[],first=read(j,m,0,2,'read',n=>served.push(n));
 check(first.events[1].amount===2&&first.hp_context[0].hp===10);
 check(j.mark().native_autochess_context.memberships[0].uid===7);
 first.events[0].amount=1000;
 check(read(j,m,0,2).events[0].amount===1);
 check(plain(served).join(',')==='2');
 check(j.ack(3,()=>{throw Error('must not commit');}).error!=null);
 const wrong=j.ack(2,()=>({acknowledged:1,error:'source rejected'}));
 check(wrong.error&&j.acknowledged===0&&j.events.length===2);
 check(j.ack(2,n=>({acknowledged:n,error:null})).acknowledged===2&&j.events.length===0);
 // Cumulative ACK retries must not call the reducer again.
 check(j.ack(2,()=>{throw Error('duplicate ACK');}).acknowledged===2);
 check(read(j,m,0,2).retry===true);
}
{
 const j=new Journal(),a=j.publish(record(1),projection(2));
 // Continuous game activity after a captured closed prefix cannot erase it.
 const open={...record(2),closed_after:false,active_after:4};
 check(j.publish(open,projection(3)).retry===true&&j.failure!=null);
 // A structural producer failure seals the journal rather than ACKing old data.
 check(read(j,a,0,2).retry&&j.ack(0,()=>({acknowledged:0})).error);
}
{
 const j=new Journal(),a=j.publish(record(1),projection(2));
 const b=j.publish(record(3),projection(4));
 check(read(j,a,0,2).events.length===2);
 check(read(j,b,2,4).events.map(x=>x.sequence).join(',')==='3,4');
 check(read(j,a,0,3).retry===true);
 check(j.read({operation:'read',publication_token:'invented',after:0,through:0},()=>{}).retry===true);
 check(j.publishedThrough===4&&j.readThrough===4);
 const baseline=read(j,b,4,4);
 check(baseline.events.length===0&&baseline.autochess_round_start.sequence===0);
 // Same normalized sequence can have later independent clocks/scopes.
 const p=projection(4,0,8);p.mark.frame=10;p.read_fields.frame=10;
 const c=j.publish(record(5),p);
 check(c.native_publication.token!==b.native_publication.token&&read(j,b,4,4).frame===4&&read(j,c,4,4).frame===10);
}
{
 const j=new Journal(),p=projection(2);
 p.terminal_read={retry:false,error:null,terminal_boundary:{sequence:2,scope:'old'},terminal_only:true,frame:2};
 const m=j.publish(record(1),p);
 check(read(j,m,0,1,'terminal_read').retry===true);
 const batch=read(j,m,0,2,'terminal_read');
 check(batch.terminal_only&&batch.terminal_read_version===1&&batch.events.length===2&&batch.terminal_boundary.scope==='old');
 check(j.ack(2,n=>({acknowledged:n,error:null})).acknowledged===2);
}
{
 const j=new Journal({maxMarks:2}),a=j.publish(record(1),projection(0));
 j.publish(record(2),projection(0));j.publish(record(3),projection(0));
 check(read(j,a,0,0).mark_retry_reason==='native_publication_token_expired');
 check(j.marks.length===2);
}
for(const mutate of [
 p=>p.events.splice(0,1),p=>p.events[1].sequence=1,p=>p.acknowledged=1,p=>p.mark.active=1,
 p=>p.mark.epoch=-1,p=>p.mark.game_time=Infinity,p=>p.events[1].amount=NaN,
 p=>p.mark.sequence=3,p=>p.hp_context=[{eventSequence:-1,context:{}}]
]){
 const j=new Journal(),p=projection(2);mutate(p);
 check(j.publish(record(1),p).retry===true&&j.failure!=null&&j.events.length===0&&j.readThrough===0);
}
{
 const j=new Journal({maxEvents:1});
 check(j.publish(record(1),projection(2)).retry&&j.events.length===0);
 const small=new Journal({maxBytes:100});
 check(small.publish(record(1),projection(1)).retry&&small.events.length===0);
 const fenceBudget=new Journal({maxBytes:650});
 check(fenceBudget.publish(record(1),projection(0)).retry&&fenceBudget.bytes===0);
}
{
 const j=new Journal(),m=j.publish(record(1),projection(2));
 check(read(j,m,1,2).mark_retry_reason==='native_publication_unserved_prefix');
 check(j.ack(2,()=>{throw Error('must not skip unserved prefix');}).error!=null&&j.events.length===2);
 check(read(j,m,2,2).retry===true);
 const baseline=j.read({operation:'read',publication_token:m.native_publication.token,after:2,through:2,baseline:true},()=>{});
 check(baseline.events.length===0&&j.readThrough===2);
 check(j.ack(2,n=>({acknowledged:n,error:null})).acknowledged===2);
}
{
 const j=new Journal(),m=j.publish(record(1),projection(1));
 assert.throws(()=>read(j,m,0,1,'read',()=>{throw Error('served failed');}));
 check(j.readThrough===0&&j.ack(1,()=>{throw Error('unexpected commit');}).error!=null);
 // Full unsigned native tickets are strings, never rounded through doubles.
 const wide=record(1);wide.ticket='18446744073709551615';wide.activity_begin='18446744073709551613';wide.activity_end='18446744073709551614';
 const q=new Journal(),published=q.publish(wide,projection(0));
 check(published.native_publication.closed_ticket==='18446744073709551615');
}
process.stdout.write(JSON.stringify({checks,live_reads:0,live_calls:0})+'\n');
