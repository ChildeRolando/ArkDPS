"""DPS is measured in game seconds, never wall-clock seconds."""
import time
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps


_timing_window = ContextVar('arkdps_timing_window', default=None)


class TimingWindow:
    """Bounded, opt-in wall-time accounting; never called per damage event.

    A window belongs to one host thread. Nested phases retain both inclusive
    and exclusive time. Empty/missing phases are unknown, not measured zeroes.
    Emission happens at batch boundaries, at most once per second.
    """
    def __init__(self, enabled, sink, *, source, clock=time.perf_counter_ns):
        self.enabled, self.sink, self.source, self.clock = enabled, sink, source, clock
        self.rows, self.stack = {}, []
        self.started = None
        self.failures = 0

    def add(self, phase, elapsed, exclusive=None):
        if phase not in self.rows and len(self.rows) >= 32:
            phase = 'other'
        row = self.rows.setdefault(phase, [0, 0, 0, 0])
        row[0] += 1
        row[1] += max(0, elapsed)
        row[2] += max(0, elapsed if exclusive is None else exclusive)
        row[3] = max(row[3], elapsed)

    def clear(self):
        self.rows.clear()
        self.started = None

    @contextmanager
    def batch(self):
        if not self.enabled():
            self.clear()
            yield
            return
        if self.started is None:
            self.started = self.clock()
        token = _timing_window.set(self)
        try:
            yield
        finally:
            _timing_window.reset(token)
            self.flush()

    @contextmanager
    def phase(self, name):
        start = self.clock()
        frame = [0]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = self.clock() - start
            self.stack.pop()
            if self.stack:
                self.stack[-1][0] += elapsed
            self.add(name, elapsed, elapsed - frame[0])

    def flush(self):
        now = self.clock()
        if self.started is None:
            self.started = now
        if now - self.started < 1_000_000_000:
            return
        data = dict(schema=1, source=self.source, pid=os.getpid(),
                    utc=datetime.now(timezone.utc).isoformat(),
                    monotonic_ns=now, window_ns=now-self.started,
                    clock='perf_counter_ns', sampling='all_batches',
                    emission_failures=self.failures,
                    phases={name: dict(calls=v[0], inclusive_ns=v[1], exclusive_ns=v[2], max_ns=v[3])
                            for name, v in self.rows.items()})
        self.rows.clear()
        self.started = now
        try:
            self.sink('performance_timing', data)
        except Exception:
            self.failures += 1  # Diagnostics must not change delivery/ACK behavior.


def timed(name):
    """No clock reads when there is no explicitly enabled batch."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            window = _timing_window.get()
            if window is None:
                return function(*args, **kwargs)
            with window.phase(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def timing_batch(function):
    with_phase = timed(function.__qualname__)(function)
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self._performance_timings.batch():
            return with_phase(self, *args, **kwargs)
    return wrapped


def observation_age(sample, now=None):
    if sample is None or 'captured_monotonic' not in sample:
        return float('inf')
    return max(0.0,(time.monotonic() if now is None else now)-sample['captured_monotonic'])
