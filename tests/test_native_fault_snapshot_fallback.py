"""Independent native snapshots survive an owned event-transport failure."""
from copy import deepcopy
from dataclasses import asdict
import ctypes
import json
from threading import Lock
from types import SimpleNamespace

import pytest

from autochess_context import NATIVE_AUTOCHESS_CHARACTER_COUNTERS
from battle_lifecycle import BattleRuntime
from collection_session import CollectionSession
from collector import ProcessChanged, UnsupportedVersion
from connection import VersionStatus
from event_capture import CaptureRetry
from event_observer import ObserverStatus
from native_pc.native_hook_source import NativeHookEventProbe, NativePipe
from test_event_queue_memory import error_pipe
from test_multiplayer_native_context import hit
from test_native_pc_autochess_collector_gate import fixture


def owned_pipe(reply):
    """Run the real envelope/parser/poison path over an owned byte response."""
    pipe = NativePipe.__new__(NativePipe)
    pipe._lock, pipe._id, pipe._poisoned = Lock(), 0, False
    pipe.handle, pipe._buffer = 1, bytearray()
    pipe.owned_reply = reply

    def io(operation, buffer, size, deadline):
        assert operation == 'WriteFile'
        command = json.loads(ctypes.string_at(buffer, size))
        arguments = {key: value for key, value in command.items()
                     if key not in ('id', 'operation')}
        response = pipe.owned_reply(command['operation'], arguments)
        pipe._buffer.extend((json.dumps(dict(id=command['id'], **response)) + '\n').encode())
        return size

    pipe._io = io
    return pipe


def owned_session(tmp_path, *, missing_roster=False, observations=None):
    collector, process, original, acknowledgements, records = fixture(
        tmp_path, failed_field='roster' if missing_roster else None)
    acquire = process.sample

    def independent_sample(**kwargs):
        report = acquire(**kwargs)
        report['sample']['actor_rows'][0]['damage'] = 90000 + process.frame
        report['sample']['total_damage'] = 90000 + process.frame
        return report

    process.sample = independent_sample
    source = NativeHookEventProbe(collector)
    source.status = ObserverStatus.READY
    source._owned_process_instance = process.process_instance

    def request(operation, **arguments):
        if operation == 'ack':
            acknowledgements.append(arguments['sequence'])
            return dict(acknowledged=arguments['sequence'])
        result = (original.mark() if operation == 'mark'
                  else original.read_through(arguments['through']))
        result.update(native_generation=1, native_frame=process.frame,
                      native_fixed_time=str(int(process.when * (1 << 32))),
                      native_process_pid=process.pid, native_stage_id=process.stage,
                      active_hooks=[])
        return result

    source._pipe = SimpleNamespace(request=request)
    runtime = BattleRuntime()
    if observations is not None:
        runtime.record = lambda observation: observations.append(
            json.loads(json.dumps(asdict(observation), default=sorted)))
    assert runtime.poll(collector, source).kind == 'sample'
    session = CollectionSession(None, None, collector=collector, frida_remote=None)
    session.runtime = runtime
    session._pending_history = runtime.pending_history
    session.capture = runtime.sessions.capture
    session.observer = source
    session.event_status = 'ready'
    collector.event_identity_only = True
    cleanup = []
    source.stop = lambda: cleanup.append('stop')
    source.start = lambda: cleanup.append('start')
    return session, process, source, records, acknowledgements, cleanup


def fail_event_pipe(source, code=6):
    source._pipe = error_pipe(dict(error=f'native capture failed: code={code}'))
    with pytest.raises(RuntimeError):
        source.mark()
    assert source.status is ObserverStatus.FAILED and source._pipe._poisoned


@pytest.mark.parametrize('missing_roster', [False, True])
@pytest.mark.parametrize('code', [4, 6, 9])
def test_failed_event_source_uses_existing_counters_without_restarting_or_ack(
        tmp_path, missing_roster, code):
    session, process, source, records, acknowledgements, cleanup = owned_session(
        tmp_path, missing_roster=missing_roster)
    records.append(hit(1, 3, 1, 75))
    retained = deepcopy(records)
    fail_event_pipe(source, code)
    assert source.owns_live_process() and not source.has_live_owned_transport()
    process.frame, process.when = 30, 1
    result = session.read()
    assert result['statistics']['native_counter_scope'] == NATIVE_AUTOCHESS_CHARACTER_COUNTERS
    assert next(iter(result['statistics']['rows'].values()))['damage'] == 90030
    assert session.runtime.sessions.multiplayer is None
    assert session.observer is source and source.status is ObserverStatus.FAILED
    assert session._snapshot_fallback_observer is source
    assert not session.events_paused
    assert collector_identity_only(session) is False
    assert records == retained and acknowledgements == [] and cleanup == []
    generation = session.runtime.sessions.generation
    process.frame, process.when = 60, 2
    assert session.read()['frame'] == 60
    assert session.runtime.sessions.generation == generation
    assert cleanup == [] and records == retained and acknowledgements == []


