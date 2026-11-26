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
  deadline=time.monotonic()+4
  while time.monotonic()<deadline:
   mark=pipe.request('mark')
   if not mark.get('retry') and mark['native_async_capture']['processed_ticket']==mark['native_async_capture']['reserved_ticket']:break
  else:raise AssertionError('owned capture prefix did not drain')
  receipt['mark']=mark
  assert mark['native_async_capture']['failure_code']==0 and mark['native_async_capture']['closed_ticket']>0,mark
  assert mark['retained_arguments']['values']==list(map(str,FULL_ARGUMENT_VALUES)),mark['retained_arguments']
  assert len(mark['records'])==12,mark['records']
  terminal=pipe.request('terminal_read',after=0,through=0); assert terminal['retry'] and terminal['events']==[],terminal
  receipt['terminal_stage_refusal']=terminal
  f.command('start-threads',function='Slow')
  pending=pipe.request('mark')
  assert pending.get('retry') is True and pending['native_async_capture']['active']>0,pending
  receipt['active_original_mark_retry']=pending
  assert f.command('join-threads')['results']==[523]*4
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
options.receipt.parent.mkdir(parents=True,exist_ok=True)
options.receipt.write_text(json.dumps(receipt,indent=2))
print(receipt['result']); print('original call completed in',receipt['call_duration_seconds'],'seconds while worker callback busy400ms')
