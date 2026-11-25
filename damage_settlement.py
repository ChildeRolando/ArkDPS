"""Per-hit evidence for attributed enemy HP, shield and overkill damage."""
from collections import deque
from math import isfinite, isclose


def observed_amount(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def extend_activity(previous_end, when):
    """Extend one lane's ordered 1.5-second engagement union."""
    end = when + 1.5
    return end, max(0.0, end - max(when, previous_end)), when <= previous_end


class DamageSettlement:
    def __init__(self):
        self.hp = self.shield = self.raw = 0.0
        self.by_type = {}
        self.raw_by_type = {}
        self.hits = self.unverified = self.raw_hits = 0
        self.overkill = self.overkill_base = 0.0
        self.overkill_hits = 0
        self.overkill_by_type = {}
        self.skill = self.non_skill = self.raw_skill = self.raw_non_skill = 0.0
        self.initial_skill_unknown_hits = 0
        self.skill_hits = self.raw_skill_hits = self.complete_skill_hits = 0
        self.before = self.after = 0.0
        self.formula_hits = 0
        self.active = self.active_end = self.raw_active = self.raw_active_end = 0.0
        self.recent = deque()
        self.elemental = None
        self.source_nominal = {}
        self.source_pairs = {}
        self.legacy_effective = 0.0
        self.raw_typed_hits = 0
        self.classified_overkill_base = 0.0
        self.raw_skill_overkill = 0.0
        self.legacy_overkill_reason = None

    def hit(self, when, hp, shield, kind, complete, skill, nominal, before, after, *, overkill=None, initial_skill_unknown=False, known_effective=None, supplement=False, evidence_reason=None, source=None):
        effective = hp + (shield or 0.0)
        if known_effective is not None:
            effective = known_effective
            self.legacy_effective += max(0.0, known_effective - hp - (shield or 0.0))
        if evidence_reason is not None:
            self.legacy_overkill_reason = evidence_reason
        if kind == 'elemental':
            if self.elemental is None:
                self.elemental = dict(actual=0.0, overkill=0.0, settlement=0.0, count=0)
            self.elemental['actual'] += hp
            self.elemental['overkill'] += overkill or 0.0
            self.elemental['settlement'] += nominal or 0.0
            self.elemental['count'] += 1
        if complete and effective == 0 and nominal is None:
            return
        self.hits += 1
        self.initial_skill_unknown_hits += initial_skill_unknown
        self.unverified += not complete
        self.hp += hp
        self.shield += shield or 0.0
        if kind is not None and effective > 0:
            self.by_type[kind] = self.by_type.get(kind, 0.0) + effective
        if overkill is not None:
            self.overkill_hits += 1
            self.overkill += overkill
            self.overkill_base += effective + overkill
            if skill is not None:
                self.classified_overkill_base += effective + overkill
                if skill:
                    self.raw_skill_overkill += overkill
            if overkill > 0:
                self.overkill_by_type[kind] = self.overkill_by_type.get(kind, 0.0) + overkill
        if effective > 0:
            self.active_end, added, _ = extend_activity(self.active_end, when)
            self.active += added
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
            if source is not None:
                self.source_nominal[source] = self.source_nominal.get(source, 0.0) + nominal
            self.raw_typed_hits += kind is not None
            if kind is not None and nominal > 0:
                self.raw_by_type[kind] = self.raw_by_type.get(kind, 0.0) + nominal
            if nominal > 0:
                self.raw_active_end, added, _ = extend_activity(self.raw_active_end, when)
                self.raw_active += added
            if skill is not None:
                self.raw_skill_hits += 1
                if skill:
                    self.raw_skill += nominal
                else:
                    self.raw_non_skill += nominal
        if before is not None:
            previous = self.source_pairs.get(source, (0.0, 0.0, 0))
            values = previous[0] + before, previous[1] + after
            if all(isfinite(value) for value in (*values, self.before + before, self.after + after)):
                self.before += before
                self.after += after
                self.formula_hits += 1
                self.source_pairs[source] = (*values, previous[2] + 1)
        self.recent.append((when, effective, nominal, complete, kind, overkill, supplement))
        while self.recent[0][0] < when - 5:
            self.recent.popleft()

    def result(self, start, now, end, *, stream_complete, missing_hits=0, live=True, boss=False,
               include_overkill=False):
        begin = max(start, now - 5)
        recent = [hit for hit in self.recent
                  if (begin < hit[0] or begin == start == hit[0]) and hit[0] <= end]
        effective = self.hp + self.shield + self.legacy_effective
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
        recent_raw = sum(h[2] or 0.0 for h in recent)
        observed_duration = end - start

        def missing_share(accepted):
            return (hits - accepted) / hits if stream_complete and hits else None

        mitigation_complete = (stream_complete and missing_hits == 0 and
                               self.formula_hits > 0 and self.formula_hits == self.hits)
        share = missing_share(self.hits - self.unverified)
        result = {
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
            # Event observations are a separate lane from native cumulative
            # counters. An empty ledger is not evidence of zero output.
            'nominal_hits': self.raw_hits,
            'observed_damage': self.raw if self.raw_hits else None,
            'observed_damage_complete': raw_complete and bool(self.raw_hits),
            'observed_dps': (self.raw / observed_duration
                             if self.raw_hits and observed_duration > 0 else None),
            'observed_damage_window': (start, end) if self.raw_hits else None,
            'observed_recent_damage': recent_raw / duration if has_raw_window else None,
            'observed_recent_damage_complete': raw_recent_complete and has_window,
            'observed_recent_damage_window': (begin, end) if has_raw_window else None,
            'observed_boss_damage': self.raw if boss and (self.raw_hits or raw_complete) else None,
            'observed_boss_damage_complete': boss and raw_complete,
            'observed_boss_recent_damage': (recent_raw / duration
                                            if boss and has_raw_window else None),
            'observed_boss_recent_damage_complete': boss and raw_recent_complete and has_window,
            'observed_boss_recent_window': (begin, end) if boss and has_raw_window else None,
            'damage_by_type': dict(self.raw_by_type) if boss else {},
            'skill_initial_unknown_hits': self.initial_skill_unknown_hits,
            'skill_unclassified_hits': hits - self.raw_skill_hits,
            'skill_effective_unclassified_hits': hits - self.complete_skill_hits,
            'observed_skill_damage_effective': self.skill if self.skill_hits else None,
            'observed_non_skill_damage_effective': self.non_skill if self.skill_hits else None,
            'skill_split_effective_complete': complete and self.skill_hits == self.hits and self.hits > 0,
            'skill_effective_damage_unverified_share': missing_share(self.complete_skill_hits),
            'observed_skill_damage': self.raw_skill if self.raw_skill_hits else None,
            'observed_non_skill_damage': self.raw_non_skill if self.raw_skill_hits else None,
            'skill_split_complete': stream_complete and hits > 0 and self.raw_skill_hits == hits,
            'skill_damage_unverified_share': missing_share(self.raw_skill_hits),
            'mitigation_complete': mitigation_complete,
            'mitigation_reason': (None if mitigation_complete else
                '已记录命中的公式前后值均已配对，整局事件覆盖未确认'
                if self.formula_hits == hits and hits > 0 and not stream_complete else
                '部分命中缺少同一次结算的公式前后值或归属'),
            'mitigation_matched_hits': self.formula_hits,
            'observed_mitigation_before': self.before if self.before else None,
            'observed_mitigation_after': self.after if self.before else None,
            'observed_mitigation_rate': (1 - self.after / self.before) * 100 if self.before else None,
            'mitigation_unverified_share': missing_share(self.formula_hits),
        }
        if include_overkill:
            overkill_complete = complete and self.overkill_hits == hits
            result.update(
                overkill_damage=self.overkill if overkill_complete else None,
                observed_overkill_damage=self.overkill if self.overkill_hits else None,
                overkill_base_damage=self.overkill_base if overkill_complete or self.overkill_hits else None,
                overkill_coverage_complete=overkill_complete,
                overkill_matched_hits=self.overkill_hits,
                overkill_unverified_share=missing_share(self.overkill_hits),
                overkill_unknown_reason=None if overkill_complete else (
                    issue or '部分命中缺少同一次结算的过量证据'),
                overkill_damage_by_type=dict(self.overkill_by_type) if overkill_complete else None,
            )
        return result

    def observed_skill_result(self):
        nominal = self.raw_skill + self.raw_non_skill > 0
        effective = self.skill + self.non_skill > 0
        return dict(observed_skill_damage=self.raw_skill if nominal else None,
            observed_non_skill_damage=self.raw_non_skill if nominal else None,
            observed_skill_damage_effective=self.skill if effective else None,
            observed_non_skill_damage_effective=self.non_skill if effective else None)

    def legacy_partial_result(self, observed, total, matched_mitigation, start, end, *, recent_native=None,
               include_start=False, reconcile_scope=False):
        skill_verified = self.raw_skill + self.raw_non_skill
        active = max(0.0, self.active - max(0.0, self.active_end - end))
        recent = [(hit[0], (hit[2] or 0.0) if hit[5] is not None else 0.0, hit[1], (hit[2] or 0.0) if hit[6] else 0.0) for hit in self.recent if (hit[5] is not None or hit[6])
                  and (hit[0] > start or (include_start and hit[0] == start)) and hit[0] <= end]
        window_verified = sum(hit[1] for hit in recent)
        has_recent = window_verified > 0 and end > start
        recent_total = (recent_native + sum(hit[3] for hit in recent)
                        if recent_native is not None else None)
        recent_mismatch = (reconcile_scope and recent_total is not None
                           and amount_exceeds_counter(window_verified, recent_total))
        has_recent &= not recent_mismatch
        effective_unverified = unverified_amount_share(self.overkill_base, total)
        row = {
            'effective_damage': (self.hp + self.shield + self.legacy_effective) if self.overkill_base > 0 else None,
            'effective_damage_unverified_share': effective_unverified,
            'skill_damage_unverified_share': unverified_amount_share(skill_verified, total),
            'skill_effective_damage_unverified_share': unverified_amount_share(self.classified_overkill_base, total),
            'mitigation_unverified_share': unverified_amount_share(matched_mitigation, total),
            'recent_effective_damage': sum(hit[2] for hit in recent) / (end - start) if has_recent else None,
            'recent_effective_damage_window': (start, end) if has_recent else None,
            'recent_effective_damage_unverified_share': unverified_amount_share(window_verified, recent_total),
            'engagement_effective_damage': (self.hp + self.shield + self.legacy_effective) / active if active > 0 else None,
            'engagement_effective_damage_unverified_share': effective_unverified,
            'active_effective_damage': active if self.overkill_base > 0 else None,
        }
        if reconcile_scope:
            row['effective_damage_by_type'] = dict(self.by_type)
            if recent_mismatch:
                row['recent_damage_scope_reason'] = '伤害事件与原生累计的统计范围不一致'
        return row

    def legacy_boss_result(self, start, end, *, known, rate_known, skill_known, mitigation_known, reason,
               live=False, reconcile_scope=False, partial_enabled=False):
        recent_start = max(start, end - 5)
        while self.recent and self.recent[0][0] < recent_start:
            self.recent.popleft()
        recent = [(hit[0], hit[4], hit[2] or 0.0, hit[5] or 0.0) for hit in self.recent
                  if (hit[0] > recent_start or recent_start == start) and hit[0] <= end]
        active = max(0.0, self.raw_active - max(0.0, self.raw_active_end - end))
        duration = end - recent_start
        recent_known = rate_known and duration > 0
        effective_known = known and (self.overkill_hits == self.hits)
        effective_skill_known = skill_known and effective_known
        recent_by_type = dict.fromkeys(('physical', 'arts', 'elemental', 'true'), 0.0)
        if recent_known and (self.raw_typed_hits == self.raw_hits):
            for _, kind, amount, _ in recent:
                recent_by_type[kind] += amount / duration
        observed_rate = ((1 - self.after / self.before) * 100 if self.before > 0 else None)
        mitigation_complete = (known and mitigation_known and (self.formula_hits == self.hits)
                               and observed_rate is not None)
        row = {
            'damage': self.raw if known else None,
            'observed_damage': self.raw if known else None,
            'damage_by_type': dict(self.raw_by_type) if known and (self.raw_typed_hits == self.raw_hits) else {},
            'types_available': known and (self.raw_typed_hits == self.raw_hits),
            'types_stale': not known,
            'counter_snapshot_complete': known,
            'event_coverage_complete': known,
            'event_coverage_end': end,
            'recent_damage': sum(hit[2] for hit in recent) / duration if recent_known else None,
            'recent_damage_by_type': recent_by_type if recent_known and (self.raw_typed_hits == self.raw_hits) else {},
            'recent_damage_window': (recent_start, end) if recent_known else None,
            'engagement_damage': self.raw / active if known and active > 0 else None,
            'active_damage': active if known else None,
            'overkill_damage': self.overkill if effective_known else None,
            'recent_overkill_damage': (sum(hit[3] for hit in recent)
                                       if recent_known and effective_known else None),
            'overkill_coverage_complete': effective_known,
            'overkill_type_complete': effective_known and (self.raw_typed_hits == self.raw_hits),
            'overkill_damage_by_type': (dict(self.overkill_by_type)
                                       if effective_known and (self.raw_typed_hits == self.raw_hits) else None),
            'overkill_unknown_reason': None if effective_known else reason or self.legacy_overkill_reason,
            'effective_damage': max(0.0, self.raw - self.overkill) if effective_known else None,
            'skill_damage': self.raw_skill if skill_known else None,
            'non_skill_damage': self.raw - self.raw_skill if skill_known else None,
            'skill_damage_effective': max(0.0, self.raw_skill - self.raw_skill_overkill)
                                      if effective_skill_known else None,
            'non_skill_damage_effective': max(0.0, self.raw - self.raw_skill - self.overkill + self.raw_skill_overkill)
                                          if effective_skill_known else None,
            'skill_split_complete': skill_known,
            'skill_split_effective_complete': effective_skill_known,
            'skill_split_reason': None if skill_known else reason or '技能事件覆盖不完整',
            **self.observed_skill_result(),
            'mitigation_rate': observed_rate if mitigation_complete else None,
            'mitigation_before': self.before if mitigation_complete else None,
            'mitigation_after': self.after if mitigation_complete else None,
            'mitigation_unknown_reason': (None if mitigation_complete else reason or
                                          ('首领命中缺少同次命中的公式前后值' if self.raw > 0
                                           else '尚无可核对的首领公式前后伤害')),
            'observed_mitigation_rate': observed_rate,
            'observed_mitigation_before': self.before if observed_rate is not None else None,
            'observed_mitigation_after': self.after if observed_rate is not None else None,
            'mitigation_matched_hits': self.formula_hits,
        }
        if partial_enabled:
            partial = self.legacy_partial_result(None, self.raw if known else None,
                                          self.after, recent_start, end,
                                          recent_native=sum(hit[2] for hit in recent) if known else None,
                                          include_start=recent_start == start, reconcile_scope=reconcile_scope)
            partial.update(engagement_damage=self.raw / active if active > 0 else None,
                           active_damage=active,
                           observed_boss_damage=self.raw,
                           observed_boss_recent_damage=sum(hit[2] for hit in recent) / duration
                           if duration > 0 and live else None,
                           observed_boss_recent_window=(recent_start, end) if duration > 0 else None,
                           engagement_damage_unverified_share=0.0 if known and self.raw > 0 else None)
            if reconcile_scope:
                partial['damage_by_type'] = dict(self.raw_by_type)
            row['partial_damage'] = partial
        return row


def unverified_amount_share(verified, total):
    if total is None or total <= 0 or not isfinite(verified) or not isfinite(total):
        return None
    if isclose(verified, total, rel_tol=1e-6, abs_tol=0.01):
        return 0.0
    return (total - verified) / total if 0 <= verified < total else None


def amount_exceeds_counter(observed, native):
    return observed > native and not isclose(observed, native, rel_tol=1e-5, abs_tol=0.01)
