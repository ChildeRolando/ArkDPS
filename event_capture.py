"""Join a quiescent event observer to frame-guarded native snapshots.

The observer must serialize marks with callback entry/exit and retain numbered
records until the host has committed the corresponding watermark.
"""
from collections import deque
from copy import deepcopy

from collector import SampleUnavailable, BattleChanged, UnsupportedVersion
from event_observer import ObserverReadRetry
from event_probe import (NativePcBinding, NativePcProbeContract, _ROUND_START_BINDING, _ROUND_START_PREFIX_LIMIT,
                         autochess_raw_read_scope, _round_start_integer, _valid_round_start_point,
                         native_battle_start_boundary, verified_native_battle_start,
                         bind_native_autochess_pair, contract_for, event_processing_payload)
from roster import RosterUnavailable
from event_stream import StreamGap, BarrierBusy, baseline_barrier
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable


def apply_event_quality(statistics, payload, enemy_damage_quality, *, baseline_frame, initial_drain):
    """Apply the existing event-family quality rules to one scoped core."""
    if 'enemy_hp_mode' in payload:
        mode = payload['enemy_hp_mode']
        if mode not in ('events', 'snapshots'):
            raise ValueError('unsupported enemy HP collection mode')
        statistics.enemy_hp_mode = mode
    for source in payload.get('unknown_sources') or ():
        statistics.mark_overkill_unknown(source)
    for source in payload.get('engagement_unknown_sources') or ():
        statistics.mark_engagement_unknown(source)
    if payload['damage_unknown']:
        statistics.mark_all_overkill_unknown()
    if payload['engagement_unknown']:
        statistics.mark_all_engagement_unknown()
    if payload['shield_unknown']:
        statistics.mark_shields_unknown()
    if payload['skill_unknown']:
        statistics.mark_skill_unknown()
    if not payload['mitigation_ready'] or payload['mitigation_unknown']:
        statistics.mark_mitigation_unknown()
    if 'boss_damage_ready' in payload or statistics.boss_damage is not None:
        statistics.observe_boss_damage(complete=payload.get('boss_damage_ready') is True
                                       and not payload.get('boss_damage_unknown'))
    if 'enemy_entry_ready' in payload or 'enemy_entry_unknown' in payload:
        statistics.observe_enemy_entry(
            baseline_frame,
            complete=(payload.get('enemy_entry_ready') is True
                      and payload.get('enemy_entry_unknown') is not True
                      and (initial_drain
                           or statistics.enemy_entry_start_frame is not None)),
        )
    if payload.get('enemy_phase_unknown') or payload.get('enemy_phase_ready') is False:
        statistics.mark_enemy_phase_unknown()
    if 'enemy_state_ready' in payload or 'enemy_state_unknown' in payload:
        statistics.observe_enemy_state(
            baseline_frame,
            complete=(payload.get('enemy_state_ready') is True
                      and payload.get('enemy_state_unknown') is not True
                      and (initial_drain
                           or statistics.enemy_state_start_frame is not None)),
        )
    elif statistics.enemy_state_start_frame is not None:
        statistics.mark_enemy_state_unknown()
    if 'enemy_damage_ready' in enemy_damage_quality or statistics.enemy_damage is not None:
        statistics.observe_enemy_damage(complete=(
            enemy_damage_quality.get('enemy_damage_ready') is True
            and enemy_damage_quality.get('enemy_damage_unknown') is not True
            and (initial_drain or statistics.enemy_damage is not None)))


class CaptureRetry(RuntimeError):
    """No stable frame was obtained; queued events remain available."""


def confirm_sampling_mark(observer, opening, *, with_presence=False):
    """Close one acquisition without asking native sampling for another hook.

    Providers still return their own independently verified closing mark. The
    ordinary barrier below remains the authority for matching clocks/scopes.
    """
    confirm = getattr(observer, 'confirm_mark', None)
    if not with_presence and callable(confirm):
        return confirm(opening)
    return observer.mark_with_presence() if with_presence else observer.mark()


_ROUND_START_READ_SIZE = 2048


def _round_start_read_scope(payload):
    # Historical zero-origin frame fallback stays in the canonical parser.
    return autochess_raw_read_scope(payload)[1]


