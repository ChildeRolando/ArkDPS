"""Construct the rc.48 pending transaction for historical contract regressions.

The live producer no longer defers an empty combat baseline. Only old wire
records still use that protocol; this fixture recreates its former admission
rule without adding a product switch or borrowing a new scope's evidence.
"""
from battle_lifecycle import BattleFeed, _verified_autochess_successor_cursor
from event_probe import pending_scope_anchor, StreamGap, BarrierBusy, annotate_barrier_busy


def enable_legacy_pending_creation(monkeypatch):
    read = BattleFeed._pending_scope_drain

    def legacy(self, observer, evidence, sample, roster, **kwargs):
        result, deferred = read(self, observer, evidence, sample, roster, **kwargs)
        if deferred or self._pending_scope is not None:
            return result, deferred
        published = kwargs['published_capture']
        stream = published.adapter.event_stream if published is not None else None
        if (kwargs['generation'] != kwargs['published_generation'] + 1
                or observer is None or not callable(getattr(observer, 'read_round_start_range', None))
                or sample.get('native_counter_scope') != 'events_only' or sample.get('state') != 2
                or type(sample.get('frame')) is not int or sample['frame'] <= 0
                or type(sample.get('game_time')) not in (int, float) or sample['game_time'] <= 0
                or roster.entries or not isinstance(result, dict) or result.get('error')
                or stream is None or observer is not kwargs['published_observer']
                or published.closed or published.pending or stream.baseline_frame != 0
                or published.adapter.statistics.event_coverage_start != 0
                or not _verified_autochess_successor_cursor(published.last_sample,
                    kwargs['published_event_mark'],
                    dict(sample, native_session=sample.get('native_session', sample['session'])),
                    evidence, stream, published.adapter.last_game_time, published.contract)
                or result.get('sequence') != stream.sequence
                or not isinstance(result.get('drain'), dict) or result['drain'].get('events') != []
                or 'autochess_round_start' in result['drain']):
            return result, False
        try:
            anchor = pending_scope_anchor(sample, result, roster.frame)
        except BarrierBusy as error:
            annotate_barrier_busy(error, 'pending_anchor', sample=sample,
                                 before=result.get('before'), after=result.get('after'))
            raise
        except StreamGap:
            return result, False
        self._pending_scope = dict(observer=observer, cursor_lifetime=kwargs['cursor_lifetime'],
            generation=kwargs['generation'], anchor=anchor, latest=anchor)
        return result, True

    monkeypatch.setattr(BattleFeed, '_pending_scope_drain', legacy)
