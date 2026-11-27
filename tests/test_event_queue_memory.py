"""Saved process-wide queue preference and real initialize request plumbing."""
from dataclasses import replace
import pytest
from connection import (ConnectionConfig, ConnectionConfigStore, event_queue_memory_mib,
                        event_queue_memory_argument)
from collection_session import CollectionSession
from test_native_pc_native_hooks import setup_probe
from test_collection_session import Collector, LiveObserver

@pytest.mark.parametrize('budget',[512,1024,2048])
def test_queue_setting_round_trip(tmp_path,budget):
    store=ConnectionConfigStore(tmp_path/'connection.json')
    store.save(replace(ConnectionConfig(),event_queue_memory_mib=budget))
    assert store.load().config.event_queue_memory_mib==budget
    assert event_queue_memory_argument(str(budget))==budget

def test_old_connection_defaults_to_1024():
    assert ConnectionConfig.from_dict({'version':1}).event_queue_memory_mib==1024

@pytest.mark.parametrize('bad',[True,False,None,511,2049,1024.0,'1024'])
def test_queue_setting_rejects_invalid_value(bad):
    with pytest.raises(ValueError):event_queue_memory_mib(bad)
    with pytest.raises(ValueError):ConnectionConfig.from_dict({'event_queue_memory_mib':bad})

@pytest.mark.parametrize('budget',[512,1024,2048])
def test_initialize_carries_budget_and_only_actual_reply_confirms_effective(setup_probe,budget):
    make,_,pipes=setup_probe
    probe=make();probe.set_event_queue_memory_mib(budget)
    assert probe.queue_memory_status()['effective_mib']is None
    factory=probe.pipe_factory
    def configured(name,pid):
        pipe=factory(name,pid)
        pipe.hello['ready'].update(event_queue_memory_version=1,effective_event_queue_memory_mib=512)
        return pipe
    probe.pipe_factory=configured;probe.start()
    assert pipes[0].calls[0][1]['event_queue_memory_mib']==budget
    assert probe.queue_memory_status()==dict(configured_mib=budget,effective_mib=512,applicable=True,supported=True)
    with pytest.raises(RuntimeError):probe.set_event_queue_memory_mib(2048)
    assert probe.queue_memory_status()['effective_mib']==512
    probe.stop()

def test_old_native_component_keeps_actual_budget_unknown(setup_probe):
    make,_,pipes=setup_probe;probe=make();probe.start()
    assert pipes[0].calls[0][1]['event_queue_memory_mib']==1024
    assert probe.queue_memory_status()['effective_mib']is None
    assert not probe.queue_memory_status()['supported']
    probe.stop()

def test_changed_preference_does_not_reconfigure_healthy_observer():
    selected=[1024];calls=[]
    class Observer(LiveObserver):
        def set_event_queue_memory_mib(self,value):self.budget=value;calls.append(('budget',value))
    collector=Collector([])
    session=CollectionSession('adb','serial',collector=collector,observer_factory=Observer,
                              event_queue_memory_mib=lambda:selected[0])
    assert session._attach_observer()
    observer=session.observer;selected[0]=2048
    assert observer.budget==1024 and session.observer is observer
    assert calls==[('budget',1024)]
    # Explicit lifecycle retirement/new observer is the only point sampling the new preference.
    assert session._attach_observer()
    assert session.observer is not observer and session.observer.budget==2048
    assert calls==[('budget',1024),('budget',2048)]

@pytest.mark.parametrize('value',[512,1024,2048])
def test_cli_parser_queue_budget(value):
    from app import parser_for
    assert parser_for().parse_args(['--event-queue-memory-mib',str(value)]).event_queue_memory_mib==value

@pytest.mark.parametrize('value',['511','2049','true','1024.0','-1'])
def test_cli_parser_rejects_invalid_budget(value):
    from app import parser_for
    with pytest.raises(SystemExit):parser_for().parse_args(['--event-queue-memory-mib',value])

