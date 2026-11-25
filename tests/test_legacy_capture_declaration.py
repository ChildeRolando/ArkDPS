"""A fixed producer capability cannot promote a live nominal-only stream."""
import pytest
from event_capture import LiveEventCapture
from event_probe import StreamGap
from test_event_capture import Collector, Observer


class Declaration(Observer):
    ready = False
    unknown = False
    def mark(self):
        return dict(super().mark(), enemy_damage_ready=self.ready, enemy_damage_unknown=self.unknown)
    def drain(self):
        return dict(super().drain(), partial_damage_version=7,
                    enemy_damage_ready=self.ready, enemy_damage_unknown=self.unknown)


def test_explicit_legacy_declaration_is_not_a_stream_gap_but_capability_flip_is():
    collector = Collector()
    source = Declaration(collector)
    capture = LiveEventCapture(collector, source)
    capture.sample()
    assert capture.adapter.statistics.legacy_observation_mode is True
    assert capture.adapter.statistics.settlement_stream_incomplete is False
    source.ready = True
    collector.frame, collector.game_time = 2, 1
    with pytest.raises(StreamGap, match='capability changed'):
        capture.sample()
    assert capture.closed


@pytest.mark.parametrize('ready,unknown', [(None, False), (0, False), (False, None), (False, True)])
def test_missing_numeric_or_unknown_readiness_never_enables_legacy_mode(ready, unknown):
    collector = Collector()
    source = Declaration(collector)
    source.ready, source.unknown = ready, unknown
    capture = LiveEventCapture(collector, source)
    capture.sample()
    assert capture.adapter.statistics.legacy_observation_mode is False
