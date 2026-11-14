"""Offline statistics core. Times are game seconds; inputs must be reliable.

Snapshots own damage/healing totals and sampled recent rates. Event callbacks own
engagement rates, shield absorption, overkill and formula damage pairs; never add
damage/heal events to totals. Overkill is a subset of the already inclusive
native damage total and is never subtracted from it. Source keys identify
distinct client statistic entries, not transient entities.
"""
from collections import deque
from math import isclose, isfinite

from boss_catalog import classify_boss
from damage_settlement import DamageSettlement, observed_amount
from source_classification import classify_unmapped_source

# Native DamageType values used by the verified 2.7.91 event probe.
# 0=NONE, 4=HEAL and unknown values cannot be attributed to a damage segment.
NATIVE_DAMAGE_TYPES = {1: 'physical', 2: 'arts', 3: 'true', 5: 'elemental'}
DAMAGE_TYPE_KEYS = ('physical', 'arts', 'elemental', 'true')
DAMAGE_SCOPE_MISMATCH = '伤害事件与原生累计的统计范围不一致'
ENEMY_STATE_KINDS = frozenset({
    'enemy_enter', 'enemy_hp', 'enemy_fake_death', 'enemy_reborn', 'enemy_finish',
})


class ObservedSkillDamage:
    def __init__(self):
        self.skill = self.non_skill = 0.0
        self.skill_effective = self.non_skill_effective = 0.0

    def hit(self, skill, amount, overkill):
        effective = max(0.0, amount - overkill) if overkill is not None else 0.0
        if skill:
            self.skill += amount
            self.skill_effective += effective
        else:
            self.non_skill += amount
            self.non_skill_effective += effective

    def result(self):
        has_damage = self.skill + self.non_skill > 0
        has_effective = self.skill_effective + self.non_skill_effective > 0
        return {
            'observed_skill_damage': self.skill if has_damage else None,
            'observed_non_skill_damage': self.non_skill if has_damage else None,
            'observed_skill_damage_effective': self.skill_effective if has_effective else None,
            'observed_non_skill_damage_effective': self.non_skill_effective if has_effective else None,
        }


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


class PartialDamage:
    def __init__(self):
        self.verified = self.effective = self.skill_verified = 0.0
        self.effective_by_type = {}
        self.active = self.active_end = 0.0
        self.recent = deque()

    def hit(self, when, amount, overkill, damage_type, *, split_observable, supplement=False):
        if amount <= 0:
            return
        if overkill is not None:
            effective = max(0.0, amount - overkill)
            self.verified += amount
            self.effective += effective
            if damage_type is not None and effective > 0:
                self.effective_by_type[damage_type] = (
                    self.effective_by_type.get(damage_type, 0.0) + effective)
            if split_observable:
                self.skill_verified += amount
            if effective > 0:
                self.active += when + 1.5 - max(when, self.active_end)
                self.active_end = when + 1.5
        else:
            if not supplement:
                return
            effective = 0.0
        self.recent.append((when, amount if overkill is not None else 0.0,
                            effective, amount if supplement else 0.0))
        while self.recent[0][0] < when - 5:
            self.recent.popleft()

    def result(self, observed, total, matched_mitigation, start, end, *, recent_native=None,
               include_start=False, reconcile_scope=False):
        skill_verified = observed.skill + observed.non_skill if observed is not None else 0.0
        active = max(0.0, self.active - max(0.0, self.active_end - end))
        recent = [hit for hit in self.recent
                  if (hit[0] > start or (include_start and hit[0] == start)) and hit[0] <= end]
        window_verified = sum(hit[1] for hit in recent)
        has_recent = window_verified > 0 and end > start
        recent_total = (recent_native + sum(hit[3] for hit in recent)
                        if recent_native is not None else None)
        recent_mismatch = (reconcile_scope and recent_total is not None
                           and exceeds_native_damage(window_verified, recent_total))
        has_recent &= not recent_mismatch
        effective_unverified = _unverified_share(self.verified, total)
        row = {
            'effective_damage': self.effective if self.verified > 0 else None,
            'effective_damage_unverified_share': effective_unverified,
            'skill_damage_unverified_share': _unverified_share(skill_verified, total),
            'skill_effective_damage_unverified_share': _unverified_share(self.skill_verified, total),
            'mitigation_unverified_share': _unverified_share(matched_mitigation, total),
            'recent_effective_damage': sum(hit[2] for hit in recent) / (end - start) if has_recent else None,
            'recent_effective_damage_window': (start, end) if has_recent else None,
            'recent_effective_damage_unverified_share': _unverified_share(window_verified, recent_total),
            'engagement_effective_damage': self.effective / active if active > 0 else None,
            'engagement_effective_damage_unverified_share': effective_unverified,
            'active_effective_damage': active if self.verified > 0 else None,
        }
        if reconcile_scope:
            row['effective_damage_by_type'] = dict(self.effective_by_type)
            if recent_mismatch:
                row['recent_damage_scope_reason'] = DAMAGE_SCOPE_MISMATCH
        return row


