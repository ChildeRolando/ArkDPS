"""Device-free synthetic sample shared by WPF boundary tests."""
import time


def sample():
    row = dict(name='赫默', damage=1230., healing=615., recent_damage=100.,
               recent_healing=50., present=True, shield_absorbed=None,
               engagement_damage=None, engagement_healing=None,
               active_damage=None, active_healing=None, counter_snapshot_complete=True,
               event_coverage_complete=False, damage_by_type={'physical': 1230.},
               coverage_start=20., recent_damage_window=(20., 22.3),
               recent_healing_window=(20., 22.3), sources=['char_a'])
    other = dict(row, name='夜刀', present=False, damage=500., healing=0.,
                 recent_damage=0., recent_healing=0., damage_by_type={'physical': 500.})
    return dict(battle_id='battle-a', session='a', frame=669, game_time=22.3,
                captured_monotonic=time.monotonic(), statistics_complete=True,
                event_status='failed', event_error='observer unavailable',
                # Deliberately contradictory compatibility data must never reach the GUI.
                rows=[dict(id='legacy', damage=999999)], sum_damage=999999,
                statistics=dict(game_time=22.3, rows={'a': row, 'b': other}))