def collector_identity_only(session):
    return session.collector.event_identity_only


@pytest.mark.parametrize('fault', ['dead', 'birth', 'pid', 'unknown'])
def test_process_only_proof_rejects_changed_or_unknown_owner(tmp_path, fault):
    session, process, source, _, _, _ = owned_session(tmp_path)
    fail_event_pipe(source)
    if fault == 'dead':
        process.process_alive = lambda: False
    elif fault == 'birth':
        process.process_instance = (process.pid, 999999)
    elif fault == 'pid':
        process.pid += 1
    else:
        process.process_alive = lambda: None
    assert source.owns_live_process() is False
    assert source.has_live_owned_transport() is False


@pytest.mark.parametrize('fault', ['ready', 'unsupported', 'foreign_collector', 'unknown_source'])
def test_fallback_gate_does_not_borrow_unknown_or_healthy_source_authority(tmp_path, fault):
    session, process, source, _, _, _ = owned_session(tmp_path)
    if fault != 'ready':
        fail_event_pipe(source)
    if fault == 'unsupported':
        session.collector.diagnostics.version_status = VersionStatus.INCOMPATIBLE
    elif fault == 'foreign_collector':
        source.collector = SimpleNamespace(process=process)
    elif fault == 'unknown_source':
        session.observer = SimpleNamespace(status=ObserverStatus.FAILED,
                                          owns_live_process=lambda: True)
    assert session._enter_native_snapshot_fallback() is False
    assert getattr(session, '_snapshot_fallback_observer', None) is None


@pytest.mark.parametrize('operation', ['mark', 'read', 'ack'])
def test_first_transport_failure_in_live_read_degrades_without_generic_escape(tmp_path, operation):
    from test_native_pc_autochess_collector_gate import SCOPE

    session, process, source, records, acknowledgements, cleanup = owned_session(tmp_path)
    records.append(hit(1, 3, 1, 75, event_scope=dict(
        protocol_version=1, status='available', verified_same_frame=True,
        scope_key=SCOPE, phase=4, round=4, frame=30, game_time=1)))
    process.frame, process.when = 30, 1
    original_request = source._pipe.request
    failed = []

    def request(op, arguments):
        if op == operation and not failed:
            failed.append(op)
            return dict(error='native capture failed: code=6')
        return dict(result=original_request(op, **arguments))

    source._pipe = owned_pipe(request)
    with pytest.raises(CaptureRetry):
        session.read()
    assert failed == [operation]
    result = session.read()
    assert result['statistics']['native_counter_scope'] == NATIVE_AUTOCHESS_CHARACTER_COUNTERS
    assert source.status is ObserverStatus.FAILED and source._pipe._poisoned
    assert acknowledgements == [] and cleanup == []
    assert len(records) == 1


def pending_native_session(monkeypatch):
    import test_autochess_scope_end
    from connection import DetectionStatus
    from test_autochess_scope_end import OwnedScopeEnd
    from test_pc_serial_clock_reset_end import CollectorClockBattle

    class OwnedNativeTransport(CollectorClockBattle):
        def __init__(self):
            super().__init__()
            self.collector.diagnostics.status = DetectionStatus.READY_UNIQUE
            self.collector.diagnostics.connection_ok = True
            self.collector.diagnostics.version_status = VersionStatus.COMPATIBLE
            acquire = self.collector.process.sample

            def independent(**kwargs):
                report = acquire(**kwargs)
                if not self.collector.event_identity_only:
                    report['sample'].update(roster_status='available', roster=dict(local_side=0, entries=[]),
                        actor_rows_status='available', actor_rows=[
                        dict(id='char_001_amiya', damage=90000 + self.frame, healing=0,
                             physical=90000 + self.frame, arts=0, true=0, elemental=0)])
                return report

            self.collector.process.sample = independent
            native = NativeHookEventProbe(self.collector)
            native.status = ObserverStatus.READY
            native._owned_process_instance = self.collector.process.process_instance

            def request(operation, **arguments):
                if operation == 'ack':
                    self.commit(arguments['sequence'])
                    return dict(acknowledged=arguments['sequence'])
                result = self.mark() if operation == 'mark' else self.batch(arguments['through'])
                if operation == 'read':
                    result['events'] = [event for event in result['events']
                                        if arguments['after'] < event['sequence']]
                return result

            native._pipe = owned_pipe(lambda operation, arguments: dict(
                result=request(operation, **arguments)))
            self.observer = native

    monkeypatch.setattr(test_autochess_scope_end, 'CrossingPcBattle', OwnedNativeTransport)
    case = OwnedScopeEnd('native_pc', phase=4, mode='all')
    world = case.fixture
    session = CollectionSession(None, None, collector=world.collector, frida_remote=None)
    session.runtime = world.runtime
    session._pending_history = world.runtime.pending_history
    session.capture = world.runtime.sessions.capture
    session.observer = world.observer
    session.event_status = 'ready'
    world.collector.event_identity_only = True
    return case, session


