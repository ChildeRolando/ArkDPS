"""Ordered, process-local battle observations and exactly-once archive handoff.

The feed has one acquisition owner. Consumers may queue observations, but must
keep their generation and sequence. Addresses and frames are not battle IDs.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import math
import uuid

from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch, AdapterError


@dataclass(frozen=True)
class BattleObservation:
    feed: str
    sequence: int
    generation: int
    kind: str
    sample: dict | None = None
    roster: object | None = None
    reason: str = ''


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

    def _emit(self, kind, sample=None, roster=None, reason=''):
        self.sequence += 1
        return BattleObservation(self.id, self.sequence, self.generation,
                                 kind, deepcopy(sample), roster, reason)

    def gap(self, reason='read_unavailable'):
        return self._emit('gap', reason=reason)

    def _end(self, reason, sample=None, roster=None):
        if self.identity is None:
            return self._emit('waiting', reason=reason)
        self.retired, self.retired_clock = self.identity, self.clock
        self.retired_session = self.native_session
        self.identity = self.clock = self.native_session = None
        self.terminal = False
        return self._emit('end', sample, roster, reason=reason)

    def _read_pair(self, collector, before):
        collector.bind_battle(before['battle'][0])
        sample = collector.read()
        roster = collector.read_roster()
        after = collector.read_lifecycle()
        if before != after or sample['frame'] != roster.frame:
            raise FrameMismatch('生命周期或累计/编队帧在读取期间变化')
        if tuple(sample['battle_identity']) != tuple(before['battle']):
            raise FrameMismatch('快照不属于当前单例战局')
        if sample.get('state') != before.get('state'):
            raise FrameMismatch('快照战斗状态与生命周期状态不一致')
        now = sample['game_time']
        if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
            raise FrameMismatch('游戏时间无效')
        return sample, roster

    def acquire(self, collector):
        """Bracket cumulative/roster reads with the live singleton and flow.

        Failed reads are unknown. Only an observed null/destroyed singleton,
        FINISHED state, finish transition, or a new guarded identity ends a
        battle. No polling timeout, empty unit list or transport error does.
        """
        before = collector.read_lifecycle()
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
            if self.identity == identity and before.get('state') == 3 and not exiting:
                from collector import SampleUnavailable
                from roster import RosterUnavailable
                from tools.stage_global_state.memory import ReadFailure
                try:
                    sample, roster = self._read_pair(collector, before)
                except (SampleUnavailable, RosterUnavailable, AdapterError, ReadFailure):
                    self.terminal = True
                    return self._emit('terminal', reason='finished_snapshot_unavailable')
                if sample['session'] == self.native_session and sample['game_time'] >= self.clock:
                    sample['native_session'] = sample['session']
                    sample['session'] = f'{self.id}:{self.generation}'
                    self.terminal = True
                    self.clock = sample['game_time']
                    return self._emit('terminal', sample, roster)
            return self._end('finished' if self.identity == identity else 'replaced')
        if not before['ready'] or before['transiting']:
            if before.get('state') in (0, 1):
                observation = self._end('battle_initializing')
                self.retired = self.retired_clock = None
                self.retired_session = None
                return observation
            return self.gap('scene_transition')
        sample, roster = self._read_pair(collector, before)
        now = sample['game_time']
        if (self.terminal and identity == self.identity and
                sample['session'] == self.native_session and now >= self.clock):
            return self._emit('waiting', reason='terminal_battle')
        if (self.identity is None and identity == self.retired
                and sample['session'] == self.retired_session):
            if self.retired_clock is None or now >= self.retired_clock:
                return self._emit('waiting', reason='retired_battle')
        changed = (self.identity != identity or self.native_session != sample['session']
                   or (self.clock is not None and now < self.clock))
        if changed:
            self.generation += 1
            self.terminal = False
        self.identity, self.clock = identity, now
        self.native_session = sample['session']
        sample['native_session'] = sample['session']
        sample['session'] = f'{self.id}:{self.generation}'
        return self._emit('sample', sample, roster)


class BattleSessions:
    """Live statistics, independent connection state, and archive signals.

    pop_archives transfers immutable copies once in this process. The future
    history store must deduplicate by battle_id for durable idempotence.
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
        self._archives = deque()

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
            last_sample=self.latest_sample)))
        self._archived = True
        self.state = 'ended'

    def _terminal_update(self, observation):
        if observation.sample is None:
            self.adapter.on_gap()
            self.adapter.finish()
            self.metadata['final_sample_error'] = observation.reason
            return
        if observation.sample['session'] != self.battle_id:
            raise ValueError('final sample does not belong to the live battle')
        final_adapter = deepcopy(self.adapter)
        try:
            final_adapter.update(observation.sample, observation.roster, terminal=True)
        except (AdapterError, ValueError) as exc:
            self.adapter.on_gap()
            self.adapter.finish()
            self.metadata['final_sample_error'] = str(exc)
        else:
            self.adapter = final_adapter
            self.latest_sample = deepcopy(observation.sample)
            self._merge_metadata(observation.sample)

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
                    self._archived or (self.adapter is not None and
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
                self.generation = observation.generation
                self.battle_id = observation.sample['session']
                self._archived = False
                self.metadata = self._metadata(observation.sample)
            else:
                self.adapter.update(observation.sample, observation.roster)
                self._merge_metadata(observation.sample)
            self.latest_sample = deepcopy(observation.sample)
            self.state = 'playing'
            self.connection = 'connected'
            self.reason = ''
        elif observation.kind == 'terminal':
            if (observation.generation != self.generation or self.adapter is None
                    or self._archived):
                return False
            self._terminal_update(observation)
            self.state = 'finalizing'
            self.connection = 'connected'
        elif observation.kind == 'end':
            if observation.generation != self.generation:
                return False
            if self.adapter is not None and self.adapter.statistics.end_time is None:
                self.adapter.on_gap()
            if observation.sample is not None and not self._archived:
                self._terminal_update(observation)
            self._archive(observation.reason)
            self.connection = 'connected'
        elif observation.kind == 'gap':
            self.freeze(observation.reason)
        else:
            self.connection = 'connected'
        self.sequence = observation.sequence
        return True

    def live(self):
        return deepcopy(dict(battle_id=self.battle_id, state=self.state,
                             connection=self.connection, reason=self.reason,
                             metadata=self.metadata,
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
    """Shared collector/statistics path for GUI, HUD and future history store."""

    def __init__(self):
        self.feed = BattleFeed()
        self.sessions = BattleSessions(self.feed.id)
        self.view = BattleView(self.sessions)

    def poll(self, collector):
        try:
            observation = self.feed.acquire(collector)
            self.sessions.consume(observation)
        except Exception:
            self.sessions.consume(self.feed.gap('read_unavailable'))
            raise
        return observation

    def disconnected(self, reason):
        observation = self.feed.gap(reason)
        self.sessions.consume(observation)
        self.sessions.connection = 'disconnected'
        return observation

    def display_sample(self):
        """Compatibility projection for the existing prototype window."""
        if self.sessions.latest_sample is None:
            return None
        result = self.sessions.adapter.result()
        sample = deepcopy(self.sessions.latest_sample)
        total = sum(row['damage'] for row in result['rows'].values()
                    if row['damage'] is not None)
        complete = (not any(result[key] for key in
                            ('unknown_sources', 'unmapped_tokens', 'ambiguous_sources',
                             'mapping_conflicts', 'missing_fields')) and
                    all(row['counter_snapshot_complete'] for row in result['rows'].values()))
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
        return sample
