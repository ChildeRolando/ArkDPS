"""Ordered, process-local battle observations and exactly-once archive handoff.

The feed has one acquisition owner. Consumers may queue observations, but must
keep their generation and sequence. Addresses and frames are not battle IDs.
"""
from __future__ import annotations

from metrics import timed
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
import math
import re
import hashlib
import struct
import time
import uuid

from autochess_context import (NATIVE_AUTOCHESS_CHARACTER_COUNTERS, native_autochess_scope_id,
                               native_autochess_event_identity)
from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch, AdapterError, MappingUnavailable
from event_capture import (LiveEventCapture, CaptureRetry, read_event_batch,
                           acknowledge_event_batch, read_terminal_mark, source_attribution_batch,
                           read_pending_scope_prefix, confirm_sampling_mark)
from event_observer import ObserverReadRetry
from event_probe import (CallbackBusyMark, MuMuAutoChessProbeContract, NativePcProbeContract,
                         android_autochess_event_window, android_autochess_mark_context,
                         annotate_barrier_busy,
                         autochess_event_mark_context, verified_autochess_event_window,
                         autochess_root_failure, autochess_scope_detail,
                         bind_native_autochess_pair, contract_for, native_autochess_context_bound,
                         native_identity_sampling_mark, TerminalReadRetry,
                         verified_autochess_round_start, autochess_round_start_origin,
                         pending_scope_anchor, verified_pending_scope_start,
                         pending_scope_successor, verified_pending_scope_successor,
                         _verified_pending_scope_anchor,
                         settlement_source_quality_clear, settlement_failure_samples,
                         probe_cursor_lifetime, check_cursor_lifetime)
from event_stream import StreamGap, BarrierBusy, baseline_barrier
from panel_model import team_status
from roster import RosterSnapshot


NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED = 'native_pc_autochess_scope_unverified'
ANDROID_PRESENCE_INTERVAL = 1.
ANDROID_PRESENCE_UNIT_LIMIT = 128


def _native_pc_autochess(metadata, stage):
    return (metadata.get('platform') == 'native_pc' and isinstance(stage, str)
            and 'AUTOCHESS' in stage.upper())


def _native_pc_autochess_scope_unavailable(metadata, stage, sample, observer):
    # Complete paired snapshots with an observer still use the established
    # event admission rules. Incomplete native pairs need independent scope
    # evidence before the event path can advance its watermark.
    return (_native_pc_autochess(metadata, stage)
            and (observer is None or 'native_snapshot_witnesses' in sample)
            and not native_autochess_context_bound(sample))


def _use_autochess_character_counters(sample, observer, previous_scope):
    witnesses = sample.get('native_snapshot_witnesses') or {}
    actor_rows = witnesses.get('actor_rows')
    roster = witnesses.get('roster') or {}
    # The event-only optimization skips the roster by design while the
    # observer is starting. A missing observer here is not read-only mode.
    if (observer is not None or previous_scope is not None
            or roster.get('reason') == 'native_event_identity'
            or (actor_rows is not None and actor_rows.get('status') != 'available')):
        return False
    sample['native_counter_scope'] = NATIVE_AUTOCHESS_CHARACTER_COUNTERS
    sample['presence_duration_supported'] = False
    return True


@dataclass(frozen=True)
class NativeAutoChessScene:
    match: tuple
    scope: str
    phase: int
    round: int
    collection_mode: str | None = None


def autochess_scene(sample):
    """Return display identity from a guarded pair, without requiring a drain."""
    if not native_autochess_context_bound(sample):
        return None
    context, metadata = sample['autochess_context'], sample['battle_metadata']
    identity = context['identity']
    roots, separator, _ = identity['scope_key'].rpartition(':autochess:')
    session = sample.get('native_session', sample.get('session'))
    if not (roots and separator and isinstance(session, str)):
        return None
    if metadata.get('platform') == 'android':
        parts = session.split(':')
        if len(parts) != 6 or not parts[0] or not all(p.isascii() and p.isdecimal() for p in parts[1:]):
            return None
        process = parts[:3]
    else:
        parts = session.split(':', 2)
        if len(parts) != 3 or not all(p.isascii() and p.isdecimal() for p in parts[:2]):
            return None
        process = parts[:2]
    match_key = context.get('match_key', roots)
    if not match_key:
        return None
    accounts = tuple(sorted((player['index'], player['uid']) for player in context['players']))
    # The verified game manager and data center survive small-round controller
    # changes. Older PC records remain tied to their guarded battle roots.
    match = (*process, metadata.get('source_build'), metadata['stage_id'], match_key, accounts,
             context.get('self_player_uid'))
    return NativeAutoChessScene(match, native_autochess_scope_id(identity, accounts),
                                context['phase'], context['round'], context.get('collection_mode'))


native_autochess_scene = autochess_scene  # Existing offline fixture entry point.


def _autochess_successor_ends_round(previous, successor):
    if (previous is None or successor is None or previous.match != successor.match
            or previous.scope == successor.scope
            or previous.phase not in (4, 5, 6)
            or previous.collection_mode not in ('local', 'all')
            or previous.collection_mode != successor.collection_mode):
        return False
    # Defense can hand off to assistance before the shared round clock resets.
    # This only identifies a candidate scope boundary; the consumer still
    # requires the unchanged observer sequence/epoch and all native fences.
    return (successor.phase == 7 and previous.round == successor.round
            or previous.phase == 4 and successor.phase == 5 and previous.round == successor.round
            or successor.phase in (3, previous.phase) and successor.round == previous.round + 1)


def _native_pc_guarded_clock_reset(previous, successor):
    """Recognize the collector's serial change using both independent reads.

    PC sessions include the sampler serial, which increments on clock reset.
    Equal Unity roots alone are insufficient: verify the exact two guarded
    serials, process birth, stage/build and their canonical session digests.
    This identifies a candidate only; source epoch/sequence fences still own
    whether that candidate can finish any event prefix.
    """
    try:
        scenes = [autochess_scene(sample) for sample in (previous, successor)]
        if (None in scenes or scenes[0].match != scenes[1].match
                or scenes[0].phase not in (4, 5, 6) or scenes[1].phase != scenes[0].phase
                or scenes[1].round not in (scenes[0].round, scenes[0].round + 1)
                or scenes[0].scope == scenes[1].scope
                or scenes[0].collection_mode != scenes[1].collection_mode
                or successor['frame'] >= previous['frame']
                or successor['game_time'] >= previous['game_time']):
            return False
        guards = []
        roots = ('controller', 'native', 'logger', 'stats', 'level')
        for sample in (previous, successor):
            meta, guard = sample['battle_metadata'], sample['native_window_guard']
            if (type(sample.get('state')) is not int or sample['state'] != 2
                    or meta['platform'] != 'native_pc' or guard.get('version') != 1
                    or type(guard['version']) is not int or guard.get('source') != 'native_pc_guarded_read'
                    or guard['source_build'] != meta['source_build']
                    or not isinstance(meta['source_build'], str) or not meta['source_build']
                    or type(guard['battle_serial']) is not int or guard['battle_serial'] < 1
                    or guard['process_instance_before'] != guard['process_instance_after']
                    or type(guard['process_instance_before']) not in (list, tuple)
                    or len(guard['process_instance_before']) != 2
                    or any(type(word) is not int or word <= 0 for word in guard['process_instance_before'])
                    or guard['before'] != guard['after']):
                return False
            point = guard['after']
            identity, frame, fixed = point['native_identity'], point['frame'], point['native_fixed_time']
            anchor = sample.get('autochess_snapshot_anchor', sample)
            if (type(identity) is not dict or set(identity) != set(roots)
                    or any(not isinstance(identity[key], str)
                           or re.fullmatch(r'0x[0-9a-fA-F]{1,16}', identity[key]) is None
                           or not 0x10000 <= int(identity[key], 16) <= 0x7fffffffffff
                           or int(identity[key], 16) % 8 for key in roots)
                    or type(frame) is not int or not 0 <= frame <= sample['frame']
                    or not isinstance(fixed, str) or not re.fullmatch(r'[0-9]{1,19}', fixed)
                    or int(fixed) > 0x7fffffffffffffff
                    or int(fixed) / (1 << 32) > sample['game_time']
                    or anchor['frame'] != frame or type(anchor['frame']) is not int
                    or type(anchor['game_time']) not in (int, float)
                    or anchor['game_time'] != int(fixed) / (1 << 32)
                    or point['stage_id'] != meta['stage_id']):
                return False
            digest = hashlib.sha256(struct.pack('<6Q', *[int(identity[key], 16) for key in roots],
                                                guard['battle_serial'])).hexdigest()
            expected_session = ':'.join(map(str, (*guard['process_instance_before'], digest)))
            if (sample.get('native_session', sample['session']) != expected_session
                    or tuple(sample['battle_identity']) != (digest,)):
                return False
            guards.append(guard)
        old, new = guards
        return (new['battle_serial'] == old['battle_serial'] + 1
                and new['process_instance_before'] == old['process_instance_before']
                and new['source_build'] == old['source_build']
                and new['after']['native_identity'] == old['after']['native_identity']
                and new['after']['frame'] < old['after']['frame']
                and int(new['after']['native_fixed_time']) < int(old['after']['native_fixed_time']))
    except (KeyError, ValueError, TypeError, AttributeError, struct.error):
        return False



def _verified_pending_empty_handoff(anchor, latest, successor):
    """Distinguish a real empty serial reset from a legal late zero-output exit."""
    origin, _, _ = _verified_pending_scope_anchor(anchor)
    previous, _, _ = _verified_pending_scope_anchor(latest, allow_epoch_advance=True)
    evidence = {key: latest[key] for key in ('before', 'after', 'sequence', 'drain')}
    evidence['pending_scope_start'] = dict(version=1, anchor=anchor, pages=[])
    verified_pending_scope_start(previous, evidence, previous_sequence=anchor['sequence'])
    current, _ = verified_pending_scope_successor(successor)
    old_scene, new_scene = autochess_scene(previous), autochess_scene(current)
    if (old_scene is None or new_scene is None or old_scene.match != new_scene.match
            or old_scene.collection_mode != new_scene.collection_mode
            or old_scene.scope == new_scene.scope):
        raise StreamGap('pending empty scope handoff changed its process/match/accounts')
    mark, before, after = latest['after'], successor['before'], successor['after']
    for fence in (mark, before, after):
        if (type(fence.get('sequence')) is not int or fence['sequence'] != anchor['sequence']
                or type(fence.get('epoch')) is not int or fence['epoch'] != mark['epoch']
                or type(fence.get('active')) is not int or fence['active'] != 0
                or fence.get('active_hooks') != [] or fence.get('stopped') is not False
                or fence.get('error') is not None or fence.get('retry', False) is not False
                or fence.get('terminal_boundary') is not None
                or type(fence.get('native_battle_state')) is not int or fence['native_battle_state'] != 2
                or 'native_quiet_boundary_version' in fence or 'native_deferred_callbacks' in fence):
            raise StreamGap('pending empty scope handoff has an unread tail or nonquiet source')
    if (before['frame'] != after['frame'] or before['game_time'] != after['game_time']
            or type(mark.get('native_generation')) is not int):
        raise StreamGap('pending empty scope handoff crossed a source clock')
    reset = _native_pc_guarded_clock_reset(previous, current)
    if reset:
        if (anchor['after']['epoch'] != mark['epoch']
                or any(type(fence.get('native_generation')) is not int
                       or fence['native_generation'] != mark['native_generation'] + 1
                       or fence.get('native_identity') != mark.get('native_identity')
                       or fence.get('native_process_pid') != mark.get('native_process_pid')
                       for fence in (before, after))):
            raise StreamGap('pending empty reset lacks a strict source-generation fence')
        # No callback was entered during the entire witnessed pre-start fragment.
        # Retained uncertainty must not be silently discarded with that fragment.
        for fence in (anchor['after'], mark, before, after):
            if (fence.get('enemy_damage_unknown') is not False
                    or fence.get('damage_unknown') is not False
                    or fence.get('damage_settlement_unlocalized_unknown') is not False
                    or fence.get('damage_settlement_source_issues') != []):
                raise StreamGap('pending empty reset contains unresolved source evidence')
        return 'reset'
    if (not _autochess_successor_ends_round(old_scene, new_scene)
            or current['native_session'] != previous['native_session']
            or tuple(current['battle_identity']) != tuple(previous['battle_identity'])
            or current['frame'] <= previous['frame'] or current['game_time'] <= previous['game_time']):
        raise StreamGap('pending zero-output scope exit is not an independently proved successor')
    for fence in (before, after):
        if (fence.get('native_generation') != mark.get('native_generation')
                or fence.get('native_identity') != mark.get('native_identity')
                or type(fence.get('damage_settlement_scope')) is not int
                or fence['damage_settlement_scope'] != mark.get('damage_settlement_scope')
                or not _same_autochess_boundary_quality(previous, mark, fence)):
            raise StreamGap('pending zero-output scope exit changed raw source quality/roots')
    return 'scope_end'


