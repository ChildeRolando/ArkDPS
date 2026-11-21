"""Pure Windows enemy row projections; no reader, game, events or UI."""

from copy import deepcopy

import pytest

# Use the existing offline vendor-path bootstrap before loading telemetry.
# Importing Collector neither connects to a device nor creates a reader.
import collector
from enemy_telemetry import native_enemy_view


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
