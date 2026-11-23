"""Controlled combat through the C++ host and production ArkDPS consumer."""

import json
from pathlib import Path

import pytest

from battle_history import BattleHistory
from native_pc.internal_capture import CID, _builtin_fixture, run_internal_capture
from native_pc.internal_event_host.build import DEFAULT_OUTPUT
from panel_model import EventAvailability, PanelConfig, render_panel, rows_from_statistics
from test_event_probe_parity import declared_quality, event, first_window, second_window


@pytest.fixture
def native_event_host():
    if not DEFAULT_OUTPUT.is_file():
        pytest.skip('C++ internal event host has not been built')
    return DEFAULT_OUTPUT


def combat_fixture(*, complete_formula):
    fixture = _builtin_fixture('normal')
    baseline, opening, finish, departure = fixture['frames']
    opening_events = first_window()
    if not complete_formula:
        for record in opening_events:
            record.pop('mitigation_before', None)

    skill_end, damage, settlement = second_window()
    elemental_hp = dict(damage, kind='elemental_burst', damage_type=5)
    elemental_settlement = dict(settlement, damage_type=5)
    buildup = event('elemental_bar', 70, 2, target_instance=damage['target_instance'],
                    target_uid=damage['target_uid'])
    finish_events = [skill_end, buildup, elemental_hp, elemental_settlement]

    sequence = 0
    for frame, events, physical, elemental, healing in (
        (baseline, [], 0, 0, 0),
        (opening, opening_events, 20, 0, 80),
        (finish, finish_events, 20, 10, 80),
    ):
        frame['events'] = [dict(record, sequence=number, source=CID)
                           for number, record in enumerate(events, sequence + 1)]
        sequence += len(events)
        frame['mark'].update(declared_quality(), sequence=sequence, epoch=sequence)
        frame['snapshot'].update(global_total=physical + elemental, global_heal=healing)
        frame['snapshot']['rows'][0].update(damage=physical + elemental, healing=healing,
                                             physical=physical, elemental=elemental)
    departure.update(mark=finish['mark'], terminal_mark=finish['mark'])
    return fixture


@pytest.mark.parametrize('complete_formula', [True, False])
def test_internal_host_preserves_verified_combat_and_quality_through_history(
        native_event_host, tmp_path, complete_formula):
    fixture = tmp_path / 'mechanism-fixture.json'
    fixture.write_text(json.dumps(combat_fixture(complete_formula=complete_formula)), encoding='utf-8')
    state = tmp_path / 'state'
    published = []
    result = run_internal_capture(native_event_host, fixture, state, publish=published.append)

    assert result['status'] == 'ok'
    assert result['game_connected'] is False
    archive, = BattleHistory(state / 'history').list_recent().records
    assert archive.event_status == 'finished'
    active_sample = next(sample for sample in published if sample['frame'] == 30)
    active = active_sample['statistics']['rows'][CID]
    assert active['skill_split_complete'] and active['skill_split_effective_complete']
    assert active['skill_damage'] == 20 and active['skill_damage_effective'] == 8

    history = archive.statistics
    row = history['rows'][CID]
    view = render_panel(PanelConfig(panel_id='controlled-mechanisms', effective_only=True),
                        rows_from_statistics(history), event=EventAvailability('finished', live=False))
    detail = view.details_by_owner[CID]
    for projection in (result['last_sample']['statistics']['rows'][CID], row, detail):
        assert projection['damage'] == 30 and projection['effective_damage'] == 18
        assert projection['overkill_damage'] == 12
        assert projection['shield_absorbed'] == 3
        assert projection['skill_damage'] == 20 and projection['non_skill_damage'] == 10
        assert projection['skill_damage_effective'] == 8
        assert projection['non_skill_damage_effective'] == 10
        assert projection['skill_split_complete'] and projection['skill_split_effective_complete']
        settlement = projection['settled_damage']
        assert settlement['complete'] is True
        assert settlement['known_hp_damage'] == 18
        assert settlement['mitigation_complete'] is complete_formula
        assert settlement['mitigation_matched_hits'] == (2 if complete_formula else 1)
        assert settlement['observed_mitigation_rate'] == pytest.approx(40 if complete_formula else 0)
    assert row['healing'] == row['observed_healing'] == 80
    assert row['skill_window_count'] == 1 and row['skill_active_seconds'] == 1
    assert row['damage_by_type']['elemental'] == 10
    assert row['burst_damage'] == row['burst_settlement'] == 10
    assert row['burst_count'] == 1 and row['burst_added_settlement'] is None
    assert row['mitigation_rate'] == (pytest.approx(40) if complete_formula else None)

    records = []
    with Path(result['journal']).open(encoding='utf-8') as journal:
        for line in journal:
            entry = json.loads(line)
            if entry['kind'] == 'lifecycle_observation' and entry['data'].get('events'):
                records.extend(entry['data']['events']['drain']['events'])
    assert [record['sequence'] for record in records] == list(range(1, 10))
    healing, = [record for record in records if record['kind'] == 'healing']
    assert healing['proof']['direct_healing']['modifier_actual_delta'] == 23.5
    buildup, = [record for record in records if record['kind'] == 'elemental_bar']
    assert buildup['amount'] == 70