class BossDamage:
    """Target-scoped damage for one operator, including attributed summons."""

    def __init__(self):
        self.total = self.overkill = 0.0
        self.by_type = dict.fromkeys(DAMAGE_TYPE_KEYS, 0.0)
        self.overkill_by_type = dict.fromkeys(DAMAGE_TYPE_KEYS, 0.0)
        self.typed = self.overkill_known = True
        self.overkill_reason = None
        self.skill = self.skill_overkill = 0.0
        self.observed_split = ObservedSkillDamage()
        self.before = self.after = 0.0
        self.pairs = 0
        self.mitigation_known = True
        self.recent = deque()
        self.active = self.active_end = 0.0
        self.partial = None

    def hit(self, when, amount, kind, overkill, reason, skill, before, after, *, split_observable,
            partial_damage_enabled):
        self.total += amount
        self.typed &= kind is not None
        self.overkill_known &= overkill is not None
        if overkill is None:
            self.overkill_reason = reason
        excess = overkill or 0.0
        self.overkill += excess
        if kind is not None:
            self.by_type[kind] += amount
            self.overkill_by_type[kind] += excess
        if skill:
            self.skill += amount
            self.skill_overkill += excess
        if split_observable:
            self.observed_split.hit(skill, amount, overkill)
        if partial_damage_enabled:
            if self.partial is None:
                self.partial = PartialDamage()
            self.partial.hit(when, amount, overkill, kind, split_observable=split_observable)
        if before is not None:
            self.before += before
            self.after += after
            self.pairs += 1
        elif amount > 0:
            self.mitigation_known = False
        self.recent.append((when, kind, amount, excess))
        if amount > 0:
            self.active += when + 1.5 - max(when, self.active_end)
            self.active_end = when + 1.5

    def result(self, start, end, *, known, rate_known, skill_known, mitigation_known, reason,
               live=False, reconcile_scope=False):
        recent_start = max(start, end - 5)
        while self.recent and self.recent[0][0] < recent_start:
            self.recent.popleft()
        recent = [hit for hit in self.recent
                  if (hit[0] > recent_start or recent_start == start) and hit[0] <= end]
        active = max(0.0, self.active - max(0.0, self.active_end - end))
        duration = end - recent_start
        recent_known = rate_known and duration > 0
        effective_known = known and self.overkill_known
        effective_skill_known = skill_known and effective_known
        recent_by_type = dict.fromkeys(DAMAGE_TYPE_KEYS, 0.0)
        if recent_known and self.typed:
            for _, kind, amount, _ in recent:
                recent_by_type[kind] += amount / duration
        observed_rate = ((1 - self.after / self.before) * 100 if self.before > 0 else None)
        mitigation_complete = (known and mitigation_known and self.mitigation_known
                               and observed_rate is not None)
        row = {
            'damage': self.total if known else None,
            'observed_damage': self.total if known else None,
            'damage_by_type': dict(self.by_type) if known and self.typed else {},
            'types_available': known and self.typed,
            'types_stale': not known,
            'counter_snapshot_complete': known,
            'event_coverage_complete': known,
            'event_coverage_end': end,
            'recent_damage': sum(hit[2] for hit in recent) / duration if recent_known else None,
            'recent_damage_by_type': recent_by_type if recent_known and self.typed else {},
            'recent_damage_window': (recent_start, end) if recent_known else None,
            'engagement_damage': self.total / active if known and active > 0 else None,
            'active_damage': active if known else None,
            'overkill_damage': self.overkill if effective_known else None,
            'recent_overkill_damage': (sum(hit[3] for hit in recent)
                                       if recent_known and effective_known else None),
            'overkill_coverage_complete': effective_known,
            'overkill_type_complete': effective_known and self.typed,
            'overkill_damage_by_type': (dict(self.overkill_by_type)
                                       if effective_known and self.typed else None),
            'overkill_unknown_reason': None if effective_known else reason or self.overkill_reason,
            'effective_damage': max(0.0, self.total - self.overkill) if effective_known else None,
            'skill_damage': self.skill if skill_known else None,
            'non_skill_damage': self.total - self.skill if skill_known else None,
            'skill_damage_effective': max(0.0, self.skill - self.skill_overkill)
                                      if effective_skill_known else None,
            'non_skill_damage_effective': max(0.0, self.total - self.skill - self.overkill + self.skill_overkill)
                                          if effective_skill_known else None,
            'skill_split_complete': skill_known,
            'skill_split_effective_complete': effective_skill_known,
            'skill_split_reason': None if skill_known else reason or '技能事件覆盖不完整',
            **self.observed_split.result(),
            'mitigation_rate': observed_rate if mitigation_complete else None,
            'mitigation_before': self.before if mitigation_complete else None,
            'mitigation_after': self.after if mitigation_complete else None,
            'mitigation_unknown_reason': (None if mitigation_complete else reason or
                                          ('首领命中缺少同次命中的公式前后值' if self.total > 0
                                           else '尚无可核对的首领公式前后伤害')),
            'observed_mitigation_rate': observed_rate,
            'observed_mitigation_before': self.before if observed_rate is not None else None,
            'observed_mitigation_after': self.after if observed_rate is not None else None,
            'mitigation_matched_hits': self.pairs,
        }
        if self.partial is not None:
            partial = self.partial.result(self.observed_split, self.total if known else None,
                                          self.after, recent_start, end,
                                          recent_native=sum(hit[2] for hit in recent) if known else None,
                                          include_start=recent_start == start, reconcile_scope=reconcile_scope)
            partial.update(engagement_damage=self.total / active if active > 0 else None,
                           active_damage=active,
                           observed_boss_damage=self.total,
                           observed_boss_recent_damage=sum(hit[2] for hit in recent) / duration
                           if duration > 0 and live else None,
                           observed_boss_recent_window=(recent_start, end) if duration > 0 else None,
                           engagement_damage_unverified_share=0.0 if known and self.total > 0 else None)
            if reconcile_scope:
                partial['damage_by_type'] = dict(self.by_type)
            row['partial_damage'] = partial
        return row


