import subprocess
import sys

import pytest

from battle_lifecycle import _terminal_boundary
from event_capture import acknowledge_event_batch, read_event_batch
from event_observer import ObserverStatus
from event_probe import NATIVE_PC_CONTRACT
from native_pc import internal_event_source


@pytest.fixture
def host(monkeypatch, tmp_path):
    popen = subprocess.Popen
    processes = []
    probes = []

    def launch(command, **kwargs):
        process = popen([sys.executable, '-X', 'utf8', *command], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(internal_event_source.subprocess, 'Popen', launch)

    def prepare(source):
        script = tmp_path / 'test event host.py'
        script.write_text(source, encoding='utf-8')
        probe = internal_event_source.InternalEventProbe(script)
        probes.append(probe)
        return probe, processes

    yield prepare
    for probe in probes:
        probe.stop()


def test_internal_transport_exposes_existing_event_probe_contract(host):
    probe, processes = host("""
import json
import sys

def reply(request, result):
    print(json.dumps(dict(id=request['id'], result=result)), flush=True)

mark, events, terminal = {}, [], {}
for line in sys.stdin:
    request = json.loads(line)
    operation = request['op']
    if operation == 'hello':
        print('non-protocol diagnostics', file=sys.stderr, flush=True)
        reply(request, dict(protocol=1))
    elif operation == 'advance':
        mark, events, terminal = request['mark'], request['events'], request['terminal_mark']
        reply(request, True)
    elif operation == 'mark':
        reply(request, mark)
    elif operation == 'terminal':
        reply(request, terminal or {})
    elif operation == 'read':
        reply(request, dict(events=[event for event in events if event['sequence'] <= request['sequence']]))
    elif operation == 'commit':
        reply(request, request['sequence'] == mark['sequence'])
    elif operation == 'stop':
        reply(request, True)
        break
""")
    probe.start()
    assert probe.status == ObserverStatus.READY
    assert probe.probe_modes['probe_source']['protocol'] == 1
    assert _terminal_boundary(probe) is None

    mark = {'sequence': 1, 'source_build': 'fixture-build', 'native_session': 'fixture-battle'}
    events = [{'sequence': 1, 'kind': 'damage', 'source': 'char_砾'}]
    assert probe.advance(mark, events, terminal_mark=mark) is True
    assert probe.mark() == mark
    batch = read_event_batch(probe, 1)
    assert batch == {'events': events, 'probe_contract': NATIVE_PC_CONTRACT.describe()}
    assert _terminal_boundary(probe) == {'terminal_boundary': mark}
    assert acknowledge_event_batch(probe, 0) is False
    assert acknowledge_event_batch(probe, 1) is True

    probe.stop()
    probe.stop()
    assert processes[0].poll() == 0
    assert probe.status == ObserverStatus.STOPPED
    assert not probe.cleanup_errors


def test_internal_transport_rejects_wrong_response_id_and_reaps_child(host):
    probe, processes = host("""
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request['op'] == 'hello':
        print(json.dumps(dict(id=request['id'], result=dict(protocol=1))), flush=True)
    else:
        print(json.dumps(dict(id=request['id'] + 1, result={})), flush=True)
""")
    probe.start()
    with pytest.raises(RuntimeError, match='does not match the request ID'):
        probe.mark()
    assert probe.status == ObserverStatus.FAILED
    assert probe.error
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


def test_internal_transport_deadline_covers_blocked_stdin(host):
    probe, processes = host("""
import json
import sys
import time

hello = json.loads(sys.stdin.readline())
print(json.dumps(dict(id=hello['id'], result=dict(protocol=1))), flush=True)
time.sleep(30)
""")
    probe.start()
    probe.request_timeout = 0.2
    with pytest.raises(RuntimeError, match='advance timed out'):
        probe.advance({'description': 'x' * (1024 * 1024)}, [])
    assert probe.status == ObserverStatus.FAILED
    assert processes[0].poll() is not None
    assert not probe.cleanup_errors


def test_internal_transport_rejects_oversize_reply_without_waiting_for_newline(host):
    probe, processes = host("""
import json
import sys
import time

hello = json.loads(sys.stdin.readline())
print(json.dumps(dict(id=hello['id'], result=dict(protocol=1))), flush=True)
sys.stdin.readline()
sys.stdout.write('x' * 4096)
sys.stdout.flush()
time.sleep(30)
""")
    probe.max_response_bytes = 1024
    probe.start()
    with pytest.raises(RuntimeError, match='frame length or terminator'):
        probe.mark()
    assert probe.status == ObserverStatus.FAILED
    assert processes[0].poll() is not None
