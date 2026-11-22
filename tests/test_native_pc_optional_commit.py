"""Optional read scheduling commits with the guarded pair, never a failed try."""
from copy import deepcopy

import pytest

import native_pc_collector as native
from collector import NativeClockBusy, SnapshotGap
from test_native_pc_battle_probe import runtime as memory_runtime, battle


def fields(_read, _context):
    return dict(actor_rows_status='available', actor_rows=[], roster_status='available',
                roster=dict(local_side=1, entries=[]), presence_status='unavailable', presence=None)


def collector(memory_runtime, monkeypatch):
    root, api, addresses = memory_runtime
    monkeypatch.setattr(native, '_capture_fields', fields)
    monkeypatch.setattr(native, 'capture_game_flow', lambda *_: dict(game_flow_status='unavailable'))
    monkeypatch.setattr(native, 'capture_stage_spawns', lambda *_: dict(stage_spawns_status='unavailable'))
    monkeypatch.setattr(native.time, 'monotonic', lambda: 10.)
    probe = battle.NativePcBattleProbe(root, api)
    value = native.NativePcCollector(root, probe_factory=lambda _: probe)
    value.connect()
    return value, probe, api, addresses


def test_failed_optional_outer_guard_rolls_back_then_gives_core_one_recovery_slot(memory_runtime, monkeypatch):
    value, probe, api, p = collector(memory_runtime, monkeypatch)
    calls = []

    def enemies(_read, context):
        calls.append(context['frame'])
        if len(calls) == 1:
            api.put(p['clock'] + battle.CLOCK_FRAME, '<I', context['frame'] + 1)
        return dict(enemies_status='available', enemies=dict(entities=[]))

    monkeypatch.setattr(native, 'capture_enemies', enemies)
    with pytest.raises(NativeClockBusy):
        value.read_lifecycle()
    assert value._next_enemy == float('-inf') and value._enemy_snapshot is None
    lifecycle = value.read_lifecycle()
    value.read_guarded_pair(lifecycle)
    assert calls == [30] and value._enemy_snapshot is None
    assert value._next_enemy == float('-inf')
    lifecycle = value.read_lifecycle()
    value.read_guarded_pair(lifecycle)
    assert calls == [30, 31] and value._next_enemy == 11.
    assert value._enemy_snapshot[1] == 31
    value.close()


@pytest.mark.parametrize('discard', ['unused', 'wrong_lifecycle', 'missing_roster'])
def test_uncommitted_pair_cannot_consume_optional_due_time(memory_runtime, monkeypatch, discard):
    value, probe, api, p = collector(memory_runtime, monkeypatch)
    calls = []
    monkeypatch.setattr(native, 'capture_enemies', lambda *_: calls.append(True) or
                        dict(enemies_status='available', enemies=dict(entities=[])))
    if discard == 'missing_roster':
        def incomplete(*args):
            return dict(fields(*args), roster_status='unavailable', roster=None)
        monkeypatch.setattr(native, '_capture_fields', incomplete)
    lifecycle = value.read_lifecycle()
    if discard != 'unused':
        with pytest.raises(SnapshotGap):
            value.read_guarded_pair(deepcopy(lifecycle) if discard == 'wrong_lifecycle' else lifecycle)
        assert value._next_enemy == float('-inf')
    monkeypatch.setattr(native, '_capture_fields', fields)
    value.read_guarded_pair(value.read_lifecycle())
    assert calls == [True] and value._enemy_snapshot is None
    value.read_guarded_pair(value.read_lifecycle())
    assert calls == [True, True] and value._enemy_snapshot is not None
    value.close()


@pytest.mark.parametrize('change', ['clock_reset', 'roots'])
def test_recovery_slot_is_not_inherited_by_new_native_identity(memory_runtime, monkeypatch, change):
    value, probe, api, p = collector(memory_runtime, monkeypatch)
    calls = []

    def enemies(_read, context):
        calls.append(context['frame'])
        if len(calls) == 1:
            api.put(p['clock'] + battle.CLOCK_FRAME, '<I', context['frame'] + 1)
        return dict(enemies_status='available', enemies=dict(entities=[]))

    monkeypatch.setattr(native, 'capture_enemies', enemies)
    with pytest.raises(NativeClockBusy):
        value.read_lifecycle()
    if change == 'clock_reset':
        api.put(p['clock'] + battle.CLOCK_FRAME, '<Iq', 0, 0)
    else:
        replacement = api.allocate()
        api.put(replacement, '<Q', p['stats_class'])
        api.put(replacement + battle.STATS_TOTAL_HEAL, '<ff', 0., 0.)
        api.put(p['logger'] + battle.LOGGER_STATS, '<Q', replacement)
    value.read_guarded_pair(value.read_lifecycle())
    assert len(calls) == 2 and value._enemy_snapshot is not None
    value.close()