def test_headless_capture_forwards_queue_budget(tmp_path,monkeypatch):
    import capture_cli
    from test_capture_cli import options,Session,stub_git_and_clock
    stub_git_and_clock(monkeypatch)
    args=options(tmp_path);args.event_queue_memory_mib=2048
    capture_cli.run_capture(args,session_factory=Session)
    assert Session.instances[-1].event_queue_memory_mib==2048

def queue_fault():
    return dict(version=1,failure_code=4,ticket='71',lane=0,attempted_bytes=100,
        private_queue=dict(slot_capacity=10,arena_capacity=1024,used_slots=10,used_bytes=1000),
        spill=dict(configured_banks=2,prepared_banks=2,free_banks=0,owned_banks=1,
            foreign_owned_banks=1,busy_banks=0,unprepared_banks=0,slot_rejections=2,
            byte_rejections=0,oversize_rejections=0))

def error_pipe(reply):
    import json
    from threading import Lock
    from native_pc.native_hook_source import NativePipe
    pipe=NativePipe.__new__(NativePipe)
    pipe._lock,pipe._id,pipe._poisoned=Lock(),0,False
    pipe.handle,pipe._buffer=1,bytearray((json.dumps(dict(id=1,**reply))+'\n').encode())
    pipe._io=lambda operation,buffer,size,deadline:size
    return pipe

def test_fixed_queue_error_is_cleaned_and_keeps_poison_and_exception_category():
    import json
    data=queue_fault();data['private_token']='SECRET';data['spill']['game_text']='SECRET'
    pipe=error_pipe(dict(error='native capture failed: code=4',queue_failure=data))
    with pytest.raises(RuntimeError)as caught:pipe.request('mark')
    assert type(caught.value)is RuntimeError
    assert caught.value.queue_failure==queue_fault()
    assert 'SECRET'not in json.dumps(caught.value.queue_failure)
    assert pipe._poisoned
    with pytest.raises(RuntimeError,match='closed or has an incomplete request'):pipe.request('ack',sequence=1)

@pytest.mark.parametrize('data',[{'failure_code':4},dict(queue_fault(),failure_code=6),dict(queue_fault(),ticket='01'),dict(queue_fault(),lane=True)])
def test_malformed_failure_detail_does_not_enter_exception(data):
    pipe=error_pipe(dict(error='native capture failed: code=4',queue_failure=data))
    with pytest.raises(RuntimeError)as caught:pipe.request('mark')
    assert not hasattr(caught.value,'queue_failure')
    assert pipe._poisoned

def test_success_envelope_must_not_carry_failure_detail():
    pipe=error_pipe(dict(result={},queue_failure=queue_fault()))
    with pytest.raises(RuntimeError,match='envelope mismatch'):pipe.request('mark')
    assert pipe._poisoned

def test_fixed_queue_failure_joins_existing_failure_audit(setup_probe):
    from metrics import sanitize_native_queue_failure
    make,_,_=setup_probe;probe=make();records=[]
    pipe=error_pipe(dict(error='native capture failed: code=4',queue_failure=queue_fault()))
    from event_observer import ObserverStatus
    probe.status=ObserverStatus.READY
    probe._pipe=pipe;probe.set_request_timing_audit(lambda kind,data:records.append((kind,data)))
    with pytest.raises(RuntimeError):probe._raw_request('mark')
    assert records[-1][0]=='native_hook_request_failed'
    assert records[-1][1]['queue_failure']==sanitize_native_queue_failure(queue_fault())

@pytest.mark.parametrize('version,effective',[(True,512),(2,1024),(1,511),(1,2049),(1,True),(1,None)])
def test_invalid_actual_budget_receipt_is_not_claimed_effective(setup_probe,version,effective):
    make,_,_=setup_probe;probe=make();factory=probe.pipe_factory
    def invalid(name,pid):
        pipe=factory(name,pid);pipe.hello['ready'].update(event_queue_memory_version=version,
            effective_event_queue_memory_mib=effective);return pipe
    probe.pipe_factory=invalid
    with pytest.raises((RuntimeError,ValueError)):probe.start()
    assert probe.queue_memory_status()['effective_mib']is None
    assert not probe.queue_memory_status()['supported']
