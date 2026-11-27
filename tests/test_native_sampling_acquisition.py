"""Host acquisition ownership and retry identity; native evidence has separate gates."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from event_observer import ObserverStatus
from event_probe import ObserverReadRetry, reset_probe_cursor
from native_pc.native_hook_source import NativeHookEventProbe
from test_native_pc_native_hooks import collector


def mark(serial=1, *, frame=30, request='1'):
    return dict(sequence=0, epoch=0, frame=frame, game_time=frame/30,
        native_publication=dict(version=1, kind='sampling_checkpoint', token='0:'+str(serial),
            closed_ticket='0', activity_begin='0', activity_end='0', sequence=0, epoch=0,
            sampling_serial=str(serial), sample_request=request))


def probe_with(replies):
    probe=NativeHookEventProbe(collector())
    probe.status=ObserverStatus.READY
    probe._sampling_supported=True
    calls=[]
    def request(operation, **kwargs):
        calls.append((operation,kwargs))
        return deepcopy(replies.pop(0))
    def bind(operation, raw):
        if raw.get('retry'):
            raise ObserverReadRetry('native publication not ready')
        return raw
    probe._raw_request=request
    probe.binding=SimpleNamespace(bind=bind)
    return probe,calls


def test_retry_keeps_the_producer_request_before_binding_yields():
    probe,calls=probe_with([dict(retry=True,native_sample_request='7'),
                            dict(mark(request='7'),native_sample_request='7'),mark(2,request='8')])
    with pytest.raises(ObserverReadRetry):probe.mark()
    opening=probe.mark()
    assert calls[0][1]['sampling']=='fresh' and 'sample_request' not in calls[0][1]
    assert calls[1][1]['sample_request']=='7'
    closing=probe.confirm_mark(opening)
    assert calls[2][1]['sampling']=='confirm'
    assert calls[2][1]['publication_token']==opening['native_publication']['token']
    assert closing['native_publication']['token']!=opening['native_publication']['token']
    assert closing['frame']==opening['frame']


def test_foreign_copied_or_superseded_opening_cannot_confirm():
    probe,calls=probe_with([mark(),mark(2)])
    first=probe.mark()
    with pytest.raises(RuntimeError):probe.confirm_mark(deepcopy(first))
    probe.mark()
    with pytest.raises(RuntimeError):probe.confirm_mark(first)
    assert len(calls)==2


def test_actual_cursor_change_during_acquisition_still_refuses_confirmation():
    probe,calls=probe_with([mark()])
    first=probe.mark();reset_probe_cursor(probe)
    with pytest.raises(Exception) as failure:probe.confirm_mark(first)
    assert failure.value.cursor_failure['reason']=='cursor_lifetime_changed'
    assert len(calls)==1


def test_mode_change_does_not_reuse_an_unfulfilled_request():
    probe,calls=probe_with([dict(retry=True,native_sample_request='7'),mark()])
    mode=[True];probe._autochess_enabled=lambda:mode[0]
    with pytest.raises(ObserverReadRetry):probe.mark()
    mode[0]=False;probe.mark()
    assert 'sample_request' not in calls[1][1]
    assert calls[1][1]['autochess_enabled'] is False


@pytest.mark.parametrize('patch', [dict(kind='callback'),dict(sampling_serial='0'),
    dict(sample_request='01'),dict(activity_end='1'),dict(sampling_serial=True)])
def test_zero_raw_checkpoint_fence_keeps_its_distinct_strict_contract(patch):
    value=mark();value['native_publication'].update(patch)
    with pytest.raises(RuntimeError):NativeHookEventProbe._validated_publication_token(value)


def test_zero_raw_prefix_is_valid_only_for_a_proven_checkpoint_fence_shape():
    value=mark();assert NativeHookEventProbe._validated_publication_token(value)=='0:1'
    value['native_publication'].pop('kind')
    with pytest.raises(RuntimeError):NativeHookEventProbe._validated_publication_token(value)
