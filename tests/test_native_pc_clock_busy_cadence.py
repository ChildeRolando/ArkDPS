"""Bounded worker cadence with owned clocks/queues, never a game or benchmark."""
from types import SimpleNamespace

import pytest

import desktop_bridge as bridge
import native_pc_collector as native
from battle_lifecycle import BattleRuntime, MappingRetry
from collection_session import CollectionSession
from collector import NativeClockBusy, ProcessChanged, SampleUnavailable, SnapshotGap
from connection import ConnectionConfig, ConnectionDiagnostics, DetectionStatus
from event_capture import CaptureRetry, LiveEventCapture
from event_probe import BarrierBusy, NATIVE_PC_CONTRACT, ObserverReadRetry
from native_pc.frida_runtime import _GadgetObserver
from sample_data import sample
from test_multiplayer_native_context import hit
from test_native_pc_autochess_collector_gate import fixture, SCOPE


def retry(cause):
    try:
        raise cause
    except Exception as error:
        raise CaptureRetry('guarded acquisition retry') from error


@pytest.mark.parametrize('failure', [TimeoutError('RPC timeout'), OSError('read failure'),
                                    RuntimeError('unclassified failure')])
def test_busy_wrapper_cannot_hide_an_unclassified_underlying_failure(failure):
    busy = BarrierBusy('busy wrapper')
    busy.__cause__ = failure
    wrapped = CaptureRetry('acquisition retry')
    wrapped.__cause__ = busy
    assert not bridge._native_clock_busy(wrapped)


def owned_observer(**changes):
    return SimpleNamespace(native_clock_busy_retry=True, contract=NATIVE_PC_CONTRACT,
                           status='ready', error=None, **changes)


