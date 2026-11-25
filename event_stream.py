"""Validate an ordered event stream before submitting it to Statistics.

The producer must provide a barrier taken after the complete baseline and a
monotonic sequence shared by all emitted events and watermarks. A watermark is
valid only after all events through its sequence have been delivered. This
receiver cannot establish those producer guarantees by itself.
"""
from copy import deepcopy
from math import isfinite

from stats_core import ENEMY_STATE_KINDS, Statistics
from deployment_lifecycle import lifecycle_proof
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
            changed = [key for key in ('sequence', 'epoch', 'frame', 'game_time') if before[key] != after[key]]
            if shared_scope and before['damage_settlement_scope'] != after['damage_settlement_scope']:
                changed.append('damage_settlement_scope')
            changed.extend('snapshot_' + key for key in ('frame', 'game_time') if sample[key] != after[key])
            raise BarrierBusy('baseline crossed an event or game frame (' + ', '.join(changed) + ')')
    except (KeyError, TypeError):
        raise StreamGap('incomplete producer mark or baseline') from None
    return after['sequence']


class EventStream:
    def __init__(self, statistics: Statistics, *, session, baseline_frame,
                 baseline_sequence, retain_owned_replay_records=False):
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
        # The scoped multiplayer receiver already detached each raw record
        # from the transport. Its private receipt stores only read that copy.
        self._retain_owned_replay_records = retain_owned_replay_records
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
        if replay_record is not None and not self._retain_owned_replay_records:
            payload = deepcopy(replay_record)
        self.received[sequence] = payload

    def _observe_unresolved_source(self, source, source_keys, kind, amount, target_instance, *, damage_type):
        for key in source_keys or (source,):
            self.statistics.observe_unresolved_source(kind, key, amount, target_instance,
                                                      damage_type=damage_type)

    def _mark_settlement_skill_unassigned(self, source, source_keys, method, reason):
        for key in source_keys or (source,):
            self.statistics.mark_settlement_skill_unassigned(key, method, reason)

    def accept(self, verified_skill_end_owner=None, unresolved_source_keys=(), skip_scope=False, world_only=False, verified_body_source=False, /, *, session, sequence, frame,
               game_time, source, kind, amount,
               target=None, real_delta=None, hp_before=None, hp_after=None,
               killed=None, target_type=None, damage_type=None,
               cancel_reason=None, shield_absorbed=None, target_instance=None,
               target_uid=None, enemy_damage_owner=None,
               skill_key=None, skill_id=None, skill_name=None, skill_reason=None,
               skill_method=None, skill_wait_for_end=None, source_instance_uid=None, actor_lifecycle=None, skill_cast_invocation=None,
               phase_total_hp=None, phase_count=None, phase_from_birth=None,
               enemy_entry_attributes=None, finish_reason=None, enemy_max_hp=None, enemy_regen_ticks=None,
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
                       finish_reason, enemy_max_hp, enemy_regen_ticks, mitigation_before, mitigation_after,
                       source_character_uid, unresolved_source, settled_after_nested_kill,
                       settlement_complete, nominal_amount, settlement_identity, source_instance_uid, actor_lifecycle, skill_cast_invocation)
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
        if type(skip_scope) is not bool:
            self._fail('invalid consumer scope decision')
        if type(world_only) is not bool:
            self._fail('invalid world reducer decision')
        if type(verified_body_source) is not bool:
            self._fail('invalid consumer body-source decision')
        if skip_scope:
            # This record belongs to another native phase/round, or its scope
            # cannot be proved. Consume its protocol receipt without letting
            # old elemental bars, skills or amounts touch this scoped core.
            # Positional-only: a producer field cannot select this path.
            self._record_receipt(sequence, frame, game_time, payload, replay_record)
            return
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
        ignore_unmatched_native_cast = False
        native_cast = (skill_method or target_instance) == 'BasicSkill.OnCastFinish'
        skill_actor = (settlement_identity[2] if settlement_identity and settlement_identity[0] == 'local'
                       else source_character_uid if not self.statistics.settlement_enabled else None)
        retired_skill = (kind in ('skill_start', 'skill_end') and settlement_identity
            and settlement_identity[0] == 'local' and self.statistics.deployment_lifecycle.is_retired(
                settlement_identity[1], settlement_identity[2], source_instance_uid))
        try:
            if skill_cast_invocation is not None and (type(skill_cast_invocation) is not int
                    or not 0 < skill_cast_invocation <= 9007199254740991
                    or type(source_instance_uid) is not int or source_instance_uid <= 0
                    or (skill_method or target_instance) not in ('BasicSkill.OnCastFinish', 'Character.OnAfterSkill', 'Character.OnSkillFinish', 'Character.SwitchOutFromSkillState')):
                raise ValueError('invalid native skill invocation')
            if native_cast and not retired_skill:
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
                    if matches and source_instance_uid is not None:
                        matches = all(window.get('deployment') in (None, source_instance_uid) for window in (
                            self.statistics.skill_active.get(owner),
                            self.statistics.settlement_skill_active.get((owner, actor))))
                    effect_ended = (owner, actor, source_instance_uid, skill_cast_invocation, frame, game_time) in self.statistics.completed_skill_cast_invocations
                    if not matches and effect_ended:
                        ignore_unmatched_native_cast = True
                    if not matches and not effect_ended:
                        identity = (owner, actor)
                        before_observed_activation = (
                            state == 'local' and identity in self.statistics.settlement_skill_initial_pending
                            and identity not in self.statistics.settlement_skill_active)
                        typed_unmatched = (state == 'local' and type(actor) is int and actor > 0
                                           and type(source_instance_uid) is int and source_instance_uid > 0)
                        if typed_unmatched and skill_wait_for_end is True:
                            self.statistics.settlement_skill_deployment_unknown.add((owner, actor, source_instance_uid))
                            current = self.statistics.settlement_skill_active.get((owner, actor))
                            other_body = current is not None and current.get('deployment') not in (None, source_instance_uid)
                            if not before_observed_activation and not other_body:
                                self.statistics.mark_settlement_skill_cast_unmatched(owner, actor)
                        if skill_wait_for_end is False or before_observed_activation or typed_unmatched:
                            ignore_unmatched_native_cast = True
                        else:
                            raise ValueError('native cast state has no matching actor activation')
            terminal = lifecycle_proof(actor_lifecycle, kind=kind, actor=skill_actor,
                instance=source_instance_uid, frame=frame, game_time=game_time) == 'finish'
            terminal = terminal and actor_lifecycle['reason'] == skill_reason
            if self.statistics.settlement_enabled and settlement_identity and settlement_identity[0] == 'local':
                if kind in ('damage_settlement', 'healing') and verified_body_source:
                    self.statistics.deployment_lifecycle.note_body_output(settlement_identity[1], settlement_identity[2],
                        source_instance_uid, game_time)
                self.statistics.deployment_lifecycle.observe(settlement_identity[1], settlement_identity[2],
                    source_instance_uid, kind, game_time,
                    actor_lifecycle if kind != 'skill_end' or isinstance(actor_lifecycle, dict)
                        and actor_lifecycle.get('reason') == skill_reason else None, frame=frame,
                    expected_finish=kind == 'skill_end' and (skill_method or target_instance) == 'BattleController.OnCharacterFinished')
            if retired_skill:
                pass
            elif kind == 'character_born':
                pass
            elif self.statistics.settlement_enabled and kind == 'damage_settlement':
                state, owner, actor = settlement_identity or ('unknown', None, None)
                self.statistics.record_damage_settlement(
                    game_time, state, owner, actor, source, target, target_uid, target_instance, amount, shield_absorbed,
                    settlement_complete, damage_type, nominal_amount, mitigation_before, mitigation_after,
                    killed=killed, settled_after_nested_kill=settled_after_nested_kill,
                    world_only=world_only)
            elif world_only and kind == 'elemental_bar':
                # The ordinary settlement-bar reducer below owns the witness;
                # a shared world has no actor engagement or legacy damage row.
                pass
            elif settlement_identity == ('summon', None, None):
                # A proven summon skill never opens a host skill window.
                pass
            elif ignore_unmatched_native_cast:
                # A passive return or a return from an unobserved initial
                # activation leaves the current actor's skill state unchanged.
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
                    skill_actor=skill_actor, skill_deployment=source_instance_uid, skill_terminal=terminal,
                    skill_cast_invocation=skill_cast_invocation,
                    skill_frame=frame if kind in ('skill_start', 'skill_end') else None,
                    verified_skill_end_owner=verified_skill_end_owner,
                    mitigation_before=mitigation_before, mitigation_after=mitigation_after,
                    settled_after_nested_kill=settled_after_nested_kill)
            if (self.statistics.settlement_enabled and settlement_identity and not ignore_unmatched_native_cast and not retired_skill
                    and kind in ('skill_start', 'skill_end') and settlement_identity[0] == 'local'
                    and settlement_identity[2] is not None):
                _state, owner, actor = settlement_identity
                self.statistics.record_settlement_skill(
                    owner, actor, source, kind, game_time, key=skill_key, skill_id=skill_id,
                    name=skill_name, method=skill_method or target_instance, reason=skill_reason,
                    wait_for_end=skill_wait_for_end, frame=frame, deployment=source_instance_uid, terminal=terminal, invocation=skill_cast_invocation)
                if (skill_cast_invocation is not None and (skill_method or target_instance) in
                        ('Character.OnSkillFinish', 'Character.SwitchOutFromSkillState')):
                    self.statistics.completed_skill_cast_invocations.add((owner, actor, source_instance_uid,
                        skill_cast_invocation, frame, game_time))
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
                typed_actor = (settlement_identity and settlement_identity[0] == 'local'
                    and type(settlement_identity[2]) is int and settlement_identity[2] > 0
                    and type(source_instance_uid) is int and source_instance_uid > 0)
                if typed_actor:
                    self.statistics.mark_settlement_skill_cast_unmatched(settlement_identity[1], settlement_identity[2])
                else:
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
                      and not ignore_unmatched_native_cast):
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
                        finish_reason=finish_reason, enemy_max_hp=enemy_max_hp,
                        enemy_regen_ticks=enemy_regen_ticks)
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
        self.statistics.lifecycle_watermark = game_time
