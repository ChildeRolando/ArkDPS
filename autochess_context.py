"""Validate native AutoChess identity evidence without collecting or counting.

The acquisition owner must bind process/build and construct a canonical root
scope before calling this module. A scope string is compared, never treated as
proof of its own provenance. This contract accepts one guarded frame, not a
merge of research pages. Player index, battle side and character data UID are
different namespaces; neither UID bit patterns nor card/instance IDs are used.
"""
from hashlib import sha256
import json
from math import isfinite
import re
from types import MappingProxyType


PROTOCOL_VERSION = 1
MAX_MEMBERSHIPS = 256
PHASE_NAMES = {0: 'NONE', 1: 'LOADING', 2: 'SP_PREPARE', 3: 'PREPARE',
               4: 'BATTLE', 5: 'HELP_BATTLE', 6: 'BOSS_BATTLE', 7: 'SETTLE',
               8: 'END', 9: 'PAUSE', 100: 'BATTLE_WAITING',
               101: 'BOSS_PREPARE_WAITING', 102: 'PREPARE_RESTART',
               103: 'SP_PREPARE_RESTART', 104: 'BOSS_BATTLE_RESTART'}
_CID = re.compile(r'[A-Za-z0-9_#.-]{1,128}\Z')
_PLAYER_UID = re.compile(r'[0-9]{1,20}\Z')
_PAGE_FIELDS = ('pages', 'page', 'sampling', 'source_frames', 'sampled_frames')


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _time(value):
    if type(value) not in (int, float) or value < 0:
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _scope(value):
    return (isinstance(value, str) and 0 < len(value) <= 2048
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))


def _cid(value):
    return isinstance(value, str) and _CID.fullmatch(value) is not None


def valid_player_uid(value):
    """Validate opaque account text; never rewrite its leading zeros or identity."""
    return (isinstance(value, str) and _PLAYER_UID.fullmatch(value) is not None
            and any(character != '0' for character in value)
            and (len(value) < 20 or value <= '18446744073709551615'))


