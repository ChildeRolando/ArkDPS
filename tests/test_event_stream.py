import pytest

from event_stream import EventStream, StreamGap
from stats_core import Statistics


def stream():
    stats = Statistics('battle', 30, event_coverage=True)
    stats.register_source('a', 'host')
    stats.register_source('token', 'host')
    stats.snapshot(30, {'a': {'damage': 1000, 'healing': 500},
                        'token': {'damage': 0, 'healing': 0}})
    return stats, EventStream(stats, session='battle', baseline_frame=900,
                              baseline_sequence=10)


def test_same_frame_after_barrier_and_replay_do_not_change_snapshot():
    stats, receiver = stream()
    event = dict(session='battle', sequence=11, frame=900, game_time=30,
                 source='a', kind='damage', amount=20)
    receiver.accept(**event)
    receiver.accept(**event)
    assert stats.result()['rows']['host']['damage'] == 1000
    assert stats.result()['rows']['host']['observed_damage'] == 20


@pytest.mark.parametrize('change', [dict(sequence=12), dict(frame=899),
                                    dict(session='other'), dict(source='unknown')])
def test_invalid_or_missing_event_closes_coverage(change):
    stats, receiver = stream()
    event = dict(session='battle', sequence=11, frame=900, game_time=30,
                 source='a', kind='damage', amount=20)
    event.update(change)
    with pytest.raises(StreamGap):
        receiver.accept(**event)
    assert stats.result()['rows']['host']['event_coverage_complete'] is False
    assert stats.result()['rows']['host']['damage'] == 1000


def test_conflicting_replay_and_out_of_order_frame_close_coverage():
    stats, receiver = stream()
    event = dict(session='battle', sequence=11, frame=900, game_time=30,
                 source='token', kind='shield_absorbed', amount=2)
    receiver.accept(**event)
    with pytest.raises(StreamGap):
        receiver.accept(**{**event, 'amount': 3})
    assert stats.result()['rows']['host']['shield_absorbed'] == 2
    assert stats.result()['rows']['host']['event_coverage_complete'] is False


def test_watermark_detects_missing_tail_and_stop_closes_coverage():
    stats, receiver = stream()
    with pytest.raises(StreamGap):
        receiver.watermark(session='battle', sequence=11, frame=901, game_time=30)
    assert stats.result()['rows']['host']['event_coverage_end'] == 30


def test_prebaseline_event_never_counted():
    stats, receiver = stream()
    with pytest.raises(StreamGap):
        receiver.accept(session='battle', sequence=10, frame=900, game_time=30,
                        source='a', kind='damage', amount=20)
    assert stats.result()['rows']['host']['observed_damage'] == 0


def test_gap_ends_at_last_event_not_a_later_snapshot():
    stats, receiver = stream()
    stats.snapshot(32, {'a': {'damage': 1200, 'healing': 500},
                        'token': {'damage': 0, 'healing': 0}})
    receiver.accept(session='battle', sequence=11, frame=930, game_time=31,
                    source='a', kind='damage', amount=100)
    with pytest.raises(StreamGap):
        receiver.accept(session='battle', sequence=13, frame=960, game_time=32,
                        source='a', kind='damage', amount=100)
    result = stats.result()['rows']['host']
    assert result['event_coverage_end'] == 31
    assert result['damage'] == 1200


def test_watermark_advances_reliable_coverage_and_rejects_older_events():
    stats, receiver = stream()
    stats.snapshot(32, {'a': {'damage': 1000, 'healing': 500},
                        'token': {'damage': 0, 'healing': 0}})
    receiver.watermark(session='battle', sequence=10, frame=930, game_time=31)
    with pytest.raises(StreamGap):
        receiver.accept(session='battle', sequence=11, frame=920, game_time=30.5,
                        source='a', kind='damage', amount=1)
    assert stats.result()['rows']['host']['event_coverage_end'] == 31


@pytest.mark.parametrize('frame,game_time', [(899, 30), (900, 31), (930, float('nan')), (930, 33)])
def test_invalid_watermark_closes_coverage(frame, game_time):
    stats, receiver = stream()
    stats.snapshot(32, {'a': {'damage': 1000, 'healing': 500},
                        'token': {'damage': 0, 'healing': 0}})
    with pytest.raises(StreamGap):
        receiver.watermark(session='battle', sequence=10, frame=frame, game_time=game_time)
    assert stats.result()['rows']['host']['event_coverage_end'] == 30


def test_overkill_evidence_crosses_the_ordered_receiver_and_is_compared_on_replay():
    stats, receiver = stream()
    event = dict(session='battle', sequence=11, frame=900, game_time=30,
                 source='token', kind='damage', amount=200,
                 target='enemy_1', real_delta=-44, hp_before=44, hp_after=0,
                 killed=True, target_type=0, damage_type=1, shield_absorbed=0)
    receiver.accept(**event)
    row = stats.result()['rows']['host']
    assert row['overkill_damage'] == 156
    assert row['overkill_coverage_complete'] is True
    receiver.accept(**event)
    with pytest.raises(StreamGap):
        receiver.accept(**{**event, 'hp_before': 45})
    assert stats.result()['rows']['host']['overkill_coverage_complete'] is False


def test_invalid_supplemental_hp_evidence_never_closes_the_damage_stream():
    stats, receiver = stream()
    receiver.accept(session='battle', sequence=11, frame=900, game_time=30,
                    source='a', kind='hp_damage', amount=float('nan'))
    receiver.accept(session='battle', sequence=12, frame=900, game_time=30,
                    source='a', kind='damage', amount=20)
    row = stats.result()['rows']['host']
    assert row['observed_damage'] == 20
    assert row['event_coverage_complete'] is True


def test_skill_window_events_cross_the_ordered_receiver():
    stats, receiver = stream()
    receiver.accept(session='battle', sequence=11, frame=900, game_time=30,
                    source='a', kind='skill_start', amount=1,
                    target_instance='Character.OnSkillStart', skill_key='k',
                    skill_name='技能A')
    receiver.accept(session='battle', sequence=12, frame=900, game_time=30,
                    source='a', kind='damage', amount=20, target='enemy_1')
    receiver.accept(session='battle', sequence=13, frame=900, game_time=30,
                    source='a', kind='skill_end', amount=1,
                    target_instance='Character.OnSkillFinish', skill_key='k')
    row = stats.result()['rows']['host']
    assert row['skill_damage'] == 20
    assert row['non_skill_damage'] == 0
    assert row['skill_split_complete'] is True
    assert row['event_coverage_complete'] is True
