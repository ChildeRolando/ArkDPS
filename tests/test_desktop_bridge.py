"""WPF boundary regression tests; no device access or window required."""
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from desktop_bridge import DesktopState, CollectionWorker, PROTOCOL_VERSION, serve
from layout_store import LayoutStore
from test_battle_history import record
from test_gui import sample


def cmd(state, op, **fields):
    return state.command(dict(version=PROTOCOL_VERSION, op=op, **fields))


def test_worker_import_does_not_import_tk_or_frida():
    result = subprocess.run([sys.executable, '-c',
        "import desktop_bridge, sys; assert 'tkinter' not in sys.modules; assert 'frida' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_first_launch_and_default_model_ignores_legacy_projection(tmp_path):
    state = DesktopState(tmp_path)
    assert len(state.layout.panels) == 1 and not state.layout.huds
    assert state.layout.theme == 'lone-trail'
    data = sample()
    before = copy.deepcopy(data)
    state.accept('sample', data)
    view = state.snapshot()['panels'][0]
    assert [e['value'] for e in view['entries']] == [1230., 500.]
    assert data == before
    assert view['source']['a']['shield_absorbed'] is None


def test_rate_does_not_change_ranks_bars_shares_and_shield_rejects_engagement(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', sample())
    old = state.snapshot()['panels'][0]
    config = dict(state.layout.panels[0].to_dict(), rate_basis='engagement')
    cmd(state, 'panel_update', config=config)
    new = state.snapshot()['panels'][0]
    for key in ('owner_id', 'value', 'bar_ratio', 'share'):
        assert [e[key] for e in old['entries']] == [e[key] for e in new['entries']]
    assert all(e['rate'] is None for e in new['entries'])
    with pytest.raises(ValueError):
        cmd(state, 'panel_update', config=dict(config, category='shield'))


def test_theme_and_panel_management_persist_without_touching_live_or_history(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', sample())
    state.history.save(record())
    state.refresh_history()
    selected, last = state.selected, state.last
    cmd(state, 'panel_duplicate', panel_id='default')
    second = state.layout.panels[-1].panel_id
    assert second != 'default'
    hud = dict(panel_id=second, x=10, y=20, width=410, height=430, opacity=.83, locked=True)
    cmd(state, 'placement', gui=dict(x=50, y=60, width=1200, height=800), huds=[hud])
    cmd(state, 'theme', theme='original')
    assert state.last is last and state.selected is selected
    assert state.layout.huds[0].to_dict() == hud
    restarted = DesktopState(tmp_path)
    assert restarted.layout == state.layout
    cmd(state, 'panel_delete', panel_id=second)
    assert not state.layout.huds
    assert state.last is last
    with pytest.raises(ValueError, match='至少'):
        cmd(state, 'panel_delete', panel_id='default')


def test_history_selection_is_immutable_across_refresh_eviction_and_new_battle(tmp_path):
    state = DesktopState(tmp_path)
    first = record(1)
    state.history.save(first)
    state.refresh_history()
    selected = state.selected
    for i in range(2, 103):
        state.history.save(record(i))
    cmd(state, 'history_refresh')
    assert selected.battle_id not in state.records
    assert state.selected is selected
    state.accept('sample', sample())
    new = dict(sample(), battle_id='new', session='new')
    state.accept('sample', new)
    assert state.snapshot()['battle_id'] == 'new'
    assert state.snapshot()['history_selected']['battle_id'] == first.battle_id
    assert state.snapshot()['panels'][0]['entries'][0]['value'] == 1230


@pytest.mark.parametrize('version', [None, 0, 2, '1', True])
def test_incompatible_protocol_refuses_before_state_mutation(tmp_path, version):
    state = DesktopState(tmp_path)
    # JSON bool must not be accepted as an integer protocol version.
    with pytest.raises(ValueError, match='版本'):
        state.command(dict(version=version, op='theme', theme='original'))
    assert state.layout.theme == 'lone-trail'


def test_json_lines_handshake_state_error_and_clean_exit(tmp_path):
    state = DesktopState(tmp_path)
    messages = [dict(version=1, id=1, op='snapshot'),
                dict(version=999, id=2, op='hello'),
                dict(version=1, id=3, op='hello'),
                dict(version=1, id=4, op='snapshot'),
                dict(version=1, id=5, op='quit')]
    output = io.StringIO()
    serve(io.StringIO(''.join(json.dumps(m)+'\n' for m in messages)), output, state)
    values = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [v['kind'] for v in values] == ['error', 'error', 'hello', 'snapshot', 'bye']
    assert [v['id'] for v in values] == [1, 2, 3, 4, 5]
    assert values[2]['result']['transport'] == 'stdio-jsonl'
    assert values[3]['state']['status'] == 'waiting'


def test_bad_json_is_diagnostic_not_protocol_corruption(tmp_path):
    output = io.StringIO()
    serve(io.StringIO('not JSON\n'), output, DesktopState(tmp_path))
    value = json.loads(output.getvalue())
    assert value['kind'] == 'error' and not value['ok']


def test_statuses_keep_last_valid_values_and_do_not_convert_unknown_to_zero(tmp_path):
    state = DesktopState(tmp_path)
    assert state.status()[0] == 'waiting'
    data = sample()
    data['event_status'] = 'recording'
    state.accept('sample', data)
    assert state.status()[0] == 'live'
    state.last_progress -= 1
    assert state.status()[0] == 'paused'
    data['captured_monotonic'] -= 3
    assert state.status()[0] == 'stale'
    state.accept('error', 'lost')
    assert state.status()[0] == 'disconnected'
    assert state.snapshot()['panels'][0]['entries'][0]['value'] == 1230
    resumed = dict(sample(), frame=670)
    state.accept('sample', resumed)
    assert state.status()[0] == 'degraded'


def test_export_exact_json_and_archive_protection(tmp_path):
    state = DesktopState(tmp_path)
    original = record()
    target = tmp_path / 'share.json'
    cmd(state, 'export', format='json', path=str(target), record=original.to_dict())
    assert json.loads(target.read_text('utf-8')) == original.to_dict()
    with pytest.raises(FileExistsError):
        cmd(state, 'export', format='json', path=str(target), record=original.to_dict())
    with pytest.raises(ValueError, match='历史目录'):
        cmd(state, 'export', format='json', path=str(state.history.directory / 'bad.json'), record=original.to_dict())


def test_failed_layout_write_does_not_publish_candidate(tmp_path):
    state = DesktopState(tmp_path)
    with patch.object(state.store, 'save', side_effect=OSError('disk full')):
        with pytest.raises(OSError):
            cmd(state, 'theme', theme='original')
    assert state.layout.theme == 'lone-trail'


def test_connection_apply_layout_failure_keeps_connection_unchanged(tmp_path):
    state = DesktopState(tmp_path)
    before_connection, before_layout = state.connection, state.layout
    with patch.object(state.store, 'save', side_effect=OSError('layout disk full')):
        with pytest.raises(OSError, match='layout disk full'):
            cmd(state, 'connection_apply',
                config={'auto_detect': False, 'serial': '127.0.0.1:16384', 'adb_path': 'new-adb'},
                events=False)
    assert state.connection == before_connection
    assert state.layout == before_layout
    assert not state.connection_store.path.exists()


def test_connection_apply_rolls_layout_back_when_connection_write_fails(tmp_path):
    state = DesktopState(tmp_path)
    before_connection, before_layout = state.connection, state.layout
    with patch.object(state.connection_store, 'save', side_effect=OSError('connection disk full')):
        with pytest.raises(OSError, match='connection disk full'):
            cmd(state, 'connection_apply',
                config={'auto_detect': False, 'serial': '127.0.0.1:16384', 'adb_path': 'new-adb'},
                events=False)
    assert state.connection == before_connection
    assert state.layout == before_layout
    assert json.loads(state.store.path.read_text('utf-8')) == before_layout.to_dict()
    assert not state.connection_store.path.exists()


def test_connection_apply_reports_layout_rollback_failure(tmp_path):
    state = DesktopState(tmp_path)
    before_layout = state.layout
    real_save = state.store.save
    calls = 0

    def fail_rollback(layout):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('rollback disk full')
        real_save(layout)

    with patch.object(state.store, 'save', side_effect=fail_rollback), \
         patch.object(state.connection_store, 'save', side_effect=OSError('connection disk full')):
        with pytest.raises(RuntimeError, match='connection disk full.*rollback disk full'):
            cmd(state, 'connection_apply',
                config={'auto_detect': False, 'serial': '127.0.0.1:16384', 'adb_path': 'new-adb'},
                events=False)
    assert calls == 2
    assert state.layout == before_layout  # in-memory values publish only after both writes
    assert json.loads(state.store.path.read_text('utf-8')) != before_layout.to_dict()


def test_layout_v1_migrates_into_same_v3_owner(tmp_path):
    raw = dict(version=1, panels=[dict(panel_id='old', category='damage', ranking='total', rate_basis='recent_5s')])
    (tmp_path / 'layout.json').write_text(json.dumps(raw), encoding='utf-8')
    state = DesktopState(tmp_path)
    assert state.layout.panels[0].panel_id == 'old'
    cmd(state, 'theme', theme='original')
    assert json.loads((tmp_path/'layout.json').read_text())['version'] == 3


def test_cleanup_failure_keeps_session_owned_and_prevents_successful_release(tmp_path):
    state = DesktopState(tmp_path)
    worker = CollectionWorker(state)
    class Session:
        cleanup_errors = {'observer': ['busy']}
        fail = True
        def close(self):
            if self.fail:
                raise OSError('busy')
    session = worker.session = Session()
    assert not worker.release()
    assert worker.session is session
    session.fail = False
    assert worker.release() and worker.session is None
    assert worker.retired_errors  # successful retry cannot erase prior failure


def test_worker_preserves_final_audit_failure_after_collection_close_retry(tmp_path):
    from collection_session import CollectionSession
    from types import SimpleNamespace

    calls, stopped = [], []
    fail_once = True

    def audit(kind, data):
        nonlocal fail_once
        if kind == 'stopped':
            stopped.append(data)
            if fail_once:
                fail_once = False
                raise OSError('final audit write failed')

    worker = CollectionWorker(DesktopState(tmp_path))
    session = CollectionSession('adb', 'serial', collector=SimpleNamespace(
        close=lambda: calls.append('collector_close')), audit=audit)
    worker.session = session
    assert not worker.release()
    assert worker.session is session
    assert worker.release()
    assert worker.session is None
    assert calls == ['collector_close']
    assert len(stopped) == 2
    assert any('audit' in error and 'final audit write failed' in error
               for error in worker.retired_errors)


def test_worker_startup_setup_error_is_reported(tmp_path, monkeypatch):
    import desktop_bridge

    blocked_root = tmp_path / 'not-a-directory'
    blocked_root.write_text('file blocks runtime directory', encoding='utf-8')
    monkeypatch.setattr(desktop_bridge, 'ROOT', blocked_root)
    worker = CollectionWorker(DesktopState(tmp_path/'state'))
    worker.run()
    kind, error = worker.events.get_nowait()
    assert kind == 'error' and error
    assert worker.session is None


def test_worker_retries_final_cleanup_before_closing_audit_stream(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import desktop_bridge

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    diagnostics = SimpleNamespace(
        as_dict=lambda: {'status': 'ready'},
        selected_serial='127.0.0.1:16384',
        version_actual='test',
    )
    target = SimpleNamespace(adb_path='adb', serial='127.0.0.1:16384', diagnostics=diagnostics)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)

    state = DesktopState(tmp_path/'state')
    worker = CollectionWorker(state)

    class Session:
        cleanup_errors = {}

        def __init__(self, *_args, audit, **_kwargs):
            self.audit = audit
            self.collector = SimpleNamespace(diagnostics=diagnostics)
            self.runtime = None
            self.attempts = 0

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            worker.stop.set()

        def read(self):
            return None

        def close(self):
            self.attempts += 1
            self.audit('cleanup_attempt', {'attempt': self.attempts})
            if self.attempts == 1:
                raise OSError('transient cleanup failure')

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(state.connection, False)
    worker.run()

    assert worker.session is None and worker.cleanup_error == ''
    audit_path = next((tmp_path/'runtime'/'captures').glob('wpf-*.jsonl'))
    events = [json.loads(line) for line in audit_path.read_text('utf-8').splitlines()]
    assert events == [
        {'kind': 'cleanup_attempt', 'data': {'attempt': 1}},
        {'kind': 'cleanup_attempt', 'data': {'attempt': 2}},
    ]


def test_worker_retains_and_reports_session_after_final_cleanup_retries_fail(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import desktop_bridge

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    diagnostics = SimpleNamespace(
        as_dict=lambda: {'status': 'ready'},
        selected_serial='127.0.0.1:16384',
        version_actual='test',
    )
    target = SimpleNamespace(adb_path='adb', serial='127.0.0.1:16384', diagnostics=diagnostics)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)
    state = DesktopState(tmp_path/'state')
    worker = CollectionWorker(state)
    sessions = []

    class Session:
        cleanup_errors = {}

        def __init__(self, *_args, audit, **_kwargs):
            self.audit = audit
            self.collector = SimpleNamespace(diagnostics=diagnostics)
            self.runtime = None
            self.attempts = 0
            sessions.append(self)

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            worker.stop.set()

        def read(self):
            return None

        def close(self):
            self.attempts += 1
            self.audit('cleanup_attempt', {'attempt': self.attempts})
            raise OSError('persistent cleanup failure')

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(state.connection, False)
    worker.thread.start()
    worker.thread.join()

    assert len(sessions) == 1 and sessions[0].attempts == 2
    assert worker.session is sessions[0]
    assert 'persistent cleanup failure' in worker.cleanup_error
    with pytest.raises(RuntimeError, match='persistent cleanup failure'):
        worker.close()
    audit_path = next((tmp_path/'runtime'/'captures').glob('wpf-*.jsonl'))
    assert len(audit_path.read_text('utf-8').splitlines()) == 2


def test_worker_releases_session_while_audit_stream_is_open(tmp_path, monkeypatch):
    from datetime import datetime as RealDateTime
    from types import SimpleNamespace
    import desktop_bridge

    class FixedDateTime:
        @staticmethod
        def now(tz):
            return RealDateTime(2026, 11, 10, 9, 30, 0, tzinfo=tz)

    monkeypatch.setattr(desktop_bridge, 'datetime', FixedDateTime)
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    diagnostics = SimpleNamespace(
        as_dict=lambda: {'status': 'ready'},
        selected_serial='127.0.0.1:16384',
        version_actual='test',
    )
    target = SimpleNamespace(adb_path='adb', serial='127.0.0.1:16384', diagnostics=diagnostics)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)

    state = DesktopState(tmp_path/'state')
    workers = []

    class Session:
        cleanup_errors = {}

        def __init__(self, *_args, audit, **_kwargs):
            self.audit = audit
            self.collector = SimpleNamespace(diagnostics=diagnostics)
            self.runtime = None

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            workers[-1].stop.set()

        def read(self):
            return None

        def close(self):
            self.audit('session_closed', {})

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    for _ in range(2):
        worker = CollectionWorker(state)
        workers.append(worker)
        worker.request(state.connection, False)
        worker.run()
        assert worker.cleanup_error == ''
        assert worker.session is None

    paths = list((tmp_path/'runtime'/'captures').glob('wpf-*.jsonl'))
    assert len(paths) == 2
    assert len({path.name for path in paths}) == 2
    for audit_path in paths:
        assert json.loads(audit_path.read_text('utf-8').splitlines()[-1]) == {
            'kind': 'session_closed', 'data': {},
        }


def test_quit_ack_is_after_collector_cleanup(tmp_path):
    order = []
    class Worker:
        import queue
        events = queue.Queue()
        def close(self):
            order.append('close')
    class Output(io.StringIO):
        def write(self, data):
            if '"bye"' in data:
                assert order == ['close']
            return super().write(data)
    serve(io.StringIO('{"version":1,"op":"hello"}\n{"version":1,"op":"quit"}\n'), Output(), DesktopState(tmp_path), Worker())
    assert order == ['close']


def test_default_app_entry_routes_to_wpf_not_tk(monkeypatch):
    import app
    import desktop_launcher
    calls = []
    monkeypatch.setattr(sys, 'argv', ['app.py', '--no-events'])
    monkeypatch.setattr(desktop_launcher, 'launch', lambda args: calls.append(args))
    monkeypatch.setattr(app.tk, 'Tk', lambda: pytest.fail('default launch created Tk'))
    app.main()
    assert len(calls) == 1 and calls[0].frida_remote == ''


def test_real_metadata_key_and_invalid_manual_connection(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', dict(sample(), battle_metadata={'stage_id': 'OF-8'}))
    assert state.snapshot()['stage'] == 'OF-8'
    before = state.connection
    with pytest.raises(ValueError, match='实例地址'):
        cmd(state, 'connection_apply', config={'auto_detect': False, 'serial': ''}, events=True)
    assert state.connection == before


def test_rescan_retains_effective_cli_target_until_explicit_settings_apply(tmp_path):
    from connection import ConnectionConfig
    import queue
    state = DesktopState(tmp_path)
    override = ConnectionConfig('override-adb', 'selected-serial', False)
    class Worker:
        events = queue.Queue()
        effective = (override, False)
        requests = []
        def request(self, config, enabled):
            self.requests.append((config, enabled))
        def close(self):
            pass
    worker = Worker()
    commands = [dict(version=1, op='hello'), dict(version=1, op='rescan'), dict(version=1, op='quit')]
    serve(io.StringIO(''.join(json.dumps(c)+chr(10) for c in commands)), io.StringIO(), state, worker)
    assert worker.requests == [(override, False)]
    assert state.connection != override


@pytest.mark.parametrize('frozen', [False, True])
def test_main_merge_self_test_precedes_wpf_and_legacy_startup(tmp_path, monkeypatch, frozen):
    import app
    import desktop_launcher
    import release_smoke
    calls = []
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(sys, 'argv', ['app.py', '--self-test', str(tmp_path/'smoke')])
    monkeypatch.setattr(release_smoke, 'run_smoke', lambda output: calls.append(output) or 7)
    monkeypatch.setattr(desktop_launcher, 'launch', lambda _: pytest.fail('self-test launched WPF'))
    monkeypatch.setattr(app, 'LayoutStore', lambda: pytest.fail('self-test loaded production settings'))
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 7
    assert calls == [tmp_path/'smoke']
    assert not (tmp_path/'smoke').exists()  # mocked self-test; no GUI/collector


@pytest.mark.parametrize('mode', ['explicit-tk', 'measurement', 'frozen-tk'])
def test_main_merge_legacy_release_modes_do_not_launch_source_wpf(tmp_path, monkeypatch, mode):
    import app
    import desktop_launcher
    class ReachedLegacyLayout(Exception):
        pass
    arguments = ['app.py']
    if mode == 'explicit-tk':
        arguments.append('--legacy-tk')
    elif mode == 'measurement':
        arguments += ['--acceptance-output', str(tmp_path/'measure'), '--acceptance-seconds', '1']
    monkeypatch.setattr(sys, 'argv', arguments)
    monkeypatch.setattr(sys, 'frozen', mode == 'frozen-tk', raising=False)
    monkeypatch.setattr(desktop_launcher, 'launch', lambda _: pytest.fail('legacy mode launched WPF'))
    def stop_before_gui_or_collection():
        raise ReachedLegacyLayout()
    monkeypatch.setattr(app, 'LayoutStore', stop_before_gui_or_collection)
    with pytest.raises(ReachedLegacyLayout):
        app.main()
    assert not (tmp_path/'measure').exists()
