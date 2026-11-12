"""Validate an ordered event stream before submitting it to Statistics.

The producer must provide a barrier taken after the complete baseline and a
monotonic sequence shared by all emitted events and watermarks. A watermark is
valid only after all events through its sequence have been delivered. This
receiver cannot establish those producer guarantees by itself.
"""
from math import isfinite

from stats_core import ENEMY_STATE_KINDS, Statistics


class StreamGap(ValueError):
    """The stream can no longer prove continuous event coverage."""


class BarrierBusy(StreamGap):
    """Valid producer marks surround a changing frame; retry without draining."""


def baseline_barrier(before, sample, after):
    """Validate two producer marks surrounding one frame-guarded baseline.

    The producer must increment epoch on *entry* to every relevant callback,
    track callbacks until return, and return marks in the same serialization
    domain as those callbacks. Equal sequence alone does not prove quiescence.
    """
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
        if (any(before[k] != after[k] for k in ('sequence', 'epoch', 'frame', 'game_time'))
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

    def accept(self, verified_skill_end_owner=None, /, *, session, sequence, frame,
               game_time, source, kind, amount,
               target=None, real_delta=None, hp_before=None, hp_after=None,
               killed=None, target_type=None, damage_type=None,
               cancel_reason=None, shield_absorbed=None, target_instance=None,
               target_uid=None, enemy_damage_owner=None,
               skill_key=None, skill_id=None, skill_name=None, skill_reason=None,
               skill_method=None,
               phase_total_hp=None, phase_count=None, phase_from_birth=None,
               enemy_entry_attributes=None, finish_reason=None, enemy_max_hp=None,
               mitigation_before=None, mitigation_after=None):
        if self.closed:
            raise StreamGap('event stream is closed')
        payload = (session, frame, game_time, source, kind, amount,
                   target, real_delta, hp_before, hp_after, killed,
                   target_type, damage_type, cancel_reason, shield_absorbed,
                   target_instance, target_uid, enemy_damage_owner,
                   skill_key, skill_id, skill_name, skill_reason, skill_method,
                   phase_total_hp, phase_count, phase_from_birth, enemy_entry_attributes,
                   finish_reason, enemy_max_hp, mitigation_before, mitigation_after)
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
        try:
            if kind == 'enemy_enter':
                self.statistics.record_enemy_entry(
                    frame, game_time, source, amount, target, target_instance,
                    enemy_entry_attributes)
            elif kind == 'enemy_reborn':
                self.statistics.record_enemy_reborn(
                    frame, game_time, source, amount, target, target_instance,
                    hp_before, hp_after, phase_total_hp, phase_count, phase_from_birth)
            elif kind == 'enemy_damage':
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
                    verified_skill_end_owner=verified_skill_end_owner,
                    mitigation_before=mitigation_before, mitigation_after=mitigation_after)
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
            elif kind != 'hp_damage':
                self._fail(f'event is invalid or cannot be attributed: {exc}')
        else:
            if kind in ENEMY_STATE_KINDS:
                try:
                    self.statistics.record_enemy_state(
                        sequence, kind, frame, game_time, source, amount, target, target_instance,
                        hp_before=hp_before, hp_after=hp_after, phase_total_hp=phase_total_hp,
                        phase_count=phase_count, phase_from_birth=phase_from_birth,
                        finish_reason=finish_reason, enemy_max_hp=enemy_max_hp)
                except (ValueError, TypeError, OverflowError):
                    self.statistics.mark_enemy_state_unknown()
        self.sequence = sequence
        self.frame = frame
        self.game_time = game_time
        self.received[sequence] = payload

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
