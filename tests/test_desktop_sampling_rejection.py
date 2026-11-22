"""Nonfatal display diagnostics; no target, process, ACK or reconnect work."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

import desktop_bridge as bridge
from battle_lifecycle import MappingRetry
from connection import ConnectionConfig, ConnectionDiagnostics, DetectionStatus
from event_capture import CaptureRetry
from sample_data import sample


REJECTION = dict(code='mapping_unavailable', reason='玩家归属证据尚未确认')


def test_rejection_is_nonfatal_and_retains_raw_values_and_expiry(tmp_path, monkeypatch):
    state = bridge.DesktopState(tmp_path)
    state.accept('sampling_rejection', REJECTION)
    current = state.snapshot()
    assert current['status'] == 'sampling_rejected' and REJECTION['reason'] in current['status_text']
    assert current['sampling_rejection'] == REJECTION and current['error'] == ''
    assert current['panels'][0]['entries'] == [] and state.warnings == []
    raw = sample()
    original = deepcopy(raw)
    state.accept('sample', raw)
    assert state.sampling_rejection is None
    monkeypatch.setattr(bridge, 'observation_age', lambda _: 3)
    state.accept('sampling_rejection', REJECTION)
    current = state.snapshot()
    assert current['status'] == 'stale' and '数据已过期' in current['status_text']
    assert REJECTION['reason'] in current['status_text'] and current['panels'][0]['footer_total'] == 1730
    state.accept('lifecycle', 'preparing')  # A retained lifecycle is not a successful observation.
    assert state.sampling_rejection == REJECTION
    state.accept('error', 'transport denied')
    state.accept('sampling_rejection', None)
    assert state.error == 'transport denied' and state.status()[0] == 'disconnected'
    assert raw == original and state.last is raw


def test_latest_source_marker_blocks_old_queued_rejections_and_clears_only_display(tmp_path):
    state = bridge.DesktopState(tmp_path)
    raw = sample()
    state.accept('sample', raw)
    state.accept('sampling_rejection', REJECTION)
    first = ConnectionConfig(platform='native_pc', native_game_root='root-a')
    second = replace(first, native_game_root='root-b')
    state.expect_connection_request(first, 2)
    state.expect_connection_request(second, 3)
    assert state.sampling_rejection is None and state.last is raw
    for marker in ({'revision': 2, 'platform': 'native_pc'}, {'revision': 3, 'platform': 'mumu'}):
        state.accept('connection_source', marker)
        state.accept('sampling_rejection', REJECTION)
        assert state.sampling_rejection is None and state._awaiting_source
    state.accept('connection_source', {'revision': 3, 'platform': 'native_pc'})
    state.accept('sampling_rejection', REJECTION)
    assert state.sampling_rejection == REJECTION and state.last is raw
    state.expect_connection_request(second, 4)  # Same-owner explicit rescan is also a new display source.
    state.accept('sampling_rejection', REJECTION)
    assert state.sampling_rejection is None
    state._clear_live_source()
    assert state.sampling_rejection is None


def run_worker(tmp_path, monkeypatch, actions):
    state = bridge.DesktopState(tmp_path / 'state')
    worker = bridge.CollectionWorker(state)
    worker.stop.wait = lambda *_: False
    config = ConnectionConfig(auto_detect=False, serial='owned-fixture')
    diagnostic = ConnectionDiagnostics(status=DetectionStatus.READY_UNIQUE, platform='mumu',
                                      selected_serial='owned-fixture', version_actual='fixture')
    calls = []
    remaining = iter(actions)

    class Session:
        cleanup_errors = {}
        event_status, event_error = 'unavailable', None
        observer = None

        def __init__(self, *_args, audit, **_kwargs):
            self.audit = audit
            self.collector = SimpleNamespace(diagnostics=diagnostic)
            calls.append('session')

        def enable_lifecycle(self, runtime):
            self.runtime = runtime
            if 'real_terminal' in actions:
                from test_native_pc_autochess_acquisition import ScopedNativeBattle
                self.battle = ScopedNativeBattle()
                assert self.battle.poll().kind == 'sample'
                self.runtime = self.battle.runtime
            self.runtime.record = lambda observation: self.audit('lifecycle_observation', observation)

        def connect(self):
            calls.append('connect')

        def read(self):
            action = next(remaining, 'stop')
            if action == 'stop':
                worker.stop.set()
                raise CaptureRetry('ordinary clock race')
            if action == 'switch':
                worker.request(config, False)
                raise CaptureRetry('ordinary clock race')
            if action == 'sample':
                return sample()
            if action == 'real_terminal':
                self.battle.advance(2, state=3)
                assert self.battle.poll().kind == 'terminal'
                raise CaptureRetry('owned successful terminal observation')
            if action in ('map', 'other'):
                observation = self.runtime.feed._emit('sample')
                self.runtime.record(observation)
                raise MappingRetry(REJECTION['reason'] if action == 'map' else '新的归属拒收原因', observation)
            if action == 'ordinary':
                raise CaptureRetry('baseline crossed an event or game frame')
            if action in ('preparing', 'waiting', 'end', 'gap'):
                if action == 'preparing':
                    self.runtime.feed.generation += 1
                observation = self.runtime.feed._emit(action, reason='owned_fixture')
                self.runtime._consume(observation)
                raise CaptureRetry(f'lifecycle: {action}/owned_fixture')
            if action == 'wrong_feed':
                observation = replace(self.runtime.feed._emit('waiting'), feed='foreign-feed')
                self.runtime.record(observation)
                raise CaptureRetry('ordinary clock race')
            raise AssertionError(action)

        def close(self):
            calls.append('close')

    monkeypatch.setattr(bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(bridge, 'CollectionSession', Session)
    monkeypatch.setattr(bridge, 'resolve_configured', lambda _: SimpleNamespace(
        adb_path='owned-fixture', serial='owned-fixture', diagnostics=diagnostic))
    worker.request(config, False)
    worker.run()
    events = list(worker.events.queue)
    assert not any(kind in ('error', 'worker_error') for kind, _ in events)
    assert worker.requests.empty()
    return state, worker, events, calls


def test_mapping_dedup_gaps_and_old_lifecycle_cannot_clear_but_real_preparing_can(tmp_path, monkeypatch):
    state, worker, events, calls = run_worker(tmp_path, monkeypatch,
        ['map', 'map', 'ordinary', 'gap', 'wrong_feed', 'other', 'preparing', 'map', 'ordinary'])
    values = [value for kind, value in events if kind == 'sampling_rejection']
    assert values == [None, REJECTION, dict(code='mapping_unavailable', reason='新的归属拒收原因'), None, REJECTION]
    for kind, value in events:
        state.accept(kind, value)
    assert state.sampling_rejection == REJECTION and state.status()[0] == 'sampling_rejected'
    assert calls == ['session', 'connect', 'close'] and worker.pc_runtime is None


@pytest.mark.parametrize('clear', ['waiting', 'end', 'sample', 'real_terminal'])
def test_successfully_consumed_waiting_end_or_sample_clears_rejection(tmp_path, monkeypatch, clear):
    prefix = ['preparing'] if clear == 'end' else []
    state, _, events, calls = run_worker(tmp_path, monkeypatch, prefix + ['map', clear])
    assert [value for kind, value in events if kind == 'sampling_rejection'] == [None, REJECTION, None]
    for kind, value in events:
        state.accept(kind, value)
    assert state.sampling_rejection is None and calls == ['session', 'connect', 'close']


def test_new_source_republishes_identical_reason_after_its_marker(tmp_path, monkeypatch):
    _, _, events, calls = run_worker(tmp_path, monkeypatch, ['map', 'switch', 'map'])
    selected = [(kind, value) for kind, value in events if kind in ('connection_source', 'sampling_rejection')]
    assert [value for kind, value in selected if kind == 'sampling_rejection'] == [None, REJECTION, None, REJECTION]
    assert [value['revision'] for kind, value in selected if kind == 'connection_source'] == [1, 2]
    assert calls == ['session', 'connect', 'close'] * 2


def test_plain_cross_frame_retry_never_becomes_a_connection_or_mapping_error(tmp_path, monkeypatch):
    state, _, events, calls = run_worker(tmp_path, monkeypatch, ['ordinary', 'ordinary'])
    assert [value for kind, value in events if kind == 'sampling_rejection'] == [None]
    for kind, value in events:
        state.accept(kind, value)
    assert state.sampling_rejection is None and state.error == '' and state.status()[0] == 'waiting'
    assert calls == ['session', 'connect', 'close']