def _read_long_round_start_prefix(observer, sequence, batch, contract):
    point = batch.get('autochess_round_start')
    if (batch.get('autochess_round_start_events_complete') is not False
            or batch.get('autochess_round_start_events') != []
            or not _valid_round_start_point(point) or not _round_start_integer(sequence)
            or not _ROUND_START_READ_SIZE < sequence - point['sequence'] <= _ROUND_START_PREFIX_LIMIT):
        return batch
    read_range = getattr(observer, 'read_round_start_range', None)
    if not callable(read_range):
        raise StreamGap('event probe cannot read bounded AutoChess round-start prefixes')
    # PC binds these two fields in the host observer. Android replies carry
    # raw native roots, which the host checks against the independent sample.
    transport_keys = ('native_session', 'source_build')
    text_keys = ('native_stage_id', *(key for key in transport_keys
                 if isinstance(contract, NativePcProbeContract) or key in batch))
    if (any(not NativePcBinding._same(batch.get(key), point[key]) for key in _ROUND_START_BINDING)
            or any(not isinstance(batch.get(key), str) or not batch[key] for key in text_keys)):
        raise StreamGap('AutoChess round-start origin does not match its first read')
    scope = _round_start_read_scope(batch)
    if _round_start_read_scope(point) != scope:
        raise StreamGap('AutoChess round-start origin scope or accounts changed')
    binding_keys = (*_ROUND_START_BINDING, *transport_keys)
    records = []
    for after in range(point['sequence'], sequence, _ROUND_START_READ_SIZE):
        through = min(after + _ROUND_START_READ_SIZE, sequence)
        try:
            chunk = read_range(after, through)
        except (ObserverReadRetry, BattleChanged, UnsupportedVersion, StreamGap):
            raise
        except (RuntimeError, TypeError, ValueError) as exc:
            raise StreamGap('could not read the AutoChess round-start event range') from exc
        if not isinstance(chunk, dict) or chunk.get('error') or chunk.get('stopped'):
            raise StreamGap('could not read the AutoChess round-start event range')
        if chunk.get('retry'):
            raise CaptureRetry('AutoChess round-start event range requires a new read')
        if (any((key in chunk) != (key in batch)
                or not NativePcBinding._same(chunk.get(key), batch.get(key)) for key in binding_keys)
                or _round_start_read_scope(chunk) != scope):
            raise StreamGap('AutoChess round-start event range changed its source or scope')
        events = chunk.get('events')
        if (not isinstance(events, list) or len(events) != through - after
                or any(not isinstance(event, dict) for event in events)):
            raise StreamGap('incomplete AutoChess round-start event range')
        records.extend(events)
    return dict(batch, autochess_round_start_events=records, autochess_round_start_events_complete=True)


def read_event_batch(observer, sequence, *, baseline=False):
    contract = contract_for(observer)
    if (contract.bounded_reads_required
            and not (callable(getattr(observer, 'read_through', None))
                     and callable(getattr(observer, 'commit_events', None)))):
        raise StreamGap('event probe must support bounded reads and commit acknowledgements')
    try:
        read_baseline = getattr(observer, 'read_baseline', None) if baseline else None
        if callable(read_baseline):
            batch = read_baseline(sequence)
            if not isinstance(batch, dict):
                raise StreamGap('invalid event baseline response')
            if not (batch.get('error') or batch.get('stopped') or batch.get('retry')):
                if batch.get('events') != []:
                    raise StreamGap('event baseline did not return an empty window')
                batch = _read_long_round_start_prefix(observer, sequence, batch, contract)
                point = native_battle_start_boundary(batch)
                if point is not None and isinstance(contract, NativePcProbeContract):
                    committed = getattr(observer, '_committed_sequence', None)
                    read_range = getattr(observer, 'read_round_start_range', None)
                    if type(committed) is int and committed == 0 and callable(read_range) and sequence <= _ROUND_START_PREFIX_LIMIT:
                        records = []
                        for after in range(0, sequence, _ROUND_START_READ_SIZE):
                            through = min(sequence, after + _ROUND_START_READ_SIZE)
                            page = read_range(after, through)
                            if not isinstance(page, dict) or page.get('error') or page.get('stopped'):
                                raise StreamGap('native battle-start prefix is unavailable')
                            if page.get('retry'):
                                raise CaptureRetry('native battle-start prefix requires retry')
                            if (native_battle_start_boundary(page) != point
                                    or any(not NativePcBinding._same(page.get(key), batch.get(key))
                                           for key in (*_ROUND_START_BINDING, 'native_session', 'source_build', 'damage_settlement_scope'))
                                    or not isinstance(page.get('events'), list)
                                    or len(page['events']) != through - after):
                                raise StreamGap('native battle-start prefix binding or range changed')
                            records.extend(page['events'])
                        batch = dict(batch, native_battle_start_events=records,
                                     native_battle_start_read_after=0, native_battle_start_read_through=sequence,
                                     native_battle_start_committed_sequence=0,
                                     native_battle_start_events_complete=True)
        elif hasattr(observer, 'read_through'):
            batch = observer.read_through(sequence)
        else:
            batch = observer.drain()
        return dict(batch, probe_contract=contract.describe())
    except ObserverReadRetry as exc:
        raise CaptureRetry(str(exc)) from exc


