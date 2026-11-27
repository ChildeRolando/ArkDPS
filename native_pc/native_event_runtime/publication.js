// One worker owns this journal. Game callbacks only own native capture lanes.
// Publishing a closed prefix never acknowledges it or reads live game memory.
'use strict';
const __arkdpsConsumeSamplingPublication=typeof __arkdpsConsumeSamplingCheckpoint==='function'?__arkdpsConsumeSamplingCheckpoint:null;
// These finite branch numbers describe rejection, never error text or payloads.
const __arkdpsPublicationFailureCodes=Object.freeze({
 'sampling publication lacks native authority':1,'publication lacks a captured closed leave':2,
 'invalid raw capture ticket':3,'raw capture ticket overflow':4,
 'publication raw prefix regressed or remained open':5,'native publication producer failed or stopped':6,
 'invalid closed publication projection':7,'publication event prefix is incomplete':8,
 'publication event epoch regressed':9,'publication event store unavailable':10,
 'publication does not reference canonical events':11,'publication does not reference canonical contexts':12,
 'publication event sequence gap':13,'invalid publication context sequence':14,
 'invalid terminal publication endpoint':15,'terminal publication changed its final fence':16,
 'terminal publication changed its native binding':17,'publication value exceeds byte budget':18,
 'native publication retention budget exceeded':19,'native publication retained prefix is incomplete':20,
 'non-finite normalized value':21,'invalid normalized value':22});
