"""Explicit self-owned native async engine acceptance; never attaches to a game."""
from pathlib import Path
import json,time,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from native_pc.native_event_runtime.smoke import Fixture,fixture_source,FULL_ARGUMENT_VALUES
from native_pc.native_hook_source import NativePipe
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--binary',type=Path,required=True)
parser.add_argument('--receipt',type=Path,required=True)
options=parser.parse_args()
binary=options.binary.resolve()
source=fixture_source(binary,slow=True)
source=source.replace("hooks.push(Interceptor.attach(base.add(offset), {",r"""
 const address=base.add(offset),argCount={Integer:12,FullArguments:16,Sret:3,Nested:1,Float:0,Slow:1}[name];
 const fields=name==='Sret'?[{field_id:1,phase:'leave',role:'leave',root:'return_value',root_index:0,pointer_offsets:[],offset:0,bytes:32,kind:'bytes',guard_id:0,guard_side:'none',required:true}]:[];
 const contract={schema_version:1,complete:true,name,address:address.toString(),abi:'win64-gp',profile:'owned',arg_count:argCount,arg_mask:(1<<argCount)-1,globals:[],fields,max_bytes:65536,max_reads:4096,required_projections:[]};
 hooks.push(Interceptor.attach(address, {arkdpsCaptureContract:contract,
""")
source=source.replace("if(name==='Slow')activity.writeU64(1);",'').replace("if(name==='Slow')activity.writeU64(0);",'')
source=source.replace('this.serial = ++serial;',"this.serial = ++serial; if(serial===1){const until=Date.now()+400;while(Date.now()<until){}}")
source=source.replace("if(name==='Sret')record.values=", "if(name==='Sret')__arkdpsReplayRole('leave',0,1); if(name==='Sret')record.values=")
source += r"""
let lastClosedRecord=null,ownedAcknowledged=0,servedThrough=0;
globalThis.__arkdpsPublicationServed=through=>{servedThrough=Math.max(servedThrough,through);};
const originalAck=rpc.exports.ackEvents;
rpc.exports.ackEvents=sequence=>{const answer=originalAck(sequence);ownedAcknowledged=answer.acknowledged;return answer;};
globalThis.__arkdpsOnClosedRecord=record=>{
 if(record.edge!=='leave'||record.closed_after!==true||record.active_after!==0
    ||BigInt(record.activity_end)!==BigInt(record.activity_begin)+1n
    ||typeof record.ticket!=='string'||typeof record.thread_birth!=='string')
  throw new Error('invalid owned closed record fence');
 lastClosedRecord=Object.freeze({ticket:record.ticket,activity_begin:record.activity_begin,
  activity_end:record.activity_end,active_after:record.active_after,closed_after:record.closed_after});
 const events=records.map((record,index)=>({...record,sequence:index+1}));
 return Object.freeze({mark:{sequence:records.length,epoch:serial,active:0,active_hooks:[],
  frame:records.length,game_time:records.length/30,error:null,retry:false,stopped:false,
  records:records.slice(),retained_arguments:retainedArgumentProof(),owned_closed_record:lastClosedRecord},
  acknowledged:ownedAcknowledged,events:events.filter(e=>e.sequence>ownedAcknowledged),
  hp_context:[],read_fields:{error:null,stopped:false},terminal_read:null});
};
// The production engine must route these through the journal, never back here.
rpc.exports.mark=()=>{throw new Error('legacy live mark was called');};
rpc.exports.readEvents=()=>{throw new Error('legacy live read was called');};
"""
from threading import Thread
from queue import Queue

def fresh_mark(pipe,fixture):
 result=Queue()
 def request():
  try: result.put(pipe.request('mark'))
  except BaseException as error: result.put(error)
 pending=Thread(target=request)
 pending.start()
 time.sleep(.01)
 fixture.command('full-arguments')
 pending.join(timeout=3)
 assert not pending.is_alive(),'fresh mark did not finish'
 answer=result.get_nowait()
 if isinstance(answer,BaseException): raise answer
 return answer