def read_pending_scope_prefix(observer, anchor, evidence, sample, *, cursor_lifetime):
    """Acquire retained pages once; Sessions and multiplayer never read them."""
    from event_probe import (_verified_pending_scope_anchor, _PENDING_SCOPE_PAGE_SIZE,
                             verified_pending_scope_start, _ROUND_START_PREFIX_LIMIT, _PENDING_SCOPE_BYTES,
                             check_cursor_lifetime)
    check_cursor_lifetime(observer, cursor_lifetime)
    point, _, contract = _verified_pending_scope_anchor(anchor)
    after, through = anchor['sequence'], evidence['sequence']
    if (type(through) is not int or not after <= through <= after + _ROUND_START_PREFIX_LIMIT):
        raise StreamGap('pending scope prefix cursor is outside its budget')
    read_range = getattr(observer, 'read_round_start_range', None)
    if not callable(read_range):
        raise StreamGap('event probe cannot read retained pending scope prefixes')
    import json
    size = len(json.dumps(anchor, ensure_ascii=True, allow_nan=False).encode('utf-8'))
    pages, records = [], []
    while after < through:
        end = min(after + _PENDING_SCOPE_PAGE_SIZE, through)
        try:
            check_cursor_lifetime(observer, cursor_lifetime)
            batch = read_range(after, end)
            check_cursor_lifetime(observer, cursor_lifetime)
        except ObserverReadRetry as exc:
            raise CaptureRetry(str(exc)) from exc
        if isinstance(batch, dict) and batch.get('retry'):
            raise CaptureRetry('pending scope prefix read requires retry')
        if not isinstance(batch, dict) or not isinstance(batch.get('events'), list):
            raise StreamGap('invalid pending scope prefix reply')
        if len(batch['events']) != end - after:
            raise StreamGap('incomplete pending scope prefix reply')
        page = dict(after=after, through=end, batch=batch)
        try:
            size += len(json.dumps(page, ensure_ascii=True, allow_nan=False).encode('utf-8'))
        except (ValueError, TypeError, OverflowError) as exc:
            raise StreamGap('invalid pending scope raw JSON') from exc
        if size > _PENDING_SCOPE_BYTES:
            raise StreamGap('pending scope prefix exceeds byte budget')
        pages.append(deepcopy(page))
        records.extend(batch['events'])
        after = end
    # Empty ranges still use a real raw read at the current endpoint, not a
    # new baseline that could exclude the first later hit.
    try:
        check_cursor_lifetime(observer, cursor_lifetime)
        batch = pages[-1]['batch'] if pages else read_range(after, through)
        check_cursor_lifetime(observer, cursor_lifetime)
    except ObserverReadRetry as exc:
        raise CaptureRetry(str(exc)) from exc
    if not isinstance(batch, dict) or batch.get('retry'):
        raise CaptureRetry('pending scope empty range requires retry')
    if not pages and batch.get('events') != []:
        raise StreamGap('pending scope empty range returned an unexpected prefix')
    result = dict(evidence, drain=dict(batch, events=records, probe_contract=contract.describe()),
                  pending_scope_start=dict(version=1, anchor=deepcopy(anchor), pages=pages))
    checked = dict(sample, native_session=sample.get('native_session', sample.get('session')))
    verified_pending_scope_start(checked, result, previous_sequence=anchor['sequence'])
    return result


