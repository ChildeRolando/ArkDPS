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
