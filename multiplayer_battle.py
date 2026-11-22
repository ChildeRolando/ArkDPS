"""Round-scoped Autochess observations, using the ordinary statistics core.

This module never manufactures native cumulative counters. A player side is a
BattlePlayerData key inside one identity epoch, not a persistent person ID.
Call consume only after the owning capture accepted its guarded event batch.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite

from character_catalog import load_character_names
from event_stream import EventStream, StreamGap
from event_capture import apply_event_quality
from event_probe import contract_for
from roster import CharacterEntry, RosterSnapshot
from source_classification import classify_unmapped_source
from stats_core import ENEMY_STATE_KINDS, Statistics


MAX_PREVIOUS_ROUNDS = 8
SUPPORTED_SETTLEMENT_VERSIONS = (3, 4, 5, 6, 7)


def _number(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def _integer(value):
    return type(value) is int and value >= 0


def _key(entry):
    return f'{entry.cid}#{entry.unique_id}'


@dataclass
class _Player:
    statistics: Statistics
    stream: EventStream | None = None
    actors: dict[str, CharacterEntry] = field(default_factory=dict)


class MultiplayerBattleStatistics:
    """Four display slots with UID-isolated, explicitly observed output.

    consume(sample, roster, sequence, drained, mark=..., round_id=...) returns
    an independent DTO; non-Autochess stages return None. gap() and finish()
    retain observed evidence. result() includes frozen previous identity epochs.
    """

    def __init__(self):
        self._names = load_character_names()
        self._enabled = False
        self._base_round = None
        self._epoch = -1
        self._players = {}
        self._roster = {}
        self._operators = {}
        self._received = {}
        self._pending = {}
        self._baseline = self._sequence = None
        self._now = self._frame = self._start = None
        self._local_side = None
        self._local_sides = set()
        self._closed = self._ended = False
        self._reasons = set()
        self._unknown = {}
        self._previous = []
        self._previous_pruned = 0
        self._retired_rounds = set()
        self._retired_identities = set()
        self._scope = None
        self._from_start = False
        self._sample_session = self._native_session = None
        self._settlement_version = None

    def _begin(self, sample, round_id, reason):
        if self._players:
            old_identity = (self._base_round, self._sample_session, self._native_session)
            new_identity = (round_id, sample.get('session'), sample.get('native_session'))
            if old_identity != new_identity:
                self._retired_identities.add(old_identity)
            self.finish(complete=False)
            frozen = self._result(include_previous=False)
            # Keep bounded summaries of retired identities, not repeated full
            # enemy-state journals and per-actor core snapshots. The lifecycle
            # remains the owner of durable battle archives.
            for player in frozen['players']:
                player.pop('statistics', None)
                player.pop('actor_ledgers', None)
            frozen.pop('unknown_attribution', None)
            frozen['retired_reason'] = reason
            self._previous.append(frozen)
            if len(self._previous) > MAX_PREVIOUS_ROUNDS:
                self._previous.pop(0)
                self._previous_pruned += 1
            if round_id != self._base_round:
                self._retired_rounds.add(self._base_round)
        self._enabled = True
        self._base_round = round_id
        self._epoch += 1
        self._scope = f'{round_id}:players:{self._epoch}'
        self._start = self._now = sample['game_time']
        self._frame = sample['frame']
        self._from_start = self._start <= 1 and self._frame <= 30
        self._sample_session = sample.get('session')
        self._native_session = sample.get('native_session')
        self._players = {}
        for side in range(1, 5):
            stats = Statistics(f'{self._scope}:{side}', self._start)
            stats.settlement_sides_known = True
            # An empty counter snapshot establishes the clock only. No source
            # receives a fabricated damage/healing counter.
            stats.snapshot(self._start, {})
            self._players[side] = _Player(stats)
        self._roster = {}
        self._operators = {}
        self._received = {}
        self._pending = {}
        self._baseline = self._sequence = None
        self._local_side = None
        self._local_sides = set()
        self._closed = self._ended = False
        self._reasons = set()
        self._unknown = {}
        self._settlement_version = None

    @staticmethod
    def _operator_map(roster):
        return {e.unique_id: e for e in roster.entries
                if not e.is_token and not e.is_predefined and e.player_side in range(1, 5)}

    def _identity_change(self, roster):
        current = self._operator_map(roster)
        if not current or not self._operators:
            return None
        shared = current.keys() & self._operators.keys()
        if any(current[uid].player_side != self._operators[uid].player_side for uid in shared):
            return '玩家阵营重新编号'
        # Normal retreat leaves roster data intact; tokens and partial roster
        # changes do not reset an epoch. A disjoint full operator replacement
        # cannot safely inherit a side's old account.
        if not shared:
            return '编队角色身份整体替换'
        return None

    def _bind(self, roster):
        for entry in roster.entries:
            if type(entry.unique_id) is not int or entry.unique_id <= 0:
                continue
            self._roster.setdefault(entry.unique_id, set()).add(entry)
        if any(len(entries) > 1 for entries in self._roster.values()):
            self._reasons.add('编队UID存在身份冲突')
            for player in self._players.values():
                player.statistics.settlement_stream_incomplete = True
        current = self._operator_map(roster)
        if current:
            self._operators = current
        side = roster.local_player_side
        if type(side) is int and side in self._players:
            self._local_sides.add(side)
            self._local_side = side if len(self._local_sides) == 1 else None
        for entries in self._roster.values():
            if len(entries) != 1:
                continue
            entry, = entries
            if entry.is_token or entry.player_side not in self._players:
                continue
            player = self._players[entry.player_side]
            owner = _key(entry)
            player.actors[owner] = entry
            player.statistics.register_source(owner, owner)

    def _identity(self, event):
        source = event.get('source')
        if (source is None and event.get('damage_type') == 5
                and event.get('kind') in ('damage_settlement', 'elemental_burst')):
            # The core can recover a sourceless elemental settlement from a
            # verified preceding bar's player context and actor UID. Preserve
            # that causal evidence, including negative foreign-bar witnesses.
            return 'elemental', None, None, None
        if source is not None and classify_unmapped_source(source) is not None:
            return 'non_team', None, None, None
        if event.get('kind') in ENEMY_STATE_KINDS or event.get('kind') == 'enemy_damage':
            if source is None:
                return 'global', None, None, None
        uid = event.get('source_character_uid')
        if type(uid) is not int or uid <= 0:
            return 'source_uid_missing', None, None, None
        entries = self._roster.get(uid, ())
        if len(entries) != 1:
            return ('source_uid_conflict' if entries else 'source_uid_unmapped'), None, None, None
        entry, = entries
        if entry.cid != source:
            return 'source_uid_cid_mismatch', None, None, None
        actor = entry
        if entry.is_token:
            hosts = self._roster.get(entry.host_uid, ())
            if len(hosts) != 1:
                return 'token_host_unverified', None, None, None
            actor, = hosts
            if (actor.is_token or actor.cid != entry.host_key
                    or actor.player_side != entry.player_side):
                return 'token_host_unverified', None, None, None
        if actor.player_side not in self._players:
            return 'player_side_unverified', None, None, None
        return 'owned', actor.player_side, actor, entry

    def _diagnose(self, event, reason):
        # Retain real missing values, never assign them to a side by CID.
        key = (reason, event.get('source'), event.get('source_character_uid'), event.get('kind'))
        item = self._unknown.setdefault(key, dict(reason=reason, source=event.get('source'),
            source_character_uid=event.get('source_character_uid'), kind=event.get('kind'),
            events=0, first_sequence=event['sequence'], last_sequence=event['sequence']))
        item['events'] += 1
        item['last_sequence'] = event['sequence']
        if event.get('kind') == 'damage_settlement':
            self._reasons.add('存在来源UID未确认的结算')
            for player in self._players.values():
                player.statistics.settlement_stream_incomplete = True

    def _start_events(self, sequence, version):
        self._baseline = self._sequence = sequence
        self._settlement_version = version
        for player in self._players.values():
            stats = player.statistics
            stats.partial_damage_enabled = stats.settlement_enabled = True
            stats.damage_settlement_version = version
            stats.event_coverage = True
            stats.start_late_event_coverage()
            player.stream = EventStream(stats, session=stats.session,
                baseline_frame=self._frame, baseline_sequence=sequence)
            stats.mark_boss_watermark(self._now)

    def _batch(self, sample, through, drained):
        """Validate the entire ordered prefix before any player's accounting."""
        pending = dict(self._pending)
        for event in drained.get('events', ()):
            if not isinstance(event, dict) or not _integer(event.get('sequence')):
                raise StreamGap('invalid multiplayer event record')
            seq = event['sequence']
            if seq <= self._baseline:
                continue
            prior = self._received.get(seq, pending.get(seq))
            if prior is not None:
                if prior != event:
                    raise StreamGap('multiplayer event replay conflicts')
                continue
            pending[seq] = deepcopy(event)
        events = [event for seq, event in pending.items() if seq <= through]
        last_seq = self._sequence
        clock = self._now
        frame = self._frame
        # Dict insertion order retains delivery order. Sorting would conceal
        # a malformed producer batch instead of reporting the real gap.
        for event in events:
            seq = event['sequence']
            when, tick = event.get('game_time'), event.get('frame')
            if seq != last_seq + 1:
                raise StreamGap('multiplayer event sequence gap or order conflict')
            if (not _number(when) or not _integer(tick) or when < clock or tick < frame
                    or (tick == frame and when != clock)
                    or when > sample['game_time'] or tick > sample['frame']):
                raise StreamGap('multiplayer event clock is out of order')
            last_seq, clock, frame = seq, when, tick
        if through != last_seq:
            raise StreamGap('multiplayer watermark has a missing tail')
        return events, {seq: event for seq, event in pending.items() if seq > through}

    def _quality(self, sample, drained, damage_quality, *, initial):
        payload = contract_for(sample=sample, descriptor=drained.get('probe_contract')).normalize_quality(drained)
        for side, player in self._players.items():
            scoped = dict(payload)
            for field in ('unknown_sources', 'engagement_unknown_sources'):
                scoped[field] = [
                    _key(entry) for entries in self._roster.values() for entry in entries
                    if entry.player_side == side and entry.cid in (payload.get(field) or ())]
            apply_event_quality(player.statistics, scoped, damage_quality,
                                baseline_frame=player.stream.baseline_frame, initial_drain=initial)

    def consume(self, sample, roster: RosterSnapshot, sequence=None, drained=None, *, mark=None,
                round_id=None, return_result=True):
        stage = str((sample.get('battle_metadata') or {}).get('stage_id') or '')
        if 'AUTOCHESS' not in stage.upper():
            return None
        self._consume(sample, roster, sequence, drained, mark=mark, round_id=round_id)
        if return_result:
            return self.result()

    def _consume(self, sample, roster, sequence, drained, *, mark, round_id):
        if (not _number(sample.get('game_time')) or not _integer(sample.get('frame'))
                or not isinstance(roster, RosterSnapshot) or roster.frame != sample['frame']):
            self.gap('快照或编队短读')
            return
        base = round_id if round_id is not None else sample.get('session')
        if not isinstance(base, str) or not base:
            self.gap('小局身份未确认')
            return
        if base in self._retired_rounds:
            return
        if (base, sample.get('session'), sample.get('native_session')) in self._retired_identities:
            return
        reason = None
        if self._base_round != base:
            reason = '小局切换'
        elif (sample.get('session') != self._sample_session
              or self._native_session is not None and sample.get('native_session') is not None
              and sample['native_session'] != self._native_session):
            reason = '采集会话或原生小局身份切换'
        elif sample['game_time'] < self._now or sample['frame'] < self._frame:
            reason = '游戏时钟重新开始'
        else:
            reason = self._identity_change(roster)
        if reason is not None:
            self._begin(sample, base, reason)
        elif self._ended:
            return
        self._bind(roster)
        if self._closed:
            return
        drained = drained or {}
        if sequence is None:
            sequence = (mark or {}).get('sequence', drained.get('sequence'))
        if not _integer(sequence):
            if self._baseline is not None:
                self.gap('事件水位缺失')
            else:
                self._now, self._frame = sample['game_time'], sample['frame']
                for player in self._players.values():
                    player.statistics.advance(self._now)
            return
        if drained.get('error') or drained.get('stopped'):
            self.gap(str(drained.get('error') or '事件观察已停止'))
            return
        quality = mark if mark is not None else drained
        version = drained.get('partial_damage_version', quality.get('partial_damage_version'))
        if type(version) is not int or version not in SUPPORTED_SETTLEMENT_VERSIONS:
            self._reasons.add('未取得支持的结算事件版本')
            if self._baseline is not None:
                self.gap('结算事件版本丢失或不受支持')
            return
        if self._settlement_version is not None and version != self._settlement_version:
            self.gap('结算事件版本发生变化')
            return
        ready = quality.get('damage_settlement_ready', quality.get('enemy_damage_ready'))
        unknown = quality.get('damage_settlement_unknown', quality.get('enemy_damage_unknown'))
        if ready is not True or unknown is not False:
            self._reasons.add('结算事件质量未完整确认')
            for player in self._players.values():
                player.statistics.settlement_stream_incomplete = True
        if self._baseline is None:
            self._now, self._frame = sample['game_time'], sample['frame']
            self._start = self._now
            self._from_start &= self._start <= 1 and self._frame <= 30
            for player in self._players.values():
                player.statistics.advance(self._now)
            self._start_events(sequence, version)
            self._quality(sample, drained, quality, initial=True)
            return
        try:
            events, pending = self._batch(sample, sequence, drained)
            self._quality(sample, drained, quality, initial=False)
            for player in self._players.values():
                player.statistics.advance(sample['game_time'])
            # The accepted snapshot clock and the last reliable event endpoint
            # are separate. A later per-event failure freezes stream coverage,
            # while every scoped core retains this already accepted clock.
            self._now, self._frame = sample['game_time'], sample['frame']
            for event in events:
                state, side, actor, entry = self._identity(event)
                if state not in ('owned', 'global', 'non_team', 'elemental'):
                    self._diagnose(event, state)
                elemental_before = None
                if state == 'elemental' and event['kind'] == 'damage_settlement':
                    elemental_before = {s: (sum(m.hits for m in p.statistics.settled.values()),
                        p.statistics.settlement_unassigned['hits']) for s, p in self._players.items()}
                for player_side, player in self._players.items():
                    payload = {key: value for key, value in event.items() if key != 'proof'}
                    proof = event.get('proof')
                    if event.get('kind') in ('skill_start', 'skill_end') and isinstance(proof, dict):
                        payload['skill_method'] = proof.get('method')
                    identity = ('unknown', None, None)
                    unresolved_source_keys = ()
                    if state == 'owned':
                        if side == player_side:
                            owner, source = _key(actor), _key(entry)
                            player.statistics.register_source(source, owner)
                            payload['source'] = source
                            identity = ('summon', None, None) if entry.is_token and event['kind'] in (
                                'skill_start', 'skill_end') else ('local', owner, actor.unique_id)
                        else:
                            identity = ('foreign', None, None)
                    elif state == 'non_team':
                        identity = ('foreign', None, None)
                    elif state not in ('global', 'elemental'):
                        payload['unresolved_source'] = True
                        # These verified actors are candidates for uncertainty,
                        # never recipients of this event's amount or window.
                        # The receiver decides whether native cast evidence is
                        # an ignorable passive observation before marking them.
                        unresolved_source_keys = tuple(owner for owner, candidate in player.actors.items()
                            if candidate.cid == event.get('source'))
                    player.stream.accept(None, unresolved_source_keys, session=player.statistics.session,
                        settlement_identity=identity, replay_record=event, **payload)
                if elemental_before is not None:
                    resolved = any(sum(m.hits for m in p.statistics.settled.values()) > elemental_before[s][0]
                                   for s, p in self._players.items())
                    unresolved = any(p.statistics.settlement_unassigned['hits'] > elemental_before[s][1]
                                     for s, p in self._players.items())
                    if not resolved and unresolved:
                        self._diagnose(event, 'elemental_owner_unverified')
                self._received[event['sequence']] = deepcopy(event)
            for player in self._players.values():
                player.stream.watermark(session=player.statistics.session, sequence=sequence,
                    frame=sample['frame'], game_time=sample['game_time'])
            self._pending = pending
            self._sequence = sequence
            self._now, self._frame = sample['game_time'], sample['frame']
        except (StreamGap, ValueError, TypeError, OverflowError) as exc:
            self.gap(str(exc))
        return

    def gap(self, reason='采集缺口'):
        if not self._players or self._ended:
            return
        self._closed = True
        self._reasons.add(reason)
        for player in self._players.values():
            if player.stream is not None:
                player.statistics.mark_boss_watermark(player.stream.game_time)
                player.stream.stop()
            player.statistics.settlement_stream_incomplete = True

    def finish(self, game_time=None, *, complete=False):
        if not self._players or self._ended:
            return self.result()
        if game_time is not None and game_time != self._now:
            self.gap('终局时间缺少匹配的事件水位')
            complete = False
        if not complete:
            self._reasons.add('观察范围已结束，终局覆盖未确认')
        for player in self._players.values():
            if player.stream is not None:
                player.stream.stop(complete=complete and not self._closed)
            if player.statistics.end_time is None:
                player.statistics.finish(player.statistics.now)
        self._ended = self._closed = True
        return self.result()

    def _player_result(self, side, player):
        stats = player.statistics.result()
        stats['totals_complete'] = False
        ledgers = []
        for owner, entry in sorted(player.actors.items()):
            row = stats['rows'].get(owner)
            if row is None:
                continue
            name = self._names.get(entry.cid, entry.cid)
            row.update(id=owner, cid=entry.cid, unique_id=entry.unique_id,
                       name=f'{name} · {entry.unique_id}')
            lane = row.get('settled_damage', {})
            row['observed_damage'] = lane.get('observed_damage')
            ledgers.append(dict(cid=entry.cid, source_character_uid=entry.unique_id,
                                unique_id=entry.unique_id, uid=entry.unique_id, actor_uid=entry.unique_id,
                                name=name, owner_id=owner, ledger=deepcopy(lane)))
        observed = [item['ledger'] for item in ledgers if item['ledger'].get('nominal_hits', 0)]
        has_output = bool(observed)
        reasons = set(self._reasons)
        reasons.add('没有按该玩家独立读取的原生累计')
        if not has_output:
            reasons.add('未观察到可按UID归属的名义结算')
        if not self._from_start:
            reasons.add('仅覆盖已观察游戏时间窗口')
        complete = (has_output and self._from_start and not self._reasons
                    and all(lane.get('observed_damage_complete') for lane in observed))
        values = lambda field: [lane[field] for lane in observed if lane.get(field) is not None]
        def total(field):
            parts = values(field)
            return sum(parts) if parts else None
        known_side = any(entry.player_side == side for entries in self._roster.values()
                         for entry in entries)
        end = (player.statistics.event_coverage_end if player.statistics.event_coverage_end is not None
               else self._now)
        return dict(player_side=side, label=f'本局阵营 {side}',
            is_local=(side == self._local_side) if self._local_side is not None and known_side else None,
            status=('unknown' if not has_output else 'observed' if complete else 'partial'),
            damage=None, dps=None, recent_dps=None,
            observed_damage=total('observed_damage'), observed_dps=total('observed_dps'),
            observed_recent_dps=total('observed_recent_damage'),
            observed_window=[player.statistics.event_coverage_start, end] if has_output else None,
            observed_recent_window=next((list(lane['observed_recent_damage_window']) for lane in observed
                                        if lane.get('observed_recent_damage_window') is not None), None),
            coverage_complete=bool(complete), reasons=sorted(reasons),
            statistics=stats, actor_ledgers=ledgers)

    def _result(self, *, include_previous):
        if not self._enabled or not self._players:
            return None
        players = [self._player_result(side, self._players[side]) for side in range(1, 5)]
        observed_sides = [player['player_side'] for player in players if player['observed_damage'] is not None]
        result = dict(schema_version=1, available=True,
            status='ended' if self._ended else 'gap' if self._closed else 'observing',
            round_id=self._scope, source_round_id=self._base_round, identity_epoch=self._epoch,
            game_time=self._now, coverage_start=self._start, expected_player_slots=4,
            observed_player_sides=observed_sides,
            note='编号仅对当前小局有效。结算伤害来自已观察事件，平均 DPS 按观察到的游戏时长计算；未知表示证据不足。',
            players=players, unknown_attribution=list(deepcopy(self._unknown).values()),
            reasons=sorted(self._reasons))
        if include_previous:
            result['previous_rounds'] = deepcopy(self._previous)
            result['previous_rounds_pruned'] = self._previous_pruned
        return result

    def result(self):
        return deepcopy(self._result(include_previous=True))
