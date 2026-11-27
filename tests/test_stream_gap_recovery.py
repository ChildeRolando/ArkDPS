"""Nonretryable evidence gaps have bounded diagnostics and keep source ownership."""
from copy import deepcopy
import json
import pytest
from capture_cli import json_value
from desktop_audit import DesktopCaptureJournal
from event_probe import StreamGap,stream_gap_failure_context,sanitize_stream_gap_failure
from test_pending_scope_start import pending_case,start_pending


def test_rejected_pending_release_is_not_reinitialized_or_acknowledged(monkeypatch):
    case=pending_case(monkeypatch);source=case.fixture;start_pending(case)
    case.advance(2,amount=75)
    read=source.observer.read_round_start_range
    def rejected(*args):
        result=read(*args);result['enemy_damage_ready']=False;return result
    monkeypatch.setattr(source.observer,'read_round_start_range',rejected)
    first=source.poll()
    assert first.kind=='sample'
    assert source.runtime.sessions.event_status=='gap'
    assert source.runtime.sessions.capture.closed
    assert source.commits==[0,1] and len(source.queue)==1
    assert source.runtime.feed._pending_scope is None
    reads=deepcopy(source.range_reads)
    source.poll()
    assert source.runtime.sessions.event_status=='gap'
    assert source.commits==[0,1] and len(source.queue)==1
    assert source.range_reads==reads
    case.advance(2.5,next_round=True)
    successor=source.poll()
    assert source.runtime.sessions.generation>first.generation
    assert source.commits==[0,1] and len(source.queue)==1
    assert source.runtime.sessions.event_status=='gap'
    assert successor.events=={'error':'rejected pending prefix remains unacknowledged'}
    assert source.runtime.sessions.capture.closed
    assert source.runtime.sessions.capture.rejected_pending_prefix.through==2
    case.advance(3)
    source.poll()
    assert source.commits==[0,1] and len(source.queue)==1
    assert source.runtime.sessions.event_status=='gap'


@pytest.mark.parametrize('debug',[False,True])
def test_stream_gap_site_is_preserved_without_error_text_or_paths(tmp_path,debug):
    from battle_lifecycle import BattleRuntime
    runtime=BattleRuntime()
    def bad(*args,**kwargs):raise StreamGap('C:/private SECRET_TOKEN')
    runtime.feed.acquire=bad
    with pytest.raises(StreamGap)as caught:runtime.poll(None)
    context=caught.value.stream_gap_failure
    assert context['reason']=='unclassified' and context['stage']=='acquire'
    assert context['frames'] and all('line'in f for f in context['frames'])
    assert sanitize_stream_gap_failure(context)==context
    with DesktopCaptureJournal(tmp_path,encoder=json_value,warn=pytest.fail,header=lambda *a,**k:{'kind':'header'},debug_mode=debug)as journal:
        journal.write('capture_error',dict(error_type='StreamGap',stream_gap_failure=context))
        journal.write('capture_error',dict(error_type='StreamGap',stream_gap_failure=dict(context,reason='SECRET_TOKEN')))
    text=''.join(p.read_text(encoding='utf8')for p in tmp_path.glob('*.jsonl'))
    assert 'SECRET_TOKEN'not in text and 'C:/private'not in text
    rows=[json.loads(l)for l in text.splitlines()]
    errors=[row['data']for row in rows if row['kind']=='capture_error']
    assert errors[0]['stream_gap_failure']==context
    assert 'stream_gap_failure'not in errors[1]

@pytest.mark.parametrize('stage',['acquire','prepare_observer','consume','acknowledge'])
def test_gap_diagnostic_reports_phase_without_turning_failure_into_retry(monkeypatch,stage):
    from types import SimpleNamespace
    from battle_lifecycle import BattleRuntime
    runtime=BattleRuntime()
    observation=SimpleNamespace(kind='sample',generation=1)
    runtime._acquire=lambda *a:observation
    runtime._consume=lambda *a:True
    runtime._acknowledge_observation=lambda *a:None
    runtime.sessions.generation=0
    def fail(*args,**kwargs):raise StreamGap('private unknown text')
    prepare=None
    if stage=='acquire':runtime._acquire=fail
    elif stage=='prepare_observer':prepare=fail
    elif stage=='consume':
        def consume(item):
            if item is observation:return fail()
            return False
        runtime._consume=consume
    else:runtime._acknowledge_observation=fail
    with pytest.raises(StreamGap)as caught:runtime.poll(None,prepare_observer=prepare)
    assert caught.value.stream_gap_failure['stage']==stage
    assert caught.value.stream_gap_failure['reason']=='unclassified'


