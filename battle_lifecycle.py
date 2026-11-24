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

from autochess_context import NATIVE_AUTOCHESS_CHARACTER_COUNTERS, native_autochess_scope_id
from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch, AdapterError, MappingUnavailable
from event_capture import (LiveEventCapture, CaptureRetry, read_event_batch,
                           acknowledge_event_batch, read_terminal_mark, source_attribution_batch)
from event_observer import ObserverReadRetry
from event_probe import (CallbackBusyMark, MuMuAutoChessProbeContract, NativePcProbeContract,
                         android_autochess_event_window, android_autochess_mark_context,
                         autochess_event_mark_context, verified_autochess_event_window,
                         autochess_root_failure, autochess_scope_detail,
                         bind_native_autochess_pair, contract_for, native_autochess_context_bound,
                         native_identity_sampling_mark, TerminalReadRetry)
from event_stream import StreamGap, BarrierBusy, baseline_barrier
from panel_model import team_status
from roster import RosterSnapshot


NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED = 'native_pc_autochess_scope_unverified'


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


def _observer_mark(observer, *, retry_busy=False):
    try:
        mark = observer.mark()
    except CallbackBusyMark:
        if not retry_busy:
            raise
        # Yield the host thread before retrying this same mark. A second busy
        # result leaves the cursor untouched and returns to normal polling.
        time.sleep(.008)
        mark = observer.mark()
    if not isinstance(mark, dict):
        raise StreamGap('invalid observer mark')
    if mark.get('error'):
        raise StreamGap(f"observer callback error: {mark['error']}")
    if mark.get('stopped'):
        raise StreamGap('observer stopped')
    if mark.get('retry'):
        raise CaptureRetry(autochess_root_failure(mark) or 'observer clock advanced')
    return mark


