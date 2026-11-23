"""The PC event identity path does not reread discarded legacy witnesses."""
import pytest

import native_pc_collector as native
from battle_lifecycle import BattleRuntime, NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED
from test_native_pc_autochess_collector_gate import fixture, SCOPE, CID
from test_multiplayer_native_context import hit


AUTO = 'Activities/ACT3AUTOCHESS/level_test'


def readers(monkeypatch):
    calls = []
    monkeypatch.setattr(native, 'capture_character_rows', lambda *_:
        calls.append('actor_rows') or dict(actor_rows_status='available',
            actor_rows=[dict(id=CID, damage=90000., healing=0.)]))
    monkeypatch.setattr(native, 'capture_roster', lambda *_:
        calls.append('roster') or dict(roster_status='available', roster=dict(local_side=3, entries=[])))
    monkeypatch.setattr(native, 'capture_presence', lambda *_:
        calls.append('presence') or dict(presence_status='unavailable', presence=None))
    monkeypatch.setattr(native, 'capture_game_flow',
        lambda *_: calls.append('flow') or dict(game_flow_status='unavailable'))
    monkeypatch.setattr(native, 'capture_enemies',
        lambda *_: calls.append('enemy') or dict(enemies_status='unavailable'))
    monkeypatch.setattr(native, 'capture_stage_spawns',
        lambda *_: calls.append('plan') or dict(stage_spawns_status='unavailable'))
    monkeypatch.setattr(native, 'capture_actions',
        lambda *_: calls.append('actions') or dict(actions_status='available', actions={}))
    return calls


@pytest.mark.parametrize('event_identity,stage,status,skip', [
    (True, AUTO, 'available', True), (False, AUTO, 'available', False),
    (True, AUTO, 'unavailable', False), (True, None, 'unavailable', False),
    (True, 'Obt/Main/level_main_01-07', 'available', False),
])
def test_only_explicit_event_mode_and_guarded_autochess_skip_legacy_reads(
        monkeypatch, event_identity, stage, status, skip):
    calls = readers(monkeypatch)
    collector = native.NativePcCollector(None, event_identity_only=event_identity)
    collector._next_enemy = collector._next_stage_plan = float('inf')
    value = collector._capture(None, dict(stage_id=stage, stage_status=status))
    assert 'actor_rows' in calls  # Still needed by the existing initial-skill check.
    assert ('roster' in calls) is not skip
    assert ('presence' in calls) is not skip
    assert ('actions' in calls) is not skip
    assert 'flow' in calls
    if skip:
        assert value['actor_rows_status'] == 'available'
        for name in ('roster', 'presence'):
            assert value[name] is None
            assert value[name + '_status'] == 'not_requested'
            assert value[name + '_reason'] == 'native_event_identity'
    else:
        assert value['actor_rows_status'] == value['roster_status'] == 'available'


def test_event_identity_still_collects_enemy_flow_and_stage_plan(monkeypatch):
    calls = readers(monkeypatch)
    collector = native.NativePcCollector(None, event_identity_only=True)
    context = dict(stage_id=AUTO, stage_status='available')
    collector._capture(None, context)
    assert calls == ['actor_rows', 'enemy', 'flow']
    collector._capture(None, context)
    assert calls == ['actor_rows', 'enemy', 'flow', 'actor_rows', 'flow', 'plan']


@pytest.mark.parametrize('mode', ['valid', 'unknown', 'foreign', 'no_observer'])
def test_omitted_witness_candidate_still_needs_independent_event_identity(tmp_path, monkeypatch, mode):
    calls = readers(monkeypatch)
    collector, probe, observer, commits, events = fixture(tmp_path, mode=mode)
    collector.event_identity_only = True
    original = probe.sample

    def sample(*, extra_reader=None, **_):
        report = original(extra_reader=extra_reader)
        if extra_reader:
            raw = report['sample']
            raw['total_damage'] = 0.  # A merged per-character counter may still be nonzero.
            raw.update(extra_reader(None, dict(frame=raw['frame'], game_time=raw['game_time'],
                stage_id=raw['stage_id'], stage_status=raw['stage_status'])))
        return report

    probe.sample = sample
    runtime = BattleRuntime()
    collector._next_enemy = collector._next_stage_plan = float('inf')
    if mode != 'valid':
        observation = runtime.poll(collector, None if mode == 'no_observer' else observer)
        assert observation.kind == 'scope_unavailable'
        assert observation.reason == NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED
        assert commits == [] and runtime.display_sample() is None
    else:
        assert runtime.poll(collector, observer).kind == 'sample'
        probe.frame, probe.when = 30, 1
        for index in range(1, 5):
            scope = dict(protocol_version=1, status='available', verified_same_frame=True,
                scope_key=SCOPE, phase=4, round=4, frame=30, game_time=1)
            events.append(hit(index, index, 1, index * 100, event_scope=scope))
        runtime.poll(collector, observer)
        value = runtime.display_sample()
        assert [p['observed_damage'] for p in value['multiplayer']['players']] == [100, 200, 300, 400]
        assert value['statistics']['rows'] == {} and not value['statistics']['totals_complete']
        assert value['native_rows'][0]['damage'] == 90000.
        assert value['multiplayer']['players'][2]['actor_ledgers'][0]['ledger']['skill_split_complete'] is False
        assert value['native_snapshot_witnesses']['actor_rows']['status'] == 'available'
        assert value['native_snapshot_witnesses']['roster']['status'] == 'not_requested'
        assert commits == [0, 4]
    assert 'roster' not in calls and 'presence' not in calls and 'actions' not in calls
