"""Offline statistics core. Times are game seconds; inputs must be reliable.

Snapshots own native damage/healing totals and sampled recent rates. Event callbacks
own observations, engagement rates, shield absorption, overkill and formula damage
pairs. Observed healing and its rate do not augment the native counters.
Overkill is a subset of the already inclusive
native damage total and is never subtracted from it. Source keys identify
distinct client statistic entries, not transient entities.
"""
from collections import deque
from math import isclose, isfinite

from boss_catalog import classify_boss
from damage_settlement import DamageSettlement, observed_amount, extend_activity
from deployment_lifecycle import DeploymentLifecycle
from source_classification import classify_unmapped_source

# Native DamageType values used by the verified 2.7.91 event probe.
# 0=NONE, 4=HEAL and unknown values cannot be attributed to a damage segment.
NATIVE_DAMAGE_TYPES = {1: 'physical', 2: 'arts', 3: 'true', 5: 'elemental'}
DAMAGE_TYPE_KEYS = ('physical', 'arts', 'elemental', 'true')
DAMAGE_SCOPE_MISMATCH = '伤害事件与原生累计的统计范围不一致'
PRE_OBSERVER_DAMAGE = '观察者就绪前已有伤害'
HEALING_RECENT_SECONDS = 5
HEALING_RECENT_LIMIT = 1024
ENEMY_STATE_KINDS = frozenset({
    'enemy_enter', 'enemy_hp', 'enemy_fake_death', 'enemy_reborn', 'enemy_finish',
})


def _unverified_share(verified, total):
    if total is None or total <= 0 or not isfinite(verified) or not isfinite(total):
        return None
    if isclose(verified, total, rel_tol=1e-6, abs_tol=0.01):
        return 0.0
    return (total - verified) / total if 0 <= verified < total else None


def exceeds_native_damage(observed, native):
    # Native counters accumulate in float32, while event amounts use fixed
    # point. A few rounding units at original counter scale are expected.
    return observed > native and not isclose(observed, native, rel_tol=1e-5, abs_tol=0.01)