def _same_autochess_boundary_quality(sample, reference, fence):
    if settlement_failure_samples(fence) != settlement_failure_samples(reference):
        return False
    for key in ('quality_contract_version', 'damage_settlement_quality_version',
                'damage_settlement_source_quality_version'):
        if type(fence.get(key)) is not int or fence[key] != 1:
            return False
    for key in ('enemy_damage_ready', 'enemy_damage_unknown', 'damage_settlement_unlocalized_unknown',
                'shield_unknown', 'damage_unknown', 'engagement_unknown', 'skill_unknown',
                'mitigation_ready', 'mitigation_unknown'):
        if type(fence.get(key)) is not bool or fence[key] != reference[key]:
            return False
    for key in ('unknown_sources', 'engagement_unknown_sources'):
        names = fence.get(key)
        if (type(names) is not list or names != reference[key]
                or any(not isinstance(name, str) or not name for name in names)):
            return False
    issues, first = fence['damage_settlement_source_issues'], fence['damage_settlement_first_issue']
    if (type(issues) is not list or issues != reference['damage_settlement_source_issues']
            or first != reference['damage_settlement_first_issue']):
        return False
    if not fence['enemy_damage_unknown']:
        return first is None and settlement_source_quality_clear(fence)
    # The first-issue summary has no scope stamp; its clock can precede a
    # logical-round reset. Its sequence remains bound to this observer prefix.
    if (not isinstance(first, dict) or type(first.get('frame')) is not int
            or first['frame'] < 0
            or type(first.get('game_time')) not in (int, float)
            or not math.isfinite(first['game_time']) or first['game_time'] < 0
            or type(first.get('sequence')) is not int or not 0 <= first['sequence'] <= reference['sequence']
            or not isinstance(first.get('reason'), str) or not first['reason']):
        return False
    if not issues:
        return fence['damage_settlement_unlocalized_unknown']
    # These are retained numeric certificates, not a transport cursor. The
    # producer may retain a prior combat phase's issues on the same controller.
    # Require unchanged, well-formed evidence without reassigning those issues
    # to this scope (or making their numeric metrics complete).
    if len(issues) > 128:
        return False
    fields = {'source', 'source_character_uid', 'frame', 'game_time', 'native_autochess_scope'}
    current = sample['autochess_context']['identity']
    current_scope = tuple(current[key] for key in ('scope_key', 'phase', 'round'))
    for issue in issues:
        if not isinstance(issue, dict) or issue.keys() != fields:
            return False
        cid, uid, frame, when = (issue[key] for key in ('source', 'source_character_uid', 'frame', 'game_time'))
        if (not isinstance(cid, str) or len(cid) > 128
                or not re.fullmatch(r'(?:char|token)_[A-Za-z0-9_#.-]+', cid)
                or type(uid) is not int or not 0 < uid <= 0xffffffff
                or type(frame) is not int or frame < 0
                or type(when) not in (int, float) or not math.isfinite(when)
                or when < 0):
            return False
        scope = native_autochess_event_identity(issue)
        if (scope is None or scope[1] not in (4, 5, 6) or scope[2] < 0
                or issue['native_autochess_scope'].get('collection_mode') not in ('all', 'local')):
            return False
        if (scope == current_scope and
                (frame > sample['frame'] or when > sample['game_time']
                 or frame == sample['frame'] and when != sample['game_time'])):
            return False
    return True


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
    read_error: dict | None = None
    autochess_successor: dict | None = None


class MappingRetry(CaptureRetry):
    """A guarded frame without reliable owner evidence."""

    def __init__(self, reason, observation):
        super().__init__(reason)
        self.observation = observation


def _terminal_boundary(observer, capture=None, *, deadline=None):
    if observer is None:
        return {'terminal_diagnostic': {'status': 'missing', 'reason': 'no_observer'}}
    try:
        if (capture is not None and getattr(capture, 'terminal_pending', False)
                and not capture.closed and capture.adapter.event_stream is not None):
            read_tail = getattr(observer, 'read_terminal_tail', None)
            if not callable(read_tail):
                raise StreamGap('pending terminal source cannot read its final prefix')
            result = read_tail(capture.adapter.event_stream.sequence, deadline=deadline)
            contract = capture.contract
            result['probe_contract'] = contract.describe()
            result['drain']['probe_contract'] = contract.describe()
            return result
        mark = read_terminal_mark(observer)
    except Exception as error:
        # Departure remains certain. Record why its optional raw proof failed,
        # without converting a retry, transport fault, or unknown scope to success.
        retry = isinstance(error, TerminalReadRetry) if getattr(capture, 'terminal_pending', False) else isinstance(error, ObserverReadRetry)
        evidence = {'terminal_diagnostic': {'status': 'retry' if retry else 'rejected',
                                         'error_type': type(error).__name__,
                                         'reason': str(error).partition('\n')[0][:240]}}
        raw = getattr(observer, 'last_terminal_response', None)
        if isinstance(raw, dict):
            evidence['terminal_raw_mark'] = deepcopy(raw)
            if isinstance(raw.get('terminal_boundary'), dict):
                evidence['terminal_raw_candidate'] = deepcopy(raw['terminal_boundary'])
        batch = getattr(observer, 'last_terminal_batch', None)
        if isinstance(batch, dict):
            evidence['terminal_raw_drain'] = deepcopy(batch)
        return evidence
    if not isinstance(mark, dict) or mark.get('error') or mark.get('stopped'):
        return {'terminal_diagnostic': {'status': 'rejected', 'reason': 'source_unavailable'}}
    boundary = mark.get('terminal_boundary')
    if not isinstance(boundary, dict):
        return {'terminal_diagnostic': {'status': 'missing', 'reason': 'no_raw_terminal_boundary'},
                'terminal_raw_mark': deepcopy(getattr(observer, 'last_terminal_response', None) or mark)}
    diagnostic = mark.get('terminal_binding')
    return {'terminal_boundary': boundary,
            'terminal_raw_mark': deepcopy(getattr(observer, 'last_terminal_response', None) or mark),
            'terminal_diagnostic': diagnostic if isinstance(diagnostic, dict)
                                   else {'status': 'candidate', 'reason': 'raw_boundary_received'}}


def _observer_mark(observer, *, retry_busy=False, with_presence=False, opening=None):
    read = ((observer.mark_with_presence if with_presence else observer.mark) if opening is None
            else lambda: confirm_sampling_mark(observer, opening, with_presence=with_presence))
    try:
        mark = read()
    except CallbackBusyMark:
        if not retry_busy:
            raise
        # Yield the host thread before retrying this same mark. A second busy
        # result leaves the cursor untouched and returns to normal polling.
        time.sleep(.008)
        mark = read()
    if not isinstance(mark, dict):
        raise StreamGap('invalid observer mark')
    if mark.get('error'):
        raise StreamGap(f"observer callback error: {mark['error']}")
    if mark.get('stopped'):
        raise StreamGap('observer stopped')
    if mark.get('retry'):
        raise CaptureRetry(autochess_root_failure(mark) or 'observer clock advanced')
    return mark


def _android_scoped_presence(sample, closing_mark, sample_seq):
    """Admit optional units only at the trusted closing mark's own clock."""
    point = dict(version=1, binding='event_mark', sample_seq=sample_seq, frame=sample['frame'],
                 game_time=sample['game_time'], units=None)
    context = sample['autochess_context']
    identity = context['identity']
    wire = closing_mark.get('native_autochess_presence')
    expected = dict(version=1, frame=point['frame'], game_time=point['game_time'],
        native_fixed_time=closing_mark.get('native_fixed_time'), scope_key=identity['scope_key'],
        phase=identity['phase'], round=identity['round'], collection_mode=context.get('collection_mode'))
    if (not isinstance(wire, dict) or set(wire) != {*expected, 'status', 'units'}
            or wire.get('status') != 'available' or expected['collection_mode'] not in ('local', 'all')
            or type(closing_mark.get('native_battle_state')) is not int or closing_mark['native_battle_state'] != 2
            or any(wire[key] != value or type(wire[key]) is not type(value)
                   for key, value in expected.items())
            or not isinstance(wire['units'], list) or len(wire['units']) > ANDROID_PRESENCE_UNIT_LIMIT):
        return point
    members = {member['data_uid']: member for member in context['memberships'] if not member['is_token']}
    instances, units = set(), []
    for unit in wire['units']:
        if (not isinstance(unit, dict) or set(unit) != {'cid', 'data_uid', 'instance_uid', 'player_side'}
                or not isinstance(unit['cid'], str) or not unit['cid']
                or any(type(unit[key]) is not int or not 0 < unit[key] <= 0xffffffff
                       for key in ('data_uid', 'instance_uid'))
                or type(unit['player_side']) is not int or not 0 <= unit['player_side'] <= 4
                or unit['instance_uid'] in instances):
            return point
        instances.add(unit['instance_uid'])
        member = members.get(unit['data_uid'])
        if (member is not None and member['cid'] == unit['cid'] and member['player_side'] == unit['player_side']
                and (context.get('collection_mode') != 'local' or member['player_uid'] == context['self_player_uid'])):
            units.append(dict(unit))
    point['units'] = units
    return point


def _event_barrier(before, sample, after, *, contract=None, capture=None):
    try:
        return _event_barrier_checked(before, sample, after, contract=contract, capture=capture)
    except BarrierBusy as error:
        annotate_barrier_busy(error, 'event_barrier', sample=sample, before=before, after=after,
                              anchor=sample.get('autochess_snapshot_anchor') if isinstance(sample, dict) else None)
        raise


def _event_barrier_checked(before, sample, after, *, contract=None, capture=None):
    """Keep counter baselines quiescent; bound an established event-only prefix.

    Event-only paths use sequence and ownership proof from their original
    marks. The Android clock window uses an independent root read. The
    same-frame path still requires the established scope and its exact clock.
    """
    if autochess_round_start_origin(sample):
        # The producer retained this real historical zero fence. The native
        # counter witness stays at its actual later read time; it supplies
        # roots, not a pretend zero-time cumulative snapshot.
        return baseline_barrier(before, sample, after, contract=contract)
    contract = contract or contract_for(sample=sample)
    if 'autochess_snapshot_anchor' in sample:
        anchor = sample['autochess_snapshot_anchor']
        if (not isinstance(contract, (MuMuAutoChessProbeContract, NativePcProbeContract))
                or not isinstance(anchor, dict) or sample.get('native_counter_scope') != 'events_only'):
            raise StreamGap('invalid AutoChess event window evidence')
        context, sequence = verified_autochess_event_window(sample, before, after, anchor=anchor)
        if sample['autochess_context'] != context:
            raise StreamGap('AutoChess event window does not match the published scope')
        return sequence
    try:
        return baseline_barrier(before, sample, after, contract=contract)
    except BarrierBusy:
        previous = capture.last_sample if capture is not None else None
        stream = capture.adapter.event_stream if capture is not None else None
        if (not isinstance(contract, (NativePcProbeContract, MuMuAutoChessProbeContract)) or capture is None
                or capture.closed or capture.baseline_sequence is None
                or not capture.adapter._events_only or stream is None or stream.closed
                or not isinstance(previous, dict) or sample.get('state') != 2
                or previous.get('state') != 2):
            raise
        # Each original mark independently has all the canonical active/clock/
        # binding checks. Neither mark is rewritten to imitate quiescence.
        opening = baseline_barrier(before, sample, before, contract=contract)
        closing = baseline_barrier(after, sample, after, contract=contract)
        if (opening < stream.sequence or closing < opening or after['epoch'] < before['epoch']
                or (closing > opening and after['epoch'] == before['epoch'])
                or type(before.get('damage_settlement_scope')) is not int
                or type(after.get('damage_settlement_scope')) is not int
                or before['damage_settlement_scope'] < 0
                or after.get('damage_settlement_scope') != before['damage_settlement_scope']):
            raise BarrierBusy('native event boundary regressed or changed settlement scope')
        for mark in (before, after):
            fixed = mark.get('native_fixed_time')
            if (type(mark.get('native_frame')) is not int or mark['native_frame'] != sample['frame']
                    or not isinstance(fixed, str) or not fixed.isascii() or not fixed.isdecimal()
                    or len(fixed) > 20 or int(fixed) > 0x7FFFFFFFFFFFFFFF
                    or int(fixed) / (1 << 32) != sample['game_time']):
                raise BarrierBusy('native event boundary lacks the exact native clock')
        proof = dict(sample)
        bind_native_autochess_pair(proof, before, after)
        current = proof.get('autochess_context') or {}
        prior = previous.get('autochess_context') or {}
        previous_metadata = previous.get('battle_metadata') or {}
        metadata = sample.get('battle_metadata') or {}
        if (not native_autochess_context_bound(proof)
                or prior.get('status') not in ('available', 'partial')
                or current.get('identity') != prior.get('identity')
                or current.get('match_key') != prior.get('match_key')
                or current.get('collection_mode') != prior.get('collection_mode')
                or current.get('self_player_uid') != prior.get('self_player_uid')
                or [(p['index'], p['uid']) for p in current['players']]
                   != [(p['index'], p['uid']) for p in prior['players']]
                or sample.get('native_session', sample.get('session'))
                   != previous.get('native_session', previous.get('session'))
                or metadata.get('source_build') != previous_metadata.get('source_build')
                or sample['frame'] < previous['frame'] or sample['game_time'] < previous['game_time']
                or ('autochess_context' in sample and sample['autochess_context'] != current)):
            raise BarrierBusy('native event-only identity is not the established combat scope')
        return closing