def acknowledge_event_batch(observer, sequence):
    """Confirm a host-owned prefix, returning False for an uncertain response.

    The producer must bind commits to their stream, or never reuse sequence
    numbers within an observer lifetime. A separate mark is not a transaction
    that can bind a later integer-only commit across a producer-side reset.
    """
    if contract_for(observer).bounded_reads_required and not callable(getattr(observer, 'commit_events', None)):
        raise StreamGap('event probe must support commit acknowledgements')
    if hasattr(observer, 'commit_events'):
        try:
            # Older providers signal success with None; supported observers
            # return False when the producer has not confirmed this prefix.
            return observer.commit_events(sequence) is not False
        except ObserverReadRetry:
            # The host already accepted this prefix. The producer may also
            # have accepted the ack before its response was lost.
            return False
    return True


def source_attribution_batch(drained, after, through, *, sample=None, mark=None):
    """Use identity evidence only from this battle's uncommitted, bounded prefix."""
    version = drained.get('source_attribution_version')
    if type(version) is not int or version != 1 or drained.get('error') or drained.get('stopped'):
        return {}
    events = drained.get('events', ())
    if sample is not None and mark is not None:
        try:
            origin = verified_native_battle_start(sample, drained, mark)
        except StreamGap:
            return {}  # Ingestion reports the invalid prefix; it grants no ownership here.
        if origin is not None:
            after, events = 0, origin[1]
    return dict(source_attribution=True, settlement_version=drained.get('partial_damage_version'),
                source_events=(
        event for event in events
        if isinstance(event, dict) and type(event.get('sequence')) is int
        and after < event['sequence'] <= through
    ))


def read_terminal_mark(observer):
    if hasattr(observer, 'read_terminal_mark'):
        return observer.read_terminal_mark()
    return observer.mark()


