"""A recovered transport must neither inherit nor erase battle coverage facts."""
import copy

import pytest

from connection import ConnectionConfig
from desktop_bridge import DesktopState
from sample_data import sample


@pytest.mark.parametrize('old_status', ['gap', 'failed', 'finished'])
@pytest.mark.parametrize('new_transport', ['attaching', 'ready'])
def test_rescan_freezes_old_battle_without_reusing_its_connection_error(
        tmp_path, old_status, new_transport):
    state = DesktopState(tmp_path)
    state.effective_connection = ConnectionConfig(platform='native_pc')
    old = dict(sample(), event_status=old_status, event_error='previous battle coverage issue')
    state.accept('sample', old)
    retained = copy.deepcopy(state.last)
    state.accept('scanning', True)
    state.accept('observer', (new_transport, None))
    projected = state.snapshot()
    assert state.last == retained
    assert state.last['event_status'] == old_status
    assert projected['event_status'] is None
    assert projected['observer_status'] == new_transport
    assert 'previous battle coverage issue' not in projected['event_text']
    assert state.analysis_interrupted


@pytest.mark.parametrize('platform', ['native_pc', 'mumu'])
def test_recent_retained_sample_is_frozen_until_new_connection_publishes(tmp_path, monkeypatch, platform):
    import desktop_bridge
    state = DesktopState(tmp_path)
    state.effective_connection = ConnectionConfig(platform=platform)
    old = dict(sample(), session='retained-battle', event_status='recording', event_error=None)
    state.accept('sample', old)
    state.accept('lifecycle', 'playing')
    # A fresh old sample would otherwise project live, then paused or stale.
    monkeypatch.setattr(desktop_bridge, 'observation_age', lambda _: 0)
    monkeypatch.setattr(desktop_bridge.time, 'monotonic', lambda: state.last_progress)
    assert state.snapshot()['status'] == 'live'
    state.accept('scanning', True)
    state.accept('observer', ('ready', None))
    state.accept('scanning', False)
    projected = state.snapshot()
    assert projected['status'] == 'frozen' and '上次快照' in projected['status_text']
    assert projected['battle_id'] == old.get('battle_id', old['session'])
    assert state.last is old and old['event_status'] == 'recording'
    state.accept('sample', dict(old, frame=old['frame'] + 1))
    assert state.snapshot()['status'] == 'live'


def test_new_connection_without_a_retained_sample_waits_and_preserves_real_error(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('scanning', True)
    state.accept('observer', ('ready', None))
    state.accept('scanning', False)
    assert state.snapshot()['status'] == 'waiting'
    assert '可靠样本' in state.snapshot()['status_text']
    state.accept('error', 'actual connection failure')
    assert state.snapshot()['status'] == 'disconnected'
    assert state.snapshot()['error'] == 'actual connection failure'


def test_same_battle_gap_is_restored_from_actual_sample_after_transport_recovers(tmp_path):
    state = DesktopState(tmp_path)
    state.effective_connection = ConnectionConfig(platform='native_pc')
    old = dict(sample(), session='same-battle', event_status='gap', event_error='capture closed')
    state.accept('sample', old)
    state.accept('scanning', True)
    state.accept('observer', ('ready', None))
    assert state.snapshot()['event_status'] is None
    resumed = dict(old, frame=old['frame'] + 1)
    state.accept('sample', resumed)
    current = state.snapshot()
    assert current['observer_status'] == 'ready'
    assert current['event_status'] == 'gap'
    assert 'capture closed' in current['event_text']
