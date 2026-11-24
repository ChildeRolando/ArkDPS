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
        script.write_text('''
import json
import sys

IDENTITY = {'protocol': 1, 'provider': 'internal-test-host', 'source_version': 1}

def reply(request, result, **fields):
    assert request['version'] == 1 and type(request['version']) is int
    response = dict(dict(version=1, id=request['id'], result=result), **fields)
    print(json.dumps(response), flush=True)

''' + source, encoding='utf-8')
        probe = internal_event_source.InternalEventProbe(script)
        probes.append(probe)
        return probe, processes

    yield prepare
    for probe in probes:
        probe.stop()


def test_internal_transport_exposes_existing_event_probe_contract(host):
    probe, processes = host("""
mark, events, terminal = {}, [], {}
for line in sys.stdin:
    request = json.loads(line)
    operation = request['op']
    if operation == 'hello':
        print('non-protocol diagnostics', file=sys.stderr, flush=True)
        reply(request, IDENTITY)
    elif operation == 'advance':
        mark, events, terminal = request['mark'], request['events'], request['terminal_mark']
        reply(request, True)
    elif operation == 'mark':
        reply(request, mark)
    elif operation == 'terminal':
        reply(request, terminal or {})
    elif operation == 'read':
        reply(request, dict(events=[] if request.get('baseline') else
                            [event for event in events if event['sequence'] <= request['sequence']]))
    elif operation == 'commit':
        reply(request, dict(acknowledged=request['sequence'] if request['sequence'] == mark['sequence'] else None))
    elif operation == 'stop':
        reply(request, True)
        break
""")
    probe.start()
    assert probe.status == ObserverStatus.READY
    assert probe.probe_modes['probe_source'] == dict(provider='internal-test-host', protocol=1, source_version=1)
    assert _terminal_boundary(probe) == {
        'terminal_diagnostic': {'status': 'missing', 'reason': 'no_raw_terminal_boundary'},
        'terminal_raw_mark': {}}

    mark = {'sequence': 1, 'source_build': 'fixture-build', 'native_session': 'fixture-battle'}
    events = [{'sequence': 1, 'kind': 'damage', 'source': 'char_砾'}]
    assert probe.advance(mark, events, terminal_mark=mark) is True
    assert probe.mark() == mark
    batch = read_event_batch(probe, 1)
    assert batch == {'events': events, 'probe_contract': NATIVE_PC_CONTRACT.describe()}
    assert read_event_batch(probe, 1, baseline=True) == dict(batch, events=[])
    assert _terminal_boundary(probe) == {
        'terminal_boundary': mark, 'terminal_raw_mark': {'terminal_boundary': mark},
        'terminal_diagnostic': {'status': 'candidate', 'reason': 'raw_boundary_received'}}
    assert acknowledge_event_batch(probe, 0) is False
    assert acknowledge_event_batch(probe, 1) is True

    probe.stop()
    probe.stop()
    assert processes[0].poll() == 0
    assert probe.status == ObserverStatus.STOPPED
    assert not probe.cleanup_errors


def test_internal_transport_rejects_wrong_response_id_and_reaps_child(host):
    probe, processes = host("""
for line in sys.stdin:
    request = json.loads(line)
    if request['op'] == 'hello':
        reply(request, IDENTITY)
    else:
        reply(request, {}, id=request['id'] + 1)
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
import time

hello = json.loads(sys.stdin.readline())
reply(hello, IDENTITY)
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
import time

hello = json.loads(sys.stdin.readline())
reply(hello, IDENTITY)
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


@pytest.mark.parametrize('late_duplicate', [False, True])
def test_internal_transport_retries_lost_acknowledgement_with_same_id(host, late_duplicate):
    probe, processes = host(f"""
first_ack = None
for line in sys.stdin:
    request = json.loads(line)
    operation = request['op']
    if operation == 'hello':
        reply(request, IDENTITY)
    elif operation == 'commit':
        if first_ack is None:
            first_ack = line
            continue
        assert line == first_ack
        if {late_duplicate!r}:
            reply(request, dict(acknowledged=request['sequence']))
        reply(request, dict(acknowledged=request['sequence']))
    elif operation == 'mark':
        reply(request, dict(sequence=7))
    elif operation == 'stop':
        reply(request, True)
        break
""")
    probe.start()
    probe.acknowledgement_timeout = 0.15
    assert acknowledge_event_batch(probe, 7) is True
    assert probe.mark() == {'sequence': 7}
    probe.stop()
    assert processes[0].poll() == 0


def test_internal_transport_never_confirms_a_different_acknowledgement(host):
    probe, processes = host("""
reply(json.loads(sys.stdin.readline()), IDENTITY)
reply(json.loads(sys.stdin.readline()), dict(acknowledged=8))
for line in sys.stdin:
    pass
""")
    probe.start()
    assert acknowledge_event_batch(probe, 7) is False
    assert probe.status == ObserverStatus.FAILED
    assert 'does not match its watermark' in probe.error
    assert processes[0].poll() is not None


def test_internal_transport_reports_acknowledgement_failure_without_raising(host):
    probe, processes = host("""
reply(json.loads(sys.stdin.readline()), IDENTITY)
for line in sys.stdin:
    pass
""")
    probe.start()
    probe.acknowledgement_timeout = 0.15
    assert acknowledge_event_batch(probe, 7) is False
    assert probe.status == ObserverStatus.FAILED
    assert 'commit timed out' in probe.error
    assert processes[0].poll() is not None


@pytest.mark.parametrize(('envelope', 'identity', 'error'), [
    (2, {}, 'response version'),
    (True, {}, 'response envelope'),
    (1, {'source_version': 2}, 'identity or version'),
    (1, {'protocol': True}, 'identity or version'),
    (1, {'provider': 'unverified'}, 'identity or version'),
])
def test_internal_transport_checks_host_identity_before_ready(host, envelope, identity, error):
    probe, processes = host(f"""
request = json.loads(sys.stdin.readline())
reply(request, dict(IDENTITY, **{identity!r}), version={envelope!r})
for line in sys.stdin:
    pass
""")
    with pytest.raises(RuntimeError, match=error):
        probe.start()
    assert probe.status == ObserverStatus.FAILED
    assert not probe.probe_modes
    assert processes[0].poll() is not None


@pytest.mark.parametrize('value', ['{"x":1,"x":2}', '{"x":1e999}', '{"x":NaN}'])
def test_internal_transport_rejects_ambiguous_event_json(host, value):
    probe, _ = host(f"""
reply(json.loads(sys.stdin.readline()), IDENTITY)
request = json.loads(sys.stdin.readline())
prefix = json.dumps(dict(version=1, id=request['id']))[:-1]
print(prefix + ',"result":' + {value!r} + '}}', flush=True)
""")
    probe.start()
    with pytest.raises(RuntimeError, match='invalid internal event host JSON response'):
        probe.mark()


def test_internal_transport_reaps_host_which_does_not_exit_after_stop(host):
    probe, processes = host("""
import time

reply(json.loads(sys.stdin.readline()), IDENTITY)
reply(json.loads(sys.stdin.readline()), True)
time.sleep(30)
""")
    probe.start()
    probe.shutdown_timeout = 0.15
    with pytest.raises(RuntimeError, match='did not exit after confirming shutdown'):
        probe.stop()
    assert probe.status == ObserverStatus.STOPPED
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed
    probe.stop()
