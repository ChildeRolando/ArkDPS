"""Same-source manual retry keeps owned one-shot transport and pending proofs."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from collection_presentation_events import presentation_events

import desktop_bridge as bridge
import native_pc_collector
from collection_session import CollectionSession
from connection import ConnectionConfig
from event_capture import LiveEventCapture
from event_observer import NativePcFridaObserver, ObserverStatus
from event_probe import StreamGap
from native_pc.native_hook_source import NativeHookEventProbe
from test_postcommit_feature_recovery import owned_session


def configured_worker(tmp_path):
    source, session, probe, pipe = owned_session(tmp_path)
    worker = bridge.CollectionWorker(bridge.DesktopState(tmp_path / 'state'))
    worker.session = session
    config = ConnectionConfig(platform='native_pc', native_pc_event_backend='native',
                              native_game_root='owned-offline')
    options = (config.to_dict(), True, worker.endpoint, None, None)
    return worker, source, session, probe, pipe, config, options


def test_same_source_eligibility_is_read_only_and_uses_real_transport_proof(tmp_path):
    worker, source, session, probe, pipe, config, options = configured_worker(tmp_path)
    pending = session.runtime._pending_event_ack = object()
    transaction = session.runtime.feed._sampling_transaction = {'owned': 'pending'}
    assert worker._can_retry_owned_input(config, True, options)
    assert session.runtime._pending_event_ack is pending
    assert session.runtime.feed._sampling_transaction is transaction
    assert source.commits == [] and pipe.handle == 1 and not pipe._poisoned


@pytest.mark.parametrize('fault', ['config', 'backend', 'feed', 'platform', 'budget', 'disabled',
    'endpoint', 'custom_factory', 'arkframework', 'failed', 'stopped', 'incompatible',
    'poisoned', 'closed_pipe', 'pid', 'birth', 'dead', 'unknown_alive', 'wrong_collector',
    'unknown_provider', 'frida_observer', 'unknown_pipe', 'restartable', 'session_closed', 'unbound_options'])
def test_unproven_or_changed_source_keeps_original_reconnect_policy(tmp_path, fault):
    worker, source, session, probe, pipe, config, options = configured_worker(tmp_path)
    enabled = True
    if fault == 'config': config = replace(config, native_game_root='other-root')
    elif fault == 'backend': config = replace(config, native_pc_event_backend='frida')
    elif fault == 'feed': config = replace(config, native_pc_event_feed='owned.jsonl')
    elif fault == 'platform': config = replace(config, platform='mumu')
    elif fault == 'budget': config = replace(config, event_queue_memory_mib=512)
    elif fault == 'disabled': enabled = False
    elif fault == 'endpoint': worker.endpoint = '127.0.0.1:27044'
    elif fault == 'custom_factory': worker.native_pc_observer_factory = lambda *a: probe
    elif fault == 'arkframework': worker.arkframework_root = Path('owned-arkframework')
    elif fault == 'failed': probe.status = ObserverStatus.FAILED
    elif fault == 'stopped': probe.status = ObserverStatus.STOPPED
    elif fault == 'incompatible': probe.status = ObserverStatus.INCOMPATIBLE
    elif fault == 'poisoned': pipe._poisoned = True
    elif fault == 'closed_pipe': pipe.handle = None
    elif fault == 'pid': source.collector.process.pid += 1
    elif fault == 'birth': source.collector.process.process_instance = (10, 999999)
    elif fault == 'dead': source.collector.process.process_alive = lambda: False
    elif fault == 'unknown_alive': source.collector.process.process_alive = lambda: None
    elif fault == 'wrong_collector': probe.collector = SimpleNamespace()
    elif fault == 'unknown_provider':
        session.observer = SimpleNamespace(status=ObserverStatus.READY, can_restart_in_process=False,
            collector=session.collector, has_live_owned_transport=lambda: True)
    elif fault == 'frida_observer':
        session.observer = NativePcFridaObserver(session.collector)
        session.observer.status = ObserverStatus.READY
    elif fault == 'unknown_pipe': probe._pipe = SimpleNamespace(handle=1, _poisoned=False)
    elif fault == 'restartable': probe.can_restart_in_process = True
    elif fault == 'session_closed': session.closed = True
    elif fault == 'unbound_options': options = None
    assert not worker._can_retry_owned_input(config, enabled, options)
    assert source.commits == []


def test_mutating_original_config_cannot_change_frozen_connection_options(tmp_path):
    worker, _, _, _, _, config, options = configured_worker(tmp_path)
    config.native_game_root = 'changed-in-place'
    assert not worker._can_retry_owned_input(config, True, options)


def run_owned_worker(tmp_path, monkeypatch, fault='healthy', *, baseline_run=None):
    source, _, probe, pipe = owned_session(tmp_path)
    state = bridge.DesktopState(tmp_path / 'state')
    worker = bridge.CollectionWorker(state)
    if baseline_run is not None:
        worker.run = baseline_run.__get__(worker, type(worker))
    clock, trace, instances = [0.], [], []
    requested, gap_fired, held, held_seen = [False], [False], [], []
    ack_allowed, held_acks, held_ack_seen = [fault != 'pending_ack'], [], []
    monkeypatch.setattr(bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(bridge, 'time', SimpleNamespace(monotonic=lambda: clock[0],
                                                      perf_counter=lambda: clock[0]))
    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', lambda *a, **k: source.collector)
    monkeypatch.setattr(worker, '_pc_provider', lambda *a, **k: None)
    # Keep the same real sampling producer fixture; only inject a busy receipt.
    wire = pipe.request
    busy = [fault == 'pending_confirmation']
    def request(op, **kwargs):
        if busy[0] and kwargs.get('sampling') == 'confirm' and instances and instances[0].reads == 2:
            busy[0] = False
            return dict(retry=True, native_sample_request='9')
        return wire(op, **kwargs)
    pipe.request = request
    commit = probe.commit_events
    def acknowledge(sequence):
        if sequence and not ack_allowed[0]:
            return False
        return commit(sequence)
    probe.commit_events = acknowledge
    def wait(delay):
        clock[0] += max(delay, .05)
        if instances and instances[0].reads >= 2 and not requested[0]:
            requested[0] = True
            runtime = instances[0].runtime
            held.append(runtime.feed._sampling_transaction)
            held_acks.append(runtime._pending_event_ack)
            trace.append(('rescan', worker._request_revision + 1))
            config, enabled = worker.effective
            if fault == 'changed_config':
                config = replace(config, native_game_root='changed-root')
            elif fault == 'new_birth':
                source.collector.process.process_instance = (10, 999999)
            elif fault == 'failed':
                probe.status = ObserverStatus.FAILED
            revision = worker.request(config, enabled)
            state.expect_connection_request(config, revision)
            ack_allowed[0] = True
        if clock[0] > 12:
            worker.stop.set()
        return worker.stop.is_set()
    worker.stop.wait = wait
    def stop():
        trace.append(('stop', instances[0].reads))
        probe.status, pipe.handle = ObserverStatus.STOPPED, None
    probe.stop = stop
    class OwnedSession(CollectionSession):
        def __init__(self, *args, collector, **kwargs):
            super().__init__(None, None, collector=collector, **kwargs)
            self.reads = 0
            instances.append(self)
        def enable_lifecycle(self, runtime):
            super().enable_lifecycle(runtime)
            source.runtime = runtime
        def connect(self):
            trace.append(('connect', len(instances)))
            if probe.status != ObserverStatus.READY:
                raise RuntimeError('owned one-shot source was already stopped')
            self.observer, self.event_status = probe, 'ready'
            self.capture = LiveEventCapture(self.collector, probe, audit=self.audit)
        def read(self):
            self.reads += 1
            trace.append(('read', self.reads))
            if self.reads == 3 and held:
                held_seen.append(self.runtime.feed._sampling_transaction is held[0])
                held_ack_seen.append(self.runtime._pending_event_ack is held_acks[0])
            source.advance(self.reads - 1, [source.scoped_hit(1, 3, 75)] if self.reads == 2 else ())
            if fault == 'host_gap' and self.reads == 2 and not gap_fired[0]:
                gap_fired[0] = True
                raise StreamGap('owned host acquisition rejection; explicit injection')
            result = super().read()
            if self.reads >= 3:
                worker.stop.set()
            return result
    monkeypatch.setattr(bridge, 'CollectionSession', OwnedSession)
    worker.request(ConnectionConfig(platform='native_pc', native_pc_event_backend='native',
                                    native_game_root='owned-offline'), True)
    worker.run()
    events = list(presentation_events(worker.events.queue))
    report = dict(trace=trace, session_count=len(instances), requested=requested[0],
        read3=('read', 3) in trace, commits=list(source.commits), queue=len(source.queue),
        held_transaction_present=bool(held and held[0] is not None), held_seen=held_seen,
        held_ack_present=bool(held_acks and held_acks[0] is not None), held_ack_seen=held_ack_seen,
        errors=[str(value) for kind, value in events if kind in ('error', 'worker_error')],
        connection_markers=[value for kind, value in events if kind == 'connection_source'],
        final_damage=source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'])
    return report, events, state


@pytest.mark.parametrize('fault', ['healthy', 'host_gap', 'pending_confirmation', 'pending_ack'])
def test_manual_rescan_is_a_retry_of_same_owned_source_and_preserves_pending(tmp_path, monkeypatch, fault):
    report, events, state = run_owned_worker(tmp_path, monkeypatch, fault)
    assert report['requested'] and report['session_count'] == 1 and report['read3'], report
    assert all(number >= 3 for operation, number in report['trace'] if operation == 'stop'), report
    assert report['commits'][-1] == 1 and report['queue'] == 0 and report['final_damage'] == 75
    assert report['held_seen'] == [True]
    assert report['held_ack_seen'] == [True]
    if fault == 'pending_confirmation':
        assert report['held_transaction_present'] and not report['errors']
    if fault == 'host_gap':
        assert report['errors'] == ['owned host acquisition rejection; explicit injection']
    if fault == 'pending_ack':
        assert report['held_ack_present'] and not report['errors']
    marker = next(index for index, (kind, value) in enumerate(events)
                  if kind == 'connection_source' and value['revision'] == 2)
    assert any(kind == 'sample' for kind, _ in events[marker + 1:])
    # UI only leaves its new-revision wait after the real marker/sample, never
    # because the rescan branch manufactured an observer-ready receipt.
    for kind, value in events:
        state.accept(kind, value)
    assert not state._awaiting_source and state.last is not None


@pytest.mark.parametrize('fault', ['changed_config', 'new_birth', 'failed'])
def test_manual_request_reconnects_when_source_no_longer_matches(tmp_path, monkeypatch, fault):
    report, _, _ = run_owned_worker(tmp_path, monkeypatch, fault)
    assert report['requested'] and report['session_count'] > 1 and not report['read3'], report
    assert ('stop', 2) in report['trace']
