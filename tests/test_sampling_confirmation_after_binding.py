"""A completed native confirmation may still fail host binding; do not reuse it."""
import pytest
from event_capture import CaptureRetry
from event_probe import ObserverReadRetry
from test_sampling_transaction_continuation import world


def test_completed_confirmation_binding_retry_cancels_old_transaction(monkeypatch):
    source,probe,busy,calls=world();source.poll()
    source.advance(1,[source.scoped_hit(1,3,75)]);busy[0]=1
    with pytest.raises(CaptureRetry):source.poll()
    held=source.runtime.feed._sampling_transaction
    assert held is not None and probe._confirm_sample_request=='9'
    bind=probe.binding.bind
    def unavailable(operation,result):
        publication=result.get('native_publication',{})
        if operation=='mark'and publication.get('sample_request')=='9':
            assert probe._confirm_sample_request is None # _request completed before binding
            raise ObserverReadRetry('owned completed checkpoint needs a new host window')
        return bind(operation,result)
    with monkeypatch.context()as binding:
        binding.setattr(probe.binding,'bind',unavailable)
        with pytest.raises(CaptureRetry):source.poll()
    assert source.runtime.feed._sampling_transaction is None
    assert probe._sampling_anchor is None and probe._confirm_sample_request is None
    assert source.commits==[0]and len(source.queue)==1
    reads=len(source.reads)
    assert source.poll().kind=='sample'
    assert len(source.reads)>reads
    assert source.commits==[0,1]
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage']==75
