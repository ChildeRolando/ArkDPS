from copy import deepcopy

import pytest

from acceptance.source_prefix_capture import audit_initial_prefix
from event_probe import NATIVE_PC_CONTRACT, ObserverReadRetry


class Observer:
    contract = NATIVE_PC_CONTRACT

    def __init__(self):
        self.calls = []
        self._committed_sequence = 0
        self.watermark = dict(sequence=3, frame=2, initial_boundary=dict(frame=0, sequence=0))
        self.prefix = dict(after=0, through=3, events=[dict(sequence=n) for n in range(1, 4)])
        self.fail = False

    def mark(self):
        return deepcopy(self.watermark)

    def read_through(self, sequence):
        self.calls.append(('prefix', sequence))
        if self.fail:
            raise ObserverReadRetry('busy fixture')
        return deepcopy(self.prefix)

    def read_baseline(self, sequence):
        self.calls.append(('baseline', sequence))
        return dict(after=sequence, through=sequence, events=[])

    def commit_events(self, sequence):
        self.calls.append(('ack', sequence))
        self._committed_sequence = sequence
        return True


def test_prefix_is_saved_before_baseline_ack_and_not_replayed_to_stats():
    observer, records = Observer(), []
    audit_initial_prefix(observer, lambda kind, data: records.append((kind, data)))
    mark = observer.mark()
    result = observer.read_baseline(3)
    assert result['events'] == [] and observer._committed_sequence == 0
    assert observer.calls == [('prefix', 3), ('baseline', 3)]
    assert records[0] == ('source_initial_prefix', dict(initial_boundary=mark['initial_boundary'],
        mark=mark, drain=observer.prefix, probe_contract=NATIVE_PC_CONTRACT.describe(),
        read_after=0, read_through=3, committed_sequence=0))
    observer.commit_events(3)
    observer.read_baseline(3)
    assert len(records) == 1
    assert observer.calls[-2:] == [('ack', 3), ('baseline', 3)]


def test_empty_zero_cursor_ack_still_closes_initial_audit():
    observer, records = Observer(), []
    observer.watermark['sequence'] = observer.prefix['through'] = 0
    observer.prefix['events'] = []
    audit_initial_prefix(observer, lambda *item: records.append(item))
    observer.mark()
    observer.read_baseline(0)
    observer.commit_events(0)
    observer.read_baseline(0)
    assert len(records) == 1


def test_retry_saves_new_candidate_without_acking_or_manufacturing_boundary():
    observer, records = Observer(), []
    observer.watermark.pop('initial_boundary')
    audit_initial_prefix(observer, lambda *item: records.append(item))
    observer.mark()
    observer.fail = True
    with pytest.raises(ObserverReadRetry):
        observer.read_baseline(3)
    assert [r[0] for r in records] == ['source_initial_prefix_error']
    assert observer.calls == [('prefix', 3)] and observer._committed_sequence == 0
    observer.fail = False
    observer.read_baseline(3)
    assert records[-1][1]['initial_boundary'] is None


@pytest.mark.parametrize('problem', ['no_mark', 'wrong_sequence', 'already_committed'])
def test_initial_prefix_requires_fresh_origin_and_exact_closing_mark(problem):
    observer = Observer()
    audit_initial_prefix(observer, lambda *_: None)
    if problem != 'no_mark':
        observer.mark()
    if problem == 'already_committed':
        observer._committed_sequence = 1
    with pytest.raises(RuntimeError, match='fresh unacknowledged'):
        observer.read_baseline(2 if problem == 'wrong_sequence' else 3)
    assert not observer.calls


def test_installation_is_idempotent():
    observer, records = Observer(), []
    for _ in range(2):
        audit_initial_prefix(observer, lambda *item: records.append(item))
    observer.mark()
    observer.read_baseline(3)
    assert len(records) == 1


class RebaseObserver(Observer):
    def __init__(self):
        super().__init__()
        self._acknowledged_sequence = 0
        self.ack_result = True

    def commit_events(self, sequence):
        self.calls.append(('ack', sequence))
        self._committed_sequence = sequence
        if self.ack_result is True:
            self._acknowledged_sequence = sequence
        return self.ack_result

    def advance(self, through, *, frame=0, echo_range=False):
        after = self._committed_sequence
        self.watermark = dict(sequence=through, frame=frame)
        self.prefix = dict(events=[dict(sequence=n, frame=9000, game_time=300,
            native_autochess_scope=dict(phase=5, round=3)) for n in range(after + 1, through + 1)],
            error=None, stopped=False, retry=False)
        if echo_range:
            self.prefix.update(after=after, through=through)


