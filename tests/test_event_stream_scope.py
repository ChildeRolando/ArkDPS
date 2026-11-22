from copy import deepcopy

import pytest

from event_stream import StreamGap
from test_event_stream import stream


def test_scope_excluded_elemental_bar_cannot_replace_current_scope_witness():
    stats, receiver = stream()
    stats.settlement_enabled = True
    stats.record_settlement_bar('17', 'enemy_a', '0xabc', 'local', 'host', 123)
    before = deepcopy(stats.settlement_bars)
    event = dict(session='battle', sequence=11, frame=900, game_time=30,
                 source='a', kind='elemental_bar', amount=0, target='enemy_a',
                 target_uid='17', target_instance='0xabc', settlement_identity=('foreign', None, None))
    receiver.accept(None, (), True, **event)
    assert receiver.sequence == 11 and stats.settlement_bars == before
    # A different player in this same scope must still supply negative evidence.
    receiver.accept(**dict(event, sequence=12))
    assert stats.settlement_bars['17'][2] == 'foreign'


@pytest.mark.parametrize('change', [dict(sequence=12), dict(frame=899), dict(game_time=29)])
def test_scope_exclusion_does_not_bypass_sequence_or_clock(change):
    stats, receiver = stream()
    event = dict(session='battle', sequence=11, frame=900, game_time=30,
                 source='a', kind='damage', amount=20)
    event.update(change)
    with pytest.raises(StreamGap):
        receiver.accept(None, (), True, **event)
    assert receiver.closed and not stats.result()['rows']['host']['event_coverage_complete']


def test_scope_exclusion_replay_keeps_original_record_and_amounts_unchanged():
    stats, receiver = stream()
    event = dict(session='battle', sequence=11, frame=900, game_time=30,
                 source='a', kind='damage', amount=20)
    raw = dict(event, native_autochess_scope=dict(scope_key='old_scope', phase=5, round=1))
    before = stats.result()
    receiver.accept(None, (), True, replay_record=raw, **event)
    receiver.accept(None, (), True, replay_record=deepcopy(raw), **event)
    assert stats.result() == before
    changed = deepcopy(raw)
    changed['native_autochess_scope']['scope_key'] = 'new_scope'
    with pytest.raises(StreamGap, match='replay conflicts'):
        receiver.accept(None, (), True, replay_record=changed, **event)


def test_producer_cannot_select_consumer_scope_exclusion_by_keyword():
    stats, receiver = stream()
    with pytest.raises(TypeError):
        receiver.accept(session='battle', sequence=11, frame=900, game_time=30,
                        source='a', kind='damage', amount=20, skip_scope=True)
    assert receiver.sequence == 10 and stats.result()['rows']['host']['observed_damage'] == 0
