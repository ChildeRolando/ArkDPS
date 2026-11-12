from types import SimpleNamespace

from collector import EnemyTelemetry


def enemy(uid, *, eid='enemy_a', hp=75, max_hp=100, roster_id=1,
          lifecycle='active', alive=True):
    return SimpleNamespace(
        instance_uid=uid, roster_id=roster_id, spawn_order=roster_id, eid=eid,
        lifecycle=lifecycle, alive=alive, hp=hp, max_hp=max_hp,
        atk=1, def_=2, res=3, mspd=4, aspd=5,
    )


def event(sequence, frame, kind, *, uid='10', eid='enemy_a', **details):
    return dict(sequence=sequence, frame=frame, game_time=frame / 30,
                kind=kind, instance_uid=uid, eid=eid, **details)


class Battle:
    def __init__(self, *snapshots):
        self.now = 0
        self.polls = 0
        self.events = []
        self.snapshots = iter(snapshots)
        self.telemetry = EnemyTelemetry(self, clock=lambda: self.now, bootstrap=lambda _: True)

    def poll_fast(self):
        self.polls += 1
        frame, rows = next(self.snapshots)
        return dict(ok=True, fixed_frame=frame, enemies=rows)

    def sample(self, frame, *new_events, complete=True, damage=0, phases=None):
        self.events.extend(new_events)
        return self.telemetry.sample(
            game_time=frame / 30, frame=frame, our_damage=damage,
            enemy_state=dict(start_frame=10, complete=complete, events=self.events),
            enemy_phases=phases,
        )


def test_enemy_events_update_health_exit_and_same_frame_curve_without_polling():
    battle = Battle((10, [enemy('10')]))
    seeded = battle.sample(10, damage=12)
    assert seeded['enemy_state_complete'] is True
    battle.now = 2
    hurt = battle.sample(11, event(1, 11, 'enemy_hp', hp_before=75, hp_after=12), damage=23)
    assert hurt['rows'][0]['hp'] == hurt['last_point']['remaining_hp'] == 12
    assert hurt['last_point']['our_effective_damage'] == 11
    healed = battle.sample(11, event(2, 11, 'enemy_hp', hp_before=12, hp_after=33), damage=24)
    assert healed['rows'][0]['hp'] == healed['last_point']['remaining_hp'] == 33
    assert healed['last_point']['our_effective_damage'] == 12
    assert len(healed['series']) == 2
    battle.now = 6
    exited = battle.sample(12, event(3, 12, 'enemy_finish', hp_after=33, finish_reason=1))
    row, = exited['rows']
    assert row['lifecycle'] == 'departed' and row['alive'] is False
    assert row['hp'] == 33 and row['finish_reason'] == 1 and row['end_frame'] == 12
    assert row['end_reason'] == 'reach_exit'
    assert exited['last_point']['remaining_hp'] == 0
    later = battle.sample(13, event(4, 13, 'enemy_hp', hp_after=20))
    assert later['rows'][0] == row
    assert battle.polls == 1


def test_between_sample_entry_seeds_actual_hp_and_updates_pending_plan():
    pending = enemy(None, eid='enemy_b', max_hp=0, roster_id=2,
                    lifecycle='pending', alive=False)
    battle = Battle((10, [enemy('10'), pending]))
    initial = battle.sample(10)
    assert initial['totals']['planned_known_count'] == 1
    birth = event(1, 11, 'enemy_enter', uid='20', eid='enemy_b', hp_after=31,
                  enemy_entry_attributes={'max_hp': 200, 'atk': 23})
    entered = battle.sample(11, birth)
    new_row = next(row for row in entered['rows'] if row['instance_uid'] == '20')
    assert (new_row['hp'], new_row['max_hp'], new_row['alive']) == (31, 200, True)
    assert new_row['spawn_frame'] == 11 and new_row['atk'] == 23
    assert entered['totals']['planned_known_count'] == 2
    assert entered['totals']['entered_total_hp'] == 300
    assert entered['totals']['remaining_hp'] == 106
    assert battle.polls == 1


