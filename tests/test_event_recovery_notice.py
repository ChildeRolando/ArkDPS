"""One-shot native recovery is explicit and never offers a destructive reconnect."""
import pytest

from panel_model import EventAvailability, rate_notice
from desktop_bridge import DesktopState
from test_desktop_bridge import sample, cmd
from test_native_fault_snapshot_fallback import owned_session, fail_event_pipe


def test_native_snapshot_fallback_marks_only_live_sample_recovery(tmp_path):
    session, process, source, records, acks, cleanup = owned_session(tmp_path)
    fail_event_pipe(source)
    process.frame, process.when = 30, 1
    result = session.read()
    assert result['event_status'] == 'failed'
    assert result['event_recovery'] == 'restart_game'
    assert 'event_recovery' not in session.runtime.sessions.latest_sample
    assert acks == [] and cleanup == []


def test_restart_game_notice_has_no_invalid_retry_and_preserves_unknown():
    available, reason, hint, action, label = rate_notice(
        EventAvailability('failed', 'owned failure', recovery='restart_game'))
    assert available is False
    assert '原生事件采集已中断' in reason
    assert '独立累计快照继续' in hint and '正常重启游戏' in hint
    assert '无法补采' in hint
    assert action == label == ''
    assert 'Frida' not in hint


@pytest.mark.parametrize('recovery', [None, 'other'])
def test_generic_failure_keeps_existing_reconnect_contract(recovery):
    _, _, hint, action, label = rate_notice(
        EventAvailability('failed', 'restart game in arbitrary error text', recovery=recovery))
    assert action == 'retry' and label == '重连观察者'
    assert 'Frida' in hint


def test_history_never_offers_manual_recovery_of_missing_events():
    _, _, hint, action, _ = rate_notice(
        EventAvailability('failed', live=False, recovery='restart_game'))
    assert '历史记录无法补采' in hint
    assert '重启游戏' not in hint and action == 'settings'


def test_desktop_live_projection_passes_explicit_recovery_and_drops_stale_marker(tmp_path):
    state = DesktopState(tmp_path)
    value = sample()
    value.update(event_status='failed', event_recovery='restart_game')
    state.accept('sample', value)
    config = dict(state.layout.panels[0].to_dict(), rate_basis='engagement')
    cmd(state, 'panel_update', config=config)
    view = state.snapshot()['panels'][0]
    assert view['rate_available'] is False
    assert '正常重启游戏' in view['rate_hint']
    assert view['rate_action'] == view['rate_action_label'] == ''
    assert all(entry['rate'] is None for entry in view['entries'])
    state.accept('scanning', True)
    state.accept('observer', ('failed', 'new observer failure'))
    view = state.snapshot()['panels'][0]
    assert view['rate_action'] == 'retry'
    assert 'Frida' in view['rate_hint']
