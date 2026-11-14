"""Per-hit evidence for attributed enemy HP damage and shield consumption."""
from collections import deque
from math import isfinite


def observed_amount(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


class DamageSettlement:
    def __init__(self):
        self.hp = self.shield = self.raw = 0.0
        self.by_type = {}
        self.raw_by_type = {}
        self.hits = self.unverified = self.raw_hits = 0
        self.skill = self.non_skill = self.raw_skill = self.raw_non_skill = 0.0
        self.skill_hits = self.raw_skill_hits = self.complete_skill_hits = 0
        self.before = self.after = 0.0
        self.formula_hits = 0
        self.active = self.active_end = self.raw_active = self.raw_active_end = 0.0
        self.recent = deque()

    def hit(self, when, hp, shield, kind, complete, skill, nominal, before, after):
        effective = hp + (shield or 0.0)
        if complete and effective == 0 and nominal is None:
            return
        self.hits += 1
        self.unverified += not complete
        self.hp += hp
        self.shield += shield or 0.0
        if kind is not None and effective > 0:
            self.by_type[kind] = self.by_type.get(kind, 0.0) + effective
        if effective > 0:
            self.active += when + 1.5 - max(when, self.active_end)
            self.active_end = when + 1.5
            if skill is not None:
                if skill:
                    self.skill += effective
                else:
                    self.non_skill += effective
        if skill is not None and (effective > 0 or complete):
            self.skill_hits += 1
            self.complete_skill_hits += complete
        if nominal is not None:
            self.raw_hits += 1
            self.raw += nominal
            if kind is not None and nominal > 0:
                self.raw_by_type[kind] = self.raw_by_type.get(kind, 0.0) + nominal
            if nominal > 0:
                self.raw_active += when + 1.5 - max(when, self.raw_active_end)
                self.raw_active_end = when + 1.5
            if skill is not None:
                self.raw_skill_hits += 1
                if skill:
                    self.raw_skill += nominal
                else:
                    self.raw_non_skill += nominal
        if before is not None:
            self.before += before
            self.after += after
            self.formula_hits += 1
        self.recent.append((when, effective, nominal, complete))
        while self.recent[0][0] < when - 5:
            self.recent.popleft()

    def result(self, start, now, end, *, stream_complete, missing_hits=0, live=True, boss=False):
        begin = max(start, now - 5)
        recent = [hit for hit in self.recent
                  if (begin < hit[0] or begin == start == hit[0]) and hit[0] <= end]
        effective = self.hp + self.shield
        complete = stream_complete and self.unverified == 0 and missing_hits == 0
        issue = ('部分命中的扣血或扣盾证据不完整' if self.unverified else
                 '部分命中的来源或目标身份未确认' if missing_hits else
                 '结算事件覆盖不完整' if not stream_complete else None)
        active = self.active - max(0.0, self.active_end - end)
        raw_active = self.raw_active - max(0.0, self.raw_active_end - end)
        duration = end - begin
        recent_complete = stream_complete and missing_hits == 0 and all(h[3] for h in recent)
        hits = self.hits + missing_hits
        raw_complete = stream_complete and self.raw_hits == hits
        raw_recent_complete = stream_complete and missing_hits == 0 and all(h[2] is not None for h in recent)
        has_window = live and duration > 0
        recent_effective = sum(h[1] for h in recent)
        has_effective_window = has_window and (recent_effective > 0 or recent_complete)
        has_raw_window = has_window and (any(h[2] is not None for h in recent) or raw_recent_complete)

        def missing_share(accepted):
            return (hits - accepted) / hits if stream_complete and hits else None

        mitigation_complete = (stream_complete and missing_hits == 0 and
                               self.formula_hits > 0 and self.formula_hits == self.hits)
        share = missing_share(self.hits - self.unverified)
        return {
            'complete': complete, 'reason': issue, 'known_hp_damage': self.hp,
            'known_shield_damage': self.shield, 'observed_hits': hits,
            'unverified_hits': self.unverified + missing_hits, 'unverified_share': share,
            'effective_damage': effective if effective > 0 or complete else None,
            'effective_damage_by_type': dict(self.by_type),
            'recent_effective_damage': recent_effective / duration if has_effective_window else None,
            'recent_effective_damage_window': (begin, end) if has_effective_window else None,
            'recent_effective_damage_complete': recent_complete and has_window,
            'recent_effective_damage_unverified_share': (
                sum(not h[3] for h in recent) / len(recent) if has_window and recent
                and stream_complete and not missing_hits else None),
            'engagement_effective_damage': effective / active if active > 0 else None,
            'active_effective_damage': active if effective > 0 else None,
            'engagement_effective_damage_unverified_share': share,
            'engagement_damage': self.raw / raw_active if raw_active > 0 else None,
            'active_damage': raw_active if self.raw > 0 else None,
            'engagement_damage_complete': raw_complete,
            'engagement_damage_unverified_share': missing_share(self.raw_hits),
            'observed_boss_damage': self.raw if boss and (self.raw_hits or raw_complete) else None,
            'observed_boss_damage_complete': boss and raw_complete,
            'observed_boss_recent_damage': (sum(h[2] or 0.0 for h in recent) / duration
                                            if boss and has_raw_window else None),
            'observed_boss_recent_damage_complete': boss and raw_recent_complete and has_window,
            'observed_boss_recent_window': (begin, end) if boss and has_raw_window else None,
            'damage_by_type': dict(self.raw_by_type) if boss else {},
            'observed_skill_damage_effective': self.skill if self.skill_hits else None,
            'observed_non_skill_damage_effective': self.non_skill if self.skill_hits else None,
            'skill_split_effective_complete': complete and self.skill_hits == self.hits and self.hits > 0,
            'skill_effective_damage_unverified_share': missing_share(self.complete_skill_hits),
            'observed_skill_damage': self.raw_skill if self.raw_skill_hits else None,
            'observed_non_skill_damage': self.raw_non_skill if self.raw_skill_hits else None,
            'skill_split_complete': stream_complete and hits > 0 and self.raw_skill_hits == hits,
            'skill_damage_unverified_share': missing_share(self.raw_skill_hits),
            'mitigation_complete': mitigation_complete,
            'mitigation_reason': None if mitigation_complete else '部分命中缺少同一次结算的公式前后值或归属',
            'mitigation_matched_hits': self.formula_hits,
            'observed_mitigation_before': self.before if self.before else None,
            'observed_mitigation_after': self.after if self.before else None,
            'observed_mitigation_rate': (1 - self.after / self.before) * 100 if self.before else None,
            'mitigation_unverified_share': missing_share(self.formula_hits),
        }
