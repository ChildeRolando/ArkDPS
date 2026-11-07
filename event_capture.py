"""Join a quiescent event observer to frame-guarded native snapshots.

The observer must serialize RPC marks with callback entry/exit and must retain
all numbered records until drain. This module does not start or install Frida.
"""
from collections import deque

from collector import SampleUnavailable, BattleChanged, UnsupportedVersion
from roster import RosterUnavailable
from event_stream import StreamGap, BarrierBusy, baseline_barrier
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable


class CaptureRetry(RuntimeError):
    """No stable frame was obtained; queued events remain available."""


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
            self.adapter.on_gap()
            self.closed = True

    def _mark(self):
        try:
            mark = self.observer.mark()
        except Exception as exc:
            self.close()
            raise StreamGap(f'observer unavailable: {exc}') from exc
        self.audit('observer_mark', mark)
        if not isinstance(mark, dict) or mark.get('error') or mark.get('stopped'):
            self.close()
            raise StreamGap('observer stopped or reported a callback error')
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
            self.adapter.update(sample, roster)
            if self.baseline_sequence is None:
                self.baseline_sequence = sequence
                self.adapter.start_events(session=sample['session'], frame=sample['frame'],
                                          sequence=sequence)
            try:
                drained = self.observer.drain()
            except Exception as exc:
                raise StreamGap(f'observer drain unavailable: {exc}') from exc
            self.audit('drain', drained)
            if drained.get('error') or drained.get('stopped'):
                raise StreamGap('observer stopped or reported a callback error')
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
                self.adapter.accept_event(session=sample['session'], **payload)
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
