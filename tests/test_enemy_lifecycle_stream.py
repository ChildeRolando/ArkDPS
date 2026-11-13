import pytest

from event_capture import LiveEventCapture
from event_stream import StreamGap
from roster import CharacterEntry, RosterSnapshot


READY = {'enemy_entry_ready': True, 'enemy_state_ready': True}


def ingest(capture, frame, sequence, *events, quality=READY, damage=100):
    sample = {
        'session': 'battle', 'frame': frame, 'game_time': frame / 30,
        'rows': [{'id': 'char_a', 'damage': damage, 'healing': 0,
                  'physical': damage, 'arts': 0, 'true': 0, 'elemental': 0}],
    }
    operator = CharacterEntry(cid='char_a', unique_id=1, is_token=False,
                              is_predefined=False, host_key=None, host_uid=None,
                              player_side=0)
    roster = RosterSnapshot(frame=frame, entries=(operator,),
                            present_unique_ids=frozenset({1}), present_known=True)
    capture.adapter.update(sample, roster)
    return capture.ingest(sample, roster, sequence, {'events': list(events), **quality})


def event(sequence, kind, **evidence):
    return {
        'sequence': sequence, 'kind': kind, 'frame': 2, 'game_time': 2 / 30,
        'source': None, 'amount': 0, 'target': 'enemy_a', 'target_instance': '7',
        **evidence,
    }


def test_enemy_lifecycle_events_share_the_damage_watermark_and_preserve_same_frame_order():
    capture = LiveEventCapture(None)
    initial = ingest(capture, 1, 0)
    assert initial['enemy_state'] == {'complete': True, 'start_frame': 1, 'events': []}
    events = [
        event(1, 'enemy_enter', hp_after=60_000, enemy_entry_attributes={'max_hp': 60_000}),
        event(2, 'enemy_hp', hp_before=60_000, hp_after=0),
        event(3, 'enemy_fake_death'),
        event(4, 'enemy_reborn', amount=60_000, hp_before=0, hp_after=60_000,
              phase_total_hp=120_000, phase_count=2, phase_from_birth=True),
        event(5, 'enemy_finish', hp_after=0, finish_reason=2),
        event(6, 'damage', source='char_a', amount=25, target_instance='0x7000'),
    ]
    result = ingest(capture, 2, 6, *events, damage=125)
    state = result['enemy_state']
    assert state['complete'] and state['start_frame'] == 1
    assert [(entry['sequence'], entry['kind']) for entry in state['events']] == [
        (e['sequence'], e['kind']) for e in events[:-1]]
    assert all((entry['eid'], entry['instance_uid'], entry['frame'], entry['game_time'])
               == ('enemy_a', '7', 2, 2 / 30) for entry in state['events'])
    assert state['events'][0]['hp_after'] == 60_000
    assert state['events'][1]['hp_before'] == 60_000
    assert state['events'][1]['hp_after'] == 0
    assert 'hp_after' not in state['events'][2]
    assert state['events'][3]['phase_total_hp'] == 120_000
    assert state['events'][4]['finish_reason'] == 2
    assert result['enemy_entry']['events'][0]['instance_uid'] == '7'
    assert result['enemy_phases']['instances']['7']['phase_count'] == 2
    assert result['rows']['char_a']['observed_damage'] == 25
    assert result['rows']['char_a']['damage'] == 125
    assert initial['enemy_state']['events'] == []
    assert ingest(capture, 3, 6, *events, damage=125)['enemy_state'] == state

    state['events'][0]['enemy_entry_attributes']['max_hp'] = 1
    assert capture.adapter.statistics.result()['enemy_state']['events'][0][
        'enemy_entry_attributes'] == {'max_hp': 60_000}


