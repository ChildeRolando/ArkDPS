"""WPF worker surface for managed event observation and manual retry."""
import io
import json
import queue
from types import SimpleNamespace

import pytest

import desktop_bridge
from desktop_bridge import CollectionWorker, DesktopState, PROTOCOL_VERSION, event_label, serve


def test_event_labels_distinguish_closed_from_failed_preparation():
    closed = event_label('unavailable')
    failed = event_label('failed', '设备未授予 root')
    assert '已关闭' in closed and '未就绪' in failed
    assert '未就绪' not in closed
    assert '设备未授予 root' in failed


def test_snapshot_exposes_event_diagnostic_and_code(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('event', ('failed', '缺少设备端 frida-server 资源'))
    state.accept('event_diagnostic', dict(state='failed', code='missing_server',
                                          message='缺少设备端 frida-server 资源',
                                          hint='使用完整发行包', steps={'resolve': 'failed'}))
    snapshot = state.snapshot()
    assert snapshot['event_code'] == 'missing_server'
    assert snapshot['event_diagnostic']['steps'] == {'resolve': 'failed'}
    assert '未就绪' in snapshot['event_text']


class Worker:
    def __init__(self):
        self.events = queue.Queue()
        self.effective = (None, False)
        self.retried = 0
        self.closed = False

    def retry_events(self):
        self.retried += 1

    def close(self):
        self.closed = True


def test_event_retry_command_requests_an_in_session_retry(tmp_path):
    state = DesktopState(tmp_path)
    worker = Worker()
    commands = ''.join(json.dumps(command) + '\n' for command in (
        dict(version=PROTOCOL_VERSION, op='hello'),
        dict(version=PROTOCOL_VERSION, op='event_retry'),
        dict(version=PROTOCOL_VERSION, op='quit')))
    output = io.StringIO()
    serve(io.StringIO(commands), output, state, worker)
    assert worker.retried == 1 and worker.closed
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [reply['kind'] for reply in replies] == ['hello', 'state', 'bye']
    assert replies[1]['state']['event_text']


def test_event_retry_requires_a_connected_session(tmp_path):
    worker = CollectionWorker(DesktopState(tmp_path))
    with pytest.raises(RuntimeError, match='尚未连接'):
        worker.retry_events()
    session = SimpleNamespace()
    worker.session = session
    worker.retry_events()
    assert worker.retry.is_set()


def test_worker_manages_the_bundled_server_only_without_an_explicit_endpoint(tmp_path):
    state = DesktopState(tmp_path)
    assert CollectionWorker(state).manage_server is True
    assert CollectionWorker(state, manage_server=False).manage_server is False


def test_worker_connects_with_managed_server_and_reports_diagnostic(tmp_path, monkeypatch):
    diagnostics = SimpleNamespace(as_dict=lambda: {'status': 'ready'},
                                  selected_serial='127.0.0.1:16384',
                                  version_actual='2.7.91')
    target = SimpleNamespace(adb_path='adb.exe', serial='127.0.0.1:16384',
                             diagnostics=diagnostics)
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)
    state = DesktopState(tmp_path/'state')
    worker = CollectionWorker(state)
    seen = {}

    class Session:
        cleanup_errors = {}
        event_status = 'failed'
        event_error = '设备未授予 root'
        event_diagnostic = dict(state='failed', code='no_root', steps={'root': 'failed'})

        def __init__(self, *_args, **kwargs):
            seen.update(kwargs)
            self.collector = SimpleNamespace(diagnostics=diagnostics)
            self.runtime = None

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            worker.stop.set()

        def read(self):
            return None

        def close(self):
            pass

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(state.connection, True)
    worker.thread.start()
    worker.thread.join()
    assert seen['manage_server'] is True
    assert seen['frida_remote'] == '127.0.0.1:27043'
    kinds = []
    while True:
        try:
            kinds.append(worker.events.get_nowait())
        except queue.Empty:
            break
    assert ('event', ('failed', '设备未授予 root')) in kinds
    assert ('event_diagnostic', Session.event_diagnostic) in kinds