def _display(value, maximum):
    return (value is None or isinstance(value, str) and len(value) <= maximum
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _unknown(reasons):
    reasons = list(dict.fromkeys(reasons))
    return dict(protocol_version=PROTOCOL_VERSION, status='unknown',
                reason=reasons[0], reasons=reasons, verified_same_frame=False,
                identity=None, scope_key=None, phase=None, phase_name=None,
                round=None, frame=None, game_time=None, self_player_index=None,
                self_player_uid=None, players=[], memberships=[],
                unknown_memberships=[], membership_status='unknown',
                coverage_complete=False)


def _players(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        return None, 'invalid_players'
    result, indices, uids = [], set(), set()
    for row in rows:
        if (not isinstance(row, dict) or not _integer(row.get('index'), 1, 4)
                or not valid_player_uid(row.get('uid'))
                or not _display(row.get('nick_name'), 128)
                or not _display(row.get('nick_number'), 32)):
            return None, 'invalid_player_identity'
        index, uid = row['index'], row['uid']
        if index in indices or uid in uids:
            return None, 'player_identity_conflict'
        indices.add(index)
        uids.add(uid)
        result.append(dict(index=index, uid=uid, nick_name=row.get('nick_name'),
                           nick_number=row.get('nick_number')))
    return sorted(result, key=lambda row: row['index']), None


def _proof_matches(row, frame, game_time, scope_key):
    if any(field in row for field in _PAGE_FIELDS):
        return False
    return (('frame' not in row or type(row['frame']) is int and row['frame'] == frame)
            and ('game_time' not in row or _time(row['game_time']) and row['game_time'] == game_time)
            and ('scope_key' not in row or row['scope_key'] == scope_key)
            and ('verified_same_frame' not in row or row['verified_same_frame'] is True))


def _member(row, players):
    if not isinstance(row, dict):
        return None, ['invalid_membership']
    reasons = []
    if 'status' in row and row['status'] != 'available':
        reasons.append('membership_unavailable')
    for valid, reason in (
            (_cid(row.get('cid')), 'invalid_cid'),
            (_integer(row.get('data_uid'), 1, (1 << 32) - 1), 'invalid_data_uid'),
            (_integer(row.get('player_index'), 1, 4), 'invalid_player_index'),
            (_integer(row.get('player_side'), 0, (1 << 31) - 1), 'invalid_player_side'),
            (type(row.get('is_token')) is bool, 'invalid_token_flag'),
            (type(row.get('is_predefined')) is bool, 'invalid_predefined_flag')):
        if not valid:
            reasons.append(reason)
    index = row.get('player_index')
    if _integer(index, 1, 4) and index not in players:
        reasons.append('membership_player_unresolved')
    if row.get('is_token') is True:
        if not _cid(row.get('host_key')) or not _integer(row.get('host_uid'), 1, (1 << 32) - 1):
            reasons.append('token_host_unresolved')
    elif row.get('is_token') is False and (row.get('host_key') is not None or row.get('host_uid') is not None):
        reasons.append('unexpected_host_identity')
    if reasons:
        return None, reasons
    result = {field: row.get(field) for field in (
        'cid', 'data_uid', 'player_index', 'player_side', 'is_token',
        'is_predefined', 'host_key', 'host_uid')}
    result['player_uid'] = players[index]['uid']
    result['actor_key'] = [result['player_uid'], result['data_uid']]
    return result, []


def _unknown_member(index, row, reasons):
    result = dict(row_index=index, reason=reasons[0], reasons=reasons)
    if isinstance(row, dict):
        for field, valid in (
                ('cid', _cid(row.get('cid'))),
                ('data_uid', _integer(row.get('data_uid'), 1, (1 << 32) - 1)),
                ('player_index', _integer(row.get('player_index'), 1, 4)),
                ('player_side', _integer(row.get('player_side'), 0, (1 << 31) - 1))):
            if valid:
                result[field] = row[field]
    return result


def _native_unknowns(payload):
    """Retain bounded producer omissions without trusting diagnostic aliases."""
    if 'unknown_memberships' not in payload:
        return []
    rows = payload['unknown_memberships']
    if not isinstance(rows, list):
        return [dict(row_index=0, origin='native_unknown',
                     reason='invalid_unknown_memberships', reasons=['invalid_unknown_memberships'])]
    result = []
    for index, row in enumerate(rows[:MAX_MEMBERSHIPS]):
        reasons = []
        if not isinstance(row, dict):
            reasons.append('invalid_unknown_membership')
        else:
            reason = row.get('reason')
            if isinstance(reason, str) and reason.strip() and _display(reason, 256):
                reasons.append(reason)
            else:
                reasons.append('invalid_unknown_membership_reason')
            if 'row_index' in row and not _integer(row['row_index'], 0, MAX_MEMBERSHIPS - 1):
                reasons.append('invalid_unknown_membership_index')
            for field, valid in (
                    ('cid', _cid(row.get('cid'))),
                    ('data_uid', _integer(row.get('data_uid'), 1, (1 << 32) - 1)),
                    ('player_index', _integer(row.get('player_index'), 1, 4)),
                    ('candidate_player_index', _integer(row.get('candidate_player_index'), 1, 4))):
                if field in row and not valid:
                    reasons.append('invalid_unknown_' + field)
        item = _unknown_member(index, row, reasons)
        item['origin'] = 'native_unknown'
        if isinstance(row, dict):
            if _integer(row.get('row_index'), 0, MAX_MEMBERSHIPS - 1):
                item['row_index'] = row['row_index']
            if _integer(row.get('candidate_player_index'), 1, 4):
                item['candidate_player_index'] = row['candidate_player_index']
        result.append(item)
    if len(rows) > MAX_MEMBERSHIPS:
        result.append(dict(row_index=MAX_MEMBERSHIPS, origin='native_unknown',
                           reason='unknown_memberships_limit', reasons=['unknown_memberships_limit']))
    return result


def normalize_native_autochess(payload, *, expected_frame, expected_game_time,
                               expected_scope_key):
    """Return a detached JSON DTO for one acquisition-owner-bound native frame.

    Root/frame/scope/player-table failures return ``unknown`` with no usable
    memberships. Local member failures return ``partial`` and quarantine only
    those rows; every claim to a duplicate data UID is quarantined together.
    A verified empty list means no evidenced actor, not zero damage or presence.
    ``coverage_complete`` concerns this identity list only, never event/DPS
    coverage. Optional per-row witnesses must belong to this exact frame/scope.
    """
    if not isinstance(payload, dict):
        return _unknown(['invalid_context'])
    reasons = []
    if type(payload.get('protocol_version')) is not int or payload['protocol_version'] != PROTOCOL_VERSION:
        reasons.append('unsupported_protocol')
    if payload.get('status') != 'available':
        reasons.append('native_context_unavailable')
    if payload.get('verified_same_frame') is not True:
        reasons.append('unverified_same_frame')
    frame, game_time, scope_key = (payload.get(key) for key in ('frame', 'game_time', 'scope_key'))
    if not _integer(expected_frame, 0, (1 << 31) - 1) or not _integer(frame, 0, (1 << 31) - 1):
        reasons.append('invalid_frame')
    elif frame != expected_frame:
        reasons.append('frame_mismatch')
    if not _time(expected_game_time) or not _time(game_time):
        reasons.append('invalid_game_time')
    elif game_time != expected_game_time:
        reasons.append('game_time_mismatch')
    if not _scope(expected_scope_key) or not _scope(scope_key):
        reasons.append('invalid_scope_key')
    elif scope_key != expected_scope_key:
        reasons.append('scope_mismatch')
    if type(payload.get('phase')) is not int or payload['phase'] not in PHASE_NAMES:
        reasons.append('invalid_phase')
    if not _integer(payload.get('round'), 0, (1 << 31) - 1):
        reasons.append('invalid_round')
    if not _integer(payload.get('self_player_index'), 0, 4):
        reasons.append('invalid_self_player_index')
    if any(field in payload for field in _PAGE_FIELDS):
        reasons.append('paged_context')
    rows = payload.get('memberships')
    if not isinstance(rows, list) or len(rows) > MAX_MEMBERSHIPS:
        reasons.append('invalid_memberships')
    elif any(isinstance(row, dict) and not _proof_matches(row, frame, game_time, scope_key)
             for row in rows):
        reasons.append('membership_proof_mismatch')
    players, error = _players(payload.get('players'))
    if error:
        reasons.append(error)
    if reasons:
        return _unknown(reasons)

    players_by_index = {row['index']: row for row in players}
    claims, candidates, errors = {}, [], []
    for index, row in enumerate(rows):
        candidate, row_errors = _member(row, players_by_index)
        candidates.append(candidate)
        errors.append(row_errors)
        if isinstance(row, dict) and _integer(row.get('data_uid'), 1, (1 << 32) - 1):
            claims.setdefault(row['data_uid'], []).append(index)
    for indices in claims.values():
        if len(indices) > 1:
            for index in indices:
                errors[index].append('data_uid_conflict')
    native_unknowns = _native_unknowns(payload)
    for unknown in native_unknowns:
        for index in claims.get(unknown.get('data_uid'), ()):
            errors[index].append('data_uid_native_unknown')
    hosts = {row['data_uid']: row for index, row in enumerate(candidates)
             if row is not None and not errors[index] and not row['is_token']}
    for index, row in enumerate(candidates):
        if row is not None and row['is_token']:
            host = hosts.get(row['host_uid'])
            if (host is None or host['cid'] != row['host_key']
                    or host['player_uid'] != row['player_uid']):
                errors[index].append('token_host_unresolved')
    members = [row for index, row in enumerate(candidates) if row is not None and not errors[index]]
    unknown = [_unknown_member(index, row, list(dict.fromkeys(errors[index])))
               for index, row in enumerate(rows) if errors[index]]
    unknown.extend(native_unknowns)
    status = 'partial' if unknown else 'available'
    self_index = payload['self_player_index']
    return dict(protocol_version=PROTOCOL_VERSION, status=status,
                reason='membership_evidence_incomplete' if unknown else None,
                reasons=['membership_evidence_incomplete'] if unknown else [],
                verified_same_frame=True, scope_key=scope_key,
                identity=dict(scope_key=scope_key, phase=payload['phase'], round=payload['round']),
                phase=payload['phase'], phase_name=PHASE_NAMES[payload['phase']],
                round=payload['round'], frame=frame, game_time=game_time,
                self_player_index=self_index,
                self_player_uid=(players_by_index[self_index]['uid'] if self_index in players_by_index else None),
                players=players, memberships=sorted(members, key=lambda row: (
                    row['player_index'], row['data_uid'], row['cid'])),
                unknown_memberships=unknown, membership_status=status,
                coverage_complete=not unknown)


def _readonly(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_readonly(item) for item in value)
    return value


def native_autochess_scope_id(identity, player_accounts):
    """Encode an already validated root identity and (index, account UID) table.

    Runtime and history use the same deterministic scope encoding. Frame/time,
    nickname and battle side never become account identity or scope keys.
    """
    canonical = (identity['scope_key'], identity['phase'], identity['round'],
                 tuple(sorted(player_accounts)))
    return 'autochess:' + sha256(json.dumps(canonical, separators=(',', ':')).encode('utf-8')).hexdigest()


def lookup_native_actor(context, *, cid, data_uid):
    """Return copied read-only identity evidence, or None; never infer an actor.

    The input is a normalized DTO. Partial contexts still expose independently
    verified members. The returned context identity must accompany an actor key
    when retaining it: ``(player_uid, data_uid)`` alone is not cross-round identity.
    """
    if (not isinstance(context, dict) or context.get('status') not in ('available', 'partial')
            or context.get('verified_same_frame') is not True
            or not isinstance(context.get('identity'), dict)
            or not _cid(cid) or not _integer(data_uid, 1, (1 << 32) - 1)):
        return None
    members = context.get('memberships')
    if not isinstance(members, list):
        return None
    if any(isinstance(row, dict) and row.get('data_uid') == data_uid
           for row in context.get('unknown_memberships', ())):
        return None
    matches = [row for row in members if isinstance(row, dict)
               and type(row.get('data_uid')) is int and row['data_uid'] == data_uid]
    if len(matches) != 1 or matches[0].get('cid') != cid:
        return None
    actor = matches[0]
    if not valid_player_uid(actor.get('player_uid')) or actor.get('actor_key') != [actor['player_uid'], data_uid]:
        return None
    return _readonly(dict(actor, context_identity=context['identity']))
