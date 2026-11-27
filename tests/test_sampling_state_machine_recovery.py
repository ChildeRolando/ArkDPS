"""A rejection/cancellation must be followed by real legal host consumption.

Owned reports exercise the real probe binding, feed and reducer. They do not
claim a Windows DLL/Frida transport or live-game acceptance.
"""
from copy import deepcopy
from types import MethodType,SimpleNamespace

import pytest

from event_capture import CaptureRetry
from event_observer import ObserverStatus
from event_probe import ObserverReadRetry,reset_probe_cursor
from native_pc.native_hook_source import NativeHookEventProbe
from test_sampling_transaction_continuation import world


def transport_world():
    source,probe,busy,calls=world()
    wire=probe._raw_request
    probe._pipe=SimpleNamespace(request=lambda op,**kw:wire(op,**kw))
    # Retain the real READY/FAILED and transport-exception state transitions.
    probe._raw_request=MethodType(NativeHookEventProbe._raw_request,probe)
    return source,probe,busy,calls


def assert_recovered_once(source):
    assert source.runtime.feed._sampling_transaction is None
    assert source.commits[-1]==1 and len(source.queue)==0
    player=source.runtime.sessions.multiplayer.result()['players'][2]
    assert player['observed_damage']==75
    source.advance(4)
    assert source.poll().kind=='sample'
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage']==75


@pytest.mark.parametrize('change',['mode','pause','revision','generation','expiry'])
def test_cancelled_confirmation_is_followed_by_a_legal_poll_and_one_reduction(change):
    source,probe,busy,calls=transport_world();assert source.poll().kind=='sample'
    source.advance(1,[source.scoped_hit(1,3,75)]);busy[0]=1
    with pytest.raises(CaptureRetry):source.poll()
    old=source.runtime.feed._sampling_transaction;assert old is not None
    if change=='mode':probe._autochess_enabled=lambda:True
    elif change=='pause':probe.capture_paused=True
    elif change=='revision':probe._capture_control_revision+=1
    elif change=='generation':probe._capture_control_generation+=1
    elif change=='expiry':old['started']-=21
    previous=len(calls);source.advance(2)
    assert source.poll().kind=='sample'
    assert probe.status==ObserverStatus.READY
    assert not any(c.get('sampling')=='confirm'and c.get('sample_request')=='9'for c in calls[previous:])
    assert_recovered_once(source)


def test_expired_completed_confirm_receipt_reopens_full_window_without_losing_retained_hit():
    source,probe,busy,calls=transport_world();source.poll()
    source.advance(1,[source.scoped_hit(1,3,75)]);busy[0]=1
    with pytest.raises(CaptureRetry):source.poll()
    wire=probe._pipe.request;expired=[True]
    def request(op,**kw):
        if kw.get('sampling')=='confirm'and expired[0]:
            expired[0]=False
            return dict(retry=True,native_sample_request='9',mark_retry_reason='native_publication_token_expired')
        return wire(op,**kw)
    probe._pipe.request=request
    with pytest.raises(CaptureRetry):source.poll()
    assert source.commits==[0]and len(source.queue)==1
    assert source.runtime.feed._sampling_transaction is None and probe._sampling_anchor is None
    source.advance(2);assert source.poll().kind=='sample'
    assert_recovered_once(source)


def test_successful_confirm_with_temporarily_unavailable_binding_does_not_pin_completed_request():
    source,probe,busy,calls=transport_world();source.poll()
    source.advance(1,[source.scoped_hit(1,3,75)])
    bind=probe.binding.bind;reject=[True]
    def binding(op,raw):
        if op=='mark'and probe._sampling_anchor is not None and reject[0]:
            reject[0]=False
            raise ObserverReadRetry('owned independent sampler temporarily unavailable')
        return bind(op,raw)
    probe.binding.bind=binding
    with pytest.raises(CaptureRetry):source.poll()
    assert source.commits==[0]and len(source.queue)==1
    assert probe.status==ObserverStatus.READY and probe._confirm_sample_request is None
    source.advance(2);assert source.poll().kind=='sample'
    assert_recovered_once(source)


