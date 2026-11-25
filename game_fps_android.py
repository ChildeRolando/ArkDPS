"""Completed guest-frame windows from the selected Android game's surface."""

from dataclasses import dataclass
import re
from shlex import quote
import time

from connection import CommandError, PACKAGE, SubprocessRunner


SECOND_NS = 1_000_000_000
PENDING_PRESENT = (1 << 63) - 1
POLL_TIMEOUT = .9
LAYER_REFRESH_SECONDS = 5
MAX_FRAME_RECORDS = 128
_SEPARATOR = '\narkdps-surface-fps\n'
_PACKAGE_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+')
_PID = re.compile(r'[1-9][0-9]{0,9}')


@dataclass(frozen=True)
class AndroidFpsSample:
    frames: int | None = None
    fps: float | None = None
    window_seconds: float | None = None
    reason: str = ''
    bucket_end_ns: int | None = None
    pid: int | None = None


def _select_game_layer(output, package):
    component = rf'{re.escape(package)}/(?P<activity>[A-Za-z0-9_.$]+)'
    game_layer = re.compile(
        rf'(?:SurfaceView\[{component}\]|SurfaceView - {re.escape(package)}/'
        r'(?P<legacy_activity>[A-Za-z0-9_.$]+))'
        r'(?:#[0-9]{1,10})?(?P<blast> ?\(BLAST\))?(?:#[0-9]{1,10})?'
    )
    candidates = [(name, game_layer.fullmatch(name)) for name in output.splitlines() if len(name) < 256]
    layers = [(name, match['activity'] or match['legacy_activity'], bool(match['blast']))
              for name, match in candidates if match]
    if len(layers) == 1:
        return layers[0][0], ''
    # BLAST creates a buffer child and a same-activity SurfaceView container.
    # Other activities or additional buffer children do not prove the game layer.
    buffers = [name for name, _, blast in layers if blast]
    if len(layers) == 2 and len(buffers) == 1 and layers[0][1] == layers[1][1]:
        return buffers[0], ''
    return None, 'surface_ambiguous' if layers else 'surface_unavailable'


def _present_timestamps(output):
    fields = [line.split() for line in output.splitlines() if line.strip()]
    if not fields or len(fields[0]) != 1 or not re.fullmatch(r'[1-9][0-9]{0,9}', fields[0][0]):
        return None
    rows = fields[1:]
    if not rows or len(rows) > MAX_FRAME_RECORDS:
        return None
    frames = []
    pending = False
    for row in rows:
        if len(row) != 3 or any(not re.fullmatch(r'-?[0-9]{1,19}', value) for value in row):
            return None
        desired, present, ready = map(int, row)
        if not frames and desired == present == ready == 0:
            continue  # Uninitialized slots precede the active frame history.
        if present <= 0 or present == PENDING_PRESENT:
            pending = True
            continue
        if present > PENDING_PRESENT or pending or (frames and frames[-1] > present):
            return None
        if not frames or frames[-1] != present:
            frames.append(present)
    return tuple(frames)


class AndroidGameFps:
    """Count display times in one guest-time second, independent of combat reads."""

    def __init__(self, adb_path, serial, *, package=PACKAGE, runner=None, clock=time.monotonic):
        if not _PACKAGE_NAME.fullmatch(package):
            raise ValueError('invalid Android game package')
        self.adb_path, self.serial, self.package = adb_path, serial, package
        self._run = runner or SubprocessRunner().run
        self._clock = clock
        self._layer = None
        self._discover_at = 0.
        self._pid = None
        self._previous = ()
        self._trusted_since = None
        self._recent = set()
        self._last_bucket_end = None

    def _reset_history(self, pid=None, timestamps=()):
        self._pid = pid
        self._previous = timestamps
        self._trusted_since = max(timestamps, default=None)
        self._recent = set(timestamps)
        self._last_bucket_end = None

    def _shell(self, command, deadline):
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise CommandError('Android FPS poll expired')
        response = self._run([self.adb_path, '-s', self.serial, 'shell', command], remaining)
        if response.returncode:
            raise CommandError('Android FPS command failed')
        return response.stdout.replace('\r\n', '\n')

    def _read_layer(self, deadline):
        # Keep this one ADB request and quote the complete, allowlisted layer name
        # for the guest shell. Bind the dump to a live package before and after.
        pid = f'pidof {quote(self.package)}'
        separator = r"printf '\narkdps-surface-fps\n'"
        command = (f'{pid}; {separator}; '
                   f'dumpsys SurfaceFlinger --latency {quote(self._layer)}; {separator}; {pid}; true')
        parts = self._shell(command, deadline).split(_SEPARATOR)
        if len(parts) != 3:
            return None, None, 'surface_stats_unavailable'
        first, latency, last = (part.strip() for part in parts)
        if not first or not last:
            return None, None, 'game_not_running'
        if not _PID.fullmatch(first) or first != last:
            return None, None, 'guest_process_unverified'
        timestamps = _present_timestamps(latency)
        if timestamps is None:
            return None, None, 'surface_stats_unavailable'
        if not timestamps:
            return None, None, 'guest_window_pending'
        return int(first), timestamps, ''

    def poll(self):
        now = self._clock()
        deadline = now + POLL_TIMEOUT
        try:
            if self._layer is None or now >= self._discover_at:
                output = self._shell('dumpsys SurfaceFlinger --list', deadline)
                layer, reason = _select_game_layer(output, self.package)
                if layer != self._layer:
                    self._reset_history()
                self._layer, self._discover_at = layer, now + LAYER_REFRESH_SECONDS
                if layer is None:
                    return AndroidFpsSample(reason=reason)
            pid, timestamps, reason = self._read_layer(deadline)
        except (CommandError, OSError):
            self._reset_history()
            return AndroidFpsSample(reason='adb_unavailable')
        if reason:
            self._reset_history()
            if reason in ('game_not_running', 'surface_stats_unavailable'):
                self._layer = None
            return AndroidFpsSample(reason=reason)
        if pid != self._pid:
            self._reset_history(pid, timestamps)
            return AndroidFpsSample(reason='warming_up')
        if self._previous[-1] not in timestamps:
            # The old tail may have been overwritten or the layer recreated
            # with the same name. Do not join unproven portions of the ring.
            self._reset_history(pid, timestamps)
            return AndroidFpsSample(reason='surface_history_gap')
        if timestamps[-1] == self._previous[-1]:
            return AndroidFpsSample(reason='guest_window_pending')

        end = (timestamps[-1] // SECOND_NS) * SECOND_NS
        start = end - SECOND_NS
        self._recent.update(timestamps)
        self._recent = {timestamp for timestamp in self._recent if timestamp >= start}
        self._previous = timestamps
        # A later presented frame proves that this whole guest-time second
        # ended. Host clock and /proc/uptime do not prove a guest's present time.
        if start < self._trusted_since or (self._last_bucket_end is not None and end <= self._last_bucket_end):
            return AndroidFpsSample(reason='guest_window_pending')
        self._last_bucket_end = end
        frames = sum(timestamp < end for timestamp in self._recent)
        return AndroidFpsSample(frames, float(frames), 1., bucket_end_ns=end, pid=pid)
