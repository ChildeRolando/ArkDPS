from pathlib import Path
import subprocess

import pytest

from stack_capture import capture, DURATION


@pytest.mark.parametrize('stop_early', [True, False])
def test_stop_and_timeout_both_save_owned_session(tmp_path, stop_early):
    output = tmp_path / '性能 采样' / 'battle.etl'
    calls = []

    class Stop:
        def wait(self, seconds):
            assert seconds == DURATION
            return stop_early

    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == '-stop':
            Path(args[1]).write_bytes(b'fixture')

    capture(output, Stop(), run=run, notify=lambda state: calls.append((state,)))
    assert [c[0] for c in calls] == ['-start', 'ready', 'saving', '-stop']
    assert calls[0][-2:] == calls[3][-2:]
    assert calls[0][-2] == '-instancename'


@pytest.mark.parametrize('failure,commands', [
    ('denied', ['-start']), ('timeout', ['-start', '-cancel']),
    ('save', ['-start', '-stop', '-cancel']),
    ('missing', ['-start', '-stop', '-cancel']),
])
def test_failures_never_report_a_saved_trace(tmp_path, failure, commands):
    from threading import Event
    stop = Event()
    stop.set()
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == '-start' and failure == 'denied':
            raise RuntimeError('denied')
        if args[0] == '-start' and failure == 'timeout':
            raise subprocess.TimeoutExpired('wpr', 30)
        if args[0] == '-stop' and failure == 'save':
            raise RuntimeError('write error')

    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        capture(tmp_path / 'game.etl', stop, run=run)
    assert [c[0] for c in calls] == commands
    assert len({c[-1] for c in calls}) == 1