def _verified_autochess_successor_cursor(previous, mark, successor, evidence, stream, last_game_time, contract):
    """The existing raw successor fence, shared by retirement and pending admission."""
    try:
        build = previous['battle_metadata']['source_build']
        if (not _autochess_successor_ends_round(autochess_scene(previous), autochess_scene(successor))
                or previous['state'] != 2 or successor['state'] != 2
                or previous['native_session'] != successor['native_session']
                or tuple(previous['battle_identity']) != tuple(successor['battle_identity'])
                or previous['battle_metadata']['platform'] != successor['battle_metadata']['platform']
                or not isinstance(build, str) or not build or build != successor['battle_metadata']['source_build']
                or successor['frame'] <= previous['frame']
                or successor['game_time'] <= previous['game_time']):
            return False
        before, after = evidence['before'], evidence['after']
        # Sequence excludes a queued tail, epoch excludes even an entered
        # callback without output. A quiet source mark flushes staged HP.
        if (type(mark.get('damage_settlement_scope')) is not int or mark['damage_settlement_scope'] < 0
                or type(mark.get('native_generation')) is not int or mark['native_generation'] < 0
                or type(evidence['sequence']) is not int or evidence['sequence'] != stream.sequence):
            return False
        for fence in (mark, before, after):
            if (type(fence.get('active')) is not int or fence['active'] != 0
                    or fence.get('active_hooks') != [] or fence.get('stopped') is not False
                    or fence['error'] is not None or fence.get('retry', False) is not False
                    or fence['terminal_boundary'] is not None
                    or type(fence.get('native_battle_state')) is not int or fence['native_battle_state'] != 2
                    or 'native_quiet_boundary_version' in fence or 'native_deferred_callbacks' in fence
                    or not _same_autochess_boundary_quality(previous, mark, fence)
                    or type(fence.get('damage_settlement_scope')) is not int
                    or fence['damage_settlement_scope'] != mark['damage_settlement_scope']
                    or type(fence.get('native_generation')) is not int
                    or fence['native_generation'] != mark['native_generation']
                    or fence.get('native_identity') != mark['native_identity']
                    or fence.get('sequence') != stream.sequence or fence.get('epoch') != mark['epoch']):
                return False
        if (stream.frame != previous['frame'] or stream.game_time != previous['game_time']
                or last_game_time != stream.game_time
                or before['frame'] < stream.frame or before['game_time'] < stream.game_time
                or before['frame'] == stream.frame and before['game_time'] != stream.game_time
                or autochess_event_mark_context(previous, mark) != previous['autochess_context']
                or baseline_barrier(mark, previous, mark, contract=contract) != stream.sequence):
            return False
        context, sequence = verified_autochess_event_window(successor, before, after,
            anchor=successor.get('autochess_snapshot_anchor'))
        old_scope = previous['autochess_context']['identity']['scope_key']
        return (context == successor['autochess_context'] and sequence == stream.sequence
                and int(context['identity']['scope_key'].rsplit(':', 1)[1]) > int(old_scope.rsplit(':', 1)[1]))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