def test_fake_death_survives_calibration_and_rebirth_adds_phase_pool():
    battle = Battle(
        (10, [enemy('10', hp=60, max_hp=0)]),
        (13, [enemy('10', hp=60, lifecycle='departed', alive=False)]),
    )
    battle.sample(10)
    battle.now = 6
    waiting = battle.sample(12, event(1, 12, 'enemy_fake_death', hp_after=0))
    row, = waiting['rows']
    assert (row['lifecycle'], row['alive'], row['hp']) == ('rebirthing', True, 0)
    assert row['end_frame'] is None
    assert waiting['totals']['entered_count'] == waiting['totals']['alive_count'] == 1
    assert waiting['totals']['remaining_hp'] == 0
    reborn = battle.sample(
        13, event(2, 13, 'enemy_reborn', hp_after=60),
        phases={'complete': True, 'instances': {'10': {
            'eid': 'enemy_a', 'phase_total_hp': 160, 'phase_count': 2, 'phase_from_birth': True,
        }}},
    )
    row, = reborn['rows']
    assert (row['lifecycle'], row['hp'], row['phase_total_hp']) == ('active', 60, 160)
    assert reborn['totals']['entered_count'] == 1
    assert reborn['last_point']['entered_total_hp'] == 160
    assert battle.polls == 2


def test_gap_reconciles_newer_native_hp_without_resurrecting_finished_uid():
    battle = Battle(
        (10, [enemy('10'), enemy('20', roster_id=2)]),
        (11, [enemy('10', hp=5), enemy('20', hp=5, roster_id=2)]),
        (15, [enemy('10', hp=50), enemy('20', hp=50, roster_id=2)]),
    )
    battle.sample(10)
    battle.now = 2
    earlier = battle.sample(
        13, event(1, 12, 'enemy_hp', uid='20', hp_after=45),
        event(2, 13, 'enemy_finish', hp_after=0, finish_reason=2),
        complete=False,
    )
    assert earlier['enemy_state_complete'] is False
    assert [row['hp'] for row in earlier['rows']] == [0, 45]
    battle.now = 3
    fallback = battle.sample(15, complete=False)
    assert [row['hp'] for row in fallback['rows']] == [0, 50]
    assert fallback['rows'][0]['lifecycle'] == 'departed'
    assert fallback['totals']['remaining_hp'] == 50


def test_seed_newer_than_event_watermark_does_not_publish_mixed_frame_curve():
    battle = Battle((13, [enemy('10', hp=30)]))
    ahead = battle.sample(10)
    assert ahead['enemy_state_complete'] is False
    assert ahead['totals']['remaining_hp_complete'] is False
    assert ahead['last_point'] is None
    late_event = battle.sample(11, event(1, 11, 'enemy_hp', hp_after=60))
    assert late_event['rows'][0]['hp'] == 30
    aligned = battle.sample(13, event(2, 13, 'enemy_hp', hp_after=20))
    assert aligned['enemy_state_complete'] is True
    assert aligned['last_point']['remaining_hp'] == 20
    assert battle.polls == 1


def test_state_events_do_not_admit_an_unconfirmed_enemy():
    battle = Battle((10, [enemy('10')]))
    battle.sample(10)
    staged = battle.sample(
        11, event(1, 11, 'enemy_hp', uid='20', hp_after=8),
        event(2, 11, 'enemy_finish', uid='30', hp_after=0, finish_reason=2),
    )
    assert [row['instance_uid'] for row in staged['rows']] == ['10']
    confirmed = battle.sample(11, event(3, 11, 'enemy_enter', uid='20'))
    assert [row['instance_uid'] for row in confirmed['rows']] == ['10', '20']
    assert confirmed['rows'][1]['hp'] == 8
    assert battle.polls == 1


def test_late_fake_death_corrects_newer_native_departure_until_reborn_event():
    battle = Battle((13, [enemy('10', hp=0, lifecycle='departed', alive=False)]))
    battle.sample(10)
    waiting = battle.sample(11, event(1, 11, 'enemy_fake_death', hp_after=0))
    assert waiting['rows'][0]['lifecycle'] == 'rebirthing'
    reborn = battle.sample(12, event(2, 12, 'enemy_reborn', hp_after=60))
    assert reborn['rows'][0]['lifecycle'] == 'active'
    aligned = battle.sample(13)
    assert aligned['last_point']['remaining_hp'] == 60
    assert battle.polls == 1