class LiveEventCapture:
    def __init__(self, collector, observer=None, audit=None):
        self.collector = collector
        self.observer = observer
        self.contract = contract_for(observer)
        self.audit = audit or (lambda *_args: None)
        self.adapter = BattleStatisticsAdapter()
        self.pending = deque()
        self.last_sample = None
        self.baseline_sequence = None
        self.initial_drain = True
        self._enemy_damage_ready = None
        self.closed = False

    def close(self):
        if not self.closed:
            # Event coverage is separate from native snapshots/presence.
            self.adapter.on_event_gap()
            self.closed = True

    def _mark(self, opening=None):
        try:
            mark = (self.observer.mark() if opening is None
                    else confirm_sampling_mark(self.observer, opening))
        except ObserverReadRetry as exc:
            raise CaptureRetry(str(exc)) from exc
        except Exception as exc:
            self.close()
            raise StreamGap(f'observer unavailable: {exc}') from exc
        self.audit('observer_mark', mark)
        if not isinstance(mark, dict):
            self.close()
            raise StreamGap('invalid observer mark')
        if mark.get('error'):
            self.close()
            raise StreamGap(f"observer callback error: {mark['error']}")
        if mark.get('stopped'):
            self.close()
            raise StreamGap('observer stopped')
        if mark.get('retry') is True:
            raise CaptureRetry('observer clock advanced during the frame read')
        return mark

    def _check_session(self, sample):
        if self.adapter.session is not None and (
                self.adapter.session != sample.get('session')
                or sample['game_time'] < self.adapter.last_game_time
                or sample['frame'] < self.adapter.last_frame):
            self.close()
            raise BattleChanged('battle identity or clock changed while observing')

    def snapshot(self):
        """Continue native counters after event loss, preserving frozen coverage."""
        try:
            sample = self.collector.read()
            roster = self.collector.read_roster()
        except (BattleChanged, UnsupportedVersion):
            self.close()
            raise
        except (SampleUnavailable, RosterUnavailable) as exc:
            raise CaptureRetry(str(exc)) from exc
        self.audit('sample', {'sample': sample, 'roster': roster})
        if roster.frame != sample.get('frame'):
            raise CaptureRetry('snapshot and roster are from different frames')
        self._check_session(sample)
        self.adapter.update(sample, roster)
        self.last_sample = sample
        return self.adapter.result()

    def sample(self):
        if self.closed:
            raise StreamGap('event capture is closed')
        if self.observer is None:
            return self.snapshot()
        try:
            before = self._mark()
            sample = self.collector.read()
            roster = self.collector.read_roster()
            after = self._mark(before)
            self.audit('sample', {'before': before, 'sample': sample, 'roster': roster, 'after': after})
        except (StreamGap, BattleChanged, UnsupportedVersion):
            self.close()
            raise
        except (SampleUnavailable, RosterUnavailable) as exc:
            self.audit('retry', {'reason': str(exc)})
            raise CaptureRetry(str(exc)) from exc
        except CaptureRetry:
            raise
        except Exception:
            self.close()
            raise
        self._check_session(sample)
        if roster.frame != sample.get('frame'):
            raise CaptureRetry('snapshot and roster are from different frames')
        try:
            sequence = baseline_barrier(before, sample, after, contract=contract_for(self.observer, sample))
            bind_native_autochess_pair(sample, before, after)
        except BarrierBusy as exc:
            raise CaptureRetry(str(exc)) from exc
        except StreamGap:
            self.close()
            raise
        try:
            try:
                contract = contract_for(self.observer, sample)
                drained = read_event_batch(self.observer, sequence,
                    baseline=contract.raw_replay and self.baseline_sequence is None)
                contract.validate_binding(sample, drained)
            except (BarrierBusy, ObserverReadRetry) as exc:
                raise CaptureRetry(str(exc)) from exc
            except CaptureRetry:
                raise
            except Exception as exc:
                raise StreamGap(f'observer event batch unavailable: {exc}') from exc
            self.adapter.update(sample, roster, **source_attribution_batch(
                drained, self.adapter.event_stream.sequence if self.adapter.event_stream else sequence, sequence,
                sample=sample, mark=after))
            self.audit('drain', drained)
            result = self.ingest(sample, roster, sequence, drained, mark=after)
            acknowledge_event_batch(self.observer, sequence)
            return result
        except CaptureRetry:
            raise
        except MappingUnavailable as exc:
            if self.baseline_sequence is None and self.adapter.statistics is None:
                raise CaptureRetry(str(exc)) from exc
            self.close()
            raise
        except Exception:
            self.close()
            raise

    def _apply_quality(self, payload, enemy_damage_quality):
        if self.adapter.statistics is not None:
            apply_event_quality(self.adapter.statistics, payload, enemy_damage_quality,
                                baseline_frame=self.adapter.event_stream.baseline_frame,
                                initial_drain=self.initial_drain)

    def ingest(self, sample, roster, sequence, drained, *, mark=None, allow_late_baseline=False):
        """Apply an acquired barrier/drain to the lifecycle-owned adapter.

        Acquisition has already bracketed the singleton and updated the native
        baseline. The observation generation is checked before reaching here.
        """
        try:
            contract = contract_for(self.observer, sample, drained.get('probe_contract'))
            self.contract = contract
            contract.validate_binding(sample, mark)
            contract.validate_batch(sample, sequence, drained, stream=self.adapter.event_stream)
            if self.baseline_sequence is None:
                origin = verified_native_battle_start(sample, drained, mark)
                self.baseline_sequence = 0 if origin is not None else sequence
                self.adapter.start_events(session=sample['session'], frame=sample['frame'],
                                          sequence=self.baseline_sequence, allow_late_baseline=allow_late_baseline,
                                          retained_zero_origin=origin is not None)
                if origin is not None:
                    drained = dict(drained, events=origin[1])
            stream = self.adapter.event_stream
            if drained.get('error'):
                raise StreamGap(f"observer callback error: {drained['error']}")
            if drained.get('stopped'):
                raise StreamGap('observer stopped')
            # A replayable drain is bounded by sequence, but its diagnostic
            # fields may come from a later round. Use the quiescent mark for
            # round-scoped damage quality and retain the old journal contract.
            damage_quality = drained
            if mark is not None and mark.get('damage_settlement_quality_version') == 1:
                damage_quality = {
                    'enemy_damage_ready': mark.get('enemy_damage_ready') is True,
                    'enemy_damage_unknown': mark.get('enemy_damage_unknown') is not False,
                }
            ready_declaration = drained.get('enemy_damage_ready')
            mark_declaration = mark.get('enemy_damage_ready') if mark is not None else None
            if (type(ready_declaration) is bool and type(mark_declaration) is bool
                    and ready_declaration != mark_declaration):
                raise StreamGap('enemy certificate capability differs between mark and drain')
            if self.initial_drain:
                self._enemy_damage_ready = ready_declaration if type(ready_declaration) is bool else None
            elif self._enemy_damage_ready is not None and any(
                    type(value) is bool and value != self._enemy_damage_ready
                    for value in (ready_declaration, mark_declaration)):
                raise StreamGap('enemy certificate capability changed within an observation stream')
            version = drained.get('partial_damage_version')
            if type(version) is int and version in (1, 2, 3, 4, 5, 6, 7):
                self.adapter.statistics.partial_damage_enabled = True
                if version >= 2:
                    self.adapter.statistics.reconcile_damage_scope = True
                if version >= 3:
                    self.adapter.statistics.settlement_enabled = True
                    self.adapter.statistics.damage_settlement_version = version
                    # Capability fallback is an explicit initial producer declaration,
                    # never a missing flag normalized to False or a later family change.
                    if self.initial_drain:
                        self.adapter.statistics.legacy_observation_mode = (
                            drained.get('enemy_damage_ready') is False
                            and drained.get('enemy_damage_unknown') is False
                            and (mark is None or mark.get('enemy_damage_ready') is False
                                 and mark.get('enemy_damage_unknown') is False))
                    ready = damage_quality.get('damage_settlement_ready', damage_quality.get('enemy_damage_ready'))
                    unknown = damage_quality.get('damage_settlement_unknown',
                                                 damage_quality.get('enemy_damage_unknown'))
                    deliberate_legacy = (
                        getattr(self.adapter.statistics, 'legacy_observation_mode', False)
                        and drained.get('enemy_damage_ready') is False
                        and drained.get('enemy_damage_unknown') is False
                        and (mark is None or mark.get('enemy_damage_ready') is False
                             and mark.get('enemy_damage_unknown') is False)
                        and not bool(unknown))
                    self.adapter.statistics.settlement_stream_incomplete |= (
                        ready is not True and not deliberate_legacy or bool(unknown))
            self._apply_quality(contract.normalize_quality(drained), damage_quality)
            self.pending.extend(drained['events'])
            while self.pending:
                event = self.pending[0]
                if not isinstance(event, dict) or not isinstance(event.get('sequence'), int):
                    raise StreamGap('invalid event record')
                if event['sequence'] > sequence:
                    break
                self.pending.popleft()
                if ((self.initial_drain and event['sequence'] <= self.baseline_sequence)
                        or (contract.raw_replay and event['sequence'] <= stream.sequence)):
                    # Native replays were compared against the original record
                    # before this frame's roster could change its attribution.
                    continue
                payload = event_processing_payload(event)
                self.adapter.accept_event(session=sample['session'], refresh_result=False,
                                          replay_record=event if contract.raw_replay else None, **payload)
            self.initial_drain = False
            if any(event.get('sequence', -1) <= sequence for event in self.pending):
                raise StreamGap('event records were delivered out of order')
            stream.watermark(session=sample['session'], sequence=sequence,
                             frame=sample['frame'], game_time=sample['game_time'])
            self.adapter.last_result = self.adapter.statistics.result()
            self.last_sample = sample
            return self.adapter.result()
        except (BarrierBusy, ObserverReadRetry) as exc:
            # Admission has not consumed an event: a later native scope needs
            # a new paired snapshot, while the retained prefix stays replayable.
            raise CaptureRetry(str(exc)) from exc
        except MappingUnavailable as exc:
            if self.baseline_sequence is None and self.adapter.statistics is None:
                raise CaptureRetry(str(exc)) from exc
            self.close()
            raise
        except Exception:
            self.close()
            raise
