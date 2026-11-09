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
