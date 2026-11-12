from types import SimpleNamespace

from collector import EnemyTelemetry
from event_stream import EventStream
from stats_core import Statistics


def observed_entry(**attributes):
    stats = Statistics('battle', 0, event_coverage=True)
    stats.snapshot(0, {})
    stats.observe_enemy_entry(0, complete=True)
    stream = EventStream(stats, session='battle', baseline_frame=0, baseline_sequence=0)
    stream.accept(session='battle', sequence=1, frame=0, game_time=0, source=None,
                  kind='enemy_enter', amount=0, target='enemy_short',
                  target_instance='7', enemy_entry_attributes=attributes)
    return stats.result()['enemy_entry']


def test_short_lived_enemy_uses_its_entry_attributes_and_later_snapshot_wins():
    observed = observed_entry(max_hp=200, atk=12, **{'def': 0}, res=-5,
                              move_speed=1.5, attack_speed=100)
    snapshots = iter([{'ok': True, 'enemies': []}, {'ok': True, 'enemies': [
        SimpleNamespace(roster_id=1, instance_uid='7', eid='enemy_short', hp=50,
                        max_hp=250, atk=24, def_=8, res=0, mspd=2, aspd=120, alive=True),
    ]}])
    reader = SimpleNamespace(poll_fast=lambda: next(snapshots))
    telemetry = EnemyTelemetry(reader, bootstrap=lambda _: True, poll_interval=0)
    early = telemetry.sample(game_time=1, our_damage=0, enemy_entry=observed)
    row, = early['rows']
    assert row['attributes_from_entry'] and row['hp_known']
    assert (row['max_hp'], row['atk'], row['def'], row['res']) == (200, 12, 0, -5)
    assert (row['move_speed'], row['attack_speed']) == (1.5, 100)
    assert row['hp'] is None and row['hp_ratio'] is None and row['alive'] is None

    later = telemetry.sample(game_time=2, our_damage=150)
    row, = later['rows']
    assert not row['attributes_from_entry']
    assert (row['hp'], row['max_hp'], row['atk'], row['def'], row['res']) == (50, 250, 24, 8, 0)
    assert row['hp_ratio'] == .2 and row['alive']


def test_optional_unreliable_attribute_values_do_not_discard_enemy_identity():
    observed = observed_entry(max_hp=0, atk=True, res=float('nan'), move_speed=1.25,
                              unrelated='ignored')
    assert observed['complete']
    assert observed['events'] == [{
        'instance_uid': '7', 'eid': 'enemy_short', 'frame': 0, 'game_time': 0,
        'enemy_entry_attributes': {'move_speed': 1.25},
    }]
