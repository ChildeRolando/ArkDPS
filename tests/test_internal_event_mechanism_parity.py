"""Controlled combat through the C++ host and production ArkDPS consumer."""

from copy import deepcopy
from itertools import groupby
import json
from pathlib import Path

import pytest

from battle_history import BattleHistory
from event_observer import NativePcFridaObserver, ObserverStatus
from native_pc.internal_capture import CID, _builtin_fixture, run_internal_capture
from native_pc.internal_event_host.build import DEFAULT_OUTPUT
from panel_model import EventAvailability, PanelConfig, render_panel, rows_from_statistics
from test_event_probe_parity import declared_quality, event, first_window, second_window


SHIELD_OWNER = 'char_147_shining'
SHIELD_TARGET = CID
ARKFRAMEWORK_EXTRACT = Path(__file__).with_name('fixtures') / 'event_probe_business' / (
    'arkframework-v2-ce5-selected-hits.json'
)


@pytest.fixture
def native_event_host():
    if not DEFAULT_OUTPUT.is_file():
        pytest.skip('C++ internal event host has not been built')
    return DEFAULT_OUTPUT


def combat_fixture(*, complete_formula):
    fixture = _builtin_fixture('normal')
    baseline, opening, finish, departure = fixture['frames']
    opening_events = first_window()
    _, fatal_hit, fatal_settlement, _, _ = opening_events
    opening_events[1:1] = [
        dict(fatal_hit, amount=3, hp_before=11, hp_after=8, real_delta=-3,
             killed=False, mitigation_before=6, mitigation_after=3),
        dict(fatal_settlement, amount=3, nominal_amount=3, killed=False,
             mitigation_before=6, mitigation_after=3),
    ]
    opening_events[-1].update(
        source=SHIELD_OWNER, source_character_uid=2,
        proof=dict(buff='shining_s_2', target=SHIELD_TARGET, target_uid='1', before=5, after=2, incoming=3),
    )
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
        (opening, opening_events, 23, 0, 80),
        (finish, finish_events, 23, 10, 80),
    ):
        frame['events'] = [dict(record, sequence=number)
                           for number, record in enumerate(events, sequence + 1)]
        for record in frame['events']:
            if record['source'] == 'char_a':
                record['source'] = CID
        sequence += len(events)
        frame['mark'].update(declared_quality(), sequence=sequence, epoch=sequence)
        frame['snapshot'].update(global_total=physical + elemental, global_heal=healing)
        frame['snapshot']['rows'][0].update(damage=physical + elemental, healing=healing,
                                             physical=physical, elemental=elemental)
        frame['roster'] = [*frame['roster'], dict(frame['roster'][0], cid=SHIELD_OWNER, unique_id=2)]
        frame['snapshot']['rows'].append(dict(id=SHIELD_OWNER, damage=0, healing=0,
                                              physical=0, arts=0, true=0, elemental=0))
    departure.update(mark=finish['mark'], terminal_mark=finish['mark'])
    return fixture


def accepted_records(result):
    records = []
    with Path(result['journal']).open(encoding='utf-8') as journal:
        for line in journal:
            entry = json.loads(line)
            if entry['kind'] == 'lifecycle_observation' and entry['data'].get('events'):
                records.extend(entry['data']['events']['drain']['events'])
    return records


class RecordedPcObserver(NativePcFridaObserver):
    """Run the existing Python PC adapter against an owned RPC recording."""

    def __init__(self, _host):
        super().__init__(None)
        self.records = []
        self.current = self.terminal = None

    def start(self):
        self.status = ObserverStatus.READY

    def set_autochess_setting(self, enabled):
        super().set_autochess_setting(lambda: enabled)

    def advance(self, mark, events, terminal_mark=None):
        self.current, self.terminal = deepcopy(mark), deepcopy(terminal_mark)
        self.records.extend(deepcopy(events))
        return True

    def _request(self, operation, *, timeout=None, **arguments):
        if operation == 'mark':
            value = deepcopy(self.current)
            if self.terminal is not None:
                value['terminal_boundary'] = deepcopy(self.terminal)
            return value
        if operation == 'read':
            return dict(deepcopy(self.current), events=[deepcopy(record) for record in self.records
                if arguments['after'] < record['sequence'] <= arguments['through']])
        assert operation == 'ack'
        return {'acknowledged': arguments['sequence']}


