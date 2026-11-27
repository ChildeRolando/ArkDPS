"""Product snapshot policy: no exact recovery claim, fresh HP and strict settlement."""
from copy import deepcopy
from pathlib import Path
import subprocess
from test_enemy_lifecycle_telemetry import Battle, enemy, event
from enemy_telemetry import EnemyTelemetry
from test_native_enemy_events import snapshot, state, no_bootstrap
from stats_core import Statistics


def light_sample(battle, frame, *events):
    battle.events.extend(events)
    return battle.telemetry.sample(game_time=frame/30, frame=frame, our_damage=0,
        enemy_state=dict(start_frame=10, complete=True, hp_mode='snapshots', events=battle.events))


def test_snapshot_policy_polls_after_seed_and_keeps_lifecycle_and_capacity():
    battle = Battle((10, [enemy('10', hp=100)]), (20, [enemy('10', hp=45)]),
                    (30, [enemy('10', hp=72, max_hp=130)]))
    first = light_sample(battle, 10, event(1, 10, 'enemy_enter', hp_after=100))
    assert first['rows'][0]['alive'] is True
    first_frozen = deepcopy(first)
    battle.now = 2
    hurt = light_sample(battle, 20)
    assert battle.polls == 2
    assert hurt['rows'][0]['hp'] == hurt['last_point']['remaining_hp'] == 45
    battle.now = 4
    healed = light_sample(battle, 30)
    assert healed['rows'][0]['hp'] == healed['last_point']['remaining_hp'] == 72
    assert healed['rows'][0]['max_hp'] == 130
    for result in (first, hurt, healed):
        for point in [result['totals'], result['last_point'], *result['series']]:
            assert point['recovered_hp'] is None
            assert point['damage_capacity_with_recovery'] is None
            assert point['recovery_available'] is False
    # The saved object and previously published points remain immutable.
    assert first_frozen == first


def test_pc_snapshot_refreshes_hp_without_overwriting_verified_life():
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    events = [dict(event(1, 10, 'enemy_enter', uid='9', hp_after=100), instance_uid='9', eid='enemy_a')]
    def project(frame, hp):
        return telemetry.sample_native_snapshot(snapshot(frame, hp), frame=frame, game_time=frame/30,
            enemy_state=dict(state(events, start=10), hp_mode='snapshots'))
    first = project(10, 100)
    assert first is not None
    second = project(20, 35)
    assert second['rows'][0]['hp'] == 35
    assert second['rows'][0]['alive'] is True
    assert second['totals']['recovered_hp'] is None
    assert second['last_point']['remaining_hp'] == 35
    assert second['last_point']['damage_capacity_with_recovery'] is None


def test_shared_lightweight_hp_contract_and_settlement_refusals():
    for fixture in ('event_probe_light_hp_fixture.js', 'event_probe_hp_settlement_fixture.js'):
        run = subprocess.run(['node', str(Path(__file__).with_name(fixture))],
                             capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr


def test_quality_mode_survives_statistics_export_without_changing_our_healing():
    from event_capture import apply_event_quality
    statistics = Statistics('battle', 0)
    statistics.register_source('healer', 'healer')
    statistics.snapshot(0, {'healer': {'damage': 0, 'healing': 10}})
    quality = dict(enemy_hp_mode='snapshots', enemy_state_ready=True, enemy_state_unknown=False,
                   damage_unknown=False, engagement_unknown=False, shield_unknown=False,
                   skill_unknown=False, mitigation_ready=True, mitigation_unknown=False)
    apply_event_quality(statistics, quality, {}, baseline_frame=0, initial_drain=True)
    statistics.snapshot(1, {'healer': {'damage': 0, 'healing': 210}})
    result = statistics.result()
    assert result['enemy_state']['hp_mode'] == 'snapshots'
    assert result['enemy_state']['complete'] is True
    assert result['rows']['healer']['healing'] == 210
    statistics.finish(2)
    archived = statistics.result()
    assert archived['enemy_state']['hp_mode'] == 'snapshots'
    assert archived['rows']['healer']['healing'] == 210


def test_snapshot_without_event_observer_does_not_invent_zero_recovery():
    battle = Battle((10, [enemy('10', hp=70)]))
    result = battle.telemetry.sample(game_time=10/30, frame=10, our_damage=0)
    assert result['rows'][0]['hp'] == 70
    assert result['totals']['recovered_hp'] is None
    assert result['totals']['damage_capacity_with_recovery'] is None
    assert result['last_point']['recovery_available'] is False
