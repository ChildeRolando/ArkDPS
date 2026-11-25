import json
from types import SimpleNamespace

import pytest

import desktop_bridge
from collector import NativeClockBusy
from desktop_bridge import CollectionWorker, DesktopState, _CaptureHealth, _capture_retry_code
from event_capture import CaptureRetry
from event_probe import CallbackBusyMark


@pytest.mark.parametrize(('cause', 'expected'), [(OSError('private failure'), 'read_failed'),
                                                 (RuntimeError('private failure'), 'capture_retry')])
def test_health_busy_wrapper_does_not_hide_deeper_failure(cause, expected):
    busy = NativeClockBusy('clock changed')
    busy.__cause__ = cause
    retry = CaptureRetry('acquisition retry')
    retry.__cause__ = busy
    assert _capture_retry_code(retry, None, None) == expected


def test_health_stale_heartbeat_and_recovery_use_acquisition_clock():
    events = []
    health = _CaptureHealth(events.append)

    def report(now, *, captured=None, code=None):
        health.observe(now=now, started=now - .125, generation=1, active=True, captured=captured, code=code)

    report(10., captured=10.)
    report(12., code='native_clock_busy')
    assert events == []
    report(12.25, code='callback_busy')
    report(40., code='observer_retry')
    report(42.25, code='event_barrier_busy')
    report(44., captured=43.75)

    assert events == [
        dict(phase='stale', code='callback_busy', age_ms=2250, attempt_ms=125, retry_count=2, generation=1),
        dict(phase='still_stale', code='event_barrier_busy', age_ms=32250, attempt_ms=125,
             retry_count=4, generation=1),
        dict(phase='recovered', code='event_barrier_busy', age_ms=250, gap_ms=34000, attempt_ms=125,
             retry_count=4, generation=1),
    ]


def test_health_observes_slow_success_and_resets_between_generations_and_idle():
    events = []
    health = _CaptureHealth(events.append)
    health.observe(now=10, started=10, generation=1, active=True, captured=10)
    health.observe(now=13, started=10, generation=1, active=True, captured=13)
    health.observe(now=16, started=13, generation=2, active=True, captured=16)
    health.observe(now=20, started=20, generation=2, active=False, code='lifecycle_transition')
    health.observe(now=26, started=26, generation=2, active=True, captured=26)
    assert events == [
        dict(phase='stale', code='sample_gap', age_ms=3000, attempt_ms=3000, retry_count=0, generation=1),
        dict(phase='recovered', code='sample_gap', age_ms=0, gap_ms=3000, attempt_ms=3000,
             retry_count=0, generation=1),
    ]


def test_health_aged_sample_does_not_count_as_recovered():
    events = []
    health = _CaptureHealth(events.append)
    health.observe(now=12, started=9, generation=1, active=True, captured=9)
    health.observe(now=13, started=12, generation=1, active=True, captured=10)
    health.observe(now=14, started=13, generation=1, active=True, captured=13)
    assert [(event['phase'], event['code'], event['age_ms']) for event in events] == [
        ('stale', 'publication_delay', 3000), ('recovered', 'publication_delay', 1000)]


def test_worker_logs_typed_stale_and_recovery_without_debug(tmp_path, monkeypatch):
    clock = 0.
    attempts = 0
    diagnostics = SimpleNamespace(as_dict=dict, selected_serial='serial', version_actual='test')
    target = SimpleNamespace(adb_path='adb', serial='serial', diagnostics=diagnostics)
    worker = CollectionWorker(DesktopState(tmp_path / 'state'))

    def wait(delay):
        nonlocal clock
        clock += delay
        if attempts == 4:
            worker.stop.set()

    class Session:
        cleanup_errors = {}
        collector = SimpleNamespace(diagnostics=diagnostics)

        def __init__(self, *_args, **_kwargs):
            pass

        def enable_lifecycle(self, runtime):
            self.runtime = runtime
            runtime.sessions.state = 'playing'
            runtime.sessions.generation = 1

        def connect(self):
            pass

        def read(self):
            nonlocal clock, attempts
            attempts += 1
            if attempts in (2, 3):
                clock += 1
                raise CaptureRetry('C:/private/cause') from CallbackBusyMark('private callback detail')
            return dict(state=2, battle_id='live', frame=attempts, captured_monotonic=clock)

        def close(self):
            pass

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(desktop_bridge, 'time', SimpleNamespace(monotonic=lambda: clock))
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)
    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    monkeypatch.setattr(worker.stop, 'wait', wait)
    worker.request(worker.effective[0], False)
    worker.run()

    path, = (tmp_path / 'state' / 'captures').glob('wpf-*.jsonl')
    journal = path.read_text(encoding='utf-8')
    assert 'private' not in journal
    records = [json.loads(line) for line in journal.splitlines()]
    events = [record['data'] for record in records if record['kind'] == 'capture_health']
    assert [(event['phase'], event['age_ms'], event['attempt_ms'], event['retry_count']) for event in events] == [
        ('stale', 2400, 1000, 2), ('recovered', 0, 0, 2)]
    assert all({key: event[key] for key in ('code', 'platform', 'event_backend', 'observer_status',
                                        'lifecycle', 'generation')} == dict(code='callback_busy', platform='mumu',
                   event_backend='disabled', observer_status='disabled', lifecycle='playing', generation=1)
               for event in events)
    assert events[1]['gap_ms'] == 2600
    assert [value['captured_monotonic'] for kind, value in worker.events.queue if kind == 'sample'] == pytest.approx([0., 2.6])