def run_worker(tmp_path, monkeypatch, actions=(), *, platform='native_pc', enabled=True,
               custom=False, opt_in=True, read_cost=.001, on_wait=None, session_factory=None,
               observer_override=None):
    state = bridge.DesktopState(tmp_path / 'state')
    worker = bridge.CollectionWorker(state, native_pc_observer_factory=(lambda *_: None) if custom else None)
    worker._pc_provider = lambda *_args, **_kwargs: None
    config = ConnectionConfig(platform=platform, native_game_root='owned-game', auto_detect=False)
    diagnostic = ConnectionDiagnostics(status=DetectionStatus.READY_UNIQUE, platform=platform,
        selected_serial='owned-fixture', version_actual='fixture')
    clock = SimpleNamespace(perf=0.)
    starts, waits, sessions = [], [], []
    pending = iter(actions)

    def wait(delay):
        waits.append(delay)
        clock.perf += delay
        if on_wait:
            on_wait(worker, sessions, clock, delay)
        return worker.stop.is_set()

    worker.stop.wait = wait
    # A frozen coarse clock proves the burst's budget uses perf_counter.
    monkeypatch.setattr(bridge, 'time', SimpleNamespace(monotonic=lambda: 0., perf_counter=lambda: clock.perf))
    monkeypatch.setattr(bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(native, 'NativePcCollector', lambda *_args, **_kwargs: SimpleNamespace(diagnostics=diagnostic))
    monkeypatch.setattr(bridge, 'resolve_configured', lambda _: SimpleNamespace(
        adb_path='owned-fixture', serial='owned-fixture', diagnostics=diagnostic))
    monkeypatch.setattr(bridge, 'ensure_frida_endpoint', lambda *_args, **_kwargs: (True, 'owned'))
    monkeypatch.setattr(bridge, 'keep_frida_endpoint', lambda *_args, **_kwargs: (True, 'owned'))

    class Session:
        cleanup_errors = {}
        event_status, event_error = 'ready', None

        def __init__(self, *_args, audit, collector=None, **_kwargs):
            self.audit = audit
            self.collector = collector or SimpleNamespace(diagnostics=diagnostic)
            self.observer = observer_override or owned_observer()
            self.observer.native_clock_busy_retry = opt_in
            sessions.append(self)

        def enable_lifecycle(self, runtime):
            self.runtime = runtime
            runtime.record = lambda observation: self.audit('lifecycle_observation', observation)

        def connect(self):
            pass

        def read(self):
            starts.append(clock.perf)
            clock.perf += read_cost
            action = next(pending, 'stop')
            if callable(action):
                return action(self, worker)
            if action == 'stop':
                worker.stop.set()
                raise CaptureRetry('ordinary retry')
            if action == 'sample':
                return sample()
            if action == 'barrier':
                retry(BarrierBusy('busy current barrier'))
            if action == 'native':
                retry(NativeClockBusy('changed native closing bracket'))
            if action == 'ordinary':
                raise CaptureRetry('baseline crossed an event or game frame')
            if action == 'rpc':
                retry(ObserverReadRetry('clock words do not prove a busy barrier'))
            if action == 'mapping':
                raise MappingRetry('identity unknown', self.runtime.feed._emit('sample'))
            if action == 'gap':
                self.runtime._consume(self.runtime.feed.gap('read_unavailable'))
                retry(SnapshotGap('read failed'))
            if action == 'process':
                worker.stop.set()
                raise ProcessChanged('new process birth')
            if action in ('preparing', 'waiting', 'end'):
                if action == 'preparing':
                    self.runtime.feed.generation += 1
                observation = self.runtime.feed._emit(action)
                self.runtime._consume(observation)
                raise CaptureRetry('successful lifecycle observation')
            if action in ('consumed', 'consume_started'):
                observation = self.runtime.feed._emit('waiting')
                if action == 'consumed':
                    self.runtime._consume(observation)
                else:
                    self.runtime.record(observation)  # consume can fail before recording its sequence.
                retry(BarrierBusy('second acquisition failed after consume'))
            if action in ('runtime', 'feed', 'sessions'):
                if action == 'runtime':
                    self.runtime = BattleRuntime()
                else:
                    setattr(self.runtime, action, getattr(BattleRuntime(), action))
                retry(BarrierBusy('owner object was replaced'))
            raise AssertionError(action)

        def close(self):
            pass

    monkeypatch.setattr(bridge, 'CollectionSession', session_factory(worker, clock, starts, sessions)
                        if session_factory else Session)
    worker.request(config, enabled)
    worker.run()
    events = list(worker.events.queue)
    assert not any(kind == 'worker_error' for kind, _ in events), events
    return worker, starts, waits, sessions, events


@pytest.mark.parametrize('status,reason,expected', [
    ('sample_retry', 'frame_or_lifecycle_changed', NativeClockBusy),
    ('sample_retry', 'other', SampleUnavailable),
    ('battle_not_ready', 'frame_or_lifecycle_changed', SampleUnavailable),
    ('read_failed', 'frame_or_lifecycle_changed', SnapshotGap),
])
def test_only_exact_native_report_uses_the_busy_type(tmp_path, status, reason, expected):
    collector, probe, _, _, _ = fixture(tmp_path)
    probe.sample = lambda **_: dict(status=status, reason=reason)
    with pytest.raises(expected) as caught:
        collector.read_lifecycle()
    assert type(caught.value) is expected


@pytest.mark.parametrize('cause', [BarrierBusy('busy'), NativeClockBusy('busy')])
def test_runtime_preserves_busy_as_retry_without_creating_a_gap(tmp_path, cause):
    runtime = BattleRuntime()
    recorded = []
    runtime.record = recorded.append
    def acquire(*_args, **_kwargs):
        if isinstance(cause, BarrierBusy):
            retry(cause)  # The real guarded-events boundary preserves this cause.
        raise cause
    runtime.feed.acquire = acquire
    with pytest.raises((CaptureRetry, BarrierBusy)):
        runtime.poll(SimpleNamespace())
    assert recorded == [] and runtime.sessions.sequence == 0


@pytest.mark.parametrize('opening_kind,eligible', [
    ('none', True), ('barrier', True), ('wrapped_barrier', True),
    ('observer', False), ('capture', False), ('transport', False),
])
def test_real_dual_acquisition_failures_preserve_opening_cause_before_retry(
        tmp_path, opening_kind, eligible):
    collector, probe, observed, commits, _ = fixture(tmp_path)
    runtime = BattleRuntime()
    recorded = []
    runtime.record = recorded.append
    opening = {
        'none': None, 'barrier': BarrierBusy('opening busy'),
        'wrapped_barrier': CaptureRetry('opening wrapper'),
        'observer': ObserverReadRetry('generic opening retry'),
        'capture': CaptureRetry('generic opening retry'),
        'transport': OSError('opening transport failed'),
    }[opening_kind]
    if opening_kind == 'wrapped_barrier':
        opening.__cause__ = BarrierBusy('verified opening busy')
    original_mark = observed.mark
    def mark():
        if opening is not None:
            raise opening
        return original_mark()
    observed.mark = mark
    busy = NativeClockBusy('closing native clock changed')
    def read_lifecycle():
        raise busy
    collector.read_lifecycle = read_lifecycle
    with pytest.raises(CaptureRetry) as caught:
        runtime.poll(collector, observed)
    assert caught.value.__cause__ is busy
    assert busy.__cause__ is opening
    assert bridge._native_clock_busy(caught.value) is eligible
    assert recorded == [] and runtime.feed.current_lifecycle is None
    assert runtime.sessions.sequence == 0 and runtime.sessions.adapter is None
    assert commits == [] and probe.reads == 0


def test_three_attempts_then_original_wait_bounds_sustained_busy(tmp_path, monkeypatch):
    _, starts, waits, _, _ = run_worker(tmp_path, monkeypatch, ['barrier'] * 7)
    assert waits == pytest.approx([.005, .005, .2, .005, .005, .2, .005, .2])
    assert starts[:3] == pytest.approx([0., .006, .012])
    assert starts[3:6] == pytest.approx([.213, .219, .225])


@pytest.mark.parametrize('action', ['ordinary', 'rpc', 'mapping', 'gap', 'process', 'consumed', 'consume_started', 'runtime', 'feed', 'sessions'])
def test_only_unconsumed_typed_busy_has_a_short_wait(tmp_path, monkeypatch, action):
    _, _, waits, _, _ = run_worker(tmp_path, monkeypatch, [action])
    assert all(delay == .2 for delay in waits)


@pytest.mark.parametrize('platform,enabled,custom,opt_in', [
    ('mumu', True, False, True), ('native_pc', False, False, True),
    ('native_pc', True, True, True), ('native_pc', True, False, False),
])
def test_default_mumu_noevents_custom_and_unopted_providers_keep_original_cadence(
        tmp_path, monkeypatch, platform, enabled, custom, opt_in):
    assert _GadgetObserver.native_clock_busy_retry is True
    _, _, waits, _, _ = run_worker(tmp_path, monkeypatch, ['barrier', 'barrier'],
        platform=platform, enabled=enabled, custom=custom, opt_in=opt_in)
    assert waits == [.2, .2, .2]


@pytest.mark.parametrize('action', ['sample', 'preparing', 'waiting', 'end'])
def test_success_or_real_lifecycle_resets_the_window(tmp_path, monkeypatch, action):
    prefix = ['preparing'] if action == 'end' else []
    _, _, waits, _, _ = run_worker(tmp_path, monkeypatch, prefix + ['barrier', action, 'barrier'])
    suffix = waits[len(prefix):]
    assert suffix == [.005, .2, .005, .2]


def test_perf_budget_stops_extra_starts_even_when_monotonic_is_frozen(tmp_path, monkeypatch):
    _, starts, waits, _, _ = run_worker(tmp_path, monkeypatch, ['native'] * 3, read_cost=.02)
    assert waits == [.005, .2, .005, .2]
    assert starts[:3] == pytest.approx([0., .025, .245])


def test_a_single_slow_attempt_gets_no_extra_retry(tmp_path, monkeypatch):
    _, starts, waits, _, _ = run_worker(tmp_path, monkeypatch, ['native'], read_cost=.05)
    assert waits == [.2, .2] and starts == pytest.approx([0., .25])


def test_overslept_five_ms_wait_cannot_start_an_extra_read_after_deadline(tmp_path, monkeypatch):
    def oversleep(_worker, _sessions, clock, delay):
        if delay == .005:
            clock.perf += .05
    _, starts, waits, _, _ = run_worker(tmp_path, monkeypatch, ['native'], on_wait=oversleep)
    assert starts == pytest.approx([0., .256]) and waits == [.005, .2, .2]


@pytest.mark.parametrize('change', ['stop', 'source', 'observer'])
def test_short_wait_rechecks_stop_and_latest_source_or_observer(tmp_path, monkeypatch, change):
    changed = []
    def update(worker, sessions, _clock, delay):
        if delay != .005 or changed:
            return
        changed.append(True)
        if change == 'stop':
            worker.stop.set()
        elif change == 'source':
            worker.request(worker.effective[0], True)
        else:
            sessions[-1].observer = owned_observer()
    _, starts, waits, sessions, _ = run_worker(tmp_path, monkeypatch, ['native'] * 4, on_wait=update)
    if change == 'stop':
        assert len(starts) == 1 and waits == [.005]
    else:
        assert waits[:4] == [.005, .005, .005, .2]
        assert len(sessions) == (2 if change == 'source' else 1)


def test_real_native_collector_runtime_queued_hits_retry_twice_then_credit_and_ack_once(tmp_path, monkeypatch):
    collector, probe, observed, commits, records = fixture(tmp_path / 'real')
    observed.native_clock_busy_retry = True
    observed.status, observed.error, observed.probe_modes = 'ready', None, {}
    observed.stop = lambda: None
    original = probe.sample
    probe_attempts = []
    failures = [2]

    def native_sample(**kwargs):
        probe_attempts.append(probe.frame)
        if probe.frame == 30 and failures[0]:
            failures[0] -= 1
            return dict(status='sample_retry', reason='frame_or_lifecycle_changed')
        return original(**kwargs)

    probe.sample = native_sample
    archived_runtime = []

    def factory(worker, clock, starts, sessions):
        def create(*_args, audit, **_kwargs):
            session = CollectionSession(None, None, collector=collector, audit=audit)
            sessions.append(session)
            session.observer = observed
            session.event_status, session.event_error = 'ready', None
            def connect():
                session.capture = LiveEventCapture(collector, observed, audit=audit)
                archived_runtime.append(session.runtime)
            session.connect = connect
            read = session.read
            def read_actual():
                starts.append(clock.perf)
                clock.perf += .001
                value = read()
                if len(starts) == 1:
                    probe.frame, probe.when = 30, 1
                    scope = dict(protocol_version=1, status='available', verified_same_frame=True,
                                 scope_key=SCOPE, phase=4, round=4, frame=30, game_time=1)
                    records.extend(hit(i, i, 1, i * 100, event_scope=scope) for i in range(1, 5))
                else:
                    worker.stop.set()  # An in-flight successful old-owner transaction still ACKs once.
                return value
            session.read = read_actual
            return session
        return create

    _, starts, waits, _, events = run_worker(tmp_path, monkeypatch, session_factory=factory)
    assert probe_attempts == [0, 30, 30, 30] and waits == [.2, .005, .005, .2]
    assert len(starts) == 4 and commits == [0, 4]
    published = [value for kind, value in events if kind == 'sample']
    assert len(published) == 2
    assert [player['observed_damage'] for player in published[-1]['multiplayer']['players']] == [100, 200, 300, 400]
    assert published[-1]['statistics']['rows'] == {} and not published[-1]['statistics']['totals_complete']
    stream = archived_runtime[0].sessions.adapter.event_stream
    assert stream.sequence == 4 and len(stream.received) == 4


def test_cyclic_or_fatal_cause_chain_cannot_certify_a_clock_retry():
    error = CaptureRetry('outer')
    error.__cause__ = BarrierBusy('busy')
    error.__cause__.__cause__ = error
    assert not bridge._native_clock_busy(error)
    error.__cause__.__cause__ = SnapshotGap('real read failure')
    assert not bridge._native_clock_busy(error)


def test_actual_prepare_observer_consumes_before_second_acquire_busy_so_no_fast_retry(tmp_path, monkeypatch):
    collector, _, observed, commits, _ = fixture(tmp_path / 'recovery')
    observed.native_clock_busy_retry = True
    observed.status, observed.error = 'ready', None
    calls = []
    consumed = []

    def read_recovery(session, _worker):
        runtime = session.runtime
        observation = runtime.feed.acquire(collector, observed)
        original = runtime._acquire
        def acquire(*_args):
            calls.append('acquire')
            if len(calls) == 1:
                return observation
            retry(BarrierBusy('new observer second acquisition is busy'))
        runtime._acquire = acquire
        try:
            return runtime.poll(collector, observed, prepare_observer=lambda _: (True, observed))
        finally:
            runtime._acquire = original
            consumed.append((runtime.sessions.sequence, observation.sequence, runtime.sessions.adapter))

    _, _, waits, _, _ = run_worker(tmp_path, monkeypatch, [read_recovery], observer_override=observed)
    assert calls == ['acquire', 'acquire'] and consumed == [(1, 1, None)]
    assert waits == [.2, .2] and commits == []


def test_new_request_during_a_failed_read_forbids_an_old_source_short_retry(tmp_path, monkeypatch):
    def switch(session, worker):
        worker.request(worker.effective[0], True)
        retry(BarrierBusy('old source busy'))
    _, _, waits, sessions, _ = run_worker(tmp_path, monkeypatch, [switch])
    assert waits == [.2, .2] and len(sessions) == 2
