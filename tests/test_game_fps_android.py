"""Owned SurfaceFlinger records exercise the guest FPS counter."""

from collections import deque

import pytest

from connection import CommandError, CommandResult, PACKAGE
from game_fps_android import AndroidGameFps, SECOND_NS


ANDROID_LAYER = f'SurfaceView[{PACKAGE}/.GameActivity](BLAST)#7'
SEP = '\narkdps-surface-fps\n'
NS = SECOND_NS


def dump(*actual_times):
    return '16666666\n0\t0\t0\n' + ''.join(
        f'{timestamp}\t{timestamp}\t{timestamp}\n' for timestamp in actual_times)


class Adb:
    def __init__(self):
        self.now = 0.
        self.layers = [ANDROID_LAYER]
        self.replies = deque()
        self.calls = []

    def run(self, command, timeout):
        self.calls.append((command, timeout))
        assert command[:4] == ['adb.exe', '-s', 'emulator-402', 'shell']
        assert 0 < timeout <= 1.
        if command[4].endswith('--list'):
            return CommandResult(0, '\n'.join(self.layers), '')
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        return CommandResult(0, reply, '')

    def reply(self, t, *times, pid='123', after=None, raw=None):
        self.now = t
        self.replies.append(SEP.join((pid, dump(*times) if raw is None else raw,
                                      pid if after is None else after)))

    def monitor(self):
        return AndroidGameFps('adb.exe', 'emulator-402', runner=self.run, clock=lambda: self.now)


def test_counts_exact_guest_bucket_once_after_later_frame():
    adb = Adb()
    adb.layers += [f'SurfaceView[{PACKAGE}/.GameActivity]#6', 'Background for ' + ANDROID_LAYER,
                   'SurfaceView[other.game/.GameActivity](BLAST)#9']
    fps = adb.monitor()
    a, b, c, d, e = 1_800_000_000, 2 * NS, 2_550_000_000, 3 * NS, 3_250_000_000
    adb.reply(0, a)
    assert fps.poll().reason == 'warming_up'
    adb.reply(.8, a, b, c)
    assert fps.poll().reason == 'guest_window_pending'
    adb.reply(2.1, a, b, c, c, d, e)
    sample = fps.poll()
    assert (sample.frames, sample.fps, sample.window_seconds, sample.bucket_end_ns) == (2, 2., 1., 3 * NS)
    assert sample.pid == 123
    adb.reply(3.2, b, c, d, e)
    assert fps.poll().reason == 'guest_window_pending'
    assert len(adb.calls) == 5
    assert f"--latency '{ANDROID_LAYER}'" in adb.calls[1][0][-1]
    assert adb.calls[1][0][-1].count(f'pidof {PACKAGE}') == 2


def test_reports_zero_only_for_a_proven_completed_guest_second():
    adb = Adb()
    fps = adb.monitor()
    adb.reply(0, 2 * NS)
    assert fps.poll().reason == 'warming_up'
    adb.reply(1.1, 2 * NS)
    assert fps.poll().frames is None
    adb.reply(2.1, 2 * NS)
    assert fps.poll().frames is None
    adb.reply(3.2, 2 * NS, 5 * NS)
    sample = fps.poll()
    assert (sample.frames, sample.window_seconds, sample.bucket_end_ns) == (0, 1., 5 * NS)


def test_history_overwrite_and_guest_process_change_require_new_baseline():
    adb = Adb()
    fps = adb.monitor()
    adb.reply(0, 2 * NS)
    assert fps.poll().reason == 'warming_up'
    adb.reply(1, 3 * NS, 4 * NS)
    assert fps.poll().reason == 'surface_history_gap'
    adb.reply(2, 4 * NS, 5 * NS)
    assert fps.poll().frames == 1
    adb.reply(3, 5 * NS, 6 * NS, pid='321')
    assert fps.poll().reason == 'warming_up'
    adb.reply(4, 5 * NS, 6 * NS, 7 * NS, pid='321', after='322')
    assert fps.poll().reason == 'guest_process_unverified'
    adb.reply(5, 7 * NS, pid='322')
    assert fps.poll().reason == 'warming_up'


@pytest.mark.parametrize('raw,reason', [
    ('16666666', 'surface_stats_unavailable'),
    ('Permission denial\n', 'surface_stats_unavailable'),
    ('16666666\n0\t0\t0\n', 'guest_window_pending'),
    ('16666666\n1\t9223372036854775807\t1\n1\t2\t1', 'surface_stats_unavailable'),
    (dump(3 * NS, 2 * NS), 'surface_stats_unavailable'),
    ('16666666\n' + '1\t2\t3\n' * 129, 'surface_stats_unavailable'),
])
def test_missing_pending_or_malformed_stats_never_invent_a_rate(raw, reason):
    adb = Adb()
    fps = adb.monitor()
    adb.reply(0, raw=raw)
    assert fps.poll().reason == reason


def test_only_game_buffers_and_failures_reset_fps():
    adb = Adb()
    fps = adb.monitor()
    adb.layers = [f'{PACKAGE}/.GameActivity#1', ANDROID_LAYER + '; reboot']
    assert fps.poll().reason == 'surface_unavailable'
    adb.layers = [ANDROID_LAYER, ANDROID_LAYER.replace('#7', '#8')]
    assert fps.poll().reason == 'surface_ambiguous'
    adb.layers = [f'SurfaceView - {PACKAGE}/.GameActivity#1']
    adb.reply(1, 2 * NS)
    assert fps.poll().reason == 'warming_up'
    adb.replies.append(CommandError('device offline'))
    assert fps.poll().reason == 'adb_unavailable'
    adb.reply(2, pid='')
    assert fps.poll().reason == 'game_not_running'


def test_each_poll_shares_one_bounded_timeout():
    adb = Adb()

    def run(command, timeout):
        result = adb.run(command, timeout)
        adb.now += .9
        return result

    fps = AndroidGameFps('adb.exe', 'emulator-402', runner=run, clock=lambda: adb.now)
    assert fps.poll().reason == 'adb_unavailable'
    assert len(adb.calls) == 1
