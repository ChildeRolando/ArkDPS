"""Real Frida message/ACK protocol over owned terminal-tail evidence."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from event_capture import CaptureRetry
from event_observer import NativePcFridaObserver, ObserverStatus
from event_probe import reset_probe_cursor
from test_native_pc_terminal_tail import damage, owned_battle


def pending_terminal_ack(*, lost=1):
    collector, runtime, native, records, calls, closed = owned_battle()
    probe = NativePcFridaObserver(collector)
    probe.status = ObserverStatus.READY
    probe.request_timeout = probe.terminal_mark_timeout = 0
    probe.probe_modes = dict(terminal_read_version=1)
    probe.session = SimpleNamespace(is_detached=False)
    busy, remote_ack, remaining = [False], [0], [lost]
    operations = []

    def post(message):
        operation = message['operation']
        operations.append((operation, message.get('sequence')))
        if operation == 'mark' and busy[0]:
            return
        arguments = {key: value for key, value in message.items()
                     if key not in ('type', 'id', 'operation')}
        reply = native._pipe.request(operation, **arguments)
        if operation == 'ack':
            remote_ack[0] = max(remote_ack[0], message['sequence'])
            if message['sequence'] == 2 and remaining[0]:
                remaining[0] -= 1
                busy[0] = True
                return
        probe._message({'type': 'send', 'payload': {
            'kind': 'arkdps-event-response', 'id': message['id'], 'result': reply}}, None)

    probe.script = SimpleNamespace(is_destroyed=False, post=post)
    runtime.poll(collector, probe)
    records.append(damage(1, 20))
    collector.advance(time=11., frame=31, damage=20)
    runtime.poll(collector, probe)
    collector.lifecycle['state'] = 3
    runtime.poll(collector, probe)
    records.append(damage(2, 5))
    closed[0] = True
    collector.lifecycle['battle'] = None
    runtime.poll(collector, probe)
    assert probe._committed_sequence == remote_ack[0] == 2
    assert probe._acknowledged_sequence == 1
    return collector, runtime, probe, operations, busy


def test_terminal_ack_recovers_without_new_sampling_or_event_replay():
    collector, runtime, probe, operations, busy = pending_terminal_ack()
    before = runtime.sessions.adapter.result()
    reads = sum(operation in ('read', 'terminal_read') for operation, _ in operations)
    try:
        runtime.poll(collector, probe)
    except CaptureRetry:
        pass
    assert probe._acknowledged_sequence == 2
    assert runtime._pending_event_ack is None
    assert runtime.sessions.metadata['terminal_ack_status'] == 'confirmed'
    assert runtime.sessions.adapter.result() == before
    assert sum(operation in ('read', 'terminal_read') for operation, _ in operations) == reads
    assert [sequence for operation, sequence in operations if operation == 'ack'] == [1, 2, 2]


@pytest.mark.parametrize('fault', ['ordinary', 'forged', 'final', 'sequence', 'script', 'session',
                                   'cursor', 'birth', 'pid', 'failed', 'destroyed', 'detached'])
def test_terminal_receipt_cannot_borrow_changed_or_ordinary_authority(fault):
    collector, runtime, probe, operations, busy = pending_terminal_ack()
    final = runtime._pending_event_ack[3]
    receipt = probe.accepted_terminal_ack_receipt(2, final)
    assert receipt is not None
    count_before_check = len(operations)
    assert probe.can_retry_accepted_terminal_ack(2, final, receipt) is True
    assert len(operations) == count_before_check
    assert probe.retry_accepted_ack(2, receipt) is None
    count = len(operations)
    sequence = 2
    if fault == 'ordinary':
        receipt = probe.accepted_ack_receipt(2)
    elif fault == 'forged':
        receipt = replace(receipt)
    elif fault == 'final':
        final = dict(final, frame=final['frame'] + 1)
    elif fault == 'sequence':
        sequence = 3
    elif fault == 'script':
        probe.script = SimpleNamespace(is_destroyed=False)
    elif fault == 'session':
        probe.session = SimpleNamespace(is_detached=False)
    elif fault == 'cursor':
        reset_probe_cursor(probe)
    elif fault == 'birth':
        collector.process.process_instance = (42, 101)
    elif fault == 'pid':
        collector.process.pid = 43
    elif fault == 'failed':
        probe.status = ObserverStatus.FAILED
    elif fault == 'destroyed':
        probe.script.is_destroyed = True
    else:
        probe.session.is_detached = True
    assert probe.can_retry_accepted_terminal_ack(sequence, final, receipt) is False
    assert probe.retry_accepted_terminal_ack(sequence, final, receipt) is None
    assert len(operations) == count


def test_unverified_final_object_cannot_issue_terminal_receipt():
    _, runtime, probe, operations, _ = pending_terminal_ack()
    final = runtime._pending_event_ack[3]
    assert probe.accepted_terminal_ack_receipt(2, dict(final, frame=final['frame'] + 1)) is None
    probe._verified_terminal_read = None
    assert probe.accepted_terminal_ack_receipt(2, final) is None


def test_terminal_unknown_replies_remain_idempotent_then_confirm():
    collector, runtime, probe, operations, _ = pending_terminal_ack(lost=2)
    for _ in range(2):
        try:
            runtime.poll(collector, probe)
        except CaptureRetry:
            pass
    assert probe._acknowledged_sequence == 2
    assert runtime._pending_event_ack is None
    assert [sequence for operation, sequence in operations if operation == 'ack'] == [1, 2, 2, 2]
    assert runtime.sessions.adapter.event_stream.sequence == 2


def archive_session(collector, runtime):
    from collection_session import CollectionSession
    saved = []
    session = CollectionSession(None, None, collector=collector)
    session.runtime = runtime
    session._pending_history = runtime.pending_history

    def save(record):
        saved.append(record.to_dict())
        return SimpleNamespace(written=True, diagnostics=[])

    session.history = SimpleNamespace(save=save)
    return session, saved


def test_terminal_pending_archive_waits_for_confirm_then_drains_once():
    collector, runtime, probe, operations, _ = pending_terminal_ack()
    session, saved = archive_session(collector, runtime)
    session._drain_archives()
    assert saved == []
    try:
        runtime.poll(collector, probe)
    except CaptureRetry:
        pass
    session._drain_archives()
    assert len(saved) == 1
    assert saved[0]['metadata']['terminal_ack_status'] == 'confirmed'
    assert saved[0]['event_status'] == 'finished'
    session._drain_archives()
    assert len(saved) == 1


@pytest.mark.parametrize('departure', ['disconnect', 'observer_retired', 'process_changed'])
def test_terminal_unconfirmed_archive_is_deliverable_on_departure(departure):
    collector, runtime, probe, operations, _ = pending_terminal_ack()
    session, saved = archive_session(collector, runtime)
    session._drain_archives()
    assert saved == []
    if departure == 'disconnect':
        runtime.disconnected('capture_stopped')
    elif departure == 'observer_retired':
        runtime.observer_retired(probe)
    else:
        runtime.process_changed()
    session._drain_archives()
    assert len(saved) == 1
    assert saved[0]['metadata']['terminal_ack_status'] != 'confirmed'
    assert saved[0]['event_status'] == 'gap'
    session._drain_archives()
    assert len(saved) == 1


def test_holding_current_terminal_archive_does_not_block_other_real_archive():
    collector, runtime, probe, operations, _ = pending_terminal_ack()
    _, other, _, _, _ = pending_terminal_ack()
    other_archive, = other.sessions.pop_archives()
    runtime.sessions._archives.append(other_archive)
    session, saved = archive_session(collector, runtime)
    session._drain_archives()
    assert len(saved) == 1
    assert saved[0]['battle_id'] == other_archive['battle_id']
    assert saved[0]['metadata']['terminal_ack_status'] == 'pending'
    try:
        runtime.poll(collector, probe)
    except CaptureRetry:
        pass
    session._drain_archives()
    assert len(saved) == 2
    assert saved[1]['metadata']['terminal_ack_status'] == 'confirmed'

@pytest.mark.parametrize('failure', ['failed', 'cursor', 'destroyed', 'detached', 'birth'])
def test_unrecoverable_terminal_owner_does_not_hold_immutable_history(failure):
    collector, runtime, probe, operations, _ = pending_terminal_ack()
    session, saved = archive_session(collector, runtime)
    session._drain_archives()
    assert saved == []
    if failure == 'failed':
        probe.status = ObserverStatus.FAILED
    elif failure == 'cursor':
        reset_probe_cursor(probe)
    elif failure == 'destroyed':
        probe.script.is_destroyed = True
    elif failure == 'detached':
        probe.session.is_detached = True
    else:
        collector.process.process_instance = (42, 101)
    # No successful later sample is needed to release a now-impossible ACK wait.
    count = len(operations)
    session._drain_archives()
    assert len(saved) == 1
    assert saved[0]['metadata']['terminal_ack_status'] != 'confirmed'
    assert len(operations) == count
