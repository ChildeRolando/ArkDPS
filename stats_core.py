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
        self.overkill_incomplete = set()
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

    def invalidate_snapshots(self):
        """Mark a known collection gap; retain totals but restart rate windows."""
        for state in self.sources.values():
            state['continuous'] = False

    def invalidate_events(self, end_time=None):
        """Close event coverage at the last reliable game time."""
        if self.event_coverage and self.event_coverage_end is None:
            end_time = self.now if end_time is None else end_time
            self._time(end_time)
            if not self.last_event_time <= end_time <= self.now:
                raise ValueError('invalid event coverage end')
            self.event_coverage_end = end_time

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
        for source, values in counters.items():
            state = self.sources.get(source)
            if state is None:
                state = self.sources[source] = {'values': dict(values), 'history': deque(), 'continuous': True, 'segment_start': game_time}
            elif not state['continuous']:
                state['history'].clear()
                state['continuous'] = True
                state['segment_start'] = game_time
            state['values'] = dict(values)
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
                                        'continuous': True, 'segment_start': self.end_time}
                changed = True
        return changed

    def correct_final_totals(self, counters):
        """Revise terminal counters without claiming timing for any final rate."""
        if not self.baseline_ready:
            raise ValueError('final totals require an observation baseline')
        changed = any(source not in self.sources or self.sources[source]['values'] != values
                      for source, values in counters.items())
        self.settle_snapshot(counters)
        self.invalidate_snapshots()
        return changed

    @staticmethod
    def _optional_number(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and isfinite(value))

    def _overkill(self, amount, *, target, real_delta, hp_before, hp_after,
                  killed, target_type, damage_type, cancel_reason, shield_absorbed):
        """Derive one hit's overkill from native settlement evidence.

        ``amount`` is the native modifier value before shield/block settlement:
        captured frame 697 recorded ``m_value`` 14, ``realDelta`` 0 and unchanged
        HP while ``BlockDamage`` absorbed the same 14. Absorbed damage therefore
        has to be subtracted here instead of being assumed out of ``amount``.
        ``real_delta`` is the native signed HP delta (negative for damage).
        Only a target whose HP actually reached zero can have overkill, and only
        a lethal hit needs the absorption context. Returns ``(known, value)``;
        any missing or contradictory field keeps the whole per-owner metric
        unknown instead of publishing a guess or zero.
        """
        if (not isinstance(target, str) or not target
                or type(killed) is not bool
                or target_type is not None and (type(target_type) is not int)
                or damage_type is not None and (type(damage_type) is not int)
                or cancel_reason is not None and (type(cancel_reason) is not int)
                or not all(self._optional_number(v) for v in
                           (real_delta, hp_before, hp_after))
                or real_delta > 0 or hp_before < 0 or hp_after < 0
                or (shield_absorbed is not None
                    and (not self._optional_number(shield_absorbed)
                         or shield_absorbed < 0))):
            return False, 0.0
        actual = -real_delta
        tolerance = max(0.01, amount * 1e-6)
        if actual > amount + tolerance:
            return False, 0.0
        if abs((hp_before - hp_after) - actual) > tolerance:
            return False, 0.0
        if killed and hp_after > tolerance:
            return False, 0.0
        if not killed and hp_after <= tolerance:
            return False, 0.0
        if not killed:
            return True, 0.0
        if shield_absorbed is None:
            # Absorption happens before the HP delta, so a lethal hit without
            # that context cannot separate absorbed damage from overkill.
            return False, 0.0
        if shield_absorbed > amount + tolerance:
            return False, 0.0
        if actual + shield_absorbed > amount + tolerance:
            return False, 0.0
        return True, max(0.0, amount - shield_absorbed - actual)

    def event(self, event_id, game_time, source, kind, amount, *,
              target=None, real_delta=None, hp_before=None, hp_after=None,
              killed=None, target_type=None, damage_type=None,
              cancel_reason=None, shield_absorbed=None):
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
        if not event_id or source not in self.owners or kind not in ('damage', 'healing', 'shield_absorbed'):
            raise ValueError('invalid event identity, source or kind')
        evidence = (target, real_delta, hp_before, hp_after, killed,
                    target_type, damage_type, cancel_reason, shield_absorbed)
        if kind != 'damage' and any(value is not None for value in evidence):
            raise ValueError('overkill evidence is only valid for damage')
        payload = (game_time, source, kind, amount, evidence)
        if event_id in self.events:
            if self.events[event_id] != payload:
                raise ValueError('conflicting event ID')
            return
        if self.end_time is not None or not self.start_time <= game_time <= self.now or game_time < self.last_event_time:
            raise ValueError('event outside ordered observation')
        self.events[event_id] = payload
        self.last_event_time = game_time
        owner = self.owners[source]
        if kind == 'damage':
            known, overkill = self._overkill(
                amount, target=target, real_delta=real_delta, hp_before=hp_before,
                hp_after=hp_after, killed=killed, target_type=target_type,
                damage_type=damage_type, cancel_reason=cancel_reason,
                shield_absorbed=shield_absorbed)
            if known:
                self.overkill[owner] = self.overkill.get(owner, 0.0) + overkill
                if overkill > 0:
                    type_key = NATIVE_DAMAGE_TYPES.get(damage_type)
                    if type_key is None:
                        self.overkill_type_incomplete.add(owner)
                    else:
                        per_type = self.overkill_by_type.setdefault(owner, {})
                        per_type[type_key] = per_type.get(type_key, 0.0) + overkill
            else:
                self.overkill_incomplete.add(owner)
        if kind == 'shield_absorbed':
            self.shields[owner] = self.shields.get(owner, 0) + amount
        elif amount > 0:
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
            complete = all(s in self.sources and self.sources[s]['continuous'] and
                           self.sources[s]['history'][-1][0] == self.now for s in registered)
            overkill_known = (bool(self.event_coverage) and self.event_coverage_end is None
                              and not self.all_overkill_unknown
                              and owner not in self.overkill_incomplete)
            overkill_type_known = overkill_known and owner not in self.overkill_type_incomplete
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
                   'event_coverage_complete': self.event_coverage and self.event_coverage_end is None,
                   'shield_absorbed': (self.shields.get(owner, 0)
                                       if self.event_coverage and not self.shield_unknown else None),
                   'shield_unknown': self.shield_unknown,
                   'overkill_damage': (self.overkill.get(owner, 0.0) if overkill_known else None),
                   'overkill_damage_by_type': ({key: self.overkill_by_type.get(owner, {}).get(key, 0.0)
                                                for key in DAMAGE_TYPE_KEYS}
                                               if overkill_type_known else None),
                   'overkill_type_complete': overkill_type_known,
                   'overkill_coverage_complete': overkill_known,
                   'overkill_coverage_start': self.start_time if self.event_coverage else None,
                   'overkill_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                             else self.end_time if self.event_coverage else None)}
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
            rows[owner] = row
        overkill_complete = bool(rows) and all(
            row['overkill_coverage_complete'] for row in rows.values())
        return {'session': self.session, 'game_time': self.now, 'ended': self.end_time is not None,
                'coverage_start': self.start_time, 'rows': rows,
                'overkill_damage': (sum(row['overkill_damage'] for row in rows.values())
                                    if overkill_complete else None),
                'overkill_coverage_complete': overkill_complete,
                'overkill_coverage_start': self.start_time if self.event_coverage else None,
                'overkill_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                          else self.end_time if self.event_coverage else None)}
