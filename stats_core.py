"""Offline statistics core. Times are game seconds; inputs must be reliable.

Snapshots own damage/healing totals and sampled recent rates. Event callbacks own
engagement rates and shield absorption; never add damage/heal events to totals.
Source keys identify distinct client statistic entries, not transient entities.
"""
from collections import deque
from math import isfinite


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

    def advance(self, game_time):
        self._time(game_time)
        if self.end_time is not None or game_time < self.now:
            raise ValueError('clock cannot advance after end or move backwards')
        self.now = game_time

    def finish(self, game_time):
        self.advance(game_time)
        self.end_time = game_time

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

    def event(self, event_id, game_time, source, kind, amount):
        """Record one reliable event: damage, healing, or actual shield absorption.

        Duplicate IDs with identical payload are ignored; conflicts are rejected.
        Inputs must arrive in game-time order. Shield expiration and replacement
        are not absorption and have no accepted event kind.
        """
        self._time(game_time)
        self._amount(amount)
        if not self.event_coverage or self.event_coverage_end is not None or not self.baseline_ready:
            raise ValueError('event coverage or baseline is unavailable')
        if not event_id or source not in self.owners or kind not in ('damage', 'healing', 'shield_absorbed'):
            raise ValueError('invalid event identity, source or kind')
        payload = (game_time, source, kind, amount)
        if event_id in self.events:
            if self.events[event_id] != payload:
                raise ValueError('conflicting event ID')
            return
        if self.end_time is not None or not self.start_time <= game_time <= self.now or game_time < self.last_event_time:
            raise ValueError('event outside ordered observation')
        self.events[event_id] = payload
        self.last_event_time = game_time
        owner = self.owners[source]
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
            if after is None or after[0] - before[0] > 1:
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
            row = {'coverage_start': self.start_time,
                   'counter_snapshot_complete': complete,
                   'shield_coverage_start': self.start_time if self.event_coverage else None,
                   'event_coverage_end': (self.event_coverage_end if self.event_coverage_end is not None
                                          else self.end_time if self.event_coverage else None),
                   'event_coverage_complete': self.event_coverage and self.event_coverage_end is None,
                   'shield_absorbed': self.shields.get(owner, 0) if self.event_coverage else None}
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
            rows[owner] = row
        return {'session': self.session, 'game_time': self.now, 'ended': self.end_time is not None,
                'coverage_start': self.start_time,
                'totals_complete': all(row['counter_snapshot_complete'] for row in rows.values()),
                'rows': rows}