def start_rebase_observer():
    observer, records = RebaseObserver(), []
    audit_initial_prefix(observer, lambda *record: records.append(record), include_rebases=True)
    observer.mark()
    observer.read_baseline(3)
    observer.commit_events(3)
    return observer, records


def test_rebased_scope_preserves_cross_clock_raw_tail_before_empty_baseline_and_ack():
    observer, records = start_rebase_observer()
    observer.advance(9)  # Real read replies need not echo after/through.
    observer.mark()
    prior = observer._committed_sequence
    result = observer.read_baseline(9)
    assert result['events'] == [] and observer._committed_sequence == prior == 3
    kind, candidate = records[-1]
    assert kind == 'source_baseline_prefix'
    assert candidate['read_after'] == candidate['committed_sequence'] == 3 and candidate['read_through'] == 9
    assert candidate['mark'] == observer.watermark and candidate['initial_boundary'] is None
    assert candidate['drain'] == observer.prefix
    assert [event['sequence'] for event in candidate['drain']['events']] == list(range(4, 10))
    assert candidate['drain']['events'][0]['frame'] > candidate['mark']['frame']
    assert observer.calls[-2:] == [('prefix', 9), ('baseline', 9)]
    observer.commit_events(9)
    assert observer.calls[-1] == ('ack', 9)


def test_each_repeated_same_sequence_baseline_candidate_is_logged_even_after_empty_ack():
    observer, records = start_rebase_observer()
    observer.advance(3, echo_range=True)
    for _ in range(2):
        observer.mark()
        assert observer.read_baseline(3)['events'] == []
    assert [kind for kind, _ in records] == ['source_initial_prefix', 'source_baseline_prefix', 'source_baseline_prefix']
    assert all(record['drain']['events'] == [] for _, record in records[1:])


def test_unconfirmed_ack_advanced_host_commit_is_not_rewound_or_claimed_confirmed():
    observer, records = RebaseObserver(), []
    audit_initial_prefix(observer, lambda *record: records.append(record), include_rebases=True)
    observer.mark()
    observer.read_baseline(3)
    observer.ack_result = False
    assert observer.commit_events(3) is False
    assert observer._committed_sequence == 3 and observer._acknowledged_sequence == 0
    observer.advance(6)
    observer.mark()
    assert observer.read_baseline(6)['events'] == []
    kind, data = records[-1]
    assert kind == 'source_baseline_prefix' and data['read_after'] == 3
    assert data['host_acknowledged_sequence'] == 0
    assert observer._committed_sequence == 3 and observer._acknowledged_sequence == 0


@pytest.mark.parametrize('fault', ['missing_events', 'not_mapping', 'not_list', 'missing_record', 'duplicate',
    'reordered', 'boolean_event_cursor', 'float_event_cursor', 'non_mapping_event',
    'error', 'invalid_error', 'retry', 'invalid_retry', 'stopped', 'invalid_stopped',
    'wrong_after', 'wrong_through', 'boolean_echo', 'float_echo'])
def test_invalid_raw_rebase_read_is_retained_as_error_and_cannot_baseline_or_ack(fault):
    observer, records = start_rebase_observer()
    observer.advance(6, echo_range=True)
    if fault == 'missing_events':
        observer.prefix.pop('events')
    elif fault == 'not_mapping':
        observer.prefix = None
    elif fault == 'not_list':
        observer.prefix['events'] = ()
    elif fault == 'missing_record':
        observer.prefix['events'].pop()
    elif fault == 'duplicate':
        observer.prefix['events'][1]['sequence'] = 4
    elif fault == 'reordered':
        observer.prefix['events'].reverse()
    elif fault in ('boolean_event_cursor', 'float_event_cursor'):
        observer.prefix['events'][0]['sequence'] = True if fault.startswith('boolean') else 4.0
    elif fault == 'non_mapping_event':
        observer.prefix['events'][0] = None
    elif fault in ('error', 'invalid_error'):
        observer.prefix['error'] = 'failed' if fault == 'error' else []
    elif fault in ('retry', 'invalid_retry'):
        observer.prefix['retry'] = True if fault == 'retry' else 0
    elif fault in ('stopped', 'invalid_stopped'):
        observer.prefix['stopped'] = True if fault == 'stopped' else None
    elif fault == 'wrong_after':
        observer.prefix['after'] = 0
    elif fault == 'wrong_through':
        observer.prefix['through'] = 7
    elif fault == 'boolean_echo':
        observer.prefix['after'] = True
    else:
        observer.prefix['through'] = 6.0
    observer.mark()
    original = deepcopy(observer.prefix)
    with pytest.raises((RuntimeError, ObserverReadRetry)):
        observer.read_baseline(6)
    assert records[-1][0] == 'source_baseline_prefix_error'
    assert records[-1][1]['drain'] == original
    assert observer.calls[-1] == ('prefix', 6) and ('baseline', 6) not in observer.calls
    with pytest.raises(RuntimeError):
        observer.commit_events(6)
    assert observer._committed_sequence == 3 and ('ack', 6) not in observer.calls


