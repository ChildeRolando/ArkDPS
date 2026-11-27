"""Real session close releases the Sessions-owned terminal archive to history."""
from types import SimpleNamespace

import pytest

from battle_history import HistoryError
from event_observer import ObserverStatus
from test_frida_terminal_ack_recovery import archive_session, pending_terminal_ack


def terminal_session():
    collector, runtime, probe, operations, _ = pending_terminal_ack()
    session, saved = archive_session(collector, runtime)
    session.capture = runtime.sessions.capture
    session.observer = probe
    collector.close = lambda: None
    script, attached = probe.script, probe.session
    script.exports_sync = SimpleNamespace(stop=lambda: None)
    script.unload = lambda: setattr(script, 'is_destroyed', True)
    attached.detach = lambda: setattr(attached, 'is_detached', True)
    return session, runtime, probe, operations, saved


def test_close_releases_unconfirmed_terminal_archive_once():
    session, runtime, probe, operations, saved = terminal_session()
    session._drain_archives()
    assert saved == []
    session.close()
    assert len(saved) == 1
    assert saved[0]['metadata']['terminal_ack_status'] == 'pending'
    assert saved[0]['event_status'] == 'gap'
    assert probe.status is ObserverStatus.STOPPED
    assert session.closed
    session.close()
    assert len(saved) == 1
    assert not runtime.sessions.pop_archives()
    assert not runtime.pending_history


def test_close_history_failure_keeps_one_owned_handoff_without_retry_loop():
    session, runtime, probe, operations, saved = terminal_session()
    attempts = []

    def failed_save(record):
        attempts.append(record.to_dict())
        raise HistoryError('owned history sink unavailable')

    session.history.save = failed_save
    session.close()
    assert len(attempts) == 1
    assert len(runtime.pending_history) == 1
    assert not runtime.sessions.pop_archives()
    assert session.history_error is not None
    assert probe.status is ObserverStatus.STOPPED
