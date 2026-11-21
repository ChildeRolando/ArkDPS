from types import SimpleNamespace

from acceptance import record_native_pc_battle as recorder


def sample(battle_serial, frame, damage, healing=0.0, actor_rows=None):
    return {'status': 'sample_available', 'sample': {
        'battle_serial': battle_serial, 'frame': frame, 'game_time': frame / 30,
        'battle_state': 2, 'total_damage': damage, 'total_heal': healing, 'actor_rows': actor_rows,
    }}


def run(monkeypatch, *reports):
    now = 0.0
    events = []
    reports = iter(reports)

    def advance(duration):
        nonlocal now
        now += duration

    def poll():
        result = next(reports)
        if result is None:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(recorder, 'time', SimpleNamespace(monotonic=lambda: now, sleep=advance))
    summary = recorder.record_samples(poll, 10, events.append)
    return summary, events


def test_distinct_same_battle_samples_prove_clock_and_counter_changes(monkeypatch):
    first = sample(1, 30, 0.0, actor_rows=[{'id': 'char_001', 'damage': 0.0}])
    summary, events = run(monkeypatch, {'status': 'waiting_for_battle'}, first, first,
                          sample(1, 60, 120.5, 80.0, [{'id': 'char_001', 'damage': 160.0}]), None)
    assert [event['status'] for event in events] == [
        'waiting_for_battle', 'sample_available', 'sample_available']
    assert summary['stable_samples'] == 2
    assert summary['status_counts'] == {'waiting_for_battle': 1, 'sample_available': 3}
    assert summary['frame_progress_observed'] and summary['damage_change_observed']
    assert summary['healing_change_observed'] and summary['interrupted']
    assert summary['actor_rows_observed'] and summary['actor_damage_change_observed']


def test_gaps_new_battles_and_time_regressions_do_not_prove_progress(monkeypatch):
    summary, _ = run(monkeypatch, sample(1, 300, 10.0), {'status': 'sample_retry'},
                     sample(1, 400, 12.0), sample(1, 50, 3.0),
                     sample(2, 70, 15.0), None)
    assert summary['stable_samples'] == 4
    assert not summary['frame_progress_observed']
    assert not summary['damage_change_observed']
    assert not summary['healing_change_observed']


def test_heartbeat_records_a_frozen_clock_without_treating_it_as_movement(monkeypatch):
    paused = sample(1, 30, 120.5)
    summary, events = run(monkeypatch, *([paused] * 8), None)
    assert [event['kind'] for event in events] == ['observation', 'heartbeat']
    assert events[0]['elapsed_seconds'] == 0
    assert events[1]['elapsed_seconds'] >= 1
    assert events[1]['frame'] == 30 and events[1]['game_time'] == 1
    assert summary['stable_samples'] == 1 and not summary['frame_progress_observed']


def test_enemy_hp_change_requires_the_same_observed_instance_and_battle(monkeypatch):
    def enemy(battle, frame, uid, hp, *, state='available'):
        capture = sample(battle, frame, 10.0)
        capture['sample'].update(enemies_status=state, enemies={'entities': [dict(
            instance_uid=uid, hp=hp, inited=True, borned=True, is_disappeared=False,
            native_object_present=True)]})
        return capture

    reports = (enemy(1, 30, 10, 500), enemy(1, 31, 11, 400), enemy(2, 30, 11, 350))
    summary, _ = run(monkeypatch, *reports, enemy(2, 31, 11, 200, state='unavailable'), None)
    assert summary['enemy_hp_observed'] and not summary['enemy_hp_change_observed']

    summary, _ = run(monkeypatch, *reports, enemy(2, 30, 11, 200), None)
    assert not summary['enemy_hp_change_observed']

    summary, _ = run(monkeypatch, *reports, enemy(2, 31, 11, 200), None)
    assert summary['enemy_hp_change_observed']


def test_stage_references_need_distinct_frames_in_one_battle_and_stable_values(monkeypatch):
    def refs(battle, frame, level=0, overwritten=False):
        evidence = sample(battle, frame, 0.0)
        evidence['sample'].update(stage_id='Obt/Main/level_main_01-07',
            stage_enemy_refs_status='available', stage_enemy_refs={
                'scope': 'current_stage_enemy_db_refs', 'collection_count': 1,
                'entries': [dict(enemy_key='enemy_1007_slime_2', variant_level=level,
                                 use_db=True, has_overwrite=overwritten)],
            })
        return evidence

    summary, _ = run(monkeypatch, refs(1, 30), refs(1, 60), None)
    assert summary['stage_enemy_refs_samples'] == 2 and summary['stage_enemy_refs_stable']
    assert not summary['damage_change_observed']

    for other in (refs(1, 30, 1), refs(2, 60), refs(1, 60, 1), refs(1, 60, overwritten=True)):
        summary, _ = run(monkeypatch, refs(1, 30), other, None)
        assert not summary['stage_enemy_refs_stable']


def test_game_flow_witness_only_flags_unresolved_finish_for_the_sampled_stage(monkeypatch):
    def flow(**changes):
        capture = sample(1, 60, 120.0)
        capture['sample'].update(stage_id='Obt/Main/level_main_01-07', battle_result=0,
            game_flow_status='available', game_flow=dict(transiting=True,
                source_scene='level_main_01-07', target_scene='battle_finish'))
        capture['sample'].update(changes)
        return capture

    summary, _ = run(monkeypatch, flow(), None)
    assert summary['game_flow_samples'] == 1 and summary['unresolved_finish_transition_observed']
    for other in (flow(battle_state=3, battle_result=2),
                  flow(stage_id='Obt/Main/level_main_01-08'),
                  flow(game_flow_status='unavailable', game_flow=None)):
        summary, _ = run(monkeypatch, other, None)
        assert not summary['unresolved_finish_transition_observed']


def test_lobby_flow_changes_are_recorded_without_a_battle_or_duplicate_snapshot(monkeypatch):
    def idle(transiting, target):
        return dict(status='waiting_for_battle', game_flow_status='available',
                    game_flow=dict(transiting=transiting, source_scene='home', target_scene=target))

    before, transition = idle(False, 'home'), idle(True, 'battle_loader')
    summary, events = run(monkeypatch, before, before, transition, transition, None)
    assert len(events) == summary['idle_game_flow_observations'] == 2
    assert summary['game_flow_samples'] == 0 and not summary['unresolved_finish_transition_observed']


def test_practice_selection_records_both_raw_values_without_assigning_a_battle_mode(monkeypatch):
    def selection(practice):
        return dict(status='waiting_for_battle', practice_selection_status='available',
                    practice_selection=dict(stage_id='main_01-07', practice=practice))

    first, second = selection(False), selection(True)
    summary, events = run(monkeypatch, first, first, second, second, sample(1, 3, 0), None)
    assert summary['practice_selection_observations'] == 2
    assert summary['practice_selection_values'] == [False, True]
    assert 'practice' not in events[-1]['sample']


def test_lifetime_summary_counts_instances_within_each_battle(monkeypatch):
    before = sample(1, 6, 0.)
    live = sample(1, 15, 0.)
    replacement = sample(2, 3, 0.)
    before['sample'].update(presence_lifetime_status='available', presence_lifetime={'witnesses': []})
    for report in (live, replacement):
        report['sample'].update(presence_lifetime_status='available', presence_lifetime={
            'witnesses': [{'instance_uid': 68}],
        })
    summary, _ = run(monkeypatch, before, live, live, replacement, None)
    assert summary['presence_lifetime_samples'] == 2
    assert summary['presence_lifetime_instances'] == 2