@pytest.mark.parametrize('bad', [True, False, -1, 3.0, '3', None, 2 ** 53])
def test_rebase_requires_exact_typed_safe_nonnegative_host_cursor(bad):
    observer, _ = start_rebase_observer()
    observer.advance(6)
    observer.mark()
    observer._committed_sequence = bad
    prior_calls = list(observer.calls)
    with pytest.raises(RuntimeError):
        observer.read_baseline(6)
    assert observer.calls == prior_calls


@pytest.mark.parametrize('bad', [True, False, -1, 6.0, '6', None, 2 ** 53])
def test_rebase_rejects_invalid_requested_through_or_mark_cursor(bad):
    observer, _ = start_rebase_observer()
    observer.advance(6)
    observer.watermark['sequence'] = bad
    observer.mark()
    with pytest.raises(RuntimeError):
        observer.read_baseline(bad)
    assert ('prefix', bad) not in observer.calls[3:]


def test_rebase_journal_failure_and_read_exception_block_baseline_without_cursor_repair():
    observer, records = start_rebase_observer()
    observer.advance(6)
    observer.mark()
    observer.fail = True
    with pytest.raises(ObserverReadRetry):
        observer.read_baseline(6)
    assert records[-1][0] == 'source_baseline_prefix_error'
    observer.fail = False
    # A successful later candidate can recover without discarding the failed one.
    observer.read_baseline(6)
    assert records[-1][0] == 'source_baseline_prefix'
    assert observer._committed_sequence == 3


def test_audit_callback_runs_before_original_baseline_and_logging_failure_forbids_ack():
    observer, records = RebaseObserver(), []
    def audit(kind, data):
        observer.calls.append(('journal', kind))
        records.append((kind, data))
        if kind == 'source_baseline_prefix':
            raise OSError('journal fixture full')
    audit_initial_prefix(observer, audit, include_rebases=True)
    observer.mark(); observer.read_baseline(3); observer.commit_events(3)
    observer.advance(6); observer.mark()
    with pytest.raises(OSError, match='journal fixture full'):
        observer.read_baseline(6)
    assert ('baseline', 6) not in observer.calls
    assert observer.calls[-2:] == [('journal', 'source_baseline_prefix'), ('journal', 'source_baseline_prefix_error')]
    with pytest.raises(RuntimeError):
        observer.commit_events(6)
    assert observer._committed_sequence == 3


def test_rebase_installation_can_upgrade_default_without_double_wrapping_or_losing_origin():
    observer, records = RebaseObserver(), []
    audit = lambda *record: records.append(record)
    audit_initial_prefix(observer, audit)
    observer.mark(); observer.read_baseline(3); observer.commit_events(3)
    audit_initial_prefix(observer, audit, include_rebases=True)
    audit_initial_prefix(observer, audit, include_rebases=True)
    observer.advance(5); observer.mark(); observer.read_baseline(5)
    assert [kind for kind, _ in records] == ['source_initial_prefix', 'source_baseline_prefix']
    assert observer.calls.count(('prefix', 5)) == observer.calls.count(('baseline', 5)) == 1


def test_prefix_session_explicitly_enables_rebase_audits(monkeypatch):
    from acceptance.source_prefix_capture import PrefixAuditSession
    from collection_session import CollectionSession
    monkeypatch.setattr(CollectionSession, '_attach_observer', lambda self: True)
    session = PrefixAuditSession.__new__(PrefixAuditSession)
    session.observer, records = RebaseObserver(), []
    session.audit = lambda *record: records.append(record)
    assert session._attach_observer() is True
    session.observer.mark(); session.observer.read_baseline(3); session.observer.commit_events(3)
    session.observer.advance(5); session.observer.mark(); session.observer.read_baseline(5)
    assert records[-1][0] == 'source_baseline_prefix'


