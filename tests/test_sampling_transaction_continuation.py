"""Owned full acquisition/consumer continuation, no game or native memory."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from event_capture import CaptureRetry
from event_observer import ObserverStatus
from event_probe import ObserverReadRetry
from native_pc.native_hook_source import NativeHookEventProbe
from test_pc_serial_clock_reset_end import CollectorClockBattle

def world():
 source=CollectorClockBattle();old=source.observer
 independent=source.collector.process.sample
 def sampled(**kwargs):
  source.reads.append('owned_independent_sample')
  return independent(**kwargs)
 source.collector.process.sample=sampled
 probe=NativeHookEventProbe(source.collector);probe.status=ObserverStatus.READY
 probe._sampling_supported=True
 serial=[0];busy=[0];calls=[];opening=[None]
 def request(op,**kw):
  calls.append(kw)
  if kw.get('sampling')=='confirm' and busy[0]:
   busy[0]-=1;return dict(retry=True,native_sample_request='9')
  raw=source.mark();serial[0]+=1
  raw['native_publication']=dict(version=1,kind='sampling_checkpoint',token='0:'+str(serial[0]),closed_ticket='0',activity_begin='0',activity_end='0',sequence=raw['sequence'],epoch=raw['epoch'],sampling_serial=str(serial[0]),sample_request='9'if kw.get('sampling')=='confirm'else'8')
  return raw
 probe._raw_request=request
 probe.read_through=source.batch;probe.commit_events=source.commit
 probe.read_baseline=lambda seq:dict(source.batch(seq),events=[])
 source.observer=probe
 return source,probe,busy,calls

def test_busy_confirmation_across_polls_keeps_original_sample_and_one_reduction():
 s,p,busy,calls=world();assert s.poll().kind=='sample'
 s.advance(1,[s.scoped_hit(1,3,75)]);busy[0]=3
 for _ in range(3):
  with pytest.raises(CaptureRetry):s.poll()
  assert s.runtime.feed._sampling_transaction is not None
  if _==0:reads=list(s.reads);held=deepcopy(s.runtime.feed._sampling_transaction['sample'])
  else:
   assert s.reads==reads
   assert s.runtime.feed._sampling_transaction['sample']==held
  assert s.commits==[0]
 assert s.poll().kind=='sample'
 assert s.runtime.feed._sampling_transaction is None
 assert s.commits==[0,1]
 assert s.runtime.sessions.multiplayer.result()['players'][2]['observed_damage']==75
 assert [x.get('sample_request')for x in calls if x.get('sampling')=='confirm'][-3:]==['9','9','9']

@pytest.mark.parametrize('fault',['mode','cursor','pause','revision','generation','expiry','process','failed'])
def test_transaction_cancellation_never_reuses_original_request(monkeypatch,fault):
 from event_probe import reset_probe_cursor
 s,p,busy,calls=world();s.poll();s.advance(1,[s.scoped_hit(1,3,75)]);busy[0]=1
 with pytest.raises(CaptureRetry):s.poll()
 held=s.runtime.feed._sampling_transaction
 assert held is not None
 if fault=='mode':p._autochess_enabled=lambda:True
 elif fault=='cursor':reset_probe_cursor(p)
 elif fault=='pause':p.capture_paused=True
 elif fault=='revision':p._capture_control_revision+=1
 elif fault=='generation':p._capture_control_generation+=1
 elif fault=='expiry':held['started']-=21
 elif fault=='process':s.collector.process.process_instance=(10,9999)
 elif fault=='failed':p.status=ObserverStatus.FAILED
 previous=len(calls);s.advance(2)
 try:s.poll()
 except Exception:pass # Genuine source/identity failure remains rejection.
 assert s.runtime.feed._sampling_transaction is not held
 assert p._confirm_sample_request is None
 assert not any(x.get('sample_request')=='9' for x in calls[previous:])
 assert not any(x.get('sampling')=='confirm' and x.get('publication_token')==held['opening'][0]['native_publication']['token'] for x in calls[previous:])


def test_changed_scope_confirmation_is_not_accepted_or_acked():
 s,p,busy,calls=world();s.poll();s.advance(1,[s.scoped_hit(1,3,75)]);busy[0]=1
 with pytest.raises(CaptureRetry):s.poll()
 s.native_phase=5;s.autochess_epoch+=1
 with pytest.raises(CaptureRetry):s.poll()
 assert s.commits==[0] and len(s.queue)==1
 assert s.runtime.feed._sampling_transaction is None


def test_terminal_retry_expiry_releases_only_uncommitted_transaction():
 s,p,busy,calls=world();s.poll();s.advance(1,[s.scoped_hit(1,3,75)]);busy[0]=2
 with pytest.raises(CaptureRetry):s.poll()
 held=s.runtime.feed._sampling_transaction;reads=list(s.reads)
 s.state=3
 with pytest.raises(CaptureRetry):s.poll()
 assert s.reads==reads and s.commits==[0] and len(s.queue)==1
 held=s.runtime.feed._sampling_transaction;held['started']-=21
 previous=len(calls)
 try:s.poll()
 except CaptureRetry:pass
 assert s.runtime.feed._sampling_transaction is not held
 assert 'owned_independent_sample' in s.reads[len(reads):]
 assert not any(x.get('sample_request')=='9'for x in calls[previous:])


@pytest.mark.parametrize('kind',['fresh','confirm'])
def test_expired_completed_receipt_does_not_resume_dead_request(kind):
 from test_native_sampling_acquisition import probe_with,mark
 expired=dict(retry=True,native_sample_request='9',mark_retry_reason='native_publication_token_expired')
 responses=[expired,mark(3,request='10')]if kind=='fresh'else[mark(),expired,mark(3,request='10')]
 p,calls=probe_with(responses)
 if kind=='confirm':
  opening=p.mark()
  with pytest.raises(ObserverReadRetry):p.confirm_mark(opening)
 else:
  with pytest.raises(ObserverReadRetry):p.mark()
 assert p._sample_request is None and p._confirm_sample_request is None
 assert p._sampling_anchor is None
 p.mark()
 assert calls[-1][1]['sampling']=='fresh'and'sample_request'not in calls[-1][1]


def test_expired_completed_terminal_receipt_reopens_terminal_without_ack():
 from test_native_sampling_acquisition import probe_with,mark
 p,calls=probe_with([dict(retry=True,native_sample_request='9',mark_retry_reason='native_publication_token_expired'),mark(3,request='10')])
 p._sampling_scheduler_supported=True;p.binding.bind_terminal_mark=lambda raw:raw
 assert p.read_terminal_mark()['retry']
 assert p._terminal_sample_request is None
 p.read_terminal_mark()
 assert [x[1]['sampling']for x in calls]==['terminal','terminal']
 assert all('sample_request'not in x[1]for x in calls)
 assert all(x[0]=='mark'for x in calls)
