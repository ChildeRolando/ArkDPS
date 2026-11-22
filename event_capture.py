"""Join a quiescent event observer to frame-guarded native snapshots.

The observer must serialize marks with callback entry/exit and retain numbered
records until the host has committed the corresponding watermark.
"""
from collections import deque

from collector import SampleUnavailable, BattleChanged, UnsupportedVersion
from event_observer import ObserverReadRetry
from event_probe import bind_native_autochess_pair, contract_for, event_processing_payload
from roster import RosterUnavailable
from event_stream import StreamGap, BarrierBusy, baseline_barrier
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable


def apply_event_quality(statistics, payload, enemy_damage_quality, *, baseline_frame, initial_drain):
    """Apply the existing event-family quality rules to one scoped core."""
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
            if not (batch.get('error') or batch.get('stopped')) and batch.get('events') != []:
                raise StreamGap('event baseline did not return an empty window')
        elif hasattr(observer, 'read_through'):
            batch = observer.read_through(sequence)
        else:
            batch = observer.drain()
        return dict(batch, probe_contract=contract.describe())
    except ObserverReadRetry as exc:
        raise CaptureRetry(str(exc)) from exc


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


def source_attribution_batch(drained, after, through):
    """Use identity evidence only from this battle's uncommitted, bounded prefix."""
    version = drained.get('source_attribution_version')
    if type(version) is not int or version != 1 or drained.get('error') or drained.get('stopped'):
        return {}
    return dict(source_attribution=True, settlement_version=drained.get('partial_damage_version'),
                source_events=(
        event for event in drained.get('events', ())
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
        self.closed = False

    def close(self):
        if not self.closed:
            # Event coverage is separate from native snapshots/presence.
            self.adapter.on_event_gap()
            self.closed = True

    def _mark(self):
        try:
            mark = self.observer.mark()
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
            after = self._mark()
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
                drained = read_event_batch(self.observer, sequence)
                contract_for(self.observer, sample).validate_binding(sample, drained)
            except (BarrierBusy, ObserverReadRetry) as exc:
                raise CaptureRetry(str(exc)) from exc
            except CaptureRetry:
                raise
            except Exception as exc:
                raise StreamGap(f'observer event batch unavailable: {exc}') from exc
            self.adapter.update(sample, roster, **source_attribution_batch(
                drained, self.adapter.event_stream.sequence if self.adapter.event_stream else sequence, sequence))
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
                self.baseline_sequence = sequence
                self.adapter.start_events(session=sample['session'], frame=sample['frame'],
                                          sequence=sequence, allow_late_baseline=allow_late_baseline)
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
            version = drained.get('partial_damage_version')
            if type(version) is int and version in (1, 2, 3, 4, 5, 6, 7):
                self.adapter.statistics.partial_damage_enabled = True
                if version >= 2:
                    self.adapter.statistics.reconcile_damage_scope = True
                if version >= 3:
                    self.adapter.statistics.settlement_enabled = True
                    self.adapter.statistics.damage_settlement_version = version
                    ready = damage_quality.get('damage_settlement_ready', damage_quality.get('enemy_damage_ready'))
                    unknown = damage_quality.get('damage_settlement_unknown',
                                                 damage_quality.get('enemy_damage_unknown'))
                    self.adapter.statistics.settlement_stream_incomplete |= (ready is not True or bool(unknown))
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