class Statistics:
    def __init__(self, session, start_time, *, event_coverage=False):
        self._time(start_time)
        self.session = session
        self.start_time = start_time
        self.now = start_time
        self.end_time = None
        self.event_coverage = event_coverage
        self.event_coverage_end = None
        self.event_coverage_broken = False
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
        self.overkill = {}
        self.overkill_events = deque()
        self.overkill_incomplete = set()
        # Unit.OnTakeDamage is an incoming value before later settlement.
        # Track sources that only have incoming evidence without calling it
        # a confirmed settlement or treating the missing output as zero.
        self.incoming_damage_sources = set()
        self.settled_damage_sources = set()
        self.overkill_reason = {}
        # Match formula totals per source against its own cumulative counter
        # before aggregating a character and its summons.
        self.mitigation = {}
        # A sourceless burst is credited to an owner as a supplement to the
        # native source counters. Its formula pair follows the same rule.
        self.sourceless_burst_mitigation = {}
        self.mitigation_incomplete = set()
        self.mitigation_probe_incomplete = False
        self.mitigation_unattributed_burst = False
        # Elemental bar damage is not HP damage; only its source matters.
        self.last_elemental = {}
        self.burst = {}
        self.burst_unknown = False
        self.burst_unknown_reason = None
        # Per-type overkill is a separate subset. It stays unknown if a lethal
        # event has overkill but no recognised native damage type.
        self.overkill_by_type = {}
        self.overkill_type_incomplete = set()
        self.unknown_overkill_sources = set()
        self.all_overkill_unknown = False
        # A dropped damage/heal event makes the engagement numerator short.
        # That gap is reported per owner instead of silently lowering the rate.
        self.engagement_incomplete = set()
        self.unknown_engagement_sources = set()
        self.all_engagement_unknown = False
        # Shield-only uncertainty must not close damage/overkill coverage.
        self.shield_unknown = False
        # Active-skill windows and the observed damage split. A skill event is
        # supplemental; an unmapped source or a missing hook makes the split
        # unknown rather than closing the authoritative damage stream.
        self.skill_active = {}
        self.skill_windows = {}
        self.skill_observed = {}
        self.non_skill_observed = {}
        self.observed_skill_splits = {}
        self.skill_seen = set()
        self.skill_unknown = False
        self.skill_incomplete = {}
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
        self.skill_overkill = {}
        self.non_skill_overkill = {}
        self.boss_damage = None
        self.boss_event_end = None
        self.boss_observed_from = None
        self.boss_target_unknown = set()
        self.settlement_totals = {}
        self.boss_damage_incomplete = False
        self.partial_damage_enabled = False
        # Legacy captures replay with the policy that produced their recorded
        # values. Current observers declare the stricter settlement policy.
        self.reconcile_damage_scope = False
        self.settlement_enabled = False
        self.settlement_sides_known = False
        self.settlement_stream_incomplete = False
        self.settled = {}
        self.settled_boss = {}
        self.settlement_missing = {}
        self.settlement_boss_missing = {}
        self.settlement_bars = {}
        self.settlement_supplements = {}
        self.settlement_overkill = {}
        self.settlement_unassigned = {'hits': 0, 'hp': 0.0, 'shield': 0.0}
        self.settlement_unscoped = False
        self.settlement_skill_active = {}
        self.settlement_skill_incomplete = set()
        self.settlement_skill_unassigned = set()
        self.partial_damage = {}
        self.partial_watermark = None
        self.partial_unquantified_damage = set()
        self.partial_unattributed_damage = False

    def observe_boss_damage(self, *, complete):
        if self.boss_damage is None:
            self.boss_damage = {}
        self.boss_damage_incomplete |= not complete

    def _record_boss_damage(self, source, owner, when, target, amount, damage_type,
                            overkill, reason=None, before=None, after=None):
        if isinstance(target, str) and target.startswith(('char_', 'token_')):
            return
        if source is not None and (self.boss_damage is not None or self.partial_damage_enabled):
            self.settlement_totals[source] = self.settlement_totals.get(source, 0.0) + amount
        if self.boss_damage is None:
            return
        is_boss = classify_boss(target)
        if is_boss is None and amount > 0:
            self.boss_target_unknown.add(owner)
        if is_boss:
            meter = self.boss_damage.setdefault(owner, BossDamage())
            pair = self._mitigation_pair(amount, before, after)
            kind = NATIVE_DAMAGE_TYPES.get(damage_type) if type(damage_type) is int else None
            meter.hit(when, amount, kind, overkill, reason,
                      owner in self.skill_active, before if pair else None, after if pair else None,
                      split_observable=self._skill_hit_observable(owner),
                      partial_damage_enabled=self.partial_damage_enabled)

    def _skill_hit_observable(self, owner):
        window = self.skill_active.get(owner)
        if window is not None:
            return self._unconfirmed_skill_reason(window, live=True) is None
        return not self.skill_unknown and owner not in self.skill_incomplete

    def _observe_skill_hit(self, owner, amount, overkill):
        if self._skill_hit_observable(owner):
            if owner not in self.observed_skill_splits:
                self.observed_skill_splits[owner] = ObservedSkillDamage()
            self.observed_skill_splits[owner].hit(owner in self.skill_active, amount, overkill)

    def _partial_hit(self, owner, when, amount, overkill, damage_type, *, supplement=False):
        if self.partial_damage_enabled and amount > 0:
            if owner not in self.partial_damage:
                self.partial_damage[owner] = PartialDamage()
            key = NATIVE_DAMAGE_TYPES.get(damage_type) if type(damage_type) is int else None
            self.partial_damage[owner].hit(when, amount, overkill, key,
                                           split_observable=self._skill_hit_observable(owner),
                                           supplement=supplement)

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
            self.skill_incomplete[owner] = '技能事件的角色归属未确认'

    def mark_skill_unknown(self):
        self.skill_unknown = True

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

    def record_settlement_bar(self, target_uid, target, instance, state, owner, actor):
        if (not self._enemy_identity(target if target is not None else 'elemental_bar', target_uid)
                or not isinstance(instance, str) or not instance):
            if isinstance(instance, str) and instance:
                self.settlement_bars = {uid: bar for uid, bar in self.settlement_bars.items()
                                        if bar[1] != instance}
            else:
                self.settlement_bars.clear()
            return
        self.settlement_bars[target_uid] = (target, instance, state, owner, actor)

    def clear_settlement_bar(self, target_uid, kind):
        # Death can be reported before the damage callback returns. Retain
        # attribution until its burst consumes it, or the UID is admitted
        # again by entry or rebirth.
        if kind in ('enemy_enter', 'enemy_reborn') and isinstance(target_uid, str):
            self.settlement_bars.pop(target_uid, None)

    def record_damage_settlement(self, when, state, owner, actor, source, target, target_uid, instance,
                                 hp, shield, complete, kind, nominal, before, after):
        """Record each confirmed component, preserving independently verified neighbors."""
        valid_target = self._enemy_identity(target, target_uid) and target.startswith(('enemy_', 'trap_'))
        previous = self.enemy_damage.get(target_uid) if valid_target and self.enemy_damage is not None else None
        if previous and previous['eid'] != target:
            valid_target = False
        element = type(kind) is int and kind == 5
        if source is None and element and valid_target:
            bar = self.settlement_bars.pop(target_uid, None)
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
        if state != 'local' or owner is None or not valid_target:
            if hp > 0 or shield or not complete:
                self.settlement_unassigned['hits'] += 1
                self.settlement_unassigned['hp'] += hp
                self.settlement_unassigned['shield'] += shield or 0.0
                self.observe_enemy_damage(complete=False)
                if owner is not None:
                    self.settlement_missing[owner] = self.settlement_missing.get(owner, 0) + 1
                else:
                    self.settlement_unscoped = True
            return
        if hp > 0:
            self.record_enemy_damage(source, owner, kind, hp, target, target_uid, None, settled=True)
        if nominal is None or not self._mitigation_pair(nominal, before, after):
            before = after = None
        if source is None and element and nominal is not None:
            self.settlement_supplements[owner] = self.settlement_supplements.get(owner, 0.0) + nominal
            if complete:
                self.settlement_overkill[owner] = (
                    self.settlement_overkill.get(owner, 0.0) + max(0.0, nominal - hp - shield))
        skill = self._settlement_skill(owner, actor)
        args = (when, hp, shield, kind_name, complete, skill, nominal, before, after)
        meter = self.settled.get(owner)
        if meter is None:
            meter = self.settled[owner] = DamageSettlement()
        meter.hit(*args)
        boss = classify_boss(target)
        if boss is True:
            meter = self.settled_boss.get(owner)
            if meter is None:
                meter = self.settled_boss[owner] = DamageSettlement()
            meter.hit(*args)
        elif boss is None:
            self.settlement_boss_missing[owner] = self.settlement_boss_missing.get(owner, 0) + 1
        if source is not None and element:
            self.settlement_bars.pop(target_uid, None)

    def _settlement_skill(self, owner, actor):
        if (actor is None or self.skill_unknown or owner in self.settlement_skill_unassigned
                or (owner, actor) in self.settlement_skill_incomplete):
            return None
        window = self.settlement_skill_active.get((owner, actor))
        if window is not None and self._unconfirmed_skill_reason(window, live=True):
            return None
        return window is not None

    def _settlement_row(self, owner, *, boss=False):
        meters = self.settled_boss if boss else self.settled
        meter = meters.get(owner) or DamageSettlement()
        end = self.partial_watermark if self.partial_watermark is not None else self.start_time
        stream_complete = (self.event_coverage and not self.event_coverage_broken
                           and end == self.now and not self.settlement_stream_incomplete
                           and not self.settlement_unscoped and self.settlement_sides_known)
        missing = self.settlement_missing.get(owner, 0)
        if boss:
            missing += self.settlement_boss_missing.get(owner, 0)
        row = meter.result(self.start_time, self.now, end, stream_complete=stream_complete,
                           missing_hits=missing, live=self.end_time is None, boss=boss)
        if not self.settlement_sides_known:
            row['reason'] = '当前玩家阵营未确认'
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
                or not self.start_time <= game_time <= self.now):
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
                or not self.start_time <= game_time <= self.now
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
                           finish_reason=None, enemy_max_hp=None):
        if self.enemy_state_start_frame is None:
            return
        if (kind not in ENEMY_STATE_KINDS
                or not self._enemy_identity(eid, instance_uid)
                or source is not None
                or (kind != 'enemy_reborn' and (amount != 0 or isinstance(amount, bool)))
                or not self.start_time <= game_time <= self.now
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
                self.skill_incomplete[owner] = '技能事件的角色归属未确认'
        if (kind in ('elemental_bar', 'elemental_burst')
                or kind == 'enemy_damage' and damage_type == 5) and target_instance:
            self.last_elemental.pop(target_instance, None)

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
        if self.partial_damage_enabled:
            self.partial_watermark = game_time
        if self.boss_damage is not None:
            if self.boss_observed_from is None:
                self.boss_observed_from = game_time
            self.boss_event_end = game_time

    def _skill_active_seconds(self, owner):
        end = (self.event_coverage_end if self.event_coverage_end is not None
               else self.now)
        total = 0.0
        for window in self.skill_windows.get(owner, ()):
            start = window.get('start')
            stop = window.get('end')
            if self._optional_number(start) and self._optional_number(stop):
                total += max(0.0, min(stop, end) - start)
        active = self.skill_active.get(owner)
        if active is not None and self._optional_number(active.get('start')):
            total += max(0.0, end - active['start'])
        return total

    @staticmethod
    def _unconfirmed_skill_reason(window, *, live=False):
        if window is None or ('cast_completed_at' not in window and 'movement_at' not in window):
            return None
        if 'skchr_angel2_3' in (window['key'], window['id']):
            return '未观察到弹药技能的实际结束'
        if live and window['effect_started']:
            # The observed portion of a confirmed activation is a usable window.
            return None
        return '未观察到技能效果的实际结束'

    def _close_skill_window(self, owner, game_time, reason, *, confirmed):
        window = self.skill_active.pop(owner)
        if not confirmed:
            unconfirmed = self._unconfirmed_skill_reason(window)
            if unconfirmed is not None:
                self.skill_incomplete[owner] = unconfirmed
        window['end'] = game_time
        window['reason'] = reason
        self.skill_windows.setdefault(owner, []).append(window)

    def _close_skill_windows(self, game_time, *, battle_end):
        reason = 'battle_end' if battle_end else 'coverage_end'
        for owner, window in tuple(self.skill_active.items()):
            # A reliable battle end clips any window that was valid while open.
            confirmed = battle_end and self._unconfirmed_skill_reason(window, live=True) is None
            self._close_skill_window(owner, game_time, reason, confirmed=confirmed)

    def _record_skill_transition(self, owner, source, kind, game_time, *,
                                 key, skill_id, name, method, reason):
        self._apply_skill_transition(
            self.skill_active, owner, source, kind, game_time, key=key, skill_id=skill_id,
            name=name, method=method, reason=reason, close=self._close_skill_window)

    def record_settlement_skill(self, owner, actor, source, kind, game_time, *,
                                key, skill_id, name, method, reason):
        def close(identity, _time, _reason, *, confirmed):
            window = self.settlement_skill_active.pop(identity)
            if not confirmed and self._unconfirmed_skill_reason(window):
                self.settlement_skill_incomplete.add(identity)
        self._apply_skill_transition(
            self.settlement_skill_active, (owner, actor), source, kind, game_time,
            key=key, skill_id=skill_id, name=name, method=method, reason=reason, close=close)

    def mark_settlement_skill_unassigned(self, source, method, reason):
        owner = self.owners.get(source)
        if owner is None:
            return
        if method == 'BattleController.OnCharacterFinished':
            for identity, window in self.settlement_skill_active.items():
                if (identity[0] == owner and reason == 11
                        and 'skchr_ulpia_3' in (window['key'], window['id'])):
                    self.settlement_skill_incomplete.add(identity)
            return
        self.settlement_skill_unassigned.add(owner)

    def _apply_skill_transition(self, active, identity_key, source, kind, game_time, *,
                                key, skill_id, name, method, reason, close):
        window = active.get(identity_key)
        identity = {key, skill_id} - {None}
        active_identity = {window['key'], window['id']} - {None} if window is not None else set()
        same_skill = not active_identity or not identity or not active_identity.isdisjoint(identity)
        if kind == 'skill_start':
            if active is self.skill_active:
                self.skill_seen.add(identity_key)
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
                    'name': name, 'effect_started': effect_started}
            elif source == window['source'] and same_skill and effect_started:
                window['effect_started'] = True
            return
        if window is None or source != window['source'] or not same_skill:
            return
        if method == 'Character.OnAfterSkill' and type(reason) is int and reason == 1:
            # Ability.FinishReason.INTERRUPTED can end the cast action while
            # the skill's damage effect is still active.
            window.setdefault('cast_completed_at', game_time)
            return
        if method == 'BattleController.OnCharacterFinished' and reason == 11:
            # Entity.FinishReason.MOVE_LIKE_RESPAWN_SELF reports both Ulpian
            # S3's outward move on the cast-completion frame and his return.
            if 'skchr_ulpia_3' in active_identity:
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
                                        'history': deque([(self.end_time, dict(values))]),
                                        'continuous': True, 'segment_start': self.end_time,
                                        'totals_valid': True, 'totals_time': self.end_time}
                changed = True
            else:
                prior['totals_valid'] = True
                prior['totals_time'] = self.end_time
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
        pairs, key = (self.sourceless_burst_mitigation, owner) if source is None else (self.mitigation, source)
        before_total, after_total, hits = pairs.get(key, (0.0, 0.0, 0))
        totals = before_total + before, after_total + after
        if not all(isfinite(value) for value in totals):
            self.mitigation_incomplete.add(owner)
            return
        pairs[key] = (*totals, hits + 1)

    def _mitigation_result(self, owner, registered, *, complete):
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
              verified_skill_end_owner=None, skill_method=None,
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
                self.skill_unknown = True
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
                   skill_key, skill_id, skill_name, skill_reason, skill_method,
                   mitigation_before, mitigation_after, settled_after_nested_kill)
        if event_id in self.events:
            if self.events[event_id] != payload:
                raise ValueError('conflicting event ID')
            return
        if self.end_time is not None or not self.start_time <= game_time <= self.now or game_time < self.last_event_time:
            raise ValueError('event outside ordered observation')
        self.events[event_id] = payload
        self.last_event_time = game_time
        if kind == 'elemental_bar':
            if (not target_instance or owner is None
                    or (self.partial_damage_enabled and not isinstance(target_instance, str))):
                if self.partial_damage_enabled:
                    # The next source-null burst could belong to this bar.
                    # Without the target identity no previous bar is reliable.
                    self.last_elemental.clear()
                    self.burst_unknown = True
                    self.burst_unknown_reason = '元素损伤缺少可靠的目标身份'
                    return
                raise ValueError('elemental bar needs a source and target instance')
            self.last_elemental[target_instance] = owner
            return
        if kind == 'elemental_burst':
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
                self.last_elemental.pop(target_instance, None)
                return
            actual = -real_delta
            if actual > amount + tolerance:
                self._reject_burst_evidence('elemental burst actual exceeds settlement', attributed, target, amount,
                                            sourceless=source is None)
                self.last_elemental.pop(target_instance, None)
                return
            friendly = isinstance(target, str) and target.startswith(('char_', 'token_'))
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
            entry = self.burst.setdefault(attributed, {
                'actual': 0.0, 'overkill': 0.0, 'settlement': 0.0, 'count': 0,
                'added_settlement': 0.0,
            })
            burst_overkill = max(0.0, amount - actual)
            self._record_boss_damage(source, attributed, game_time, target, amount, 5, burst_overkill,
                                     before=mitigation_before, after=mitigation_after)
            entry['actual'] += actual
            entry['overkill'] += burst_overkill
            entry['settlement'] += amount
            entry['count'] += 1
            # The client already includes explicitly sourced burst settlement
            # in that source's native counter. Only attributed sourceless
            # settlement needs adding to native totals.
            if source is None:
                entry['added_settlement'] += amount
            bucket = (self.skill_observed if attributed in self.skill_active
                      else self.non_skill_observed)
            bucket[attributed] = bucket.get(attributed, 0.0) + amount
            self._observe_skill_hit(attributed, amount, burst_overkill)
            if not friendly:
                self._partial_hit(attributed, game_time, amount, burst_overkill, 5, supplement=source is None)
            if burst_overkill > 0:
                overkill_bucket = (self.skill_overkill
                                   if attributed in self.skill_active
                                   else self.non_skill_overkill)
                overkill_bucket[attributed] = (
                    overkill_bucket.get(attributed, 0.0) + burst_overkill)
            self.last_elemental.pop(target_instance, None)
            return
        if kind in ('skill_start', 'skill_end'):
            self._record_skill_transition(
                owner, source, kind, game_time, key=skill_key, skill_id=skill_id,
                name=skill_name, method=skill_method or target_instance, reason=skill_reason)
            return
        if kind == 'hp_damage':
            friendly = (isinstance(target, str)
                        and target.startswith(('char_', 'token_')))
            if amount > 0 and not friendly and damage_type != 5:
                self.incoming_damage_sources.add(source)
            return
        if kind == 'damage':
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
            friendly = isinstance(target, str) and target.startswith(('char_', 'token_'))
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
                    self.last_elemental.pop(target_instance, None)
                self.overkill[owner] = self.overkill.get(owner, 0.0) + overkill
                if overkill > 0:
                    self.overkill_events.append((game_time, owner, overkill))
                    type_key = NATIVE_DAMAGE_TYPES.get(damage_type)
                    if type_key is None:
                        self.overkill_type_incomplete.add(owner)
                    else:
                        per_type = self.overkill_by_type.setdefault(owner, {})
                        per_type[type_key] = per_type.get(type_key, 0.0) + overkill
                    bucket = (self.skill_overkill if owner in self.skill_active
                              else self.non_skill_overkill)
                    bucket[owner] = bucket.get(owner, 0.0) + overkill
            else:
                self.overkill_incomplete.add(owner)
                self.overkill_reason[owner] = reason
            if amount > 0 and not friendly:
                bucket = (self.skill_observed if owner in self.skill_active
                          else self.non_skill_observed)
                bucket[owner] = bucket.get(owner, 0.0) + amount
                self._observe_skill_hit(owner, amount, overkill if known else None)
                self._partial_hit(owner, game_time, amount, overkill if known else None, damage_type)
        if kind == 'shield_absorbed':
            self.shields[owner] = self.shields.get(owner, 0) + amount
        elif kind in ('damage', 'healing') and amount > 0:
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
                if intervals and game_time <= intervals[-1][1]:
                    intervals[-1] = (intervals[-1][0], max(intervals[-1][1], game_time + 1.5))
                else:
                    intervals.append((game_time, game_time + 1.5))

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
        meter = self.boss_damage.get(owner)
        if not registered and meter is None and reason is None:
            reason = '尚无可核对的原生累计'
        meter = meter or BossDamage()
        observed_from = self.start_time if self.boss_observed_from is None else self.boss_observed_from
        live = self.end_time is None and self.event_coverage_end is None
        scoped = meter.result(
            observed_from, end if end is not None else observed_from,
            known=reason is None, rate_known=reason is None and live, live=live,
            skill_known=reason is None and row['skill_coverage_complete'],
            mitigation_known=not self.mitigation_probe_incomplete, reason=reason,
            reconcile_scope=self.reconcile_damage_scope)
        return scoped, reason

    def _partial_row(self, owner, row, observed, burst, sources):
        end = max(self.last_event_time, self.partial_watermark or self.start_time)
        start = max(self.start_time, self.now - 5)
        comparable_damage = (row['counter_snapshot_complete'] and not self.partial_unattributed_damage
                             and owner not in self.partial_unquantified_damage)
        total = row['damage'] if comparable_damage else None
        recent_native = (row['recent_damage'] * (self.now - start)
                         if comparable_damage and end == self.now
                         and row['recent_damage_window'] == (start, self.now)
                         and all(any(when == start for when, _ in self.sources[source]['history'])
                                 for source in sources)
                         else None)
        meter = self.partial_damage.get(owner) or PartialDamage()
        partial = meter.result(observed, total, row['observed_mitigation_after'] or 0.0, start, end,
                               recent_native=recent_native, include_start=start == self.start_time,
                               reconcile_scope=self.reconcile_damage_scope)
        absorbed = self.shields.get(owner)
        partial['observed_shield_absorbed'] = absorbed
        partial['shield_observation_window'] = (self.start_time, end) if absorbed is not None else None
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
        owners = set(self.owners.values())
        if self.settlement_enabled:
            owners.update(self.settled)
            owners.update(self.settlement_missing)
        rows = {}
        boss_reasons = set()
        for owner in owners:
            sources = [s for s, o in self.owners.items() if o == owner and s in self.sources]
            registered = [s for s, o in self.owners.items() if o == owner]
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
            overkill_known = (coverage_complete
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
            row = {'coverage_start': self.start_time,
                   'counter_snapshot_complete': complete,
                   'shield_coverage_start': self.start_time if self.event_coverage else None,
                   'event_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                          else self.end_time if self.event_coverage else None),
                   'event_coverage_complete': coverage_complete,
                   'shield_absorbed': (self.shields.get(owner, 0)
                                       if self.event_coverage and not self.shield_unknown else None),
                   'shield_unknown': self.shield_unknown,
                   'overkill_damage': (self.overkill.get(owner, 0.0) if overkill_known else None),
                   'overkill_damage_by_type': ({key: self.overkill_by_type.get(owner, {}).get(key, 0.0)
                                                for key in DAMAGE_TYPE_KEYS}
                                               if overkill_type_known else None),
                   'overkill_type_complete': overkill_type_known,
                   'overkill_unknown_reason': overkill_unknown_reason,
                   'overkill_coverage_complete': overkill_known,
                   'overkill_coverage_start': self.start_time if self.event_coverage else None,
                   'overkill_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                             else self.end_time if self.event_coverage else None)}
            row.update(self._mitigation_result(owner, registered, complete=complete))
            if scope_mismatch:
                row['damage_scope_reason'] = DAMAGE_SCOPE_MISMATCH
                row['mitigation_unknown_reason'] = DAMAGE_SCOPE_MISMATCH
            burst = self.burst.get(owner)
            added_settlement = (self.settlement_supplements.get(owner) if self.settlement_enabled
                                else burst['added_settlement'] if burst else None)
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
                    row['recent_overkill_damage'] = (
                        sum(value for when, owner_key, value in self.overkill_events
                            if owner_key == owner and start <= when <= self.now)
                        if overkill_known and row['recent_damage_window'] is not None
                        else None)
                state = self.engagement.get((owner, kind))
                if state:
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
                if not engagement_known:
                    # A dropped event leaves the numerator and its interval
                    # short; publish unknown instead of a low observed rate.
                    row['engagement_' + kind] = None
                    row['active_' + kind] = None
                    row['observed_' + kind] = None
            # Unowned sourceless bursts are outside the credited damage total.
            # Adjust the native total and overkill with the same attributed evidence.
            if added_settlement is not None and row['damage'] is not None:
                row['damage'] += added_settlement
            if row['overkill_damage'] is not None:
                burst_overkill = (self.settlement_overkill.get(owner, 0.0) if self.settlement_enabled
                                  else burst['overkill'] if burst else 0.0)
                row['overkill_damage'] += burst_overkill
                types = row['overkill_damage_by_type']
                if types is not None:
                    types['elemental'] += burst_overkill
            if row['damage'] is not None and row['overkill_damage'] is not None:
                row['effective_damage'] = row['damage'] - row['overkill_damage']
            else:
                row['effective_damage'] = None
            skill_incomplete_reason = (
                self.skill_incomplete.get(owner)
                or self._unconfirmed_skill_reason(self.skill_active.get(owner), live=True))
            skill_known = (coverage_complete and not self.skill_unknown
                           and skill_incomplete_reason is None)
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
            elif not covers_total:
                reason = '技能窗口未覆盖全部伤害事件'
            elif not burst_complete:
                reason = self.burst_unknown_reason
            else:
                reason = None
            split_skill_overkill = self.skill_overkill.get(owner, 0.0)
            split_non_skill_overkill = self.non_skill_overkill.get(owner, 0.0)
            split_complete = skill_known and covers_total and burst_complete and not scope_mismatch
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
                                         + (1 if owner in self.skill_active else 0))
            row['skill_active_seconds'] = (
                self._skill_active_seconds(owner)
                if self.event_coverage and skill_incomplete_reason is None else None)
            row['skill_coverage_complete'] = skill_known
            row['skill_split_complete'] = split_complete
            row['skill_split_effective_complete'] = effective_split_known
            row['skill_split_reason'] = reason
            observed = self.observed_skill_splits.get(owner)
            if observed is not None:
                row.update(observed.result())
            if self.partial_damage_enabled:
                row['partial_damage'] = self._partial_row(owner, row, observed, burst, sources)
            if self.boss_damage is not None:
                row['boss_damage'], boss_reason = self._boss_row(owner, registered, row)
                if boss_reason:
                    boss_reasons.add(boss_reason)
            if self.settlement_enabled:
                row['settled_damage'] = self._settlement_row(owner)
                row.setdefault('boss_damage', {})['settled_damage'] = self._settlement_row(owner, boss=True)
            rows[owner] = row
        overkill_complete = bool(rows) and all(
            row['overkill_coverage_complete'] for row in rows.values())
        result = {
            'session': self.session, 'game_time': self.now, 'ended': self.end_time is not None,
            'coverage_start': self.start_time,
            'totals_complete': all(row['counter_snapshot_complete'] for row in rows.values()),
            'rows': rows,
            'overkill_damage': (sum(row['overkill_damage'] for row in rows.values())
                                if overkill_complete else None),
            'overkill_coverage_complete': overkill_complete,
            'overkill_coverage_start': self.start_time if self.event_coverage else None,
            'overkill_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                      else self.end_time if self.event_coverage else None),
        }
        if self.boss_damage is not None:
            result['boss_damage_available'] = True
            result['boss_damage_reason'] = ' · '.join(sorted(boss_reasons))
        if self.settlement_enabled:
            result['settlement_unassigned'] = dict(self.settlement_unassigned)
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
