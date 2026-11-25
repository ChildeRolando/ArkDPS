"""Round-scoped Autochess observations, using the ordinary statistics core.

This module never manufactures native cumulative counters. Legacy player sides
remain round-local keys; verified native contexts use actual player account IDs.
Call consume only after the owning capture accepted its guarded event batch.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite

from autochess_context import (lookup_native_actor, native_actor_lookup, native_autochess_event_identity,
                              native_autochess_scope_id, resolve_native_event_member)
from character_catalog import load_character_names
from event_stream import EventStream, StreamGap
from event_capture import apply_event_quality
from event_probe import (contract_for, event_processing_payload, autochess_round_start_live_skill_uids,
                         autochess_round_start_origin, settlement_source_quality_clear,
                         verified_autochess_settlement_source_issues)
from roster import CharacterEntry, RosterSnapshot
from source_classification import classify_unmapped_source
from stats_core import ENEMY_STATE_KINDS, Statistics


MAX_PREVIOUS_ROUNDS = 8
MAX_BASELINE_SKILL_EVENTS = 1024
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
    slot: int | None = None
    player_uid: str | None = None
    player_name: str | None = None
    observed_presence: dict = field(default_factory=dict)
    observed_skill_time: dict = field(default_factory=dict)
    skill_timed_presence: dict = field(default_factory=dict)
    unknown_skill_prefix: dict = field(default_factory=dict)

    def mark_unknown_skill_prefix(self, owner, actor, *reasons):
        # This annotates the existing uncertainty; it never certifies a state.
        self.unknown_skill_prefix.setdefault(owner, set()).update(reasons)
        self.statistics.mark_settlement_skill_initial_unknown(owner, actor)


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
        self._skill_initialization = None
        self._round_start_live_skill_uids = None
        self._sample_session = self._native_session = None
        self._settlement_version = None
        self._native_mode = False
        self._collection_mode = 'all'
        self._mode_declared = False
        self._native_accounts = {}
        self._local_account_uid = None
        self._context = self._active_context = None
        self._previous_context = None
        self._actor_lookup = self._previous_actor_lookup = lookup_native_actor
        self._native_source_claims = {}
        self._native_unverified_source_uids = set()
        self._context_fingerprint = None
        self._retired_contexts = set()
        self._retired_event_scopes = set()
        self._presence_point = None
        self._presence_deployments = {}
        self._presence_skill_replaced = set()
        self._native_entries = {}
        self._native_aliases = {}
        self._player_reasons = {}
        self._scope_foreign_events = 0
        self._scope_unknown_targets = set()
        self._scope_unknown_all_targets = False
        self._scope_verified_targets = set()
        self._terminal_tail = False

    @staticmethod
    def _native_fingerprint(context, sample):
        if (not isinstance(context, dict) or context.get('status') not in ('available', 'partial')
                or context.get('collection_mode', 'all') not in ('all', 'local')
                or context.get('verified_same_frame') is not True
                or context.get('frame') != sample['frame']
                or context.get('game_time') != sample['game_time']):
            return None
        identity = context.get('identity')
        if (not isinstance(identity, dict) or set(identity) != {'scope_key', 'phase', 'round'}
                or identity != {key: context.get(key) for key in identity}
                or not isinstance(identity['scope_key'], str) or not identity['scope_key']
                or type(identity['phase']) is not int or type(identity['round']) is not int):
            return None
        players = context.get('players')
        if not isinstance(players, list) or not players:
            return None
        table = []
        for player in players:
            if (not isinstance(player, dict) or type(player.get('index')) is not int
                    or player['index'] not in range(1, 5)
                    or not isinstance(player.get('uid'), str) or not player['uid']):
                return None
            table.append((player['index'], player['uid']))
        if len({slot for slot, _ in table}) != len(table) or len({uid for _, uid in table}) != len(table):
            return None
        return identity['scope_key'], identity['phase'], identity['round'], tuple(sorted(table))

    def _source_key(self, entry, player_key):
        return f'{player_key}:{entry.cid}#{entry.unique_id}' if self._native_mode else _key(entry)

    def _begin(self, sample, round_id, reason, *, context=None):
        new_context = self._native_fingerprint(context, sample) if context is not None else None
        if self._enabled:
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
            if self._context_fingerprint is not None and self._context_fingerprint != new_context:
                self._retired_contexts.add(self._context_fingerprint)
                self._retired_event_scopes.add(self._context_fingerprint[:3])
        self._enabled = True
        self._base_round = round_id
        self._epoch += 1
        self._native_mode = context is not None
        self._collection_mode = context.get('collection_mode', 'all') if context is not None else 'all'
        self._mode_declared = context is not None and 'collection_mode' in context
        self._native_accounts = {row['uid']: dict(row) for row in context['players']} if context is not None else {}
        self._local_account_uid = context.get('self_player_uid') if context is not None else None
        self._context = self._active_context = deepcopy(context)
        self._previous_context = None
        self._actor_lookup = self._previous_actor_lookup = lookup_native_actor
        self._native_source_claims = {}
        self._native_unverified_source_uids = set()
        self._context_fingerprint = new_context
        self._scope = (native_autochess_scope_id(context['identity'], self._context_fingerprint[3]) if self._native_mode
            else f'{round_id}:players:{self._epoch}')
        self._start = self._now = sample['game_time']
        self._frame = sample['frame']
        self._from_start = self._start <= 1 and self._frame <= 30
        self._skill_initialization = None
        self._round_start_live_skill_uids = None
        self._sample_session = sample.get('session')
        self._native_session = sample.get('native_session')
        self._players = {}
        slots = ([(row['uid'], row['index'], row['nick_name']) for row in context['players']]
                 if self._native_mode else [(side, None, None) for side in range(1, 5)])
        for key, slot, name in slots:
            if self._native_mode and self._collection_mode == 'local' and key != self._local_account_uid:
                continue
            stats = Statistics(f'{self._scope}:{key}', self._start)
            stats.settlement_sides_known = True
            # An empty counter snapshot establishes the clock only. No source
            # receives a fabricated damage/healing counter.
            stats.snapshot(self._start, {})
            self._players[key] = _Player(stats, slot=slot,
                player_uid=key if self._native_mode else None, player_name=name)
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
        self._presence_point = None
        self._presence_deployments = {}
        self._presence_skill_replaced = set()
        self._native_entries = {}
        self._native_aliases = {key: {} for key in self._players}
        self._player_reasons = {key: set() for key in (self._native_accounts if self._native_mode else self._players)}
        self._scope_foreign_events = 0
        self._scope_unknown_targets = set()
        self._scope_unknown_all_targets = False
        self._scope_verified_targets = set()

    def invalidate_presence(self):
        """Break optional timing adjacency without closing the event stream."""
        self._presence_point = None

    def _observe_presence(self, sample):
        if 'scoped_presence' not in sample:
            return
        previous, self._presence_point = self._presence_point, None
        point = sample['scoped_presence']
        anchor = sample.get('autochess_snapshot_anchor', sample)
        platform = (sample.get('battle_metadata') or {}).get('platform')
        sample_seq = None
        basis = 'snapshot'
        if isinstance(point, dict) and ('binding' in point or 'version' in point):
            if (type(point.get('version')) is not int or point['version'] != 1
                    or point.get('binding') != 'event_mark'
                    or platform != 'android'
                    or sample.get('native_counter_scope') != 'events_only'
                    or not _integer(point.get('sample_seq')) or point['sample_seq'] == 0):
                return
            # Android optional units are read at the accepted closing mark,
            # independently of the earlier physical counter snapshot.
            anchor = sample
            sample_seq = point['sample_seq']
            basis = 'event_mark'
        elif isinstance(point, dict):
            if platform == 'android':
                return
            if 'sample_seq' in point:
                if (platform != 'native_pc'
                        or sample.get('native_counter_scope') != 'events_only'
                        or not _integer(point['sample_seq']) or point['sample_seq'] == 0):
                    return
                sample_seq = point['sample_seq']
        if (not self._native_mode or not self._active_context or self._terminal_tail
                or not isinstance(point, dict) or not isinstance(point.get('units'), list)
                or not _integer(point.get('frame')) or not _number(point.get('game_time'))
                or point['frame'] != anchor.get('frame') or point['game_time'] != anchor.get('game_time')
                or point['game_time'] > sample['game_time']):
            return
        when = point['game_time']
        actors = {}
        for unit in point['units']:
            if not isinstance(unit, dict) or not _integer(unit.get('data_uid')):
                continue
            match = self._native_entries.get(unit['data_uid'])
            if not match:
                continue
            uid, entry = match
            if (uid not in self._players or entry.is_token or unit.get('cid') != entry.cid
                    or type(unit.get('player_side')) is not int or unit['player_side'] != entry.player_side
                    or not _integer(unit.get('instance_uid')) or not unit['instance_uid']):
                continue
            key = (uid, self._source_key(entry, uid), entry.unique_id)
            actors[key] = unit['instance_uid'] if key not in actors else None
        # Only matching deployments on adjacent observed endpoints earn time.
        # A roster member, missing unit or replaced instance earns no interval.
        known = {}
        for key, instance in actors.items():
            uid, owner, actor = key
            player = self._players[uid]
            if instance is None or self._presence_deployments.get(key, instance) != instance:
                self._presence_skill_replaced.add(key)
            if instance is not None:
                self._presence_deployments.setdefault(key, instance)
            # Skill callbacks identify data UID, not the native deployment.
            # A replacement body cannot inherit the old body's open window.
            # Keep proven earlier intervals; its real presence still counts.
            skill = (player.statistics.observed_skill_interval(owner, actor, when, when)
                     if key not in self._presence_skill_replaced else None)
            if skill is not None:
                known[key] = player.statistics.skill_observation_revision(owner, actor)
            if previous is None or not instance or previous[1].get(key) != instance:
                continue
            start, _, prior_known, watermark, prior_seq, prior_basis = previous
            if basis != prior_basis:
                continue
            if sample_seq is not None and (prior_seq is None or sample_seq != prior_seq + 1):
                continue
            if sample_seq is None and prior_seq is not None:
                continue
            delta = when - start
            if not 0 < delta <= 5:
                continue
            player.observed_presence[owner] = player.observed_presence.get(owner, 0.0) + delta
            if (key in known and key in prior_known and known[key] == prior_known[key]
                    and watermark < when):
                overlap = player.statistics.observed_skill_interval(owner, actor, watermark, when)
                if _number(overlap) and overlap <= when - watermark:
                    player.observed_skill_time[owner] = player.observed_skill_time.get(owner, 0.0) + overlap
                    player.skill_timed_presence[owner] = player.skill_timed_presence.get(owner, 0.0) + (when - watermark)
        self._presence_point = when, actors, known, sample['game_time'], sample_seq, basis

    def _bind_native(self, context):
        self._previous_context = self._active_context
        self._previous_actor_lookup = self._actor_lookup
        self._context = self._active_context = deepcopy(context)
        self._actor_lookup = native_actor_lookup(self._active_context)
        self._mode_declared = self._mode_declared or 'collection_mode' in context
        for row in context['players']:
            self._native_accounts[row['uid']] = dict(row)
            if row['uid'] in self._players:
                self._players[row['uid']].player_name = row['nick_name']
        entries = {}
        for member in context['memberships']:
            uid = member['player_uid']
            entry = CharacterEntry(member['cid'], member['data_uid'], member['is_token'],
                member['is_predefined'], member['host_key'], member['host_uid'], member['player_side'])
            entries[entry.unique_id] = uid, entry
            host_key = entry.host_key if entry.is_token else entry.cid
            host_uid = entry.host_uid if entry.is_token else entry.unique_id
            self._native_source_claims.setdefault((entry.cid, entry.unique_id), set()).add((uid, host_key, host_uid))
            if not entry.is_token and uid in self._players:
                player = self._players[uid]
                owner = self._source_key(entry, uid)
                if owner not in player.actors and self._baseline is not None:
                    unresolved_prefix = any(
                        item['source'] == entry.cid and item['kind'] in ('skill_start', 'skill_end')
                        and item['reason'] != 'event_scope_mismatch'
                        and (item['source_character_uid'] == entry.unique_id
                             or type(item['source_character_uid']) is not int
                             or item['source_character_uid'] <= 0)
                        for item in self._unknown.values())
                    live_uids = self._round_start_live_skill_uids
                    if unresolved_prefix:
                        reason = 'actor_has_unverified_skill_prefix'
                    elif live_uids is None:
                        reason = 'actor_joined_after_baseline'
                    elif entry.unique_id in live_uids:
                        reason = 'round_start_actor_already_live'
                    else:
                        reason = None
                    if reason is not None:
                        player.mark_unknown_skill_prefix(owner, entry.unique_id, reason)
                player.actors[owner] = entry
                player.statistics.register_source(owner, owner)
        self._native_entries = entries
        for uid, entry in entries.values():
            if uid not in self._players:
                continue
            host = entries.get(entry.host_uid) if entry.is_token else (uid, entry)
            if host is None or host[0] != uid:
                continue
            source, owner = self._source_key(entry, uid), self._source_key(host[1], uid)
            self._players[uid].statistics.register_source(source, owner)
            self._native_aliases[uid].setdefault(entry.cid, set()).add(source)
        table = {row['index']: row['uid'] for row in context['players']}
        for unknown in context['unknown_memberships']:
            data_uid = unknown.get('data_uid')
            if type(data_uid) is int and 0 < data_uid <= 0xffffffff:
                self._native_unverified_source_uids.add(data_uid)
            if classify_unmapped_source(unknown.get('cid')) is not None:
                continue
            index = unknown.get('player_index', unknown.get('candidate_player_index'))
            uid = table.get(index)
            if uid in self._player_reasons:
                self._player_reasons[uid].add('该玩家存在未核实的角色归属')
            else:
                self._reasons.add('存在无法按玩家隔离的角色归属缺证')

    @staticmethod
    def _scope_stamp_identity(event):
        return native_autochess_event_identity(event)

    def _native_event_scope(self, event):
        identity = self._scope_stamp_identity(event)
        if self._active_context is None or identity is None:
            return 'event_scope_unverified'
        if identity == self._context_fingerprint[:3]:
            stamp = event.get('native_autochess_scope') or {}
            if 'collection_mode' in stamp and stamp['collection_mode'] != self._collection_mode:
                return 'event_scope_mismatch'
            return None
        if self._terminal_tail or identity in self._retired_event_scopes:
            return 'foreign_scope'
        return 'event_scope_mismatch'

    @staticmethod
    def _native_initial_rejections(sample):
        reasons = []
        if 'native_snapshot_witnesses' in sample:
            witnesses = sample['native_snapshot_witnesses']
            actor_rows = witnesses.get('actor_rows') if isinstance(witnesses, dict) else None
            if not isinstance(actor_rows, dict) or actor_rows.get('status') != 'available':
                reasons.append('native_actor_rows_unavailable')
        if sample['frame'] != 0 or sample['game_time'] != 0:
            reasons.append('baseline_not_zero_tick')
        rows = sample.get('rows')
        if not (_number(sample.get('global_total')) and sample['global_total'] == 0
                and _number(sample.get('global_heal')) and sample['global_heal'] == 0
                and isinstance(rows, (list, tuple))
                and all(isinstance(row, dict) and _number(row.get('damage'))
                        and _number(row.get('healing')) and row['damage'] == row['healing'] == 0
                        for row in rows)):
            reasons.append('native_counters_not_zero_or_unavailable')
        return reasons

    @staticmethod
    def _native_initial_point(sample):
        return not MultiplayerBattleStatistics._native_initial_rejections(sample)

    @staticmethod
    def _same_native_roots(left, right):
        # Canonical root keys have already been bound by the acquisition owner.
        # Only compare their five-root prefix, never decode player/actor IDs.
        parts = [key.rpartition(':autochess:') for key in (left, right)]
        return (all(separator and epoch.isascii() and epoch.isdecimal() and len(epoch) <= 16
                    and (epoch == '0' or not epoch.startswith('0'))
                    and int(epoch) <= 9007199254740991 for _, separator, epoch in parts)
                and parts[0][0] == parts[1][0])

    def _initialize_native_skills(self, sample, drained):
        discarded = drained.get('baseline_discarded_skill_events')
        retained = drained.get('events')
        complete = (drained.get('baseline_discarded_skill_events_complete') is True
                    and isinstance(discarded, list) and len(discarded) <= MAX_BASELINE_SKILL_EVENTS
                    and isinstance(retained, list) and len(retained) <= MAX_BASELINE_SKILL_EVENTS)
        self._round_start_live_skill_uids = (
            autochess_round_start_live_skill_uids(sample) if complete else None)
        live_uids = self._round_start_live_skill_uids
        if live_uids is not None:
            rejections = []
        elif (autochess_round_start_origin(sample)
              and sample['autochess_round_start']['version'] == 2):
            rejections = ['round_start_live_characters_unverified']
        else:
            rejections = self._native_initial_rejections(sample)
        if not complete:
            rejections.append('skill_prefix_incomplete')
        witnesses = sample.get('native_snapshot_witnesses')
        actor_rows = witnesses.get('actor_rows') if isinstance(witnesses, dict) else None
        self._skill_initialization = dict(frame=sample['frame'], game_time=sample['game_time'],
            sequence=self._baseline, reason_codes=list(rejections), prefix_complete=complete,
            actor_rows_status=(actor_rows.get('status') if isinstance(actor_rows, dict)
                and actor_rows.get('status') in ('available', 'unavailable', 'not_requested') else None),
            native_damage=sample.get('global_total') if _number(sample.get('global_total')) else None,
            native_healing=sample.get('global_heal') if _number(sample.get('global_heal')) else None)
        if live_uids is not None:
            self._skill_initialization['source'] = 'producer_round_start'
        for player in self._players.values():
            for owner, entry in player.actors.items():
                if rejections:
                    player.mark_unknown_skill_prefix(owner, entry.unique_id, *rejections)
                elif live_uids is not None and entry.unique_id in live_uids:
                    player.mark_unknown_skill_prefix(owner, entry.unique_id, 'round_start_actor_already_live')
        if not complete:
            self._reasons.add('初始技能事件前缀证据不完整')
        # These records are uncertainty witnesses only. They do not open a
        # window, enter EventStream.received, or credit any pre-baseline amount.
        for group, start_summary in ((discarded, True), (retained, False)):
            if not isinstance(group, list):
                continue
            for event in group[:MAX_BASELINE_SKILL_EVENTS]:
                if not isinstance(event, dict):
                    continue
                if start_summary:
                    # The native baseline protocol already selected starts and
                    # omits kind/amount. Do not invent an event to replay them.
                    if 'kind' in event and event['kind'] != 'skill_start':
                        self._reasons.add('初始技能前缀类型未确认')
                        continue
                elif event.get('kind') != 'skill_start':
                    continue
                sequence = event.get('sequence')
                if not _integer(sequence) or sequence > self._baseline:
                    self._reasons.add('初始技能前缀序号未确认')
                    continue
                identity = self._scope_stamp_identity(event)
                if identity is None:
                    self._reasons.add('初始技能前缀阶段身份未确认')
                    # An unreadable scope cannot certify a foreign discard.
                    # A real cached start with this exact current CID/data UID
                    # is only a candidate for local initial-state uncertainty.
                else:
                    current = self._context_fingerprint[:3]
                    if (identity[1:] != current[1:] or identity[0] != current[0]
                            and not self._same_native_roots(identity[0], current[0])):
                        continue
                    event_mode = (event.get('native_autochess_scope') or {}).get('collection_mode', self._collection_mode)
                    if event_mode != self._collection_mode:
                        self._reasons.add('初始技能前缀采集模式未确认')
                data_uid = event.get('source_character_uid')
                evidence = self._actor_lookup(self._context, cid=event.get('source'), data_uid=data_uid)
                if evidence is None:
                    self._reasons.add('初始技能前缀存在角色UID缺证')
                    continue
                if evidence['is_token']:
                    # A summon never supplies a host's activation window.
                    continue
                uid, entry = self._native_entries[data_uid]
                if uid not in self._players:
                    continue
                self._players[uid].mark_unknown_skill_prefix(
                    self._source_key(entry, uid), entry.unique_id, 'discarded_skill_start')

    @staticmethod
    def _scope_target(event):
        uid, instance = event.get('target_uid'), event.get('target_instance')
        if (isinstance(uid, str) and 0 < len(uid) <= 128
                and isinstance(instance, str) and 0 < len(instance) <= 128):
            return uid, instance
        return None

    def _observe_healing_uncertainty(self, event):
        if (event.get('kind') != 'healing' or not _number(event.get('amount')) or event['amount'] <= 0
                or classify_unmapped_source(event.get('source')) is not None):
            return
        identity = self._scope_stamp_identity(event)
        if (self._active_context is not None and identity is not None
                and identity != self._context_fingerprint[:3]):
            return
        data_uid = event.get('source_character_uid')
        claims = (self._native_source_claims.get((event.get('source'), data_uid))
                  if type(data_uid) is int and data_uid not in self._native_unverified_source_uids else None)
        if claims and len(claims) == 1:
            uid, host_cid, host_uid = next(iter(claims))
            if uid not in self._players:
                return
            stats = self._players[uid].statistics
            source = f"{uid}:{event['source']}#{data_uid}"
            owner = f'{uid}:{host_cid}#{host_uid}'
            # Claims include historical ownership within this scope. A current
            # roster alone cannot relabel an earlier rejected callback.
            stats.mark_healing_observation_unknown(
                source if stats.owners.get(source) == owner else None, game_time=event['game_time'])
            return
        for player in self._players.values():
            player.statistics.mark_healing_observation_unknown(game_time=event['game_time'])

    def _observe_scope_uncertainty(self, event):
        self._observe_healing_uncertainty(event)
        # Unknown scope can hide an intervening bar. A later source-null hit
        # cannot inherit an older owner until the shared core confirms a new
        # current-scope bar for that exact target. No amount is calculated here.
        if (event.get('kind') in ('elemental_bar', 'elemental_burst')
                or event.get('kind') == 'damage_settlement' and event.get('damage_type') == 5):
            target = self._scope_target(event)
            if target is None:
                self._scope_unknown_all_targets = True
                self._scope_verified_targets.clear()
            else:
                self._scope_unknown_targets.add(target)
                self._scope_verified_targets.discard(target)
        data_uid = event.get('source_character_uid')
        identity = self._scope_stamp_identity(event)
        current_possible = (classify_unmapped_source(event.get('source')) is None
            and (identity is None or self._active_context is None
                 or identity == self._context_fingerprint[:3]))
        evidence = self._native_entries.get(data_uid) if type(data_uid) is int else None
        if evidence is not None and evidence[1].cid == event.get('source'):
            uid, entry = evidence
            self._player_reasons[uid].add('该玩家存在阶段或轮次身份未核实的事件')
            if uid in self._players and event.get('kind') in ('skill_start', 'skill_end') and not entry.is_token:
                source = self._source_key(entry, uid)
                # This uncertainty is about scope, not native-cast semantics.
                # Ordinary current-scope casts still go through EventStream.
                self._players[uid].statistics.mark_settlement_skill_unassigned(
                    source, 'native_autochess_scope_unverified', None, actor=entry.unique_id)
            return
        self._reasons.add('存在阶段或轮次身份未核实的事件')
        if event.get('kind') in ('skill_start', 'skill_end') and current_possible:
            # Without an actor witness any collected actor could have changed
            # skill state. Do not certify a timed interval across this hole.
            for player in self._players.values():
                player.statistics.mark_skill_unknown()

    def _confirm_scope_bar(self, event, player_key, actor):
        target = self._scope_target(event)
        if target is None:
            return
        if player_key in self._players:
            owner = self._source_key(actor, player_key)
            bar = self._players[player_key].statistics.settlement_bars.get(target[0])
            confirmed = bar is not None and bar[1:] == (target[1], 'local', owner, actor.unique_id)
        else:
            # Local mode still feeds a verified foreign bar to the real local
            # receiver. Its negative ownership witness can restore this target's
            # scope without allocating or crediting a foreign statistics core.
            confirmed = any((bar := player.statistics.settlement_bars.get(target[0])) is not None
                            and bar[1:] == (target[1], 'foreign', None, None)
                            for player in self._players.values())
        if confirmed:
            self._scope_unknown_targets.discard(target)
            self._scope_verified_targets.add(target)

    def _native_identity(self, event):
        scope = self._native_event_scope(event)
        if scope is not None:
            return scope, None, None, None
        source = event.get('source')
        if source is None and event.get('kind') == 'elemental_burst':
            # The native settled event owns the amount. Its legacy callback
            # can lack target_uid and must not invalidate other target bars.
            return 'global', None, None, None
        if source is None and event.get('damage_type') == 5 and event.get('kind') == 'damage_settlement':
            target = self._scope_target(event)
            if (target is None or target in self._scope_unknown_targets
                    or self._scope_unknown_all_targets and target not in self._scope_verified_targets):
                return 'elemental_scope_unverified', None, None, None
            return 'elemental', None, None, None
        if source is not None and classify_unmapped_source(source) is not None:
            return 'non_team', None, None, None
        if source is None and (event.get('kind') in ENEMY_STATE_KINDS or event.get('kind') == 'enemy_damage'):
            return 'global', None, None, None
        data_uid = event.get('source_character_uid')
        if type(data_uid) is not int or data_uid <= 0:
            return 'source_uid_missing', None, None, None
        evidence = resolve_native_event_member(self._active_context, event, self._previous_context,
            lookup=self._actor_lookup, previous_lookup=self._previous_actor_lookup)
        if evidence is None:
            return 'source_uid_unmapped', None, None, None
        player_uid = evidence['player_uid']
        bound = self._native_entries.get(data_uid)
        entry = (bound[1] if bound is not None else CharacterEntry(source, data_uid,
                 evidence['is_token'], evidence['is_predefined'], evidence['host_key'],
                 evidence['host_uid'], evidence['player_side']))
        actor = entry
        if entry.is_token:
            host = self._native_entries.get(entry.host_uid)
            if (host is None or host[0] != player_uid or host[1].cid != entry.host_key or host[1].is_token):
                return 'token_host_unverified', None, None, None
            actor = host[1]
        if player_uid not in self._players:
            return 'not_collected', player_uid, actor, entry
        return 'owned', player_uid, actor, entry

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
        if self._native_mode:
            return self._native_identity(event)
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
        if reason == 'event_scope_unverified':
            stamp = event.get('native_autochess_scope')
            detail = stamp.get('reason') if isinstance(stamp, dict) else None
            if isinstance(detail, str) and detail:
                item['last_scope_reason'] = detail[:160]
        if self._native_mode:
            affected = set()
            if reason.startswith('event_scope_') or reason == 'elemental_scope_unverified':
                self._observe_scope_uncertainty(event)
                return
            if event.get('kind') == 'healing':
                self._observe_healing_uncertainty(event)
                return
            if event.get('kind') != 'damage_settlement':
                # Let the canonical receiver decide passive native casts and
                # source-local skill uncertainty before changing any quality.
                return
            table = {row['index']: row['uid'] for row in self._context['players']}
            source_uid = event.get('source_character_uid')
            for row in self._context['unknown_memberships']:
                if (type(source_uid) is int and row.get('data_uid') == source_uid
                        or source_uid is None and row.get('cid') == event.get('source')):
                    uid = table.get(row.get('player_index', row.get('candidate_player_index')))
                    if uid in self._players:
                        affected.add(uid)
            if not affected:
                affected = {uid for uid, player in self._players.items()
                    if any(entry.cid == event.get('source') for entry in player.actors.values())}
            if affected:
                for uid in affected:
                    self._player_reasons[uid].add('该玩家存在来源UID未确认的事件')
            else:
                self._reasons.add('存在无法按玩家隔离的来源UID缺证')
            return
        if event.get('kind') == 'damage_settlement':
            self._reasons.add('存在来源UID未确认的结算')
            for player in self._players.values():
                player.statistics.settlement_stream_incomplete = True

    def _start_events(self, sequence, version, *, sample, drained):
        self._baseline = self._sequence = sequence
        self._settlement_version = version
        for player in self._players.values():
            stats = player.statistics
            stats.partial_damage_enabled = stats.settlement_enabled = True
            stats.damage_settlement_version = version
            stats.event_coverage = True
            stats.start_late_event_coverage()
            player.stream = EventStream(stats, session=stats.session,
                baseline_frame=self._frame, baseline_sequence=sequence,
                retain_owned_replay_records=True)
            stats.mark_boss_watermark(self._now)
        if self._native_mode:
            self._initialize_native_skills(sample, drained)

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

    def _settlement_quality(self, sample, payload):
        ready = payload.get('damage_settlement_ready', payload.get('enemy_damage_ready'))
        unknown = payload.get('damage_settlement_unknown', payload.get('enemy_damage_unknown'))
        if ready is True and unknown is False and settlement_source_quality_clear(payload):
            return
        reason = '结算事件质量未完整确认'
        if self._native_mode and ready is True and unknown is True:
            issues = verified_autochess_settlement_source_issues(sample, payload)
            if issues is not None:
                owners = []
                for issue in issues:
                    if issue['source_character_uid'] in self._native_unverified_source_uids:
                        break
                    state, uid, actor, _ = self._native_identity(issue)
                    if state not in ('owned', 'not_collected'):
                        break
                    # Capture proved the actor identity, but it did not stamp
                    # the account or a token host. Later rosters cannot select
                    # one of multiple claims already seen within this scope.
                    claims = self._native_source_claims.get((issue['source'], issue['source_character_uid']))
                    if claims != {(uid, actor.cid, actor.unique_id)}:
                        break
                    if state == 'owned':
                        owners.append((uid, actor))
                else:
                    for uid, actor in owners:
                        self._players[uid].statistics.settlement_source_incomplete.add(self._source_key(actor, uid))
                        self._player_reasons[uid].add(reason)
                    return
        self._reasons.add(reason)
        for player in self._players.values():
            player.statistics.settlement_stream_incomplete = True

    def _quality(self, sample, drained, damage_quality, *, initial):
        payload = contract_for(sample=sample, descriptor=drained.get('probe_contract')).normalize_quality(drained)
        for side, player in self._players.items():
            scoped = dict(payload)
            if self._native_mode:
                # Global entry/state/phase history has one shared owner. Player
                # cores keep their own damage ledgers, not four world journals.
                for family in ('enemy_entry', 'enemy_state', 'enemy_phase'):
                    scoped.pop(family + '_ready', None)
                    scoped.pop(family + '_unknown', None)
            for field in ('unknown_sources', 'engagement_unknown_sources'):
                if self._native_mode:
                    scoped[field] = [source for cid in payload.get(field) or ()
                                     for source in self._native_aliases[side].get(cid, ())]
                else:
                    scoped[field] = [
                        _key(entry) for entries in self._roster.values() for entry in entries
                        if entry.player_side == side and entry.cid in (payload.get(field) or ())]
            apply_event_quality(player.statistics, scoped, damage_quality,
                                baseline_frame=player.stream.baseline_frame, initial_drain=initial)

    def consume(self, sample, roster: RosterSnapshot, sequence=None, drained=None, *, mark=None,
                round_id=None, return_result=True, terminal=False):
        stage = str((sample.get('battle_metadata') or {}).get('stage_id') or '')
        if 'AUTOCHESS' not in stage.upper():
            return None
        self._terminal_tail = False
        try:
            self._consume(sample, roster, sequence, drained, mark=mark, round_id=round_id, terminal=terminal)
        finally:
            self._terminal_tail = False
        if return_result:
            return self.result()

    def _consume(self, sample, roster, sequence, drained, *, mark, round_id, terminal):
        if type(terminal) is not bool:
            self.gap('终局投影标记无效')
            return
        if (not _number(sample.get('game_time')) or not _integer(sample.get('frame'))
                or not isinstance(roster, RosterSnapshot) or roster.frame != sample['frame']):
            self.gap('快照或编队短读')
            return
        base = round_id if round_id is not None else sample.get('session')
        if not isinstance(base, str) or not base:
            self.gap('小局身份未确认')
            return
        native_requested = (self._native_mode or 'autochess_context' in sample
                            or sample.get('native_counter_scope') == 'events_only')
        if terminal and native_requested:
            # A lifecycle-owned terminal handoff closes the last combat scope;
            # END/SETTLE/unknown snapshots cannot create a new measurement.
            if not self._native_mode or self._context['phase'] not in (4, 5, 6):
                return
            if self._ended:
                return
            metadata = sample.get('battle_metadata') or {}
            abandoned = (metadata.get('outcome') == 'abandoned'
                         and isinstance(metadata.get('evidence'), str)
                         and bool(metadata['evidence'].strip()))
            trusted = (base == self._base_round and sample.get('session') == self._sample_session
                       and isinstance(self._native_session, str) and bool(self._native_session)
                       and sample.get('native_session') == self._native_session
                       and sample['game_time'] >= self._now and sample['frame'] >= self._frame
                       and (type(sample.get('state')) is int and sample['state'] == 3 or abandoned))
            if not trusted:
                self.gap('终局缺少同原生会话、时钟及结束状态证明')
                return
            self._terminal_tail = True
            # Keep the original witness and its original frame/time. Each tail
            # record still needs its own matching scope and capture-time stamp;
            # this is not a new-frame roster proof or a retagged current sample.
            self._active_context = self._context
        elif (base in self._retired_rounds
              or (base, sample.get('session'), sample.get('native_session')) in self._retired_identities):
            return
        context = sample.get('autochess_context') if native_requested else None
        fingerprint = self._native_fingerprint(context, sample) if native_requested else None
        if (not self._terminal_tail and fingerprint is not None and fingerprint == self._context_fingerprint
                and (context.get('collection_mode', 'all') != self._collection_mode
                     or self._collection_mode == 'local' and context.get('self_player_uid') != self._local_account_uid)):
            self.gap('同一原生观察范围的采集模式或本家身份发生变化')
            return
        if not self._terminal_tail and fingerprint in self._retired_contexts:
            return
        if not self._terminal_tail and native_requested and fingerprint is None:
            # Never fall back to side-based legacy identity after a native
            # context was declared. Existing streams can receipt this unknown
            # interval without applying its events to the last known actors.
            self.invalidate_presence()
            if not self._native_mode:
                if self._players:
                    self.gap('原生卫戍上下文未确认')
                return
            self._active_context = None
            self._reasons.add('原生卫戍上下文未确认')
        reason = None
        if self._terminal_tail:
            reason = None
        elif self._base_round != base:
            reason = '小局切换'
        elif (sample.get('session') != self._sample_session
              or self._native_session is not None and sample.get('native_session') is not None
              and sample['native_session'] != self._native_session):
            reason = '采集会话或原生小局身份切换'
        elif sample['game_time'] < self._now or sample['frame'] < self._frame:
            reason = '游戏时钟重新开始'
        elif fingerprint is not None and (not self._native_mode or fingerprint != self._context_fingerprint):
            reason = '原生卫戍阶段、轮次或玩家身份切换'
        else:
            reason = None if native_requested else self._identity_change(roster)
        if reason is not None:
            if native_requested and fingerprint is None:
                self.gap('新小局缺少已验证的原生卫戍上下文')
                return
            self._begin(sample, base, reason, context=context if native_requested else None)
        elif self._ended:
            return
        if self._native_mode:
            if fingerprint is not None and not self._terminal_tail:
                self._bind_native(context)
        else:
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
        self._settlement_quality(sample, quality)
        if self._baseline is None:
            self._now, self._frame = sample['game_time'], sample['frame']
            self._start = self._now
            self._from_start &= self._start <= 1 and self._frame <= 30
            if self._native_mode:
                # A short clock is insufficient: counters may already include
                # output, and an old journal prefix is not this phase's start.
                self._from_start &= (autochess_round_start_origin(sample)
                    or sequence == 0 and not drained.get('events')
                    and _number(sample.get('global_total')) and sample['global_total'] == 0
                    and _number(sample.get('global_heal')) and sample['global_heal'] == 0
                    and all(isinstance(row, dict) and _number(row.get('damage'))
                            and _number(row.get('healing')) and row['damage'] == row['healing'] == 0
                            for row in sample.get('rows', ())))
            for player in self._players.values():
                player.statistics.advance(self._now)
            self._start_events(sequence, version, sample=sample, drained=drained)
            self._quality(sample, drained, quality, initial=True)
            self._observe_presence(sample)
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
                skip_scope = state in ('foreign_scope', 'event_scope_unverified',
                                       'event_scope_mismatch', 'elemental_scope_unverified')
                if state == 'foreign_scope':
                    self._scope_foreign_events += 1
                elif state not in ('owned', 'global', 'non_team', 'not_collected', 'elemental'):
                    self._diagnose(event, state)
                elemental_before = None
                if state == 'elemental' and event['kind'] == 'damage_settlement':
                    elemental_before = {s: (sum(m.hits for m in p.statistics.settled.values()),
                        p.statistics.settlement_unassigned['hits']) for s, p in self._players.items()}
                for player_side, player in self._players.items():
                    payload = event_processing_payload(event)
                    identity = ('unknown', None, None)
                    unresolved_source_keys = ()
                    if state == 'owned':
                        if side == player_side:
                            owner, source = self._source_key(actor, side), self._source_key(entry, side)
                            player.statistics.register_source(source, owner)
                            payload['source'] = source
                            identity = ('summon', None, None) if entry.is_token and event['kind'] in (
                                'skill_start', 'skill_end') else ('local', owner, actor.unique_id)
                        else:
                            identity = ('foreign', None, None)
                    elif state in ('non_team', 'not_collected'):
                        identity = ('foreign', None, None)
                    elif state not in ('global', 'elemental') and not skip_scope:
                        payload['unresolved_source'] = True
                        # These verified actors are candidates for uncertainty,
                        # never recipients of this event's amount or window.
                        # The receiver decides whether native cast evidence is
                        # an ignorable passive observation before marking them.
                        skill_uid = (event.get('source_character_uid') if self._native_mode
                                     and event['kind'] in ('skill_start', 'skill_end') else None)
                        unresolved_source_keys = tuple(owner for owner, candidate in player.actors.items()
                            if candidate.cid == event.get('source')
                            and (type(skill_uid) is not int or skill_uid <= 0
                                 or candidate.unique_id == skill_uid))
                    world_receipt = (self._native_mode and (event['kind'] in ENEMY_STATE_KINDS
                        or event['kind'] == 'elemental_burst' and event.get('source') is None))
                    player.stream.accept(None, unresolved_source_keys, skip_scope or world_receipt, session=player.statistics.session,
                        settlement_identity=identity, replay_record=event, **payload)
                if self._native_mode and state in ('owned', 'not_collected') and event['kind'] == 'elemental_bar':
                    self._confirm_scope_bar(event, side, actor)
                if elemental_before is not None:
                    resolved = any(sum(m.hits for m in p.statistics.settled.values()) > elemental_before[s][0]
                                   for s, p in self._players.items())
                    unresolved = any(p.statistics.settlement_unassigned['hits'] > elemental_before[s][1]
                                     for s, p in self._players.items())
                    if not resolved and unresolved:
                        self._diagnose(event, 'elemental_owner_unverified')
                # _batch detached this record from the producer before any
                # reduction. All scoped receipt stores retain that same copy.
                self._received[event['sequence']] = event
            for player in self._players.values():
                player.stream.watermark(session=player.statistics.session, sequence=sequence,
                    frame=sample['frame'], game_time=sample['game_time'])
            self._observe_presence(sample)
            self._pending = pending
            self._sequence = sequence
            self._now, self._frame = sample['game_time'], sample['frame']
        except (StreamGap, ValueError, TypeError, OverflowError) as exc:
            self.gap(str(exc))
        return

    def gap(self, reason='采集缺口'):
        if not self._enabled or self._ended:
            return
        self._closed = True
        self._reasons.add(reason)
        for player in self._players.values():
            if player.stream is not None:
                player.statistics.mark_boss_watermark(player.stream.game_time)
                player.stream.stop()
            player.statistics.settlement_stream_incomplete = True

    def finish(self, game_time=None, *, complete=False):
        if not self._enabled or self._ended:
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
        # Core entry snapshots own their event dicts but retain the internal
        # flat numeric attribute dict. Detach that last mutable leaf here.
        enemy_entry = stats.get('enemy_entry')
        if enemy_entry is not None:
            for event in enemy_entry['events']:
                if 'enemy_entry_attributes' in event:
                    event['enemy_entry_attributes'] = dict(event['enemy_entry_attributes'])
        stats['totals_complete'] = False
        ledgers = []
        for owner, entry in sorted(player.actors.items()):
            row = stats['rows'].get(owner)
            if row is None:
                continue
            name = self._names.get(entry.cid, entry.cid)
            row.update(id=owner, cid=entry.cid, unique_id=entry.unique_id,
                       name=name)
            lane = row.get('settled_damage', {})
            initial_reasons = player.unknown_skill_prefix.get(owner)
            if initial_reasons:
                # Historical unknown hits remain unknown after future state
                # recovery. Explain the original gate without changing counts.
                lane['skill_initial_unknown_reason_codes'] = sorted(initial_reasons)
                if (lane.get('skill_initial_unknown_hits', 0) > 0
                        and not lane.get('skill_split_reason')):
                    lane['skill_split_reason'] = '部分命中的采集起点技能状态未确认'
            row['observed_damage'] = lane.get('observed_damage')
            if self._native_mode and (owner in player.unknown_skill_prefix
                    or owner in player.statistics.settlement_skill_unassigned):
                row.update(skill_coverage_complete=False, skill_active_seconds=None)
            if owner in player.observed_presence:
                row['observed_presence_seconds'] = player.observed_presence[owner]
                covered = player.skill_timed_presence.get(owner)
                if covered:
                    row['observed_skill_active_seconds'] = player.observed_skill_time[owner]
                    row['skill_timed_presence_seconds'] = covered
                    row['observed_skill_coverage'] = player.observed_skill_time[owner] / covered
            ledgers.append(dict(cid=entry.cid, source_character_uid=entry.unique_id,
                                unique_id=entry.unique_id, uid=entry.unique_id, actor_uid=entry.unique_id,
                                name=name, owner_id=owner, ledger=deepcopy(lane)))
            if self._native_mode:
                ledgers[-1]['player_uid'] = side
                row['player_uid'] = side
        observed = [item['ledger'] for item in ledgers if item['ledger'].get('nominal_hits', 0)]
        has_output = bool(observed)
        player_reasons = self._player_reasons.get(side, set())
        reasons = set(self._reasons) | player_reasons
        reasons.add('没有按该玩家独立读取的原生累计')
        if not has_output:
            reasons.add('未观察到可按UID归属的名义结算')
        if not self._from_start:
            reasons.add('仅覆盖已观察游戏时间窗口')
        complete = (has_output and self._from_start and not self._reasons and not player_reasons
                    and all(lane.get('observed_damage_complete') for lane in observed))
        values = lambda field: [lane[field] for lane in observed if lane.get(field) is not None]
        def total(field):
            parts = values(field)
            return sum(parts) if parts else None
        known_side = any(entry.player_side == side for entries in self._roster.values()
                         for entry in entries)
        end = (player.statistics.event_coverage_end if player.statistics.event_coverage_end is not None
               else self._now)
        result = dict(player_side=side, label=f'本局阵营 {side}',
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
        if self._native_mode:
            result['skill_initialization'] = deepcopy(self._skill_initialization)
            sides = {entry.player_side for uid, entry in self._native_entries.values() if uid == side}
            result.update(slot=player.slot, player_uid=side, player_name=player.player_name,
                player_side=next(iter(sides)) if len(sides) == 1 else None,
                label=player.player_name or f'玩家 {player.slot}',
                is_local=(side == self._context['self_player_uid'])
                         if self._context['self_player_uid'] is not None else None)
        return result

    @staticmethod
    def _unknown_slot(slot):
        # A display placeholder is not a statistics bucket or a made-up UID.
        return dict(slot=slot, player_uid=None, player_name=None, player_side=None,
            label=f'玩家 {slot}', is_local=None, status='unknown',
            damage=None, dps=None, recent_dps=None,
            observed_damage=None, observed_dps=None, observed_recent_dps=None,
            observed_window=None, observed_recent_window=None,
            coverage_complete=False, reasons=['玩家身份未确认'],
            statistics=None, actor_ledgers=[])

    def _result(self, *, include_previous):
        if not self._enabled:
            return None
        if self._native_mode:
            by_slot = {}
            for uid, account in self._native_accounts.items():
                if uid in self._players:
                    value = self._player_result(uid, self._players[uid])
                else:
                    value = self._unknown_slot(account['index'])
                    sides = {entry.player_side for owner, entry in self._native_entries.values() if owner == uid}
                    value.update(player_uid=uid, player_name=account['nick_name'],
                        player_side=next(iter(sides)) if len(sides) == 1 else None,
                        label=account['nick_name'] or f"玩家 {account['index']}",
                        is_local=(uid == self._context['self_player_uid']) if self._context['self_player_uid'] is not None else None,
                        statistics_status='not_collected', reasons=['未启用该玩家统计'])
                by_slot[account['index']] = value
            players = [by_slot.get(slot) or self._unknown_slot(slot) for slot in range(1, 5)]
            observed_sides = sorted({player['player_side'] for player in players
                if player['observed_damage'] is not None and player['player_side'] in range(1, 5)})
        else:
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
        if self._native_mode:
            phase = self._context['phase']
            context_available = self._active_context is not None
            result.update(schema_version=2, scope=self._scope, available=context_available,
                scope_kind=({2: 'prepare', 3: 'prepare', 4: 'defense', 5: 'assist', 6: 'boss'}.get(phase, 'unknown')
                            if context_available else 'unknown'),
                native_phase=phase, native_round=self._context['round'],
                context_identity=deepcopy(self._context['identity']),
                self_player_uid=self._context['self_player_uid'],
                observed_player_slots=[p['slot'] for p in players if p['observed_damage'] is not None],
                scope_foreign_events=self._scope_foreign_events,
                unknown_memberships=deepcopy(self._context['unknown_memberships']),
                note='玩家身份来自当前原生上下文。伤害来自同阶段同轮次的已观察事件，DPS按观察游戏时长计算；未知表示证据不足。')
            if self._mode_declared:
                result['collection_mode'] = self._collection_mode
        if include_previous:
            result['previous_rounds'] = deepcopy(self._previous)
            result['previous_rounds_pruned'] = self._previous_pruned
        return result

    def result(self):
        return self._result(include_previous=True)