def test_first_pending_prefix_read_failure_is_local_to_event_source(monkeypatch):
    case, session = pending_native_session(monkeypatch)
    world, source = case.fixture, case.fixture.observer
    case.advance(1.5, phase=5)
    assert world.poll().kind == 'preparing'
    assert world.runtime.feed._pending_scope is not None
    case.advance(2, amount=75)
    retained = deepcopy(world.queue)
    commits = list(world.commits)
    original_reply = source._pipe.owned_reply

    def failed_read(operation, arguments):
        if operation == 'read':
            return dict(error='native capture failed: code=6')
        return original_reply(operation, arguments)

    source._pipe.owned_reply = failed_read
    with pytest.raises(CaptureRetry):
        session.read()
    result = session.read()
    assert result['statistics']['native_counter_scope'] == NATIVE_AUTOCHESS_CHARACTER_COUNTERS
    assert world.queue == retained and world.commits == commits
    assert session.observer is source and source.status is ObserverStatus.FAILED


@pytest.mark.parametrize('error', [MemoryError('owned'), SystemError('owned'),
    InterruptedError('owned'), OSError(12, 'owned'), RecursionError('owned'),
    ProcessChanged('owned'), UnsupportedVersion('owned')])
def test_fundamental_pipe_failure_survives_feed_stringification(tmp_path, error):
    session, _, source, _, acknowledgements, cleanup = owned_session(tmp_path)
    pipe = owned_pipe(lambda operation, arguments: {})

    def failed_io(*arguments):
        raise error

    pipe._io = failed_io
    source._pipe = pipe
    with pytest.raises(type(error)) as caught:
        session.read()
    assert caught.value is error
    assert source.status is ObserverStatus.FAILED and pipe._poisoned
    assert not source.has_owned_transport_failure()
    assert not source.is_owned_transport_failure(error)
    assert getattr(session, '_snapshot_fallback_observer', None) is None
    assert acknowledgements == [] and cleanup == []
    with pytest.raises(type(error)) as repeated:
        session.read()
    assert repeated.value is error


@pytest.mark.parametrize('changed', ['error', 'pipe', 'lifetime', 'status'])
def test_transport_receipt_cannot_be_borrowed_by_another_failure(tmp_path, changed):
    session, _, source, _, _, _ = owned_session(tmp_path)
    source._pipe = owned_pipe(lambda operation, arguments: dict(error='native capture failed: code=6'))
    with pytest.raises(RuntimeError) as caught:
        source.mark()
    assert source.is_owned_transport_failure(caught.value)
    if changed == 'error':
        assert not source.is_owned_transport_failure(RuntimeError(str(caught.value)))
        return
    if changed == 'pipe':
        source._pipe = owned_pipe(lambda operation, arguments: {})
        source._pipe._poisoned = True
    elif changed == 'lifetime':
        source._cursor_lifetime = object()
    else:
        source.status = ObserverStatus.READY
    assert not source.has_owned_transport_failure()
    assert not source.is_owned_transport_failure(caught.value)


@pytest.mark.parametrize('error', [TypeError('owned'), ValueError('owned')])
def test_ordinary_error_in_real_poisoned_transport_has_exact_receipt(tmp_path, error):
    session, _, source, _, _, _ = owned_session(tmp_path)
    source._pipe = owned_pipe(lambda operation, arguments: {})

    def failed_io(*arguments):
        raise error

    source._pipe._io = failed_io
    with pytest.raises(type(error)) as caught:
        source.mark()
    assert caught.value is error and source.is_owned_transport_failure(error)
    assert source.has_owned_transport_failure()