receipt={}
with tempfile.TemporaryDirectory(prefix='arkdps-async-owned-') as temp:
 f=Fixture(Path(temp)/'valid',binary); pipe=None
 try:
  f.configure(source=source); loaded=f.command('load'); pipe=NativePipe(f.pipe_name,f.identity['pid'])
  receipt['identity']=f.identity; receipt['ready']=pipe.request('initialize',token=f.token)
  pipe.request('mark',native_timing={'version':1,'enabled':True})
  start=time.perf_counter(); result=f.command('call'); duration=time.perf_counter()-start
  assert duration<.25,(duration,result)
  receipt['call_duration_seconds']=duration; receipt['abi']=result
  receipt['full_arguments_call']=f.command('full-arguments')
  assert result['integer']==650 and result['floating']==62.5 and result['sret']==[17,29,46,12] and result['nested']==687,result
  mark=fresh_mark(pipe,f)
  assert not mark.get('retry'),mark
  receipt['mark']=mark
  assert mark['native_async_capture']['failure_code']==0 and mark['native_async_capture']['closed_ticket']>0,mark
  assert mark['native_async_capture']['publication_ready'] is True,mark
  assert mark['native_async_capture']['queue_age_available'] is True,mark
  assert any(lane['peak_resident_records']>=2 and lane['peak_resident_bytes']>0 for lane in mark['native_async_capture']['lane_metrics']),mark
  assert int(mark['owned_closed_record']['ticket'])==mark['native_async_capture']['published_ticket'],mark
  assert mark['retained_arguments']['values']==list(map(str,FULL_ARGUMENT_VALUES)),mark['retained_arguments']
  assert len(mark['records'])==14,mark['records']
  token=mark['native_publication']['token']
  read=pipe.request('read',publication_token=token,after=0,through=mark['sequence'])
  assert len(read['events'])==mark['sequence'],read
  assert pipe.request('ack',sequence=mark['sequence'])['acknowledged']==mark['sequence']
  receipt['read_ack']=read
  terminal=pipe.request('terminal_read',publication_token=token,after=mark['sequence'],through=mark['sequence']); assert terminal['retry'] and terminal['events']==[],terminal
  receipt['terminal_stage_refusal']=terminal
  f.command('start-threads',function='Slow')
  pending=pipe.request('mark')
  assert pending.get('retry') is True and pending['native_async_capture']['active']>0,pending
  receipt['active_original_mark_retry']=pending
  historical=pipe.request('read',publication_token=token,after=mark['sequence'],through=mark['sequence'])
  assert not historical.get('retry') and historical['events']==[],historical
  receipt['historical_read_during_active_original']=historical
  assert f.command('join-threads')['results']==[523]*4
  assert not fresh_mark(pipe,f).get('retry')
  disabled=pipe.request('mark',native_timing={'version':1,'enabled':False})
  assert disabled['native_async_capture']['queue_age_available'] is False,disabled
  assert all(lane['maximum_capture_to_replay_qpc'] is None for lane in disabled['native_async_capture']['lane_metrics'])
  receipt['timing_disabled']=disabled
  receipt['stop']=pipe.request('stop'); assert receipt['stop']['native_quiet'] is True
  assert f.command('snapshot')['code']==loaded['code'],'original code not restored'
 finally:
  if pipe:pipe.close()
  f.close()
receipt['result']='PASS owned async ABI/slow consumer/timing-enabled/closed prefix/terminal refusal/stop restore'
with tempfile.TemporaryDirectory(prefix='arkdps-async-incomplete-') as temp:
 f=Fixture(Path(temp)/'incomplete',binary); pipe=None
 try:
  f.configure(source=source.replace('complete:true','complete:false'))
  loaded=f.command('load'); pipe=NativePipe(f.pipe_name,f.identity['pid'])
  rejected=False
  try: pipe.request('initialize',token=f.token)
  except Exception: rejected=True
  assert rejected,'incomplete capture plan enabled hooks'
  assert f.command('snapshot')['code']==loaded['code'],'incomplete plan patched original code'
  receipt['incomplete_plan_refused_without_patching']=True
 finally:
  if pipe: pipe.close()
  f.close()
# Calling the C++ primitives directly must still fail closed even if script
# catches the JS exception. Native originals continue; next host reply fails.
receipt['cpp_live_replay_bans']={}
for name,operation in [('read','__read(1n,8)'),('native_call','__nativeCall(1n,[])')]:
 with tempfile.TemporaryDirectory(prefix='arkdps-async-direct-'+name+'-') as temp:
  f=Fixture(Path(temp)/name,binary); pipe=None
  try:
   hostile=source.replace('this.serial = ++serial;', 'try{'+operation+';}catch(ignored){} this.serial = ++serial;')
   f.configure(source=hostile); f.command('load'); pipe=NativePipe(f.pipe_name,f.identity['pid'])
   pipe.request('initialize',token=f.token)
   result=f.command('call'); assert result['integer']==650 and result['nested']==687
   rejected=False
   try: pipe.request('mark')
   except Exception: rejected=True
   assert rejected,'C++ '+name+' bypassed replay isolation'
   receipt['cpp_live_replay_bans'][name]='rejected despite JS catch; original ABI preserved'
  finally:
   if pipe:pipe.close()
   f.close()
options.receipt.parent.mkdir(parents=True,exist_ok=True)
options.receipt.write_text(json.dumps(receipt,indent=2))
print(receipt['result']); print('original call completed in',receipt['call_duration_seconds'],'seconds while worker callback busy400ms')