def _event_barrier(before, sample, after, *, contract=None, capture=None):
    """Keep counter baselines quiescent; bound an established event-only prefix.

    Event-only paths use sequence and ownership proof from their original
    marks. The Android clock window uses an independent root read. The
    same-frame path still requires the established scope and its exact clock.
    """
    contract = contract or contract_for(sample=sample)
    if 'autochess_snapshot_anchor' in sample:
        anchor = sample['autochess_snapshot_anchor']
        if (not isinstance(contract, (MuMuAutoChessProbeContract, NativePcProbeContract))
                or not isinstance(anchor, dict) or sample.get('native_counter_scope') != 'events_only'):
            raise BarrierBusy('invalid AutoChess event window evidence')
        context, sequence = verified_autochess_event_window(sample, before, after, anchor=anchor)
        if sample['autochess_context'] != context:
            raise BarrierBusy('AutoChess event window does not match the published scope')
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

    def _emit(self, kind, sample=None, roster=None, reason='', events=None, *, round_reset=None,
              read_error=None, observer=None):
        self.sequence += 1
        memo = None
        if events and getattr(observer, 'transfers_event_records', False):
            records = (events.get('drain') or {}).get('events')
            if type(records) is list:
                # The producer handed off these freshly decoded records. Marks
                # and the rest of the event envelope still need their own copies.
                memo = {id(records): records}
        return BattleObservation(self.id, self.sequence, self.generation,
                                 kind, deepcopy(sample), roster, reason, deepcopy(events, memo),
                                 deepcopy(self.current_lifecycle), deepcopy(round_reset), deepcopy(read_error))

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
        context, sequence = verified_autochess_event_window(sample, before, after)
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
            except (CaptureRetry, ObserverReadRetry) as exc:
                raise CaptureRetry(str(exc)) from exc
            except Exception as exc:
                events = {'error': str(exc)}
        sample = collector.read()
        if metadata:
            sample['battle_metadata'] = deepcopy(metadata)
        if (sample.get('state') == 2 and (metadata or {}).get('platform') == 'android'
                and 'AUTOCHESS' in str(metadata.get('stage_id', '')).upper()
                and android_autochess_mark_context(sample, mark) is not None):
            # A separately guarded counter read binds roots; the exact closing
            # producer clock and its event-only prefix supply published time.
            try:
                end_mark = _observer_mark(observer)
            except ObserverReadRetry as exc:
                raise CaptureRetry(str(exc)) from exc
            sequence = self._project_event_window(sample, mark, end_mark)
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
            if isinstance(opening_error, (CaptureRetry, ObserverReadRetry)):
                raise opening_error
            if opening_error is not None:
                return {'error': str(opening_error)}
            metadata = sample.get('battle_metadata') or {}
            scoped_pc = (sample.get('state') == 2 and metadata.get('platform') == 'native_pc'
                         and 'AUTOCHESS' in str(metadata.get('stage_id', '')).upper()
                         and autochess_event_mark_context(sample, mark) is not None)
            end_mark = _observer_mark(observer, retry_busy=scoped_pc)
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
            reject = getattr(observer, 'reject_unverified_frame', None)
            if callable(reject):
                reason = reject(exc)
                if reason is not None:
                    return {'error': reason}
            raise CaptureRetry(str(exc)) from exc
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

    def acquire(self, collector, observer=None, *, published_generation=0,
                preparing_generation=None, published_capture=None):
        """Bracket cumulative/roster reads with the live singleton and flow.

        Failed reads are unknown. Only an observed null/destroyed singleton,
        FINISHED state, finish transition, or a new guarded identity ends a
        battle. No polling timeout, empty unit list or transport error does.
        """
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
        if not preparing and not scope_unavailable:
            events = self._drain(observer, events, sample, capture=capture)
        round_reset = None
        if clock_reset and not scope_unavailable and events is not None and 'sequence' in events:
            drained = events.get('drain') or {}
            if not (drained.get('error') or drained.get('stopped')):
                round_reset = {key: events[key] for key in ('before', 'after', 'sequence')}
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
        if scope_unavailable:
            return self._emit('scope_unavailable', sample, roster,
                              reason=NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED, events=events)
        if preparing:
            reason = 'awaiting_character_output' if character_counters else 'empty_roster'
            return self._emit('preparing', sample, roster, reason=reason, round_reset=round_reset)
        return self._emit('sample', sample, roster, events=events, round_reset=round_reset, observer=observer)


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
            if getattr(self.capture, 'terminal_pending', False):
                return False
            if not self._clock_reset_matches_watermark(observation, stream):
                return False
        elif not self._terminal_boundary_matches_watermark(observation, stream):
            return False
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
            except (KeyError, StreamGap):
                return {}
        return source_attribution_batch(drained, after, through)

    def consume(self, observation):
        if (observation.feed != self.feed_id or
                observation.sequence <= self.sequence or
                observation.generation < max(self.generation, self.preparing_generation or 0)):
            return False
        if observation.kind not in ('sample', 'terminal', 'gap', 'end', 'waiting', 'preparing', 'scope_unavailable'):
            raise ValueError('unknown observation kind')
        if observation.kind == 'scope_unavailable':
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
                self.multiplayer = None
                self.multiplayer_error = None
                self.metadata = self._metadata(observation.sample)
                self.snapshot_intervals = []
                self._snapshot_open = False
            else:
                self.adapter.update(observation.sample, observation.roster,
                                    **self._source_attribution(observation, self.adapter.event_stream,
                                                              capture=self.capture))
                self._merge_metadata(observation.sample)
            self._events(observation)
            self.latest_sample = deepcopy(observation.sample)
            self._retain_multiplayer()
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
                                 preparing_generation=self.sessions.preparing_generation,
                                 published_capture=self.sessions.capture)

    def _acknowledge_observation(self, observation, observer, consumed):
        """Release accepted data without turning a lost ack response into a gap."""
        pending_ack = self._pending_event_ack
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
                               deepcopy(evidence['after']) if is_terminal_tail else None)
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
                                              events=None, round_reset=None))
                    else:
                        self._consume(replace(observation, sample=native, events=None))
                    observation = self._acquire(collector, observer)
            consumed = self._consume(observation)
            self._acknowledge_observation(observation, observer, consumed)
        except CaptureRetry:
            raise
        except (SampleUnavailable, RosterUnavailable, FrameMismatch, ReadFailure) as exc:
            if isinstance(exc, ProcessChanged):
                self.process_changed()
                raise
            if isinstance(exc, BattleChanged):
                self._pending_event_ack = None
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
        self.feed.current_lifecycle = None
        self._consume(self.feed._end('process_changed'))

    def disconnected(self, reason):
        self._pending_event_ack = None
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
        if sample.get('native_counter_scope') == 'events_only':
            sample['enemy_world_scope'] = self.sessions.adapter.enemy_world_scope
        # consume/gap/finish retain the latest detached player projection. The
        # sample copy above already isolates it from this caller; rebuild only
        # when retention was unavailable, preserving optional-result retries.
        if self.sessions.multiplayer is not None and not isinstance(sample.get('multiplayer'), dict):
            sample['multiplayer'] = self.sessions._multiplayer_result()
        return sample
