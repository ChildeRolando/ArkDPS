"""Actual Frida request-ID/response loop over owned messages, never a target."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from battle_lifecycle import BattleRuntime
from event_capture import CaptureRetry
from event_observer import FridaEventObserver,ObserverStatus
from event_probe import NATIVE_PC_CONTRACT,reset_probe_cursor
from test_lifecycle_events import native_connection
from test_event_capture import event


def pending_frida(*,lost=1):
    runtime,collector,old,snapshot,state,events,reads,_=native_connection()
    collector.process.process_alive=lambda:True
    collector._owned_events=events;collector._owned_snapshot=snapshot
    probe=FridaEventObserver(collector,None);probe.contract=NATIVE_PC_CONTRACT
    probe.status=ObserverStatus.READY;probe.request_timeout=0
    probe.session=SimpleNamespace(is_detached=False)
    rpc=[];busy=[False];remaining=[lost];remote_ack=[0]
    def post(request):
        operation=request['operation'];rpc.append((operation,request.get('sequence')))
        if operation=='mark':
            if busy[0]:return # actual _request waits then raises ObserverReadRetry, keeps source READY
            reply=old.mark()
        elif operation=='read':
            reply=old.read_through(request['through'])
            reply['events']=[item for item in reply['events']if request['after']<item['sequence']<=request['through']]
        elif operation=='ack':
            sequence=request['sequence']
            remote_ack[0]=max(remote_ack[0],sequence)
            events.queue[:]=[item for item in events.queue if item['sequence']>sequence]
            if sequence==1 and remaining[0]:
                remaining[0]-=1;return # producer applied it, response lost
            reply={'acknowledged':remote_ack[0]}
        else:raise AssertionError(operation)
        probe._message({'type':'send','payload':{'kind':'arkdps-event-response','id':request['id'],'result':reply}},None)
    probe.script=SimpleNamespace(is_destroyed=False,post=post)
    runtime.poll(collector,probe)
    snapshot.frame,snapshot.game_time,snapshot.damage=2,1.,20.
    events.queue=[event(1,frame=2,game_time=1.,amount=20)]
    accepted=runtime.poll(collector,probe)
    assert runtime._pending_event_ack is not None and len(runtime._pending_event_ack)==6
    assert probe._committed_sequence==1 and probe._acknowledged_sequence==0 and probe._ack_error is None
    return runtime,collector,probe,rpc,busy,remote_ack,accepted


def test_accepted_ack_retries_while_new_sampling_is_busy_without_new_drain():
    runtime,collector,probe,rpc,busy,remote,accepted=pending_frida()
    busy[0]=True;reads=sum(op=='read'for op,_ in rpc)
    before=runtime.sessions.adapter.result()['rows']['char_a']['observed_damage']
    with pytest.raises(CaptureRetry):runtime.poll(collector,probe)
    assert runtime._pending_event_ack is None
    assert probe._acknowledged_sequence==remote[0]==1
    assert [seq for op,seq in rpc if op=='ack']==[1,1]
    assert sum(op=='read'for op,_ in rpc)==reads
    assert runtime.sessions.adapter.result()['rows']['char_a']['observed_damage']==before==20
    assert probe.status is ObserverStatus.READY and not probe.script.is_destroyed


def test_repeated_unknown_reply_is_idempotent_and_keeps_the_same_receipt():
    runtime,collector,probe,rpc,busy,remote,_=pending_frida(lost=2)
    pending=runtime._pending_event_ack;busy[0]=True
    with pytest.raises(CaptureRetry):runtime.poll(collector,probe)
    assert runtime._pending_event_ack is pending and probe._ack_error is None
    with pytest.raises(CaptureRetry):runtime.poll(collector,probe)
    assert runtime._pending_event_ack is None and remote[0]==1
    assert [seq for op,seq in rpc if op=='ack']==[1,1,1]
    assert runtime.sessions.adapter.event_stream.sequence==1


@pytest.mark.parametrize('fault',['observer','script','session','cursor','birth','pid','failed','destroyed','detached','ack_error','unknown_script','wrong_sequence','forged'])
def test_changed_or_unknown_owner_never_retries_the_old_ack(fault):
    runtime,collector,probe,rpc,busy,remote,_=pending_frida()
    pending=runtime._pending_event_ack;count=len(rpc);target=probe
    if fault=='observer':target=FridaEventObserver(collector,None)
    elif fault=='script':probe.script=SimpleNamespace(is_destroyed=False,post=lambda *_:pytest.fail('new script cannot ACK'))
    elif fault=='session':probe.session=SimpleNamespace(is_detached=False)
    elif fault=='cursor':reset_probe_cursor(probe)
    elif fault=='birth':collector.process.process_instance=(10,1235)
    elif fault=='pid':collector.process.pid=11
    elif fault=='failed':probe.status=ObserverStatus.FAILED
    elif fault=='destroyed':probe.script.is_destroyed=True
    elif fault=='detached':probe.session.is_detached=True
    elif fault=='ack_error':probe._ack_error='protocol mismatch'
    elif fault=='unknown_script':del probe.script.is_destroyed
    elif fault=='wrong_sequence':runtime._pending_event_ack=(*pending[:1],2,*pending[2:])
    else:runtime._pending_event_ack=(*pending[:5],replace(pending[5]))
    assert runtime._retry_accepted_frida_ack(target)is None
    assert len(rpc)==count
    assert runtime._pending_event_ack is not None


def test_final_fence_receipt_never_uses_ordinary_ack_shortcut():
    runtime,collector,probe,rpc,busy,remote,_=pending_frida()
    pending=runtime._pending_event_ack
    runtime._pending_event_ack=(*pending[:3],{'final':'fence'},*pending[4:])
    count=len(rpc)
    assert runtime._retry_accepted_frida_ack(probe)is None and len(rpc)==count


def test_control_settlement_can_confirm_only_the_existing_prefix_without_fresh_mark():
    runtime,collector,probe,rpc,busy,remote,_=pending_frida()
    busy[0]=True;before=list(rpc)
    assert runtime.settle_event_ack(probe)
    assert rpc[len(before):]==[('ack',1)]
    assert runtime._pending_event_ack is None


def test_uncommitted_or_unavailable_source_cannot_issue_a_receipt():
    probe=FridaEventObserver(SimpleNamespace(core=SimpleNamespace(mc=SimpleNamespace(pid=12))),None)
    probe.status=ObserverStatus.READY
    probe.script=SimpleNamespace(is_destroyed=False,post=lambda *_:None)
    probe.session=SimpleNamespace(is_detached=False)
    assert probe.accepted_ack_receipt(1)is None
    probe._committed_sequence=1
    receipt=probe.accepted_ack_receipt(1)
    assert receipt is not None
    probe.collector.core.mc.pid=13
    assert probe.retry_accepted_ack(1,receipt)is None


def test_retry_never_acknowledges_a_new_unverified_record():
    runtime,collector,probe,rpc,busy,remote,_=pending_frida()
    snapshot=collector._owned_snapshot
    snapshot.frame,snapshot.game_time,snapshot.damage=3,2.,40.
    collector._owned_events.queue.append(event(3,frame=3,game_time=2.,amount=20)) # sequence 2 missing
    runtime.poll(collector,probe)
    assert probe._acknowledged_sequence==remote[0]==1
    assert [seq for op,seq in rpc if op=='ack']==[1,1]
    assert runtime.sessions.event_status=='gap'and runtime.sessions.capture.closed
    assert runtime.sessions.adapter.event_stream.sequence==1
    assert collector._owned_events.queue[0]['sequence']==3


def test_cross_round_busy_releases_only_previous_accepted_prefix():
    runtime,collector,probe,rpc,busy,remote,_=pending_frida()
    collector._owned_snapshot.session='next-logical-battle'
    collector._owned_snapshot.frame,collector._owned_snapshot.game_time=1,0.
    collector._owned_events.queue.append(event(2,frame=1,game_time=0.,amount=100))
    busy[0]=True
    with pytest.raises(CaptureRetry):runtime.poll(collector,probe)
    assert probe._acknowledged_sequence==remote[0]==1
    assert collector._owned_events.queue[0]['sequence']==2
    assert [seq for op,seq in rpc if op=='ack']==[1,1]
    assert runtime.sessions.adapter.event_stream.sequence==1


def test_real_rejected_ack_reply_disables_the_capability_without_another_rpc():
    runtime,collector,probe,rpc,busy,remote,_=pending_frida()
    pending=runtime._pending_event_ack
    def invalid_reply(request):
        rpc.append((request['operation'],request.get('sequence')))
        probe._message({'type':'send','payload':{'kind':'arkdps-event-response','id':request['id'],
            'result':{'acknowledged':0,'error':'refused'}}},None)
    probe.script.post=invalid_reply
    assert runtime._retry_accepted_frida_ack(probe)is False
    assert probe._ack_error is not None
    count=len(rpc)
    assert runtime._retry_accepted_frida_ack(probe)is None
    assert len(rpc)==count and runtime._pending_event_ack is pending
