"""Ordered, process-local battle observations and exactly-once archive handoff.

The feed has one acquisition owner. Consumers may queue observations, but must
keep their generation and sequence. Addresses and frames are not battle IDs.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
import math
import uuid

from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch, AdapterError
from event_capture import LiveEventCapture, CaptureRetry
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

    def _emit(self, kind, sample=None, roster=None, reason='', events=None):
        self.sequence += 1
        return BattleObservation(self.id, self.sequence, self.generation,
                                 kind, deepcopy(sample), roster, reason, deepcopy(events),
                                 deepcopy(self.current_lifecycle))

    def gap(self, reason='read_unavailable'):
        self.current_lifecycle = None
        return self._emit('gap', reason=reason)

    def _end(self, reason, sample=None, roster=None):
        if self.identity is None:
            return self._emit('waiting', reason=reason)
        self.retired, self.retired_clock = self.identity, self.clock
        self.retired_session = self.native_session
        self.retired_stage = self.stage
        self.identity = self.clock = self.native_session = None
        self.stage = None
        self.terminal = False
        return self._emit('end', sample, roster, reason=reason)

    def _read_pair(self, collector, before, observer=None):
        collector.bind_battle(before['battle'][0])
        events = None
        mark = None
        if observer is not None:
            try:
                mark = observer.mark()
                if mark.get('error') or mark.get('stopped'):
                    raise StreamGap('observer stopped or reported a callback error')
                if mark.get('retry'):
                    raise CaptureRetry('observer clock advanced')
            except CaptureRetry:
                raise
            except Exception as exc:
                events = {'error': str(exc)}
        sample = collector.read()
        if hasattr(collector, 'event_capability'):
            sample['event_capability'] = deepcopy(collector.event_capability)
        roster = collector.read_roster()
        if mark is not None and events is None:
            try:
                end_mark = observer.mark()
                if end_mark.get('retry'):
                    raise CaptureRetry('observer clock advanced')
                sequence = baseline_barrier(mark, sample, end_mark)
                # Drain only after the lifecycle bracket below has succeeded.
                events = dict(before=mark, after=end_mark, sequence=sequence)
            except (CaptureRetry, BarrierBusy) as exc:
                raise CaptureRetry(str(exc)) from exc
            except Exception as exc:
                events = {'error': str(exc)}
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
        if events is not None and 'error' not in events:
            try:
                events['drain'] = observer.drain()
            except Exception as exc:
                events = {'error': str(exc)}
        return sample, roster, events

    def acquire(self, collector, observer=None):
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
            observation = self._end('battle_absent')
            self.retired = self.retired_clock = None
            self.retired_session = None
            return observation
        identity = (*collector.core.mc.process_instance,
                    collector.core.mc.pid, *battle)
        exiting = (before['transiting'] and before['source'].startswith('level_')
                   and before['target'] == 'battle_finish')
        if before.get('state') == 3 or exiting:
            # A singleton replacement may be observed only after it finished.
            # It cannot provide a result for the previously observed battle.
            if self.identity == identity and not stage_changed and before.get('state') == 3 and not exiting:
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
                    self.terminal = True
                    return self._emit('terminal', reason='finished_snapshot_unavailable')
                if sample['session'] == self.native_session and sample['game_time'] >= self.clock:
                    sample['native_session'] = sample['session']
                    sample['session'] = f'{self.id}:{self.generation}'
                    self.terminal = True
                    self.clock = sample['game_time']
                    return self._emit('terminal', sample, roster, events=events)
            return self._end('finished' if self.identity == identity and not stage_changed else 'replaced')
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
                return self._end('finished')
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
        changed = (self.identity != identity or stage_changed or self.native_session != sample['session']
                   or (self.clock is not None and now < self.clock))
        if changed:
            self.generation += 1
            self.terminal = False
            self.stage = stage
        elif stage:
            self.stage = stage
        self.identity, self.clock = identity, now
        self.native_session = sample['session']
        sample['native_session'] = sample['session']
        sample['session'] = f'{self.id}:{self.generation}'
        return self._emit('sample', sample, roster, events=events)


class BattleSessions:
    """Live statistics, independent connection state, and archive signals.

    pop_archives transfers independent copies once in this process. The history
    store deduplicates by battle_id for durable idempotence.
    """

    def __init__(self, feed_id):
        self.feed_id = feed_id
        self.sequence = 0
        self.generation = 0
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
            self.event_status = 'recording'
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

    def _archive(self, reason):
        if self.adapter is None or self._archived:
            return
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
            self._terminal_gap(observation.reason)
            return
        if observation.sample['session'] != self.battle_id:
            raise ValueError('final sample does not belong to the live battle')
        previous_adapter, previous_capture = self.adapter, self.capture
        previous_status, previous_error = self.event_status, self.event_error
        final_adapter = deepcopy(self.adapter)
        self.capture = deepcopy(self.capture)
        self.capture.adapter = final_adapter
        try:
            # Accept the last verified event watermark before freezing time.
            # Later same-frame revisions change native counters only.
            if final_adapter.statistics.end_time is None and observation.events:
                final_adapter.update(observation.sample, observation.roster)
                self.adapter = final_adapter
                self.capture.adapter = final_adapter
                self._events(observation)
            final_adapter.update(observation.sample, observation.roster, terminal=True)
        except (AdapterError, ValueError) as exc:
            self.adapter, self.capture = previous_adapter, previous_capture
            self.event_status, self.event_error = previous_status, previous_error
            self._terminal_gap(str(exc))
        else:
            self.adapter = final_adapter
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
        self.capture.close()
        if self.event_status == 'recording':
            self.event_status, self.event_error = 'gap', reason
        self.metadata['final_sample_error'] = reason

    def _merge_terminal_metadata(self, observation):
        if self.latest_sample is None or not observation.lifecycle:
            return
        identity = observation.lifecycle.get('battle')
        if identity and tuple(identity) == tuple(self.latest_sample.get('battle_identity', ())):
            self._merge_metadata({'battle_metadata': observation.lifecycle.get('metadata') or {}})

    def consume(self, observation):
        if (observation.feed != self.feed_id or
                observation.sequence <= self.sequence or
                observation.generation < self.generation):
            return False
        if observation.kind not in ('sample', 'terminal', 'gap', 'end', 'waiting'):
            raise ValueError('unknown observation kind')
        if observation.kind == 'sample':
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
                self._archive('new_battle')
                adapter = BattleStatisticsAdapter()
                adapter.update(observation.sample, observation.roster)
                self.adapter = adapter
                self.capture = LiveEventCapture(None)
                self.capture.adapter = adapter
                self.event_status, self.event_error = 'unavailable', None
                self.generation = observation.generation
                self.battle_id = observation.sample['session']
                self._archived = False
                self._terminal_seen = False
                self.metadata = self._metadata(observation.sample)
                self.snapshot_intervals = []
                self._snapshot_open = False
            else:
                self.adapter.update(observation.sample, observation.roster)
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
            if observation.generation != self.generation:
                return False
            if self.adapter is not None and self.adapter.statistics.end_time is None:
                # A vanished/finished battle is a boundary, not a read gap:
                # keep the last complete counters as final totals.
                self.adapter.on_end()
            if not self._archived and observation.reason == 'finished':
                self._merge_terminal_metadata(observation)
            if observation.sample is not None and not self._archived:
                self._terminal_update(observation)
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
        result = list(self._archives)
        self._archives.clear()
        return deepcopy(result)


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
        self.sessions.consume(observation)

    def poll(self, collector, observer=None, *, prepare_observer=None):
        """Prepare event recovery only after the feed confirms a new battle."""
        from collector import SampleUnavailable, BattleChanged, ProcessChanged
        from roster import RosterUnavailable
        from tools.stage_global_state.memory import ReadFailure
        try:
            observation = self.feed.acquire(collector, observer)
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
                    observation = self.feed.acquire(collector, observer)
            self._consume(observation)
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
