"""Native Boolean state is produced by actual source callbacks, never filled in."""
import json
from pathlib import Path
import subprocess

import pytest

from event_probe import ready_probe_modes


@pytest.fixture(scope='module')
def producer():
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run(['node', str(root / 'tests/event_probe_skill_cast_state_fixture.js')],
                         cwd=root, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


@pytest.mark.parametrize('backend', ['android', 'native_pc'])
@pytest.mark.parametrize('scenario,wait,reason', [
    ('wait_true', True, 1), ('wait_false', False, 1),
    ('other_reason_wait_true', True, 17), ('generic_actor_wait_true', True, 1),
])
def test_native_return_bool_and_original_reason_are_observed(producer, backend, scenario, wait, reason):
    report = producer[backend][scenario]
    native = [e for e in report['batch']['events'] if e['proof']['method'] == 'BasicSkill.OnCastFinish']
    assert len(native) == 1
    assert native[0]['skill_wait_for_end'] is wait
    assert native[0]['skill_reason'] == reason
    assert native[0]['source_character_uid'] == 2147483989
    assert any(e['proof']['method'] == 'Character.OnAfterSkill' for e in report['batch']['events'])


@pytest.mark.parametrize('backend', ['android', 'native_pc'])
def test_real_true_finish_marker_remains_between_two_activations(producer, backend):
    events = producer[backend]['two_activations']['batch']['events']
    cast = [e for e in events if e['proof']['method'] == 'BasicSkill.OnCastFinish']
    true_finish = [e for e in events if e['proof']['method'] == 'Character.OnSkillFinish']
    assert [e['skill_wait_for_end'] for e in cast] == [True, True]
    assert len(true_finish) == 1
    assert cast[0]['sequence'] < true_finish[0]['sequence'] < cast[1]['sequence']
    assert true_finish[0]['frame'] == 2543
    assert 'skill_wait_for_end' not in true_finish[0]


@pytest.mark.parametrize('backend', ['android', 'native_pc'])
@pytest.mark.parametrize('scenario', [
    'owner_uid', 'data_uid', 'zero_uid', 'owner_cid', 'skill_key',
    'current_skill', 'skill_owner', 'skill_class', 'owner_class', 'skill_data_class',
    'character_data_class', 'character_data_pointer', 'clock', 'clock_fraction', 'invalid_bool',
])
def test_unverified_identity_clock_or_bool_stays_unknown(producer, backend, scenario):
    report = producer[backend][scenario]
    assert report['mark']['skill_unknown'] is True
    assert not any('skill_wait_for_end' in event for event in report['batch']['events'])
    assert any(event['proof']['method'] == 'Character.OnAfterSkill' for event in report['batch']['events'])


@pytest.mark.parametrize('backend', ['android', 'native_pc'])
@pytest.mark.parametrize('scenario', [
    'wait_field_int', 'wait_field_static', 'static_method', 'ability_byref', 'reason_byref', 'reason_not_enum',
    'reason_size8', 'last_int', 'last_byref',
])
def test_unsupported_metadata_preserves_legacy_markers_without_a_bool(producer, backend, scenario):
    report = producer[backend][scenario]
    modes = ready_probe_modes(report['ready'])
    assert modes['skill_cast_state_ready'] is False
    assert isinstance(modes['skill_cast_state_error'], str)
    assert not any('skill_wait_for_end' in event for event in report['batch']['events'])


def test_installation_diagnostics_do_not_implicitly_certify_observation(producer):
    report = producer['native_pc']['wait_true']
    assert ready_probe_modes(report['ready'])['skill_cast_state_ready'] is True
    assert not report['initial_batch']['events']
    assert not any(key == 'skill_wait_for_end' for key in report['initial_mark'])
    assert 'skill_cast_state_ready' not in ready_probe_modes({'skill_cast_state_ready': 1})


@pytest.mark.parametrize('backend', ['android', 'native_pc'])
@pytest.mark.parametrize('scenario', ['foreign_enemy', 'foreign_token', 'null_owner',
                                      'non_entity_owner', 'unsupported_receiver', 'foreign_clock'])
def test_known_non_character_owner_is_ignored_before_player_fields(producer, backend, scenario):
    report = producer[backend][scenario]
    assert report['ready']['skill_cast_state_ready'] is True
    assert report['mark']['skill_unknown'] is False
    assert [event['kind'] for event in report['batch']['events']] == ['skill_start']
    assert report['batch']['events'][0]['source'] == 'char_1041_angel2'
