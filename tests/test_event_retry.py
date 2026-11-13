"""A failed observer is retried at a confirmed new battle, never mid-battle."""
import json

import pytest

from event_capture import CaptureRetry
from test_collection_session import LiveObserver
from test_event_capture import event
from test_lifecycle_events import connected, advance


def next_battle(session, collector, damage=3, *, initialize=True):
    if initialize:
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
    collector.state, collector.frame, collector.game_time, collector.damage = 2, 1, 0, damage


@pytest.mark.parametrize('initialize', [False, True])
def test_event_retry_after_transport_gap_preserves_old_prefix_and_new_watermark(initialize):
    session, collector, calls, records = connected()
    try:
        first = session.read()
        advance(session, collector)
        session.read()
        old = session.observer
        old.error = 'transport lost'
        collector.frame, collector.game_time, collector.damage = 3, 2, 150
        assert session.read()['event_status'] == 'gap'
        collector.frame, collector.game_time, collector.damage = 4, 3, 160
        session.read()
        assert calls.count('attach') == 1
        next_battle(session, collector, initialize=initialize)
        current = session.read()
        assert current['event_status'] == 'recording'
        assert session.observer is not old and calls.count('attach') == 2
        assert current['battle_id'] != first['battle_id']
        assert current['event_watermark'] == dict(sequence=0, frame=1, game_time=0)
        assert current['statistics']['rows']['char_a']['damage'] == 3
        assert current['statistics']['rows']['char_a']['observed_damage'] == 0
        archive, = [data for kind, data in records if kind == 'archive_ready']
        assert archive['battle_id'] == first['battle_id']
        assert archive['statistics']['rows']['char_a']['damage'] == 160
        assert archive['statistics']['rows']['char_a']['observed_damage'] == 20
        assert archive['statistics']['rows']['char_a']['event_coverage_end'] == 1
        collector.frame, collector.game_time, collector.damage = 2, 1, 23
        session.observer.sequence = session.observer.epoch = 1
        session.observer.events = [event(1, frame=2, game_time=1)]
        assert session.read()['statistics']['rows']['char_a']['observed_damage'] == 20
    finally:
        session.close()
    assert calls.count('attach') == calls.count('detach') == 2


def test_event_retry_startup_failure_waits_until_the_following_battle():
    class Recovering(LiveObserver):
        attempts = 0

        def start(self):
            super().start()
            type(self).attempts += 1
            if self.attempts == 1:
                raise ConnectionError('endpoint not ready')

    session, collector, calls, _ = connected(observer=Recovering)
    try:
        assert session.read()['event_status'] == 'failed'
        assert calls.count('attach') == 1
        assert session.read()['event_status'] == 'failed'
        next_battle(session, collector)
        assert session.read()['event_status'] == 'recording'
        assert calls.count('attach') == 2
    finally:
        session.close()


def test_event_retry_failed_attempt_keeps_snapshots_and_does_not_loop_in_that_battle():
    class Flaky(LiveObserver):
        attempts = 0

        def start(self):
            super().start()
            type(self).attempts += 1
            if self.attempts == 2:
                raise ConnectionError('still unavailable')

    session, collector, calls, _ = connected(observer=Flaky)
    try:
        session.read()
        session.observer.error = 'transport lost'
        collector.frame, collector.game_time, collector.damage = 2, 1, 120
        session.read()
        next_battle(session, collector)
        failed = session.read()
        assert failed['event_status'] == 'failed'
        assert failed['statistics']['rows']['char_a']['damage'] == 3
        assert failed['statistics']['rows']['char_a']['shield_absorbed'] is None
        collector.frame, collector.game_time, collector.damage = 2, 1, 13
        assert session.read()['statistics']['rows']['char_a']['damage'] == 13
        assert calls.count('attach') == 2
        next_battle(session, collector, damage=5)
        assert session.read()['event_status'] == 'recording'
        assert calls.count('attach') == 3
    finally:
        session.close()


def test_event_retry_busy_baseline_archives_old_battle_once_and_reuses_the_attempt():
    class Busy(LiveObserver):
        attempts = 0

        def start(self):
            super().start()
            type(self).attempts += 1
            if self.attempts == 2:
                self.marks = [dict(retry=True)]

    session, collector, calls, records = connected(observer=Busy)
    try:
        first = session.read()
        session.observer.error = 'transport lost'
        collector.frame, collector.game_time, collector.damage = 2, 1, 120
        session.read()
        next_battle(session, collector, initialize=False)
        with pytest.raises(CaptureRetry):
            session.read()
        archives = [data for kind, data in records if kind == 'archive_ready']
        assert len(archives) == 1 and archives[0]['battle_id'] == first['battle_id']
        assert session.runtime.sessions.latest_sample['rows'][0]['damage'] == 3
        assert session.read()['event_status'] == 'recording'
        assert calls.count('attach') == 2
        assert len([1 for kind, _ in records if kind == 'archive_ready']) == 1
    finally:
        session.close()


@pytest.mark.parametrize('remote', [None, 'remote'])
def test_event_retry_does_not_reattach_disabled_or_healthy_observers(remote):
    session, collector, calls, _ = connected(remote=remote)
    try:
        session.read()
        next_battle(session, collector)
        sample = session.read()
        assert calls.count('attach') == int(remote is not None)
        assert sample['event_status'] == ('unavailable' if remote is None else 'recording')
    finally:
        session.close()


def test_previous_terminal_gap_does_not_reattach_a_healthy_observer():
    session, collector, calls, _ = connected()
    try:
        previous = session.read()
        observer = session.observer
        session.observer = None
        collector.state = 3
        assert session.read()['event_status'] == 'gap'
        session.observer = observer
        next_battle(session, collector)
        current = session.read()
        assert current['battle_id'] != previous['battle_id']
        assert current['event_status'] == 'recording'
        assert session.observer is observer
        assert calls.count('attach') == 1
    finally:
        session.close()


def test_event_retry_detects_loss_first_observed_at_the_next_battle():
    session, collector, calls, _ = connected()
    try:
        session.read()
        session.observer.error = 'transport disappeared between battles'
        next_battle(session, collector)
        assert session.read()['event_status'] == 'recording'
        assert calls.count('attach') == 2
    finally:
        session.close()


def test_event_retry_journal_preserves_replay_and_each_archive_once(tmp_path):
    from capture_battles import replay
    from capture_cli import json_value
    session, collector, _, records = connected()
    try:
        session.read()
        session.observer.error = 'transport lost'
        collector.frame, collector.game_time, collector.damage = 2, 1, 120
        session.read()
        next_battle(session, collector, initialize=False)
        session.read()
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
    finally:
        session.close()
    path = tmp_path / 'recovery.jsonl'
    path.write_text('\n'.join(json.dumps(dict(kind=kind, data=data), default=json_value)
                              for kind, data in records) + '\n', encoding='utf-8')
    assert replay(path)['archives'] == 2
