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


def test_supported_to_unsupported_flip_also_closes_without_accepting_events():
    collector = Collector()
    source = Declaration(collector); source.ready = True
    capture = LiveEventCapture(collector, source); capture.sample()
    source.ready = False
    collector.frame, collector.game_time = 2, 1
    with pytest.raises(StreamGap, match='capability changed'): capture.sample()
    assert capture.closed and capture.adapter.event_stream.sequence == 0


def test_unknown_flag_may_change_without_changing_the_fixed_capability():
    collector = Collector(); source = Declaration(collector)
    capture = LiveEventCapture(collector, source); capture.sample()
    source.unknown = True
    collector.frame, collector.game_time = 2, 1
    capture.sample()
    assert not capture.closed and capture.adapter.statistics.legacy_observation_mode is True
    assert capture.adapter.statistics.settlement_stream_incomplete is True


def test_conflicting_literal_mark_and_drain_is_rejected_even_at_initial_baseline():
    collector = Collector(); source = Declaration(collector)
    raw = source.mark
    source.mark = lambda: dict(raw(), enemy_damage_ready=True)
    capture = LiveEventCapture(collector, source)
    with pytest.raises(StreamGap, match='mark and drain'): capture.sample()
    assert capture.closed


def test_new_observer_and_new_stream_can_declare_another_fixed_capability():
    first = LiveEventCapture(Collector(), None)
    collector = Collector(); source = Declaration(collector)
    first = LiveEventCapture(collector, source); first.sample(); first.close()
    other = Collector(); replacement = Declaration(other); replacement.ready = True
    current = LiveEventCapture(other, replacement); current.sample()
    assert not current.closed and current.adapter.statistics.legacy_observation_mode is False


def test_paired_legacy_and_hp_certificate_never_enter_ledger_after_capability_flip():
    from test_event_probe_parity import CanonicalCollector, BoundedObserver, declared_quality, first_window
    from event_probe import NATIVE_PC_CONTRACT
    class Source(BoundedObserver):
        def mark(self):
            return dict(super().mark(), enemy_damage_ready=self.quality['enemy_damage_ready'],
                        enemy_damage_unknown=self.quality['enemy_damage_unknown'])
    collector = CanonicalCollector('native_pc')
    quality = declared_quality()
    quality.update(enemy_damage_ready=False, enemy_damage_unknown=False,
                   damage_settlement_ready=False, damage_settlement_unknown=False)
    source = Source(collector, NATIVE_PC_CONTRACT, quality)
    capture = LiveEventCapture(collector, source); capture.sample()
    source.quality = declared_quality()
    collector.frame, collector.game_time, collector.damage = 30, 1, 20
    source.append(first_window()[1:3])
    with pytest.raises(StreamGap, match='capability changed'): capture.sample()
    assert source.ack_calls == [0] and len(source.records) == 2
    assert capture.adapter.event_stream.sequence == 0
    assert capture.adapter.event_stream.received == {} and capture.closed
