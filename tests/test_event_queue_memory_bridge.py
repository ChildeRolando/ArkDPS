"""Saved queue preference does not reconnect an existing observer."""
from dataclasses import replace
import pytest
from desktop_bridge import DesktopState, CollectionWorker, PROTOCOL_VERSION

def native_state(path):
    state=DesktopState(path)
    state.connection=replace(state.connection,platform='native_pc',native_pc_event_backend='native')
    state.effective_connection=state.connection
    return state

def save(state,value):
    return state.command(dict(version=PROTOCOL_VERSION,op='event_queue_memory',value=value))

def test_saved_budget_clears_only_budget_override_without_reconnect(tmp_path):
    state=native_state(tmp_path);worker=CollectionWorker(state)
    state._event_queue_memory_override=512
    state._event_queue_memory_status_reader=lambda:dict(effective_mib=512,applicable=True,supported=True)
    assert state.event_queue_memory_view()['pending_restart'] is False
    assert state.event_queue_memory_view()['saved_mib']==1024
    save(state,1536)
    assert worker.requests.empty()
    assert state.connection.event_queue_memory_mib==1536
    assert state._event_queue_memory_override is None
    assert state.event_queue_memory_view()['effective_mib']==512
    assert state.event_queue_memory_view()['pending_restart'] is True
    assert DesktopState(tmp_path).connection.event_queue_memory_mib==1536
    assert worker._queue_memory_requested()==1536

@pytest.mark.parametrize('bad',[True,False,511,2049,1024.0,'1024',None])
def test_invalid_budget_is_transactional(tmp_path,bad):
    state=native_state(tmp_path);before=state.connection
    with pytest.raises((ValueError,TypeError)):save(state,bad)
    assert state.connection==before

@pytest.mark.parametrize('status,supported',[(None,None),({'applicable':True,'supported':False,'effective_mib':None},False),({'applicable':True,'supported':True,'effective_mib':True},True)])
def test_unknown_effective_is_not_defaulted_or_pending(tmp_path,status,supported):
    state=native_state(tmp_path);state._event_queue_memory_status_reader=lambda:status
    view=state.event_queue_memory_view()
    assert view['effective_mib'] is None and view['pending_restart'] is False
    assert view['supported'] is supported

def test_save_failure_keeps_previous_preference_and_override(tmp_path,monkeypatch):
    state=native_state(tmp_path);state._event_queue_memory_override=512
    def fail(config):raise OSError('disk unavailable')
    monkeypatch.setattr(state.connection_store,'save',fail)
    with pytest.raises(OSError):save(state,1536)
    assert state.connection.event_queue_memory_mib==1024
    assert state._event_queue_memory_override==512
