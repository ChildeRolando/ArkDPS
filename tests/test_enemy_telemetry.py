from types import SimpleNamespace

from enemy_telemetry import (EnemyTelemetry, EnemyTimeline, enemy_rows,
                             enemy_totals)


def enemy(**values):
    base = dict(roster_id=1, spawn_order=1, eid='enemy_1', name='士兵', code='B1',
                hp=100.0, max_hp=200.0, atk=10.0, def_=2.0, res=3.0,
                mspd=0.5, aspd=100.0, alive=True, lifecycle='active',
                state_id=1, spawn_frame=10, end_frame=None, end_reason=None,
                is_summon=False, pos_x=1.5, pos_y=2.5)
    base.update(values)
    return SimpleNamespace(**base)


def test_entered_scope_excludes_pending_and_keeps_departed():
    rows = enemy_rows({'enemies': [
        enemy(roster_id=2, spawn_order=2, lifecycle='pending', alive=False),
        enemy(roster_id=1, spawn_order=1),
        enemy(roster_id=3, spawn_order=3, lifecycle='departed', alive=False,
              hp=0.0, end_reason='death'),
    ]})
    assert [row['roster_id'] for row in rows] == [1, 3]
    assert rows[0]['hp_known'] is True and rows[0]['hp_ratio'] == 0.5
    assert rows[1]['state'] == '移动' and rows[1]['lifecycle'] == 'departed'


def test_missing_hp_is_never_rendered_as_zero_percent():
    rows = enemy_rows({'enemies': [enemy(hp=0.0, max_hp=0.0)]})
    assert rows[0]['hp_known'] is False
    assert rows[0]['hp_ratio'] is None
    totals = enemy_totals(rows)
    assert totals['entered_total_hp'] == 0
    assert totals['hp_complete'] is False


def test_totals_only_sum_entered_enemies_and_alive_remaining():
    rows = enemy_rows({'enemies': [
        enemy(roster_id=1, max_hp=100.0, hp=40.0, alive=True),
        enemy(roster_id=2, max_hp=50.0, hp=0.0, alive=False, lifecycle='departed'),
        enemy(roster_id=3, spawn_order=3, max_hp=999.0, lifecycle='pending'),
    ]})
    totals = enemy_totals(rows)
    assert totals['entered_count'] == 2
    assert totals['alive_count'] == 1
    assert totals['entered_total_hp'] == 150
    assert totals['remaining_hp'] == 40
    assert totals['hp_complete'] is True


def test_timeline_is_ordered_deduplicated_and_downsampled():
    timeline = EnemyTimeline()
    rows = enemy_rows({'enemies': [enemy(max_hp=100.0, hp=100.0)]})
    assert timeline.add(1.0, rows, 0.0)['entered_total_hp'] == 100
    assert timeline.add(1.0, rows, 0.0) is None
    assert timeline.add(0.5, rows, 0.0) is None
    for second in range(2, 12):
        timeline.add(float(second), rows, float(second * 10))
    series = timeline.series(limit=5)
    assert len(series) == 5
    assert series[0]['game_time'] == 1.0 and series[-1]['game_time'] == 11.0
    assert [point['game_time'] for point in series] == sorted(point['game_time'] for point in series)


def test_telemetry_throttles_without_dropping_last_rows():
    now = [0.0]
    snapshots = [
        {'ok': True, 'enemies': [enemy(max_hp=100.0, hp=90.0)]},
        {'ok': True, 'enemies': [enemy(max_hp=100.0, hp=80.0)]},
    ]

    class Reader:
        def __init__(self):
            self.calls = 0

        def poll_fast(self):
            value = snapshots[min(self.calls, len(snapshots) - 1)]
            self.calls += 1
            return value

    telemetry = EnemyTelemetry(Reader(), clock=lambda: now[0],
                               bootstrap=lambda reader: True, poll_interval=1.0)
    first = telemetry.sample(game_time=1.0, our_damage=10)
    assert first['status'] == 'ready' and first['rows'][0]['hp'] == 90
    now[0] = 0.5
    throttled = telemetry.sample(game_time=1.5, our_damage=20)
    assert throttled['rows'][0]['hp'] == 90
    assert len(throttled['series']) == 1
    now[0] = 1.1
    fresh = telemetry.sample(game_time=2.0, our_damage=30)
    assert fresh['rows'][0]['hp'] == 80
    assert len(fresh['series']) == 2


