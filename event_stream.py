"""Validate an ordered event stream before submitting it to Statistics.

The producer must provide a barrier taken after the complete baseline and a
monotonic sequence shared by all emitted events and watermarks. A watermark is
valid only after all events through its sequence have been delivered. This
receiver cannot establish those producer guarantees by itself.
"""
from copy import deepcopy
from math import isfinite

from stats_core import ENEMY_STATE_KINDS, Statistics
from event_probe import BarrierBusy, StreamGap, contract_for


def validate_event_binding(sample, *payloads):
    """Compatibility entry point for recordings without a live probe owner."""
    descriptor = next((p['probe_contract'] for p in payloads
                       if isinstance(p, dict) and 'probe_contract' in p), None)
    contract_for(sample=sample, descriptor=descriptor).validate_binding(sample, *payloads)


def validate_native_event_batch(sample, through, batch, *, stream=None):
    """Compatibility name; the selected contract validates a bounded prefix."""
    contract_for(sample=sample, descriptor=batch.get('probe_contract')).validate_batch(
        sample, through, batch, stream=stream)


def baseline_barrier(before, sample, after, *, contract=None):
    """Validate two producer marks surrounding one frame-guarded baseline.

    The producer must increment epoch on *entry* to every relevant callback,
    track callbacks until return, and return marks in the same serialization
    domain as those callbacks. Equal sequence alone does not prove quiescence.
    """
    if contract is None:
        contract = contract_for(sample=sample, descriptor=after.get('probe_contract') if isinstance(after, dict) else None)
    contract.validate_binding(sample, before, after)

    def valid_integer(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    try:
        for mark in (before, after):
            if (not all(valid_integer(mark[k]) for k in ('sequence', 'epoch', 'active', 'frame'))
                    or mark.get('error') or mark.get('stopped')
                    or not isinstance(mark['game_time'], (int, float))
                    or isinstance(mark['game_time'], bool)
                    or not isfinite(mark['game_time']) or mark['game_time'] < 0):
                raise StreamGap('invalid or non-quiescent producer mark')
            if mark['active'] != 0:
                hooks = mark.get('active_hooks') or []
                detail = ', '.join(str(name) for name in hooks[:4])
                raise BarrierBusy('callback is in flight' + (': ' + detail if detail else ''))
        shared_scope = 'damage_settlement_scope' in before and 'damage_settlement_scope' in after
        if (any(before[k] != after[k] for k in ('sequence', 'epoch', 'frame', 'game_time'))
                or (shared_scope and before['damage_settlement_scope'] != after['damage_settlement_scope'])
                or sample['frame'] != after['frame']
                or sample['game_time'] != after['game_time']):
            raise BarrierBusy('baseline crossed an event or game frame')
    except (KeyError, TypeError):
        raise StreamGap('incomplete producer mark or baseline') from None
    return after['sequence']


class EventStream:
    def __init__(self, statistics: Statistics, *, session, baseline_frame,
                 baseline_sequence):
        if (session != statistics.session or not statistics.baseline_ready
                or not statistics.event_coverage or statistics.event_coverage_end is not None
                or not self._nonnegative_int(baseline_frame)
                or not self._nonnegative_int(baseline_sequence)):
            raise ValueError('a covered baseline and its ordered barrier are required')
        self.statistics = statistics
        self.session = session
        self.baseline_sequence = baseline_sequence
        self.baseline_frame = baseline_frame
        self.sequence = baseline_sequence
        self.frame = baseline_frame
        self.game_time = statistics.now
        self.received = {}
        self.closed = False

    @staticmethod
    def _nonnegative_int(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def stop(self, *, complete=False):
        if not self.closed:
            self.statistics.invalidate_events(self.game_time, complete=complete)
            self.closed = True

    def _fail(self, message):
        self.stop()
        raise StreamGap(message)

    def _record_receipt(self, sequence, frame, game_time, payload, replay_record):
        self.sequence = sequence
        self.frame = frame
        self.game_time = game_time
        self.received[sequence] = deepcopy(replay_record) if replay_record is not None else payload

    def _observe_unresolved_source(self, source, source_keys, kind, amount, target_instance, *, damage_type):
        for key in source_keys or (source,):
            self.statistics.observe_unresolved_source(kind, key, amount, target_instance,
                                                      damage_type=damage_type)

    def _mark_settlement_skill_unassigned(self, source, source_keys, method, reason):
        for key in source_keys or (source,):
            self.statistics.mark_settlement_skill_unassigned(key, method, reason)

    def accept(self, verified_skill_end_owner=None, unresolved_source_keys=(), /, *, session, sequence, frame,
               game_time, source, kind, amount,
               target=None, real_delta=None, hp_before=None, hp_after=None,
               killed=None, target_type=None, damage_type=None,
               cancel_reason=None, shield_absorbed=None, target_instance=None,
               target_uid=None, enemy_damage_owner=None,
               skill_key=None, skill_id=None, skill_name=None, skill_reason=None,
               skill_method=None, skill_wait_for_end=None,
               phase_total_hp=None, phase_count=None, phase_from_birth=None,
               enemy_entry_attributes=None, finish_reason=None, enemy_max_hp=None,
               mitigation_before=None, mitigation_after=None,
               source_character_uid=None, unresolved_source=False, settled_after_nested_kill=False,
               settlement_complete=None, nominal_amount=None, settlement_identity=None,
               replay_record=None):
        if self.closed:
            raise StreamGap('event stream is closed')
        payload = replay_record
        if payload is None:
            payload = (session, frame, game_time, source, kind, amount,
                       target, real_delta, hp_before, hp_after, killed,
                       target_type, damage_type, cancel_reason, shield_absorbed,
                       target_instance, target_uid, enemy_damage_owner,
                       skill_key, skill_id, skill_name, skill_reason, skill_method, skill_wait_for_end,
                       phase_total_hp, phase_count, phase_from_birth, enemy_entry_attributes,
                       finish_reason, enemy_max_hp, mitigation_before, mitigation_after,
                       source_character_uid, unresolved_source, settled_after_nested_kill,
                       settlement_complete, nominal_amount, settlement_identity)
        if session != self.session or not self._nonnegative_int(sequence):
            self._fail('session or sequence is invalid')
        if sequence <= self.sequence:
            if sequence in self.received and self.received[sequence] == payload:
                return
            self._fail('event replay conflicts or precedes baseline')
        if sequence != self.sequence + 1:
            self._fail('event sequence gap')
        if (not self._nonnegative_int(frame) or frame < self.frame
                or not isinstance(game_time, (int, float)) or isinstance(game_time, bool)
                or not isfinite(game_time) or game_time < self.game_time
                or (frame == self.frame and game_time != self.game_time)):
            self._fail('event clock is out of order')
        if (self.statistics.settlement_enabled and settlement_identity
                and settlement_identity[0] == 'foreign'):
            # The roster/UID proved this event belongs to another player.
            # It shares the global sequence, but no local skill activation or
            # local legacy damage/HP account can validate its actor evidence.
            elemental_burst = (kind == 'elemental_burst'
                               or kind == 'damage_settlement' and damage_type == 5)
            if kind == 'elemental_bar':
                self.statistics.record_settlement_bar(target_uid, target, target_instance,
                                                       'foreign', None, None)
            elif elemental_burst:
                for uid, bar in tuple(self.statistics.settlement_bars.items()):
                    if uid == target_uid or bar[1] == target_instance:
                        self.statistics.settlement_bars.pop(uid, None)
            if kind == 'elemental_bar' or elemental_burst:
                # Keep foreign target witnesses from leaving earlier local
                # buildup available to an eventual source-null explosion.
                if isinstance(target_instance, str) and target_instance:
                    self.statistics.last_elemental.pop(target_instance, None)
                else:
                    self.statistics.last_elemental.clear()
            self._record_receipt(sequence, frame, game_time, payload, replay_record)
            return
        ignore_passive_native_cast = False
        native_cast = (skill_method or target_instance) == 'BasicSkill.OnCastFinish'
        skill_actor = (settlement_identity[2] if settlement_identity and settlement_identity[0] == 'local'
                       else source_character_uid if not self.statistics.settlement_enabled else None)
        try:
            if native_cast:
                self.statistics._validate_skill_cast_state(
                    kind, 'BasicSkill.OnCastFinish', skill_reason, skill_wait_for_end, skill_key, skill_id)
                if self.statistics.settlement_enabled:
                    state, owner, actor = settlement_identity or ('unknown', None, None)
                    matches = (state == 'local' and actor is not None
                               and self.statistics._native_cast_window_matches(
                                   self.statistics.skill_active.get(owner), source, skill_key, skill_id, actor)
                               and self.statistics._native_cast_window_matches(
                                   self.statistics.settlement_skill_active.get((owner, actor)),
                                   source, skill_key, skill_id, actor))
                    if not matches:
                        if skill_wait_for_end is False:
                            ignore_passive_native_cast = True
                        else:
                            raise ValueError('native cast state has no matching actor activation')
            if self.statistics.settlement_enabled and kind == 'damage_settlement':
                state, owner, actor = settlement_identity or ('unknown', None, None)
                self.statistics.record_damage_settlement(
                    game_time, state, owner, actor, source, target, target_uid, target_instance, amount, shield_absorbed,
                    settlement_complete, damage_type, nominal_amount, mitigation_before, mitigation_after,
                    killed=killed, settled_after_nested_kill=settled_after_nested_kill)
            elif settlement_identity == ('summon', None, None):
                # A proven summon skill never opens a host skill window.
                pass
            elif ignore_passive_native_cast:
                # A false return can follow a real finish inside the native
                # call. An unmatched passive observation cannot affect a CID peer.
                pass
            elif unresolved_source:
                self._observe_unresolved_source(source, unresolved_source_keys,
                    kind, amount, target_instance, damage_type=damage_type)
            elif (skill_method == 'BasicSkill.OnCastFinish' and self.statistics.settlement_enabled
                  and (settlement_identity is None or settlement_identity[0] != 'local')):
                # New native state evidence must not upgrade a different actor
                # merely because its CID still resolves to the same owner.
                self._observe_unresolved_source(source, unresolved_source_keys,
                    kind, amount, target_instance, damage_type=damage_type)
            elif kind == 'enemy_enter':
                self.statistics.record_enemy_entry(
                    frame, game_time, source, amount, target, target_instance,
                    enemy_entry_attributes)
            elif kind == 'enemy_reborn':
                self.statistics.record_enemy_reborn(
                    frame, game_time, source, amount, target, target_instance,
                    hp_before, hp_after, phase_total_hp, phase_count, phase_from_birth)
            elif kind == 'enemy_damage':
                if not self.statistics.settlement_enabled:
                    self.statistics.record_enemy_damage(
                        source, enemy_damage_owner, damage_type, amount, target,
                        target_uid, target_instance)
            elif kind not in ENEMY_STATE_KINDS:
                self.statistics.event(
                    f'{self.session}:{sequence}', game_time, source, kind, amount,
                    target=target, real_delta=real_delta, hp_before=hp_before,
                    hp_after=hp_after, killed=killed, target_type=target_type,
                    damage_type=damage_type, cancel_reason=cancel_reason,
                    shield_absorbed=shield_absorbed, target_instance=target_instance,
                    skill_key=skill_key, skill_id=skill_id, skill_name=skill_name,
                    skill_reason=skill_reason, skill_method=skill_method,
                    skill_wait_for_end=skill_wait_for_end,
                    skill_actor=skill_actor,
                    verified_skill_end_owner=verified_skill_end_owner,
                    mitigation_before=mitigation_before, mitigation_after=mitigation_after,
                    settled_after_nested_kill=settled_after_nested_kill)
            if (self.statistics.settlement_enabled and settlement_identity and not ignore_passive_native_cast
                    and kind in ('skill_start', 'skill_end') and settlement_identity[0] == 'local'
                    and settlement_identity[2] is not None):
                _state, owner, actor = settlement_identity
                self.statistics.record_settlement_skill(
                    owner, actor, source, kind, game_time, key=skill_key, skill_id=skill_id,
                    name=skill_name, method=skill_method or target_instance, reason=skill_reason,
                    wait_for_end=skill_wait_for_end)
        except (ValueError, TypeError, OverflowError) as exc:
            # Supplemental evidence can fail without losing ordered damage.
            if kind == 'enemy_enter':
                self.statistics.mark_enemy_entry_unknown()
                self.statistics.mark_enemy_state_unknown()
            elif kind == 'enemy_reborn':
                self.statistics.mark_enemy_phase_unknown()
                self.statistics.mark_enemy_state_unknown()
            elif kind == 'enemy_damage':
                self.statistics.observe_enemy_damage(complete=False)
            elif self.statistics.settlement_enabled and kind in ('damage', 'elemental_burst', 'elemental_bar'):
                if isinstance(source, str) and source:
                    self.statistics.mark_overkill_unknown(source)
                if kind == 'elemental_bar':
                    self.statistics.record_settlement_bar(target_uid, target, target_instance, 'unknown', None, None)
            elif kind in ('skill_start', 'skill_end') and (self.statistics.settlement_enabled or native_cast):
                if native_cast:
                    self._observe_unresolved_source(source, unresolved_source_keys,
                        kind, amount, target_instance, damage_type=damage_type)
                if self.statistics.settlement_enabled:
                    self._mark_settlement_skill_unassigned(source, unresolved_source_keys,
                        skill_method or target_instance, skill_reason)
            elif self.statistics.settlement_enabled and kind == 'damage_settlement':
                self.statistics.settlement_stream_incomplete = True
                self.statistics.observe_enemy_damage(complete=False)
            elif kind != 'hp_damage':
                self._fail(f'event is invalid or cannot be attributed: {exc}')
        else:
            if self.statistics.settlement_enabled and settlement_identity:
                state, owner, actor = settlement_identity
                if kind == 'elemental_bar':
                    self.statistics.record_settlement_bar(target_uid, target, target_instance, state, owner, actor)
                elif (kind in ('skill_start', 'skill_end') and state == 'unknown'
                      and not ignore_passive_native_cast):
                    self._mark_settlement_skill_unassigned(source, unresolved_source_keys,
                        skill_method or target_instance, skill_reason)
            if self.statistics.settlement_enabled and kind in ('enemy_enter', 'enemy_fake_death',
                                                               'enemy_reborn', 'enemy_finish'):
                self.statistics.clear_settlement_bar(target_instance, kind)
            if kind in ENEMY_STATE_KINDS:
                try:
                    self.statistics.record_enemy_state(
                        sequence, kind, frame, game_time, source, amount, target, target_instance,
                        hp_before=hp_before, hp_after=hp_after, phase_total_hp=phase_total_hp,
                        phase_count=phase_count, phase_from_birth=phase_from_birth,
                        finish_reason=finish_reason, enemy_max_hp=enemy_max_hp)
                except (ValueError, TypeError, OverflowError):
                    self.statistics.mark_enemy_state_unknown()
        self._record_receipt(sequence, frame, game_time, payload, replay_record)

    def watermark(self, *, session, sequence, frame, game_time):
        if self.closed:
            raise StreamGap('event stream is closed')
        if (session != self.session or not self._nonnegative_int(sequence)
                or sequence != self.sequence or not self._nonnegative_int(frame)
                or frame < self.frame
                or not isinstance(game_time, (int, float)) or isinstance(game_time, bool)
                or not isfinite(game_time) or not self.game_time <= game_time <= self.statistics.now
                or (frame == self.frame and game_time != self.game_time)):
            self._fail('watermark does not match the delivered stream')
        self.frame = frame
        self.game_time = game_time
        self.statistics.mark_boss_watermark(game_time)