class ArkdpsPublicationJournal {
 constructor(options={}){
  this.maxEvents=options.maxEvents??100000;
  this.maxBytes=options.maxBytes??64*1024*1024;
  this.maxMarks=options.maxMarks??256;
  for(const n of [this.maxEvents,this.maxBytes,this.maxMarks])
   if(!Number.isSafeInteger(n)||n<=0)throw new Error('invalid publication budget');
  this.store=null;this.marks=[];this.metadataBytes=0;this.publishedThrough=0;
  if(options.store)this.bindStore(options.store);
  this.rawClosed=0n;this.samplingSerial=0n;this.serial=0;this.failure=null;this.failureDiagnostic=null;this.latest=null;
 }
 bindStore(store){
  if(this.store){if(this.store!==store)throw Error('publication event store already bound');return;}
  if(!store||store.version!==1||typeof store.state!=='function'||typeof store.snapshot!=='function'||typeof store.served!=='function')
   throw Error('publication event store unavailable');
  const state=store.state();
  if(!this.integer(state.acknowledged)||!this.integer(state.readThrough)||!this.integer(state.sequence)
     ||state.acknowledged>state.readThrough||state.readThrough>state.sequence||!this.integer(state.retainedBytes))
   throw Error('invalid publication event store');
  this.store=store;this.publishedThrough=state.acknowledged;
 }
 get acknowledged(){return this.store?.state().acknowledged??0;}
 get readThrough(){return this.store?.state().readThrough??0;}
 get events(){return this.store?this.store.snapshot(this.acknowledged,Math.max(this.acknowledged,this.publishedThrough)).events:[];}
 get contexts(){return this.store?this.store.snapshot(this.acknowledged,Math.max(this.acknowledged,this.publishedThrough)).hp_context:[];}
 get bytes(){return this.metadataBytes+(this.store?.state().retainedBytes??0);}
 integer(value){return Number.isSafeInteger(value)&&value>=0;}
 raw(value){
  if(typeof value!=='string'||!/^(0|[1-9][0-9]{0,19})$/.test(value))throw new Error('invalid raw capture ticket');
  const n=BigInt(value);if(n>0xffffffffffffffffn)throw new Error('raw capture ticket overflow');return n;
 }
 own(value){
  const owned=ownProbeValue(value);
  if(owned.bytes>this.maxBytes)throw Error('publication value exceeds byte budget');
  return owned;
 }
 retry(reason){return {retry:true,error:null,stopped:false,events:[],hp_context:[],
  mark_retry_reason:reason,native_publication_version:1};}
 failed(){return {retry:false,error:this.failure,stopped:false,events:[],hp_context:[],native_publication_failure:true,
  native_publication_diagnostic:this.failureDiagnostic};}
 fail(reason){
  // Preserve the first failure. Diagnostic reads cannot replace it or ACK data.
  if(this.failure)return this.failed();
  this.failure=String(reason);
  const d={version:1,failure_code:9,reason_code:Object.prototype.hasOwnProperty.call(__arkdpsPublicationFailureCodes,this.failure)
    ?__arkdpsPublicationFailureCodes[this.failure]:255,
   store_state_available:false,acknowledged:0,read_through:0,store_sequence:0,store_event_count:0,store_retained_bytes:0,
   published_through:this.publishedThrough,marks_count:this.marks.length,metadata_bytes:this.metadataBytes,
   max_events:this.maxEvents,max_bytes:this.maxBytes,max_marks:this.maxMarks};
  try{
   const s=this.store?.state();
   if(s&&['acknowledged','readThrough','sequence','eventCount','retainedBytes'].every(k=>this.integer(s[k]))){
    Object.assign(d,{store_state_available:true,acknowledged:s.acknowledged,read_through:s.readThrough,
     store_sequence:s.sequence,store_event_count:s.eventCount,store_retained_bytes:s.retainedBytes});
   }
  }catch(_){} // A broken store must not mask the original rejection.
  this.failureDiagnostic=Object.freeze(d);return this.failed();
 }
 terminalVerified(mark,terminal,acknowledged){
  if(terminal==null)return false;
  const boundary=terminal.terminal_boundary;
  if(terminal.terminal_only!==true||terminal.terminal_read_version!==1||terminal.retry!==false
     ||terminal.error||terminal.stopped||terminal.active!==0||!Array.isArray(terminal.active_hooks)
     ||terminal.active_hooks.length!==0||terminal.after!==acknowledged||terminal.through!==mark.sequence
     ||!boundary||!mark.terminal_boundary||JSON.stringify(boundary)!==JSON.stringify(mark.terminal_boundary)
     ||!Array.isArray(terminal.events)||terminal.events.length!==mark.sequence-acknowledged
     ||terminal.events.some((event,i)=>event.sequence!==acknowledged+i+1))
   throw new Error('invalid terminal publication endpoint');
  for(const key of ['sequence','epoch','frame','game_time','damage_settlement_scope'])
   if(boundary[key]!==mark[key]||terminal[key]!==mark[key])throw new Error('terminal publication changed its final fence');
  for(const key of Object.keys(mark))if(key.startsWith('native_')&&key!=='native_publication'
     &&JSON.stringify(terminal[key])!==JSON.stringify(mark[key]))
   throw new Error('terminal publication changed its native binding');
  return true;
 }
 publish(record,projection,receiptOnly=false){
  if(this.failure)return this.failed();
  try{return this.publishChecked(record,projection,receiptOnly);}
  catch(error){return this.fail(error.message||error);}
 }
 publishChecked(record,p,receiptOnly=false){
  const sampling=record?.kind==='sampling_checkpoint';
  if(sampling&&(__arkdpsConsumeSamplingPublication===null||!__arkdpsConsumeSamplingPublication(record)))
   throw new Error('sampling publication lacks native authority');
  if(record?.version!==1||(sampling?record.edge!=='checkpoint':record.edge!=='leave')||record.closed_after!==true||record.active_after!==0)
   throw new Error('publication lacks a captured closed leave');
  const ticket=this.raw(record.ticket),begin=this.raw(record.activity_begin),end=this.raw(record.activity_end);
  const sample=sampling?this.raw(record.sampling_serial):0n;
  if(sampling?(ticket<this.rawClosed||end!==begin||sample<=this.samplingSerial):(ticket<=this.rawClosed||end!==begin+1n))
   throw new Error('publication raw prefix regressed or remained open');
  if(!p||p.mark?.retry)return this.retry('native_publication_not_ready');
  if(p.mark?.error||p.mark?.stopped)throw new Error('native publication producer failed or stopped');
  const m=p.mark;
  if(!this.integer(m.sequence)||!this.integer(m.epoch)||!this.integer(m.frame)
     ||m.active!==0||!Array.isArray(m.active_hooks)||m.active_hooks.length!==0
     ||typeof m.game_time!=='number'||!Number.isFinite(m.game_time)||m.game_time<0
     ||!this.integer(p.acknowledged)||p.acknowledged!==this.acknowledged
     ||!this.integer(p.events_after)||p.events_after!==this.publishedThrough||p.events_after<p.acknowledged
     ||p.events_through!==m.sequence
     ||!Array.isArray(p.events)||!Array.isArray(p.hp_context)||!p.read_fields||typeof p.read_fields!=='object')
   throw new Error('invalid closed publication projection');
  if(m.sequence<this.publishedThrough||p.events.length!==m.sequence-p.events_after)
   throw new Error('publication event prefix is incomplete');
  if(this.latest&&m.epoch<this.latest.mark.epoch)throw new Error('publication event epoch regressed');
  if(!this.store)throw Error('publication event store unavailable');
  const retained=this.store.snapshot(this.publishedThrough,m.sequence);
  if(retained.events.length!==p.events.length||retained.events.some((event,i)=>event!==p.events[i]||!Object.isFrozen(event)))
   throw Error('publication does not reference canonical events');
  if(retained.hp_context.length!==p.hp_context.length||retained.hp_context.some((item,i)=>item!==p.hp_context[i]))
   throw Error('publication does not reference canonical contexts');
  let cost=0;
  for(let seq=this.publishedThrough+1;seq<=m.sequence;seq++){
   const event=p.events[seq-p.events_after-1];
   if(event?.sequence!==seq)throw new Error('publication event sequence gap');

  }
  // Repeated publications may have the same event sequence. Late diagnostic
  // contexts must be assigned to a future sequence by the shared producer.
  for(const item of p.hp_context){
   if(!this.integer(item?.eventSequence))throw new Error('invalid publication context sequence');
   if(item.eventSequence<=this.publishedThrough)continue;

  }
  const terminal_verified=this.terminalVerified(m,p.terminal_read,p.acknowledged);
  const token=ticket.toString()+':'+(this.serial+1);
  const fence={version:1,token,closed_ticket:ticket.toString(),activity_begin:begin.toString(),
   activity_end:end.toString(),sequence:m.sequence,epoch:m.epoch,terminal_verified,
   ...(sampling?{kind:'sampling_checkpoint',sampling_serial:sample.toString(),sample_request:record.publication_request}:{})};
  const mark=this.own({...m,native_publication:fence}),fields=this.own(p.read_fields);
  // The source store owns normalized events and ACK. Retain only certificates.
  // Terminal metadata must not duplicate the entire unacknowledged prefix.
  const terminal=p.terminal_read==null?null:this.own({...p.terminal_read,events:[],hp_context:[]});
  cost+=mark.bytes+fields.bytes+(terminal?.bytes??0);
  let retire=Math.max(0,this.marks.length-this.maxMarks+1),evicted=0;
  for(let i=0;i<retire;i++)evicted+=this.marks[i].bytes;
  while(this.bytes+cost-evicted>this.maxBytes&&retire<this.marks.length)
   evicted+=this.marks[retire++].bytes;
  if(this.store.state().eventCount>this.maxEvents||this.bytes+cost-evicted>this.maxBytes)
   throw new Error('native publication retention budget exceeded');
  const published={token,mark:mark.value,fields:fields.value,terminal:terminal?.value??null,
   bytes:mark.bytes+fields.bytes+(terminal?.bytes??0)};
  this.metadataBytes+=cost;
  this.marks.push(published);
  for(let i=0;i<retire;i++){const old=this.marks.shift();this.metadataBytes-=old.bytes;}
  this.latest=published;this.rawClosed=ticket;if(sampling)this.samplingSerial=sample;this.publishedThrough=m.sequence;this.serial++;
  // The native worker only needs this bounded certificate. It never serves
  // this return value to a host: mark/read still use owned journal snapshots.
  if(receiptOnly)return {retry:false,error:null,native_publication:fence,
   native_capture_scope_mode_epoch:m.native_capture_scope_mode_epoch};
  return this.copy(published.mark);
 }
 copy(value){return JSON.parse(JSON.stringify(value));}
 select(token){
  if(typeof token!=='string')return null;
  return this.marks.find(mark=>mark.token===token)??null;
 }
 mark(){return this.failure?this.failed():this.latest?this.copy(this.latest.mark):this.retry('native_publication_not_ready');}
 read(command,served){
  if(this.failure)return this.failed();
  const pub=this.select(command.publication_token),after=command.after,through=command.through;
  if(!pub)return this.retry('native_publication_token_expired');
  if(!this.integer(after)||!this.integer(through)||after<this.acknowledged||after>through||through>pub.mark.sequence)
   return this.retry('native_publication_range_unavailable');
  // Only an explicit empty baseline may establish a new initial cursor.
  // Other reads must extend or replay the contiguous prefix actually served.
  if(after>this.readThrough&&!(command.baseline===true&&after===through))
   return this.retry('native_publication_unserved_prefix');
  const terminal=command.operation==='terminal_read';
  if(terminal&&(!pub.terminal||through!==pub.mark.sequence||pub.terminal.retry||pub.terminal.error
     ||pub.terminal.terminal_boundary?.sequence!==through))return this.retry('native_terminal_publication_not_ready');
  const retained=this.store.snapshot(after,through),entries=retained.events;
  if(entries.length!==through-after||entries.some((entry,i)=>entry.sequence!==after+i+1))
   return this.fail('native publication retained prefix is incomplete');
  const base=terminal?pub.terminal:pub.fields;
  const result=this.copy(base);
  Object.assign(result,{error:null,retry:false,stopped:false,active:0,active_hooks:[],after,through,
   events:entries.map(entry=>this.copy(entry)),hp_context:retained.hp_context.map(entry=>this.copy(entry.context)),native_publication:this.copy(pub.mark.native_publication)});
  if(terminal)Object.assign(result,{terminal_only:true,terminal_read_version:1});
  // Construct the entire owned reply before advancing the producer's served
  // watermark. A publication alone can never authorize ACK.
  if(typeof served!=='function')throw new Error('missing publication served callback');
  served(through);
  if(this.store.state().readThrough<through)throw Error('publication served owner did not commit');
  return result;
 }
 ack(sequence,commit){
  if(this.failure)return {acknowledged:this.acknowledged,error:this.failure};
  if(!this.integer(sequence)||sequence>this.readThrough)
   return {acknowledged:this.acknowledged,error:'event acknowledgement exceeds the served publication watermark'};
  if(sequence<=this.acknowledged)return {acknowledged:this.acknowledged,error:null};
  if(typeof commit!=='function')throw new Error('missing publication acknowledgement callback');
  const result=commit(sequence);
  if(result?.error||result?.acknowledged!==sequence)return result;
  if(this.acknowledged!==sequence)return {acknowledged:this.acknowledged,error:'event store acknowledgement was not committed'};
  return {acknowledged:this.acknowledged,error:null};
 }
}
const __arkdpsPublicationJournal=new ArkdpsPublicationJournal();
function __arkdpsPublishClosedRecord(record,projection,receiptOnly=false){return __arkdpsPublicationJournal.publish(record,projection,receiptOnly);}
function __arkdpsPublicationRequest(command){
 switch(command.operation){
  case 'mark':{
   if(command.sampling==='receipt'){
    const pub=__arkdpsPublicationJournal.select(command.publication_token);
    return pub?__arkdpsPublicationJournal.copy(pub.mark):__arkdpsPublicationJournal.retry('native_publication_token_expired');
   }
   return __arkdpsPublicationJournal.mark();
  }
  case 'read':case 'terminal_read':return __arkdpsPublicationJournal.read(command,__arkdpsPublicationServed);
  case 'ack':return __arkdpsPublicationJournal.ack(command.sequence,sequence=>rpc.exports.ackEvents(sequence));
  default:throw new Error('unknown native publication request');
 }
}
