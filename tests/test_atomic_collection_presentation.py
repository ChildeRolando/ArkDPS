"""One completed collection outcome is one bounded UI queue admission.

The worker tests replace only transport/session acquisition with already accepted
presentation data. They run the real worker, Queue, drain and DesktopState.
No game, GUI, source proof or event ACK is fabricated by this display fixture.
"""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import queue
import sys
import threading
import time

import pytest
import desktop_bridge as bridge
from connection import ConnectionConfig, ConnectionDiagnostics, DetectionStatus
from event_capture import CaptureRetry
from sample_data import sample
from test_desktop_shared_autochess_presentation import state_for


def accepted_sample(*, age=0):
    value = sample()
    value.update(state=2, event_status='recording', event_error=None)
    value['captured_monotonic'] -= age
    return value


@pytest.mark.parametrize('opening_outcome', ['sample', 'retry', 'error'])
def test_real_worker_opening_has_one_admission_even_when_producer_is_blocked(
        tmp_path, monkeypatch, opening_outcome):
    state = bridge.DesktopState(tmp_path / 'state')
    config = ConnectionConfig(platform='native_pc', native_pc_event_backend='native')
    state.connection = state.effective_connection = config
    worker = bridge.CollectionWorker(state)
    old = accepted_sample(age=99)
    old_time = old['captured_monotonic']
    prepared, open_read, blocked, release = [threading.Event() for _ in range(4)]
    seen = []
    fresh = accepted_sample()
    fresh['frame'] += 1

    class BarrierQueue(queue.Queue):
        def put(self, event, *args, **kwargs):
            super().put(event, *args, **kwargs)
            seen.append(event)
            kind, value = event
            if (kind == 'collection_presentation' and prepared.is_set()
                    and (value.sample is fresh or value.outcome == opening_outcome)):
                blocked.set()
                assert release.wait(5), 'consumer did not release completed presentation'
                worker.stop.set()

    worker.events = BarrierQueue()
    diagnostic = ConnectionDiagnostics(status=DetectionStatus.READY_UNIQUE,
        platform='native_pc', version_actual='owned-display-fixture')

    class Session:
        cleanup_errors = {}
        event_status, event_error, observer = 'ready', None, None

        def __init__(self, *_args, audit, **_kwargs):
            self.collector = SimpleNamespace(diagnostics=diagnostic)
            self.step = 0

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            pass

        def close(self):
            pass

        def read(self):
            self.step += 1
            if self.step == 1:
                self.runtime.sessions.state = 'playing'
                return old
            if self.step == 2:
                self.runtime.sessions.state = 'preparing'
                raise CaptureRetry('owned preparation with no new sample')
            if self.step == 3:
                prepared.set()
                assert open_read.wait(5)
                self.runtime.sessions.state = 'playing'
                if opening_outcome == 'retry':
                    raise CaptureRetry('owned clock still awaiting an accepted sample')
                if opening_outcome == 'error':
                    raise ValueError('owned real read failure')
                return fresh
            raise AssertionError('unexpected extra read')

    monkeypatch.setattr(bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(bridge, 'CollectionSession', Session)
    monkeypatch.setitem(sys.modules, 'native_pc_collector', SimpleNamespace(
        NativePcCollector=lambda *_args, **_kwargs: SimpleNamespace(diagnostics=diagnostic)))
    worker.request(config, False)
    thread = threading.Thread(target=worker.run, name='owned-atomic-collection')
    thread.start()
    try:
        assert prepared.wait(5)
        bridge._drain_worker_events(worker, state)
        assert state.snapshot()['status'] == 'preparing' and state.last is old
        open_read.set()
        assert blocked.wait(5), 'worker must publish an atomic completed-read outcome'
        assert worker.events.qsize() == 1
        bridge._drain_worker_events(worker, state)
        expected_status = dict(sample='live', retry='stale', error='disconnected')[opening_outcome]
        assert state.snapshot()['status'] == expected_status
        assert state.last is (fresh if opening_outcome == 'sample' else old)
        if opening_outcome != 'error':
            assert state.lifecycle == 'playing' and not state.error
        else:
            assert state.error == 'owned real read failure'
        assert worker.events.empty()
        assert old['captured_monotonic'] == old_time
    finally:
        open_read.set()
        release.set()
        worker.stop.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not [kind for kind, _ in seen if kind in ('lifecycle', 'sample', 'autochess_scene', 'worker_error', 'error')]
    outcomes = [value for kind, value in seen if kind == 'collection_presentation']
    assert [value.outcome for value in outcomes] == ['sample', 'retry', opening_outcome]
    assert [value.lifecycle for value in outcomes] == [
        'playing', 'preparing', None if opening_outcome == 'error' else 'playing']


@pytest.mark.parametrize('budget', ['count', 'elapsed'])
def test_real_drain_cannot_split_a_presentation_at_either_budget(tmp_path, monkeypatch, budget):
    state = bridge.DesktopState(tmp_path)
    state.effective_connection = ConnectionConfig(platform='native_pc')
    old, fresh = accepted_sample(age=99), accepted_sample()
    state.accept('sample', old)
    state.accept('lifecycle', 'preparing')
    worker = bridge.CollectionWorker(state)
    leading = bridge.BRIDGE_EVENT_LIMIT - 1 if budget == 'count' else 1
    for _ in range(leading):
        worker.events.put(('connection', 'unchanged owned connection'))
    worker.events.put(('collection_presentation', bridge.CollectionPresentation(
        outcome='sample', lifecycle='playing', rejection_changed=True, sample=fresh)))
    worker.events.put(('connection', 'after complete presentation'))
    if budget == 'elapsed':
        ticks = iter((0, 0, bridge.BRIDGE_EVENT_BUDGET + .001))
        monkeypatch.setattr(bridge.time, 'perf_counter', lambda: next(ticks))
    bridge._drain_worker_events(worker, state)
    assert state.status()[0] == 'live' and state.last is fresh
    assert worker.events.qsize() == 1
    assert worker.events.get_nowait() == ('connection', 'after complete presentation')
    assert old['captured_monotonic'] < fresh['captured_monotonic']


def test_one_revision_preserves_lifecycle_before_sample_analysis(tmp_path):
    state = bridge.DesktopState(tmp_path)
    state.effective_connection = ConnectionConfig(platform='native_pc')
    state.accept('lifecycle', 'finalizing')
    revision = state._presentation_revision
    value = accepted_sample()
    update = bridge.CollectionPresentation(outcome='sample', lifecycle='playing',
        rejection_changed=True, sample=value)
    state.accept('collection_presentation', update)
    assert state._presentation_revision == revision + 1
    assert state.lifecycle == 'playing' and state.live_analysis['team_recent_dps'] == 100
    terminal = deepcopy(value)
    terminal['state'] = 3
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='sample', lifecycle='finalizing', sample=terminal))
    assert state.live_analysis['team_recent_dps'] is None
    assert state.last is terminal and state.lifecycle == 'finalizing'


