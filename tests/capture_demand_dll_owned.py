"""Delivered native DLL → real shared damage consumers → pipe read/ACK.

Only fresh children, their own fixed-address heap and native helper are used.
No game process, performance timing, install, product mutation or packaging.
"""
from pathlib import Path
from hashlib import sha256
from queue import Queue
from threading import Thread
from tempfile import TemporaryDirectory
import argparse,ctypes,json,os,secrets,shutil,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from native_pc.native_event_runtime.smoke import Fixture
from native_pc.native_hook_source import NativePipe
from capture_demand_dll_source import source

def child(binary,helper):
 from ctypes import wintypes
 k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=wintypes.HANDLE;k.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
 values=[wintypes.FILETIME()for _ in range(4)];assert k.GetProcessTimes(k.GetCurrentProcess(),*[ctypes.byref(x)for x in values]);print(json.dumps({'pid':os.getpid(),'created':values[0].dwHighDateTime<<32|values[0].dwLowDateTime}),flush=True)
 helper_lib=ctypes.WinDLL(str(helper));library=None;owned=None;diagnostic_address=0;route_count=0
 k.VirtualAlloc.argtypes=[ctypes.c_void_p,ctypes.c_size_t,wintypes.DWORD,wintypes.DWORD];k.VirtualAlloc.restype=ctypes.c_void_p
 helper_lib.DemandInstall.argtypes=[ctypes.c_char_p,ctypes.c_size_t,ctypes.c_size_t];helper_lib.DemandInstall.restype=ctypes.c_int;helper_lib.DemandRun.argtypes=[ctypes.c_uint];helper_lib.DemandRun.restype=ctypes.c_int;helper_lib.DemandError.restype=ctypes.c_char_p
 for line in sys.stdin:
  command=json.loads(line);op=command['operation']
  if op=='exit':return
  if op=='load':library=ctypes.WinDLL(str(binary));answer={'loaded':True}
  elif op=='install-world':
   assert owned is None and set(command)=={'operation','world'}
   world=command['world'];ranges=world['ranges'];start=min(int(p['address'],0)for p in ranges)&~65535;end=(max(int(p['address'],0)+len(p['bytes'])for p in ranges)+65535)&~65535
   assert 65536<=start<end and end-start<=64*1024*1024
   owned=k.VirtualAlloc(start,end-start,0x3000,4);assert owned==start,'fixed self-owned heap unavailable'
   diagnostic_address=int(world['diagnostic_address'],0)
   route_count=len(world['routes'])
   for part in ranges:
    at=int(part['address'],0);data=bytes(part['bytes']);assert start<=at<=end-len(data);ctypes.memmove(at,data,len(data))
   recipe=Path(binary).parent/'recipe.json';recipe.write_text(json.dumps({'routes':world['routes']}),encoding='utf-8');assert helper_lib.DemandInstall(str(recipe).encode(),start,end)==0,helper_lib.DemandError()
   answer={'mapped':True,'bytes':end-start,'routes':len(world['routes'])}
  elif op=='diagnostic':
   assert set(command)=={'operation'} and diagnostic_address
   size=ctypes.c_uint32.from_address(diagnostic_address).value;assert size<32000
   answer={'message':ctypes.string_at(diagnostic_address+4,size*2).decode('utf-16-le')}
  elif op=='run-route':
   assert set(command)=={'operation','route'} and owned is not None and type(command['route'])is int and 0<=command['route']<route_count
   result=helper_lib.DemandRun(command['route']);answer={'route':command['route'],'edges':result,'error':helper_lib.DemandError().decode()};assert result>0,answer
  else:raise ValueError('unsupported self-owned command')
  print(json.dumps(answer),flush=True)

class OwnedFixture(Fixture):
 """Reuse common authenticated config/commands/close; choose this owned child."""
 def __init__(self,root,binary,helper):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.binary=self.root/binary.name;shutil.copyfile(binary,self.binary);self.helper=self.root/'CaptureDemandFixture.dll';shutil.copyfile(helper,self.helper)
  self.process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',str(self.binary),str(self.helper)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8');self.lines=Queue()
  def reader():
   for line in self.process.stdout:self.lines.put(line)
   self.lines.put(None)
  Thread(target=reader,daemon=True).start();self.identity=self.receive();self.token=secrets.token_urlsafe(48);self.pipe_name=r'\\.\pipe\ArkDPS.NativeEvents.'+secrets.token_hex(16)

