"""DPS is measured in game seconds, never wall-clock seconds."""
import time


def observation_age(sample, now=None):
    if sample is None or 'captured_monotonic' not in sample:
        return float('inf')
    return max(0.0,(time.monotonic() if now is None else now)-sample['captured_monotonic'])
