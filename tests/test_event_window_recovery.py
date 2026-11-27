"""Temporal event windows retry transactionally; source/sequence faults stay fatal."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import pytest
from event_probe import BarrierBusy, StreamGap, barrier_failure_context, verified_autochess_event_window
from event_capture import CaptureRetry
from battle_lifecycle import BattleFeed, BattleSessions, BattleRuntime
from desktop_audit import DesktopCaptureJournal, sanitize_window_failure
from capture_cli import json_value
from test_pc_autochess_cross_frame_admission import established, own_amount


def delayed_marks(source, monkeypatch):
    original = source.observer.mark
    def old_clock():
        value = deepcopy(original())
        frame = value['frame'] - 1
        fixed = max(0, int(value['native_fixed_time']) - (1 << 32) // 30)
        value.update(frame=frame, game_time=fixed/(1 << 32), native_frame=frame, native_fixed_time=str(fixed))
        value['native_autochess_context'].update(frame=frame, game_time=value['game_time'])
        return value
    monkeypatch.setattr(source.observer, 'mark', old_clock)


def test_real_counter_window_busy_keeps_cursor_coverage_and_source_then_recovers(monkeypatch):
    source = established()
    source.advance(2)
    before = list(source.commits), own_amount(source), source.runtime.sessions.sequence
    capture = source.runtime.sessions.capture
    recorded=[];source.runtime.record=recorded.append
    drained=[]
    for name in ('read_through','read_baseline'):
        original=getattr(source.observer,name)
        monkeypatch.setattr(source.observer,name,lambda *args,_name=name,_read=original: (drained.append(_name),_read(*args))[1])
    with monkeypatch.context() as busy:
        delayed_marks(source,busy)
        with pytest.raises(CaptureRetry) as caught:
            source.poll()
    assert not drained and not recorded
    assert (source.commits, own_amount(source), source.runtime.sessions.sequence)==before
    assert source.runtime.sessions.capture is capture and not capture.closed
    context=barrier_failure_context(caught.value)
    assert context['stage'] in ('guarded_events','runtime_poll')
    assert context['snapshot']['frame'] > context['after']['frame']
    assert sanitize_window_failure(context)==context
    source.advance(2,[source.scoped_hit(2,3,40)])
    assert source.poll().kind=='sample'
    assert own_amount(source)==70 and source.commits[-1]==2
    assert not capture.closed


def test_opening_busy_is_not_stringified_into_a_fatal_event_payload():
    error=BarrierBusy('typed opening window')
    with pytest.raises(CaptureRetry) as caught:
        BattleFeed._guarded_events(SimpleNamespace(),(None,error),{})
    assert caught.value.__cause__ is error


def test_direct_runtime_busy_does_not_emit_gap_or_retire_source():
    runtime=BattleRuntime();recorded=[];runtime.record=recorded.append
    def busy(*args,**kwargs): raise BarrierBusy('window not aligned')
    runtime.feed.acquire=busy
    with pytest.raises(CaptureRetry): runtime.poll(SimpleNamespace(),SimpleNamespace(_committed_sequence=7))
    assert recorded==[] and runtime.sessions.adapter is None


def test_busy_consume_preflight_cannot_close_or_archive_the_previous_scope(monkeypatch):
    source=established();source.advance(2)
    original=source.observer.mark;before=original();sample,roster=source.collector.read_guarded_pair(source.collector.read_lifecycle())
    source.runtime.feed._project_event_window(sample,before,deepcopy(before))
    evidence=dict(before=before,after=deepcopy(before),sequence=before['sequence'],drain=source.batch(before['sequence']))
    observation=source.runtime.feed._emit('sample',sample,roster,events=evidence)
    # Independent physical anchor moves outside the admitted marks; do not retag marks.
    observation.sample['autochess_snapshot_anchor']['frame']+=1
    for side in ('before','after'):observation.sample['native_window_guard'][side]['frame']+=1
    capture=source.runtime.sessions.capture;adapter=source.runtime.sessions.adapter
    archives=deepcopy(list(source.runtime.sessions._archives))
    with pytest.raises(BarrierBusy):source.runtime._consume(observation)
    assert source.runtime.sessions.capture is capture and not capture.closed
    assert source.runtime.sessions.adapter is adapter and list(source.runtime.sessions._archives)==archives


@pytest.mark.parametrize('fault',['source','records','queue_full'])
def test_real_source_and_prefix_failures_are_not_treated_as_window_busy(fault,monkeypatch):
    source=established();source.advance(2,[source.scoped_hit(2,3,40)])
    if fault in ('source','queue_full'):
        original=source.observer.mark
        def bad():
            value=original()
            if fault=='source':value['source_build']='other-build'
            else:value['error']='native callback capture failed code=4'
            return value
        monkeypatch.setattr(source.observer,'mark',bad)
        # A rejected mark is event coverage failure, not a transport ERROR.
        sample,_=source.collector.read_guarded_pair(source.collector.read_lifecycle())
        with pytest.raises(StreamGap) as caught:
            verified_autochess_event_window(sample,bad(),bad())
        assert not isinstance(caught.value,BarrierBusy)
        observation=source.poll()
        assert observation.kind=='sample' and observation.sample['frame']==source.frame
        assert source.runtime.sessions.capture.closed
        assert source.runtime.sessions.event_status=='gap'
        assert own_amount(source)==30  # rejected hit never enters the accepted ledger
    else:
        monkeypatch.setattr(source.observer,'read_through',lambda sequence:dict(source.batch(sequence),events=[]))
        source.poll()
        assert source.runtime.sessions.capture.closed
        assert source.runtime.sessions.event_status=='gap'
    assert source.commits==[0,1]


def test_default_journal_keeps_bounded_window_context_but_never_arbitrary_payload(tmp_path,monkeypatch):
    source=established();source.advance(2)
    with monkeypatch.context()as busy:
        delayed_marks(source,busy)
        with pytest.raises(CaptureRetry)as caught:source.poll()
    context=barrier_failure_context(caught.value)
    with DesktopCaptureJournal(tmp_path,encoder=json_value,warn=pytest.fail,header=lambda *a,**k:{'kind':'header'},debug_mode=False)as journal:
        journal.write('capture_retry',dict(error_type='CaptureRetry',code='event_barrier_busy',window_failure=context))
        journal.write('capture_error',dict(error_type='StreamGap',window_failure=dict(context,events=[{'private':'bad'}])))
    rows=[json.loads(l)for p in tmp_path.glob('*.jsonl')for l in p.read_text(encoding='utf8').splitlines()]
    assert next(x for x in rows if x['kind']=='capture_retry')['data']['window_failure']==context
    assert 'window_failure'not in next(x for x in rows if x['kind']=='capture_error')['data']

def test_pending_scope_busy_does_not_discard_anchor_or_first_retained_hit(monkeypatch):
    from test_pending_scope_start import pending_case,start_pending
    case=pending_case(monkeypatch);source=case.fixture;start_pending(case)
    case.advance(2,amount=75)
    capture=source.runtime.sessions.capture
    with monkeypatch.context()as busy:
        delayed_marks(source,busy)
        with pytest.raises(CaptureRetry):source.poll()
    assert source.commits==[0,1] and not capture.closed
    assert source.range_reads==[]
    source.poll()
    assert source.commits==[0,1,2]
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage']==75


def test_round_change_busy_waits_before_retiring_previous_round(monkeypatch):
    from test_autochess_scope_end import OwnedScopeEnd
    case=OwnedScopeEnd('native_pc');source=case.fixture
    old_capture=source.runtime.sessions.capture
    archives=deepcopy(list(source.runtime.sessions._archives));commits=list(source.commits)
    case.advance(1.5,next_round=True)
    with monkeypatch.context()as busy:
        delayed_marks(source,busy)
        with pytest.raises(CaptureRetry):source.poll()
    assert source.runtime.sessions.capture is old_capture and not old_capture.closed
    assert list(source.runtime.sessions._archives)==archives and source.commits==commits
    successor=source.poll()
    archives=source.runtime.sessions.pop_archives()
    assert len(archives)==1 and archives[0]['event_status']=='finished'
    assert successor.kind=='sample'


def test_worker_keeps_same_session_after_real_runtime_barrier_retry(tmp_path,monkeypatch):
    from test_native_pc_clock_busy_cadence import run_worker
    def transient(session,worker):
        original=session.runtime.feed.acquire
        def busy(*args,**kwargs):raise BarrierBusy('transient counter window')
        session.runtime.feed.acquire=busy
        try:return session.runtime.poll(session.collector,session.observer)
        finally:session.runtime.feed.acquire=original
    worker,starts,waits,sessions,events=run_worker(tmp_path,monkeypatch,[transient,'sample','stop'])
    assert len(sessions)==1
    assert len(starts)==3 and not any(kind=='worker_error' for kind,_ in events)

@pytest.mark.parametrize('debug',[False,True])
def test_queue_failure_is_sanitized_in_both_journal_modes(tmp_path,debug):
    from test_native_queue_memory_diagnostics import queue_failure
    raw=queue_failure();raw['SECRET']={'large':'payload'}
    with DesktopCaptureJournal(tmp_path,encoder=json_value,warn=pytest.fail,header=lambda *a,**k:{'kind':'header'},debug_mode=debug)as journal:
        journal.write('native_hook_request_failed',dict(op='mark',queue_failure=raw))
        journal.write('native_hook_request_failed',dict(op='mark',queue_failure=dict(raw,lane=True)))
    rows=[json.loads(l)for p in tmp_path.glob('*.jsonl')for l in p.read_text(encoding='utf8').splitlines()]
    failures=[row['data']for row in rows if row['kind']=='native_hook_request_failed']
    assert failures[0]['queue_failure']==queue_failure()
    assert 'queue_failure'not in failures[1]

@pytest.mark.parametrize('debug',[False,True])
@pytest.mark.parametrize('point',[{'frame':'C:/private/secret'},{'scope_key':'SECRET_TOKEN'},{'native_session':'C:/private'},{'source_build':'SECRET_TOKEN'},{'native_fixed_time':'C:/private'},{'collection_mode':'SECRET_TOKEN'}])
def test_window_diagnostics_reject_text_in_numeric_or_private_identity_fields(tmp_path,debug,point):
    context=dict(stage='runtime_poll',stages=['runtime_poll'],before=point)
    with DesktopCaptureJournal(tmp_path,encoder=json_value,warn=pytest.fail,header=lambda *a,**k:{'kind':'header'},debug_mode=debug)as journal:
        journal.write('capture_retry',dict(error_type='CaptureRetry',window_failure=context))
    text=''.join(p.read_text(encoding='utf8')for p in tmp_path.glob('*.jsonl'))
    assert 'C:/private'not in text and 'SECRET_TOKEN'not in text
    assert 'window_failure'not in text


def test_annotation_normalizes_fixed_without_retaining_identity_text():
    from event_probe import annotate_barrier_busy
    error=BarrierBusy('busy')
    annotate_barrier_busy(error,'runtime_poll',before=dict(frame=12,native_fixed_time='4294967296',
        native_session='SECRET',source_build='C:/private',native_autochess_context=dict(scope_key='SECRET',round=3,phase=6,collection_mode='all')))
    context=barrier_failure_context(error)
    assert context['before']==dict(frame=12,native_fixed_time=4294967296,round=3,phase=6,collection_mode='all')
    assert sanitize_window_failure(context)==context

# Use the actual transport/observer ownership boundary, not mark error text.
from test_native_pc_native_hooks import setup_probe
@pytest.mark.parametrize('code',[4,6])
def test_real_native_error_poison_is_not_window_retry(setup_probe,code):
    from test_event_queue_memory import error_pipe
    from event_observer import ObserverStatus
    make,_,_=setup_probe;probe=make()
    pipe=error_pipe(dict(error=f'native capture failed: code={code}'))
    probe.status=ObserverStatus.READY;probe._pipe=pipe
    with pytest.raises(RuntimeError)as caught:probe._raw_request('mark')
    assert not isinstance(caught.value,(CaptureRetry,BarrierBusy))
    assert pipe._poisoned and probe.status is ObserverStatus.FAILED
    with pytest.raises(RuntimeError):probe._raw_request('mark')

def test_rejected_projection_has_no_native_clock_busy_retry_privilege(monkeypatch):
    from desktop_bridge import _native_clock_busy
    from snapshot_adapter import FrameMismatch
    source=established();source.advance(2)
    before=source.observer.mark();after=deepcopy(before);after['source_build']='foreign'
    sample,_=source.collector.read_guarded_pair(source.collector.read_lifecycle())
    with pytest.raises(FrameMismatch)as caught:
        source.runtime.feed._project_event_window(sample,before,after)
    assert isinstance(caught.value.__cause__,StreamGap)
    assert not isinstance(caught.value.__cause__,BarrierBusy)
    retry=CaptureRetry('projection rejected');retry.__cause__=caught.value
    assert not _native_clock_busy(retry)
    assert 'autochess_snapshot_anchor'not in sample
    assert source.commits==[0,1]
