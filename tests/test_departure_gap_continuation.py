"""A failed successor read retains its seal without a second host failure."""
from test_owned_pending_departure_tail_d651 import depart_pending
from test_pending_scope_start import pending_case, start_pending


def test_closed_departure_successor_does_not_reissue_proof_or_ack(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    depart_pending(case)
    certificate = source.runtime.sessions.capture.rejected_pending_prefix
    case.advance(3, phase=5, next_round=True)
    read = source.observer.read_baseline
    with monkeypatch.context() as bad:
        bad.setattr(source.observer, 'read_baseline',
                    lambda sequence: dict(read(sequence), enemy_damage_ready=False))
        source.poll()
    assert source.runtime.sessions.event_status == 'gap'
    assert source.runtime.sessions.capture.closed
    assert source.runtime.sessions.capture.rejected_pending_prefix is certificate
    # A closed capture deliberately has no next drain. It must stay an honest
    # gap, rather than indexing None or acknowledging the rejected baseline.
    source.poll()
    assert source.runtime.sessions.event_status == 'gap'
    assert source.commits == [0, 1]
    assert source.runtime.sessions.capture.rejected_pending_prefix is certificate