class Statistics:
    def __init__(self, session, start_time, *, event_coverage=False):
        self._time(start_time)
        self.session = session
        self.start_time = start_time
        self.now = start_time
        self.end_time = None
        self.event_coverage = event_coverage
        self.event_coverage_start = start_time
        self.event_coverage_end = None
        self.event_coverage_broken = False
        self.event_watermark = None
        self.baseline_ready = False
        self.initial_damage = {}
        self.owners = {}
        self.sources = {}
        self.events = {}
        self.last_event_time = start_time
        self.engagement = {}
        self.shields = {}
        # Per-owner overkill is only published for a continuously covered event
        # stream in which every damage event carried verifiable target HP and
        # settlement evidence. Any unverifiable damage event makes it unknown.
        self.overkill_incomplete = set()
        # Unit.OnTakeDamage is an incoming value before later settlement.
        # Track sources that only have incoming evidence without calling it
        # a confirmed settlement or treating the missing output as zero.
        self.incoming_damage_sources = set()
        self.settled_damage_sources = set()
        self.overkill_reason = {}
        # Match formula totals per source against its own cumulative counter
        # before aggregating a character and its summons.
        # A sourceless burst is credited to an owner as a supplement to the
        # native source counters. Its formula pair follows the same rule.
        self.mitigation_incomplete = set()
        self.mitigation_probe_incomplete = False
        self.mitigation_unattributed_burst = False
        # Elemental bar damage is not HP damage; only its source matters.
        self.burst_unknown = False
        self.burst_unknown_reason = None
        # Per-type overkill is a separate subset. It stays unknown if a lethal
        # event has overkill but no recognised native damage type.
        self.overkill_type_incomplete = set()
        self.unknown_overkill_sources = set()
        self.all_overkill_unknown = False
        # A dropped damage/heal event makes the engagement numerator short.
        # That gap is reported per owner instead of silently lowering the rate.
        self.engagement_incomplete = set()
        self.unknown_engagement_sources = set()
        self.all_engagement_unknown = False
        self.healing_observation_gaps = {}
        # Shield-only uncertainty must not close damage/overkill coverage.
        self.shield_unknown = False
        # Active-skill windows and the observed damage split. A skill event is
        # supplemental; an unmapped source or a missing hook makes the split
        # unknown rather than closing the authoritative damage stream.
        self.deployment_lifecycle = DeploymentLifecycle()
        self.settlement_skill_deployment_unknown = set()
        self.completed_skill_cast_invocations = set()
        self.lifecycle_watermark = start_time
        self.skill_windows = {}
        self.skill_seen = set()
        self.skill_unknown = False
        self.skill_history_issues = {}
        self._skill_observation_uncertainty = {}
        self._pending_skill_observation_uncertainty = {}
        self._skill_unresolved_owners = set()
        self.unresolved_skill_sources = set()
        self.enemy_entry_start_frame = None
        self.enemy_entry_incomplete = False
        self.enemy_entries = {}
        self.enemy_phase_incomplete = False
        self.enemy_phases = {}
        self.enemy_state_start_frame = None
        self.enemy_state_incomplete = False
        self.enemy_state_events = []
        self.enemy_state_identities = {}
        self.enemy_damage = None
        self.enemy_damage_incomplete = False
        # Per-event overkill is split by the same skill window as its damage.
        self.boss_damage = None
        self.boss_event_end = None
        self.boss_observed_from = None
        self.boss_target_unknown = set()
        self.boss_damage_incomplete = False
        self.partial_damage_enabled = False
        # Legacy captures replay with the policy that produced their recorded
        # values. Current observers declare the stricter settlement policy.
        self.reconcile_damage_scope = False
        self.settlement_enabled = False
        self.damage_settlement_version = 0
        self.settlement_sides_known = False
        self.settlement_stream_incomplete = False
        self.settlement_source_incomplete = set()
        self.settled = {}
        self.settled_boss = {}
        self.settlement_missing = {}
        self.settlement_boss_missing = {}
        self.settlement_bars = {}
        self.settlement_bar_origins = {}
        self.settlement_supplements = {}
        self.settlement_unassigned = {'hits': 0, 'hp': 0.0, 'shield': 0.0}
        self.settlement_boss_unassigned = 0
        self.settlement_unscoped_hits = 0
        self.settlement_unscoped = False
        self.settlement_skill_active = {}
        self.settlement_skill_initial_pending = set()
        self.settlement_skill_incomplete = set()
        self.settlement_skill_scope_unassigned = set()
        self.settlement_skill_past_incomplete = set()
        self.settlement_skill_unassigned = set()
        self.partial_watermark = None
        self.partial_unquantified_damage = set()
        self.partial_unattributed_damage = False

    def observe_boss_damage(self, *, complete):
        if self.boss_damage is None:
            self.boss_damage = {}
        self.boss_damage_incomplete |= not complete

    def _record_boss_damage(self, source, owner, when, target, amount, damage_type,
                            overkill, reason=None, before=None, after=None, *, actual=None):
        if isinstance(target, str) and target.startswith(('char_', 'token_')):
            return
        if self.settlement_enabled:
            return
        kind = NATIVE_DAMAGE_TYPES.get(damage_type) if type(damage_type) is int else None
        skill = any(candidate == owner for candidate, _ in self.settlement_skill_active) if self._skill_hit_observable(owner) else None
        effective = max(0.0, amount - overkill) if overkill is not None else None
        pair = self._mitigation_pair(amount, before, after)
        args = (when, actual or 0, None, kind, False, skill, amount,
                before if pair else None, after if pair else None)
        # Old APIs carry an independently verified effective amount, but do not
        # prove a modern target UID or its HP/shield decomposition.
        self.settled.setdefault(owner, DamageSettlement()).hit(*args,
            overkill=overkill, known_effective=effective, supplement=source is None,
            evidence_reason=reason, source=source)
        if self.boss_damage is None:
            return
        is_boss = classify_boss(target)
        if is_boss is None and amount > 0:
            self.boss_target_unknown.add(owner)
        if is_boss:
            self.settled_boss.setdefault(owner, DamageSettlement()).hit(*args,
                overkill=overkill, known_effective=effective, supplement=source is None,
                evidence_reason=reason, source=source)

    def _skill_hit_observable(self, owner):
        windows = [window for (candidate, _), window in self.settlement_skill_active.items() if candidate == owner]
        if windows:
            return all(self._unconfirmed_skill_reason(window, live=True) is None for window in windows)
        return not self.skill_unknown and owner not in self.skill_incomplete

    @staticmethod
    def _time(value):
        if not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError('invalid game time')

    @staticmethod
    def _amount(value):
        if not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError('invalid amount')

    def register_source(self, source, owner):
        if not source or not owner:
            raise ValueError('source and owner are required')
        if source in self.owners and self.owners[source] != owner:
            raise ValueError('source ownership cannot change within a session')
        self.owners[source] = owner
        if source in self.unknown_overkill_sources:
            self.overkill_incomplete.add(owner)
        if source in self.unknown_engagement_sources:
            self.engagement_incomplete.add(owner)
        if source in self.unresolved_skill_sources:
            self._mark_skill_owner_unresolved(owner)

    def mark_skill_unknown(self):
        self.skill_unknown = True

    def _mark_skill_observation_uncertain(self, owner, actor=None):
        revisions = self._skill_observation_uncertainty.setdefault(owner, {})
        revisions[actor] = revisions.get(actor, 0) + 1

    def _mark_skill_owner_unresolved(self, owner):
        if owner not in self._skill_unresolved_owners:
            self._skill_unresolved_owners.add(owner)
            self._mark_skill_observation_uncertain(owner)
        self.skill_history_issues[(owner, None)] = '技能事件的角色归属未确认'

    def skill_observation_revision(self, owner, actor):
        """Detect intervening uncertainty even if an actor's current state recovered."""
        revisions = self._skill_observation_uncertainty.get(owner, {})
        return (self.skill_unknown or self.event_coverage_broken,
                revisions.get(None, 0), revisions.get(actor, 0))

    def observe_enemy_entry(self, start_frame, *, complete):
        if self.enemy_entry_start_frame is None:
            self.enemy_entry_start_frame = start_frame
        if not complete:
            self.mark_enemy_entry_unknown()

    def mark_enemy_entry_unknown(self):
        self.enemy_entry_incomplete = True

    def observe_enemy_state(self, start_frame, *, complete):
        if self.enemy_state_start_frame is None:
            self.enemy_state_start_frame = start_frame
        if not complete:
            self.mark_enemy_state_unknown()

    def mark_enemy_state_unknown(self):
        self.enemy_state_incomplete = True

    def observe_enemy_damage(self, *, complete):
        if self.enemy_damage is None:
            self.enemy_damage = {}
        self.enemy_damage_incomplete |= not complete

    def record_enemy_damage(self, source, owner, damage_type, amount, eid, uid, instance, *, settled=False):
        if self.enemy_damage is None:
            return
        if (not self._enemy_identity(eid, uid) or not self._optional_number(amount)
                or amount <= 0 or (not settled and (type(damage_type) is not int
                                                     or damage_type not in NATIVE_DAMAGE_TYPES))):
            self.enemy_damage_incomplete = True
            return
        if owner is None and source is None and damage_type == 5:
            owner = self.last_elemental.get(instance)
        if owner is None:
            if classify_unmapped_source(source) is None:
                self.enemy_damage_incomplete = True
            return
        existing = self.enemy_damage.setdefault(uid, {'eid': eid, 'amount': 0.0})
        if existing['eid'] != eid:
            self.enemy_damage_incomplete = True
            return
        existing['amount'] += amount

    @property
    def settlement_totals(self):
        return {source: amount for meter in self.settled.values()
                for source, amount in meter.source_nominal.items()}

    @property
    def mitigation(self):
        return {source: values for meter in self.settled.values()
                for source, values in meter.source_pairs.items() if source is not None}

    @property
    def sourceless_burst_mitigation(self):
        return {owner: meter.source_pairs[None] for owner, meter in self.settled.items()
                if None in meter.source_pairs}

    @property
    def partial_damage(self):
        return self.settled if not self.settlement_enabled else {}

    @property
    def skill_observed(self):
        return {owner: meter.raw_skill for owner, meter in self.settled.items()}

    @property
    def non_skill_observed(self):
        return {owner: meter.raw_non_skill for owner, meter in self.settled.items()}

    @property
    def skill_overkill(self):
        return {owner: meter.raw_skill_overkill for owner, meter in self.settled.items()}

    @property
    def non_skill_overkill(self):
        return {owner: meter.overkill - meter.raw_skill_overkill for owner, meter in self.settled.items()}

    @property
    def overkill(self):
        return {owner: meter.overkill for owner, meter in self.settled.items()}

    @property
    def overkill_by_type(self):
        return {owner: meter.overkill_by_type for owner, meter in self.settled.items()}

    @property
    def overkill_events(self):
        return [(hit[0], owner, hit[5]) for owner, meter in self.settled.items()
                for hit in meter.recent if hit[5] is not None and hit[5] > 0]

    @property
    def burst(self):
        return {owner: meter.elemental for owner, meter in self.settled.items()
                if meter.elemental is not None}

    @property
    def last_elemental(self):
        """Legacy observation projection of the sole target witness store."""
        return {bar[1]: bar[3] for bar in self.settlement_bars.values()
                if bar[2] == 'local' and bar[3] is not None}

    def _discard_elemental_instance(self, instance, *, legacy=False):
        # A legacy output callback previews the same hit before its complete
        # settlement arrives. Only that settlement consumes modern witnesses.
        if legacy and self.settlement_enabled:
            return
        for uid, bar in tuple(self.settlement_bars.items()):
            if instance is None or bar[1] == instance:
                self.discard_settlement_bar(uid)

    def record_settlement_bar(self, target_uid, target, instance, state, owner, actor, *, sequence=None, frame=None, game_time=None, birth=None):
        if (not self._enemy_identity(target if target is not None else 'elemental_bar', target_uid)
                or not isinstance(instance, str) or not instance):
            if isinstance(instance, str) and instance:
                self.settlement_bars = {uid: bar for uid, bar in self.settlement_bars.items()
                                        if bar[1] != instance}
                self.settlement_bar_origins = {uid: origin for uid, origin in self.settlement_bar_origins.items()
                                               if uid in self.settlement_bars}
            else:
                self.settlement_bars.clear()
                self.settlement_bar_origins.clear()
            return
        self.settlement_bars[target_uid] = (target, instance, state, owner, actor)
        self.settlement_bar_origins[target_uid] = (sequence, frame, game_time, birth)

    def discard_settlement_bar(self, target_uid):
        self.settlement_bar_origins.pop(target_uid, None)
        return self.settlement_bars.pop(target_uid, None)

    def clear_settlement_bar(self, target_uid, kind, *, birth_proof=None):
        # Death can be reported before the damage callback returns. Retain
        # attribution until its burst consumes it, or the UID is admitted
        # again by entry or rebirth. A delayed first admission can preserve
        # only a bar proved to have followed this exact native Born boundary.
        if kind in ('enemy_enter', 'enemy_reborn') and isinstance(target_uid, str):
            bar = self.settlement_bars.get(target_uid)
            origin = self.settlement_bar_origins.get(target_uid)
            if (kind == 'enemy_enter' and isinstance(birth_proof, dict) and bar and origin
                    and self.event_coverage and not self.event_coverage_broken
                    and birth_proof.get('target_uid') == target_uid
                    and birth_proof.get('target_instance') == bar[1]
                    and bar[0] in (None, birth_proof.get('target'))
                    and type(origin[0]) is int and type(origin[1]) is int and type(origin[2]) in (int, float)
                    and isinstance(origin[3], dict) and origin[3].get('version') == 1
                    and type(birth_proof.get('sequence_after_born')) is int
                    and type(birth_proof.get('epoch_after_born')) is int
                    and origin[3].get('sequence_after_born') == birth_proof['sequence_after_born']
                    and origin[3].get('epoch_after_born') == birth_proof['epoch_after_born']
                    and type(origin[3].get('modifier_enter_epoch')) is int
                    and origin[3]['modifier_enter_epoch'] > birth_proof['epoch_after_born']
                    and type(birth_proof.get('entry_sequence')) is int
                    and birth_proof['sequence_after_born'] < origin[0] < birth_proof['entry_sequence']
                    and type(birth_proof.get('frame')) is int and birth_proof['frame'] <= origin[1]
                    and type(birth_proof.get('game_time')) in (int, float) and self.event_coverage_start <= birth_proof['game_time'] <= origin[2]):
                return
            self.discard_settlement_bar(target_uid)

    def record_damage_settlement(self, when, state, owner, actor, source, target, target_uid, instance,
                                 hp, shield, complete, kind, nominal, before, after, *, killed=None,
                                 settled_after_nested_kill=False, world_only=False):
        """Record each confirmed component, preserving independently verified neighbors."""
        valid_target = self._enemy_identity(target, target_uid) and target.startswith(('enemy_', 'trap_'))
        previous = self.enemy_damage.get(target_uid) if valid_target and self.enemy_damage is not None else None
        if previous and previous['eid'] != target:
            valid_target = False
        element = type(kind) is int and kind == 5
        if source is None and element and valid_target:
            bar = self.discard_settlement_bar(target_uid)
            if bar and bar[0] in (None, target) and bar[1] == instance:
                _, _, state, owner, actor = bar
        if state == 'foreign' or isinstance(target, str) and target.startswith(('char_', 'token_')):
            return
        if source is not None and classify_unmapped_source(source) is not None:
            return
        hp_valid = observed_amount(hp)
        shield_valid = observed_amount(shield)
        hp = hp if hp_valid else 0.0
        shield = shield if shield_valid else None
        kind_name = NATIVE_DAMAGE_TYPES.get(kind) if type(kind) is int else None
        nominal_valid = observed_amount(nominal) and nominal + .01 >= hp + (shield or 0.0)
        if not nominal_valid:
            nominal = None
        complete = (complete is True and hp_valid and shield_valid and valid_target and kind_name is not None)
        nested = settled_after_nested_kill is True and hp == 0 and shield == 0 and killed is not False
        overkill = None
        if self.damage_settlement_version >= 7 and complete:
            # A positive difference can also be a cap on a living target.
            # Only a proven lethal hit or a verified nested kill leaves excess.
            if killed is False:
                overkill = 0.0
            elif nominal is not None:
                residual = max(0.0, nominal - (hp + shield))
                if nested or (killed is True and hp > 0):
                    overkill = residual
                elif residual <= 0.01:
                    overkill = 0.0
        if state != 'local' or owner is None or not valid_target:
            if world_only:
                self.observe_enemy_damage(complete=False)
                return
            if hp > 0 or shield or not complete or (self.damage_settlement_version >= 7 and nominal):
                self.settlement_unassigned['hits'] += 1
                self.settlement_unassigned['hp'] += hp
                self.settlement_unassigned['shield'] += shield or 0.0
                self.observe_enemy_damage(complete=False)
                if owner is not None:
                    self.settlement_missing[owner] = self.settlement_missing.get(owner, 0) + 1
                else:
                    self.settlement_unscoped = True
                    self.settlement_unscoped_hits += 1
                if self.damage_settlement_version >= 6 and (not valid_target or classify_boss(target) is not False):
                    self.settlement_boss_unassigned += 1
                    if owner is not None:
                        self.settlement_boss_missing[owner] = self.settlement_boss_missing.get(owner, 0) + 1
            return
        if hp > 0:
            self.record_enemy_damage(source, owner, kind, hp, target, target_uid, None, settled=True)
        if world_only:
            # The shared native world owns target HP facts, not player meters.
            # All identity, HP and elemental-witness validation above is shared.
            if not complete:
                self.observe_enemy_damage(complete=False)
            if source is not None and element:
                self.discard_settlement_bar(target_uid)
            return
        if nominal is None or not self._mitigation_pair(nominal, before, after):
            before = after = None
        if source is None and element and nominal is not None:
            self.settlement_supplements[owner] = self.settlement_supplements.get(owner, 0.0) + nominal
            if complete and self.damage_settlement_version < 7:
                if self.damage_settlement_version < 5 or killed is True:
                    overkill = max(0.0, nominal - hp - shield)
                elif killed is False:
                    overkill = 0.0
        if element and (not complete or nominal is None or overkill is None):
            self.burst_unknown = True
            self.burst_unknown_reason = '元素爆发缺少同一次结算的完整证据'
        skill = self._settlement_skill(owner, actor)
        initial_unknown = skill is None and (owner, actor) in self.settlement_skill_initial_pending
        args = (when, hp, shield, kind_name, complete, skill, nominal, before, after)
        meter = self.settled.get(owner)
        if meter is None:
            meter = self.settled[owner] = DamageSettlement()
        meter.hit(*args, overkill=overkill, initial_skill_unknown=initial_unknown, source=source)
        boss = classify_boss(target)
        if boss is True:
            meter = self.settled_boss.get(owner)
            if meter is None:
                meter = self.settled_boss[owner] = DamageSettlement()
            meter.hit(*args, overkill=overkill, initial_skill_unknown=initial_unknown, source=source)
        elif boss is None:
            self.settlement_boss_missing[owner] = self.settlement_boss_missing.get(owner, 0) + 1
        if source is not None and element:
            self.discard_settlement_bar(target_uid)

    def _settlement_skill(self, owner, actor):
        identity = (owner, actor)
        if (actor is None or self.skill_unknown or owner in self.settlement_skill_unassigned
                or identity in self.settlement_skill_initial_pending
                or identity in self.settlement_skill_scope_unassigned
                or identity in self.settlement_skill_incomplete):
            return None
        window = self.settlement_skill_active.get(identity)
        if window is not None and self._unconfirmed_skill_reason(window, live=True):
            return None
        return window is not None

    def _settlement_row(self, owner, *, boss=False):
        meters = self.settled_boss if boss else self.settled
        meter = meters.get(owner) or DamageSettlement()
        end = self.partial_watermark if self.partial_watermark is not None else self.event_coverage_start
        stream_complete = (self.event_coverage and not self.event_coverage_broken
                           and end == self.now and not self.settlement_stream_incomplete
                           and owner not in self.settlement_source_incomplete
                           and (self.damage_settlement_version >= 6 or not self.settlement_unscoped)
                           and self.settlement_sides_known)
        missing = self.settlement_missing.get(owner, 0)
        if boss:
            missing = self.settlement_boss_missing.get(owner, 0) + (missing if self.damage_settlement_version < 6 else 0)
        row = meter.result(self.event_coverage_start, self.now, end, stream_complete=stream_complete,
                           missing_hits=missing, live=self.end_time is None, boss=boss,
                           include_overkill=self.damage_settlement_version >= 7)
        # Classification accepted while an effect was live remains an observed
        # component. A later missing endpoint cannot certify the whole window.
        skill_reason = self.skill_incomplete.get(owner)
        if skill_reason is None and (self.skill_unknown or owner in self.settlement_skill_past_incomplete
                                     or owner in self.settlement_skill_unassigned
                                     or any(identity[0] == owner for identity in self.settlement_skill_incomplete)
                                     or any(identity[0] == owner for identity in self.settlement_skill_scope_unassigned)):
            skill_reason = '技能事件覆盖不完整'
        if skill_reason is None:
            live = self.end_time is None and self.event_coverage_end is None
            for (candidate_owner, _actor), window in self.settlement_skill_active.items():
                if candidate_owner == owner:
                    # Existing ordinary effects can be clipped by a reliable
                    # battle boundary. New native wait evidence certifies only
                    # its open interval, until an actual end is observed.
                    window_live = live or not self._is_ammo_window(window)
                    skill_reason = self._unconfirmed_skill_reason(window, live=window_live)
                    if skill_reason is not None:
                        break
        if skill_reason is not None:
            row.update(skill_split_complete=False, skill_split_effective_complete=False,
                       skill_split_reason=skill_reason)
        if not self.settlement_sides_known:
            row['reason'] = '当前玩家阵营未确认'
            if 'overkill_unknown_reason' in row:
                row['overkill_unknown_reason'] = row['reason']
        return row

    @staticmethod
    def _enemy_identity(eid, uid):
        return (isinstance(eid, str) and bool(eid)
                and isinstance(uid, str) and uid.isascii()
                and uid.isdecimal() and len(uid) <= 10
                and int(uid) <= 0xffffffff and str(int(uid)) == uid)

    def record_enemy_entry(self, frame, game_time, source, amount, eid, instance_uid,
                           attributes=None):
        self._time(game_time)
        if (source is not None or amount != 0 or isinstance(amount, bool)
                or not self._enemy_identity(eid, instance_uid)
                or not self.event_coverage_start <= game_time <= self.now):
            raise ValueError('invalid enemy entry')
        if instance_uid in self.enemy_entries:
            # The event stream already handles retries of the same sequence.
            raise ValueError('enemy entry was emitted more than once')
        entry = dict(
            instance_uid=instance_uid, eid=eid, frame=frame, game_time=game_time)
        if isinstance(attributes, dict):
            evidence = {}
            for name in ('max_hp', 'atk', 'def', 'res', 'move_speed', 'attack_speed'):
                value = attributes.get(name)
                if (type(value) in (int, float) and -1e10 < value < 1e10
                        and (name != 'max_hp' or value > 0)):
                    evidence[name] = value
            if evidence:
                entry['enemy_entry_attributes'] = evidence
        self.enemy_entries[instance_uid] = entry

    def mark_enemy_phase_unknown(self):
        self.enemy_phase_incomplete = True

    def record_enemy_reborn(self, frame, game_time, source, amount, eid, instance_uid,
                            hp_before, hp_after, phase_total_hp, phase_count, phase_from_birth):
        values = (amount, hp_before, hp_after, phase_total_hp)
        if (source is not None or not self._enemy_identity(eid, instance_uid)
                or not self.event_coverage_start <= game_time <= self.now
                or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                           and isfinite(value) and value >= 0 for value in values)
                or amount <= 0
                or not isclose(hp_after, amount, rel_tol=1e-9, abs_tol=1e-6)
                or phase_total_hp < amount
                or type(phase_count) is not int or phase_count < 1
                or type(phase_from_birth) is not bool
                or phase_from_birth and (phase_count < 2 or phase_total_hp <= amount)):
            raise ValueError('invalid enemy phase')
        entry = self.enemy_entries.get(instance_uid)
        previous = self.enemy_phases.get(instance_uid)
        if ((entry is not None and entry['eid'] != eid)
                or (previous is not None and (
                    previous['eid'] != eid
                    or phase_count != previous['phase_count'] + 1
                    or phase_from_birth != previous['phase_from_birth']
                    or not isclose(previous['phase_total_hp'] + amount, phase_total_hp,
                                   rel_tol=1e-9, abs_tol=1e-6)))):
            raise ValueError('enemy phase progression conflicts')
        self.enemy_phases[instance_uid] = dict(
            eid=eid, frame=frame, game_time=game_time, phase_total_hp=phase_total_hp,
            phase_count=phase_count, phase_from_birth=phase_from_birth)

    def record_enemy_state(self, sequence, kind, frame, game_time, source, amount,
                           eid, instance_uid, *, hp_before=None, hp_after=None,
                           phase_total_hp=None, phase_count=None, phase_from_birth=None,
                           finish_reason=None, enemy_max_hp=None, enemy_regen_ticks=None):
        if self.enemy_state_start_frame is None:
            return
        if (kind not in ENEMY_STATE_KINDS
                or not self._enemy_identity(eid, instance_uid)
                or source is not None
                or (kind != 'enemy_reborn' and (amount != 0 or isinstance(amount, bool)))
                or not self.event_coverage_start <= game_time <= self.now
                or self.enemy_state_identities.get(instance_uid, eid) != eid):
            raise ValueError('invalid enemy state identity or amount')
        if (any(value is not None and (not self._optional_number(value) or value < 0)
                for value in (hp_before, hp_after))
                or (kind == 'enemy_hp' and hp_after is None)):
            raise ValueError('invalid enemy state HP')
        if enemy_max_hp is not None and (
                kind != 'enemy_hp' or not self._optional_number(enemy_max_hp)
                or enemy_max_hp <= 0):
            raise ValueError('invalid enemy max HP')
        if kind == 'enemy_finish':
            if (type(finish_reason) is not int or finish_reason == 0
                    or not -(1 << 31) <= finish_reason < (1 << 31)):
                raise ValueError('invalid enemy finish reason')
        elif finish_reason is not None:
            raise ValueError('only enemy finish carries a finish reason')

        event = dict(sequence=sequence, kind=kind, frame=frame, game_time=game_time,
                     eid=eid, instance_uid=instance_uid)
        if hp_before is not None:
            event['hp_before'] = hp_before
        if hp_after is not None:
            event['hp_after'] = hp_after
        if enemy_regen_ticks is not None:
            if (kind != 'enemy_hp' or eid not in ('enemy_1061_zomshd', 'enemy_1061_zomshd_2')
                    or type(enemy_regen_ticks) is not int or enemy_regen_ticks < 2
                    or hp_before is None or hp_before <= 0 or enemy_max_hp is None
                    or hp_after > enemy_max_hp or hp_after - hp_before != enemy_regen_ticks):
                raise ValueError('invalid shieldguard recovery ticks')
            event['enemy_regen_ticks'] = enemy_regen_ticks
        if enemy_max_hp is not None:
            event['enemy_max_hp'] = enemy_max_hp
        if kind == 'enemy_enter':
            attributes = self.enemy_entries[instance_uid].get('enemy_entry_attributes')
            if attributes:
                event['enemy_entry_attributes'] = dict(attributes)
        elif kind == 'enemy_reborn':
            event.update(phase_total_hp=phase_total_hp, phase_count=phase_count,
                         phase_from_birth=phase_from_birth)
        elif kind == 'enemy_finish':
            event['finish_reason'] = finish_reason
        self.enemy_state_identities[instance_uid] = eid
        self.enemy_state_events.append(event)

    def mark_overkill_unknown(self, source):
        """Record a probe-level event gap for one source without closing coverage."""
        if not source:
            return
        owner = self.owners.get(source)
        if owner is None:
            self.unknown_overkill_sources.add(source)
        else:
            self.overkill_incomplete.add(owner)

    def mark_all_overkill_unknown(self):
        """A damage event could not be attributed; keep other metrics but hide overkill."""
        self.all_overkill_unknown = True

    def mark_engagement_unknown(self, source):
        """A dropped damage/heal event leaves this source's engagement short."""
        if not source:
            return
        owner = self.owners.get(source)
        if owner is None:
            self.unknown_engagement_sources.add(source)
        else:
            self.engagement_incomplete.add(owner)

    def mark_all_engagement_unknown(self):
        """An unattributable damage event could belong to any owner's numerator."""
        self.all_engagement_unknown = True

    def mark_healing_observation_unknown(self, source=None, *, game_time=None):
        """A possible heal without scope/owner proof cannot certify HPS."""
        owner = self.owners.get(source)
        when = (game_time if observed_amount(game_time) and self.event_coverage_start <= game_time <= self.now
                else float('inf'))
        self.healing_observation_gaps[owner] = max(self.healing_observation_gaps.get(owner, when), when)

    def mark_shields_unknown(self):
        """Shield events were incomplete; keep damage, overkill and engagement."""
        self.shield_unknown = True

    def observe_unresolved_source(self, kind, source, amount, target_instance, *, damage_type):
        """Keep independently owned observations when a record has no confirmed owner."""
        self._amount(amount)
        if kind in ('damage', 'healing'):
            self.mark_engagement_unknown(source)
        if kind in ('damage', 'elemental_burst'):
            self.mark_overkill_unknown(source)
        if kind == 'shield_absorbed':
            self.mark_shields_unknown()
        if kind == 'enemy_damage':
            self.observe_enemy_damage(complete=False)
        if kind in ('skill_start', 'skill_end'):
            self.unresolved_skill_sources.add(source)
            owner = self.owners.get(source)
            if owner is not None:
                self._mark_skill_owner_unresolved(owner)
        if (kind in ('elemental_bar', 'elemental_burst')
                or kind == 'enemy_damage' and damage_type == 5) and target_instance:
            self._discard_elemental_instance(target_instance, legacy=True)

    def invalidate_source_total(self, source):
        state = self.sources.get(source)
        if state is not None:
            state['totals_valid'] = False
            state['totals_time'] = None
            state['continuous'] = False

    def advance(self, game_time):
        self._time(game_time)
        if self.end_time is not None or game_time < self.now:
            raise ValueError('clock cannot advance after end or move backwards')
        self.now = game_time

    def finish(self, game_time):
        self.advance(game_time)
        self.end_time = game_time
        self._close_skill_windows(game_time, battle_end=True)

    def invalidate_snapshots(self):
        """Mark a known collection gap; retain totals but stop certifying them.

        A gap is not a battle boundary. Retained cumulative values stay
        displayable, but they cannot be called totals-complete until a later
        complete snapshot re-establishes every source; rate windows restart
        from the first reliable snapshot after the gap.
        """
        for state in self.sources.values():
            state['continuous'] = False
            state['totals_valid'] = False
            state['totals_time'] = None

    def invalidate_rates(self):
        """Stop certifying rate windows without touching cumulative totals.

        Used at a battle boundary: the ended battle's cumulative counters stay
        authoritative while no rate can be observed past the endpoint.
        """
        for state in self.sources.values():
            state['continuous'] = False

    def seal_totals(self):
        """Accept every reliable cumulative value as final for this battle.

        A battle boundary is not a collection gap: a source that disappeared
        from the final snapshot (for example a summon that left) cannot add
        more output, so its last observed cumulative is final. Sources whose
        totals were invalidated by an unrecovered gap stay unknown.
        """
        for state in self.sources.values():
            if state['totals_valid']:
                state['totals_time'] = self.now

    def start_late_event_coverage(self):
        """Scope the first paired event barrier without resetting native counters."""
        self.event_coverage_start = self.last_event_time = self.now
        self.event_watermark = None
        self.initial_damage = {source: state['values']['damage'] for source, state in self.sources.items()}

    def invalidate_events(self, end_time=None, *, complete=False):
        """Close event coverage at the last reliable game time.

        ``complete`` is reserved for a confirmed battle boundary: the stream
        ended because the battle ended, not because evidence was lost. A clean
        close still certifies the covered interval; a broken close does not.
        """
        if self.event_coverage and self.event_coverage_end is None:
            end_time = self.now if end_time is None else end_time
            self._time(end_time)
            if not self.last_event_time <= end_time <= self.now:
                raise ValueError('invalid event coverage end')
            self.event_coverage_end = end_time
            if not complete:
                self.event_coverage_broken = True
            self._close_skill_windows(end_time, battle_end=complete)

    def mark_boss_watermark(self, game_time):
        self.event_watermark = game_time
        if self.partial_damage_enabled:
            self.partial_watermark = game_time
        if self.boss_damage is not None:
            if self.boss_observed_from is None:
                self.boss_observed_from = game_time
            self.boss_event_end = game_time

    def observed_skill_interval(self, owner, actor, start, end):
        if (not self.event_coverage or self.event_coverage_broken
                or owner in self._skill_unresolved_owners
                or self._settlement_skill(owner, actor) is None):
            return None
        return self._skill_active_seconds(owner, start=start, stop=end, actor=actor)

    def _skill_active_seconds(self, owner, *, start=0.0, stop=None, actor=None):
        end = self.event_coverage_end if self.event_coverage_end is not None else self.now
        if stop is not None:
            end = min(end, stop)
        windows = list(self.skill_windows.get(owner, ()))
        windows.extend(window for (candidate, _), window in self.settlement_skill_active.items()
                       if candidate == owner)
        intervals = sorted((max(start, window['start']), min(end, window.get('end', end)))
                           for window in windows if actor is None or window.get('actor') == actor)
        total = 0.0
        through = start
        for left, right in intervals:
            total += max(0.0, right - max(left, through))
            through = max(through, right)
        return total

    @staticmethod
    def _is_ammo_window(window):
        return window is not None and 'skchr_angel2_3' in (window['key'], window['id'])

    @staticmethod
    def _validate_skill_cast_state(kind, method, reason, wait_for_end, key, skill_id, frame=None):
        if frame is not None and (type(frame) is not int or frame < 0):
            raise ValueError('invalid skill frame')
        if method == 'BasicSkill.OnCastFinish':
            if (kind != 'skill_end' or type(wait_for_end) is not bool
                    or type(reason) is not int or not -(1 << 31) <= reason < (1 << 31)
                    or not any(isinstance(value, str) and value for value in (key, skill_id))):
                raise ValueError('invalid native skill cast state')
        elif wait_for_end is not None:
            raise ValueError('native skill state is only valid for a cast-return observation')

    @staticmethod
    def _native_cast_window_matches(window, source, key, skill_id, actor):
        if window is None or source != window['source'] or actor != window.get('actor'):
            return False
        identity = {value for value in (key, skill_id) if isinstance(value, str) and value}
        active_identity = {value for value in (window['key'], window['id'])
                           if isinstance(value, str) and value}
        return not active_identity.isdisjoint(identity)

    @classmethod
    def _unconfirmed_skill_reason(cls, window, *, live=False):
        if window is not None and 'normal_cast_pending' in window:
            return '原生施放调用尚未返回完整状态'
        if window is not None and 'owner_dead_at' in window:
            return '未观察到同实例的永久退场证明'
        if window is None or ('cast_completed_at' not in window and 'movement_at' not in window):
            return None
        if cls._is_ammo_window(window):
            if live and window['effect_started'] and window.get('native_wait_for_skill_end') is True:
                return None
            return '未观察到弹药技能的实际结束'
        if live and window['effect_started']:
            # The observed portion of a confirmed activation is a usable window.
            return None
        return '未观察到技能效果的实际结束'

    @property
    def skill_incomplete(self):
        """Historical actor quality projected to the owner, without another writer."""
        return {owner: reason for (owner, _actor), reason in self.skill_history_issues.items()}

    @property
    def skill_active(self):
        """Owner projection of the actor windows; never a second state store."""
        return {owner: window for (owner, _actor), window in self.settlement_skill_active.items()}

    def _close_skill_window(self, identity, game_time, reason, *, confirmed):
        owner, actor = identity
        window = self.settlement_skill_active.pop(identity)
        if not confirmed:
            unconfirmed = self._unconfirmed_skill_reason(window)
            if unconfirmed is not None:
                self.skill_history_issues[identity] = unconfirmed
                self.settlement_skill_incomplete.add(identity)
                self._mark_skill_observation_uncertain(owner, actor)
        window['end'] = game_time
        window['reason'] = reason
        self.skill_windows.setdefault(owner, []).append(window)

    def _close_skill_windows(self, game_time, *, battle_end):
        reason = 'battle_end' if battle_end else 'coverage_end'
        for identity, window in tuple(self.settlement_skill_active.items()):
            confirmed = battle_end and self._unconfirmed_skill_reason(window, live=True) is None
            if self._is_ammo_window(window):
                confirmed = False
            self._close_skill_window(identity, game_time, reason, confirmed=confirmed)

    def _record_skill_transition(self, owner, source, kind, game_time, *,
                                 key, skill_id, name, method, reason, wait_for_end=None, actor=None, frame=None, deployment=None, terminal=False, invocation=None):
        self.record_settlement_skill(owner, actor, source, kind, game_time,
            key=key, skill_id=skill_id, name=name, method=method, reason=reason,
            wait_for_end=wait_for_end, frame=frame, deployment=deployment,
            terminal=terminal, invocation=invocation)

    def record_settlement_skill(self, owner, actor, source, kind, game_time, *,
                                key, skill_id, name, method, reason, wait_for_end=None, frame=None, deployment=None, terminal=False, invocation=None):
        identity = (owner, actor)
        was_known = self._settlement_skill(owner, actor) is not None
        previous = self.settlement_skill_active.get(identity)
        was_active = previous is not None and previous['effect_started']
        effect_started = self._apply_skill_transition(
            self.settlement_skill_active, identity, source, kind, game_time,
            key=key, skill_id=skill_id, name=name, method=method, reason=reason,
            wait_for_end=wait_for_end, close=self._close_skill_window, actor=actor, frame=frame, deployment=deployment, terminal=terminal, invocation=invocation)
        if effect_started:
            # Earlier unclassified hits stay in the damage ledger's denominator.
            self.settlement_skill_initial_pending.discard(identity)
            if identity in self.settlement_skill_scope_unassigned:
                self.settlement_skill_scope_unassigned.remove(identity)
                self.settlement_skill_past_incomplete.add(owner)
            if (identity in self.settlement_skill_incomplete
                    and (not was_active or self.settlement_skill_active[identity] is not previous)):
                # A proved new effect timestamps subsequent hits. Its start cannot
                # repair the missing endpoint or reclassify hits in an older window.
                self.settlement_skill_incomplete.remove(identity)
                self.settlement_skill_past_incomplete.add(owner)

        elif (kind == 'skill_end'
                and (terminal or method in ('Character.OnSkillFinish', 'Character.SwitchOutFromSkillState'))
                and type(actor) is int and actor > 0 and self.owners.get(source) == owner
                and (terminal or any(isinstance(value, str) and value for value in (key, skill_id)))
                and identity not in self.settlement_skill_active):
            # A verified effect endpoint proves this actor is inactive from
            # here onward, even if its activation was outside our observation.
            # It cannot classify an earlier hit or supply a missing duration.
            self.settlement_skill_initial_pending.discard(identity)
            if identity in self.settlement_skill_scope_unassigned:
                self.settlement_skill_scope_unassigned.remove(identity)
                self.settlement_skill_past_incomplete.add(owner)
        if self._settlement_skill(owner, actor) is not None:
            uncertain_since = self._pending_skill_observation_uncertainty.pop(identity, None)
            if uncertain_since is not None and uncertain_since < game_time:
                self._mark_skill_observation_uncertain(owner, actor)
        elif was_known:
            # One callback can report cast completion before a same-clock
            # callback proves the effect is still open. Only a break across
            # the native event clock makes a previously timed interval unknown.
            self._pending_skill_observation_uncertainty[identity] = game_time

    def mark_settlement_skill_cast_unmatched(self, owner, actor):
        # Actor ownership is already proven. Missing activation is a state gap,
        # distinct from an unknown initial state or owner-wide attribution gap.
        self.settlement_skill_incomplete.add((owner, actor))
        self._mark_skill_observation_uncertain(owner, actor)

    def mark_settlement_skill_initial_unknown(self, owner, actor):
        identity = (owner, actor)
        if identity not in self.settlement_skill_initial_pending:
            self.settlement_skill_initial_pending.add(identity)
            self._mark_skill_observation_uncertain(owner, actor)

    def mark_settlement_skill_unassigned(self, source, method, reason, *, actor=None):
        owner = self.owners.get(source)
        if owner is None:
            return
        if method == 'BattleController.OnCharacterFinished':
            for identity, window in self.settlement_skill_active.items():
                if (identity[0] == owner and reason == 11
                        and 'skchr_ulpia_3' in (window['key'], window['id'])):
                    self.settlement_skill_incomplete.add(identity)
                    self._mark_skill_observation_uncertain(*identity)
            return
        if type(actor) is int and actor > 0:
            # The caller proved which actor is uncertain, without claiming
            # that the uncertain event belongs to its current battle scope.
            # A later verified activation/endpoint may establish future state.
            self.settlement_skill_scope_unassigned.add((owner, actor))
            self._mark_skill_observation_uncertain(owner, actor)
            return
        self.settlement_skill_unassigned.add(owner)
        self._mark_skill_observation_uncertain(owner)

    def _apply_skill_transition(self, active, identity_key, source, kind, game_time, *,
                                key, skill_id, name, method, reason, close,
                                wait_for_end=None, actor=None, frame=None, deployment=None, terminal=False, invocation=None):
        self._validate_skill_cast_state(kind, method, reason, wait_for_end, key, skill_id, frame)
        window = active.get(identity_key)
        if (window is not None and deployment is not None and window.get('deployment') is not None
                and deployment != window['deployment']):
            # A late callback from an older deployed body cannot touch this window.
            if kind != 'skill_start':
                return
            close(identity_key, game_time, 'deployment_changed', confirmed=False)
            window = None
        identity = {key, skill_id} - {None}
        active_identity = {window['key'], window['id']} - {None} if window is not None else set()
        same_skill = not active_identity or not identity or not active_identity.isdisjoint(identity)
        if kind == 'skill_start':
            self.skill_seen.add(identity_key[0])
            if (window is not None and source == window['source']
                    and method != 'Character.OnSkillRetriggered'
                    and self._unconfirmed_skill_reason(window) is not None
                    and game_time > max(window.get('cast_completed_at', 0),
                                        window.get('movement_at', 0))):
                # A later activation cannot certify the previous effect's endpoint.
                close(identity_key, game_time, 'next_activation', confirmed=False)
                window = None
            effect_started = method in ('Character.SwitchToSkillState', 'Character.OnSkillCastSucceed',
                                        'Character.OnToggleSkillStart', 'Character.OnSkillRetriggered')
            if window is None:
                active[identity_key] = {
                    'start': game_time, 'source': source, 'key': key, 'id': skill_id,
                    'name': name, 'effect_started': effect_started, 'actor': actor, 'deployment': deployment}
                return effect_started
            elif source == window['source'] and same_skill and effect_started:
                window['effect_started'] = True
                return True
            return False
        if terminal and method == 'BattleController.OnCharacterFinished':
            if (window is not None and actor == window.get('actor') and deployment is not None
                    and deployment == window.get('deployment') and source == window['source']):
                same_endpoint = window.get('owner_dead_at', game_time) == game_time
                close(identity_key, game_time, reason, confirmed=same_endpoint)
            return
        if method == 'BasicSkill.OnCastFinish' and not self._native_cast_window_matches(
                window, source, key, skill_id, actor):
            if wait_for_end is True:
                raise ValueError('native cast state has no matching skill activation')
            return
        if window is None or source != window['source'] or not same_skill:
            if method == 'BasicSkill.OnCastFinish' and wait_for_end is True:
                raise ValueError('native cast state has no matching skill activation')
            return
        if method == 'BasicSkill.OnCastFinish':
            pending = window.get('normal_cast_pending')
            if pending is not None:
                if (invocation != pending['invocation'] or frame != pending['frame'] or game_time != pending['time']
                        or deployment is None or deployment != window.get('deployment')):
                    raise ValueError('native return does not close the pending invocation')
                del window['normal_cast_pending']
                if wait_for_end is False:
                    close(identity_key, game_time, reason, confirmed=False)
                    return
            # This native return observes cast state, never an effect endpoint.
            window.setdefault('cast_completed_at', game_time)
            window['native_cast_return_at'] = game_time
            window['native_cast_return_frame'] = frame
            window['native_wait_for_skill_end'] = wait_for_end
            return
        if method == 'Character.OnAfterSkill' and type(reason) is int:
            # Ability.FinishReason.INTERRUPTED can end the cast action while
            # the skill's damage effect is still active.
            # A normal return can also report the same cast completion when
            # the same actor and skill just confirmed that its effect remains.
            if (reason == 0 and invocation is not None and window['effect_started']
                    and deployment is not None and deployment == window.get('deployment')
                    and type(actor) is int and actor > 0 and frame is not None):
                pending = window.get('normal_cast_pending')
                if pending is not None and pending['invocation'] != invocation:
                    raise ValueError('overlapping native skill cast invocations')
                window.setdefault('cast_completed_at', game_time)
                window['normal_cast_pending'] = dict(invocation=invocation, frame=frame, time=game_time)
                return
            if reason == 2 and deployment is not None and deployment == window.get('deployment'):
                window.setdefault('cast_completed_at', game_time)
                window['owner_dead_at'] = game_time
                return
            if reason == 1 or (reason == 0 and window['effect_started']
                    and type(actor) is int and actor > 0
                    and frame is not None and window.get('native_cast_return_frame') == frame
                    and window.get('native_cast_return_at') == game_time
                    and window.get('native_wait_for_skill_end') is True
                    and self._native_cast_window_matches(window, source, key, skill_id, actor)):
                window.setdefault('cast_completed_at', game_time)
                return
        if method == 'BattleController.OnCharacterFinished' and reason in (11, 12):
            # Entity.FinishReason.MOVE_LIKE_RESPAWN_SELF reports both Ulpian
            # S3's outward move on the cast-completion frame and his return.
            if reason == 11 and 'skchr_ulpia_3' in active_identity:
                moved_at = window.setdefault('movement_at', game_time)
                if moved_at == window.get('cast_completed_at') and game_time > moved_at:
                    close(identity_key, game_time, reason, confirmed=True)
            return
        confirmed = method in ('Character.OnSkillFinish', 'Character.SwitchOutFromSkillState')
        close(identity_key, game_time, reason, confirmed=confirmed)

    def snapshot(self, game_time, counters):
        """Apply a complete source-key snapshot; omitted sources become unknown.

        Each value contains both damage and healing cumulative client counters.
        Validation is transactional. A reappearing source starts a new reliable
        window without treating its missing interval as zero.
        """
        self._time(game_time)
        if self.end_time is not None or game_time < self.now:
            raise ValueError('invalid snapshot time')
        if not self.baseline_ready and game_time != self.start_time:
            raise ValueError('first snapshot must establish the baseline at coverage start')
        for source, values in counters.items():
            prior = self.sources.get(source)
            if prior and prior['history'] and prior['history'][-1][0] == game_time and prior['values'] != values:
                raise ValueError('conflicting counters at the same game time')
            if source not in self.owners or set(values) != {'damage', 'healing'}:
                raise ValueError('unknown source or incomplete counters')
            for kind in ('damage', 'healing'):
                self._amount(values[kind])
                old = self.sources.get(source)
                if old and values[kind] < old['values'][kind]:
                    raise ValueError('cumulative counter decreased')
        if not self.baseline_ready:
            self.initial_damage = {source: values['damage'] for source, values in counters.items()}
        self.now = game_time
        self.baseline_ready = True
        for source, state in self.sources.items():
            if source not in counters:
                state['continuous'] = False
                state['totals_time'] = None
        for source, values in counters.items():
            state = self.sources.get(source)
            if state is None:
                state = self.sources[source] = {'values': dict(values), 'history': deque(),
                                                'continuous': True, 'segment_start': game_time,
                                                'totals_valid': True, 'totals_time': game_time}
            elif not state['continuous']:
                state['history'].clear()
                state['continuous'] = True
                state['segment_start'] = game_time
            state['values'] = dict(values)
            state['totals_valid'] = True
            state['totals_time'] = game_time
            history = state['history']
            entry = (game_time, dict(values))
            if history and history[-1][0] == game_time:
                history[-1] = entry
            else:
                history.append(entry)
            cutoff = game_time - 5
            while len(history) > 2 and history[1][0] <= cutoff:
                history.popleft()

    def settle_snapshot(self, counters):
        """Revise authoritative totals at the already frozen battle endpoint.

        This is deliberately separate from ordinary snapshots. It never moves
        the clock, reopens events or invents a rate for a same-time correction.
        The adapter must verify the same ended battle/frame before calling it.
        """
        if self.end_time is None:
            raise ValueError('only an ended battle can receive settlement revisions')
        return self._revise_terminal_totals(counters, self.end_time)

    def revise_pending_totals(self, game_time, counters):
        """Revise an independently read terminal snapshot without closing events.

        The adapter must prove the same ended native battle/frame. This narrow
        entry never relaxes ordinary same-time snapshot conflicts.
        """
        if type(game_time) not in (int, float):
            raise ValueError('pending terminal time must be a finite number')
        self._time(game_time)
        if self.end_time is not None or not self.baseline_ready or game_time != self.now:
            raise ValueError('pending terminal totals require the current unfrozen endpoint')
        changed = self._revise_terminal_totals(counters, game_time)
        self.invalidate_rates()
        return changed

    def _revise_terminal_totals(self, counters, endpoint):
        for source, values in counters.items():
            if source not in self.owners or set(values) != {'damage', 'healing'}:
                raise ValueError('unknown source or incomplete settlement counters')
            for kind, value in values.items():
                self._amount(value)
                if source in self.sources and value < self.sources[source]['values'][kind]:
                    raise ValueError('settlement counter decreased')
        changed = False
        for source, state in self.sources.items():
            if source not in counters and state['continuous']:
                state['continuous'] = False
                changed = True
        for source, values in counters.items():
            prior = self.sources.get(source)
            if prior is None or prior['values'] != values or not prior['continuous']:
                self.sources[source] = {'values': dict(values),
                                        'history': deque([(endpoint, dict(values))]),
                                        'continuous': True, 'segment_start': endpoint,
                                        'totals_valid': True, 'totals_time': endpoint}
                changed = True
            else:
                prior['totals_valid'] = True
                prior['totals_time'] = endpoint
        return changed

    def correct_final_totals(self, counters):
        """Revise terminal counters without claiming timing for any final rate."""
        if not self.baseline_ready:
            raise ValueError('final totals require an observation baseline')
        changed = any(source not in self.sources or self.sources[source]['values'] != values
                      for source, values in counters.items())
        self.settle_snapshot(counters)
        self.seal_totals()
        self.invalidate_rates()
        return changed

    @staticmethod
    def _optional_number(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and isfinite(value))

    def mark_mitigation_unknown(self):
        self.mitigation_probe_incomplete = True

    def _mitigation_pair(self, amount, before, after):
        try:
            return (self._optional_number(before) and before > 0
                    and self._optional_number(after) and after >= 0
                    and isclose(after, amount, rel_tol=0, abs_tol=1e-6))
        except OverflowError:
            return False

    def _record_mitigation(self, source, owner, amount, before, after):
        if self.mitigation_probe_incomplete:
            return
        if not self._mitigation_pair(amount, before, after):
            self.mitigation_incomplete.add(owner)
            return
        meter = self.settled.get(owner)
        before_total, after_total, hits = meter.source_pairs.get(source, (0.0, 0.0, 0)) if meter else (0.0, 0.0, 0)
        totals = before_total + before, after_total + after
        if not all(isfinite(value) for value in totals):
            self.mitigation_incomplete.add(owner)
            return

    def _mitigation_result(self, owner, registered, *, complete):
        if self.settlement_enabled:
            lane = self._settlement_row(owner)
            known = lane['mitigation_complete']
            return dict(mitigation_rate=lane['observed_mitigation_rate'] if known else None,
                mitigation_before=lane['observed_mitigation_before'] if known else None,
                mitigation_after=lane['observed_mitigation_after'] if known else None,
                mitigation_unknown_reason=lane['mitigation_reason'],
                observed_mitigation_rate=lane['observed_mitigation_rate'],
                observed_mitigation_before=lane['observed_mitigation_before'],
                observed_mitigation_after=lane['observed_mitigation_after'],
                mitigation_matched_hits=lane['mitigation_matched_hits'])
        totals = [self.mitigation.get(source, (0.0, 0.0, 0)) for source in registered]
        supplement = self.sourceless_burst_mitigation.get(owner, (0.0, 0.0, 0))
        before = sum(pair[0] for pair in totals) + supplement[0]
        after = sum(pair[1] for pair in totals) + supplement[1]
        hits = sum(pair[2] for pair in totals) + supplement[2]
        rate = (1 - after / before) * 100 if before > 0 and isfinite(before) and isfinite(after) else None
        if rate is not None and not isfinite(rate):
            rate = None

        # Keep the original full-battle scope separate from valid matched hits.
        # A later gap does not change the values of previously settled pairs.
        if not self.event_coverage:
            reason = '事件观察未建立'
        elif self.event_coverage_broken:
            reason = '事件覆盖已结束'
        elif self.mitigation_probe_incomplete:
            reason = '伤害公式回调覆盖不足'
        elif self.mitigation_unattributed_burst:
            reason = self.burst_unknown_reason
        elif owner in self.mitigation_incomplete:
            reason = '部分伤害缺少同次命中的公式前后值'
        elif not complete:
            reason = '原生累计伤害尚不可核对'
        elif any(not isclose(pair[1], self.sources[source]['values']['damage'],
                             rel_tol=1e-6, abs_tol=0.01)
                 for source, pair in zip(registered, totals)):
            reason = '公式后伤害与本局累计不一致'
        elif rate is None:
            reason = '尚无可核对的公式前后伤害'
        else:
            reason = None
        return {
            'mitigation_rate': rate if reason is None else None,
            'mitigation_before': before if reason is None else None,
            'mitigation_after': after if reason is None else None,
            'mitigation_unknown_reason': reason,
            'observed_mitigation_rate': rate,
            'observed_mitigation_before': before if rate is not None else None,
            'observed_mitigation_after': after if rate is not None else None,
            'mitigation_matched_hits': hits,
        }

    def _overkill(self, amount, *, target, real_delta, hp_before, hp_after,
                  killed, target_type, damage_type, cancel_reason, shield_absorbed,
                  settled_after_nested_kill=False):
        """Derive one hit's overkill from native settlement evidence.

        Returns ``(known, value, reason)``. On failure ``reason`` names the
        missing or contradictory evidence so the UI can explain the unknown.
        """
        if not isinstance(target, str) or not target:
            return False, 0.0, '伤害事件缺少目标身份'
        if type(killed) is not bool:
            return False, 0.0, '伤害事件缺少击杀标记'
        if target_type is not None and type(target_type) is not int:
            return False, 0.0, '目标类型无效'
        if damage_type is not None and type(damage_type) is not int:
            return False, 0.0, '伤害类型无效'
        if cancel_reason is not None and type(cancel_reason) is not int:
            return False, 0.0, '取消原因无效'
        if settled_after_nested_kill is not False:
            if (settled_after_nested_kill is not True or target.startswith(('char_', 'token_'))
                    or killed is not True or type(cancel_reason) is not int or cancel_reason != 0
                    or not all(self._optional_number(value) and value == 0
                               for value in (real_delta, hp_before, hp_after, shield_absorbed))):
                return False, 0.0, '嵌套击杀后的结算证据不一致'
            # The child caused the terminal HP transition. The parent is still
            # credited by the client at nominal value, with zero actual damage.
            return True, amount, ''
        # A non-lethal hit cannot overkill: if the probe observed that the
        # target was still alive after this hit, no HP-before/real-delta
        # reconstruction is required. This matters for same-frame multi-hit
        # effects where another hit changes HP between the two reads.
        tolerance = max(0.01, amount * 1e-6)
        if (killed is False and self._optional_number(hp_after)
                and hp_after > tolerance):
            return True, 0.0, ''
        if not all(self._optional_number(v) for v in (real_delta, hp_before, hp_after)):
            return False, 0.0, '伤害事件缺少目标 HP 或实际扣血'
        if real_delta > 0 or hp_before < 0 or hp_after < 0:
            return False, 0.0, '目标 HP 或扣血数值无效'
        if (shield_absorbed is not None
                and (not self._optional_number(shield_absorbed) or shield_absorbed < 0)):
            return False, 0.0, '护盾/抵挡吸收数值无效'
        actual = -real_delta
        if actual > amount + tolerance:
            return False, 0.0, '实际扣血超过结算伤害'
        if abs((hp_before - hp_after) - actual) > tolerance:
            return False, 0.0, '目标 HP 变化与实际扣血不一致'
        if killed and hp_after > tolerance:
            return False, 0.0, '击杀标记与目标 HP 不一致'
        if not killed and hp_after <= tolerance:
            return False, 0.0, '击杀标记与目标 HP 不一致'
        if not killed:
            return True, 0.0, ''
        if shield_absorbed is None:
            return False, 0.0, '击杀缺少护盾/抵挡吸收上下文'
        if shield_absorbed > amount + tolerance:
            return False, 0.0, '护盾吸收超过结算伤害'
        if actual + shield_absorbed > amount + tolerance:
            return False, 0.0, '护盾吸收与实际扣血之和不一致'
        return True, max(0.0, amount - shield_absorbed - actual), ''

    def _reject_burst_evidence(self, error, owner, target, amount, *, sourceless):
        if not self.partial_damage_enabled:
            raise ValueError(error)
        self.burst_unknown = True
        self.burst_unknown_reason = '部分元素爆条缺少可靠的扣血结算'
        if amount > 0 and (not isinstance(target, str) or not target.startswith(('char_', 'token_'))):
            if owner is None:
                self.mitigation_unattributed_burst = True
                self.partial_unattributed_damage |= sourceless
            else:
                self.overkill_incomplete.add(owner)
                self.overkill_reason[owner] = self.burst_unknown_reason
                self.mitigation_incomplete.add(owner)
                if sourceless:
                    self.partial_unquantified_damage.add(owner)
        if classify_boss(target) is not False:
            self.boss_damage_incomplete = True

    def event(self, event_id, game_time, source, kind, amount, *,
              target=None, real_delta=None, hp_before=None, hp_after=None,
              killed=None, target_type=None, damage_type=None,
              cancel_reason=None, shield_absorbed=None, target_instance=None,
              skill_key=None, skill_id=None, skill_name=None, skill_reason=None,
              verified_skill_end_owner=None, skill_method=None, skill_wait_for_end=None,
              skill_actor=None, skill_frame=None, skill_deployment=None, skill_terminal=False, skill_cast_invocation=None,
              mitigation_before=None, mitigation_after=None, settled_after_nested_kill=False):
        """Record one reliable event: damage, healing, or actual shield absorption.

        Duplicate IDs with identical payload are ignored; conflicts are rejected.
        Inputs must arrive in game-time order. Shield expiration and replacement
        are not absorption and have no accepted event kind.

        Damage events may carry overkill evidence: ``amount`` is the native
        settlement value before shield/block absorption, ``shield_absorbed``
        the absorption observed for that hit, ``real_delta`` the native signed
        HP delta (negative for damage), ``hp_before``/``hp_after`` the target
        HP, ``killed`` the explicit endpoint and ``target_type``/
        ``damage_type``/``cancel_reason`` the native context. A lethal hit with
        missing or inconsistent absorption/HP evidence keeps overkill unknown;
        it never becomes zero and never changes the native totals.
        """
        self._time(game_time)
        self._amount(amount)
        if not self.event_coverage or self.event_coverage_end is not None or not self.baseline_ready:
            raise ValueError('event coverage or baseline is unavailable')
        kinds = ('damage', 'healing', 'shield_absorbed', 'elemental_bar', 'elemental_burst',
                 'hp_damage', 'skill_start', 'skill_end')
        if not event_id or kind not in kinds:
            raise ValueError('invalid event identity or kind')
        if kind == 'healing' and type(amount) is bool:
            raise ValueError('invalid healing amount')
        self._validate_skill_cast_state(kind, skill_method or target_instance, skill_reason,
                                        skill_wait_for_end, skill_key, skill_id, skill_frame)
        owner = self.owners.get(source)
        if kind == 'skill_end' and owner is None:
            owner = verified_skill_end_owner
        if kind == 'hp_damage' and owner is None:
            # Target-side HP evidence is supplemental. An unmapped source must
            # not tear down the authoritative damage/engagement stream.
            return
        if kind in ('skill_start', 'skill_end') and owner is None:
            # A verified stage device has no squad skill window. A source
            # without verified ownership still makes the split uncertain.
            if classify_unmapped_source(source) is None:
                self.mark_skill_unknown()
            return
        if kind != 'elemental_burst' and owner is None:
            raise ValueError('invalid event source')
        if kind == 'elemental_burst' and source is not None and source not in self.owners:
            raise ValueError('invalid burst source')
        evidence = (target, real_delta, hp_before, hp_after, killed,
                    target_type, damage_type, cancel_reason, shield_absorbed)
        if kind not in ('damage', 'elemental_burst', 'hp_damage') and any(value is not None for value in evidence):
            raise ValueError('damage evidence is only valid for damage, burst, or hp_damage')
        payload = (game_time, source, kind, amount, evidence, target_instance, owner,
                   skill_key, skill_id, skill_name, skill_reason, skill_method, skill_wait_for_end, skill_actor,
                   mitigation_before, mitigation_after, settled_after_nested_kill)
        if skill_frame is not None:
            payload += (skill_frame,)
        if event_id in self.events:
            if self.events[event_id] != payload:
                raise ValueError('conflicting event ID')
            return
        if (self.end_time is not None or not self.event_coverage_start <= game_time <= self.now
                or game_time < self.last_event_time):
            raise ValueError('event outside ordered observation')
        self.events[event_id] = payload
        self.last_event_time = game_time
        if self.settlement_enabled and kind in ('damage', 'elemental_burst'):
            # The certificate is the sole amount writer. Legacy outputs remain
            # ordered receipts, not another damage/skill/formula account.
            if kind == 'damage':
                self.settled_damage_sources.add(source)
            return
        if kind == 'elemental_bar':
            if (not target_instance or owner is None
                    or (self.partial_damage_enabled and not isinstance(target_instance, str))):
                if self.partial_damage_enabled:
                    # The next source-null burst could belong to this bar.
                    # Without the target identity no previous bar is reliable.
                    self._discard_elemental_instance(None, legacy=True)
                    self.burst_unknown = True
                    self.burst_unknown_reason = '元素损伤缺少可靠的目标身份'
                    return
                raise ValueError('elemental bar needs a source and target instance')
            if not self.settlement_enabled:
                self.settlement_bars[('legacy', target_instance)] = (target, target_instance, 'local', owner, None)
            return
        if kind == 'elemental_burst':
            friendly = isinstance(target, str) and target.startswith(('char_', 'token_'))
            if self.damage_settlement_version >= 5 and friendly:
                return
            if not target_instance or (self.partial_damage_enabled and not isinstance(target_instance, str)):
                self._reject_burst_evidence('elemental burst needs a target instance', owner, target, amount,
                                            sourceless=source is None)
                return
            attributed = owner if owner is not None else self.last_elemental.get(target_instance)
            tolerance = max(0.01, amount * 1e-6)
            if (not self._optional_number(real_delta) or real_delta > 0
                    or not self._optional_number(hp_before)
                    or not self._optional_number(hp_after)
                    or hp_before < 0 or hp_after < 0
                    or (self.partial_damage_enabled and (abs(hp_before - hp_after + real_delta) > tolerance
                        or type(killed) is not bool or killed != (hp_after <= 1e-6)))):
                self._reject_burst_evidence('invalid elemental burst evidence', attributed, target, amount,
                                            sourceless=source is None)
                self._discard_elemental_instance(target_instance, legacy=True)
                return
            actual = -real_delta
            if actual > amount + tolerance:
                self._reject_burst_evidence('elemental burst actual exceeds settlement', attributed, target, amount,
                                            sourceless=source is None)
                self._discard_elemental_instance(target_instance, legacy=True)
                return
            if attributed is None:
                self.burst_unknown = True
                self.burst_unknown_reason = '元素爆条缺少可归属的最后元素损伤来源'
                if amount > 0 and not friendly:
                    self.mitigation_unattributed_burst = True
                    self.partial_unattributed_damage |= self.partial_damage_enabled and source is None
                if classify_boss(target) is not False:
                    self.boss_damage_incomplete = True
                return
            if not friendly and (amount > 0 or mitigation_before is not None
                                 or mitigation_after is not None):
                self._record_mitigation(source, attributed, amount, mitigation_before, mitigation_after)
            burst_overkill = max(0.0, amount - actual)
            self._record_boss_damage(source, attributed, game_time, target, amount, 5, burst_overkill,
                                     before=mitigation_before, after=mitigation_after, actual=actual)
            if not self.settlement_enabled:
                self.settlement_supplements.setdefault(attributed, 0.0)
                if source is None:
                    self.settlement_supplements[attributed] += amount
            self._discard_elemental_instance(target_instance, legacy=True)
            return
        if kind in ('skill_start', 'skill_end'):
            self._record_skill_transition(
                owner, source, kind, game_time, key=skill_key, skill_id=skill_id,
                name=skill_name, method=skill_method or target_instance, reason=skill_reason,
                wait_for_end=skill_wait_for_end, actor=skill_actor, frame=skill_frame,
                deployment=skill_deployment, terminal=skill_terminal, invocation=skill_cast_invocation)
            return
        if kind == 'hp_damage':
            friendly = (isinstance(target, str)
                        and target.startswith(('char_', 'token_')))
            if amount > 0 and not friendly and damage_type != 5:
                self.incoming_damage_sources.add(source)
            return
        if kind == 'damage':
            friendly = isinstance(target, str) and target.startswith(('char_', 'token_'))
            if self.damage_settlement_version >= 5 and friendly:
                return
            self.settled_damage_sources.add(source)
            # A modifier can still return its nominal amount for a target
            # already at zero HP or for a cancelled hit. These verified zero
            # settlements are absent from the source's native output counter.
            confirmed_miss = (self.reconcile_damage_scope and cancel_reason == 2
                              and type(shield_absorbed) in (int, float) and shield_absorbed == 0)
            if (settled_after_nested_kill is False and real_delta == 0 and self._optional_number(hp_before)
                    and hp_before == hp_after
                    and (hp_before == 0 and killed is True or cancel_reason == 1 or confirmed_miss)):
                return
            if not friendly and (amount > 0 or mitigation_before is not None
                                 or mitigation_after is not None):
                self._record_mitigation(source, owner, amount, mitigation_before, mitigation_after)
            known, overkill, reason = self._overkill(
                amount, target=target, real_delta=real_delta, hp_before=hp_before,
                hp_after=hp_after, killed=killed, target_type=target_type,
                damage_type=damage_type, cancel_reason=cancel_reason,
                shield_absorbed=shield_absorbed, settled_after_nested_kill=settled_after_nested_kill)
            self._record_boss_damage(
                source, owner, game_time, target, amount, damage_type,
                overkill if known else None, reason, mitigation_before, mitigation_after)
            if known:
                if killed and target_instance and settled_after_nested_kill is False:
                    self._discard_elemental_instance(target_instance, legacy=True)
                if overkill > 0 and NATIVE_DAMAGE_TYPES.get(damage_type) is None:
                    self.overkill_type_incomplete.add(owner)
            else:
                self.overkill_incomplete.add(owner)
                self.overkill_reason[owner] = reason
        if kind == 'shield_absorbed':
            self.shields[owner] = self.shields.get(owner, 0) + amount
        elif (kind == 'healing' or kind == 'damage' and not self.settlement_enabled) and amount > 0:
            # Damage dealt to a friendly/self target (e.g. a skill's HP cost)
            # is not outgoing DPS. Only enemy-directed damage defines the
            # damage engagement numerator and active interval. Healing keeps
            # its own friendly-target interval as before.
            friendly_damage = (kind == 'damage' and isinstance(target, str)
                               and (target.startswith('char_') or target.startswith('token_')))
            if not friendly_damage:
                state = self.engagement.setdefault((owner, kind), {'total': 0, 'intervals': []})
                state['total'] += amount
                intervals = state['intervals']
                endpoint, _, joins = extend_activity(intervals[-1][1] if intervals else game_time, game_time)
                if intervals and joins:
                    intervals[-1] = (intervals[-1][0], endpoint)
                else:
                    intervals.append((game_time, endpoint))
                if kind == 'healing':
                    self._record_recent_healing(state, game_time, amount)

    @staticmethod
    def _record_recent_healing(state, when, amount):
        recent = state.get('recent')
        if recent is None:
            recent = state['recent'] = deque()
        cutoff = when - HEALING_RECENT_SECONDS
        while recent and recent[0][0] < cutoff:
            recent.popleft()
        if recent and recent[-1][0] == when:
            recent[-1] = when, recent[-1][1] + amount
        else:
            recent.append((when, amount))
            if len(recent) > HEALING_RECENT_LIMIT:
                state['recent_discarded_through'] = recent.popleft()[0]

    def _healing_observation(self, state, engagement_known, gap):
        total = state['total'] if state and observed_amount(state['total']) and state['total'] > 0 else None
        covered = (self.event_coverage and not self.event_coverage_broken and engagement_known
                   and self.event_watermark == self.now
                   and (self.event_coverage_end is None or self.event_coverage_end == self.now))
        result = dict(observed_healing_total=total,
                      observed_healing_window=(self.event_coverage_start, self.event_watermark)
                          if covered and total is not None else None,
                      observed_healing_complete=covered and gap is None and total is not None,
                      observed_recent_healing=None, observed_recent_healing_window=None,
                      observed_recent_healing_complete=False)
        if not covered or total is None:
            return result
        end = self.event_watermark
        start = max(self.event_coverage_start, end - HEALING_RECENT_SECONDS)
        include_start = start == self.event_coverage_start
        discarded = state.get('recent_discarded_through')
        if end <= start or any(when is not None and (when > start or include_start and when == start)
                               for when in (gap, discarded)):
            return result
        recent = state['recent']
        while recent and (recent[0][0] < start or recent[0][0] == start and not include_start):
            recent.popleft()
        amount = sum(amount for when, amount in recent if when <= end)
        rate = amount / (end - start)
        if observed_amount(rate):
            result.update(observed_recent_healing=rate, observed_recent_healing_window=(start, end),
                          observed_recent_healing_complete=True)
        return result

    def _recent(self, state, kind, start):
        if not state['continuous'] or not state['history']:
            return None
        history = state['history']
        # A sample missing at the current time cannot represent a current rate.
        if history[-1][0] != self.now:
            return None
        duration = self.now - start
        if duration <= 0 or history[0][0] > start:
            return None
        before = history[0]
        after = None
        for point in history:
            if point[0] <= start:
                before = point
            else:
                after = point
                break
        baseline = before[1][kind]
        if before[0] < start:
            # The window is five game seconds. A bracket wider than the window is
            # a real collection gap, not a normal sampling interval; normal 2x
            # sampling is roughly 1.7 game seconds. Interpolating the boundary
            # keeps the five-second rate available without requiring a sample at
            # exactly start.
            if after is None or after[0] - before[0] > 5:
                return None
            fraction = (start - before[0]) / (after[0] - before[0])
            baseline += fraction * (after[1][kind] - baseline)
        return (history[-1][1][kind] - baseline) / duration

    def _boss_row(self, owner, registered, row):
        if self.settlement_enabled:
            lane = self._settlement_row(owner, boss=True)
            nominal_known = lane['observed_boss_damage_complete']
            result = dict(lane,
                settled_damage=lane,
                damage=lane['observed_damage'] if nominal_known else None,
                counter_snapshot_complete=False,
                event_coverage_complete=nominal_known,
                event_coverage_end=self.partial_watermark,
                types_available=nominal_known,
                types_stale=not nominal_known,
                recent_damage=lane['observed_boss_recent_damage'],
                recent_damage_window=lane['observed_boss_recent_window'],
                recent_damage_by_type={},
                skill_damage=lane['observed_skill_damage'] if lane['skill_split_complete'] else None,
                non_skill_damage=lane['observed_non_skill_damage'] if lane['skill_split_complete'] else None,
                skill_damage_effective=lane['observed_skill_damage_effective'] if lane['skill_split_effective_complete'] else None,
                non_skill_damage_effective=lane['observed_non_skill_damage_effective'] if lane['skill_split_effective_complete'] else None,
                skill_split_reason=lane.get('skill_split_reason'),
                mitigation_rate=lane['observed_mitigation_rate'] if lane['mitigation_complete'] else None,
                mitigation_before=lane['observed_mitigation_before'] if lane['mitigation_complete'] else None,
                mitigation_after=lane['observed_mitigation_after'] if lane['mitigation_complete'] else None,
                mitigation_unknown_reason=lane['mitigation_reason'],
                partial_damage=lane,
            )
            if self.damage_settlement_version >= 7:
                result['overkill_type_complete'] = lane['overkill_coverage_complete']
                result['recent_overkill_damage'] = None
            return result, lane['reason']
        end = self.boss_event_end
        reason = row.get('damage_scope_reason')
        if reason:
            return {
                'damage_scope_reason': reason, 'overkill_unknown_reason': reason,
                'mitigation_unknown_reason': reason, 'skill_split_reason': reason,
                'counter_snapshot_complete': False, 'event_coverage_complete': False,
            }, reason
        if self.event_coverage_broken or self.boss_damage_incomplete or self.all_engagement_unknown:
            reason = '首领伤害事件覆盖不完整'
        elif end != self.now:
            reason = '等待与当前累计同帧的伤害事件'
        elif owner in self.boss_target_unknown:
            reason = '部分伤害目标的首领身份未知'
        elif owner in self.engagement_incomplete or any(
            source in self.incoming_damage_sources and source not in self.settled_damage_sources
            for source in registered
        ):
            reason = '部分伤害事件缺失'
        elif not row['counter_snapshot_complete'] or any(
            not isclose(self.settlement_totals.get(source, 0.0),
                        self.sources[source]['values']['damage'], rel_tol=1e-6, abs_tol=0.01)
            for source in registered
        ):
            reason = '已捕获伤害与原生累计尚未对齐'
        meter = self.settled_boss.get(owner)
        if not registered and meter is None and reason is None:
            reason = '尚无可核对的原生累计'
        meter = meter or DamageSettlement()
        observed_from = (self.event_coverage_start if self.boss_observed_from is None
                         else self.boss_observed_from)
        live = self.end_time is None and self.event_coverage_end is None
        scoped = meter.legacy_boss_result(
            observed_from, end if end is not None else observed_from,
            known=reason is None, rate_known=reason is None and live, live=live,
            skill_known=reason is None and row['skill_coverage_complete'],
            mitigation_known=not self.mitigation_probe_incomplete, reason=reason,
            reconcile_scope=self.reconcile_damage_scope, partial_enabled=self.partial_damage_enabled)
        return scoped, reason

    def _partial_row(self, owner, row, observed, burst, sources):
        if self.settlement_enabled:
            partial = dict(row['settled_damage'])
            absorbed = self.shields.get(owner)
            end = self.partial_watermark if self.partial_watermark is not None else self.event_coverage_start
            partial.update(observed_shield_absorbed=absorbed,
                shield_observation_window=(self.event_coverage_start, end) if absorbed is not None else None)
            healing = self.engagement.get((owner, 'healing'))
            if healing:
                active = sum(max(0.0, min(end, right) - left) for left, right in healing['intervals'])
                partial.update(engagement_healing=healing['total'] / active if active > 0 else None,
                    active_healing=active,
                    engagement_healing_unverified_share=_unverified_share(healing['total'], row['healing'] if row['counter_snapshot_complete'] else None))
            return partial
        end = max(self.last_event_time, self.partial_watermark or self.event_coverage_start)
        start = max(self.event_coverage_start, self.now - 5)
        comparable_damage = (row['counter_snapshot_complete'] and not self.partial_unattributed_damage
                             and owner not in self.partial_unquantified_damage)
        total = row['damage'] if comparable_damage else None
        recent_native = (row['recent_damage'] * (self.now - start)
                         if comparable_damage and end == self.now
                         and row['recent_damage_window'] == (start, self.now)
                         and all(any(when == start for when, _ in self.sources[source]['history'])
                                 for source in sources)
                         else None)
        meter = self.settled.get(owner) or DamageSettlement()
        partial = meter.legacy_partial_result(observed, total, row['observed_mitigation_after'] or 0.0, start, end,
                               recent_native=recent_native, include_start=start == self.event_coverage_start,
                               reconcile_scope=self.reconcile_damage_scope)
        absorbed = self.shields.get(owner)
        partial['observed_shield_absorbed'] = absorbed
        partial['shield_observation_window'] = (self.event_coverage_start, end) if absorbed is not None else None
        if row.get('damage_scope_reason'):
            # These events cannot be used as a subset of the native damage
            # ledger. Shield and healing still have independent evidence.
            partial = {key: value for key, value in partial.items() if key in (
                'observed_shield_absorbed', 'shield_observation_window')}
        # Existing engagement counts ordinary damage. Attributed elemental
        # bursts contribute to the cumulative damage, so exclude that same
        # settlement from the denominator for this particular engagement rate.
        if total is not None and burst:
            total -= burst['settlement']
        healing = row['healing'] if row['counter_snapshot_complete'] else None
        for kind, denominator in (('damage', total), ('healing', healing)):
            if kind == 'damage' and row.get('damage_scope_reason'):
                continue
            evidence = self.engagement.get((owner, kind))
            if evidence:
                active = sum(max(0.0, min(end, right) - left) for left, right in evidence['intervals'])
                partial['engagement_' + kind] = evidence['total'] / active if active > 0 else None
                partial['active_' + kind] = active
                partial['engagement_' + kind + '_unverified_share'] = _unverified_share(evidence['total'], denominator)
        return partial

    def result(self):
        """Return per-owner values and explicit coverage; unknown is None."""
        late_events = self.event_coverage_start > self.start_time
        owners = set(self.owners.values())
        if self.settlement_enabled:
            owners.update(self.settled)
            owners.update(self.settlement_missing)
        rows = {}
        boss_reasons = set()
        for owner in owners:
            sources = [s for s, o in self.owners.items() if o == owner and s in self.sources]
            registered = [s for s, o in self.owners.items() if o == owner]
            pre_observer_damage = late_events and any(self.initial_damage.get(s, 0.0) > 0 for s in registered)
            # Totals completeness never borrows rate-window continuity: a
            # settlement or battle boundary retires rates, not final counters.
            complete = (not self.settlement_enabled or bool(registered)) and all(
                s in self.sources and self.sources[s]['totals_valid'] and
                self.sources[s]['totals_time'] == self.now for s in registered)
            scope_mismatch = self.reconcile_damage_scope and any(
                self.sources[source]['totals_valid'] and
                exceeds_native_damage(self.settlement_totals.get(source, 0.0),
                                      self.sources[source]['values']['damage']
                                      - self.initial_damage.get(source, 0.0))
                for source in sources)
            coverage_complete = bool(self.event_coverage) and not self.event_coverage_broken
            missing_settlement = any(source in self.incoming_damage_sources
                                     and source not in self.settled_damage_sources
                                     for source in registered)
            overkill_known = ((not self.settlement_enabled or self.damage_settlement_version < 7
                               and owner in self.settled and self.settled[owner].overkill_hits == self.settled[owner].hits) and coverage_complete
                              and not self.all_overkill_unknown
                              and not missing_settlement
                              and not scope_mismatch
                              and owner not in self.overkill_incomplete)
            overkill_type_known = overkill_known and owner not in self.overkill_type_incomplete
            if overkill_known:
                overkill_unknown_reason = None
            elif scope_mismatch:
                overkill_unknown_reason = DAMAGE_SCOPE_MISMATCH
            elif not self.event_coverage:
                overkill_unknown_reason = '事件观察未建立'
            elif not coverage_complete:
                overkill_unknown_reason = '事件覆盖已结束'
            elif self.all_overkill_unknown:
                overkill_unknown_reason = '存在未归属的伤害事件'
            elif missing_settlement:
                overkill_unknown_reason = '仅观察到结算前的目标来伤'
            else:
                overkill_unknown_reason = self.overkill_reason.get(owner, '伤害证据不足')
            # Engagement keeps its observed interval after coverage closes;
            # only an explicitly reported dropped event makes it unknown.
            engagement_known = (bool(self.event_coverage)
                                and not self.all_engagement_unknown
                                and owner not in self.engagement_incomplete)
            settled_lane = self._settlement_row(owner) if self.settlement_enabled else None
            row = {'coverage_start': self.start_time,
                   'counter_snapshot_complete': complete,
                   'shield_coverage_start': self.event_coverage_start if self.event_coverage else None,
                   'event_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                          else self.end_time if self.event_coverage else None),
                   'event_coverage_complete': coverage_complete,
                   'shield_absorbed': (self.shields.get(owner, 0)
                                       if self.event_coverage and not self.shield_unknown else None),
                   'shield_unknown': self.shield_unknown,
                   **({
                       'settled_damage': settled_lane,
                       'effective_damage': settled_lane['effective_damage'],
                       'overkill_damage': settled_lane.get('overkill_damage'),
                       'overkill_damage_by_type': settled_lane.get('overkill_damage_by_type'),
                       'overkill_type_complete': settled_lane.get('overkill_coverage_complete', False),
                       'overkill_unknown_reason': settled_lane.get('overkill_unknown_reason', '旧证书未提供逐击过量'),
                       'overkill_coverage_complete': settled_lane.get('overkill_coverage_complete', False),
                       'recent_overkill_damage': None,
                   } if self.settlement_enabled and self.damage_settlement_version >= 7 else {
                   'overkill_damage': (self.overkill.get(owner, 0.0) if overkill_known else None),
                   'overkill_damage_by_type': ({key: self.overkill_by_type.get(owner, {}).get(key, 0.0)
                                                for key in DAMAGE_TYPE_KEYS}
                                               if overkill_type_known else None),
                   'overkill_type_complete': overkill_type_known,
                   'overkill_unknown_reason': overkill_unknown_reason,
                   'overkill_coverage_complete': overkill_known,
                   }),
                   'overkill_coverage_start': self.event_coverage_start if self.event_coverage else None,
                   'overkill_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                             else self.end_time if self.event_coverage else None)}
            row.update(self._mitigation_result(owner, registered, complete=complete))
            if scope_mismatch:
                row['damage_scope_reason'] = DAMAGE_SCOPE_MISMATCH
                row['mitigation_unknown_reason'] = DAMAGE_SCOPE_MISMATCH
            burst = self.burst.get(owner)
            added_settlement = self.settlement_supplements.get(owner)
            burst_complete = not self.burst_unknown
            row['burst_damage'] = (burst['actual'] if burst and burst_complete else None)
            row['burst_overkill'] = (burst['overkill'] if burst and burst_complete else None)
            row['burst_settlement'] = (burst['settlement'] if burst and burst_complete else None)
            # Only attributed sourceless settlements augment the native total.
            # Keep a verified supplement even when another burst was unowned.
            row['burst_added_settlement'] = (
                added_settlement
                if added_settlement is not None and (burst_complete or added_settlement > 0) else None)
            row['burst_count'] = (burst['count'] if burst and burst_complete else 0)
            row['burst_complete'] = burst_complete
            row['burst_unknown_reason'] = self.burst_unknown_reason if not burst_complete else None
            for kind in ('damage', 'healing'):
                row[kind] = sum(self.sources[s]['values'][kind] for s in sources) if sources else None
                start = max([self.start_time, self.now - 5] +
                            [self.sources[s]['segment_start'] for s in sources])
                rates = [self._recent(self.sources[s], kind, start) for s in sources]
                row['recent_' + kind] = sum(rates) if complete and rates and all(v is not None for v in rates) else None
                # A short post-gap/new-source rate is useful, but cannot certify
                # a full team window. Publish only the expected full interval.
                expected_start = max(self.start_time, self.now - 5)
                row['recent_' + kind + '_window'] = (
                    (start, self.now) if row['recent_' + kind] is not None
                    and start == expected_start else None)
                if kind == 'damage':
                    row['recent_overkill_damage'] = None if self.settlement_enabled and self.damage_settlement_version >= 7 else (
                        sum(value for when, owner_key, value in self.overkill_events
                            if owner_key == owner and start <= when <= self.now)
                        if overkill_known and start >= self.event_coverage_start
                        and row['recent_damage_window'] is not None
                        else None)
                state = self.engagement.get((owner, kind))
                if kind == 'damage' and self.settlement_enabled:
                    lane = settled_lane
                    row['settled_damage'] = lane
                    row['engagement_damage'] = lane['engagement_damage']
                    row['active_damage'] = lane['active_damage']
                    row['observed_damage'] = lane['observed_damage']
                elif state:
                    covered_until = self.event_coverage_end if self.event_coverage_end is not None else self.now
                    active = sum(max(0, min(covered_until, end) - start) for start, end in state['intervals'])
                    row['engagement_' + kind] = (state['total'] / active
                                                 if active > 0 and not (kind == 'damage' and scope_mismatch)
                                                 else None)
                    row['active_' + kind] = active
                    row['observed_' + kind] = state['total']
                else:
                    row['engagement_' + kind] = None
                    row['active_' + kind] = 0 if self.event_coverage else None
                    row['observed_' + kind] = 0 if self.event_coverage else None
                kind_engagement_known = engagement_known
                if kind == 'healing':
                    gap = max(self.healing_observation_gaps.get(None, -1),
                              self.healing_observation_gaps.get(owner, -1))
                    row.update(self._healing_observation(state, engagement_known, None if gap < 0 else gap))
                    kind_engagement_known &= gap < 0
                if not kind_engagement_known:
                    # A dropped event leaves the numerator and its interval
                    # short; publish unknown instead of a low observed rate.
                    row['engagement_' + kind] = None
                    row['active_' + kind] = None
                    row['observed_' + kind] = None
            # Unowned sourceless bursts are outside the credited damage total.
            # Adjust the native total and overkill with the same attributed evidence.
            if added_settlement is not None and row['damage'] is not None:
                row['damage'] += added_settlement
            if (self.damage_settlement_version in (5, 6) and row['damage'] is not None
                    and row['overkill_damage'] is not None and row['overkill_damage'] > row['damage']):
                row.update(overkill_damage=None, overkill_damage_by_type=None,
                           overkill_coverage_complete=False, overkill_type_complete=False,
                           recent_overkill_damage=None, overkill_unknown_reason=DAMAGE_SCOPE_MISMATCH,
                           damage_scope_reason=DAMAGE_SCOPE_MISMATCH)
                overkill_known = False
            if not self.settlement_enabled or self.damage_settlement_version < 7:
                if row['damage'] is not None and row['overkill_damage'] is not None:
                    row['effective_damage'] = row['damage'] - row['overkill_damage']
                else:
                    row['effective_damage'] = None
            skill_incomplete_reason = (
                self.skill_incomplete.get(owner)
                or next((reason for (candidate, _), window in self.settlement_skill_active.items()
                         if candidate == owner
                         and (reason := self._unconfirmed_skill_reason(window, live=True)) is not None), None))
            skill_known = (coverage_complete and not self.skill_unknown
                           and skill_incomplete_reason is None)
            observed = self.settled.get(owner)
            if self.settlement_enabled:
                lane = row['settled_damage']
                split_complete = lane['skill_split_complete']
                effective_split_known = lane['skill_split_effective_complete']
                reason = lane.get('skill_split_reason')
                row['skill_damage'] = lane['observed_skill_damage'] if split_complete else None
                row['non_skill_damage'] = lane['observed_non_skill_damage'] if split_complete else None
                row['skill_damage_effective'] = lane['observed_skill_damage_effective'] if effective_split_known else None
                row['non_skill_damage_effective'] = lane['observed_non_skill_damage_effective'] if effective_split_known else None
            else:
                skill_value = self.skill_observed.get(owner, 0.0)
                non_skill_value = self.non_skill_observed.get(owner, 0.0)
                expected = row.get('observed_damage')
                # Reconcile the credited split against credited damage. An
                # unowned burst is in neither total, but known bursts are in both.
                if expected is not None and burst:
                    expected += burst['settlement']
                covers_total = (expected is not None
                                and abs(skill_value + non_skill_value - expected)
                                <= max(0.01, abs(expected) * 1e-6))
                if complete and row['damage'] is not None:
                    initial = sum(self.initial_damage.get(source, 0.0) for source in sources)
                    # A same-frame event can arrive just after the opening mark.
                    # Its counter appears in the next snapshot. Only a shortfall
                    # against native damage proves missing coverage here.
                    native_damage = row['damage'] - initial
                    covers_total &= (skill_value + non_skill_value
                                     >= native_damage - max(0.01, abs(native_damage) * 1e-6))
                if scope_mismatch:
                    reason = DAMAGE_SCOPE_MISMATCH
                elif not skill_known:
                    reason = ('事件观察未建立' if not self.event_coverage else
                              '事件覆盖已结束' if not coverage_complete else
                              skill_incomplete_reason or '技能事件覆盖不完整')
                elif pre_observer_damage:
                    reason = PRE_OBSERVER_DAMAGE
                elif not covers_total:
                    reason = '技能窗口未覆盖全部伤害事件'
                elif not burst_complete:
                    reason = self.burst_unknown_reason
                else:
                    reason = None
                split_skill_overkill = self.skill_overkill.get(owner, 0.0)
                split_non_skill_overkill = self.non_skill_overkill.get(owner, 0.0)
                split_complete = (skill_known and covers_total and burst_complete
                                  and not scope_mismatch and not pre_observer_damage)
                effective_split_known = split_complete and overkill_known
                row['skill_damage'] = skill_value if skill_known and not scope_mismatch else None
                row['non_skill_damage'] = non_skill_value if skill_known and not scope_mismatch else None
                row['skill_damage_effective'] = (
                    max(0.0, skill_value - split_skill_overkill)
                    if effective_split_known else None)
                row['non_skill_damage_effective'] = (
                    max(0.0, non_skill_value - split_non_skill_overkill)
                    if effective_split_known else None)
            row['skill_seen'] = owner in self.skill_seen
            row['skill_window_count'] = (len(self.skill_windows.get(owner, ()))
                                         + sum(candidate == owner for candidate, _ in self.settlement_skill_active))
            timing_incomplete = owner in self._skill_observation_uncertainty
            row['skill_active_seconds'] = (
                self._skill_active_seconds(owner)
                if self.event_coverage and skill_incomplete_reason is None and not timing_incomplete else None)
            row['skill_coverage_complete'] = skill_known and not late_events and not timing_incomplete
            row['skill_split_complete'] = split_complete
            row['skill_split_effective_complete'] = effective_split_known
            row['skill_split_reason'] = reason
            observed = self.settled.get(owner)
            if self.settlement_enabled:
                for key in ('observed_skill_damage', 'observed_non_skill_damage', 'observed_skill_damage_effective', 'observed_non_skill_damage_effective'):
                    row[key] = lane[key]
            elif observed is not None:
                row.update(observed.observed_skill_result())
            if self.partial_damage_enabled:
                row['partial_damage'] = self._partial_row(owner, row, observed, burst, sources)
            if self.boss_damage is not None:
                row['boss_damage'], boss_reason = self._boss_row(owner, registered, row)
                if boss_reason:
                    boss_reasons.add(boss_reason)
            if self.settlement_enabled:
                if 'settled_damage' not in row:
                    row['settled_damage'] = self._settlement_row(owner)
                boss = row.setdefault('boss_damage', {})
                if 'settled_damage' not in boss:
                    boss['settled_damage'] = self._settlement_row(owner, boss=True)
            if pre_observer_damage:
                row.update(effective_damage=None, overkill_damage=None, overkill_damage_by_type=None,
                           overkill_coverage_complete=False, overkill_type_complete=False,
                           overkill_unknown_reason=PRE_OBSERVER_DAMAGE)
            row.update(self.deployment_lifecycle.result(owner, self.lifecycle_watermark,
                continuous=self.start_time == 0 and self.event_coverage_start == 0
                    and self.event_coverage and not self.event_coverage_broken,
                archived=self.end_time is not None, start_time=self.event_coverage_start))
            rows[owner] = row
        overkill_complete = bool(rows) and all(
            row['overkill_coverage_complete'] for row in rows.values())
        if self.settlement_enabled and self.damage_settlement_version >= 7:
            overkill_complete &= not self.settlement_unassigned['hits']
        result = {
            'session': self.session, 'game_time': self.now, 'ended': self.end_time is not None,
            'coverage_start': self.start_time,
            'totals_complete': all(row['counter_snapshot_complete'] for row in rows.values()),
            'rows': rows,
            'overkill_damage': (sum(row['overkill_damage'] for row in rows.values())
                                if overkill_complete else None),
            'overkill_coverage_complete': overkill_complete,
            'overkill_coverage_start': self.event_coverage_start if self.event_coverage else None,
            'overkill_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                      else self.end_time if self.event_coverage else None),
        }
        if self.boss_damage is not None:
            result['boss_damage_available'] = True
            result['boss_damage_reason'] = ' · '.join(sorted(boss_reasons))
        if self.settlement_enabled:
            result['settlement_unassigned'] = dict(self.settlement_unassigned)
            if self.damage_settlement_version >= 6:
                result['settlement_unassigned'].update(
                    boss_hits=self.settlement_boss_unassigned,
                    unscoped_hits=self.settlement_unscoped_hits)
            result.setdefault('boss_damage_available', True)
        if self.enemy_entry_start_frame is not None:
            result['enemy_entry'] = {
                'complete': not (self.enemy_entry_incomplete or self.event_coverage_broken),
                'start_frame': self.enemy_entry_start_frame,
                'events': [dict(entry) for entry in self.enemy_entries.values()],
            }
        if self.enemy_phases:
            result['enemy_phases'] = {
                'complete': not (self.enemy_phase_incomplete or self.event_coverage_broken),
                'instances': {uid: dict(phase) for uid, phase in self.enemy_phases.items()},
            }
        if self.enemy_state_start_frame is not None:
            events = []
            for event in self.enemy_state_events:
                snapshot = dict(event)
                if 'enemy_entry_attributes' in snapshot:
                    snapshot['enemy_entry_attributes'] = dict(snapshot['enemy_entry_attributes'])
                events.append(snapshot)
            result['enemy_state'] = {
                'complete': not (self.enemy_state_incomplete or self.event_coverage_broken),
                'start_frame': self.enemy_state_start_frame,
                'events': events,
            }
        if self.enemy_damage is not None:
            result['enemy_damage'] = {
                'complete': not (self.enemy_damage_incomplete or self.event_coverage_broken),
                'by_uid': {uid: dict(damage) for uid, damage in self.enemy_damage.items()},
            }
        return result