@pytest.mark.parametrize('complete_formula', [True, False])
def test_internal_host_preserves_verified_combat_and_quality_through_history(
        native_event_host, tmp_path, complete_formula, monkeypatch):
    fixture = tmp_path / 'mechanism-fixture.json'
    fixture.write_text(json.dumps(combat_fixture(complete_formula=complete_formula)), encoding='utf-8')
    state = tmp_path / 'state'
    published = []
    result = run_internal_capture(native_event_host, fixture, state, publish=published.append)

    assert result['status'] == 'ok', result['last_sample']['event_error']
    assert result['game_connected'] is False
    archive, = BattleHistory(state / 'history').list_recent().records
    assert archive.event_status == 'finished'
    active_sample = next(sample for sample in published if sample['frame'] == 30)
    active = active_sample['statistics']['rows'][CID]
    assert active['skill_split_complete'] and active['skill_split_effective_complete']
    assert active['skill_damage'] == 23 and active['skill_damage_effective'] == 11

    history = archive.statistics
    row = history['rows'][CID]
    panel = PanelConfig(panel_id='controlled-mechanisms', effective_only=True)
    availability = EventAvailability('finished', live=False)
    view = render_panel(panel, rows_from_statistics(history), event=availability)
    detail = view.details_by_owner[CID]
    for projection in (result['last_sample']['statistics']['rows'][CID], row, detail):
        assert projection['damage'] == 33 and projection['effective_damage'] == 21
        assert projection['overkill_damage'] == 12
        assert projection['shield_absorbed'] == 0
        assert projection['skill_damage'] == 23 and projection['non_skill_damage'] == 10
        assert projection['skill_damage_effective'] == 11
        assert projection['non_skill_damage_effective'] == 10
        assert projection['skill_split_complete'] and projection['skill_split_effective_complete']
        settlement = projection['settled_damage']
        assert settlement['complete'] is True
        assert settlement['known_hp_damage'] == 21
        assert settlement['observed_hits'] == 3
        assert settlement['mitigation_complete'] is complete_formula
        assert settlement['mitigation_matched_hits'] == (3 if complete_formula else 1)
        assert settlement['observed_mitigation_rate'] == pytest.approx(100 * 23 / 56 if complete_formula else 0)
    assert result['last_sample']['statistics']['rows'][SHIELD_OWNER]['shield_absorbed'] == 3
    assert history['rows'][SHIELD_OWNER]['shield_absorbed'] == 3
    assert row['healing'] == row['observed_healing'] == 80
    assert row['skill_window_count'] == 1 and row['skill_active_seconds'] == 1
    assert row['damage_by_type']['elemental'] == 10
    assert row['burst_damage'] == row['burst_settlement'] == 10
    assert row['burst_count'] == 1 and row['burst_added_settlement'] is None
    assert row['mitigation_rate'] == (pytest.approx(100 * 23 / 56) if complete_formula else None)

    records = accepted_records(result)
    assert [record['sequence'] for record in records] == list(range(1, 12))
    hits = [record for record in records if record['kind'] == 'damage_settlement' and record['frame'] == 30]
    assert [(hit['sequence'], hit['target_instance'], hit['amount']) for hit in hits] == [
        (3, 'enemy-1', 3), (5, 'enemy-1', 8),
    ]
    healing, = [record for record in records if record['kind'] == 'healing']
    assert healing['proof']['direct_healing']['modifier_actual_delta'] == 23.5
    buildup, = [record for record in records if record['kind'] == 'elemental_bar']
    assert buildup['amount'] == 70
    shield, = [record for record in records if record['kind'] == 'shield_absorbed']
    assert (shield['source'], shield['source_character_uid'], shield['proof']['target']) == (
        SHIELD_OWNER, 2, SHIELD_TARGET,
    )
    assert shield['proof']['before'] - shield['proof']['after'] == shield['amount'] == 3

    monkeypatch.setattr('native_pc.internal_event_source.InternalEventProbe', RecordedPcObserver)
    reference_dir = tmp_path / 'reference'
    reference_samples = []
    reference = run_internal_capture(native_event_host, fixture, reference_dir, publish=reference_samples.append)
    reference_archive, = BattleHistory(reference_dir / 'history').list_recent().records
    assert reference['status'] == 'ok' and reference['fixture_sha256'] == result['fixture_sha256']
    assert accepted_records(reference) == records
    assert [(sample['frame'], sample['event_status'], sample['event_watermark'], sample['statistics']['rows'])
            for sample in reference_samples] == [
        (sample['frame'], sample['event_status'], sample['event_watermark'], sample['statistics']['rows'])
        for sample in published
    ]
    reference_view = render_panel(panel, rows_from_statistics(reference_archive.statistics), event=availability)
    assert reference_archive.statistics['rows'] == history['rows']
    assert reference_view == view


