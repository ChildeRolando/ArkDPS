"""Pure Windows enemy row projections; no reader, game, events or UI."""

from copy import deepcopy

import pytest

# Use the existing offline vendor-path bootstrap before loading telemetry.
# Importing Collector neither connects to a device nor creates a reader.
import collector
from enemy_telemetry import NativeEnemyObservations, native_enemy_view


def entity(uid=2115, **changes):
    return {
        'instance_uid': uid, 'enemy_key': 'enemy_1007_slime_2',
        'inited': True, 'borned': True, 'is_disappeared': False,
        'native_object_present': True, 'hp_status': 'available',
        'hp': 731.4000005275011, **changes,
    }


def snapshot(*entities):
    return {'enemies_status': 'available', 'enemies': {
        'scope': 'unit_manager_enemies_membership',
        'collection_count': len(entities), 'entities': list(entities),
    }}


def view(raw, **changes):
    return native_enemy_view(raw, **{
        'frame': 286, 'game_time': 9.533324241638184,
        'catalog': {'enemy_1007_slime_2': {'name': '源石虫', 'code': 'B1'}}, **changes,
    })


def test_verified_current_hp_keeps_exact_watermark_and_no_capacity_or_history_claims():
    raw = snapshot(entity(max_hp=9999, alive=True, atk=123, end_reason='death'))
    before = deepcopy(raw)
    result = view(raw)
    assert result == {
        'scope': 'native_pc_current_hp', 'status': 'ready', 'ready': True,
        'error': None, 'observed_frame': 286, 'observed_game_time': 9.533324241638184,
        'rows': [{
            'instance_uid': '2115', 'eid': 'enemy_1007_slime_2', 'name': '源石虫',
            'code': 'B1', 'lifecycle': 'current', 'hp': 731.4000005275011,
            'max_hp': None, 'hp_ratio': None, 'hp_known': False,
        }], 'totals': {}, 'series': [],
    }
    assert raw == before


def test_rows_sort_by_numeric_identity_and_use_only_catalog_labels():
    result = view(snapshot(entity(10), entity(2, enemy_key='enemy_unknown', name='unverified')))
    assert [row['instance_uid'] for row in result['rows']] == ['2', '10']
    assert result['rows'][0]['name'] == 'enemy_unknown' and result['rows'][0]['code'] == ''
    assert view(snapshot(entity()), catalog=None)['rows'][0]['name'] == 'enemy_1007_slime_2'


@pytest.mark.parametrize('changes', [
    {'inited': False}, {'inited': 1}, {'borned': False}, {'borned': None},
    {'is_disappeared': True}, {'is_disappeared': 0},
    {'native_object_present': False}, {'native_object_present': 1},
    {'enemy_key': ''}, {'enemy_key': ' enemy_1'},
])
def test_unverified_membership_flags_or_key_cannot_render_as_current(changes):
    result = view(snapshot(entity(**changes)))
    assert result['status'] == 'ready' and result['rows'] == []
    assert result['totals'] == {} and result['series'] == []


def test_only_positive_unique_uint32_uids_are_kept_without_selecting_a_duplicate_winner():
    result = view(snapshot(entity(0), entity(-1), entity(True), entity('2115'),
                           entity(2**32), entity(12), entity(12, hp=10), entity(13)))
    assert [row['instance_uid'] for row in result['rows']] == ['13']


@pytest.mark.parametrize('hp', [None, True, '75', -1, float('nan'), float('inf'), -float('inf')])
def test_missing_invalid_or_nonfinite_hp_stays_unknown(hp):
    result = view(snapshot(entity(hp=hp)))
    assert result['rows'][0]['hp'] is None
    assert result['rows'][0]['max_hp'] is None and result['rows'][0]['hp_ratio'] is None
    assert result['totals'] == {}


def test_unverified_getter_does_not_publish_numeric_storage_and_zero_is_retained_when_verified():
    assert view(snapshot(entity(hp_status='unavailable', hp=123)))['rows'][0]['hp'] is None
    assert view(snapshot(entity(hp=0)))['rows'][0]['hp'] == 0


def test_unavailable_snapshot_is_distinct_from_a_successfully_empty_collection():
    unknown = view({'enemies_status': 'unavailable', 'enemies_reason': 'enemy_field_layout',
                    'enemies': None})
    assert unknown['status'] == 'unavailable' and not unknown['ready']
    assert unknown['error'] == 'enemy_field_layout'
    assert unknown['rows'] == [] and unknown['totals'] == {} and unknown['series'] == []
    empty = view(snapshot())
    assert empty['status'] == 'ready' and empty['ready'] and empty['error'] is None
    assert empty['rows'] == [] and empty['totals'] == {} and empty['series'] == []