def build_helper(out,compiler):
 out.mkdir(parents=True,exist_ok=True);helper=out/'CaptureDemandFixture.dll';cpp=ROOT/'tests/capture_demand_dll_helper.cpp';deps=ROOT/'runtime/terminal-destroy-prefix/final-build/dependencies'
 cmd=[compiler,'-std=c++17','-shared','-static','-O1','-DNOMINMAX','-I'+str(deps),str(cpp),'-o',str(helper)]
 r=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',env=dict(os.environ,PATH=str(Path(compiler).parent)+';'+os.environ['PATH']),timeout=60);(out/'build.stdout').write_text(r.stdout,encoding='utf-8');(out/'build.stderr').write_text(r.stderr,encoding='utf-8');(out/'build.json').write_text(json.dumps({'command':cmd,'returncode':r.returncode,'source_sha256':sha256(cpp.read_bytes()).hexdigest(),'binary_sha256':sha256(helper.read_bytes()).hexdigest()if helper.exists()else None},indent=2));assert r.returncode==0,r.stderr
 return helper

def wire_challenge(pipe,sequence,bypass_poison=False):
 """Test-only observation. It never turns a failed connection into recovery."""
 rows=[];original=pipe._io;answer=None;failure=None
 def observed(operation,buffer,size,deadline):
  row={'operation':operation,'requested_bytes':size};rows.append(row)
  if operation=='WriteFile':row['request']=ctypes.string_at(buffer,size).decode('utf-8')
  try:
   count=original(operation,buffer,size,deadline);row['transferred_bytes']=count
   if operation=='ReadFile':row['response']=ctypes.string_at(buffer,count).decode('utf-8')
   return count
  except BaseException as error:row.update(error_type=type(error).__name__,error=str(error),os_error=getattr(error,'errno',None));raise
 pipe._io=observed
 if bypass_poison:pipe._poisoned=False
 try:answer=pipe.request('ack',sequence=sequence,timeout=2)
 except BaseException as error:failure={'type':type(error).__name__,'message':str(error)}
 finally:
  pipe._io=original
  if bypass_poison:pipe._poisoned=True
 return {'test_only_poison_bypass':bypass_poison,'io':rows,'answer':answer,'failure':failure,'post_disconnect_acknowledged':None,'post_disconnect_acknowledged_reason':'not queryable after server closes this authenticated pipe'}

