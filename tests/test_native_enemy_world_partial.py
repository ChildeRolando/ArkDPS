"""Partial native world facts never invent initial membership or continuity."""
from copy import deepcopy

import pytest

from enemy_telemetry import EnemyTelemetry
from test_native_enemy_events import event, snapshot, state


def born_records():
    return [event(1, 3, 'enemy_enter', hp_after=100.,
                  enemy_entry_attributes={'max_hp': 100., 'atk': 10.}),
            event(2, 6, 'enemy_hp', hp_before=100., hp_after=80., enemy_max_hp=100.)]


def entered():
    return {'complete': False, 'start_frame': 0, 'events': [dict(
        instance_uid='9', eid='enemy_a', frame=3, game_time=.1,
        enemy_entry_attributes={'max_hp': 100., 'atk': 10.})]}


def partial(telemetry, *, frame=6, records=None, seed=None, complete=False):
    return telemetry.sample_native_snapshot(seed, frame=frame, game_time=frame / 30.,
        enemy_state=state(born_records() if records is None else records, start=0, complete=complete),
        enemy_entry=entered(), enemy_damage={'complete': True,
            'by_uid': {'9': {'eid': 'enemy_a', 'amount': 20.}}}, allow_partial=True)


def assert_subset(view):
    assert view['ready'] and view['observed_subset_only']
    assert view['enemy_state_complete'] is False
    for totals in [view['totals'], view['last_point'], *view['series']]:
        assert totals['total_hp_complete'] is False
        assert totals['remaining_hp_complete'] is False
        assert totals['planned_complete'] is False
        assert totals['our_damage_complete'] is False


def test_lost_birth_scope_keeps_later_verified_entry_hp_and_damage_as_partial():
    telemetry = EnemyTelemetry(None)
    assert telemetry.sample_native_snapshot(snapshot(0), frame=0, game_time=0,
        enemy_state=state(start=0), allow_partial=True) is not None
    view = partial(telemetry)
    assert_subset(view)
    assert view['rows'][0]['hp'] == 80
    assert view['totals']['entered_total_hp'] == 100
    assert view['last_point']['our_effective_damage'] == 20
    later = partial(telemetry, frame=9, complete=True)
    assert_subset(later)  # A later ready mark cannot repair the missing prefix.
    row, = later['rows']
    assert row['hp'] is None and row['alive'] is None
    assert row['last_observed_hp'] == 80 and row['last_hp_observed_frame'] == 6


def test_verified_birth_without_seed_shows_only_that_entrant_and_does_not_change_quality():
    telemetry = EnemyTelemetry(None)
    records = born_records()
    original = deepcopy(records)
    view = partial(telemetry, records=records, complete=True)
    assert_subset(view)
    assert len(view['rows']) == 1 and view['rows'][0]['instance_uid'] == '9'
    assert view['rows'][0]['hp'] == 80
    assert view['totals']['entered_total_hp'] == 100
    assert view['last_point']['our_effective_damage'] == 20
    assert view['snapshot_observation'] == {'stale': True}
    assert records == original


def test_same_scope_seed_survives_two_partial_frames_without_inventing_a_birth():
    telemetry = EnemyTelemetry(None)
    assert telemetry.sample_native_snapshot(snapshot(0), frame=0, game_time=0,
        enemy_state=state(start=0), allow_partial=True) is not None
    observation = state([event(1, 3, hp_after=80.)], start=0, complete=False)
    first = telemetry.sample_native_snapshot(None, frame=3, game_time=.1,
        enemy_state=observation, allow_partial=True)
    assert first['ready'] and first['observed_subset_only']
    second = telemetry.sample_native_snapshot(None, frame=6, game_time=.2,
        enemy_state=observation, allow_partial=True)
    assert second['ready'] and second['observed_subset_only']
    assert second['enemy_state_complete'] is False
    row, = second['rows']
    assert row['hp'] is None
    assert row['last_observed_hp'] == 80 and row['last_hp_observed_frame'] == 3
    telemetry.reset()  # Collector scope reset must revoke that old native seed.
    assert telemetry.sample_native_snapshot(None, frame=6, game_time=.2,
        enemy_state=observation, allow_partial=True) is None


@pytest.mark.parametrize('kind', ['enemy_hp', 'enemy_finish'])
def test_no_birth_no_seed_never_upgrades_hp_or_finish_to_membership(kind):
    telemetry = EnemyTelemetry(None)
    fields = {'hp_after': 80} if kind == 'enemy_hp' else {'hp_after': 0, 'finish_reason': 2}
    value = telemetry.sample_native_snapshot(None, frame=6, game_time=.2,
        enemy_state=state([event(1, 6, kind, **fields)], start=0), allow_partial=True)
    assert value is None and telemetry.ready is False


@pytest.mark.parametrize('change', ['future', 'clock', 'identity'])
def test_partial_path_keeps_clock_and_identity_guards(change):
    telemetry = EnemyTelemetry(None)
    assert_subset(partial(telemetry))
    records = born_records()
    extra = event(3, 9, hp_after=70)
    if change == 'future':
        extra['frame'] = 30
    elif change == 'clock':
        extra['game_time'] = 10
    else:
        extra['eid'] = 'enemy_wrong_identity'
    records.append(extra)
    assert partial(telemetry, frame=9, records=records) is None