def test_retry_and_error_do_not_manufacture_freshness_or_clear_real_failure(tmp_path):
    state = bridge.DesktopState(tmp_path)
    state.effective_connection = ConnectionConfig(platform='native_pc')
    value = accepted_sample(age=99)
    timestamp = value['captured_monotonic']
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='sample', lifecycle='playing', sample=value))
    assert state.status()[0] == 'stale'
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='error', error='actual controlled read failure'))
    assert state.status()[0] == 'disconnected'
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='retry', lifecycle='preparing', rejection_changed=True))
    assert state.status()[0] == 'disconnected'
    assert state.last is value and value['captured_monotonic'] == timestamp
    fresh = accepted_sample()
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='sample', lifecycle='playing', sample=fresh))
    assert not state.error and state.status()[0] == 'live'


def test_retired_request_update_before_source_marker_is_not_admitted(tmp_path):
    state = bridge.DesktopState(tmp_path)
    config = ConnectionConfig(platform='native_pc')
    state.expect_connection_request(config, 9)
    stale = accepted_sample()
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='sample', lifecycle='playing', sample=stale, rejection_changed=True))
    assert state.last is None and state.lifecycle is None and state._awaiting_source
    state.accept('connection_source', dict(revision=9, platform='native_pc'))
    fresh = accepted_sample()
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='sample', lifecycle='playing', sample=fresh))
    assert state.last is fresh and not state._awaiting_source


def test_atomic_scene_and_team_projection_never_shows_previous_combat_as_current(tmp_path, monkeypatch):
    state, samples, scenes = state_for(tmp_path, monkeypatch, 'native_pc')
    for name in ('defense_hits', 'assist_hits'):
        state.accept('collection_presentation', bridge.CollectionPresentation(
            outcome='sample', lifecycle='playing', scene_changed=True,
            scene=scenes[name], sample=samples[name], rejection_changed=True))
        view = state.snapshot()
        assert state.last is samples[name]
        assert view['multiplayer']['scope'] == samples[name]['multiplayer']['scope']
        assert view['multiplayer']['scope_kind'] == samples[name]['multiplayer']['scope_kind']
    current = state.last
    timestamp = current['captured_monotonic']
    late = deepcopy(samples['defense_hits'])
    late['state'] = 3
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='sample', lifecycle='finalizing', sample=late))
    # Preserve the existing late-terminal scene gate; no old payload becomes live.
    assert state.last is current and state.last['captured_monotonic'] == timestamp
    assert state.snapshot()['multiplayer']['scope'] == samples['assist_hits']['multiplayer']['scope']


@pytest.mark.parametrize('fields', [
    dict(outcome='arbitrary'), dict(outcome='retry', lifecycle='playing', sample={}),
    dict(outcome='error', error='failed', lifecycle='playing'),
    dict(outcome='sample', sample={}),
    dict(outcome='error', error='failed', rejection_changed=True),
])
def test_collection_contract_cannot_become_an_arbitrary_batch(fields):
    with pytest.raises(ValueError):
        bridge.CollectionPresentation(**fields)


def test_retry_rejection_decision_is_part_of_the_same_presentation(tmp_path):
    state = bridge.DesktopState(tmp_path)
    state.effective_connection = ConnectionConfig(platform='native_pc')
    value = accepted_sample()
    state.accept('sample', value)
    rejection = dict(code='mapping_unavailable', reason='current account not proved')
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='retry', lifecycle='playing', rejection_changed=True, sampling_rejection=rejection))
    assert state.status()[0] == 'sampling_rejected'
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='retry', lifecycle='playing'))
    assert state.sampling_rejection == rejection
    state.accept('collection_presentation', bridge.CollectionPresentation(
        outcome='retry', lifecycle='preparing', rejection_changed=True))
    assert state.sampling_rejection is None and state.status()[0] == 'preparing'
    assert state.last is value