@pytest.mark.parametrize('raw', [
    None, {}, {'enemies_status': 'available', 'enemies': None},
    {'enemies_status': 'available', 'enemies': {'scope': 'unverified', 'collection_count': 0, 'entities': []}},
    {'enemies_status': 'available', 'enemies': {'scope': 'unit_manager_enemies_membership',
                                            'collection_count': 1, 'entities': []}},
])
def test_missing_or_inconsistent_envelope_is_unavailable(raw):
    result = view(raw)
    assert result['status'] == 'unavailable' and result['rows'] == []


@pytest.mark.parametrize('frame,game_time', [(True, 1), (-1, 1), (3, -1),
                                           (3, float('nan')), (3, True)])
def test_invalid_watermark_is_unknown_instead_of_fabricating_a_frame(frame, game_time):
    result = view(snapshot(entity()), frame=frame, game_time=game_time)
    assert result['status'] == 'unavailable' and result['error'] == 'native_enemy_watermark_invalid'


def test_native_history_retains_last_hp_only_after_verified_membership_change():
    tracker = NativeEnemyObservations()

    def publish(raw, frame):
        return tracker.observe(raw, frame=frame, game_time=frame / 30, catalog={})

    first = publish(snapshot(entity(1, hp=900)), 30)
    assert publish(snapshot(), 30) == first
    assert not publish({'enemies_status': 'unavailable'}, 40)['ready']
    uncertain = publish(snapshot(entity(1, native_object_present=False, hp=3)), 60)
    row, = uncertain['rows']
    assert row['lifecycle'] == 'entered' and row['hp'] is None
    assert row['last_observed_hp'] == 900

    departed = publish(snapshot(entity(2, hp=400)), 90)
    current, previous = departed['rows']
    assert current['instance_uid'] == '2' and current['hp'] == 400
    assert previous['instance_uid'] == '1' and previous['lifecycle'] == 'departed'
    assert previous['hp'] is None and previous['last_observed_hp'] == 900
    assert 'end_reason' not in previous
    assert departed['totals'] == dict(entered_count=2, current_count=1, current_hp_count=1,
                                      remaining_hp=400, total_hp_complete=False,
                                      remaining_hp_complete=True)
    assert 'entered_total_hp' not in departed['series'][-1]

    returned = publish(snapshot(entity(1, hp=80), entity(2, hp=400)), 120)
    assert returned['totals']['entered_count'] == 2
    assert returned['rows'][0]['lifecycle'] == 'current'
    assert returned['rows'][0]['hp'] == 80
    tracker.reset()
    assert publish(snapshot(entity(2, hp=100)), 30)['totals']['entered_count'] == 1


def test_incomplete_membership_does_not_prove_departure():
    tracker = NativeEnemyObservations()
    tracker.observe(snapshot(entity(1)), frame=30, game_time=1, catalog={})
    uncertain = tracker.observe(snapshot(entity(2), entity(2)), frame=60, game_time=2, catalog={})
    row, = uncertain['rows']
    assert row['instance_uid'] == '1' and row['lifecycle'] == 'entered'
    assert row['hp'] is None
    assert uncertain['totals']['remaining_hp'] is None
    assert not uncertain['totals']['remaining_hp_complete']


def test_current_hp_total_distinguishes_partial_sum_verified_zero_and_missing_sample():
    tracker = NativeEnemyObservations()

    def publish(raw, frame):
        return tracker.observe(raw, frame=frame, game_time=frame / 30, catalog={})

    partial = publish(snapshot(entity(1, hp=100), entity(2, hp_status='unavailable')), 30)
    assert partial['totals']['current_count'] == 2
    assert partial['totals']['current_hp_count'] == 1
    assert partial['totals']['remaining_hp'] == 100
    assert partial['series'][-1]['remaining_hp_complete'] is False

    gap = publish({'enemies_status': 'unavailable'}, 60)
    assert not gap['ready'] and 'remaining_hp' not in gap['totals']

    empty = publish(snapshot(), 90)
    assert empty['totals']['entered_count'] == 2
    assert empty['totals']['current_count'] == empty['totals']['current_hp_count'] == 0
    assert empty['series'][-1]['remaining_hp'] == 0
    assert empty['series'][-1]['remaining_hp_complete'] is True
    assert not empty['totals']['total_hp_complete']


def test_native_history_retains_current_instances_when_history_is_capped(monkeypatch):
    monkeypatch.setattr('enemy_telemetry.MAX_NATIVE_ENEMY_HISTORY', 2)
    tracker = NativeEnemyObservations()
    tracker.observe(snapshot(entity(1)), frame=30, game_time=1, catalog={})
    tracker.observe(snapshot(entity(2)), frame=60, game_time=2, catalog={})
    view = tracker.observe(snapshot(entity(3)), frame=90, game_time=3, catalog={})
    assert view['history_truncated'] and view['totals']['entered_count'] == 2
    assert [row['instance_uid'] for row in view['rows']] == ['3', '2']