def test_a_failed_wire_ack_exception_keeps_the_host_cursor_and_saved_prefix():
    class RaisingAckObserver(RebaseObserver):
        fail_ack = True
        def commit_events(self, sequence):
            self.ack_result = not self.fail_ack
            result = super().commit_events(sequence)
            if self.fail_ack:
                raise OSError('wire response lost')
            return result
    observer, records = RaisingAckObserver(), []
    audit_initial_prefix(observer, lambda *record: records.append(record), include_rebases=True)
    observer.mark(); observer.read_baseline(3)
    with pytest.raises(OSError, match='wire response lost'):
        observer.commit_events(3)
    assert observer._committed_sequence == 3 and observer._acknowledged_sequence == 0
    observer.advance(6); observer.mark(); observer.read_baseline(6)
    assert records[-1][1]['read_after'] == 3 and records[-1][1]['host_acknowledged_sequence'] == 0
    assert records[0][1]['read_after'] == 0


def test_zero_ack_rebase_is_a_new_raw_candidate_without_a_manufactured_initial_boundary():
    observer, records = RebaseObserver(), []
    observer.watermark = dict(sequence=0, frame=0)
    observer.prefix = dict(events=[])
    audit_initial_prefix(observer, lambda *record: records.append(record), include_rebases=True)
    observer.mark(); observer.read_baseline(0); observer.commit_events(0)
    observer.mark(); observer.read_baseline(0)
    assert [kind for kind, _ in records] == ['source_initial_prefix', 'source_baseline_prefix']
    assert all(record['initial_boundary'] is None for _, record in records)
    assert observer.calls.count(('prefix', 0)) == 2


def test_mutating_the_returned_mark_cannot_retag_the_actual_closing_mark():
    observer, _ = start_rebase_observer()
    observer.advance(6)
    returned = observer.mark()
    returned['sequence'] = 9
    with pytest.raises(RuntimeError):
        observer.read_baseline(9)
    assert ('prefix', 9) not in observer.calls


def test_raw_read_cursor_side_effect_is_rejected_without_repair_or_ack():
    class MutatingReadObserver(RebaseObserver):
        mutate = False
        def read_through(self, sequence):
            result = super().read_through(sequence)
            if self.mutate:
                self._committed_sequence = sequence
            return result
    observer, records = MutatingReadObserver(), []
    audit_initial_prefix(observer, lambda *record: records.append(record), include_rebases=True)
    observer.mark(); observer.read_baseline(3); observer.commit_events(3)
    observer.advance(6); observer.mark(); observer.mutate = True
    with pytest.raises(RuntimeError, match='changed its host committed cursor'):
        observer.read_baseline(6)
    assert observer._committed_sequence == 6  # This audit wrapper does not own or rewind it.
    assert records[-1][0] == 'source_baseline_prefix_error'
    with pytest.raises(RuntimeError):
        observer.commit_events(6)
    assert ('ack', 6) not in observer.calls and ('baseline', 6) not in observer.calls


def test_original_baseline_exception_keeps_the_raw_candidate_and_blocks_ack():
    class FailingBaselineObserver(RebaseObserver):
        def read_baseline(self, sequence):
            super().read_baseline(sequence)
            raise ObserverReadRetry('baseline busy')
    observer, records = FailingBaselineObserver(), []
    audit_initial_prefix(observer, lambda *record: records.append(record), include_rebases=True)
    observer.mark()
    with pytest.raises(ObserverReadRetry, match='baseline busy'):
        observer.read_baseline(3)
    assert [kind for kind, _ in records] == ['source_initial_prefix', 'source_initial_prefix_error']
    assert records[-1][1]['stage'] == 'baseline'
    with pytest.raises(RuntimeError):
        observer.commit_events(3)
    assert ('ack', 3) not in observer.calls


def test_error_journal_failure_does_not_replace_the_original_journal_exception():
    observer = RebaseObserver()
    def audit(kind, data):
        raise OSError('original journal unavailable')
    audit_initial_prefix(observer, audit, include_rebases=True)
    observer.mark()
    with pytest.raises(OSError, match='original journal unavailable'):
        observer.read_baseline(3)
    assert ('baseline', 3) not in observer.calls
    with pytest.raises(RuntimeError):
        observer.commit_events(3)


@pytest.mark.parametrize('value', [0, 1, None])
def test_rebase_option_does_not_use_truthy_non_boolean_values(value):
    with pytest.raises(TypeError, match='Boolean'):
        audit_initial_prefix(Observer(), lambda *_: None, include_rebases=value)
