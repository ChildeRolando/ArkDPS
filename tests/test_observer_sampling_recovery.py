"""Sampling validity cannot revoke a process-owned native observer."""
from types import SimpleNamespace

import pytest

from collection_session import CollectionSession
from event_observer import ObserverStatus
from native_pc.native_hook_source import NativeHookEventProbe


def owned_session(*, restartable=False, status=ObserverStatus.READY):
    calls, records = [], []
    class Probe:
        can_restart_in_process = restartable
        error = None
        probe_modes = {}
        cursor = object()
        acknowledged = 19

        def start(self):
            calls.append('start')
            self.status = ObserverStatus.READY

        def stop(self):
            calls.append('stop')
            self.status = ObserverStatus.STOPPED

    def factory(*_):
        calls.append('construct')
        return Probe()
    collector = SimpleNamespace(diagnostics=SimpleNamespace(platform='native_pc'))
    current = CollectionSession(None, None, collector=collector, frida_remote=None,
        observer_factory=factory, audit=lambda kind, data: records.append((kind, data)))
    current.runtime = SimpleNamespace(observer_retired=lambda _: calls.append('retire'))
    current.observer = Probe()
    current.observer.status = status
    current.event_status = 'recording'
    current._observer_generation = 53
    return current, calls, records


@pytest.mark.parametrize('restartable', [False, True])
def test_new_generation_sampling_errors_preserve_explicitly_ready_transport(restartable):
    current, calls, records = owned_session(restartable=restartable)
    original, cursor = current.observer, current.observer.cursor
    for generation in (54, 54, 55, 56):
        retry, probe = current._prepare_battle_observer(SimpleNamespace(
            generation=generation, events={'error': 'scope membership temporarily unverified'}))
        assert retry is False and probe is original
        assert probe.cursor is cursor and probe.acknowledged == 19
    assert not calls
    assert not any(kind == 'observer_retry' for kind, _ in records)
    assert current._retire_observer()  # Explicit cleanup still releases exactly once.
    assert calls == ['retire', 'stop']


def test_failed_nonrestartable_native_input_keeps_cleanup_owner_without_reconstruction():
    current, calls, _ = owned_session(status=ObserverStatus.FAILED)
    original = current.observer
    original.error = 'broken native pipe'
    for generation in (54, 55):
        retry, probe = current._prepare_battle_observer(SimpleNamespace(
            generation=generation, events={'error': original.error}))
        assert not retry and probe is original and probe.status == ObserverStatus.FAILED
    assert not calls
    assert current._retire_observer()
    assert calls == ['retire', 'stop']


def test_failed_restartable_transport_still_retries_once_at_next_generation():
    current, calls, _ = owned_session(restartable=True, status=ObserverStatus.FAILED)
    original = current.observer
    observation = SimpleNamespace(generation=54, events={'error': 'transport detached'})
    retry, probe = current._prepare_battle_observer(observation)
    assert retry and probe is not original and probe.status == ObserverStatus.READY
    assert calls == ['retire', 'stop', 'construct', 'start']
    assert not current._prepare_battle_observer(observation)[0]
    assert calls == ['retire', 'stop', 'construct', 'start']


def test_arkpie_declares_its_process_lifetime_recovery_limit():
    assert NativeHookEventProbe.can_restart_in_process is False


def test_preserved_pc_observer_rejects_bad_watermark_then_delivers_once():
    from test_lifecycle_events import native_connection
    from test_event_capture import event
    runtime, collector, observer, snapshot, state, events, calls, commits = native_connection()
    collector.diagnostics = SimpleNamespace(platform='native_pc')
    observer.status = ObserverStatus.READY
    observer.can_restart_in_process = False
    def forbidden(*_):
        pytest.fail('sampling rejection retired a live native input')
    observer.stop = forbidden
    current = CollectionSession(None, None, collector=collector, frida_remote=None, observer_factory=forbidden)
    current.runtime, current.observer = runtime, observer
    runtime.poll(collector, observer)
    current._observer_generation = runtime.sessions.generation
    snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
    events.queue = [event(1, frame=2, game_time=1., amount=20)]
    commits_before = list(commits)
    retry, preserved = current._prepare_battle_observer(SimpleNamespace(
        generation=current._observer_generation+1, events={'error': 'unverified watermark'}))
    assert not retry and preserved is observer and commits == commits_before
    # The same feed still rejects an invalid mark; preserving ownership grants no ACK.
    mark = observer.mark
    observer.mark = lambda: dict(mark(), sequence=-1)
    rejected = runtime.poll(collector, observer)
    assert rejected.events['error'] and commits == commits_before
    observer.mark = mark
    # A fresh valid battle baseline is admitted without recreating the producer.
    snapshot.session = 'next-owned-battle'
    observer.mark = lambda: dict(mark(), native_session=snapshot.session)
    read_through = observer.read_through
    observer.read_through = lambda sequence: dict(read_through(sequence), native_session=snapshot.session)
    runtime.poll(collector, observer)
    snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2., 30.
    events.queue.append(event(2, frame=3, game_time=2., amount=10))
    runtime.poll(collector, observer)
    assert runtime.sessions.adapter.result()['rows']['char_a']['observed_damage'] == 10
    assert commits[-1] == 2
    runtime.poll(collector, observer)
    assert runtime.sessions.adapter.result()['rows']['char_a']['observed_damage'] == 10


def test_recovery_decision_is_logged_without_enabling_raw_debug(tmp_path):
    import json
    from capture_cli import json_value
    from desktop_audit import DesktopCaptureJournal
    current, _, _ = owned_session()
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail,
        header=lambda *_args, **_kwargs: dict(kind='log_header', data=dict(debug_mode=False)),
        debug_mode=lambda: False) as journal:
        current.audit = journal.write
        current._prepare_battle_observer(SimpleNamespace(generation=54,
            events={'error': 'private raw membership detail'}))
    rows=[json.loads(line) for line in next(tmp_path.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()]
    record=rows[-1]
    assert record['kind']=='observer_recovery_deferred'
    assert record['data']['code']=='sample_rejected' and record['data']['observer_status']=='ready'
    assert record['data']['can_restart_in_process'] is False
    assert 'private raw' not in str(rows)