def test_enemy_max_hp_updates_are_preserved_independently_of_hp_and_damage():
    capture = LiveEventCapture(None)
    ingest(capture, 1, 0)
    result = ingest(
        capture, 2, 4, event(1, 'enemy_enter', hp_after=4_062.5,
                             enemy_entry_attributes={'max_hp': 4_062.5}),
        event(2, 'enemy_hp', hp_before=4_062.5, hp_after=4_062.5, enemy_max_hp=8_125),
        event(3, 'enemy_hp', hp_before=4_062.5, hp_after=4_037.5),
        event(4, 'damage', source='char_a', amount=25, target_instance='0x7000'),
        damage=125)
    state = result['enemy_state']
    assert state['complete']
    assert state['events'][0]['enemy_entry_attributes'] == {'max_hp': 4_062.5}
    assert state['events'][1]['enemy_max_hp'] == 8_125
    assert state['events'][1]['hp_before'] == state['events'][1]['hp_after'] == 4_062.5
    assert 'enemy_max_hp' not in state['events'][2]
    assert result['rows']['char_a']['observed_damage'] == 25
    assert result['rows']['char_a']['damage'] == 125


@pytest.mark.parametrize('invalid', [
    event(2, 'enemy_hp'),
    event(2, 'enemy_hp', hp_after=0, enemy_max_hp=0),
    event(2, 'enemy_hp', hp_after=0, enemy_max_hp=-1),
    event(2, 'enemy_hp', hp_after=0, enemy_max_hp=True),
    event(2, 'enemy_hp', hp_after=0, enemy_max_hp=float('inf')),
    event(2, 'enemy_hp', hp_after=0, enemy_max_hp=float('nan')),
    event(2, 'enemy_finish', hp_after=0, finish_reason=2, enemy_max_hp=100),
    event(2, 'enemy_hp', hp_after=True),
    event(2, 'enemy_hp', hp_after=float('nan')),
    event(2, 'enemy_finish', finish_reason=True),
    event(2, 'enemy_finish', finish_reason=0),
    event(2, 'enemy_finish', finish_reason=1 << 31),
    event(2, 'enemy_fake_death', hp_after=-1),
    event(2, 'enemy_hp', hp_after=0, target='enemy_other'),
])
def test_invalid_enemy_state_only_marks_enemy_state_incomplete(invalid):
    capture = LiveEventCapture(None)
    ingest(capture, 1, 0)
    result = ingest(
        capture, 2, 3, event(1, 'enemy_enter'), invalid,
        event(3, 'damage', source='char_a', amount=25, target_instance='0x7000'),
        damage=125)
    assert not result['enemy_state']['complete']
    assert [entry['sequence'] for entry in result['enemy_state']['events']] == [1]
    assert result['enemy_entry']['complete']
    assert result['rows']['char_a']['observed_damage'] == 25
    assert result['rows']['char_a']['event_coverage_complete']
    assert capture.adapter.event_stream.sequence == 3


def test_enemy_state_is_absent_for_legacy_journals_and_marks_a_late_capability_incomplete():
    capture = LiveEventCapture(None)
    legacy = {'enemy_entry_ready': True}
    ingest(capture, 1, 0, quality=legacy)
    result = ingest(capture, 2, 1, event(1, 'enemy_enter'), quality=legacy)
    assert 'enemy_state' not in result
    assert result['enemy_entry']['events'][0]['instance_uid'] == '7'
    result = ingest(capture, 3, 1)
    assert result['enemy_state'] == {'complete': False, 'start_frame': 1, 'events': []}
    assert result['rows']['char_a']['event_coverage_complete']


def test_state_quality_loss_and_shared_sequence_gap_preserve_the_verified_prefix():
    capture = LiveEventCapture(None)
    ingest(capture, 1, 0)
    born = event(1, 'enemy_enter')
    result = ingest(capture, 2, 1, born, quality={**READY, 'enemy_state_unknown': True})
    assert not result['enemy_state']['complete']
    assert result['rows']['char_a']['event_coverage_complete']

    with pytest.raises(StreamGap, match='watermark'):
        ingest(capture, 3, 2)
    result = capture.adapter.result()
    assert not result['enemy_state']['complete']
    assert [entry['sequence'] for entry in result['enemy_state']['events']] == [1]
    assert not result['rows']['char_a']['event_coverage_complete']


def test_initial_prefix_stays_outside_live_lifecycle_coverage():
    capture = LiveEventCapture(None)
    born = event(1, 'enemy_enter', frame=1, game_time=1 / 30)
    result = ingest(capture, 2, 1, born)
    assert result['enemy_state'] == {'complete': True, 'start_frame': 2, 'events': []}
