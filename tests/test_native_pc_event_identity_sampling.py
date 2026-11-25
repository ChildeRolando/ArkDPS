"""The PC event identity path does not reread discarded legacy witnesses."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import native_pc_collector as native
from battle_lifecycle import BattleRuntime, NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED
from desktop_bridge import DesktopState, _display_statistics, _native_display_source
from event_capture import CaptureRetry
from panel_model import PanelConfig
from test_native_pc_autochess_collector_gate import fixture, SCOPE, CID, ROOTS
from test_multiplayer_native_context import hit, member, stamp
from test_multiplayer_native_skill_baseline import activation


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

@pytest.mark.parametrize('phase,players,expected', [
    (4, 4, True), (5, 2, True), (4, 1, True), (5, 1, True), (6, 1, True), (6, 4, True),
    (4, 0, False), (4, 5, False), (2, 4, False), (3, 4, False), (7, 4, False), (8, 4, False),
])
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


@pytest.mark.parametrize('phase,mode,player_indices', [
    (4, 'local', {3}), (6, 'local', {3}), (5, 'all', {3}), (6, 'all', {2, 3}), (4, 'local', {2, 3}),
])
def test_scoped_presence_plan_produces_inspector_values_and_keeps_gaps(
        tmp_path, monkeypatch, phase, mode, player_indices):
    calls = readers(monkeypatch)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(native, 'time', SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: 1_700_000_000 + clock.now))
    collector, probe, observer, _, events = fixture(tmp_path)
    observer.native_autochess_identity_sampling = True
    probe.phase = phase
    raw_mark, raw_batch, raw_sample = observer.mark, observer.read_through, probe.sample

    def identity(packet):
        context = packet['native_autochess_context']
        context.update(collection_mode=mode, match_key='0x60000:0x70000')
        context['players'] = [player for player in context['players'] if player['index'] in player_indices]
        context['memberships'] = [entry for entry in context['memberships'] if entry['player_index'] in player_indices]
        context['memberships'].append(member(3, uid=311, cid='token_test', is_token=True,
                                             host_key=CID, host_uid=301))
        return packet

    def mark():
        return identity(raw_mark()) | dict(native_stage_id=probe.stage, native_frame=probe.frame,
            native_fixed_time=str(int(probe.when * (1 << 32))), native_process_pid=probe.pid,
            native_generation=1, damage_settlement_scope=1, active_hooks=[])

    observer.mark = mark
    observer.read_through = lambda through: identity(raw_batch(through))
    observer.read_baseline = lambda through: dict(observer.read_through(through), events=[])

    def sampled(*, extra_reader):
        sampled = raw_sample(extra_reader=extra_reader)
        raw = sampled['sample']
        stamp = dict(native_identity=ROOTS, frame=probe.frame,
                     native_fixed_time=str(int(probe.when * (1 << 32))), stage_id=probe.stage)
        raw['native_window_guard'] = dict(version=1, source='native_pc_guarded_read',
            process_instance_before=list(probe.process_instance), process_instance_after=list(probe.process_instance),
            before=deepcopy(stamp), after=deepcopy(stamp), source_build=probe.build['source_build'],
            battle_serial=raw['battle_serial'])
        context = dict(stage_id=probe.stage, stage_status='available', frame=probe.frame,
                       game_time=probe.when, battle_state=probe.state, battle_native=0x20000,
                       battle_controller=0x10000, battle_logger=0x30000, battle_stats=0x40000, level_data=0x50000)
        raw.update(extra_reader(None, context))
        if point.reject_frame and 'presence_status' in raw:
            return dict(status='sample_retry', reason='frame_or_lifecycle_changed')
        if probe.frame == 0:
            raw['total_damage'] = 0
            raw['actor_rows'] = [dict(id=CID, damage=0, healing=0)]
        return sampled

    probe.sample = sampled
    point = SimpleNamespace(instance=701, available=True, reject_frame=False)

    def capture_presence(*_):
        calls.append('presence')
        if not point.available:
            return dict(presence_status='unavailable', presence=None)
        units = [
            dict(id=cid, data_uid=uid, instance_uid=instance, player_side=side, inited=True,
                 borned=True, is_disappeared=False, is_token=is_token)
            for cid, uid, instance, side, is_token in (
                (CID, 301, point.instance, 3, False),
                (CID, 201, 801, 2, False),
                ('token_test', 311, 901, 3, True),
            )
        ]
        return dict(presence_status='available',
                    presence=dict(scope='unit_manager_characters_membership', local_side=3, entities=units))

    monkeypatch.setattr(native, 'capture_presence', capture_presence)
    runtime = BattleRuntime()
    state = DesktopState(tmp_path / 'desktop')

    def step(host, when):
        clock.now = host
        probe.when, probe.frame = when, round(when * 30)
        observation = runtime.poll(collector, observer)
        assert observation.kind == 'sample'
        return observation.sample

    def detail_row():
        sample = runtime.display_sample()
        owner = _native_display_source(sample)
        view = state._actor_view(PanelConfig(effective_only=True), _display_statistics(sample, source=owner),
                                 owner['actor_ledgers'])
        return next(row for row in view['source'].values() if row['actor_uid'] == 301)

    def effect_scope(when):
        return stamp(phase=phase, root=SCOPE, when=when, collection_mode=mode)

    assert 'scoped_presence' not in step(0, 0)
    events.append(activation(1, .5, event_scope=effect_scope(.5)))
    first = step(.25, .5)['scoped_presence']
    expected_uids = {301}
    if mode == 'all':
        expected_uids.add(201)
    assert {unit['data_uid'] for unit in first['units']} == expected_uids
    events.extend((dict(activation(2, 1, event_scope=effect_scope(1)), kind='skill_end',
                        proof={'method': 'Character.OnSkillFinish'}),
                   hit(3, 3, 1, 100, event_scope=effect_scope(1))))
    assert 'scoped_presence' not in step(.50, 1)
    assert calls.count('presence') == calls.count('enemy') == 1
    assert 'scoped_presence' not in step(1.25, 1.5)  # The due enemy read retains priority.
    assert 'scoped_presence' in step(1.25, 1.5)
    assert calls.count('presence') == calls.count('enemy') == 2
    row = detail_row()
    assert row['observed_presence_seconds'] == 1
    assert row['observed_skill_active_seconds'] == .5
    assert row['skill_timed_presence_seconds'] == 1
    assert row['observed_skill_coverage'] == .5
    assert row.get('presence_complete') is not True and row.get('presence_seconds') is None
    payload = runtime.display_sample()['multiplayer']
    timed_peers = {peer['unique_id'] for player in payload['players']
                   for peer in (player.get('statistics') or {}).get('rows', {}).values()
                   if peer['unique_id'] != 301 and 'observed_presence_seconds' in peer}
    assert timed_peers == ({201} if mode == 'all' and 2 in player_indices else set())

    point.instance = 702
    step(2.25, 2.5)
    step(2.25, 2.5)
    assert detail_row()['observed_presence_seconds'] == 1
    point.available = False
    step(3.25, 3.5)
    assert step(3.25, 3.5)['scoped_presence']['units'] is None
    assert detail_row()['observed_presence_seconds'] == 1
    point.available = True
    step(4.25, 4.5)
    step(4.25, 4.5)
    assert detail_row()['observed_presence_seconds'] == 1
    step(5.25, 5.5)
    step(5.25, 5.5)
    assert detail_row()['observed_presence_seconds'] == 2
    assert calls.count('presence') == calls.count('enemy') == 6

    # The real optional reader ran, but the probe rejected its enclosing
    # frame. A regular retry must not join the next point to the old one.
    assert 'scoped_presence' not in step(6.25, 6.5)
    point.reject_frame = True
    previous_ack = runtime.sessions.adapter.event_stream.sequence
    with pytest.raises(CaptureRetry, match='frame_or_lifecycle_changed'):
        step(6.25, 6.5)
    assert runtime.sessions.adapter.event_stream.sequence == previous_ack
    point.reject_frame = False
    assert 'scoped_presence' not in step(6.25, 6.5)
    step(7.25, 7.5)
    recovered = step(7.25, 7.5)['scoped_presence']
    assert detail_row()['observed_presence_seconds'] == 2
    assert recovered['sample_seq'] == 8
    step(8.25, 8.5)
    step(8.25, 8.5)
    assert detail_row()['observed_presence_seconds'] == 3
    assert calls.count('presence') == calls.count('enemy') == 9
    assert 'roster' not in calls