def test_poll_failure_is_reported_not_raised():
    class Reader:
        def poll_fast(self):
            raise RuntimeError('channel lost')

    telemetry = EnemyTelemetry(Reader(), bootstrap=lambda reader: True,
                               clock=lambda: 10.0)
    state = telemetry.sample(game_time=1.0, our_damage=0)
    assert state['status'] == 'unavailable'
    assert 'channel lost' in state['error']
    assert state['rows'] == []


def test_bootstrap_failure_retries_then_locates():
    attempts = []

    def bootstrap(reader):
        attempts.append(1)
        return len(attempts) >= 2

    class Reader:
        def poll_fast(self):
            return {'ok': True, 'enemies': [enemy()]}

    now = [0.0]
    telemetry = EnemyTelemetry(Reader(), bootstrap=bootstrap,
                               clock=lambda: now[0], bootstrap_retry=3.0)
    assert telemetry.sample(game_time=1.0, our_damage=0)['status'] == 'locating'
    now[0] = 1.0
    assert len(attempts) == 1
    now[0] = 4.0
    state = telemetry.sample(game_time=4.0, our_damage=40)
    assert state['status'] == 'ready' and len(state['rows']) == 1


def test_bootstrap_uses_the_certified_battle_controller_without_a_second_scan(monkeypatch):
    import enemy_telemetry

    monkeypatch.setattr(enemy_telemetry, '_gs', SimpleNamespace(
        BattleControllerFields=SimpleNamespace(SCHEDULER=0x30),
        SchedulerFields=SimpleNamespace(M_MANAGED_WAVE_ENEMIES=0xC0)))

    class MC:
        def is_ptr(self, value):
            return bool(value)

        def read_klass_name(self, value):
            return 'Scheduler'

    class Reader:
        mc = MC()
        bc_addr = 1000

        def _read_ptr(self, address):
            return {0x418: 2000, 0x890: 3000}.get(address, 0)

        def _read_live_enemy_list(self, address):
            assert address == 3000
            return [11, 12]

        def _resolve_unit_manager(self, bc):
            assert bc == 1000
            return False

        def _union_enemy_ptrs(self, primary, fallback):
            return list(primary) + list(fallback)

        def _prefill(self):
            self.prefilled = True

    reader = Reader()
    assert enemy_telemetry.bootstrap_at_battle(reader) is True
    assert reader.sched_addr == 2000 and reader.list_addr == 3000
    assert reader.enemy_addrs == [11, 12]
    assert reader._spawn_plan == [] and reader._roster_initialized is False
    assert reader.prefilled


def test_bootstrap_does_not_require_the_static_handbook_bundle(monkeypatch):
    import enemy_telemetry

    monkeypatch.setattr(enemy_telemetry, '_gs', SimpleNamespace(
        BattleControllerFields=SimpleNamespace(SCHEDULER=0x30),
        SchedulerFields=SimpleNamespace(M_MANAGED_WAVE_ENEMIES=0xC0)))

    class MC:
        def is_ptr(self, value):
            return bool(value)

        def read_klass_name(self, value):
            return 'Scheduler'

    class Reader:
        mc = MC()
        bc_addr = 1000

        def _read_ptr(self, address):
            return {0x418: 2000, 0x890: 3000}.get(address, 0)

        def _read_live_enemy_list(self, address):
            return []

        def _resolve_unit_manager(self, bc):
            return False

        def _union_enemy_ptrs(self, primary, fallback):
            return []

        def _prefill(self):
            raise RuntimeError('static data missing')

    reader = Reader()
    assert enemy_telemetry.bootstrap_at_battle(reader) is True
    assert reader._spawn_plan == [] and reader._roster_initialized is False
    assert getattr(reader, '_db', None) is None


def test_timeline_keeps_enemy_hp_when_effective_damage_is_unknown():
    timeline = EnemyTimeline()
    rows = enemy_rows({'enemies': [enemy(max_hp=100.0, hp=100.0)]})
    point = timeline.add(1.0, rows, None)
    assert point['entered_total_hp'] == 100
    assert point['our_effective_damage'] is None
    assert point['our_damage_known'] is False


def test_bundled_enemy_catalog_provides_names_and_handbook_codes():
    import sys
    from pathlib import Path
    vendor = Path(__file__).resolve().parents[1] / 'vendor' / 'ark-source'
    sys.path.insert(0, str(vendor))
    try:
        from tools.enemy_health.enemy_db import load_enemy_db
        db = load_enemy_db()
    finally:
        sys.path.remove(str(vendor))
    assert db['enemy_1007_slime']['name']
    assert db['enemy_1007_slime']['code'] == 'B1'
    assert 'enemy_1027_mob' in db
