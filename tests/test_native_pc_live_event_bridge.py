import json
import subprocess
import sys
from time import monotonic, sleep

import pytest

from test_native_pc_live_capture import BUILD, OwnedSnapshotProcess, frame_mark


def _command(path, process):
    pid, created = process.process_instance
    return [
        sys.executable, '-B', '-m', 'native_pc.live_event_bridge', '--output', str(path),
        '--producer', 'owned-stdin-test', '--producer-version', '1', '--pid', str(pid),
        '--creation-filetime', str(created), '--source-build', BUILD,
    ]


def _line(**fields):
    return json.dumps(fields).encode('utf-8') + b'\n'


def test_bridge_publishes_while_upstream_stdin_is_open(tmp_path):
    path = tmp_path / 'battle.jsonl'
    process = OwnedSnapshotProcess(123)
    mark = frame_mark(process, 0, 0)
    end = frame_mark(process, 30, 0)
    with subprocess.Popen(_command(path, process), stdin=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        try:
            child.stdin.write(_line(mark=mark, events=[]))
            child.stdin.write(_line(heartbeat=True))
            child.stdin.flush()
            deadline = monotonic() + 5
            while monotonic() < deadline and child.poll() is None:
                body = path.read_bytes() if path.is_file() else b''
                if body.count(b'\n') == 3:
                    break
                sleep(.01)
            else:
                pytest.fail('bridge did not publish a header, frame and heartbeat with stdin still open')
            header, frame, heartbeat = map(json.loads, body.splitlines())
            assert header['process_instance'] == list(process.process_instance)
            assert header['source_build'] == BUILD
            assert frame == heartbeat == dict(mark=mark, events=[])
            _, errors = child.communicate(_line(mark=end, events=[], terminal=True), timeout=5)
            assert child.returncode == 0, errors
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert json.loads(path.read_bytes().splitlines()[-1]) == dict(mark=end, events=[], terminal_mark=end)


@pytest.mark.parametrize(('suffix', 'error'), [
    (b'', b'closed before publishing a terminal frame'),
    (b'{"heartbeat":false}\n', b'expected a completed frame or explicit heartbeat'),
    (b'{"heartbeat":true,"heartbeat":true}\n', b'invalid UTF-8 JSON'),
    (b'{"heartbeat":true}', b'bounded newline-terminated record'),
])
def test_bridge_does_not_seal_an_interrupted_or_invalid_input(tmp_path, suffix, error):
    path = tmp_path / 'battle.jsonl'
    process = OwnedSnapshotProcess(123)
    mark = frame_mark(process, 0, 0)
    result = subprocess.run(_command(path, process), input=_line(mark=mark, events=[]) + suffix,
                            capture_output=True, timeout=10)
    assert result.returncode == 1 and error in result.stderr
    assert len(path.read_bytes().splitlines()) == 2
    assert json.loads(path.read_bytes().splitlines()[-1]) == dict(mark=mark, events=[])
