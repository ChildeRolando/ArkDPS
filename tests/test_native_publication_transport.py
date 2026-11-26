"""Native reads pin the exact closed publication selected by the bound mark."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from native_pc.native_hook_source import NativeHookEventProbe


def publication(sequence=2, token='11:1'):
    return dict(version=1, token=token, closed_ticket=token.split(':')[0], activity_begin='20',
                activity_end='21', sequence=sequence, epoch=3)


def probe(monkeypatch, replies):
    source = NativeHookEventProbe(SimpleNamespace())
    calls = []

    def request(operation, **arguments):
        calls.append((operation, arguments))
        return deepcopy(replies.pop(0))

    monkeypatch.setattr(source, '_raw_request', request)
    monkeypatch.setattr(source.binding, 'bind', lambda operation, raw: raw)
    return source, calls


def test_successful_mark_pins_bounded_reads_and_explicit_baseline(monkeypatch):
    mark = dict(sequence=2, epoch=3, native_publication=publication(), native_async_capture=dict(version=1))
    source, calls = probe(monkeypatch, [mark, mark, mark])
    source.mark()
    source.read_baseline(2)
    source.read_through(2)
    assert calls[0][1]['native_publication_version'] == 1
    assert calls[1][1] == dict(timeout=None, after=2, through=2, baseline=True, publication_token='11:1')
    assert calls[2][1]['publication_token'] == '11:1'
    assert source._committed_sequence == source._acknowledged_sequence == 0


@pytest.mark.parametrize('change', [
    {'version': True}, {'closed_ticket': '12'}, {'activity_end': '22'}, {'sequence': 3},
    {'epoch': 4}, {'token': '11:0'}, {'closed_ticket': '18446744073709551616'},
])
def test_malformed_closed_prefix_cannot_pin_a_read(monkeypatch, change):
    fence = publication()
    fence.update(change)
    source, _ = probe(monkeypatch, [dict(sequence=2, epoch=3, native_publication=fence)])
    with pytest.raises(RuntimeError, match='发布前缀'):
        source.mark()
    assert source._publication_token is None


def test_read_cannot_silently_select_a_newer_publication(monkeypatch):
    source, _ = probe(monkeypatch, [dict(sequence=2, epoch=3, native_publication=publication()),
                                  dict(sequence=2, epoch=3, native_publication=publication(token='12:2'))])
    source.mark()
    with pytest.raises(RuntimeError, match='更换'):
        source.read_through(2)
    assert source._committed_sequence == 0


def test_async_success_requires_publication_and_retry_cannot_replace_the_cursor(monkeypatch):
    source, _ = probe(monkeypatch, [dict(sequence=2, epoch=3, native_publication=publication()),
                                  dict(retry=True, native_async_capture=dict(version=1)),
                                  dict(sequence=2, epoch=3, native_async_capture=dict(version=1))])
    source.mark()
    source.mark()
    assert source._publication_token == '11:1'
    with pytest.raises(RuntimeError, match='发布前缀'):
        source.mark()
    assert source._publication_token == '11:1'
