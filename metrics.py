"""DPS is measured in game seconds, never wall-clock seconds."""
from collections import deque
from copy import deepcopy
import time


def observation_age(sample, now=None):
    if sample is None or 'captured_monotonic' not in sample:
        return float('inf')
    return max(0.0,(time.monotonic() if now is None else now)-sample['captured_monotonic'])


class Meter:
    def __init__(self):
        self.session = None
        self.history = deque()

    def update(self, sample):
        now = sample['game_time']
        totals = {r['id']: r['damage'] for r in sample['rows']}
        reset = (sample['session'] != self.session or
                 (self.history and now < self.history[-1][0]))
        if self.history and not reset:
            reset = any(totals.get(k, -1) < v - 0.02 for k, v in self.history[-1][1].items())
        if reset:
            self.history.clear()
            self.session = sample['session']
        # Pause does not consume the rolling window or add duplicate samples.
        if not self.history or now > self.history[-1][0]:
            self.history.append((now, totals))
        elif totals != self.history[-1][1]:
            self.history[-1] = (now, totals)
        cutoff = now - 5.0
        while len(self.history) > 2 and self.history[1][0] <= cutoff:
            self.history.popleft()
        result = deepcopy(sample)
        for row in result['rows']:
            row['dps'] = row['damage'] / now if now > 0 else None
            row['share'] = row['damage'] / sample['sum_damage'] if sample['sum_damage'] else 0
            row['recent_dps'] = None
            # Require a full observed five-second window. A newly attached
            # collector must not invent the unobserved historical baseline.
            if len(self.history) >= 2 and self.history[0][0] <= cutoff:
                t0, v0 = self.history[0]
                t1, v1 = self.history[1]
                if t1 > t0 and t1 - t0 <= 1.0:
                    fraction = max(0.0, min(1.0, (cutoff - t0) / (t1 - t0)))
                    baseline = v0.get(row['id'], 0) + fraction * (v1.get(row['id'], 0) - v0.get(row['id'], 0))
                    row['recent_dps'] = max(0, row['damage'] - baseline) / 5.0
        result['rows'].sort(key=lambda r: r['damage'], reverse=True)
        result['global_difference'] = sample['sum_damage'] - sample['global_total']
        result['recent_method'] = '5 game seconds, sampled cumulative totals, interpolated boundary; unavailable across gaps >1s'
        return result