def test_unknown_stack_and_reason_are_not_sanitized_as_public_diagnostics():
    good=dict(version=1,reason='unclassified',stage='consume',frames=[])
    assert sanitize_stream_gap_failure(good)==good
    for field,value in [('reason','C:/private'),('stage','SECRET'),('version',True),
        ('frames',[dict(module='battle_lifecycle',function='C:/private',line=1)]),
        ('frames',[dict(module='battle_lifecycle',function='poll',line=True)])]:
        assert sanitize_stream_gap_failure(dict(good,**{field:value}))is None

def test_unverified_pending_failure_never_retires_the_transaction(monkeypatch):
    import event_probe
    case=pending_case(monkeypatch);source=case.fixture;start_pending(case)
    pending=source.runtime.feed._pending_scope
    case.advance(2,amount=75)
    monkeypatch.setattr(event_probe,'_PENDING_SCOPE_BYTES',1)
    with pytest.raises(StreamGap):source.poll()
    assert source.runtime.feed._pending_scope is pending
    assert source.commits==[0,1] and len(source.queue)==1
    assert source.runtime.sessions.generation==1


def test_busy_pending_release_keeps_original_transaction_and_recovers(monkeypatch):
    from test_event_window_recovery import delayed_marks
    from event_capture import CaptureRetry
    case=pending_case(monkeypatch);source=case.fixture;start_pending(case)
    pending=source.runtime.feed._pending_scope
    case.advance(2,amount=75)
    with monkeypatch.context()as busy:
        delayed_marks(source,busy)
        with pytest.raises(CaptureRetry):source.poll()
    assert source.runtime.feed._pending_scope is pending
    assert source.range_reads==[] and source.commits==[0,1]
    source.poll()
    assert source.runtime.feed._pending_scope is None
    assert source.commits==[0,1,2]
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage']==75

def test_worker_keeps_proven_healthy_native_source_on_host_gap(tmp_path,monkeypatch):
    from test_native_pc_clock_busy_cadence import run_worker
    from test_host_gap_source_ownership import source
    owned,probe,pipe=source()
    def host_gap(session,worker):
        probe.collector.diagnostics=session.collector.diagnostics
        session.observer=probe;session.collector=probe.collector
        # Delegate the actual qualification method over these exact owned
        # collector/observer/pipe objects; the worker itself does not infer it.
        session.preserve_host_stream_gap=owned.preserve_host_stream_gap
        def invalid(*args,**kwargs):raise StreamGap('host rejected current scope')
        session.runtime.feed.acquire=invalid
        return session.runtime.poll(session.collector,session.observer)
    worker,starts,waits,sessions,events=run_worker(tmp_path,monkeypatch,[host_gap,'sample','stop'])
    assert len(sessions)==1 and len(starts)==3
    assert sessions[0].observer is probe and probe._pipe is pipe and not pipe._poisoned
    assert all(delay>=.1 for delay in waits) # ordinary cadence, no native busy burst
    assert not any(kind=='worker_error'for kind,_ in events)

def test_worker_does_not_preserve_poisoned_native_source(tmp_path,monkeypatch):
    from test_native_pc_clock_busy_cadence import run_worker
    from test_host_gap_source_ownership import source
    from event_observer import ObserverStatus
    owned,probe,pipe=source()
    def host_gap(session,worker):
        probe.collector.diagnostics=session.collector.diagnostics
        session.observer=probe;session.collector=probe.collector
        session.preserve_host_stream_gap=owned.preserve_host_stream_gap
        pipe._poisoned=True;probe.status=ObserverStatus.FAILED
        raise StreamGap('wrapped source failure')
    worker,starts,waits,sessions,events=run_worker(tmp_path,monkeypatch,[host_gap,'sample'],
        on_wait=lambda worker,*a:worker.stop.set())
    assert len(sessions)==1 and len(starts)==1
    assert pipe._poisoned and probe.status is ObserverStatus.FAILED
    assert not owned.preserve_host_stream_gap(StreamGap('still failed'))

