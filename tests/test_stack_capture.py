from pathlib import Path
import subprocess
from xml.etree import ElementTree

import pytest

from stack_capture import capture, DURATION, PROFILE


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
    assert calls[0][1:-2] == (str(PROFILE) + '!ArkDPS', '-filemode', '-recordtempto', output.parent)
    assert calls[0][-2:] == calls[3][-2:]
    assert calls[0][-2] == '-instancename'


def test_file_profile_keeps_frame_and_stack_sources_in_sequential_files():
    definitions = ElementTree.parse(PROFILE).getroot().find('Profiles')
    profile = definitions.find("Profile[@Id='ArkDPS.Verbose.File']")
    assert profile.attrib['LoggingMode'] == 'File'
    kernel = profile.find("Collectors/SystemCollectorId[@Value='Kernel']")
    frames = profile.find("Collectors/EventCollectorId[@Value='Frames']")
    for collector in (kernel, frames):
        limit = collector.find('MaximumFileSize')
        assert limit.attrib['FileMode'] == 'Sequential'
        assert int(limit.attrib['Value']) > 0
    assert kernel.find("SystemProviderId[@Value='Threads']") is not None
    assert frames.find("EventProviders/EventProviderId[@Value='DXGI']") is not None
    stacks = definitions.findall("SystemProvider[@Id='Threads']/Stacks/Stack")
    assert {stack.attrib['Value'] for stack in stacks} == {'SampledProfile', 'CSwitch'}


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
