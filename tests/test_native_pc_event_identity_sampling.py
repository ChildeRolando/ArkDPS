"""The PC event identity path does not reread discarded legacy witnesses."""
import pytest

import native_pc_collector as native
from battle_lifecycle import BattleRuntime, NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED
from test_native_pc_autochess_collector_gate import fixture, SCOPE, CID, ROOTS
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


@pytest.mark.parametrize('scenario', ['match', 'missing_capability', 'unverified', 'clock_changed',
                                     'next_tick', 'multi_tick', 'new_native', 'terminal_tick', 'root_changed', 'comparison'])
def test_guarded_pc_reads_use_authorized_identity_only_for_a_matching_mark(tmp_path, monkeypatch, scenario):
    calls = readers(monkeypatch)
    mode = 'unknown' if scenario == 'unverified' else 'valid'
    collector, probe, observer, _, _ = fixture(tmp_path, mode=mode, stage=AUTO, failed_field=None)
    collector.allow_event_identity_sampling = scenario != 'comparison'
    observer.native_autochess_identity_sampling = scenario != 'missing_capability'
    mark = observer.mark
    observer.mark = lambda: dict(mark(), native_stage_id=AUTO)
    original_sample = probe.sample

    def sample(*, extra_reader):
        report = original_sample(extra_reader=extra_reader)
        raw = report['sample']
        context = dict(stage_id=AUTO, stage_status='available', frame=raw['frame'], game_time=raw['game_time'],
                       battle_controller=int(ROOTS['controller'], 16), battle_logger=int(ROOTS['logger'], 16),
                       battle_stats=int(ROOTS['stats'], 16), level_data=int(ROOTS['level'], 16))
        if scenario == 'clock_changed':
            context['frame'] += 1
        if scenario in ('next_tick', 'multi_tick', 'new_native', 'terminal_tick'):
            elapsed = 3 if scenario == 'multi_tick' else 1
            context.update(frame=raw['frame']+elapsed, game_time=raw['game_time']+elapsed/30,
                           battle_state=3 if scenario == 'terminal_tick' else 2,
                           battle_native=int(ROOTS['native'], 16) + (8 if scenario == 'new_native' else 0))
        if scenario == 'root_changed':
            context['battle_stats'] += 8
        raw.update(extra_reader(None, context))
        return report

    probe.sample = sample
    BattleRuntime().feed.acquire(collector, observer)
    assert ('roster' not in calls) is (scenario in ('match', 'next_tick', 'multi_tick'))
    assert ('presence' not in calls) is (scenario in ('match', 'next_tick', 'multi_tick'))
    assert ('actor_rows' not in calls) is (scenario in ('next_tick', 'multi_tick'))
    assert 'flow' in calls and 'enemy' in calls


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

@pytest.mark.parametrize('phase,players,expected', [(4, 4, True), (5, 2, True), (4, 1, False), (3, 4, False)])
def test_scoped_presence_has_its_own_budget_and_never_starves_enemy(monkeypatch, phase, players, expected):
    calls = readers(monkeypatch)
    collector = native.NativePcCollector(None)
    mark = dict(frame=30, game_time=1., native_stage_id=AUTO, native_identity=ROOTS,
                native_autochess_context=dict(phase=phase, status='available', players=[{}]*players))
    scene = dict(frame=30, game_time=1., battle_state=2, stage_id=AUTO, stage_status='available',
                 battle_controller=int(ROOTS['controller'],16), battle_logger=int(ROOTS['logger'],16),
                 battle_stats=int(ROOTS['stats'],16), level_data=int(ROOTS['level'],16))
    for _ in range(3):
        collector._capture(None, scene, event_identity_mark=mark)
    assert calls.count('presence') == int(expected)
    assert calls.count('enemy') == 1
    assert 'roster' not in calls

def test_local_mode_scoped_presence_excludes_other_players_before_publication(monkeypatch):
    readers(monkeypatch)
    units = [dict(data_uid=i, id=CID, player_side=i) for i in (2, 3)]
    monkeypatch.setattr(native, 'capture_presence', lambda *_: dict(presence_status='available',
        presence=dict(entities=units)))
    collector = native.NativePcCollector(None)
    collector._next_enemy = float('inf')
    mark = dict(frame=30, game_time=1., native_stage_id=AUTO, native_identity=ROOTS,
        native_autochess_context=dict(phase=5, status='available', players=[{}, {}],
            collection_mode='local', self_player_index=3,
            memberships=[dict(data_uid=i, cid=CID, player_side=i, player_index=i) for i in (2, 3)]))
    scene = dict(frame=30, game_time=1., battle_state=2, stage_id=AUTO, stage_status='available',
        battle_controller=int(ROOTS['controller'],16), battle_logger=int(ROOTS['logger'],16),
        battle_stats=int(ROOTS['stats'],16), level_data=int(ROOTS['level'],16))
    assert collector._capture(None, scene, event_identity_mark=mark)['presence']['entities'] == [units[1]]