def test_failed_pending_certificate_survives_terminal_copy_and_blocks_segment_bypass(monkeypatch):
    from event_capture import CaptureRetry
    from threading import Lock
    case=pending_case(monkeypatch);source=case.fixture;start_pending(case)
    case.advance(2,amount=75)
    read=source.observer.read_round_start_range
    def rejected(*args):
        result=read(*args);result['enemy_damage_ready']=False;return result
    monkeypatch.setattr(source.observer,'read_round_start_range',rejected)
    source.observer.uncloneable_owned_lock=Lock()
    observation=source.poll()
    certificate=source.runtime.sessions.capture.rejected_pending_prefix
    copy=deepcopy(source.runtime.sessions.capture)
    assert copy.rejected_pending_prefix is certificate
    assert certificate.observer is source.observer
    assert certificate.cursor_lifetime is source.runtime.sessions.capture.rejected_pending_prefix.cursor_lifetime
    feed=source.runtime.feed
    with pytest.raises(CaptureRetry):source.runtime.begin_capture_segment('event_capture_paused')
    assert source.runtime.feed is feed
    assert source.commits==[0,1] and len(source.queue)==1
    # The internal source capability never enters observation/history wire.
    from dataclasses import asdict
    assert 'rejected_pending_prefix'not in json.dumps(asdict(observation),default=json_value)

def test_failed_pending_tail_stays_unacked_when_new_scope_is_unverified(monkeypatch):
    case=pending_case(monkeypatch);source=case.fixture;start_pending(case)
    case.advance(2,amount=75)
    read=source.observer.read_round_start_range
    monkeypatch.setattr(source.observer,'read_round_start_range',lambda *a:dict(read(*a),enemy_damage_ready=False))
    source.poll()
    certificate=source.runtime.sessions.capture.rejected_pending_prefix
    case.advance(2.5,next_round=True)
    mark=source.observer.mark
    with monkeypatch.context()as bad:
        bad.setattr(source.observer,'mark',lambda:dict(mark(),source_build='foreign'))
        observation=source.poll()
    assert observation.kind=='scope_unavailable'
    assert source.commits==[0,1] and len(source.queue)==1
    assert source.runtime.sessions.capture.rejected_pending_prefix is certificate
    source.poll() # now the new scope is valid, but old tail is still not consumed
    assert source.runtime.sessions.event_status=='gap'
    assert source.commits==[0,1] and len(source.queue)==1

def test_only_verified_current_replacement_owner_can_start_new_segment(monkeypatch):
    from event_capture import CaptureRetry
    case=pending_case(monkeypatch);old=case.fixture;start_pending(case)
    case.advance(2,amount=75)
    read=old.observer.read_round_start_range
    with monkeypatch.context()as rejected:
        rejected.setattr(old.observer,'read_round_start_range',lambda *a:dict(read(*a),enemy_damage_ready=False))
        old.poll()
    runtime=old.runtime
    certificate=runtime.sessions.capture.rejected_pending_prefix
    newcase=pending_case(monkeypatch);new=newcase.fixture
    # Merely obtaining a replacement, or a stale receipt for it, does not
    # accept its prefix or erase the old rejection certificate.
    old_accepted=runtime._accepted_event_observer
    for wrong in [(new.observer,runtime.sessions.generation-1,new.observer._cursor_lifetime),
                  (new.observer,runtime.sessions.generation,object())]:
        runtime._accepted_event_observer=wrong
        with pytest.raises(CaptureRetry):runtime.begin_capture_segment('event_capture_paused')
        assert runtime.sessions.capture.rejected_pending_prefix is certificate
    runtime._accepted_event_observer=old_accepted
    with pytest.raises(CaptureRetry):runtime.begin_capture_segment('event_capture_paused')
    new.runtime=runtime;newcase.advance(3,next_round=True)
    new.poll()
    accepted=runtime._accepted_event_observer
    assert accepted[0]is new.observer and accepted[2]is not certificate.cursor_lifetime
    assert runtime.sessions.event_status=='recording'and not runtime.sessions.capture.closed
    assert not hasattr(runtime.sessions.capture,'rejected_pending_prefix')
    result=runtime.begin_capture_segment('event_capture_paused')
    assert result['reason']=='event_capture_paused'
    assert old.commits==[0,1]and len(old.queue)==1
    assert new.commits==[0,1,1]

@pytest.mark.parametrize('sample',[None,{}, {'battle_metadata':{'platform':'native_pc'}}])
def test_canonical_native_no_ack_partial_sample_does_not_raise_or_touch_pipe(monkeypatch,sample):
    from types import SimpleNamespace
    from battle_lifecycle import BattleRuntime
    from test_host_gap_source_ownership import source
    owned,probe,pipe=source()
    def forbidden(*args,**kwargs):pytest.fail('no ACK action may touch the pipe')
    monkeypatch.setattr(pipe,'request',forbidden)
    runtime=BattleRuntime()
    observation=SimpleNamespace(sequence=1,generation=0,sample=sample,events=None)
    runtime._acknowledge_observation(observation,probe,False)
    assert runtime._pending_event_ack is None
    assert runtime._accepted_event_observer is None
    assert not pipe._poisoned