def run(binary,helper,source_root,out,missing=False,mismatch=False,early_ack=False):
 out.mkdir(parents=True,exist_ok=True);text=source(source_root,helper,missing,mismatch);(out/'source.js').write_text(text,encoding='utf-8');receipt={'missing':missing,'mismatch':mismatch,'early_ack':early_ack,'source_sha256':sha256(text.encode()).hexdigest(),'product_sha256':{name:sha256((source_root/name).read_bytes()).hexdigest()for name in ['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','event_probe_store.js']}}
 with TemporaryDirectory(prefix='arkdps-damage-dll-')as temp:
  f=OwnedFixture(Path(temp)/'child',binary,helper);pipe=None
  try:
   receipt['identity']=f.identity;f.configure(source=text);f.command('load');pipe=NativePipe(f.pipe_name,f.identity['pid'],timeout=20);hello=pipe.request('initialize',token=f.token);ready=hello['ready'];assert 'fixture_setup_error'not in ready,ready
   (out/'ready.json').write_text(json.dumps(hello,indent=2),encoding='utf-8');world=ready.pop('world');(out/'world.json').write_text(json.dumps(world),encoding='utf-8');receipt['world_sha256']=sha256((out/'world.json').read_bytes()).hexdigest();receipt['setup']=f.command('install-world',world=world)
   receipt['calls']=[]
   for route in ([0]if missing else [6,0]if mismatch else [0,1,2,3,4,5]):receipt['calls'].append(f.command('run-route',route=route))
   if missing:
    try:pipe.request('mark',sampling='fresh',timeout=5)
    except RuntimeError as e:receipt['strict_error']=str(e);assert 'native capture failed: code=6'in str(e),str(e)
    else:raise AssertionError('missing required evidence accepted')
    assert pipe._poisoned
    try:pipe.request('ack',sequence=1)
    except RuntimeError as e:receipt['ack_refusal']=str(e)
    else:raise AssertionError('ACK allowed after structural poison')
    receipt['post_poison_wire_challenge']=wire_challenge(pipe,1,bypass_poison=True)
    challenge=receipt['post_poison_wire_challenge'];assert challenge['failure']and any(row['operation']=='WriteFile'for row in challenge['io']),challenge
   else:
    # Sampling observes current owned heap independently after the real calls;
    # it never fabricates a hook edge or imports business events.
    mark=pipe.request('mark',sampling='fresh',timeout=10);receipt['mark']=mark;assert not mark.get('retry'),mark
    through=mark['sequence'];assert through==ready['initial_sequence']+(2 if mismatch else 6),mark
    assert mark['native_async_capture']['delivered_event_sequence']==0,mark
    receipt['initial_ack']=pipe.request('ack',sequence=0);assert receipt['initial_ack']['acknowledged']==0
    if early_ack:
     receipt['unserved_wire_challenge']=wire_challenge(pipe,through);challenge=receipt['unserved_wire_challenge'];assert challenge['failure']and any(row.get('transferred_bytes',0)>0 and row['operation']=='WriteFile'for row in challenge['io']),challenge
     replies=[json.loads(line)for row in challenge['io']if row['operation']=='ReadFile'for line in row.get('response','').splitlines()if line]
     assert replies and replies[-1].get('id')==pipe._id and replies[-1].get('error'),replies
     receipt['result']='PASS';return receipt
    batch=pipe.request('read',publication_token=mark['native_publication']['token'],after=0,through=through);receipt['read']=batch;events=[e for e in batch['events']if e['sequence']>ready['initial_sequence']];damage=[e for e in events if e['kind']=='damage_settlement'];shield=[e for e in events if e['kind']=='shield_absorbed']
    assert len(damage)==(1 if mismatch else 5)and len(shield)==(0 if mismatch else 1),events
    for event in damage:assert event['settlement_complete']is True and event['nominal_amount']==40 and event['hp_before']==50 and event['mitigation_before']==80 and event['mitigation_after']==40,event
    expected=ready['expected_actors'];assert expected['source']['address']!=expected['target']['address']
    for event in damage:
     assert event['target']==expected['target']['cid']and event['target_instance']==expected['target']['address']and str(event['target_uid'])==str(expected['target']['instance_uid']),event
    assert sorted((e['amount'],e['shield_absorbed'],e['hp_after'])for e in damage)==([(40,0,10)]if mismatch else[(30,10,20)]+[(40,0,10)]*4)
    if mismatch:
     nominal=[e for e in events if e['kind']=='damage'];assert len(nominal)==1 and nominal[0]['amount']==40 and nominal[0].get('settlement_complete')is not True,events
     assert nominal[0]['target']==expected['source']['cid']and nominal[0]['target_instance']==expected['source']['address'],nominal
     assert (nominal[0]['target'],nominal[0]['target_instance'])!=(damage[0]['target'],damage[0]['target_instance'])
     assert all(key not in nominal[0]for key in ['hp_before','hp_after','real_delta','mitigation_before','mitigation_after']),nominal
     assert mark['enemy_damage_unknown']is True and any(x.get('target_commit_rejected')is True for x in mark['damage_settlement_failure_samples']),mark
    else:
     assert shield[0]['amount']==10 and shield[0]['proof']['before']==15 and shield[0]['proof']['after']==5
     diagnostics=mark['mitigation_diagnostics'];assert diagnostics['formula_candidates']==diagnostics['emitted']==5 and diagnostics['factory_candidates']==diagnostics['factory_emitted']==2,diagnostics
    ack=pipe.request('ack',sequence=through);receipt['ack']=ack;assert ack['acknowledged']==through,ack
    counters=mark['native_async_capture'];assert counters['failure_code']==0 and counters['reserved_ticket']==counters['processed_ticket']==counters['closed_ticket']==counters['published_ticket'],counters
    final=pipe.request('mark',sampling='confirm',publication_token=mark['native_publication']['token']);receipt['final']=final;assert final['native_async_capture']['delivered_event_sequence']==through,final
    receipt['final_ack']=pipe.request('ack',sequence=through);assert receipt['final_ack']['acknowledged']==through
   receipt['result']='PASS'
  except BaseException as e:
   receipt.update(result='FAIL',error_type=type(e).__name__,error=str(e))
   if 'setup'in receipt:
    try:receipt['diagnostic']=f.command('diagnostic')
    except BaseException as diagnostic_error:receipt['diagnostic_error']=str(diagnostic_error)
   raise
  finally:
   (out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
   if pipe:pipe.close()
   f.close()
 return receipt

def run_ordinary(binary,helper,source_root,out):
 """First raw-ticket-zero cold sampling; game frame remains explicitly 30."""
 out.mkdir(parents=True,exist_ok=True);text=source(source_root,helper,ordinary=True);(out/'source.js').write_text(text,encoding='utf-8')
 receipt={'case':'ordinary-first-cold-future-graphs-nested-read-ack','source_sha256':sha256(text.encode()).hexdigest(),'product_sha256':{name:sha256((source_root/name).read_bytes()).hexdigest()for name in ['event_probe_native_pc.js','event_probe_runtime.js','event_probe_autochess.js','event_probe_store.js']}}
 with TemporaryDirectory(prefix='arkdps-ordinary-dll-')as temp:
  f=OwnedFixture(Path(temp)/'child',binary,helper);pipe=None
  try:
   receipt['identity']=f.identity;f.configure(source=text);f.command('load');pipe=NativePipe(f.pipe_name,f.identity['pid'],timeout=20);hello=pipe.request('initialize',token=f.token);ready=hello['ready'];assert 'fixture_setup_error'not in ready,ready
   (out/'ready.json').write_text(json.dumps(hello,indent=2),encoding='utf-8');world=ready.pop('world');(out/'world.json').write_text(json.dumps(world),encoding='utf-8');receipt['world_sha256']=sha256((out/'world.json').read_bytes()).hexdigest()
   assert ready['ordinary']and ready['scope_fields']==ready['publication_fields']==ready['setup_graph_publications']==0,ready
   receipt['setup']=f.command('install-world',world=world)
   first=pipe.request('mark',sampling='fresh',timeout=10);receipt['first_mark']=first;receipt['first_audit']=pipe.request('capture_status');assert not first.get('retry'),first
   assert first['frame']==30 and first['sequence']==ready['initial_sequence']and 'native_autochess_context'not in first,first
   counters=first['native_async_capture'];assert counters['reserved_ticket']==counters['processed_ticket']==counters['closed_ticket']==counters['published_ticket']==0,counters
   handoff=receipt['first_audit']['dll_audit'];cold=[r for r in handoff['records']if r['facts'].get('cold_revision')]
   assert cold and all('scope_revision'not in r['facts']and 'publication_revision'not in r['facts']for r in cold),handoff
   assert all(r['kind']=='sampling_checkpoint'and r['ticket']=='0'for r in cold),cold
   graphs=handoff['graphs'];assert any(not g['publication']for g in graphs)and any(g['publication']for g in graphs),handoff
   assert all(g['active']and g['facts'].get('cold_revision')for g in graphs),graphs
   # A separate later sample proves geometry is used only for future records.
   warm=pipe.request('mark',sampling='fresh',timeout=10);receipt['warm_mark']=warm;assert not warm.get('retry'),warm
   receipt['warm_audit']=pipe.request('capture_status');warm_records=receipt['warm_audit']['dll_audit']['records'];assert any(r['facts'].get('scope_revision')and r['facts'].get('publication_revision')and 'cold_revision'not in r['facts']for r in warm_records),warm_records
   receipt['calls']=[f.command('run-route',route=0)]
   mark=pipe.request('mark',sampling='fresh',timeout=10);receipt['mark']=mark;assert not mark.get('retry'),mark
   through=mark['sequence'];batch=pipe.request('read',publication_token=mark['native_publication']['token'],after=0,through=through);receipt['read']=batch
   events=[e for e in batch['events']if e['sequence']>ready['initial_sequence']];damage=[e for e in events if e['kind']=='damage_settlement'];assert len(damage)==2,events
   assert all(e['settlement_complete']is True and e['target']==ready['expected_actors']['target']['cid']and e['target_instance']==ready['expected_actors']['target']['address']for e in damage),damage
   assert sorted((e['damage_type'],e['amount'],e['nominal_amount'])for e in damage)==[(2,40,40),(5,60,60)],damage
   assert all(e.get('native_autochess_scope')is None for e in events),events
   assert any(e['kind']=='elemental_bar'and e['amount']==5 for e in events),events
   normal=next(e for e in damage if e['damage_type']==2);elemental=next(e for e in damage if e['damage_type']==5)
   assert (normal['hp_before'],normal['hp_after'],normal['real_delta'])==(100,60,-40)and (elemental['hp_before'],elemental['hp_after'],elemental['real_delta'])==(60,0,-60),(normal,elemental)
   nested=normal['proof']['nested_settlement'];assert (nested['parent_hp_before'],nested['parent_hp_after'],nested['parent_real_delta'],nested['child_hp_before'],nested['child_hp_after'],nested['child_real_delta'])==(100,60,-40,60,0,-60),nested
   counters=mark['native_async_capture'];assert counters['failure_code']==0 and counters['reserved_ticket']==counters['processed_ticket']==counters['closed_ticket']==counters['published_ticket']==8,counters
   ack=pipe.request('ack',sequence=through);receipt['ack']=ack;assert ack['acknowledged']==through,ack
   final=pipe.request('mark',sampling='confirm',publication_token=mark['native_publication']['token']);receipt['final']=final;assert final['native_async_capture']['delivered_event_sequence']==through,final
   receipt['final_ack']=pipe.request('ack',sequence=through);assert receipt['final_ack']['acknowledged']==through
   receipt['result']='PASS'
  except BaseException as e:
   receipt.update(result='FAIL',error_type=type(e).__name__,error=str(e));raise
  finally:
   (out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
   if pipe:pipe.close()
   f.close()
 return receipt

def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--source-root',type=Path,default=ROOT);p.add_argument('--output',type=Path,required=True);p.add_argument('--compiler',default='C:/msys64/ucrt64/bin/g++.exe');p.add_argument('--mismatch',action='store_true');p.add_argument('--pre-read-ack',action='store_true');p.add_argument('--ordinary',action='store_true');p.add_argument('--ordinary-only',action='store_true');a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);binary=a.binary.resolve();helper=build_helper(out/'helper',a.compiler);receipt={'binary_sha256':sha256(binary.read_bytes()).hexdigest(),'helper_sha256':sha256(helper.read_bytes()).hexdigest(),'cases':[]}
 try:
  for missing in ([]if a.ordinary_only else[False,True]):receipt['cases'].append(run(binary,helper,a.source_root.resolve(),out/('missing'if missing else'valid'),missing))
  if a.mismatch:receipt['cases'].append(run(binary,helper,a.source_root.resolve(),out/'mismatch-then-normal',mismatch=True))
  if a.pre_read_ack:receipt['cases'].append(run(binary,helper,a.source_root.resolve(),out/'unserved-ack',early_ack=True))
  if a.ordinary or a.ordinary_only:receipt['cases'].append(run_ordinary(binary,helper,a.source_root.resolve(),out/'ordinary-cold'))
  receipt['result']='PASS'
 except BaseException as e:receipt.update(result='FAIL',error=str(e));raise
 finally:(out/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
 print('PASS delivered native DLL damage/formula/Buff/Block → canonical publication/read/ACK, plus missing-evidence refusal')
if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='--child':child(Path(sys.argv[2]),Path(sys.argv[3]))
 else:main()
