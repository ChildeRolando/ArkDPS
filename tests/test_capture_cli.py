import json
from types import SimpleNamespace

import pytest

import capture_cli
from collection_session import CollectionSession
from event_capture import CaptureRetry
from test_collection_session import Collector, LiveObserver


def options(tmp_path):
    return SimpleNamespace(expect_head='candidate', stop_file=None,
                           output_dir=tmp_path / 'evidence', stage='OF-8',
                           frida_remote='remote', adb='adb', serial='serial',
                           capture_seconds=.01, wait_battle_seconds=1, baseline_timeout=1,
                           maa_job=None, maa_log=None, require_progress=False, require_events=True)


class Session:
    instances = []
    failed = False

    def __init__(self, adb, serial, *, frida_remote, audit):
        self.audit = audit
        self.calls = 0
        self.closed = False
        self.capture = SimpleNamespace(adapter=SimpleNamespace(result=lambda: {'rows': {}}))
        self.instances.append(self)

    def connect(self):
        self.event_status = 'ready'
        self.event_error = None
        self.audit('observer_ready', {'remote': 'remote'})

    def locate(self, **kwargs):
        pass

    def read(self):
        self.calls += 1
        if self.calls == 1:
            raise CaptureRetry('transient frame mismatch')
        sample = {'frame': 2, 'game_time': 1, 'event_status': 'gap' if self.failed else 'recording'}
        self.audit('published', sample)
        return sample

    def close(self):
        self.closed = True
        self.audit('coverage_closed', {'rows': {}})


def stub_git_and_clock(monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(capture_cli.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(capture_cli.time, 'sleep', lambda seconds: setattr(clock, 'now', clock.now + seconds))
    monkeypatch.setattr(capture_cli.subprocess, 'check_output',
                        lambda command, **kwargs: 'candidate\n' if command[1] == 'rev-parse' else '')
    Session.failed = False


def test_cli_retries_and_writes_ready_raw_journal_and_summary(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    result = capture_cli.run_capture(args, session_factory=Session)
    assert result['samples'] == 1 and result['retries'] == 1
    assert result['error'] is None
    assert Session.instances[-1].closed
    assert json.loads((args.output_dir / 'READY.json').read_text())['head'] == 'candidate'
    kinds = [json.loads(line)['kind'] for line in (args.output_dir / 'capture.jsonl').read_text().splitlines()]
    assert kinds == ['metadata', 'observer_ready', 'retry', 'published', 'coverage_closed']


def test_cli_retains_failure_evidence_and_cleans_up(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    Session.failed = True
    args = options(tmp_path)
    with pytest.raises(RuntimeError):
        capture_cli.run_capture(args, session_factory=Session)
    result = json.loads((args.output_dir / 'summary.json').read_text())
    assert 'event coverage is unavailable' in result['error']
    assert Session.instances[-1].closed


def test_cli_refuses_overwriting_evidence_before_connecting(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    args = options(tmp_path)
    args.output_dir.mkdir()
    previous = args.output_dir / 'capture.jsonl'
    previous.write_text('earlier run')
    before = len(Session.instances)
    with pytest.raises(RuntimeError):
        capture_cli.run_capture(args, session_factory=Session)
    assert previous.read_text() == 'earlier run' and len(Session.instances) == before


@pytest.mark.parametrize('require_events', [False, True])
def test_cli_start_failure_reports_readiness_and_optionally_continues_snapshots(tmp_path, monkeypatch,
                                                                            require_events):
    stub_git_and_clock(monkeypatch)
    calls = []

    class MissingFrida(LiveObserver):
        def start(self):
            raise ModuleNotFoundError('optional frida missing')

    def factory(adb, serial, **kwargs):
        return CollectionSession(adb, serial, collector=Collector(calls),
                                 observer_factory=MissingFrida, **kwargs)

    args = options(tmp_path)
    args.require_events = require_events
    if require_events:
        with pytest.raises(RuntimeError, match='optional frida missing'):
            capture_cli.run_capture(args, session_factory=factory)
    else:
        capture_cli.run_capture(args, session_factory=factory)
    summary = json.loads((args.output_dir / 'summary.json').read_text(encoding='utf-8'))
    if require_events:
        assert not (args.output_dir / 'READY.json').exists()
        assert summary['samples'] == 0 and 'optional frida missing' in summary['error']
    else:
        ready = json.loads((args.output_dir / 'READY.json').read_text(encoding='utf-8'))
        assert ready['event_status'] == 'failed' and ready['event_error'] == 'optional frida missing'
        assert summary['error'] is None and summary['samples'] == 1
        assert summary['last_sample']['event_status'] == 'failed'
        row = summary['last_sample']['statistics']['rows']['char_a']
        assert row['damage'] == 100 and row['shield_absorbed'] is None
    assert calls[-1] == 'collector_close'


def test_normal_cli_continues_after_event_gap(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    Session.failed = True
    args = options(tmp_path)
    args.require_events = False
    summary = capture_cli.run_capture(args, session_factory=Session)
    assert summary['error'] is None
    assert summary['last_sample']['event_status'] == 'gap'
    assert Session.instances[-1].closed


@pytest.mark.parametrize('flags, remote', [([], '127.0.0.1:27043'),
                                         (['--no-events'], None),
                                         (['--frida-remote', 'custom:1234'], 'custom:1234')])
def test_app_capture_entry_uses_shared_event_policy(tmp_path, monkeypatch, flags, remote):
    import app
    received = []
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--capture-seconds', '1', *flags])
    monkeypatch.setattr(capture_cli, 'run_capture', received.append)

    class Lock:
        def bind(self, _address):
            pass

        def listen(self, _backlog):
            pass

    monkeypatch.setattr(app.socket, 'socket', Lock)
    app.main()
    assert received[0].frida_remote == remote
    assert received[0].require_events is False


def test_app_rejects_disabled_events_in_event_acceptance(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--no-events', '--require-events'])
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 2
