from types import SimpleNamespace

from collection_session import CollectionSession
from enemy_telemetry import EnemyTelemetry, enemy_totals


def row(eid, hp, *, uid=None, current=0, alive=False, lifecycle='departed', **phase):
    return dict(eid=eid, instance_uid=uid, max_hp=hp, hp_known=hp is not None,
                hp=current, alive=alive, lifecycle=lifecycle, **phase)


def test_total_pool_replaces_the_matched_planned_instances_first_stage():
    patrol = row('boss', 40000, uid='9', current=30000, alive=True,
                 phase_total_hp=100000, phase_from_birth=True, phase_coverage_complete=True)
    later = row('boss', 60000, lifecycle='pending')
    total = enemy_totals([patrol], [row('boss', 60000, uid='9'), later])

    assert total['planned_total_hp'] == 120000
    assert total['observed_total_hp'] == 100000
    assert total['entered_total_hp'] == 160000
    assert total['remaining_hp'] == 30000
    assert total['total_hp_complete'] is True


def test_phase_prefix_cannot_be_added_to_a_possibly_already_counted_stage():
    boss = row('boss', 60000, uid='9', phase_total_hp=80000, phase_from_birth=False,
               phase_coverage_complete=True)
    total = enemy_totals([boss], [row('boss', 60000, uid='9'), row('soldier', 5000)])

    assert total['observed_total_hp'] == 80000
    assert total['entered_total_hp'] == 85000
    assert total['total_hp_complete'] is False
    assert total['remaining_hp_complete'] is True


def test_ambiguous_plan_match_does_not_guess_by_enemy_type():
    boss = row('boss', 60000, uid='9', phase_total_hp=120000, phase_from_birth=True,
               phase_coverage_complete=True)
    plans = [row('boss', 60000, lifecycle='pending'), row('soldier', 5000, lifecycle='pending')]
    total = enemy_totals([boss], plans)

    assert total['entered_total_hp'] == 125000
    assert total['total_hp_complete'] is False


def test_known_planned_pool_does_not_make_event_only_current_hp_known():
    missed = row('soldier', 200, uid='8', alive=None, current=None, lifecycle='entered')
    total = enemy_totals([missed], [row('soldier', 200, lifecycle='pending')])

    assert total['entered_total_hp'] == 200
    assert total['total_hp_complete'] is True
    assert total['remaining_hp'] == 0
    assert total['remaining_hp_complete'] is False
    assert total['hp_complete'] is False


def test_phase_replaces_base_in_both_live_totals_and_the_history_point(monkeypatch):
    boss = SimpleNamespace(eid='boss', instance_uid='9', max_hp=60000, hp=20000,
                           alive=True, lifecycle='active')
    reader = SimpleNamespace(poll_fast=lambda: {'ok': True, 'enemies': [boss]})
    now = [50.0]
    telemetry = EnemyTelemetry(reader, bootstrap=lambda _: True, clock=lambda: now[0])
    session = CollectionSession(None, None, collector=SimpleNamespace(
        enemy_telemetry=telemetry, read_enemies=telemetry.sample))
    monkeypatch.setattr('collection_session.time.monotonic', lambda: now[0])
    first = {'session': 'battle', 'game_time': now[0]}
    session._attach_enemy(first)
    now[0] = 75.0
    phase = {'eid': 'boss', 'phase_total_hp': 120000, 'phase_count': 2, 'phase_from_birth': True}
    second = {'session': 'battle', 'game_time': now[0], 'statistics': {
        'rows': {'char': {'damage': 70000, 'effective_damage': 70000}},
        'enemy_phases': {'complete': True, 'instances': {'9': phase}},
    }}
    session._attach_enemy(second)
    result = second['enemy']

    assert result['totals']['entered_total_hp'] == 120000
    assert result['last_point']['entered_total_hp'] == 120000
    assert result['last_point']['remaining_hp'] == 20000
    assert result['last_point']['our_effective_damage'] == 70000