def test_real_worker_keeps_one_session_across_fatal_and_repeated_basic_reads(tmp_path, monkeypatch):
    from test_native_pc_clock_busy_cadence import run_worker

    actual, process, source, records, acknowledgements, cleanup = owned_session(tmp_path)
    source._pipe = owned_pipe(lambda operation, arguments: dict(error='native capture failed: code=6'))
    samples, closes = [], []

    def factory(worker, clock, starts, sessions):
        class Session:
            def __init__(self, *arguments, **kwargs):
                sessions.append(self)
                source.runtime = worker.pc_runtime

            def __getattr__(self, name):
                return getattr(actual, name)

            def enable_lifecycle(self, runtime):
                # The owned setup already established a real connected battle.
                pass

            def connect(self):
                pass

            def read(self):
                starts.append(clock.perf)
                clock.perf += .001
                if len(starts) > 6:
                    worker.stop.set()
                    raise AssertionError('worker failed to make bounded fallback progress')
                process.frame += 30
                process.when += 1
                result = actual.read()
                samples.append(result)
                if len(samples) == 3:
                    worker.stop.set()
                return result

            def close(self):
                closes.append('final cleanup')

        return Session

    worker, starts, waits, sessions, events = run_worker(
        tmp_path, monkeypatch, session_factory=factory, backend='native')
    assert len(sessions) == 1 and len(closes) == 1
    assert len(samples) == 3 and len(starts) == 4
    assert all(result['statistics']['native_counter_scope'] == NATIVE_AUTOCHESS_CHARACTER_COUNTERS
               for result in samples)
    assert source.status is ObserverStatus.FAILED and actual.observer is source
    assert actual._snapshot_fallback_observer is source
    assert acknowledgements == [] and cleanup == []
    assert worker.effective[1] is True


def test_fatal_terminal_source_releases_truthful_gap_archive_once(tmp_path):
    from battle_history import BattleRecord
    from battle_lifecycle import BattleSessions
    from capture_battles import observation_from_json

    observations = []
    session, process, source, records, acknowledgements, cleanup = owned_session(
        tmp_path, observations=observations)
    original_request, acquire = source._pipe.request, process.sample

    def terminal_capability(operation, **arguments):
        result = original_request(operation, **arguments)
        result['terminal_read_version'] = 1
        return result

    def terminal_sample(**arguments):
        report = acquire(**arguments)
        report['sample']['battle_state'] = 3
        return report

    source._pipe.request = terminal_capability
    process.sample = terminal_sample
    assert session.runtime.poll(session.collector, source).kind == 'terminal'
    assert session.runtime.sessions.capture.terminal_pending
    retained, commits = deepcopy(records), list(acknowledgements)
    fail_event_pipe(source)
    saved = []

    def save(record):
        saved.append(record.to_dict())
        return SimpleNamespace(written=True, diagnostics=[])

    session.history = SimpleNamespace(save=save)
    assert session._enter_native_snapshot_fallback()
    assert session.runtime.pending_terminal_archive is None
    session._drain_archives()
    session._drain_archives()
    assert len(saved) == 1
    assert saved[0]['event_status'] == 'gap'
    assert saved[0]['metadata']['final_counter_status'] == 'unknown'
    assert saved[0]['metadata']['terminal_pending'] is False
    assert saved[0]['metadata'].get('terminal_ack_status') != 'confirmed'
    assert records == retained and acknowledgements == commits and cleanup == []
    replayed = BattleSessions(observations[0]['feed'])
    for observation in observations:
        replayed.consume(observation_from_json(observation))
    archive, = replayed.pop_archives()
    replay_record = BattleRecord.from_archive(archive).to_dict()
    # Persistence time is assigned anew; every captured business field is exact.
    assert {key: value for key, value in replay_record.items() if key != 'recorded_at'} == {
        key: value for key, value in saved[0].items() if key != 'recorded_at'}


def test_active_source_failure_does_not_invent_a_terminal_archive(tmp_path):
    session, _, source, records, acknowledgements, cleanup = owned_session(tmp_path)
    assert not session.runtime.sessions._terminal_seen
    retained = deepcopy(records)
    fail_event_pipe(source)
    assert session._enter_native_snapshot_fallback()
    assert not session.runtime.sessions.pop_archives()
    assert records == retained and acknowledgements == [] and cleanup == []
