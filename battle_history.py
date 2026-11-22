"""Versioned local summaries. One acquisition owner writes; readers get copies."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from threading import RLock

from autochess_context import MAX_MEMBERSHIPS, PHASE_NAMES, native_autochess_scope_id


POLICIES = {
    'damage': 'native_cumulative_including_overkill',
    'healing': 'native_cumulative_including_overheal',
    'shield_absorbed': 'observed_actual_absorption_only',
    'overkill': 'observed_incoming_value_minus_verified_shield_absorption_minus_actual_hp_at_kill',
    'overkill_by_type': 'observed_overkill_grouped_by_native_damage_type',
    'burst': 'elemental_burst_attributed_to_last_elemental_bar_source',
    'recent': 'cumulative_delta_game_time_window_up_to_5s',
    'engagement': 'observed_events_separate_damage_healing_union_1.5s_tail',
    'ownership': 'verified_statistics_source_to_owner_no_guessing',
}
# Files written before overkill existed keep their original policy map and are
# read with overkill unknown rather than being rejected as unsupported. ARK-25
# records written before per-type grouping are accepted the same way.
PRE_BY_TYPE_POLICIES = {key: value for key, value in POLICIES.items()
                        if key != 'overkill_by_type'}
LEGACY_POLICIES = {key: value for key, value in POLICIES.items()
                   if key not in ('overkill', 'overkill_by_type')}
# The first ARK-25 candidate recorded a policy name that treated the native
# settlement value as post-shield. Keep those files readable instead of
# rejecting them; their stored values are still validated as before.
_PREVIOUS_OVERKILL_POLICY = 'observed_settlement_value_minus_actual_hp_at_kill_after_shield'
ACCEPTED_POLICIES = (
    POLICIES, PRE_BY_TYPE_POLICIES, LEGACY_POLICIES,
    {**POLICIES, 'overkill': _PREVIOUS_OVERKILL_POLICY},
    {**PRE_BY_TYPE_POLICIES, 'overkill': _PREVIOUS_OVERKILL_POLICY})
_LOCK = RLock()


class HistoryError(ValueError):
    """A failed validation/write; reliable files have not been overwritten."""


def _require(condition, message):
    if not condition:
        raise HistoryError(message)


def _number(value, name, *, nullable=False):
    if nullable and value is None:
        return
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
             f'invalid {name}')


def _date(value):
    _require(isinstance(value, str), 'invalid timestamp')
    try:
        parsed = datetime.fromisoformat(value)
        _require(parsed.utcoffset() is not None, 'timestamp needs timezone')
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise HistoryError(f'invalid timestamp: {value}') from exc


def _intervals(value, end):
    _require(isinstance(value, list), 'invalid snapshot intervals')
    previous = 0
    for interval in value:
        _require(isinstance(interval, list) and len(interval) == 2, 'invalid interval')
        start, stop = interval
        _number(start, 'interval start')
        _number(stop, 'interval end')
        _require(previous <= start <= stop <= end, 'unordered/out-of-range interval')
        previous = stop


def _validate_multiplayer(payload, battle_id, duration):
    """Bind optional player observations to this frozen battle, never native totals."""
    if payload is None:
        return

    def player_uid(value):
        return (isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,19}', value) is not None
                and int(value) < 1 << 64)

    def text(value, maximum):
        return isinstance(value, str) and 0 < len(value) <= maximum

    def reasons(value, name, maximum=64):
        _require(isinstance(value, list) and len(value) <= maximum
                 and all(text(reason, 1024) for reason in value), f'invalid {name}')

    def native_membership_unknowns(value):
        rows = value.get('unknown_memberships')
        _require(isinstance(rows, list) and len(rows) <= MAX_MEMBERSHIPS * 2 + 1,
                 'invalid native unknown_memberships')
        for row in rows:
            _require(isinstance(row, dict) and set(row) <= {
                'row_index', 'origin', 'reason', 'reasons', 'cid', 'data_uid',
                'player_index', 'player_side', 'candidate_player_index'},
                'invalid native unknown membership')
            _require(type(row.get('row_index')) is int and 0 <= row['row_index'] <= MAX_MEMBERSHIPS,
                     'invalid native unknown membership index')
            _require(text(row.get('reason'), 256), 'invalid native unknown membership reason')
            reasons(row.get('reasons'), 'native unknown membership reasons', 16)
            _require(row['reasons'] and row['reason'] == row['reasons'][0],
                     'native unknown membership reasons mismatch')
            if 'origin' in row:
                _require(row['origin'] == 'native_unknown', 'invalid native unknown membership origin')
            if 'cid' in row:
                _require(isinstance(row['cid'], str)
                         and re.fullmatch(r'[A-Za-z0-9_#.-]{1,128}', row['cid']) is not None,
                         'invalid native unknown membership cid')
            for key in ('player_index', 'candidate_player_index'):
                if key in row:
                    _require(type(row[key]) is int and row[key] in range(1, 5),
                             'invalid native unknown membership player index')
            if 'player_side' in row:
                _require(type(row['player_side']) is int and 0 <= row['player_side'] < 1 << 31,
                         'invalid native unknown membership side')
            if 'data_uid' in row:
                _require(type(row['data_uid']) is int and 0 < row['data_uid'] <= 0xffffffff,
                         'invalid native unknown membership UID')

    def native_header(value):
        identity = value.get('context_identity')
        _require(isinstance(identity, dict) and set(identity) == {'scope_key', 'phase', 'round'},
                 'invalid native multiplayer context identity')
        root, phase, number = identity['scope_key'], identity['phase'], identity['round']
        _require(text(root, 2048) and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in root),
                 'invalid native multiplayer root scope')
        _require(type(phase) is int and phase in PHASE_NAMES
                 and type(number) is int and 0 <= number < 1 << 31,
                 'invalid native multiplayer phase or round')
        _require(type(value.get('native_phase')) is int and value['native_phase'] == phase
                 and type(value.get('native_round')) is int and value['native_round'] == number,
                 'native multiplayer phase or round mismatch')
        _require(type(value.get('available')) is bool, 'invalid native multiplayer availability')
        expected_kind = ({2: 'prepare', 3: 'prepare', 4: 'defense', 5: 'assist', 6: 'boss'}.get(phase, 'unknown')
                         if value['available'] else 'unknown')
        _require(value.get('scope_kind') == expected_kind, 'native multiplayer scope kind mismatch')
        players = value.get('players')
        _require(isinstance(players, list) and len(players) == 4, 'invalid native multiplayer slots')
        accounts, slots, uids = [], set(), set()
        for player in players:
            _require(isinstance(player, dict), 'invalid native multiplayer player')
            slot, uid = player.get('slot'), player.get('player_uid')
            _require(type(slot) is int and slot in range(1, 5) and slot not in slots,
                     'duplicate or invalid native multiplayer slot')
            slots.add(slot)
            _require(uid is None or player_uid(uid) and uid not in uids,
                     'duplicate or invalid native multiplayer player UID')
            if uid is not None:
                accounts.append((slot, uid))
                uids.add(uid)
            name = player.get('player_name')
            _require(name is None or isinstance(name, str) and len(name) <= 128,
                     'invalid native multiplayer player name')
        _require(accounts, 'native multiplayer has no verified player accounts')
        scope_id = native_autochess_scope_id(identity, accounts)
        _require(value.get('scope') == scope_id, 'native multiplayer scope identity mismatch')
        self_uid = value.get('self_player_uid')
        _require('self_player_uid' in value and (self_uid is None or self_uid in uids),
                 'invalid native multiplayer self UID')
        _require(type(value.get('scope_foreign_events')) is int and value['scope_foreign_events'] >= 0,
                 'invalid native multiplayer foreign count')
        reasons(value.get('reasons'), 'native multiplayer reasons')
        native_membership_unknowns(value)
        return scope_id

    def ledger_numbers(value, *, key='', depth=0):
        _require(depth <= 8, 'native multiplayer metric nesting limit')
        if type(value) in (int, float):
            # A native mitigation pair can represent amplification. Its signed
            # percentage is evidence, while damage/count/rate values stay >=0.
            if key == 'observed_mitigation_rate':
                _require(math.isfinite(value), 'invalid native observed mitigation rate')
            else:
                _number(value, 'native multiplayer ' + key)
            if key.endswith('_hits'):
                _require(type(value) is int, 'invalid native multiplayer hit count')
            if key.endswith('_share'):
                _require(value <= 1, 'invalid native multiplayer unverified share')
        elif isinstance(value, dict):
            _require(len(value) <= 512, 'native multiplayer metric field limit')
            for field, item in value.items():
                _require(isinstance(field, str), 'invalid native multiplayer metric field')
                ledger_numbers(item, key=field, depth=depth + 1)
        elif isinstance(value, (list, tuple)):
            _require(len(value) <= 4096, 'native multiplayer metric array limit')
            for item in value:
                ledger_numbers(item, key=key, depth=depth + 1)
        else:
            _require(value is None or type(value) is bool or isinstance(value, str),
                     'invalid native multiplayer metric')
            if type(value) is bool:
                _require(key == 'complete' or key.endswith(('_complete', '_available', '_stale')),
                         'boolean native multiplayer amount')

    def window(value, start, end, name):
        if value is None:
            return
        _require(isinstance(value, (list, tuple)) and len(value) == 2, f'invalid {name}')
        _number(value[0], name + ' start')
        _number(value[1], name + ' end')
        _require(start <= value[0] <= value[1] <= end, f'out-of-range {name}')

    def scope(value, *, compact):
        _require(isinstance(value, dict), 'invalid multiplayer scope')
        _require(type(value.get('schema_version')) is int and value['schema_version'] in (1, 2),
                 'unsupported multiplayer schema_version')
        native = value['schema_version'] == 2
        _require(value.get('source_round_id') == battle_id, 'multiplayer battle_id mismatch')
        epoch = value.get('identity_epoch')
        _require(type(epoch) is int and epoch >= 0, 'invalid multiplayer identity_epoch')
        if native:
            scope_id = native_header(value)
        else:
            scope_id = f'{battle_id}:players:{epoch}'
        _require(value.get('round_id') == scope_id, 'multiplayer round_id mismatch')
        _require(value.get('status') == 'ended', 'multiplayer scope not frozen')
        _require(type(value.get('available')) is bool, 'invalid multiplayer availability')
        start, end = value.get('coverage_start'), value.get('game_time')
        _number(start, 'multiplayer coverage_start')
        _number(end, 'multiplayer game_time')
        _require(start <= end <= duration, 'multiplayer time exceeds battle duration')
        if 'expected_player_slots' in value:
            _require(type(value['expected_player_slots']) is int and value['expected_player_slots'] == 4,
                     'invalid multiplayer display slots')
        players = value.get('players')
        _require(isinstance(players, list) and len(players) == 4, 'invalid multiplayer players')
        seen = set()
        actor_uids = set()
        observed = set()
        observed_sides = set()
        for player in players:
            _require(isinstance(player, dict), 'invalid multiplayer player')
            side = player.get('player_side')
            if native:
                _require(side is None or type(side) is int and 0 <= side < 1 << 31,
                         'invalid native multiplayer side')
                slot, uid_key = player['slot'], player['player_uid']
                self_uid = value['self_player_uid']
                _require(player.get('is_local') is ((uid_key == self_uid) if self_uid is not None and uid_key is not None else None),
                         'native multiplayer local UID mismatch')
                reasons(player.get('reasons'), 'native multiplayer player reasons')
            else:
                _require(type(side) is int and 1 <= side <= 4 and side not in seen,
                         'duplicate or invalid multiplayer player_side')
                seen.add(side)
                slot, uid_key = side, side
            _require(player.get('is_local') is None or type(player['is_local']) is bool,
                     'invalid multiplayer local flag')
            _require(type(player.get('coverage_complete')) is bool,
                     'invalid multiplayer coverage flag')
            _require(player.get('status') in ('unknown', 'observed', 'partial'),
                     'invalid multiplayer player status')
            # Schema 1 has no independent native counter witness per player.
            for metric in ('damage', 'dps', 'recent_dps'):
                _require(metric in player and player[metric] is None,
                         'multiplayer native counter has no independent witness')
            for metric in ('observed_damage', 'observed_dps', 'observed_recent_dps'):
                _require(metric in player, 'missing multiplayer observed metric')
                _number(player[metric], 'multiplayer ' + metric, nullable=True)
            for key in ('observed_window', 'observed_recent_window'):
                window(player.get(key), start, end, 'multiplayer ' + key)
            if player['observed_damage'] is None:
                _require(player['observed_dps'] is None and player['observed_recent_dps'] is None
                         and player.get('observed_window') is None
                         and player.get('observed_recent_window') is None
                         and player['coverage_complete'] is False,
                         'multiplayer unknown output has a fabricated rate or coverage')
                _require(player['status'] == 'unknown', 'multiplayer unknown output has a known status')
            else:
                observed.add(slot)
                if type(side) is int and side in range(1, 5):
                    observed_sides.add(side)
                _require(player.get('observed_window') is not None,
                         'multiplayer observed output lacks its game-time window')
            if native and uid_key is None:
                _require(player['observed_damage'] is None and player.get('player_name') is None
                         and player.get('player_side') is None and player['is_local'] is None,
                         'native multiplayer unknown slot has fabricated identity or output')
            if compact:
                _require('statistics' not in player and 'actor_ledgers' not in player,
                         'retired multiplayer scope must be a compact summary')
                continue
            if native and uid_key is None:
                _require(player.get('statistics') is None and player.get('actor_ledgers') == []
                         and player['observed_damage'] is None and player['is_local'] is None,
                         'native multiplayer unknown slot has fabricated statistics')
                continue
            stats = player.get('statistics')
            _require(isinstance(stats, dict) and stats.get('session') == f'{scope_id}:{uid_key}',
                     'multiplayer statistics session mismatch')
            _require(stats.get('ended') is True and stats.get('game_time') == end,
                     'multiplayer statistics not frozen at scope endpoint')
            _require(stats.get('totals_complete') is False,
                     'multiplayer native totals cannot be certified')
            rows = stats.get('rows')
            actors = player.get('actor_ledgers')
            _require(isinstance(rows, dict) and isinstance(actors, list),
                     'invalid multiplayer actor rows')
            actor_keys = set()
            for actor in actors:
                _require(isinstance(actor, dict), 'invalid multiplayer actor')
                cid, uid = actor.get('cid'), actor.get('source_character_uid')
                valid_cid = (isinstance(cid, str) and re.fullmatch(r'[A-Za-z0-9_#.-]{1,128}', cid) is not None
                             if native else isinstance(cid, str) and bool(cid) and '#' not in cid)
                # Current missing/conflicting membership cannot revoke amounts
                # accepted under an earlier verified witness in this scope.
                # Global UID uniqueness and account/owner binding still apply.
                _require(valid_cid and type(uid) is int and 0 < uid <= 0xffffffff
                         and uid not in actor_uids,
                         'invalid multiplayer actor identity')
                actor_uids.add(uid)
                owner = f'{uid_key}:{cid}#{uid}' if native else f'{cid}#{uid}'
                if native:
                    _require(actor.get('player_uid') == uid_key,
                             'native multiplayer actor account mismatch')
                _require(actor.get('owner_id') == owner and owner in rows and owner not in actor_keys,
                         'multiplayer actor owner mismatch')
                actor_keys.add(owner)
                for alias in ('uid', 'actor_uid', 'unique_id'):
                    if alias in actor:
                        _require(type(actor[alias]) is int and actor[alias] == uid,
                                 'multiplayer actor UID mismatch')
                row = rows[owner]
                _require(isinstance(row, dict) and row.get('cid') == cid
                         and type(row.get('unique_id')) is int and row['unique_id'] == uid
                         and row.get('id') == owner,
                         'multiplayer statistics actor identity mismatch')
                if native:
                    _require(row.get('player_uid') == uid_key, 'native multiplayer row account mismatch')
                _require(row.get('damage') is None and row.get('healing') is None
                         and row.get('counter_snapshot_complete') is False,
                         'multiplayer actor has unwitnessed native counters')
                ledger = actor.get('ledger')
                _require(isinstance(ledger, dict) and ledger == row.get('settled_damage'),
                         'multiplayer actor ledger mismatch')
                if native:
                    ledger_numbers(ledger)
                    for field, amount in row.items():
                        if field.startswith('observed_'):
                            ledger_numbers(amount, key=field)
                    _require(row.get('observed_damage') == ledger.get('observed_damage'),
                             'native multiplayer row observed damage mismatch')
                for metric in ('observed_damage', 'observed_dps', 'observed_recent_damage',
                               'effective_damage', 'known_hp_damage', 'known_shield_damage'):
                    _number(ledger.get(metric), 'multiplayer actor ' + metric, nullable=True)
                window(ledger.get('observed_damage_window'), start, end, 'multiplayer actor window')
                window(ledger.get('observed_recent_damage_window'), start, end,
                       'multiplayer actor recent window')
            _require(set(rows) == actor_keys, 'multiplayer statistics has an unbound actor owner')
        if 'observed_player_sides' in value:
            sides = value['observed_player_sides']
            _require(isinstance(sides, list) and all(type(s) is int for s in sides)
                     and len(sides) == len(set(sides)) and set(sides) == (observed_sides if native else observed),
                     'invalid multiplayer observed sides')
        if native:
            slots = value.get('observed_player_slots')
            _require(isinstance(slots, list) and all(type(slot) is int for slot in slots)
                     and len(slots) == len(set(slots)) and set(slots) == observed,
                     'invalid native multiplayer observed slots')
        return epoch

    epoch = scope(payload, compact=False)
    previous = payload.get('previous_rounds', [])
    _require(isinstance(previous, list) and len(previous) <= 8,
             'invalid retired multiplayer scopes')
    last_epoch = -1
    native_scopes = {payload.get('scope')} if payload.get('schema_version') == 2 else set()
    last_end = None
    for old in previous:
        old_epoch = scope(old, compact=True)
        _require(last_epoch < old_epoch < epoch, 'unordered retired multiplayer identity epochs')
        if old.get('schema_version') == 2:
            _require(old['scope'] not in native_scopes, 'retired native multiplayer scope collision')
            native_scopes.add(old['scope'])
            _require(old['game_time'] <= payload['coverage_start']
                     and (last_end is None or last_end <= old['coverage_start']),
                     'overlapping native multiplayer scopes')
            last_end = old['game_time']
        last_epoch = old_epoch
    pruned = payload.get('previous_rounds_pruned', 0)
    _require(type(pruned) is int and pruned >= 0, 'invalid retired multiplayer pruned count')


class BattleRecord:
    """Validated JSON summary; null and numeric zero retain their meanings."""

    def __init__(self, data):
        self._data = data

    @classmethod
    def from_archive(cls, archive):
        data = deepcopy(archive)
        stats = data.get('statistics', {})
        observed = (data.get('last_sample') or {}).get('observed_at')
        if observed is not None:
            _number(observed, 'observed_at')
            try:
                observed = datetime.fromtimestamp(observed, timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError) as exc:
                raise HistoryError('invalid observed_at') from exc
        data.update(schema_version=1, recorded_at=datetime.now(timezone.utc).isoformat(),
                    observed_at=observed, duration=stats.get('game_time'),
                    policies=deepcopy(POLICIES))
        # Older handoffs lack interval history; unknown must not imply full coverage.
        data.setdefault('snapshot_intervals', None)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data):
        try:
            # Normalize tuples, reject NaN/infinity and non-JSON source objects.
            data = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
            _require(isinstance(data, dict), 'record must be an object')
            _require(type(data.get('schema_version')) is int and data['schema_version'] == 1,
                     'unsupported BattleRecord schema_version')
            required = {'battle_id', 'generation', 'reason', 'metadata', 'statistics',
                        'last_sample', 'event_status', 'event_error', 'recorded_at',
                        'observed_at', 'duration', 'policies', 'snapshot_intervals'}
            _require(required <= data.keys(), 'missing BattleRecord fields')
            battle_id = data['battle_id']
            _require(isinstance(battle_id, str) and bool(battle_id.strip()), 'invalid battle_id')
            _require(type(data['generation']) is int and data['generation'] > 0, 'invalid generation')
            _require(isinstance(data['reason'], str) and bool(data['reason']), 'invalid reason')
            _date(data['recorded_at'])
            if data['observed_at'] is not None:
                _date(data['observed_at'])
            policies = data['policies']
            _require(isinstance(policies, dict)
                     and set(policies) <= set(POLICIES)
                     and all(POLICIES.get(key) == value for key, value in policies.items()),
                     'unsupported statistics policies')
            data['policies'] = deepcopy(POLICIES)
            _number(data['duration'], 'duration')
            metadata = data['metadata']
            _require(isinstance(metadata, dict), 'invalid metadata')
            _require({'stage_id', 'practice', 'outcome', 'evidence'} <= metadata.keys(),
                     'missing metadata fields')
            _require(metadata.get('outcome') in ('victory', 'defeat', 'abandoned', 'unknown'),
                     'invalid outcome')
            _require(metadata.get('practice') is None or type(metadata['practice']) is bool,
                     'invalid practice')
            _require(metadata.get('stage_id') is None or isinstance(metadata['stage_id'], str),
                     'invalid stage_id')
            stats = data['statistics']
            _require(isinstance(stats, dict) and stats.get('ended') is True, 'statistics not frozen')
            _require(stats.get('session') == battle_id and stats.get('adapter_session') == battle_id,
                     'statistics battle_id mismatch')
            _require(stats.get('game_time') == data['duration'], 'duration mismatch')
            _number(stats['game_time'], 'game_time')
            _number(stats['coverage_start'], 'coverage_start')
            _require(stats['coverage_start'] <= data['duration'], 'coverage starts after end')
            _require(isinstance(stats['rows'], dict), 'invalid statistics rows')
            for key, default in (('overkill_damage', None),
                                 ('overkill_coverage_complete', False),
                                 ('overkill_coverage_start', None),
                                 ('overkill_coverage_end', None)):
                stats.setdefault(key, default)
            _number(stats['overkill_damage'], 'overkill_damage', nullable=True)
            _require(type(stats['overkill_coverage_complete']) is bool,
                     'invalid overkill_coverage_complete')
            _number(stats['overkill_coverage_start'], 'overkill_coverage_start', nullable=True)
            _number(stats['overkill_coverage_end'], 'overkill_coverage_end', nullable=True)
            if stats['overkill_coverage_start'] is not None:
                _require(stats['overkill_coverage_end'] is not None
                         and 0 <= stats['overkill_coverage_start']
                         <= stats['overkill_coverage_end'] <= data['duration'],
                         'invalid overkill coverage interval')
            for owner, row in stats['rows'].items():
                _require(bool(owner) and isinstance(row, dict), 'invalid owner row')
                for key, default in (('overkill_damage', None),
                                     ('overkill_damage_by_type', None),
                                     ('overkill_type_complete', False),
                                     ('shield_unknown', False),
                                     ('burst_damage', None),
                                     ('burst_overkill', None),
                                     ('burst_settlement', None),
                                     ('burst_count', 0),
                                     ('burst_complete', False),
                                     ('burst_unknown_reason', None),
                                     ('effective_damage', None),
                                     ('overkill_coverage_complete', False),
                                     ('overkill_coverage_start', None),
                                     ('overkill_coverage_end', None),
                                     ('presence_seconds', None),
                                     ('presence_complete', False),
                                     ('recent_overkill_damage', None)):
                    row.setdefault(key, default)
                for metric in ('damage', 'healing', 'shield_absorbed', 'recent_damage',
                               'recent_healing', 'engagement_damage', 'engagement_healing',
                               'active_damage', 'active_healing', 'observed_damage', 'observed_healing',
                               'shield_coverage_start', 'event_coverage_end',
                               'overkill_damage', 'overkill_coverage_start',
                               'overkill_coverage_end', 'burst_damage',
                               'burst_overkill', 'burst_settlement', 'effective_damage',
                               'presence_seconds', 'recent_overkill_damage'):
                    _number(row[metric], metric, nullable=True)
                for scope in (row, row.get('boss_damage')):
                    if isinstance(scope, dict):
                        for metric in ('observed_skill_damage', 'observed_non_skill_damage',
                                       'observed_skill_damage_effective',
                                       'observed_non_skill_damage_effective'):
                            _number(scope.get(metric), metric, nullable=True)
                _number(row['coverage_start'], 'row coverage_start')
                _require(row['coverage_start'] <= data['duration'], 'invalid row coverage start')
                for flag in ('counter_snapshot_complete', 'event_coverage_complete',
                             'overkill_coverage_complete', 'overkill_type_complete',
                             'shield_unknown', 'burst_complete', 'types_available', 'types_stale',
                             'presence_complete'):
                    _require(type(row[flag]) is bool, f'invalid {flag}')
                by_type = row['overkill_damage_by_type']
                _require(by_type is None or
                         (isinstance(by_type, dict) and
                          set(by_type) <= {'physical', 'arts', 'elemental', 'true'}),
                         'invalid overkill damage types')
                if by_type is not None:
                    for value in by_type.values():
                        _number(value, 'overkill damage type')
                if row['overkill_type_complete']:
                    _require(by_type is not None,
                             'overkill type completeness without typed values')
                _require(type(row['burst_count']) is int and row['burst_count'] >= 0,
                         'invalid burst_count')
                _require(row['burst_unknown_reason'] is None
                         or isinstance(row['burst_unknown_reason'], str),
                         'invalid burst_unknown_reason')
                _require(row['present'] is None or type(row['present']) is bool, 'invalid presence')
                _require(isinstance(row['sources'], list) and
                         all(isinstance(s, str) and s for s in row['sources']), 'invalid sources')
                _require(isinstance(row['name'], str), 'invalid name')
                _require(isinstance(row['damage_by_type'], dict), 'invalid damage types')
                for value in row['damage_by_type'].values():
                    _number(value, 'damage type')
                for kind in ('damage', 'healing'):
                    window = row[f'recent_{kind}_window']
                    if window is not None:
                        _intervals([window], data['duration'])
                start, end = row['shield_coverage_start'], row['event_coverage_end']
                if start is not None:
                    _require(end is not None and 0 <= start <= end <= data['duration'],
                             'invalid event coverage interval')
                start, end = row['overkill_coverage_start'], row['overkill_coverage_end']
                if start is not None:
                    _require(end is not None and 0 <= start <= end <= data['duration'],
                             'invalid overkill coverage interval')
            for field in ('unknown_sources', 'unmapped_tokens', 'ambiguous_sources',
                          'mapping_conflicts', 'missing_fields'):
                _require(isinstance(stats[field], list) and
                         all(isinstance(s, str) for s in stats[field]), f'invalid {field}')
            # Optional ARK-24 fields: records written before the totals/rate
            # split lack them, but a present value must never be malformed.
            if 'totals_complete' in stats:
                _require(type(stats['totals_complete']) is bool, 'invalid totals_complete')
            if 'non_team_sources' in stats:
                _require(isinstance(stats['non_team_sources'], list) and
                         all(isinstance(s, str) and s for s in stats['non_team_sources']),
                         'invalid non_team_sources')
            if 'non_team_output' in stats:
                output = stats['non_team_output']
                _require(isinstance(output, dict), 'invalid non_team_output')
                for cid, values in output.items():
                    _require(isinstance(cid, str) and bool(cid) and isinstance(values, dict),
                             'invalid non_team_output entry')
                    _require(set(values) == {'damage', 'healing'},
                             'invalid non_team_output counters')
                    for value in values.values():
                        _number(value, 'non_team_output', nullable=True)
            sample = data['last_sample']
            _require(isinstance(sample, dict) and sample.get('session') == battle_id,
                     'last sample battle_id mismatch')
            _validate_multiplayer(sample.get('multiplayer'), battle_id, data['duration'])
            _require(isinstance(data['event_status'], str), 'invalid event_status')
            _require(data['event_error'] is None or isinstance(data['event_error'], str),
                     'invalid event_error')
            if data['snapshot_intervals'] is not None:
                _intervals(data['snapshot_intervals'], data['duration'])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise HistoryError(f'invalid BattleRecord: {exc}') from exc
        return cls(data)

    @property
    def battle_id(self):
        return self._data['battle_id']

    @property
    def statistics(self):
        return deepcopy(self._data['statistics'])

    def statistics_fields(self, fields):
        statistics = self._data['statistics']
        return deepcopy({key: statistics[key] for key in fields if key in statistics})

    @property
    def last_sample(self):
        return deepcopy(self._data['last_sample'])

    @property
    def metadata(self):
        return deepcopy(self._data['metadata'])

    @property
    def event_status(self):
        return self._data['event_status']

    @property
    def event_error(self):
        return self._data['event_error']

    def display_metadata(self):
        metadata = self.metadata
        evidence = [metadata.get('evidence')]
        sources = metadata.get('evidence_sources')
        if isinstance(sources, (list, tuple)):
            evidence.extend(sources)
        # This old PC hint also reported True for ordinary actions. Retain the
        # archival evidence, but do not label its mode as an established fact.
        if (metadata.get('platform') == 'native_pc' and any(
                isinstance(value, str) and 'native_pc_squad_loader' in value.split('+')
                for value in evidence)):
            metadata['practice'] = None
        return dict(metadata=metadata, duration=self._data['duration'],
                    recorded_at=self._data['recorded_at'], observed_at=self._data['observed_at'])

    @property
    def operator_names(self):
        return tuple(row.get('name', '') for row in self._data['statistics']['rows'].values())

    def to_dict(self):
        return deepcopy(self._data)


@dataclass(frozen=True)
class HistoryDiagnostic:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class HistoryResult:
    records: tuple = ()
    diagnostics: tuple = ()
    written: bool = False


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f'duplicate JSON key: {key}')
        result[key] = value
    return result


class BattleHistory:
    """Atomic JSON files, durable ID deduplication, and newest-100 read API.

    Retention counts only validated records. Invalid/unknown files stay intact.
    The app's single acquisition owner performs all writes; the lock also
    serializes writers/readers in this process, including separate instances.
    """

    def __init__(self, directory):
        self.directory = Path(directory)

    def path_for(self, battle_id):
        _require(isinstance(battle_id, str) and bool(battle_id.strip()), 'invalid battle_id')
        return self.directory / (hashlib.sha256(battle_id.encode('utf-8')).hexdigest() + '.json')

    def _read(self, path):
        with path.open(encoding='utf-8') as stream:
            record = BattleRecord.from_dict(json.load(stream, object_pairs_hook=_unique_keys))
        _require(self.path_for(record.battle_id) == path, 'filename battle_id mismatch')
        return record

    def _scan(self):
        records, diagnostics = [], []
        try:
            paths = list(self.directory.iterdir())
        except FileNotFoundError:
            return records, diagnostics
        except OSError as exc:
            return records, [HistoryDiagnostic('read_failed', str(self.directory), str(exc))]
        for path in paths:
            if path.suffix != '.json':
                continue
            try:
                records.append(self._read(path))
            except (OSError, ValueError, UnicodeError) as exc:
                diagnostics.append(HistoryDiagnostic('invalid_record', str(path), str(exc)))
        records.sort(key=lambda r: (_date(r._data['observed_at'] or r._data['recorded_at']),
                                    r.battle_id), reverse=True)
        return records, diagnostics

    def list_recent(self):
        with _LOCK:
            records, diagnostics = self._scan()
            return HistoryResult(tuple(records[:100]), tuple(diagnostics))

    def get(self, battle_id):
        path = self.path_for(battle_id)
        with _LOCK:
            try:
                return HistoryResult((self._read(path),))
            except FileNotFoundError:
                return HistoryResult()
            except (OSError, ValueError, UnicodeError) as exc:
                return HistoryResult(diagnostics=(HistoryDiagnostic('invalid_record', str(path), str(exc)),))

    def save(self, record):
        record = BattleRecord.from_dict(record.to_dict())
        path = self.path_for(record.battle_id)
        with _LOCK:
            temporary = None
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    existing = self._read(path)
                    before, after = existing.to_dict(), record.to_dict()
                    before.pop('recorded_at')
                    after.pop('recorded_at')
                    _require(before == after, 'battle_id conflict; original record retained')
                    return self._retention(existing, written=False)
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory,
                                                 prefix='.battle-', suffix='.tmp', delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(record.to_dict(), stream, ensure_ascii=False, allow_nan=False,
                              sort_keys=True, separators=(',', ':'))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                temporary = None
                self._read(path)
            except (OSError, ValueError, UnicodeError) as exc:
                raise HistoryError(f'history write failed ({path.name}): {exc}') from exc
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
            return self._retention(record, written=True)

    def _retention(self, record, *, written):
        # Never evict reliable history before a summary has committed and been
        # read back. Duplicate handoffs can retry an earlier failed eviction.
        records, diagnostics = self._scan()
        for old in records[100:]:
            old_path = self.path_for(old.battle_id)
            try:
                old_path.unlink()
            except OSError as exc:
                diagnostics.append(HistoryDiagnostic('prune_failed', str(old_path), str(exc)))
        return HistoryResult((record,), tuple(diagnostics), written=written)
