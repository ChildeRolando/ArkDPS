"""Preserve raw source evidence before acceptance baseline acknowledgements.

This additional journal is acceptance evidence only. It never supplies an
invented zero-frame snapshot or replays early events into cumulative Stats.
"""
from copy import deepcopy

from collection_session import CollectionSession
from event_probe import contract_for


MAX_SEQUENCE = (1 << 53) - 1


def _sequence(value):
    return type(value) is int and 0 <= value <= MAX_SEQUENCE


def _validate_raw_prefix(drain, after, through):
    """Validate the requested sequence range, not old records' scope or clock.

    Production readEvents replies do not echo after/through. Record the actual
    host request; reject conflicting optional echoes without inventing them.
    """
    if not isinstance(drain, dict):
        raise RuntimeError('raw prefix read did not return an object')
    if (drain.get('error') is not None and drain.get('error') is not False
            or drain.get('stopped', False) is not False or drain.get('retry', False) is not False):
        raise RuntimeError('raw prefix read failed, stopped or requires retry')
    for key, expected in (('after', after), ('through', through)):
        if key in drain and (not _sequence(drain[key]) or drain[key] != expected):
            raise RuntimeError('raw prefix reply conflicts with its requested range')
    events = drain.get('events')
    if not isinstance(events, list) or len(events) != through - after:
        raise RuntimeError('raw prefix read is not the complete requested sequence range')
    for expected, event in enumerate(events, after + 1):
        if (not isinstance(event, dict) or not _sequence(event.get('sequence'))
                or event['sequence'] != expected):
            raise RuntimeError('raw prefix read contains invalid, missing or reordered records')


def audit_initial_prefix(observer, audit, *, include_rebases=False):
    if type(include_rebases) is not bool:
        raise TypeError('include_rebases must be a Boolean')
    if getattr(observer, '_source_prefix_audited', False):
        state = getattr(observer, '_source_prefix_audit_state', None)
        if isinstance(state, dict):
            state['include_rebases'] = state['include_rebases'] or include_rebases
        return
    mark, read_baseline, commit = observer.mark, observer.read_baseline, observer.commit_events
    closing = None
    baseline_committed = False
    prefix_journaled = False
    baseline_blocked = False
    state = dict(include_rebases=include_rebases)

    def marked():
        nonlocal closing
        closing = None
        result = mark()
        closing = deepcopy(result)
        return result

    def baseline(sequence):
        nonlocal prefix_journaled, baseline_blocked
        if baseline_committed and not state['include_rebases']:
            return read_baseline(sequence)
        # Preserve every candidate, including same-watermark retries and later
        # scope baselines. These records never enter Stats or fabricate closure.
        baseline_blocked = True
        after = getattr(observer, '_committed_sequence', None)
        initial = not baseline_committed and after == 0
        kind = 'source_initial_prefix' if initial else 'source_baseline_prefix'
        raw_mark, drain, stage = deepcopy(closing), None, 'range'
        try:
            if (not _sequence(sequence) or not _sequence(after)
                    or not isinstance(raw_mark, dict) or not _sequence(raw_mark.get('sequence'))
                    or raw_mark['sequence'] != sequence or after > sequence
                    or not initial and (not state['include_rebases'] or not prefix_journaled)):
                raise RuntimeError('initial prefix requires a fresh unacknowledged observer and its closing mark')
            acknowledged = getattr(observer, '_acknowledged_sequence', None)
            if acknowledged is not None and (not _sequence(acknowledged) or acknowledged > after):
                raise RuntimeError('raw prefix has an invalid host acknowledgement cursor')
            stage = 'read'
            drain = observer.read_through(sequence)
            stage = 'validate'
            if (not _sequence(getattr(observer, '_committed_sequence', None))
                    or observer._committed_sequence != after):
                raise RuntimeError('raw prefix read changed its host committed cursor')
            _validate_raw_prefix(drain, after, sequence)
            data = dict(initial_boundary=deepcopy(raw_mark.get('initial_boundary')),
                mark=raw_mark, drain=deepcopy(drain), probe_contract=contract_for(observer).describe(),
                read_after=after, read_through=sequence, committed_sequence=after)
            if not initial:
                # Host committed and wire-confirmed cursors are not equivalent.
                data['host_acknowledged_sequence'] = acknowledged
            stage = 'journal'
            audit(kind, data)
            prefix_journaled = True
            stage = 'baseline'
            result = read_baseline(sequence)
            baseline_blocked = False
            return result
        except Exception as exc:
            try:
                audit(kind + '_error', dict(error_type=type(exc).__name__, sequence=sequence,
                    read_after=after, read_through=sequence, committed_sequence=after,
                    mark=raw_mark, drain=deepcopy(drain), stage=stage))
            except Exception:
                # A failing journal cannot authorize baseline/ACK. Preserve the
                # original read/validation/write exception even if error logging fails.
                pass
            raise

    def committed(sequence):
        nonlocal baseline_committed
        if baseline_blocked:
            raise RuntimeError('baseline acknowledgement blocked until its raw prefix is journaled')
        result = commit(sequence)
        if result is True:
            baseline_committed = True
        return result

    observer.mark, observer.read_baseline, observer.commit_events = marked, baseline, committed
    observer._source_prefix_audited = True
    observer._source_prefix_audit_state = state


class PrefixAuditSession(CollectionSession):
    def _attach_observer(self):
        ready = super()._attach_observer()
        if ready:
            audit_initial_prefix(self.observer, self.audit, include_rebases=True)
        return ready
