import pytest

from event_stream import StreamGap, baseline_barrier


def mark(**changes):
    return {'sequence': 10, 'epoch': 20, 'active': 0, 'frame': 900,
            'game_time': 30.0, **changes}


def test_snapshot_between_quiescent_marks_starts_after_baseline():
    assert baseline_barrier(mark(), {'frame': 900, 'game_time': 30.0}, mark()) == 10


@pytest.mark.parametrize('before,after', [
    (mark(active=1), mark()),
    (mark(), mark(active=1)),
    (mark(), mark(epoch=21)),
    (mark(), mark(sequence=11)),
    (mark(), mark(frame=901)),
    (mark(), mark(game_time=30.1)),
    (mark(sequence=True), mark(sequence=True)),
    (mark(game_time=float('nan')), mark(game_time=float('nan'))),
    (mark(), mark(error='callback failed')),
    (mark(), mark(stopped=True)),
])
def test_baseline_rejects_callbacks_in_flight_or_during_sample(before, after):
    with pytest.raises(StreamGap):
        baseline_barrier(before, {'frame': 900, 'game_time': 30.0}, after)


def test_baseline_rejects_mismatched_snapshot():
    with pytest.raises(StreamGap):
        baseline_barrier(mark(), {'frame': 901, 'game_time': 30.0}, mark())