class BattleFeed:
    """Issue generations at acquisition, before any asynchronous UI queue."""

    def __init__(self):
        self.id = uuid.uuid4().hex
        self.sequence = 0
        self.generation = 0
        self.identity = None
        self.clock = None
        self.frame = None
        self.retired = None
        self.retired_clock = None
        self.retired_session = None
        self.native_session = None
        self.terminal = False
        self._terminal_retry_started = None
        self._terminal_retry_attempts = 0
        self.current_lifecycle = None
        self.autochess_scene = None
        self.autochess_exited = False
        self.stage = None
        self.retired_stage = None
        self.autochess_identity = None
        self._next_android_presence = float('-inf')
        self._android_presence_seq = 0
        self._pending_scope = None
        self._acquiring_cursor = None
        self._emitted_cursor = None

    def _emit(self, kind, sample=None, roster=None, reason='', events=None, *, round_reset=None,
              read_error=None, observer=None, autochess_successor=None):
        self.sequence += 1
        lifetime = (self._acquiring_cursor[1] if self._acquiring_cursor is not None
                    and self._acquiring_cursor[0] is observer else probe_cursor_lifetime(observer))
        if observer is not None:
            check_cursor_lifetime(observer, lifetime)
        self._emitted_cursor = self.sequence, observer, lifetime
        memo = None
        if events and getattr(observer, 'transfers_event_records', False):
            records = (events.get('drain') or {}).get('events')
            if type(records) is list:
                # The producer handed off these freshly decoded records. Marks
                # and the rest of the event envelope still need their own copies.
                memo = {id(records): records}
        return BattleObservation(self.id, self.sequence, self.generation,
                                 kind, deepcopy(sample), roster, reason, deepcopy(events, memo),
                                 deepcopy(self.current_lifecycle), deepcopy(round_reset), deepcopy(read_error),
                                 deepcopy(autochess_successor))

    @property
    def battle_waiting(self):
        """Use guarded scene evidence, with no update when a read failed."""
        current = self.current_lifecycle
        if current is None:
            return None
        # Settlement can own the flow after the battle singleton is gone.
        return (current['target'] != 'battle_finish' and
                (current['battle'] is None or current['state'] in (0, 1)))

    def gap(self, reason='read_unavailable', *, error=None):
        self.current_lifecycle = None
        detail = (dict(type=type(error).__name__, message=str(error).partition('\n')[0][:240])
                  if error is not None else None)
        return self._emit('gap', reason=reason, read_error=detail)

    def _end(self, reason, sample=None, roster=None, *, observer=None, capture=None):
        if self._pending_scope is not None:
            pending = self._pending_scope
            if observer is not None:
                if observer is not pending['observer']:
                    raise CaptureRetry('pending scope observer changed before its departure')
                check_cursor_lifetime(observer, pending['cursor_lifetime'])
            latest = pending['latest']
            held = deepcopy(latest['sample'])
            held['session'] = f'{self.id}:{pending["generation"]}'
            evidence = {key: deepcopy(latest[key]) for key in ('before', 'after', 'sequence', 'drain')}
            evidence.update(pending_scope_start=dict(version=1, anchor=deepcopy(pending['anchor']), pages=[]),
                pending_scope_end=dict(version=1, reason=reason, lifecycle=deepcopy(self.current_lifecycle),
                                       terminal_evidence=_terminal_boundary(observer, capture=None)))
            self.retired, self.retired_clock, self.retired_session = self.identity, self.clock, self.native_session
            self.retired_stage = self.stage
            self.identity = self.clock = self.frame = self.native_session = self.stage = None
            self.autochess_identity = None
            self.terminal = False
            observation = self._emit('sample', held, RosterSnapshot(latest['roster_frame']),
                                     reason=reason, events=evidence, observer=observer)
            return replace(observation, generation=pending['generation'])
        if self.identity is None:
            return self._emit('waiting', reason=reason)
        # FINISHED can precede OnDestroy. Even after the final cumulative pair
        # was consumed, fetch that later raw fence independently for the journal.
        # A capable PC source may still own an unacknowledged late event tail.
        # Read it under its real destroy fence, never as a new counter sample.
        pending = capture is not None and getattr(capture, 'terminal_pending', False)
        if pending:
            now = time.monotonic()
            if self._terminal_retry_started is None:
                self._terminal_retry_started = now
            deadline = self._terminal_retry_started + 2.
            if capture.closed or now >= deadline or self._terminal_retry_attempts >= 20:
                events = {'terminal_diagnostic': dict(status='rejected', reason=(
                    'terminal_tail_capture_closed' if capture.closed else 'terminal_tail_retry_budget_expired'),
                    attempts=self._terminal_retry_attempts)}
            else:
                self._terminal_retry_attempts += 1
                events = _terminal_boundary(observer, capture, deadline=deadline)
                if (events.get('terminal_diagnostic') or {}).get('status') == 'retry':
                    events['terminal_diagnostic']['attempts'] = self._terminal_retry_attempts
                    if time.monotonic() < deadline and self._terminal_retry_attempts < 20:
                        return self._emit('terminal', reason='terminal_tail_pending', events=events)
                    events['terminal_retry_diagnostic'] = deepcopy(events['terminal_diagnostic'])
                    events['terminal_diagnostic'] = dict(status='rejected',
                        reason='terminal_tail_retry_budget_expired', attempts=self._terminal_retry_attempts)
        else:
            events = _terminal_boundary(observer, capture)
        self.retired, self.retired_clock = self.identity, self.clock
        self.retired_session = self.native_session
        self.retired_stage = self.stage
        self.identity = self.clock = self.frame = self.native_session = None
        self.stage = None
        self.terminal = False
        self._terminal_retry_started = None
        self._terminal_retry_attempts = 0
        self.autochess_identity = None
        return self._emit('end', sample, roster, reason=reason, events=events)

    @staticmethod
    def _project_event_window(sample, before, after):
        try:
            context, sequence = verified_autochess_event_window(sample, before, after)
        except BarrierBusy as error:
            annotate_barrier_busy(error, 'project_event_window', sample=sample, before=before, after=after)
            raise
        except StreamGap as error:
            # A failed event-only projection cannot publish the independent
            # counter anchor as though the requested cross-frame view existed.
            raise FrameMismatch(str(error)) from error
        anchor = {key: sample[key] for key in ('frame', 'game_time')}
        # A moved event watermark never re-tags the independently read totals.
        if (any(before[key] != after[key] for key in ('frame', 'game_time', 'sequence', 'epoch'))
                or anchor['frame'] != context['frame'] or anchor['game_time'] != context['game_time']):
            sample.update(rows=[], global_total=None, global_heal=None, sum_damage=None)
        sample.update(frame=context['frame'], game_time=context['game_time'],
                      native_counter_scope='events_only', autochess_context=context,
                      autochess_snapshot_anchor=anchor)
        return sequence

    def _read_marked_snapshot(self, collector, observer, *, metadata=None, capture=None):
        events = None
        mark = None
        if observer is not None:
            try:
                mark = _observer_mark(observer)
            except (BarrierBusy, CaptureRetry, ObserverReadRetry) as exc:
                raise CaptureRetry(str(exc)) from exc
            except Exception as exc:
                events = {'error': str(exc)}
        sample = collector.read()
        if metadata:
            sample['battle_metadata'] = deepcopy(metadata)
        opening_context = (android_autochess_mark_context(sample, mark)
            if sample.get('state') == 2 and (metadata or {}).get('platform') == 'android'
            and 'AUTOCHESS' in str(metadata.get('stage_id', '')).upper() else None)
        if opening_context is not None:
            # A separately guarded counter read binds roots; the exact closing
            # producer clock and its event-only prefix supply published time.
            now = time.monotonic()
            presence_enabled = (opening_context['phase'] in (4, 5, 6)
                                and callable(getattr(observer, 'mark_with_presence', None)))
            with_presence = presence_enabled and now >= self._next_android_presence
            if with_presence:
                self._next_android_presence = now + ANDROID_PRESENCE_INTERVAL
                self._android_presence_seq += 1
            try:
                end_mark = _observer_mark(observer, with_presence=with_presence, opening=mark)
            except ObserverReadRetry as exc:
                raise CaptureRetry(str(exc)) from exc
            sequence = self._project_event_window(sample, mark, end_mark)
            if with_presence:
                sample['scoped_presence'] = _android_scoped_presence(sample, end_mark, self._android_presence_seq)
            return (sample, RosterSnapshot(sample['frame']),
                    dict(before=mark, after=end_mark, sequence=sequence))
        if mark is not None and events is None:
            try:
                # Skip the roster transaction when the guarded counters already
                # crossed the opening mark. The closing mark is still required.
                baseline_barrier(mark, sample, mark, contract=contract_for(observer, sample))
            except BarrierBusy:
                raise
            except StreamGap as exc:
                events = {'error': str(exc)}
        roster = collector.read_roster()
        if mark is not None and events is None:
            try:
                end_mark = observer.mark()
                if end_mark.get('retry'):
                    raise CaptureRetry(autochess_root_failure(end_mark) or 'observer clock advanced')
                bind_native_autochess_pair(sample, mark, end_mark)
                sequence = _event_barrier(mark, sample, end_mark,
                                          contract=contract_for(observer, sample), capture=capture)
                events = dict(before=mark, after=end_mark, sequence=sequence)
            except BarrierBusy:
                raise
            except (CaptureRetry, ObserverReadRetry) as exc:
                raise CaptureRetry(str(exc)) from exc
            except Exception as exc:
                events = {'error': str(exc)}
        return sample, roster, events

    @staticmethod
    def _guarded_events(observer, opening, sample, capture=None):
        mark, opening_error = opening
        try:
            if isinstance(opening_error, (BarrierBusy, CaptureRetry, ObserverReadRetry)):
                raise opening_error
            if opening_error is not None:
                return {'error': str(opening_error)}
            metadata = sample.get('battle_metadata') or {}
            scoped_pc = (sample.get('state') == 2 and metadata.get('platform') == 'native_pc'
                         and 'AUTOCHESS' in str(metadata.get('stage_id', '')).upper()
                         and autochess_event_mark_context(sample, mark) is not None)
            end_mark = _observer_mark(observer, retry_busy=scoped_pc, opening=mark)
            if scoped_pc:
                BattleFeed._project_event_window(sample, mark, end_mark)
            if not isinstance(contract_for(observer), NativePcProbeContract):
                capture = None
            sequence = _event_barrier(mark, sample, end_mark,
                                     contract=contract_for(observer, sample), capture=capture)
            accept = getattr(observer, 'accept_verified_frame', None)
            if callable(accept):
                accept()
            return dict(before=mark, after=end_mark, sequence=sequence)
        except (BarrierBusy, CaptureRetry, ObserverReadRetry) as exc:
            annotate_barrier_busy(exc, 'guarded_events', sample=sample, before=mark,
                                  after=locals().get('end_mark'),
                                  cursor=getattr(observer, '_committed_sequence', None))
            reject = getattr(observer, 'reject_unverified_frame', None)
            if callable(reject):
                reason = reject(exc)
                if reason is not None:
                    return {'error': reason}
            raise CaptureRetry(str(exc)) from exc
        except FrameMismatch:
            raise
        except Exception as exc:
            return {'error': str(exc)}

    def _read_pair(self, collector, before, observer=None, opening=None, *, capture=None):
        guarded_pair = getattr(collector, 'read_guarded_pair', None)
        if guarded_pair is not None:
            sample, roster = guarded_pair(before)
            after, events = before, None
        else:
            collector.bind_battle(before['battle'][0])
            # Retry a busy frame within the original lifecycle bracket.
            deadline = time.monotonic() + .04
            for attempt in range(3):
                try:
                    sample, roster, events = self._read_marked_snapshot(
                        collector, observer, metadata=before.get('metadata'), capture=capture)
                    break
                except BarrierBusy as exc:
                    if attempt == 2 or time.monotonic() >= deadline:
                        raise CaptureRetry(str(exc)) from exc
            after = collector.read_lifecycle()
        if hasattr(collector, 'event_capability'):
            sample['event_capability'] = deepcopy(collector.event_capability)
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
        if opening is not None and observer is not None:
            events = self._guarded_events(observer, opening, sample, capture)
            if 'autochess_snapshot_anchor' in sample:
                roster = RosterSnapshot(sample['frame'])
        if events and 'before' in events and 'after' in events:
            try:
                if 'autochess_snapshot_anchor' not in sample:
                    bind_native_autochess_pair(sample, events['before'], events['after'])
            except BarrierBusy as exc:
                raise CaptureRetry(str(exc)) from exc
            bind_enemy_scope = getattr(collector, 'bind_enemy_scope', None)
            if callable(bind_enemy_scope):
                bind_enemy_scope(sample)
            self.autochess_scene = autochess_scene(sample)
        metadata = sample.get('battle_metadata') or {}
        stage = metadata.get('stage_id')
        if (metadata.get('platform') == 'android' and self.autochess_scene is None
                and isinstance(stage, str) and 'AUTOCHESS' in stage.upper()):
            if self.autochess_identity is not None and stage == self.stage:
                # A failed optional read cannot downgrade an established
                # event-only prefix, including at a terminal snapshot.
                if sample.get('state') == 3:
                    from collector import SampleUnavailable
                    raise SampleUnavailable('MuMu AutoChess terminal scope is temporarily unverified')
                raise CaptureRetry('MuMu AutoChess scope is temporarily unverified')
            if getattr(observer, 'provides_autochess_context', False):
                sample['autochess_scope_unverified'] = True
                sample['autochess_scope_detail'] = autochess_scope_detail(events)
        return sample, roster, events

    @staticmethod
    def _drain(observer, evidence, sample, *, capture=None):
        if evidence is not None:
            try:
                contract = contract_for(observer, sample)
                native_pc = isinstance(contract, NativePcProbeContract)
                if native_pc and capture is not None and capture.closed:
                    # This battle cannot accept or acknowledge any more events.
                    # Leave the observer available for the next battle's baseline.
                    return None
                if 'error' in evidence:
                    return evidence
                evidence['probe_contract'] = contract.describe()
                if (contract.bounded_reads_required
                        and not (callable(getattr(observer, 'read_through', None))
                                 and callable(getattr(observer, 'commit_events', None)))):
                    raise StreamGap('event probe must support bounded reads and commit acknowledgements')
                scoped = native_pc or sample.get('native_counter_scope') == 'events_only'
                baseline = scoped and (capture is None or capture.baseline_sequence is None)
                evidence['drain'] = read_event_batch(observer, evidence['sequence'], baseline=baseline)
                contract.validate_binding(sample, evidence['drain'])
            except (BarrierBusy, ObserverReadRetry) as exc:
                raise CaptureRetry(str(exc)) from exc
            except CaptureRetry:
                raise
            except Exception as exc:
                return {'error': str(exc)}
        return evidence

    def _pending_scope_drain(self, observer, evidence, sample, roster, *, generation,
                             published_generation, published_capture, published_observer, published_event_mark,
                             cursor_lifetime, capture):
        """Retain an empty late anchor until its first explicitly acquired prefix."""
        check_cursor_lifetime(observer, cursor_lifetime)
        pending = self._pending_scope
        if pending is not None:
            if observer is not pending['observer']:
                raise CaptureRetry('pending scope observer unavailable before its prefix committed')
            check_cursor_lifetime(observer, pending['cursor_lifetime'], phase='pending_prefix')
            anchor = pending['anchor']
            if ((sample.get('autochess_context') or {}).get('identity') !=
                    anchor['sample']['autochess_context']['identity']):
                raise CaptureRetry('pending scope exit awaits its retained empty-scope handoff')
            result = read_pending_scope_prefix(observer, anchor, evidence, sample,
                                               cursor_lifetime=pending['cursor_lifetime'])
            if evidence['sequence'] != anchor['sequence']:
                return result, False
            try:
                pending['latest'] = pending_scope_anchor(sample, result, roster.frame, allow_epoch_advance=True)
            except BarrierBusy as error:
                annotate_barrier_busy(error, 'pending_latest', sample=sample,
                                      before=result.get('before'), after=result.get('after'))
                raise
            except StreamGap:
                # The read can await its first event without proving a new empty endpoint.
                pass
            return result, True
        result = self._drain(observer, evidence, sample, capture=capture)
        check_cursor_lifetime(observer, cursor_lifetime)
        stream = published_capture.adapter.event_stream if published_capture is not None else None
        previous = stream.sequence if stream is not None and observer is published_observer else 0
        if (generation == published_generation + 1 and observer is not None
                and callable(getattr(observer, 'read_round_start_range', None))
                and sample.get('native_counter_scope') == 'events_only'
                and sample.get('state') == 2 and type(sample.get('frame')) is int and sample['frame'] > 0
                and type(sample.get('game_time')) in (int, float) and sample['game_time'] > 0
                and not roster.entries and isinstance(result, dict) and not result.get('error')
                and stream is not None and observer is published_observer
                and not published_capture.closed and not published_capture.pending
                and stream.baseline_frame == 0 and published_capture.adapter.statistics.event_coverage_start == 0
                and _verified_autochess_successor_cursor(published_capture.last_sample, published_event_mark,
                    dict(sample, native_session=sample.get('native_session', sample['session'])),
                    evidence, stream, published_capture.adapter.last_game_time, published_capture.contract)
                and result.get('sequence') == previous and isinstance(result.get('drain'), dict)
                and result['drain'].get('events') == [] and 'autochess_round_start' not in result['drain']):
            try:
                anchor = pending_scope_anchor(sample, result, roster.frame)
            except BarrierBusy as error:
                annotate_barrier_busy(error, 'pending_anchor', sample=sample,
                                      before=result.get('before'), after=result.get('after'))
                raise
            except StreamGap:
                # Legacy snapshots without the new raw independent proof retain
                # their existing explicit late-baseline behavior; never retag zero.
                return result, False
            self._pending_scope = dict(observer=observer, cursor_lifetime=cursor_lifetime, generation=generation,
                                       anchor=anchor, latest=anchor)
            return result, True
        return result, False

    def acquire(self, collector, observer=None, *, published_generation=0,
                preparing_generation=None, published_capture=None, published_observer=None, published_event_mark=None):
        """Bracket cumulative/roster reads with the live singleton and flow.

        Failed reads are unknown. Only an observed null/destroyed singleton,
        FINISHED state, finish transition, or a new guarded identity ends a
        battle. No polling timeout, empty unit list or transport error does.
        """
        self._acquiring_cursor = observer, probe_cursor_lifetime(observer)
        self.current_lifecycle = None
        self.autochess_scene = None
        self.autochess_exited = False
        opening = None
        if getattr(collector, 'read_guarded_pair', None) is not None and observer is not None:
            # The native backend captures the pair in read_lifecycle(). A mark
            # taken while retrieving its cached result would be too late.
            try:
                opening = (_observer_mark(observer, retry_busy=self.autochess_identity is not None), None)
            except Exception as exc:
                opening = (None, exc)
        from collector import NativeClockBusy
        read_with_identity = getattr(collector, 'read_lifecycle_with_event_identity', None)
        identity_mark = (native_identity_sampling_mark(observer, opening[0])
                         if opening is not None and callable(read_with_identity) else None)
        try:
            before = (read_with_identity(identity_mark) if identity_mark is not None
                      else collector.read_lifecycle())
        except NativeClockBusy as exc:
            # Both boundaries failed before any observation was consumed. Keep
            # the earlier opening failure so a typed native busy cannot hide a
            # transport error from the worker's conservative retry classifier.
            if opening is not None and opening[1] is not None:
                raise exc from opening[1]
            raise
        self.current_lifecycle = before
        metadata = before.get('metadata') or {}
        stage = metadata.get('stage_id') if metadata.get('evidence') else None
        stage_changed = bool(stage and stage != self.stage
                             and (self.stage or _native_pc_autochess(metadata, stage)))
        battle = before['battle']
        self.autochess_exited = (isinstance(stage, str) and bool(stage) and 'AUTOCHESS' not in stage.upper()
                                 or battle is None and getattr(collector, 'autochess_absence_is_exit', False))
        if battle is None:
            observation = self._end('battle_absent', observer=observer, capture=published_capture)
            self.retired = self.retired_clock = None
            self.retired_session = None
            return observation
        from collector import collector_process
        process = collector_process(collector)
        identity = (*process.process_instance, process.pid, *battle)
        exiting = (before['transiting'] and before['source'].startswith('level_')
                   and before['target'] == 'battle_finish')
        if before.get('state') == 3 or exiting:
            if self.generation == preparing_generation:
                return self._end('finished')
            # Windows can still supply a same-frame pair on a verified abandon.
            final_pair = before.get('state') == 3 or (
                exiting and metadata.get('platform') == 'native_pc'
                and metadata.get('outcome') == 'abandoned')
            if self.identity == identity and not stage_changed and final_pair:
                from collector import SampleUnavailable, ProcessChanged
                from roster import RosterUnavailable
                from tools.stage_global_state.memory import ReadFailure
                try:
                    sample, roster, events = self._read_pair(
                        collector, before, observer if not self.terminal else None, opening)
                except ProcessChanged:
                    raise
                except (SampleUnavailable, RosterUnavailable, AdapterError, ReadFailure):
                    self.current_lifecycle = before
                    return self._emit('terminal', reason='finished_snapshot_unavailable')
                if sample['session'] == self.native_session and sample['game_time'] >= self.clock:
                    if _native_pc_autochess_scope_unavailable(metadata, stage or self.stage, sample, observer):
                        if self.terminal and self.autochess_identity is not None:
                            return self._emit('waiting', reason='terminal_battle')
                        if (not sample['rows'] or not _use_autochess_character_counters(
                                sample, observer, self.autochess_identity)):
                            return self._emit('scope_unavailable', sample, roster,
                                              reason=NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED, events=events)
                        roster = RosterSnapshot(sample['frame'])
                    capture = published_capture if self.generation == published_generation else None
                    events = self._drain(observer, events, sample, capture=capture)
                    sample['native_session'] = sample['session']
                    sample['session'] = f'{self.id}:{self.generation}'
                    self.terminal = True
                    self.clock = sample['game_time']
                    self.frame = sample['frame']
                    return self._emit('terminal', sample, roster, events=events, observer=observer)
            if self.identity == identity and not stage_changed:
                return self._end('finished', observer=observer, capture=published_capture)
            return self._end('replaced')
        if not before['ready'] or before['transiting']:
            if before.get('state') in (0, 1):
                observation = self._end('battle_initializing')
                self.retired = self.retired_clock = None
                self.retired_session = None
                return observation
            return self.gap('scene_transition')
        try:
            capture = published_capture if published_generation == self.generation else None
            sample, roster, events = self._read_pair(collector, before, observer, opening, capture=capture)
        except FrameMismatch:
            after = self.current_lifecycle
            exit_metadata = (after.get('metadata') or {}) if after else {}
            if (self.identity == identity and after and after.get('battle') == battle
                    and exit_metadata.get('outcome') == 'abandoned'
                    and exit_metadata.get('evidence')
                    and exit_metadata.get('stage_id', self.stage) == self.stage):
                return self._end('finished', observer=observer, capture=published_capture)
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
        frame_reset = (metadata.get('platform') == 'native_pc' and self.frame is not None
                       and sample['frame'] < self.frame)
        clock_reset = same_native and self.clock is not None and (now < self.clock or frame_reset)
        if (not clock_reset and published_capture is not None
                and self.generation == published_generation and observer is not None
                and observer is published_observer):
            clock_reset = _native_pc_guarded_clock_reset(published_capture.last_sample or {}, sample)
        context = sample.get('autochess_context') or {}
        native_context_known = context.get('status') in ('available', 'partial')
        context_identity = context.get('identity') if native_context_known else None
        scope_changed = context_identity is not None and context_identity != self.autochess_identity
        scope_unavailable = _native_pc_autochess_scope_unavailable(
            metadata, stage or (self.stage if same_native else None), sample, observer)
        prior_scope = self.autochess_identity if same_native and not clock_reset else None
        closed_scope = (prior_scope is not None and capture is not None and capture.closed)
        character_counters = scope_unavailable and _use_autochess_character_counters(
            sample, observer, None if closed_scope else prior_scope)
        new_counter_scope = character_counters and closed_scope
        changed = not same_native or clock_reset or scope_changed or new_counter_scope
        generation = self.generation + int(changed)
        if character_counters:
            roster = RosterSnapshot(sample['frame'])
            scope_unavailable = not sample['rows'] and generation == published_generation
        preparing = (generation > published_generation and roster.present_known
                     and not roster.entries and not sample['rows'])
        if character_counters and generation > published_generation:
            preparing = not any(row['damage'] or row['healing'] for row in sample['rows'])
        elif native_context_known:
            # A proven native combat phase may have no local BattlePlayerData.
            # Its typed actor/player evidence travels on the event interface.
            preparing = context['phase'] not in (4, 5, 6)
        # Preparation has no owner baseline. Keep the observer attached and
        # leave the previous prefix for the next accepted combat watermark.
        # A same-controller round reset can still prove the old stream ended
        # at its last accepted watermark, without draining preparation events.
        capture = published_capture if generation == published_generation else None
        pending_handoff = None
        pending = self._pending_scope
        if (pending is not None and not scope_unavailable and context_identity is not None
                and context_identity != pending['anchor']['sample']['autochess_context']['identity']):
            if observer is not pending['observer']:
                raise CaptureRetry('pending scope observer changed before its empty handoff')
            check_cursor_lifetime(observer, pending['cursor_lifetime'])
            check_cursor_lifetime(observer, self._acquiring_cursor[1])
            latest = pending['latest']
            successor = pending_scope_successor(sample, events)
            mode = _verified_pending_empty_handoff(pending['anchor'], latest, successor)
            pending_handoff = dict(version=1, anchor=deepcopy(pending['anchor']),
                                   latest=latest, successor=successor, mode=mode)
        if pending_handoff is not None and pending_handoff['mode'] == 'reset':
            # Only a strict serial/generation reset with no entered callbacks can
            # supersede the witnessed pre-start fragment. The new zero is real.
            events = self._drain(observer, events, sample, capture=None)
            events['pending_scope_superseded'] = pending_handoff
        elif pending_handoff is None and not preparing and not scope_unavailable:
            events, pending_empty = self._pending_scope_drain(observer, events, sample, roster,
                generation=generation, published_generation=published_generation,
                published_capture=published_capture, published_observer=published_observer,
                published_event_mark=published_event_mark, cursor_lifetime=self._acquiring_cursor[1], capture=capture)
            preparing = preparing or pending_empty
        round_reset = None
        if (clock_reset and not scope_unavailable and observer is not None and observer is published_observer
                and self.generation == published_generation and events is not None and 'sequence' in events):
            drained = events.get('drain') or {}
            if not (drained.get('error') or drained.get('stopped')):
                round_reset = {key: events[key] for key in ('before', 'after', 'sequence')}
        # Keep the original successor marks even on preparation, which does
        # not publish an event drain or advance the acknowledged cursor.
        autochess_successor = None
        if (same_native and not clock_reset and scope_changed and not scope_unavailable
                and self.generation == published_generation and observer is not None
                and observer is published_observer and events and not events.get('error')
                and all(key in events for key in ('before', 'after', 'sequence'))
                and metadata.get('platform') in ('native_pc', 'android')
                and published_capture is not None
                and _autochess_successor_ends_round(
                    autochess_scene(published_capture.last_sample or {}), autochess_scene(sample))):
            autochess_successor = {key: events[key] for key in ('before', 'after', 'sequence')}
        if changed:
            self.generation = generation
            self.terminal = False
            self._terminal_retry_started = None
            self._terminal_retry_attempts = 0
            if stage or not same_native:
                self.stage = stage
        elif stage:
            self.stage = stage
        self.identity, self.clock, self.frame = identity, now, sample['frame']
        self.native_session = sample['session']
        if context_identity is not None:
            self.autochess_identity = deepcopy(context_identity)
        elif not same_native or clock_reset or new_counter_scope:
            self.autochess_identity = None
        sample['native_session'] = sample['session']
        sample['session'] = f'{self.id}:{self.generation}'
        if pending_handoff is not None and pending_handoff['mode'] == 'scope_end':
            pending = self._pending_scope
            latest = pending_handoff['latest']
            held_sample = deepcopy(latest['sample'])
            held_sample['session'] = f'{self.id}:{pending["generation"]}'
            held_events = {key: deepcopy(latest[key]) for key in ('before', 'after', 'sequence', 'drain')}
            held_events.update(pending_scope_start=dict(version=1, anchor=deepcopy(pending['anchor']), pages=[]),
                pending_scope_exit=dict(version=1, successor=pending_handoff['successor'], next_generation=generation))
            observation = self._emit('sample', held_sample, RosterSnapshot(latest['roster_frame']),
                                     events=held_events, observer=observer)
            return replace(observation, generation=pending['generation'])
        if scope_unavailable:
            return self._emit('scope_unavailable', sample, roster,
                              reason=NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED, events=events)
        if preparing:
            reason = 'awaiting_character_output' if character_counters else 'empty_roster'
            return self._emit('preparing', sample, roster, reason=reason, round_reset=round_reset,
                              autochess_successor=autochess_successor)
        return self._emit('sample', sample, roster, events=events, round_reset=round_reset, observer=observer,
                          autochess_successor=autochess_successor)


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
        self.multiplayer = None
        self.multiplayer_error = None

    def _consume_multiplayer(self, observation, sequence, drained, mark):
        # The acquisition owner has already accepted this barrier. Player views
        # consume the same ordered prefix and never acquire or acknowledge again.
        try:
            sample = observation.sample or {}
            if sample.get('autochess_scope_unverified'):
                return
            metadata = sample.get('battle_metadata') or {}
            stage = metadata.get('stage_id') or self.metadata.get('stage_id') or ''
            if not isinstance(stage, str) or 'AUTOCHESS' not in stage.upper():
                return
            if self.multiplayer is None:
                from multiplayer_battle import MultiplayerBattleStatistics
                self.multiplayer = MultiplayerBattleStatistics()
            self._multiplayer_call('consume', observation.sample, observation.roster, sequence,
                                   drained, mark=mark, round_id=self.battle_id, return_result=False,
                                   terminal=observation.kind == 'terminal')
        except Exception as exc:
            self.multiplayer_error = str(exc)

    def _multiplayer_call(self, method, *args, **kwargs):
        if self.multiplayer is None:
            return None
        try:
            return getattr(self.multiplayer, method)(*args, **kwargs)
        except Exception as exc:
            # An optional player projection cannot roll back accepted local
            # events, block their acknowledgment, or prevent the main archive.
            self.multiplayer_error = str(exc)
            if method != 'gap':
                self._multiplayer_call('gap', '玩家统计不可用：' + str(exc))
            return None

    def _multiplayer_result(self):
        return self._multiplayer_call('result')

    def _retain_multiplayer(self):
        if self.latest_sample is not None and self.multiplayer is not None:
            self.latest_sample['multiplayer'] = self._multiplayer_result()

    def _snapshot_accepted(self):
        now = self.adapter.last_game_time
        if self._snapshot_open:
            self.snapshot_intervals[-1][1] = now
        else:
            self.snapshot_intervals.append([now, now])
            self._snapshot_open = True

    def _events(self, observation, *, project=True):
        evidence = observation.events
        capability = (observation.sample or {}).get('event_capability', {})
        status = capability.get('status')
        if not evidence and self.event_status == 'unavailable' and status in ('failed', 'unavailable'):
            self.event_status, self.event_error = status, capability.get('error')
        if not evidence or self.capture.closed:
            return
        try:
            if evidence.get('error'):
                raise StreamGap(evidence['error'])
            sequence = _event_barrier(evidence['before'], observation.sample, evidence['after'],
                                      capture=self.capture if observation.kind == 'sample' else None)
            if sequence != evidence['sequence']:
                raise StreamGap('event observation barrier was altered')
            self.capture.ingest(observation.sample, observation.roster, sequence, evidence['drain'],
                                mark=evidence['after'],
                                allow_late_baseline=observation.kind == 'sample')
            if project:
                self._consume_multiplayer(observation, sequence, evidence['drain'], evidence['after'])
            self._last_event_mark = evidence['after']
            self.event_status = 'recording'
            return True
        except BarrierBusy as error:
            annotate_barrier_busy(error, 'consume_events', sample=observation.sample,
                                  before=evidence.get('before'), after=evidence.get('after'))
            raise
        except Exception as exc:
            self.capture.close()
            self.event_status, self.event_error = 'gap', str(exc)
            if self.multiplayer is not None:
                self._multiplayer_call('gap', str(exc))

    def _metadata(self, sample):
        # Backends may supply confirmed metadata, never guessed from state=3,
        # a lost connection, duration, damage, or presence of operators.
        metadata = sample.get('battle_metadata', {})
        outcome = metadata.get('outcome', 'unknown')
        confirmed = isinstance(metadata.get('evidence'), str) and bool(metadata['evidence'].strip())
        result = {
            'stage_id': metadata.get('stage_id') if confirmed else None,
            'practice': metadata.get('practice') if confirmed and
                        isinstance(metadata.get('practice'), bool) else None,
            'outcome': outcome if confirmed and outcome in
                       ('victory', 'defeat', 'abandoned') else 'unknown',
            'evidence': metadata.get('evidence') if confirmed else None,
        }
        build = metadata.get('source_build')
        platform = metadata.get('platform')
        if confirmed and platform in ('android', 'native_pc') and isinstance(build, str) and build:
            result.update(platform=platform, source_build=build)
        return result

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
        if self.multiplayer is not None:
            self._multiplayer_call('gap', reason)
            self._retain_multiplayer()

    def _close_unverified_event_end(self):
        if self.event_status == 'recording':
            self._close_event_gap('未取得最终事件水位')

    def _clock_reset_matches_watermark(self, observation, stream):
        evidence = observation.round_reset
        next_sample, previous = observation.sample, self.latest_sample
        if not isinstance(evidence, dict) or observation.generation != self.generation + 1 or not next_sample:
            return False
        try:
            native_serial_reset = _native_pc_guarded_clock_reset(previous, next_sample)
            if (not previous['native_session']
                    or not native_serial_reset and (next_sample['native_session'] != previous['native_session']
                        or tuple(next_sample['battle_identity']) != tuple(previous['battle_identity']))
                    or next_sample['frame'] >= previous['frame']
                    or next_sample['game_time'] >= previous['game_time']):
                return False
            mark = self._last_event_mark
            after = evidence['after']
            if native_serial_reset:
                before = evidence['before']
                if (not isinstance(self.capture.contract, NativePcProbeContract)
                        or type(mark.get('native_generation')) is not int
                        or mark['native_generation'] < 0
                        or any(type(fence.get('native_battle_state')) is not int
                               or fence['native_battle_state'] != 2
                               or fence.get('stopped') is not False or fence.get('error') is not None
                               or fence.get('terminal_boundary') is not None
                               or fence.get('active_hooks') != [] or fence.get('retry', False) is not False
                               or 'native_quiet_boundary_version' in fence
                               or 'native_deferred_callbacks' in fence
                               for fence in (mark, before, after))
                        or any(type(fence.get('native_generation')) is not int
                               or fence['native_generation'] != mark['native_generation'] + 1
                               or fence.get('native_identity') != mark.get('native_identity')
                               or fence.get('native_process_pid') != mark.get('native_process_pid')
                               for fence in (before, after))):
                    return False
                if autochess_event_mark_context(previous, mark) != previous['autochess_context']:
                    return False
                context, through = verified_autochess_event_window(next_sample, before, after,
                    anchor=next_sample.get('autochess_snapshot_anchor'))
                if context != next_sample['autochess_context'] or through != stream.sequence:
                    return False
            return (baseline_barrier(mark, previous, mark) == stream.sequence
                    and stream.frame == mark['frame'] and stream.game_time == mark['game_time']
                    and baseline_barrier(evidence['before'], next_sample, after) == stream.sequence
                    and evidence['sequence'] == stream.sequence and after['epoch'] == mark['epoch'])
        except (KeyError, ValueError, TypeError):
            return False

    def _autochess_successor_matches_watermark(self, observation, stream):
        evidence = observation.autochess_successor
        previous, successor = self.latest_sample, observation.sample
        contract = self.capture.contract
        if (not isinstance(evidence, dict) or not isinstance(successor, dict)
                or not isinstance(contract, (NativePcProbeContract, MuMuAutoChessProbeContract))
                or observation.generation != self.generation + 1 or self.capture.closed or self.capture.pending
                or stream.baseline_frame != 0 or self.adapter.statistics.event_coverage_start != 0):
            return False
        return _verified_autochess_successor_cursor(previous, self._last_event_mark, successor,
            evidence, stream, self.adapter.last_game_time, contract)

    def _finish_at_verified_watermark(self, observation):
        if (self.event_status != 'recording' or self._last_event_mark is None or
                self.latest_sample is None):
            return False
        stream = self.adapter.event_stream
        if stream is None or stream.closed or self.adapter.statistics.end_time is not None:
            return False
        if observation.kind in ('sample', 'preparing'):
            if getattr(self.capture, 'terminal_pending', False):
                return False
            if (not self._clock_reset_matches_watermark(observation, stream)
                    and not self._autochess_successor_matches_watermark(observation, stream)):
                return False
        elif not self._terminal_boundary_matches_watermark(observation, stream):
            return False
        # The independently verified final source fence also ends the time
        # spent by bodies still deployed at battle end. It is not a retreat
        # callback, and must never be inferred from an ordinary gap/finish().
        if observation.kind not in ('sample', 'preparing'):
            self.adapter.statistics.lifecycle_terminal_watermark = stream.game_time
        self.adapter.statistics.invalidate_rates()
        self.adapter.finish()
        self.event_status, self.event_error = 'finished', None
        self._multiplayer_call('finish', self.adapter.last_game_time, complete=True)
        self._retain_multiplayer()
        return True

    def _terminal_boundary_matches_watermark(self, observation, stream):
        boundary = (observation.events or {}).get('terminal_boundary')
        if not isinstance(boundary, dict):
            return False
        sample = self.latest_sample
        try:
            mark = self._last_event_mark
            contract = self.capture.contract
            if not contract.terminal_identity_matches(sample, boundary):
                return False
            if (baseline_barrier(mark, sample, boundary, contract=contract) != stream.sequence or
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
        if self.capture is not None and getattr(self.capture, 'terminal_pending', False):
            self.adapter.statistics.invalidate_snapshots()
            self.adapter.types_current.clear()
            self.metadata['final_counter_status'] = 'unknown'
            self.metadata['terminal_pending'] = False
            self.metadata['terminal_tail_error'] = self.metadata.get('terminal_tail_error', 'no verified final source fence')
            self.capture.terminal_pending = False
        self.adapter.finish()
        if self.multiplayer is not None:
            self._multiplayer_call('finish', self.adapter.last_game_time,
                                   complete=self.event_status == 'finished')
            self._retain_multiplayer()
        self._archives.append(deepcopy(dict(
            battle_id=self.battle_id, generation=self.generation,
            reason=reason, metadata=self.metadata,
            statistics=self.adapter.result(),
            last_sample=self.latest_sample, event_status=self.event_status,
            event_error=self.event_error, snapshot_intervals=self.snapshot_intervals)))
        self._archived = True
        self.state = 'ended'

    def next_capture_segment(self, reason, feed_id):
        """Shared live/replay boundary for an intentional partial observation."""
        if (reason not in ('event_capture_paused', 'event_capture_resumed')
                or not isinstance(feed_id, str) or not feed_id or feed_id == self.feed_id):
            raise ValueError('invalid capture segment boundary')
        self._close_event_gap(reason)
        self.metadata['capture_segment'] = dict(reason=reason, complete=False)
        self._archive(reason)
        successor = BattleSessions(feed_id)
        successor._archives.extend(self.pop_archives())
        successor.capture_segment_reason = reason
        return successor

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
        pending = getattr(self.capture, 'terminal_pending', False)
        capable = (isinstance(self.capture.contract, NativePcProbeContract)
                   and (observation.sample.get('battle_metadata') or {}).get('platform') == 'native_pc'
                   and type(((observation.events or {}).get('after') or {}).get('terminal_read_version')) is int
                   and observation.events['after']['terminal_read_version'] == 1)
        try:
            # Accept the last verified event watermark before freezing time.
            # Later same-frame revisions change native counters only.
            if final_adapter.statistics.end_time is None:
                if observation.events:
                    final_adapter.update(
                        observation.sample, observation.roster,
                        **self._source_attribution(observation, final_adapter.event_stream))
                    self.adapter = final_adapter
                    final_events = self._events(observation, project=False)
                if not final_events and not pending:
                    self._close_unverified_event_end()
            defer = pending or (capable and final_events)
            final_adapter.update(observation.sample, observation.roster, terminal=True, defer_finish=defer)
        except (AdapterError, ValueError) as exc:
            self.adapter, self.capture = previous_adapter, previous_capture
            self.event_status, self.event_error = previous_status, previous_error
            self._terminal_gap(str(exc))
        else:
            self.adapter = final_adapter
            if final_events:
                self.event_status = 'recording' if defer else 'finished'
                evidence = observation.events
                self._consume_multiplayer(replace(observation, kind='sample') if defer else observation,
                                          evidence['sequence'],
                                          evidence['drain'], evidence['after'])
                if not defer:
                    self._multiplayer_call('finish', self.adapter.last_game_time, complete=True)
            if defer:
                self.capture.terminal_pending = True
                self.metadata['terminal_pending'] = True
                self.metadata['terminal_read_version'] = 1
                self.metadata['final_counter_status'] = 'pending'
                self.metadata['native_counter_watermark'] = deepcopy(self._last_event_mark)
            if self.capture is not None:
                self.capture.adapter = self.adapter
            self.latest_sample = deepcopy(observation.sample)
            self._retain_multiplayer()
            self._snapshot_accepted()
            self.metadata.pop('final_sample_error', None)
            self._merge_metadata(observation.sample)

    def _terminal_tail(self, observation):
        """Consume a final source prefix without replaying cached cumulative rows."""
        evidence = observation.events or {}
        if type(evidence.get('terminal_tail_version')) is not int or evidence['terminal_tail_version'] != 1:
            return False
        previous_adapter, previous_capture = self.adapter, self.capture
        previous_mark = self._last_event_mark
        try:
            stream = self.adapter.event_stream
            sample = self.latest_sample
            if (not getattr(self.capture, 'terminal_pending', False) or self.capture.closed
                    or self.event_status != 'recording' or stream is None or stream.closed
                    or self.adapter.statistics.end_time is not None or sample.get('state') != 3
                    or type(evidence.get('read_after')) is not int or evidence['read_after'] != stream.sequence):
                raise StreamGap('terminal tail does not continue an open pending prefix')
            mark = evidence['after']
            sequence = evidence['sequence']
            if (type(sequence) is not int or sequence < stream.sequence
                    or baseline_barrier(evidence['before'], sample, mark,
                                        contract=self.capture.contract) != sequence
                    or evidence['terminal_boundary'] != mark):
                raise StreamGap('terminal tail final fence changed')
            anchor = self._last_event_mark
            if (baseline_barrier(anchor, sample, anchor, contract=self.capture.contract) != stream.sequence
                    or type(mark.get('epoch')) is not int or mark['epoch'] < anchor['epoch']
                    or (sequence > stream.sequence and mark['epoch'] <= anchor['epoch'])):
                raise StreamGap('terminal tail regressed from its accepted anchor')
            for key in ('native_frame', 'native_fixed_time', 'native_identity', 'native_generation',
                        'native_process_pid', 'native_stage_id', 'native_session', 'source_build',
                        'native_autochess_context', 'damage_settlement_scope'):
                if (key in anchor) != (key in mark) or (key in anchor and anchor[key] != mark[key]):
                    raise StreamGap('terminal tail changed its independently verified clock or scope')
            drained = evidence['drain']
            if (drained.get('terminal_only') is not True or drained.get('retry')
                    or type(drained.get('active')) is not int or drained['active'] != 0
                    or drained.get('active_hooks') != []
                    or drained.get('after') != stream.sequence or drained.get('through') != sequence):
                raise StreamGap('terminal tail batch has no explicit bounded quiescent proof')
            self.capture.contract.validate_batch(sample, sequence, drained, stream=stream)
            final_adapter = deepcopy(self.adapter)
            final_capture = deepcopy(self.capture)
            final_capture.adapter = final_adapter
            attribution = source_attribution_batch(drained, stream.sequence, sequence)
            final_adapter.bind_terminal_event_sources(sample, final_adapter.last_roster,
                source_events=attribution.get('source_events', ()),
                settlement_version=attribution.get('settlement_version'))
            final_capture.ingest(sample, final_adapter.last_roster, sequence, drained, mark=mark)
            advanced = sequence != stream.sequence or mark['epoch'] != anchor['epoch']
            if advanced:
                # These are genuine later callbacks, not a fresh native total.
                final_adapter.statistics.invalidate_snapshots()
                final_adapter.types_current.clear()
                # The optional player reducer has its own frame-bound counter
                # admission. Do not silently finish its old prefix without the
                # newly consumed source records or re-feed cached CID totals.
                self._multiplayer_call('gap', '终局尾部缺少独立的玩家累计修订')
            self.adapter, self.capture = final_adapter, final_capture
            self._last_event_mark = mark
            if not self._finish_at_verified_watermark(observation):
                raise StreamGap('terminal tail does not reach the exact final source fence')
            self.capture.terminal_pending = False
            self.metadata['terminal_pending'] = False
            self.metadata['event_source_final_watermark'] = deepcopy(mark)
            self.metadata['final_counter_status'] = 'unknown' if advanced else 'verified_at_final_watermark'
            self.metadata['event_source_prefix_complete'] = True
            self.metadata['terminal_ack_status'] = 'pending'
            return True
        except Exception as error:
            self.adapter, self.capture = previous_adapter, previous_capture
            self._last_event_mark = previous_mark
            self._close_event_gap('terminal tail rejected: ' + str(error))
            self.metadata['terminal_tail_error'] = str(error)
            return False

    def terminal_acknowledged(self, confirmed):
        """An accepted final prefix and a confirmed producer ACK are different facts."""
        if not self.metadata.get('event_source_prefix_complete'):
            return
        self.metadata['terminal_ack_status'] = 'confirmed' if confirmed else 'pending'
        self.event_status = 'finished' if confirmed else 'gap'
        self.event_error = None if confirmed else '终局事件确认待重试'
        for archive in self._archives:
            if archive['battle_id'] == self.battle_id:
                archive['metadata']['terminal_ack_status'] = self.metadata['terminal_ack_status']
                archive['event_status'], archive['event_error'] = self.event_status, self.event_error

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
    def _source_attribution(observation, stream=None, *, capture=None):
        evidence = observation.events or {}
        through = evidence.get('sequence')
        if type(through) is not int:
            return {}
        sample = observation.sample
        drained = evidence.get('drain') or {}
        if stream is not None and stream.closed:
            stream = None
        after = stream.sequence if stream is not None else through
        contract = contract_for(sample=sample, descriptor=evidence.get('probe_contract') or drained.get('probe_contract'))
        if contract.bounded_reads_required:
            try:
                if _event_barrier(evidence['before'], sample, evidence['after'],
                                  contract=contract, capture=capture) != through:
                    return {}
                contract.validate_batch(sample, through, drained, stream=stream)
            except BarrierBusy:
                raise
            except (KeyError, StreamGap):
                return {}
        return source_attribution_batch(drained, after, through, sample=sample, mark=evidence.get('after'))

    def preflight_event_window(self, observation):
        evidence = observation.events or {}
        if (observation.sample is None or evidence.get('error') or
                not all(key in evidence for key in ('before', 'after', 'sequence'))):
            return
        try:
            _event_barrier(evidence['before'], observation.sample, evidence['after'],
                           capture=self.capture if observation.generation == self.generation else None)
        except BarrierBusy as error:
            annotate_barrier_busy(error, 'consume_preflight', sample=observation.sample,
                                  before=evidence['before'], after=evidence['after'],
                                  anchor=observation.sample.get('autochess_snapshot_anchor'),
                                  cursor=(self.adapter.event_stream.sequence if self.adapter is not None
                                          and self.adapter.event_stream is not None else None))
            raise
        except (KeyError, StreamGap):
            # Preserve the established fatal/gap handling. This is not a
            # second validator and grants no acceptance of malformed evidence.
            return

    def consume(self, observation):
        if (observation.feed != self.feed_id or
                observation.sequence <= self.sequence or
                observation.generation < max(self.generation, self.preparing_generation or 0)):
            return False
        if observation.kind not in ('sample', 'terminal', 'gap', 'end', 'waiting', 'preparing', 'scope_unavailable'):
            raise ValueError('unknown observation kind')
        self.preflight_event_window(observation)
        if (observation.events or {}).get('capture_pause_tail') is True:
            if (observation.kind not in ('sample', 'terminal') or self.adapter is None
                    or observation.generation != self.generation):
                raise ValueError('pause tail does not belong to the active observation segment')
            self.adapter.statistics.event_coverage_broken = True
        if observation.kind == 'scope_unavailable':
            self._multiplayer_call('invalidate_presence')
            if observation.generation > self.generation:
                self._archive('new_battle')
                self.preparing_generation = observation.generation
            # A retry within the same generation can still fill the event
            # prefix. Keep its acknowledgment and coverage at the last mark.
            self.state = 'frozen' if self.adapter is not None else 'waiting'
            self.connection = 'connected'
            self.reason = observation.reason
        elif observation.kind == 'preparing':
            if observation.generation <= self.generation:
                return False
            self._archive('new_battle', successor=observation)
            self.preparing_generation = observation.generation
            self.state = 'preparing'
            self.connection = 'connected'
            self.reason = observation.reason
        elif observation.kind == 'sample':
            pending_fields = {'pending_scope_start', 'pending_scope_exit', 'pending_scope_end',
                              'pending_scope_superseded'} & (observation.events or {}).keys()
            if pending_fields and observation.generation <= self.generation:
                raise StreamGap('pending scope transaction cannot reinitialize an established generation')
            if (len(pending_fields & {'pending_scope_exit', 'pending_scope_end', 'pending_scope_superseded'}) > 1
                    or 'pending_scope_superseded' in pending_fields and 'pending_scope_start' in pending_fields):
                raise StreamGap('conflicting pending scope handoff transactions')
            pending_exit = (observation.events or {}).get('pending_scope_exit')
            pending_end = (observation.events or {}).get('pending_scope_end')
            if pending_end is not None:
                if (not isinstance(pending_end, dict)
                        or set(pending_end) != {'version', 'reason', 'lifecycle', 'terminal_evidence'}
                        or type(pending_end.get('version')) is not int or pending_end['version'] != 1
                        or pending_end['reason'] != observation.reason
                        or pending_end['lifecycle'] != observation.lifecycle
                        or not isinstance(pending_end['terminal_evidence'], dict)):
                    raise StreamGap('invalid pending scope interrupted-end transaction')
                life = observation.lifecycle
                reason = pending_end['reason']
                valid_end = (
                    reason == 'process_changed' and life is None
                    or isinstance(life, dict) and (
                        reason == 'battle_absent' and life.get('battle') is None
                        or reason == 'battle_initializing' and type(life.get('state')) is int and life['state'] in (0, 1)
                        or reason in ('finished', 'replaced') and (life.get('state') == 3
                            or life.get('transiting') is True and life.get('target') == 'battle_finish')))
                start = (observation.events or {}).get('pending_scope_start')
                if not valid_end or not isinstance(start, dict) or start.get('pages') != []:
                    raise StreamGap('pending scope end lacks its original lifecycle departure')
                pending_scope_anchor(observation.sample, observation.events, observation.roster.frame,
                                     allow_epoch_advance=True)
            superseded = (observation.events or {}).get('pending_scope_superseded')
            if superseded is not None:
                if (not isinstance(superseded, dict)
                        or set(superseded) != {'version', 'anchor', 'latest', 'successor', 'mode'}
                        or type(superseded.get('version')) is not int or superseded['version'] != 1
                        or superseded['mode'] != 'reset'
                        or _verified_pending_empty_handoff(superseded['anchor'], superseded['latest'], superseded['successor']) != 'reset'
                        or pending_scope_successor(observation.sample, observation.events) != superseded['successor']):
                    raise StreamGap('invalid pending scope supersession transaction')
                previous_stream = self.adapter.event_stream if self.adapter is not None else None
                expected = previous_stream.sequence if previous_stream is not None else 0
                if superseded['anchor']['sequence'] != expected:
                    raise StreamGap('pending scope supersession changed its accepted cursor')
            if pending_exit is not None:
                if (not isinstance(pending_exit, dict) or set(pending_exit) != {'version', 'successor', 'next_generation'}
                        or type(pending_exit.get('version')) is not int or pending_exit['version'] != 1
                        or type(pending_exit['next_generation']) is not int
                        or pending_exit['next_generation'] != observation.generation + 1):
                    raise StreamGap('invalid pending zero-output scope exit transaction')
                start = (observation.events or {}).get('pending_scope_start')
                if not isinstance(start, dict) or start.get('pages') != []:
                    raise StreamGap('pending empty scope exit contains an unexpected event prefix')
                latest = pending_scope_anchor(observation.sample, observation.events, observation.roster.frame,
                                              allow_epoch_advance=True)
                if _verified_pending_empty_handoff(start['anchor'], latest, pending_exit['successor']) != 'scope_end':
                    raise StreamGap('pending scope exit cannot discard a reset fragment')
            if observation.generation <= 0 or observation.sample['session'] != f'{self.feed_id}:{observation.generation}':
                raise ValueError('sample does not match its observation generation')
            if observation.generation == self.generation and (
                    self._archived or self._terminal_seen or (self.adapter is not None and
                                       self.adapter.statistics.end_time is not None)):
                return False
            # A guarded new generation retires the old battle even if the new
            # roster cannot yet be mapped. Keep the old adapter for display;
            # its independent archive copy cannot be lost with the new sample.
            event_observation = observation
            if observation.generation > self.generation:
                segment_reason = getattr(self, 'capture_segment_reason', None)
                previous_stream = self.adapter.event_stream if self.adapter is not None else None
                pending_bootstrap = (None if segment_reason else verified_pending_scope_start(
                    observation.sample, observation.events,
                    previous_sequence=previous_stream.sequence if previous_stream is not None else None))
                self._archive('new_battle', successor=observation)
                previous_multiplayer = self.multiplayer
                bootstrap = pending_bootstrap
                bootstrap_error = None
                if (not segment_reason and bootstrap is None and observation.events
                        and isinstance(observation.events.get('drain'), dict)):
                    try:
                        bootstrap = verified_autochess_round_start(observation.sample,
                            observation.events['drain'], observation.events.get('after'))
                    except BarrierBusy:
                        raise
                    except StreamGap as exc:
                        # Keep the independent current snapshot, but never ACK
                        # an advertised historical prefix whose proof failed.
                        bootstrap_error = str(exc)
                # Sessions alone selects generation retirement. The reducer
                # must not silently finish/restart on a malformed same-generation
                # sample or an unannounced clock reset.
                adapter = BattleStatisticsAdapter(lifecycle_managed=True)
                if bootstrap is not None:
                    zero_sample, zero_mark, zero_drain, replay_drain = bootstrap
                    zero_roster = RosterSnapshot(zero_sample['frame'])
                    adapter.update(zero_sample, zero_roster)
                else:
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
                self.multiplayer = None
                if (previous_multiplayer is not None and observation.events
                        and isinstance(observation.events.get('drain'), dict)):
                    from multiplayer_battle import MultiplayerBattleStatistics
                    self.multiplayer = MultiplayerBattleStatistics()
                    self.multiplayer.inherit_settlement_scopes(previous_multiplayer, observation.sample)
                self.multiplayer_error = None
                self.metadata = self._metadata(observation.sample)
                if segment_reason:
                    self.metadata['capture_segment'] = dict(reason=segment_reason, complete=False)
                    self.adapter.statistics.event_coverage_broken = True
                    self.capture_segment_reason = None
                self.snapshot_intervals = []
                self._snapshot_open = False
                if bootstrap is not None:
                    zero_observation = replace(observation, sample=zero_sample, roster=zero_roster,
                        events=dict(before=zero_mark, after=zero_mark,
                                    sequence=zero_mark['sequence'], drain=zero_drain))
                    self._events(zero_observation)
                    adapter.update(observation.sample, observation.roster)
                    event_observation = replace(observation,
                        events=dict(observation.events, drain=replay_drain))
                elif bootstrap_error is not None:
                    event_observation = replace(observation, events={'error': bootstrap_error})
            else:
                self.adapter.update(observation.sample, observation.roster,
                                    **self._source_attribution(observation, self.adapter.event_stream,
                                                              capture=self.capture))
                self._merge_metadata(observation.sample)
            self._events(event_observation)
            self.latest_sample = deepcopy(observation.sample)
            self._retain_multiplayer()
            self._snapshot_accepted()
            self.state = 'playing'
            self.connection = 'connected'
            self.reason = ''
            if pending_exit is not None:
                # Preserve the real late zero-output scope. The existing strict
                # zero-origin/terminal-body rules still decide completeness.
                self._archive('pending_scope_exit')
                self.preparing_generation = pending_exit['next_generation']
                self.state, self.reason = 'preparing', 'pending_scope_exit'
            elif pending_end is not None:
                # This endpoint proves the observed empty interval only. An absent
                # or changed source cannot certify an unread terminal prefix.
                self._archive(pending_end['reason'])
                self.preparing_generation = None
                self.state, self.reason = 'ended', pending_end['reason']
                self.connection = 'disconnected' if pending_end['reason'] == 'process_changed' else 'connected'
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
                if not self._archived:
                    self._terminal_tail(observation)
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
        result = dict(battle_id=self.battle_id, state=self.state,
                      connection=self.connection, reason=self.reason,
                      metadata=self.metadata,
                      event_status=self.event_status, event_error=self.event_error,
                      statistics=self.adapter.result() if self.adapter else None)
        if self.multiplayer is not None:
            result['multiplayer'] = self._multiplayer_result()
        return deepcopy(result)

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
        self._pending_event_ack = None
        self._accepted_event_observer = None

    def settle_event_ack(self, observer):
        """Confirm only the previously admitted prefix before a control boundary."""
        pending = self._pending_event_ack
        if pending is None:
            return True
        if (pending[0] is not observer or len(pending) < 5
                or probe_cursor_lifetime(observer) is not pending[4]):
            raise CaptureRetry('事件确认仍属于旧观察器，不能切换采集片段')
        sample = self.sessions.latest_sample or {}
        contract = contract_for(observer, sample)
        if pending[2] != contract.ack_scope(sample, self.sessions.generation):
            raise CaptureRetry('事件确认仍属于旧战局，不能切换采集片段')
        if contract.ack_mark_required:
            final = pending[3]
            if final is not None:
                checked = observer.read_terminal_tail(pending[1])
                valid = checked.get('after') == final and checked.get('sequence') == pending[1]
            else:
                valid = contract.validate_ack_mark(sample, _observer_mark(observer), pending[1])
            if not valid:
                raise CaptureRetry('原事件前缀尚未取得同源确认')
        confirmed = acknowledge_event_batch(observer, pending[1])
        if confirmed:
            if pending[3] is not None:
                self.sessions.terminal_acknowledged(True)
            self._pending_event_ack = None
        return bool(confirmed)

    def control_tail_ready(self, sequence):
        if self.sessions.adapter is None:
            return True  # No earlier host observation segment exists.
        capture = self.sessions.capture
        stream = self.sessions.adapter.event_stream
        return (stream is not None and stream.sequence >= sequence
                and self._pending_event_ack is None and not capture.closed)

    def poll_control_tail(self, collector, observer, sequence, generation):
        """Admit an owned pause tail without starting a different battle baseline."""
        observation = self._acquire(collector, observer)
        if (observation.kind not in ('sample', 'terminal') or observation.generation != generation
                or self.sessions.capture is None or self.sessions.capture.closed):
            raise CaptureRetry('暂停前事件尾部尚不能在原战局中验证，保留原输入与队列')
        observation = replace(observation, events=dict(observation.events or {}, capture_pause_tail=True))
        consumed = self._consume(observation)
        self._acknowledge_observation(observation, observer, consumed)
        return observation

    def begin_capture_segment(self, reason):
        """Seal an observation fragment without inventing a new native battle."""
        if self._pending_event_ack is not None:
            raise CaptureRetry('原事件前缀尚未确认，不能切换采集片段')
        previous_feed = self.feed.id
        # Only the host observation namespace changes. Acquired game identities
        # remain untouched, and the existing durable history queue retains ownership.
        feed = BattleFeed()
        sessions = self.sessions.next_capture_segment(reason, feed.id)
        self.feed, self.sessions = feed, sessions
        self.view = BattleView(self.sessions)
        self._accepted_event_observer = None
        return dict(reason=reason, previous_feed=previous_feed, feed=self.feed.id)

    @timed('event_consume')
    def _consume(self, observation):
        # Reject an invalid live handoff before journaling it as a consumable
        # observation; replay must not accept a prefix the live owner rejected.
        receipt = self.feed._emitted_cursor
        if receipt is not None and receipt[0] == observation.sequence and receipt[1] is not None:
            check_cursor_lifetime(receipt[1], receipt[2])
        self.sessions.preflight_event_window(observation)
        self.record(observation)
        try:
            consumed = self.sessions.consume(observation)
            pending = self.feed._pending_scope
            evidence = observation.events or {}
            if consumed and pending is not None and ('pending_scope_start' in evidence or 'pending_scope_superseded' in evidence):
                stream = self.sessions.adapter.event_stream if self.sessions.adapter is not None else None
                if (stream is not None and stream.sequence == evidence['sequence']
                        and stream.frame == observation.sample['frame']
                        and (not self.sessions.capture.closed or 'pending_scope_exit' in evidence
                             or 'pending_scope_end' in evidence)):
                    self.feed._pending_scope = None
            return consumed
        except MappingUnavailable as exc:
            # The native battle and event attachment remain valid while
            # ownership settles. Preserve the queue and the last good view.
            raise MappingRetry(str(exc), observation) from exc

    @timed('capture_acquire')
    def _acquire(self, collector, observer):
        pending = self.feed._pending_scope
        if pending is not None and observer is not None and observer is not pending['observer']:
            # A replacement source cannot certify the retired source's anchor.
            # Absence alone is not retirement: a temporary snapshot-only read
            # must retain the owned prefix for the original source to return.
            self.observer_retired(pending['observer'])
        published_observer = None
        accepted = self._accepted_event_observer
        if (accepted is not None and accepted[1] == self.sessions.generation
                and accepted[0] is observer and len(accepted) > 2
                and probe_cursor_lifetime(observer) is not accepted[2]):
            check_cursor_lifetime(observer, accepted[2], phase='accepted_before_acquisition')
        if (accepted is not None and accepted[1] == self.sessions.generation
                and (len(accepted) < 3 or probe_cursor_lifetime(accepted[0]) is accepted[2])):
            published_observer = accepted[0]
        return self.feed.acquire(collector, observer,
                                 published_generation=self.sessions.generation,
                                 preparing_generation=self.sessions.preparing_generation,
                                 published_capture=self.sessions.capture,
                                 published_observer=published_observer,
                                 published_event_mark=self.sessions._last_event_mark)

    def _acknowledge_observation(self, observation, observer, consumed):
        """Release accepted data without turning a lost ack response into a gap."""
        receipt = self.feed._emitted_cursor
        if (receipt is not None and receipt[0] == observation.sequence and receipt[1] is observer
                and probe_cursor_lifetime(observer) is not receipt[2]):
            self._pending_event_ack = self._accepted_event_observer = None
            return
        pending_ack = self._pending_event_ack
        if (pending_ack is not None and len(pending_ack) > 4
                and probe_cursor_lifetime(pending_ack[0]) is not pending_ack[4]):
            pending_ack = None
        identity_sample = observation.sample or self.sessions.latest_sample or {}
        evidence = observation.events or {}
        contract = contract_for(observer, identity_sample, evidence.get('probe_contract'))
        ack_binding = contract.ack_scope(identity_sample, observation.generation)
        if pending_ack is not None and (pending_ack[0] is not observer
                                        or pending_ack[2] != ack_binding):
            # An integer-only ack must never cross a replacement observer,
            # battle identity or a generation whose sequence can restart.
            pending_ack = None
        current_ack = False
        if consumed and observer is not None and self.sessions.event_status in ('recording', 'finished'):
            evidence = observation.events or {}
            stream = self.sessions.adapter.event_stream
            if ('drain' in evidence and stream is not None
                    and stream.sequence == evidence['sequence']
                    and stream.frame == identity_sample['frame']):
                remember = getattr(observer, 'remember_verified_mark', None)
                is_terminal_tail = evidence.get('terminal_tail_version') == 1
                if callable(remember) and not is_terminal_tail:
                    remember(identity_sample, evidence['after'])
                pending_ack = (observer, stream.sequence, ack_binding,
                               deepcopy(evidence['after']) if is_terminal_tail else None, probe_cursor_lifetime(observer))
                self._accepted_event_observer = (observer, observation.generation, probe_cursor_lifetime(observer))
                current_ack = True
        retry_ack = pending_ack is not None
        if retry_ack and not current_ack and contract.ack_mark_required:
            try:
                # Guard a retained terminal ack against an already observed
                # producer reset; commit itself must still be stream-bound.
                final = pending_ack[3] if len(pending_ack) > 3 else None
                if final is not None:
                    checked = observer.read_terminal_tail(pending_ack[1])
                    mark = checked['after']
                    valid = mark == final and checked.get('sequence') == pending_ack[1]
                else:
                    mark = _observer_mark(observer)
                    valid = contract.validate_ack_mark(identity_sample, mark, pending_ack[1])
                if not valid:
                    pending_ack = None
                    retry_ack = False
            except (CaptureRetry, ObserverReadRetry):
                retry_ack = False
            except Exception:
                pending_ack = None
                retry_ack = False
        if retry_ack:
            confirmed = acknowledge_event_batch(pending_ack[0], pending_ack[1])
            if len(pending_ack) > 3 and pending_ack[3] is not None:
                self.sessions.terminal_acknowledged(confirmed)
            if confirmed:
                pending_ack = None
        self._pending_event_ack = pending_ack

    def poll(self, collector, observer=None, *, prepare_observer=None):
        """Prepare event recovery only after the feed confirms a new battle."""
        from collector import SampleUnavailable, BattleChanged, ProcessChanged, SnapshotGap
        from roster import RosterUnavailable
        from tools.stage_global_state.memory import ReadFailure
        try:
            observation = self._acquire(collector, observer)
            if (prepare_observer is not None and observation.kind in ('sample', 'scope_unavailable')
                    and observation.generation != self.sessions.generation):
                retry, observer = prepare_observer(observation)
                if retry:
                    # Preserve independent native counters before a new
                    # observer barrier can be busy. Player scope must be
                    # certified by the fresh observer's own marks.
                    native = dict(observation.sample,
                                  event_capability=deepcopy(collector.event_capability))
                    metadata = native.get('battle_metadata') or {}
                    if (native.get('native_counter_scope') == 'events_only'
                            and _native_pc_autochess(metadata, metadata.get('stage_id'))):
                        self._consume(replace(observation, kind='scope_unavailable', sample=native,
                                              reason=NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED,
                                              events=None, round_reset=None, autochess_successor=None))
                    else:
                        self._consume(replace(observation, sample=native, events=None, autochess_successor=None))
                    observation = self._acquire(collector, observer)
            consumed = self._consume(observation)
            self._acknowledge_observation(observation, observer, consumed)
        except BarrierBusy as error:
            annotate_barrier_busy(error, 'runtime_poll', cursor=getattr(observer, '_committed_sequence', None))
            raise CaptureRetry(str(error)) from error
        except CaptureRetry:
            raise
        except (SampleUnavailable, RosterUnavailable, FrameMismatch, ReadFailure) as exc:
            if isinstance(exc, ProcessChanged):
                self.process_changed()
                raise
            if isinstance(exc, BattleChanged):
                self._pending_event_ack = None
                self._accepted_event_observer = None
                self._consume(self.feed.gap('process_changed'))
                raise
            if isinstance(exc, SnapshotGap):
                self._consume(self.feed.gap('read_unavailable', error=exc))
            raise CaptureRetry(str(exc)) from exc
        except Exception as exc:
            self._consume(self.feed.gap('read_unavailable', error=exc))
            raise
        return observation

    def process_changed(self):
        self._pending_event_ack = None
        self._accepted_event_observer = None
        self.feed.current_lifecycle = None
        self._consume(self.feed._end('process_changed'))

    def disconnected(self, reason):
        self._pending_event_ack = None
        self._accepted_event_observer = None
        observation = self.feed.gap(reason)
        self._consume(observation)
        self.sessions.connection = 'disconnected'
        return observation

    def observer_retired(self, observer):
        pending = self.feed._pending_scope
        if pending is not None and pending['observer'] is observer:
            # Preserve a replayable coverage break, without emitting the held
            # sample as though its unconsumed tail had been verified.
            self.feed._pending_scope = None
            self._consume(self.feed.gap('pending_scope_observer_retired'))
        if self._accepted_event_observer is not None and self._accepted_event_observer[0] is observer:
            self._accepted_event_observer = None
        if self._pending_event_ack is not None and self._pending_event_ack[0] is observer:
            self._pending_event_ack = None

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
        if sample.get('native_counter_scope') == 'events_only':
            sample['enemy_world_scope'] = self.sessions.adapter.enemy_world_scope
        # consume/gap/finish retain the latest detached player projection. The
        # sample copy above already isolates it from this caller; rebuild only
        # when retention was unavailable, preserving optional-result retries.
        if self.sessions.multiplayer is not None and not isinstance(sample.get('multiplayer'), dict):
            sample['multiplayer'] = self.sessions._multiplayer_result()
        return sample