def test_actual_failed_transport_never_becomes_healthy_only_because_next_report_is_valid():
    source,probe,busy,calls=transport_world();source.poll()
    source.advance(1,[source.scoped_hit(1,3,75)])
    wire=probe._pipe.request;failed=[False]
    def request(op,**kw):
        if kw.get('sampling')=='confirm':
            failed[0]=True;raise RuntimeError('native capture failed: code=9')
        return wire(op,**kw)
    probe._pipe.request=request
    try:source.poll()
    except (CaptureRetry,RuntimeError,ValueError):pass
    assert failed[0]and probe.status==ObserverStatus.FAILED
    assert source.commits==[0]and len(source.queue)==1
    previous=len(calls);probe._pipe.request=wire
    with pytest.raises(RuntimeError):probe.mark()
    assert len(calls)==previous and probe.status==ObserverStatus.FAILED
    assert source.commits==[0]


def test_changed_cursor_lifetime_stays_rejected_after_transaction_cancelled():
    source,probe,busy,calls=transport_world();source.poll()
    source.advance(1,[source.scoped_hit(1,3,75)]);busy[0]=1
    with pytest.raises(CaptureRetry):source.poll()
    reset_probe_cursor(probe)
    for _ in range(2):
        with pytest.raises(ValueError,match='cursor lifetime changed'):source.poll()
    assert source.commits==[0]and len(source.queue)==1
    assert source.runtime.feed._sampling_transaction is None


@pytest.mark.parametrize('expired',[False,True])
def test_fresh_retry_then_legal_poll_reduces_retained_hit_once(expired):
    source,probe,busy,calls=transport_world();source.poll()
    source.advance(1,[source.scoped_hit(1,3,75)])
    wire=probe._pipe.request;once=[True];fresh=[]
    def request(op,**kw):
        if kw.get('sampling')=='fresh':
            fresh.append(dict(kw))
            if once[0]:
                once[0]=False
                return dict(retry=True,native_sample_request='8',
                    mark_retry_reason='native_publication_token_expired'if expired else'native_fresh_publication_not_ready')
        return wire(op,**kw)
    probe._pipe.request=request
    with pytest.raises(CaptureRetry):source.poll()
    assert source.commits==[0]and len(source.queue)==1
    assert probe._sample_request==(None if expired else'8')
    source.advance(2);assert source.poll().kind=='sample'
    assert fresh[1].get('sample_request')==(None if expired else'8')
    assert_recovered_once(source)


def test_unaccepted_binding_proposal_recovers_at_independent_sample_then_consumes_next_hit():
    from event_probe import NativePcBinding
    from test_pc_serial_clock_reset_end import CollectorClockBattle
    source=CollectorClockBattle();process=source.collector.process
    actual=tuple(int(v,16)for v in source.identity_roots.values())
    process._identity=(*actual[:-1],0x60000);process._stage_id=source.stage
    process._serial=4;process._last_clock=(100,100*(1<<32)//30)
    binding=NativePcBinding(source.collector);raw=source.observer.mark
    source.observer.mark=lambda:binding.bind('mark',raw())
    source.observer.remember_verified_mark=binding.remember_verified_mark
    source.advance(2)
    with pytest.raises(CaptureRetry):source.poll()
    assert not binding.generation_sessions and source.commits==[]
    assert process._identity==actual and process._serial==5
    assert source.poll().kind=='sample'
    assert binding.generation_sessions and source.commits==[0]
    source.advance(3,[source.scoped_hit(1,3,75)])
    assert source.poll().kind=='sample'
    assert_recovered_once(source)


def test_frida_timed_out_mark_late_response_cannot_satisfy_following_request():
    from event_observer import FridaEventObserver
    from test_event_observer import collector
    from connection import VersionStatus
    observer=FridaEventObserver(collector(VersionStatus.COMPATIBLE),'unused')
    observer.status=ObserverStatus.READY;observer.request_timeout=0
    missed=[];posts=[]
    def reply(request,result):
        observer._message(dict(type='send',payload=dict(kind='arkdps-event-response',id=request['id'],result=result)),None)
    def post(request):
        posts.append(request)
        if not missed:missed.append(request);return
        reply(missed[0],dict(sequence=999,error='late fixture answer'))
        reply(request,dict(sequence=len(posts),error=None))
    observer.script=SimpleNamespace(post=post,is_destroyed=False)
    with pytest.raises(ObserverReadRetry,match='timed out'):observer.mark()
    assert observer.mark()==dict(sequence=2,error=None)
    assert observer.mark()==dict(sequence=3,error=None)
    assert observer._pending_request is None and observer.status==ObserverStatus.READY
    assert observer._committed_sequence==observer._acknowledged_sequence==0
