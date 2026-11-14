"""Ordered, process-local battle observations and exactly-once archive handoff.

The feed has one acquisition owner. Consumers may queue observations, but must
keep their generation and sequence. Addresses and frames are not battle IDs.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
import math
import time
import uuid

from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch, AdapterError, MappingUnavailable
from event_capture import (LiveEventCapture, CaptureRetry, read_event_batch,
                           acknowledge_event_batch, read_terminal_mark, source_attribution_batch)
from event_observer import ObserverReadRetry
from event_stream import StreamGap, BarrierBusy, baseline_barrier
from panel_model import team_status


@dataclass(frozen=True)
class BattleObservation:
    feed: str
    sequence: int
    generation: int
    kind: str
    sample: dict | None = None
    roster: object | None = None
    reason: str = ''
    events: dict | None = None
    lifecycle: dict | None = None
    round_reset: dict | None = None


class MappingRetry(CaptureRetry):
    """A guarded frame without reliable owner evidence."""

    def __init__(self, reason, observation):
        super().__init__(reason)
        self.observation = observation


def _terminal_boundary(observer):
    if observer is None:
        return None
    try:
        mark = read_terminal_mark(observer)
    except Exception:
        return None  # Departure remains certain even if the observer is unavailable.
    if not isinstance(mark, dict) or mark.get('error') or mark.get('stopped'):
        return None
    boundary = mark.get('terminal_boundary')
    return {'terminal_boundary': boundary} if isinstance(boundary, dict) else None


class BattleFeed:
    """Issue generations at acquisition, before any asynchronous UI queue."""

    def __init__(self):
        self.id = uuid.uuid4().hex
        self.sequence = 0
        self.generation = 0
        self.identity = None
        self.clock = None
        self.retired = None
        self.retired_clock = None
        self.retired_session = None
        self.native_session = None
        self.terminal = False
        self.current_lifecycle = None
        self.stage = None
        self.retired_stage = None

    def _emit(self, kind, sample=None, roster=None, reason='', events=None, *, round_reset=None):
        self.sequence += 1
        return BattleObservation(self.id, self.sequence, self.generation,
                                 kind, deepcopy(sample), roster, reason, deepcopy(events),
                                 deepcopy(self.current_lifecycle), deepcopy(round_reset))

    @property
    def battle_waiting(self):
        """Use guarded scene evidence, with no update when a read failed."""
        current = self.current_lifecycle
        if current is None:
            return None
        # Settlement can own the flow after the battle singleton is gone.
        return (current['target'] != 'battle_finish' and
                (current['battle'] is None or current['state'] in (0, 1)))

    def gap(self, reason='read_unavailable'):
        self.current_lifecycle = None
        return self._emit('gap', reason=reason)

    def _end(self, reason, sample=None, roster=None, *, observer=None):
        if self.identity is None:
            return self._emit('waiting', reason=reason)
        events = _terminal_boundary(observer) if not self.terminal else None
        self.retired, self.retired_clock = self.identity, self.clock
        self.retired_session = self.native_session
        self.retired_stage = self.stage
        self.identity = self.clock = self.native_session = None
        self.stage = None
        self.terminal = False
        return self._emit('end', sample, roster, reason=reason, events=events)

    def _read_marked_snapshot(self, collector, observer):
        events = None
        mark = None
        if observer is not None:
            try:
                mark = observer.mark()
                if mark.get('error'):
                    raise StreamGap(f"observer callback error: {mark['error']}")
                if mark.get('stopped'):
                    raise StreamGap('observer stopped')
                if mark.get('retry'):
                    raise CaptureRetry('observer clock advanced')
            except (CaptureRetry, ObserverReadRetry) as exc:
                raise CaptureRetry(str(exc)) from exc
            except Exception as exc:
                events = {'error': str(exc)}
        sample = collector.read()
        if mark is not None and events is None:
            try:
                # Skip the roster transaction when the guarded counters already
                # crossed the opening mark. The closing mark is still required.
                baseline_barrier(mark, sample, mark)
            except BarrierBusy:
                raise
            except StreamGap as exc:
                events = {'error': str(exc)}
        roster = collector.read_roster()
        if mark is not None and events is None:
            try:
                end_mark = observer.mark()
                if end_mark.get('retry'):
                    raise CaptureRetry('observer clock advanced')
                sequence = baseline_barrier(mark, sample, end_mark)
                events = dict(before=mark, after=end_mark, sequence=sequence)
            except BarrierBusy:
                raise
            except (CaptureRetry, ObserverReadRetry) as exc:
                raise CaptureRetry(str(exc)) from exc
            except Exception as exc:
                events = {'error': str(exc)}
        return sample, roster, events

    def _read_pair(self, collector, before, observer=None):
        collector.bind_battle(before['battle'][0])
        # A busy frame is normal at 2x speed. Retry within the original
        # lifecycle bracket before paying for another process lookup and poll.
        deadline = time.monotonic() + .04
        for attempt in range(3):
            try:
                sample, roster, events = self._read_marked_snapshot(collector, observer)
                break
            except BarrierBusy as exc:
                if attempt == 2 or time.monotonic() >= deadline:
                    raise CaptureRetry(str(exc)) from exc
        if hasattr(collector, 'event_capability'):
            sample['event_capability'] = deepcopy(collector.event_capability)
        after = collector.read_lifecycle()
        self.current_lifecycle = after
        if before != after or sample['frame'] != roster.frame:
            raise FrameMismatch('生命周期或累计/编队帧在读取期间变化')
        if tuple(sample['battle_identity']) != tuple(before['battle']):
            raise FrameMismatch('快照不属于当前单例战局')
        if sample.get('state') != before.get('state'):
            raise FrameMismatch('快照战斗状态与生命周期状态不一致')
        if before.get('metadata'):
            sample['battle_metadata'] = deepcopy(before['metadata'])
        now = sample['game_time']
        if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
            raise FrameMismatch('游戏时间无效')
        return sample, roster, events

    @staticmethod
    def _drain(observer, evidence):
        if evidence is not None and 'error' not in evidence:
            try:
                evidence['drain'] = read_event_batch(observer, evidence['sequence'])
            except CaptureRetry:
                raise
            except Exception as exc:
                return {'error': str(exc)}
        return evidence

    def acquire(self, collector, observer=None, *, published_generation=0,
                preparing_generation=None):
        """Bracket cumulative/roster reads with the live singleton and flow.

        Failed reads are unknown. Only an observed null/destroyed singleton,
        FINISHED state, finish transition, or a new guarded identity ends a
        battle. No polling timeout, empty unit list or transport error does.
        """
        self.current_lifecycle = None
        before = collector.read_lifecycle()
        self.current_lifecycle = before
        metadata = before.get('metadata') or {}
        stage = metadata.get('stage_id') if metadata.get('evidence') else None
        stage_changed = bool(stage and self.stage and stage != self.stage)
        battle = before['battle']
        if battle is None:
            observation = self._end('battle_absent', observer=observer)
            self.retired = self.retired_clock = None
            self.retired_session = None
            return observation
        identity = (*collector.core.mc.process_instance,
                    collector.core.mc.pid, *battle)
        exiting = (before['transiting'] and before['source'].startswith('level_')
                   and before['target'] == 'battle_finish')
        if before.get('state') == 3 or exiting:
            if self.generation == preparing_generation:
                return self._end('finished')
            # A finished controller can still supply guarded counters while the
            # scene is exiting. A replacement cannot settle the old battle.
            if self.identity == identity and not stage_changed and before.get('state') == 3:
                from collector import SampleUnavailable, ProcessChanged
                from roster import RosterUnavailable
                from tools.stage_global_state.memory import ReadFailure
                try:
                    sample, roster, events = self._read_pair(
                        collector, before, observer if not self.terminal else None)
                except ProcessChanged:
                    raise
                except (SampleUnavailable, RosterUnavailable, AdapterError, ReadFailure):
                    self.current_lifecycle = before
                    return self._emit('terminal', reason='finished_snapshot_unavailable')
                if sample['session'] == self.native_session and sample['game_time'] >= self.clock:
                    events = self._drain(observer, events)
                    sample['native_session'] = sample['session']
                    sample['session'] = f'{self.id}:{self.generation}'
                    self.terminal = True
                    self.clock = sample['game_time']
                    return self._emit('terminal', sample, roster, events=events)
            if self.identity == identity and not stage_changed:
                return self._end('finished', observer=observer)
            return self._end('replaced')
        if not before['ready'] or before['transiting']:
            if before.get('state') in (0, 1):
                observation = self._end('battle_initializing')
                self.retired = self.retired_clock = None
                self.retired_session = None
                return observation
            return self.gap('scene_transition')
        try:
            sample, roster, events = self._read_pair(collector, before, observer)
        except FrameMismatch:
            after = self.current_lifecycle
            exit_metadata = (after.get('metadata') or {}) if after else {}
            if (self.identity == identity and after and after.get('battle') == battle
                    and exit_metadata.get('outcome') == 'abandoned'
                    and exit_metadata.get('evidence')
                    and exit_metadata.get('stage_id', self.stage) == self.stage):
                return self._end('finished', observer=observer)
            raise
        now = sample['game_time']
        if (self.terminal and not stage_changed and identity == self.identity and
                sample['session'] == self.native_session and now >= self.clock):
            return self._emit('waiting', reason='terminal_battle')
        if (self.identity is None and identity == self.retired
                and sample['session'] == self.retired_session
                and not (stage and self.retired_stage and stage != self.retired_stage)):
            if self.retired_clock is None or now >= self.retired_clock:
                return self._emit('waiting', reason='retired_battle')
        same_native = (self.identity == identity and not stage_changed
                       and self.native_session == sample['session'])
        clock_reset = same_native and self.clock is not None and now < self.clock
        changed = not same_native or clock_reset
        generation = self.generation + int(changed)
        preparing = (generation > published_generation and roster.present_known
                     and not roster.entries and not sample['rows'])
        # Preparation has no owner baseline. Keep the observer attached and
        # leave the previous prefix for the next accepted combat watermark.
        # A same-controller round reset can still prove the old stream ended
        # at its last accepted watermark, without draining preparation events.
        events = events if preparing else self._drain(observer, events)
        round_reset = None
        if clock_reset and events is not None and 'sequence' in events:
            drained = events.get('drain') or {}
            if not (drained.get('error') or drained.get('stopped')):
                round_reset = {key: events[key] for key in ('before', 'after', 'sequence')}
        if changed:
            self.generation = generation
            self.terminal = False
            self.stage = stage
        elif stage:
            self.stage = stage
        self.identity, self.clock = identity, now
        self.native_session = sample['session']
        sample['native_session'] = sample['session']
        sample['session'] = f'{self.id}:{self.generation}'
        if preparing:
            return self._emit('preparing', sample, roster, reason='empty_roster', round_reset=round_reset)
        return self._emit('sample', sample, roster, events=events, round_reset=round_reset)


class BattleSessions:
    """Live statistics, independent connection state, and archive signals.

    pop_archives transfers independent copies once in this process. The history
    store deduplicates by battle_id for durable idempotence.
    """

    def __init__(self, feed_id):
        self.feed_id = feed_id
        self.sequence = 0
        self.generation = 0
        self.preparing_generation = None
        self.adapter = None
        self.battle_id = None
        self.state = 'waiting'
        self.connection = 'unknown'
        self.reason = ''
        self.metadata = {}
        self.latest_sample = None
        self._archived = False
        self._terminal_seen = False
        self._archives = deque()
        self.capture = None
        self.event_status = 'unavailable'
        self.event_error = None
        self.snapshot_intervals = []
        self._snapshot_open = False
        self._last_event_mark = None

    def _snapshot_accepted(self):
        now = self.adapter.last_game_time
        if self._snapshot_open:
            self.snapshot_intervals[-1][1] = now
        else:
            self.snapshot_intervals.append([now, now])
            self._snapshot_open = True

    def _events(self, observation):
        evidence = observation.events
        capability = (observation.sample or {}).get('event_capability', {})
        if not evidence and self.event_status == 'unavailable' and capability.get('status') == 'failed':
            self.event_status, self.event_error = 'failed', capability.get('error')
        if not evidence or self.capture.closed:
            return
        try:
            if evidence.get('error'):
                raise StreamGap(evidence['error'])
            sequence = baseline_barrier(evidence['before'], observation.sample, evidence['after'])
            if sequence != evidence['sequence']:
                raise StreamGap('event observation barrier was altered')
            self.capture.ingest(observation.sample, observation.roster, sequence, evidence['drain'])
            self._last_event_mark = evidence['after']
            self.event_status = 'recording'
            return True
        except Exception as exc:
            self.capture.close()
            self.event_status, self.event_error = 'gap', str(exc)

    def _metadata(self, sample):
        # Backends may supply confirmed metadata, never guessed from state=3,
        # a lost connection, duration, damage, or presence of operators.
        metadata = sample.get('battle_metadata', {})
        outcome = metadata.get('outcome', 'unknown')
        confirmed = isinstance(metadata.get('evidence'), str) and bool(metadata['evidence'].strip())
        return {
            'stage_id': metadata.get('stage_id') if confirmed else None,
            'practice': metadata.get('practice') if confirmed and
                        isinstance(metadata.get('practice'), bool) else None,
            'outcome': outcome if confirmed and outcome in
                       ('victory', 'defeat', 'abandoned') else 'unknown',
            'evidence': metadata.get('evidence') if confirmed else None,
        }

    def _merge_metadata(self, sample):
        incoming = self._metadata(sample)
        if not incoming['evidence']:
            return
        conflicts = set(self.metadata.get('metadata_conflicts', ()))
        for field in ('stage_id', 'practice', 'outcome'):
            unknown = 'unknown' if field == 'outcome' else None
            previous, value = self.metadata.get(field, unknown), incoming[field]
            if field in conflicts or value == unknown:
                continue
            if previous != unknown and previous != value:
                self.metadata[field] = unknown
                conflicts.add(field)
            else:
                self.metadata[field] = value
        evidence = list(self.metadata.get('evidence_sources', ()))
        for source in (self.metadata.get('evidence'), incoming['evidence']):
            if source and source not in evidence:
                evidence.append(source)
        self.metadata['evidence'] = incoming['evidence']
        self.metadata['evidence_sources'] = evidence
        if conflicts:
            self.metadata['metadata_conflicts'] = sorted(conflicts)

    def freeze(self, reason, connection=None):
        if connection is not None:
            self.connection = connection
        self.reason = reason
        self._snapshot_open = False
        if self.adapter is not None and not self._archived:
            self.adapter.on_gap()
            self.state = 'frozen'
        self._close_event_gap(reason)

    def _close_event_gap(self, reason):
        if self.capture is not None and not self.capture.closed:
            self.capture.close()
        if self.event_status in ('ready', 'recording'):
            self.event_status, self.event_error = 'gap', reason

    def _close_unverified_event_end(self):
        if self.event_status == 'recording':
            self._close_event_gap('未取得最终事件水位')

    def _clock_reset_matches_watermark(self, observation, stream):
        evidence = observation.round_reset
        next_sample, previous = observation.sample, self.latest_sample
        if not isinstance(evidence, dict) or observation.generation != self.generation + 1 or not next_sample:
            return False
        try:
            if (not previous['native_session']
                    or next_sample['native_session'] != previous['native_session']
                    or tuple(next_sample['battle_identity']) != tuple(previous['battle_identity'])
                    or next_sample['frame'] >= previous['frame']
                    or next_sample['game_time'] >= previous['game_time']):
                return False
            mark = self._last_event_mark
            after = evidence['after']
            return (baseline_barrier(mark, previous, mark) == stream.sequence
                    and stream.frame == mark['frame'] and stream.game_time == mark['game_time']
                    and baseline_barrier(evidence['before'], next_sample, after) == stream.sequence
                    and evidence['sequence'] == stream.sequence and after['epoch'] == mark['epoch'])
        except (KeyError, ValueError, TypeError):
            return False

    def _finish_at_verified_watermark(self, observation):
        if (self.event_status != 'recording' or self._last_event_mark is None or
                self.latest_sample is None):
            return False
        stream = self.adapter.event_stream
        if stream is None or stream.closed or self.adapter.statistics.end_time is not None:
            return False
        if observation.kind in ('sample', 'preparing'):
            if not self._clock_reset_matches_watermark(observation, stream):
                return False
        elif not self._terminal_boundary_matches_watermark(observation, stream):
            return False
        self.adapter.statistics.invalidate_rates()
        self.adapter.finish()
        self.event_status, self.event_error = 'finished', None
        return True

    def _terminal_boundary_matches_watermark(self, observation, stream):
        boundary = (observation.events or {}).get('terminal_boundary')
        if not isinstance(boundary, dict):
            return False
        try:
            controller, native = self.latest_sample['battle_identity']
            session_controller, stats, level = map(int, self.latest_sample['native_session'].rsplit(':', 3)[-3:])
            if session_controller != controller:
                return False
            for key, expected in (('controller', controller), ('native', native),
                                  ('stats', stats), ('level', level)):
                if expected <= 0 or int(boundary[key], 16) != expected:
                    return False
            if int(boundary['logger'], 16) <= 0:
                return False
            mark = self._last_event_mark
            if (baseline_barrier(mark, self.latest_sample, boundary) != stream.sequence or
                    stream.frame != boundary['frame'] or stream.game_time != boundary['game_time']):
                return False
        except (KeyError, ValueError, TypeError):
            return False
        return True

    def _archive(self, reason, *, successor=None):
        if self.adapter is None or self._archived:
            return
        if successor is not None:
            self._finish_at_verified_watermark(successor)
        self._close_unverified_event_end()
        self.adapter.finish()
        self._archives.append(deepcopy(dict(
            battle_id=self.battle_id, generation=self.generation,
            reason=reason, metadata=self.metadata,
            statistics=self.adapter.result(),
            last_sample=self.latest_sample, event_status=self.event_status,
            event_error=self.event_error, snapshot_intervals=self.snapshot_intervals)))
        self._archived = True
        self.state = 'ended'

    def _terminal_update(self, observation):
        if observation.sample is None:
            # A short read during settlement did not consume or lose any
            # events. Keep the queue open for the next guarded read.
            self.metadata['final_sample_error'] = observation.reason
            return
        if observation.sample['session'] != self.battle_id:
            raise ValueError('final sample does not belong to the live battle')
        previous_adapter, previous_capture = self.adapter, self.capture
        previous_status, previous_error = self.event_status, self.event_error
        final_adapter = deepcopy(self.adapter)
        self.capture = deepcopy(self.capture)
        self.capture.adapter = final_adapter
        final_events = False
        try:
            # Accept the last verified event watermark before freezing time.
            # Later same-frame revisions change native counters only.
            if final_adapter.statistics.end_time is None:
                if observation.events:
                    final_adapter.update(
                        observation.sample, observation.roster,
                        **self._source_attribution(observation, final_adapter.event_stream))
                    self.adapter = final_adapter
                    final_events = self._events(observation)
                if not final_events:
                    self._close_unverified_event_end()
            final_adapter.update(observation.sample, observation.roster, terminal=True)
        except (AdapterError, ValueError) as exc:
            self.adapter, self.capture = previous_adapter, previous_capture
            self.event_status, self.event_error = previous_status, previous_error
            self._terminal_gap(str(exc))
        else:
            self.adapter = final_adapter
            if final_events:
                self.event_status = 'finished'
            if self.capture is not None:
                self.capture.adapter = self.adapter
            self.latest_sample = deepcopy(observation.sample)
            self._snapshot_accepted()
            self.metadata.pop('final_sample_error', None)
            self._merge_metadata(observation.sample)

    def _terminal_gap(self, reason):
        # Close unreliable coverage, but leave the endpoint pending until a
        # reliable terminal pair or confirmed departure supplies the boundary.
        self.adapter.on_gap()
        self._snapshot_open = False
        self._close_event_gap(reason)
        self.metadata['final_sample_error'] = reason

    def _merge_terminal_metadata(self, observation):
        if self.latest_sample is None or not observation.lifecycle:
            return
        identity = observation.lifecycle.get('battle')
        if identity and tuple(identity) == tuple(self.latest_sample.get('battle_identity', ())):
            self._merge_metadata({'battle_metadata': observation.lifecycle.get('metadata') or {}})

    @staticmethod
    def _source_attribution(observation, stream=None):
        evidence = observation.events or {}
        through = evidence.get('sequence')
        if type(through) is not int:
            return {}
        after = stream.sequence if stream is not None and not stream.closed else through
        return source_attribution_batch(evidence.get('drain') or {}, after, through)

    def consume(self, observation):
        if (observation.feed != self.feed_id or
                observation.sequence <= self.sequence or
                observation.generation < max(self.generation, self.preparing_generation or 0)):
            return False
        if observation.kind not in ('sample', 'terminal', 'gap', 'end', 'waiting', 'preparing'):
            raise ValueError('unknown observation kind')
        if observation.kind == 'preparing':
            if observation.generation <= self.generation:
                return False
            self._archive('new_battle', successor=observation)
            self.preparing_generation = observation.generation
            self.state = 'preparing'
            self.connection = 'connected'
            self.reason = observation.reason
        elif observation.kind == 'sample':
            if observation.generation <= 0 or observation.sample['session'] != f'{self.feed_id}:{observation.generation}':
                raise ValueError('sample does not match its observation generation')
            if observation.generation == self.generation and (
                    self._archived or self._terminal_seen or (self.adapter is not None and
                                       self.adapter.statistics.end_time is not None)):
                return False
            # A guarded new generation retires the old battle even if the new
            # roster cannot yet be mapped. Keep the old adapter for display;
            # its independent archive copy cannot be lost with the new sample.
            if observation.generation > self.generation:
                self._archive('new_battle', successor=observation)
                adapter = BattleStatisticsAdapter()
                adapter.update(observation.sample, observation.roster,
                               **self._source_attribution(observation))
                self.adapter = adapter
                self.capture = LiveEventCapture(None)
                self.capture.adapter = adapter
                self.event_status, self.event_error = 'unavailable', None
                self.generation = observation.generation
                self.preparing_generation = None
                self.battle_id = observation.sample['session']
                self._archived = False
                self._terminal_seen = False
                self._last_event_mark = None
                self.metadata = self._metadata(observation.sample)
                self.snapshot_intervals = []
                self._snapshot_open = False
            else:
                self.adapter.update(observation.sample, observation.roster,
                                    **self._source_attribution(observation, self.adapter.event_stream))
                self._merge_metadata(observation.sample)
            self._events(observation)
            self.latest_sample = deepcopy(observation.sample)
            self._snapshot_accepted()
            self.state = 'playing'
            self.connection = 'connected'
            self.reason = ''
        elif observation.kind == 'terminal':
            if (observation.generation != self.generation or self.adapter is None
                    or self._archived):
                return False
            self._terminal_seen = True
            self._merge_terminal_metadata(observation)
            self._terminal_update(observation)
            self.state = 'finalizing'
            self.connection = 'connected'
        elif observation.kind == 'end':
            if self.preparing_generation is not None and observation.generation == self.preparing_generation:
                self.preparing_generation = None
                self.state = 'ended' if self.adapter is not None else 'waiting'
                self.reason = observation.reason
            elif observation.generation != self.generation:
                return False
            else:
                if not self._archived and observation.reason == 'finished':
                    self._merge_terminal_metadata(observation)
                if observation.sample is not None and not self._archived:
                    self._terminal_update(observation)
                if (self.adapter is not None and self.adapter.statistics.end_time is None and
                        not self._finish_at_verified_watermark(observation)):
                    # Preserve native totals and close events at their last watermark.
                    self._close_unverified_event_end()
                    self.adapter.on_end()
                self._archive(observation.reason)
            self.connection = 'disconnected' if observation.reason == 'process_changed' else 'connected'
        elif observation.kind == 'gap':
            self.freeze(observation.reason)
        else:
            self.connection = 'disconnected' if observation.reason == 'process_changed' else 'connected'
        self.sequence = observation.sequence
        return True

    def live(self):
        return deepcopy(dict(battle_id=self.battle_id, state=self.state,
                             connection=self.connection, reason=self.reason,
                             metadata=self.metadata,
                             event_status=self.event_status, event_error=self.event_error,
                             statistics=self.adapter.result() if self.adapter else None))

    def pop_archives(self):
        # _archive already detached the complete record from live state.
        # Transfer ownership of the list and its records to the caller.
        archives = self._archives
        self._archives = []
        return archives


class BattleView:
    """A GUI history selection never changes the shared live/HUD selection."""

    def __init__(self, sessions):
        self.sessions = sessions
        self.selected = None

    def show_history(self, record):
        self.selected = deepcopy(record)

    def show_live(self):
        self.selected = None

    def gui(self):
        return deepcopy(self.selected) if self.selected is not None else self.sessions.live()

    def hud(self):
        return self.sessions.live()


class BattleRuntime:
    """Shared collector/statistics path for GUI, HUD and history storage."""

    def __init__(self):
        self.feed = BattleFeed()
        self.sessions = BattleSessions(self.feed.id)
        self.view = BattleView(self.sessions)
        # Frozen handoffs outlive the transport session during in-process rescans.
        self.pending_history = deque()
        self.record = lambda observation: None

    def _consume(self, observation):
        self.record(observation)
        try:
            return self.sessions.consume(observation)
        except MappingUnavailable as exc:
            # The native battle and event attachment remain valid while
            # ownership settles. Preserve the queue and the last good view.
            raise MappingRetry(str(exc), observation) from exc

    def _acquire(self, collector, observer):
        return self.feed.acquire(collector, observer,
                                 published_generation=self.sessions.generation,
                                 preparing_generation=self.sessions.preparing_generation)

    def poll(self, collector, observer=None, *, prepare_observer=None):
        """Prepare event recovery only after the feed confirms a new battle."""
        from collector import SampleUnavailable, BattleChanged, ProcessChanged
        from roster import RosterUnavailable
        from tools.stage_global_state.memory import ReadFailure
        try:
            observation = self._acquire(collector, observer)
            if (prepare_observer is not None and observation.kind == 'sample'
                    and observation.generation != self.sessions.generation):
                retry, observer = prepare_observer(observation)
                if retry:
                    # Preserve the confirmed native snapshot and archive the
                    # old battle before a new observer barrier can be busy.
                    # Its event coverage starts only at the fresh paired mark.
                    native = dict(observation.sample,
                                  event_capability=deepcopy(collector.event_capability))
                    self._consume(replace(observation, sample=native, events=None))
                    observation = self._acquire(collector, observer)
            consumed = self._consume(observation)
            if consumed and observer is not None and self.sessions.event_status in ('recording', 'finished'):
                evidence = observation.events or {}
                stream = self.sessions.adapter.event_stream
                if ('drain' in evidence and stream is not None
                        and stream.sequence == evidence['sequence']
                        and stream.frame == observation.sample['frame']):
                    acknowledge_event_batch(observer, stream.sequence)
        except CaptureRetry:
            raise
        except (SampleUnavailable, RosterUnavailable, FrameMismatch, ReadFailure) as exc:
            if isinstance(exc, ProcessChanged):
                self.feed.current_lifecycle = None
                self._consume(self.feed._end('process_changed'))
                raise
            if isinstance(exc, BattleChanged):
                self._consume(self.feed.gap('process_changed'))
                raise
            raise CaptureRetry(str(exc)) from exc
        except Exception:
            self._consume(self.feed.gap('read_unavailable'))
            raise
        return observation

    def disconnected(self, reason):
        observation = self.feed.gap(reason)
        self._consume(observation)
        self.sessions.connection = 'disconnected'
        return observation

    def display_sample(self):
        """Compatibility projection for the existing prototype window."""
        if self.sessions.latest_sample is None:
            return None
        result = self.sessions.adapter.result()
        sample = deepcopy(self.sessions.latest_sample)
        sample['native_rows'] = deepcopy(sample['rows'])
        total = sum(row['damage'] for row in result['rows'].values()
                    if row['damage'] is not None)
        complete, _, _ = team_status(result)
        sample['rows'] = []
        for owner, row in result['rows'].items():
            if row['damage'] is None:
                continue
            sample['rows'].append(dict(
                id=owner, name=row['name'], damage=row['damage'],
                dps=row['damage'] / result['game_time'] if result['game_time'] else None,
                recent_dps=row['recent_damage'],
                share=(row['damage'] / total if total else 0) if complete else None,
                **{key: row['damage_by_type'].get(key)
                   for key in ('physical', 'arts', 'true', 'elemental')}))
        sample['rows'].sort(key=lambda row: row['damage'], reverse=True)
        sample['sum_damage'] = total
        sample['statistics_complete'] = complete
        sample['state'] = 3 if result['ended'] else 2
        stream = self.sessions.adapter.event_stream
        sample.update(statistics=result, battle_id=self.sessions.battle_id,
                      lifecycle_state=self.sessions.state,
                      event_status=self.sessions.event_status, event_error=self.sessions.event_error,
                      snapshot_revision=result.get('snapshot_revision'),
                      event_watermark=(dict(sequence=stream.sequence, frame=stream.frame,
                                            game_time=stream.game_time) if stream else None))
        return sample
