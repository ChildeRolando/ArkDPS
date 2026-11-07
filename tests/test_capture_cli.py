import json
from types import SimpleNamespace

import pytest

import capture_cli
from event_capture import CaptureRetry


def options(tmp_path):
    return SimpleNamespace(expect_head='candidate', stop_file=None,
                           output_dir=tmp_path / 'evidence', stage='OF-8',
                           frida_remote='remote', adb='adb', serial='serial',
                           capture_seconds=.01, wait_battle_seconds=1, baseline_timeout=1,
                           maa_job=None, maa_log=None, require_progress=False)


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
