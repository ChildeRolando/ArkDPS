"""Join a quiescent event observer to frame-guarded native snapshots.

The observer must serialize marks with callback entry/exit and retain numbered
records until the host has committed the corresponding watermark.
"""
from collections import deque

from collector import SampleUnavailable, BattleChanged, UnsupportedVersion
from event_observer import ObserverReadRetry
from roster import RosterUnavailable
from event_stream import StreamGap, BarrierBusy, baseline_barrier
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable


class CaptureRetry(RuntimeError):
    """No stable frame was obtained; queued events remain available."""


def read_event_batch(observer, sequence):
    try:
        if hasattr(observer, 'read_through'):
            return observer.read_through(sequence)
        return observer.drain()
    except ObserverReadRetry as exc:
        raise CaptureRetry(str(exc)) from exc


def acknowledge_event_batch(observer, sequence):
    if hasattr(observer, 'commit_events'):
        observer.commit_events(sequence)


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
            sequence = baseline_barrier(before, sample, after)
        except BarrierBusy as exc:
            raise CaptureRetry(str(exc)) from exc
        except StreamGap:
            self.close()
            raise
        try:
            try:
                drained = read_event_batch(self.observer, sequence)
            except CaptureRetry:
                raise
            except Exception as exc:
                raise StreamGap(f'observer event batch unavailable: {exc}') from exc
            self.adapter.update(sample, roster, **source_attribution_batch(
                drained, self.adapter.event_stream.sequence if self.adapter.event_stream else sequence, sequence))
            self.audit('drain', drained)
            result = self.ingest(sample, roster, sequence, drained)
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

    def _apply_quality(self, payload):
        """Keep recoverable gaps scoped to their affected event metric."""
        statistics = self.adapter.statistics
        if statistics is None:
            return
        for source in payload.get('unknown_sources') or ():
            statistics.mark_overkill_unknown(source)
        for source in payload.get('engagement_unknown_sources') or ():
            statistics.mark_engagement_unknown(source)
        if payload.get('damage_unknown'):
            statistics.mark_all_overkill_unknown()
        if payload.get('engagement_unknown'):
            statistics.mark_all_engagement_unknown()
        if payload.get('shield_unknown'):
            statistics.mark_shields_unknown()
        if payload.get('skill_unknown'):
            statistics.mark_skill_unknown()
        if payload.get('mitigation_ready') is False or payload.get('mitigation_unknown'):
            statistics.mark_mitigation_unknown()
        if 'boss_damage_ready' in payload or statistics.boss_damage is not None:
            statistics.observe_boss_damage(complete=payload.get('boss_damage_ready') is True
                                           and not payload.get('boss_damage_unknown'))
        if 'enemy_entry_ready' in payload or 'enemy_entry_unknown' in payload:
            statistics.observe_enemy_entry(
                self.adapter.event_stream.baseline_frame,
                complete=(payload.get('enemy_entry_ready') is True
                          and payload.get('enemy_entry_unknown') is not True
                          and (self.initial_drain
                               or statistics.enemy_entry_start_frame is not None)),
            )
        if payload.get('enemy_phase_unknown') or payload.get('enemy_phase_ready') is False:
            statistics.mark_enemy_phase_unknown()
        if 'enemy_state_ready' in payload or 'enemy_state_unknown' in payload:
            statistics.observe_enemy_state(
                self.adapter.event_stream.baseline_frame,
                complete=(payload.get('enemy_state_ready') is True
                          and payload.get('enemy_state_unknown') is not True
                          and (self.initial_drain
                               or statistics.enemy_state_start_frame is not None)),
            )
        elif statistics.enemy_state_start_frame is not None:
            statistics.mark_enemy_state_unknown()
        if 'enemy_damage_ready' in payload or statistics.enemy_damage is not None:
            statistics.observe_enemy_damage(complete=(
                payload.get('enemy_damage_ready') is True
                and payload.get('enemy_damage_unknown') is not True
                and (self.initial_drain or statistics.enemy_damage is not None)))

    def ingest(self, sample, roster, sequence, drained):
        """Apply an acquired barrier/drain to the lifecycle-owned adapter.

        Acquisition has already bracketed the singleton and updated the native
        baseline. The observation generation is checked before reaching here.
        """
        try:
            if self.baseline_sequence is None:
                self.baseline_sequence = sequence
                self.adapter.start_events(session=sample['session'], frame=sample['frame'],
                                          sequence=sequence)
            if drained.get('error'):
                raise StreamGap(f"observer callback error: {drained['error']}")
            if drained.get('stopped'):
                raise StreamGap('observer stopped')
            version = drained.get('partial_damage_version')
            if type(version) is int and version in (1, 2, 3, 4):
                self.adapter.statistics.partial_damage_enabled = True
                if version >= 2:
                    self.adapter.statistics.reconcile_damage_scope = True
                if version >= 3:
                    self.adapter.statistics.settlement_enabled = True
                    self.adapter.statistics.settlement_stream_incomplete |= (
                        drained.get('damage_settlement_ready', drained.get('enemy_damage_ready')) is not True
                        or bool(drained.get('damage_settlement_unknown', drained.get('enemy_damage_unknown'))))
            self._apply_quality(drained)
            self.pending.extend(drained['events'])
            while self.pending:
                event = self.pending[0]
                if not isinstance(event, dict) or not isinstance(event.get('sequence'), int):
                    raise StreamGap('invalid event record')
                if event['sequence'] > sequence:
                    break
                self.pending.popleft()
                if self.initial_drain and event['sequence'] <= self.baseline_sequence:
                    continue
                payload = {k: v for k, v in event.items() if k != 'proof'}
                proof = event.get('proof')
                if (payload.get('kind') in ('skill_start', 'skill_end')
                        and isinstance(proof, dict) and isinstance(proof.get('method'), str)):
                    payload['skill_method'] = proof['method']
                self.adapter.accept_event(session=sample['session'], refresh_result=False, **payload)
            self.initial_drain = False
            if any(event.get('sequence', -1) <= sequence for event in self.pending):
                raise StreamGap('event records were delivered out of order')
            self.adapter.event_stream.watermark(session=sample['session'], sequence=sequence,
                                                frame=sample['frame'], game_time=sample['game_time'])
            self.adapter.last_result = self.adapter.statistics.result()
            self.last_sample = sample
            return self.adapter.result()
        except MappingUnavailable as exc:
            if self.baseline_sequence is None and self.adapter.statistics is None:
                raise CaptureRetry(str(exc)) from exc
            self.close()
            raise
        except Exception:
            self.close()
            raise