def arkframework_fixture(originals):
    """Wrap shared fields with owned clocks and identity scaffolding for replay."""
    fixture = _builtin_fixture('normal')
    baseline, opening, terminal, departure = fixture['frames']
    actor_uids = {cid: index for index, cid in enumerate(sorted({r['actor_id'] for r in originals}), 1)}
    counters = {cid: dict(id=cid, damage=0, healing=0, physical=0, arts=0, true=0, elemental=0)
                for cid in actor_uids}

    def tick(frame, game_time, through):
        frame['mark'].update(declared_quality(), skill_unknown=True, shield_unknown=True,
                             mitigation_ready=False, mitigation_unknown=True, enemy_damage_unknown=True,
                             frame=frame['snapshot']['frame'], game_time=game_time,
                             sequence=through, epoch=through)
        frame['snapshot'].update(game_time=game_time, rows=[dict(row) for row in counters.values()],
                                 global_total=sum(row['damage'] for row in counters.values()),
                                 global_heal=sum(row['healing'] for row in counters.values()))
        prototype = frame['roster'][0]
        frame['roster'] = [dict(prototype, cid=cid, unique_id=uid) for cid, uid in actor_uids.items()]

    tick(baseline, 0, 0)
    sequence = 0
    for frame, (logic_frame, hits) in zip((opening, terminal), groupby(originals, key=lambda r: r['frame'])):
        frame['snapshot']['frame'] = logic_frame
        frame['events'] = []
        for original in hits:
            sequence += 1
            cid, game_time = original['actor_id'], original['game_time']
            proof = dict(arkframework_v2=original)
            event = dict(sequence=sequence, frame=logic_frame, game_time=game_time,
                         source=cid, source_character_uid=actor_uids[cid], proof=proof)
            if original['damage_type'] == 4:
                direct = original['direct_healing']
                proof['direct_healing'] = dict(amount=direct['amount'],
                                               modifier_actual_delta=direct['actual_hp_restored'])
                event.update(kind='healing', amount=direct['amount'])
                counters[cid]['healing'] += direct['amount']
            else:
                # V2 has no target instance or same-callback shield evidence.
                # The surrogate receipt IDs isolate events without asserting enemy identity.
                event.update(kind='damage_settlement', amount=original['actual_damage'],
                             nominal_amount=original['calculated_damage'], damage_type=original['damage_type'],
                             target=original['target_id'], target_uid=str(sequence),
                             target_instance=f'controlled-v2-receipt-{sequence}', settlement_complete=False)
                counters[cid]['damage'] += original['calculated_damage']
                counters[cid]['physical'] += original['calculated_damage']
            frame['events'].append(event)
        tick(frame, game_time, sequence)
    departure.update(mark=terminal['mark'], terminal_mark=terminal['mark'])
    return fixture


def test_internal_host_matches_frozen_v2_overlap_without_certifying_extra_fields(native_event_host, tmp_path):
    originals = json.loads(ARKFRAMEWORK_EXTRACT.read_bytes())['events']
    assert [(r['sequence'], r['frame'], r['frame_event_index']) for r in originals] == [
        (63, 388, 0), (64, 388, 1), (262, 1082, 0), (263, 1082, 1),
    ]
    fixture = tmp_path / 'frozen-v2-owned-scaffold.json'
    fixture.write_text(json.dumps(arkframework_fixture(originals)), encoding='utf-8')
    state = tmp_path / 'state'
    result = run_internal_capture(native_event_host, fixture, state)
    assert result['status'] == 'ok', result['last_sample']['event_error']
    records = accepted_records(result)
    assert [r['proof']['arkframework_v2'] for r in records] == originals
    for record, original in zip(records, originals):
        assert (record['frame'], record['game_time'], record['source']) == (
            original['frame'], original['game_time'], original['actor_id'],
        )
        if record['kind'] == 'healing':
            assert record['amount'] == original['calculated_damage']
            assert record['proof']['direct_healing']['modifier_actual_delta'] == original['actual_damage']
        else:
            assert record['nominal_amount'] == original['calculated_damage']
            assert record['amount'] == original['actual_damage']
            assert record['target'] == original['target_id']
    archive, = BattleHistory(state / 'history').list_recent().records
    closure = archive.statistics['rows']['char_4228_closur']
    shining = archive.statistics['rows'][SHIELD_OWNER]
    assert closure['settled_damage']['known_hp_damage'] == pytest.approx(
        sum(record['actual_damage'] for record in originals if record['damage_type'] != 4)
    )
    assert closure['settled_damage']['observed_hits'] == 3
    assert shining['observed_healing'] == originals[1]['calculated_damage']
    assert closure['overkill_damage'] is None and closure['mitigation_rate'] is None
    assert closure['skill_damage'] is None and shining['shield_absorbed'] is None
