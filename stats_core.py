"""Offline statistics core. Times are game seconds; inputs must be reliable.

Snapshots own damage/healing totals and sampled recent rates. Event callbacks own
engagement rates, shield absorption and event-derived overkill; never add
damage/heal events to totals. Overkill is a subset of the already inclusive
native damage total and is never subtracted from it. Source keys identify
distinct client statistic entries, not transient entities.
"""
from collections import deque
from math import isfinite

# Native DamageType values used by the verified 2.7.91 event probe.
# 0=NONE, 4=HEAL and unknown values cannot be attributed to a damage segment.
NATIVE_DAMAGE_TYPES = {1: 'physical', 2: 'arts', 3: 'true', 5: 'elemental'}
DAMAGE_TYPE_KEYS = ('physical', 'arts', 'elemental', 'true')


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
        # Target-side HP events are an independent, authoritative overkill
        # source: amount minus observed shield absorption minus HP before.
        self.hp_overkill = {}
        self.hp_overkill_incomplete = set()
        self.hp_overkill_by_type = {}
        self.hp_overkill_type_incomplete = set()
        self.hp_seen = set()
        self.overkill_reason = {}
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
        self.skill_seen = set()
        self.skill_unknown = False
        # Per-event overkill is split by the same skill window as its damage.
        # HP evidence is kept separately because it overrides the ApplyModifier
        # overkill total for owners that produced target-side evidence.
        self.skill_overkill = {}
        self.non_skill_overkill = {}
        self.hp_skill_overkill = {}
        self.hp_non_skill_overkill = {}

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

    def mark_skill_unknown(self):
        self.skill_unknown = True

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

    def advance(self, game_time):
        self._time(game_time)
        if self.end_time is not None or game_time < self.now:
            raise ValueError('clock cannot advance after end or move backwards')
        self.now = game_time

    def finish(self, game_time):
        self.advance(game_time)
        self.end_time = game_time
        self._close_skill_windows(game_time)

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
            self._close_skill_windows(end_time)

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

    def _close_skill_windows(self, game_time):
        for owner, window in list(self.skill_active.items()):
            window['end'] = game_time
            window['reason'] = 'coverage_end'
            self.skill_windows.setdefault(owner, []).append(window)
        self.skill_active.clear()

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

    def _overkill(self, amount, *, target, real_delta, hp_before, hp_after,
                  killed, target_type, damage_type, cancel_reason, shield_absorbed):
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

    def event(self, event_id, game_time, source, kind, amount, *,
              target=None, real_delta=None, hp_before=None, hp_after=None,
              killed=None, target_type=None, damage_type=None,
              cancel_reason=None, shield_absorbed=None, target_instance=None,
              skill_key=None, skill_id=None, skill_name=None, skill_reason=None):
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
        if kind == 'hp_damage' and source not in self.owners:
            # Target-side HP evidence is supplemental. An unmapped source must
            # not tear down the authoritative damage/engagement stream.
            return
        if kind in ('skill_start', 'skill_end') and source not in self.owners:
            # Skill windows explain only the split; they are never authoritative
            # damage evidence.
            self.skill_unknown = True
            return
        if kind != 'elemental_burst' and source not in self.owners:
            raise ValueError('invalid event source')
        if kind == 'elemental_burst' and source is not None and source not in self.owners:
            raise ValueError('invalid burst source')
        evidence = (target, real_delta, hp_before, hp_after, killed,
                    target_type, damage_type, cancel_reason, shield_absorbed)
        if kind not in ('damage', 'elemental_burst', 'hp_damage') and any(value is not None for value in evidence):
            raise ValueError('damage evidence is only valid for damage, burst, or hp_damage')
        payload = (game_time, source, kind, amount, evidence, target_instance)
        if event_id in self.events:
            if self.events[event_id] != payload:
                raise ValueError('conflicting event ID')
            return
        if self.end_time is not None or not self.start_time <= game_time <= self.now or game_time < self.last_event_time:
            raise ValueError('event outside ordered observation')
        self.events[event_id] = payload
        self.last_event_time = game_time
        owner = self.owners.get(source)
        if kind == 'elemental_bar':
            if not target_instance or owner is None:
                raise ValueError('elemental bar needs a source and target instance')
            self.last_elemental[target_instance] = owner
            return
        if kind == 'elemental_burst':
            if not target_instance:
                raise ValueError('elemental burst needs a target instance')
            if (not self._optional_number(real_delta) or real_delta > 0
                    or not self._optional_number(hp_before)
                    or not self._optional_number(hp_after)
                    or hp_before < 0 or hp_after < 0):
                raise ValueError('invalid elemental burst evidence')
            actual = -real_delta
            tolerance = max(0.01, amount * 1e-6)
            if actual > amount + tolerance:
                raise ValueError('elemental burst actual exceeds settlement')
            attributed = owner if owner is not None else self.last_elemental.get(target_instance)
            if attributed is None:
                self.burst_unknown = True
                self.burst_unknown_reason = '元素爆条缺少可归属的最后元素损伤来源'
                return
            entry = self.burst.setdefault(attributed, {'actual': 0.0, 'overkill': 0.0,
                                                        'settlement': 0.0, 'count': 0})
            entry['actual'] += actual
            entry['overkill'] += max(0.0, amount - actual)
            entry['settlement'] += amount
            entry['count'] += 1
            bucket = (self.skill_observed if attributed in self.skill_active
                      else self.non_skill_observed)
            bucket[attributed] = bucket.get(attributed, 0.0) + amount
            burst_overkill = max(0.0, amount - actual)
            if burst_overkill > 0:
                overkill_bucket = (self.skill_overkill
                                   if attributed in self.skill_active
                                   else self.non_skill_overkill)
                overkill_bucket[attributed] = (
                    overkill_bucket.get(attributed, 0.0) + burst_overkill)
            self.last_elemental.pop(target_instance, None)
            return
        if kind in ('skill_start', 'skill_end'):
            if kind == 'skill_start':
                self.skill_seen.add(owner)
                if owner not in self.skill_active:
                    self.skill_active[owner] = {
                        'start': game_time, 'key': skill_key, 'id': skill_id,
                        'name': skill_name, 'method': target_instance}
            else:
                window = self.skill_active.pop(owner, None)
                if window is not None:
                    window['end'] = game_time
                    window['reason'] = skill_reason
                    self.skill_windows.setdefault(owner, []).append(window)
            return
        if kind == 'damage':
            known, overkill, reason = self._overkill(
                amount, target=target, real_delta=real_delta, hp_before=hp_before,
                hp_after=hp_after, killed=killed, target_type=target_type,
                damage_type=damage_type, cancel_reason=cancel_reason,
                shield_absorbed=shield_absorbed)
            if known:
                if killed and target_instance:
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
        if kind == 'damage' and amount > 0:
            friendly = (isinstance(target, str)
                        and (target.startswith('char_') or target.startswith('token_')))
            if not friendly:
                bucket = (self.skill_observed if owner in self.skill_active
                          else self.non_skill_observed)
                bucket[owner] = bucket.get(owner, 0.0) + amount
        if kind == 'hp_damage':
            if not isinstance(target, str) or not target:
                self.hp_seen.add(owner)
                self.hp_overkill_incomplete.add(owner)
                return
            # Friendly/self damage is not outgoing HP damage for DPS/overkill.
            if target.startswith('char_') or target.startswith('token_'):
                return
            self.hp_seen.add(owner)
            tolerance = max(0.01, amount * 1e-6)
            if not self._optional_number(hp_before) or hp_before < 0:
                self.hp_overkill_incomplete.add(owner)
                return
            if amount <= hp_before + tolerance:
                overkill = 0.0
            else:
                if not self._optional_number(shield_absorbed) or shield_absorbed < 0:
                    self.hp_overkill_incomplete.add(owner)
                    return
                overkill = max(0.0, amount - shield_absorbed - hp_before)
            self.hp_overkill[owner] = self.hp_overkill.get(owner, 0.0) + overkill
            if overkill > 0:
                bucket = (self.hp_skill_overkill if owner in self.skill_active
                          else self.hp_non_skill_overkill)
                bucket[owner] = bucket.get(owner, 0.0) + overkill
                type_key = NATIVE_DAMAGE_TYPES.get(damage_type)
                if type_key is None:
                    self.hp_overkill_type_incomplete.add(owner)
                else:
                    per_type = self.hp_overkill_by_type.setdefault(owner, {})
                    per_type[type_key] = per_type.get(type_key, 0.0) + overkill
            return
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

    def result(self):
        """Return per-owner values and explicit coverage; unknown is None."""
        owners = set(self.owners.values())
        rows = {}
        for owner in owners:
            sources = [s for s, o in self.owners.items() if o == owner and s in self.sources]
            registered = [s for s, o in self.owners.items() if o == owner]
            # Totals completeness never borrows rate-window continuity: a
            # settlement or battle boundary retires rates, not final counters.
            complete = all(s in self.sources and self.sources[s]['totals_valid'] and
                           self.sources[s]['totals_time'] == self.now for s in registered)
            coverage_complete = bool(self.event_coverage) and not self.event_coverage_broken
            overkill_known = (coverage_complete
                              and not self.all_overkill_unknown
                              and owner not in self.overkill_incomplete)
            overkill_type_known = overkill_known and owner not in self.overkill_type_incomplete
            if overkill_known:
                overkill_unknown_reason = None
            elif not self.event_coverage:
                overkill_unknown_reason = '事件观测未建立'
            elif not coverage_complete:
                overkill_unknown_reason = '事件覆盖已结束'
            elif self.all_overkill_unknown:
                overkill_unknown_reason = '存在未归属的伤害事件'
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
            burst = self.burst.get(owner)
            burst_complete = not self.burst_unknown
            row['burst_damage'] = (burst['actual'] if burst and burst_complete else None)
            row['burst_overkill'] = (burst['overkill'] if burst and burst_complete else None)
            row['burst_settlement'] = (burst['settlement'] if burst and burst_complete else None)
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
                    row['engagement_' + kind] = state['total'] / active if active > 0 else None
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
            if burst_complete and burst:
                if row['damage'] is not None:
                    row['damage'] += burst['settlement']
                if row['overkill_damage'] is not None:
                    row['overkill_damage'] += burst['overkill']
                    types = row['overkill_damage_by_type']
                    if types is not None:
                        types['elemental'] = types.get('elemental', 0.0) + burst['overkill']
            if owner in self.hp_seen:
                hp_known = bool(self.event_coverage) and owner not in self.hp_overkill_incomplete
                if hp_known:
                    row['overkill_damage'] = self.hp_overkill.get(owner, 0.0)
                    row['overkill_coverage_complete'] = True
                    row['overkill_unknown_reason'] = None
                    if owner in self.hp_overkill_type_incomplete:
                        row['overkill_damage_by_type'] = None
                        row['overkill_type_complete'] = False
                    else:
                        row['overkill_damage_by_type'] = dict(
                            self.hp_overkill_by_type.get(owner, {}))
                        row['overkill_type_complete'] = True
                else:
                    row['overkill_damage'] = None
                    row['overkill_coverage_complete'] = False
                    row['overkill_unknown_reason'] = '目标掉血证据不完整'
            if row['damage'] is not None and row['overkill_damage'] is not None:
                row['effective_damage'] = row['damage'] - row['overkill_damage']
            else:
                row['effective_damage'] = None
            skill_known = coverage_complete and not self.skill_unknown
            skill_value = self.skill_observed.get(owner, 0.0)
            non_skill_value = self.non_skill_observed.get(owner, 0.0)
            expected = row.get('observed_damage')
            if expected is not None and burst_complete and burst:
                expected += burst['settlement']
            covers_total = (expected is not None
                            and abs(skill_value + non_skill_value - expected)
                            <= max(0.01, abs(expected) * 1e-6))
            if not skill_known:
                reason = ('事件观测未建立' if not self.event_coverage else
                          '事件覆盖已结束' if not coverage_complete else
                          '技能事件覆盖不完整')
            elif not covers_total:
                reason = '技能窗口未覆盖全部伤害事件'
            else:
                reason = None
            hp_overkill_known = owner in self.hp_seen and owner not in self.hp_overkill_incomplete
            apply_overkill_known = overkill_known
            if hp_overkill_known:
                split_skill_overkill = self.hp_skill_overkill.get(owner, 0.0)
                split_non_skill_overkill = self.hp_non_skill_overkill.get(owner, 0.0)
                split_overkill_known = True
            elif apply_overkill_known:
                split_skill_overkill = self.skill_overkill.get(owner, 0.0)
                split_non_skill_overkill = self.non_skill_overkill.get(owner, 0.0)
                split_overkill_known = True
            else:
                split_skill_overkill = split_non_skill_overkill = None
                split_overkill_known = False
            effective_split_known = skill_known and covers_total and split_overkill_known
            row['skill_damage'] = skill_value if skill_known else None
            row['non_skill_damage'] = non_skill_value if skill_known else None
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
                if self.event_coverage else None)
            row['skill_coverage_complete'] = coverage_complete and not self.skill_unknown
            row['skill_split_complete'] = skill_known and covers_total
            row['skill_split_effective_complete'] = effective_split_known
            row['skill_split_reason'] = reason
            rows[owner] = row
        overkill_complete = bool(rows) and all(
            row['overkill_coverage_complete'] for row in rows.values())
        return {'session': self.session, 'game_time': self.now, 'ended': self.end_time is not None,
                'coverage_start': self.start_time,
                'totals_complete': all(row['counter_snapshot_complete'] for row in rows.values()),
                'rows': rows,
                'overkill_damage': (sum(row['overkill_damage'] for row in rows.values())
                                    if overkill_complete else None),
                'overkill_coverage_complete': overkill_complete,
                'overkill_coverage_start': self.start_time if self.event_coverage else None,
                'overkill_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                          else self.end_time if self.event_coverage else None)}