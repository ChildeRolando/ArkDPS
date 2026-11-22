"""A failed observer is retried at a confirmed new battle, never mid-battle."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from event_capture import CaptureRetry
from event_observer import FridaEventObserver, ObserverReadRetry
from collector import BattleChanged, ProcessChanged
from test_collection_session import LiveObserver
from test_event_capture import event
from test_lifecycle_events import connected, advance, native_connection


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


def native_ack_timeout(*, terminal=False):
    # This fixture serializes identity changes between poll calls. Its commit
    # callback belongs to that fixture stream; a real producer must provide the
    # same binding or keep sequences unique for the observer's entire lifetime.
    runtime, collector, observer, snapshot, state, events, calls, commits = native_connection()
    attempts = []
    lost = False

    def acknowledge(sequence):
        nonlocal lost
        attempts.append(sequence)
        if sequence == 1 and not lost:
            lost = True
            raise ObserverReadRetry('ack response timed out')
        commits.append(sequence)

    observer.commit_events = acknowledge
    runtime.poll(collector, observer)
    snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
    state.battle_state = 3 if terminal else 2
    events.queue = [event(1, frame=2, game_time=1., amount=20)]
    accepted = runtime.poll(collector, observer)  # An accepted sample must not raise.
    assert runtime.sessions.adapter.result()['rows']['char_a']['observed_damage'] == 20
    assert runtime.sessions.adapter.result()['rows']['char_a']['event_coverage_complete'] is True
    assert attempts == [0, 1] and commits == [0]
    return runtime, collector, observer, snapshot, state, events, attempts, commits, accepted


@pytest.mark.parametrize('new_events', [False, True])
def test_native_ack_timeout_retries_accepted_prefix_without_gap_or_double_credit(new_events):
    runtime, collector, observer, snapshot, _, events, attempts, commits, _ = native_ack_timeout()
    if new_events:
        snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2., 40.
        events.queue.append(event(2, frame=3, game_time=2., amount=20))
    runtime.poll(collector, observer)
    row = runtime.sessions.adapter.result()['rows']['char_a']
    expected_sequence = 2 if new_events else 1
    assert row['damage'] == row['observed_damage'] == (40 if new_events else 20)
    assert row['event_coverage_complete'] is True and runtime.sessions.event_status == 'recording'
    assert runtime.sessions.adapter.event_stream.sequence == expected_sequence
    assert attempts == [0, 1, expected_sequence]
    assert commits == [0, expected_sequence]
    assert runtime._pending_event_ack is None


@pytest.mark.parametrize('changed_field', [None, 'amount', 'source_character_uid'])
def test_native_ack_replay_uses_the_original_record_after_roster_attribution_changes(changed_field):
    runtime, collector, observer, snapshot, _, events, _, commits = native_connection()
    original_roster = snapshot.read_roster
    player_side = None
    snapshot.read_roster = lambda: replace(original_roster(), local_player_side=player_side)
    events.claims['batch'] = dict(source_attribution_version=1, partial_damage_version=7,
                                  damage_settlement_ready=True)

    def acknowledge(sequence):
        if sequence == 1:
            raise ObserverReadRetry('ack response timed out')
        commits.append(sequence)

    def damage_record(sequence, frame, game_time):
        return dict(event(sequence, frame, game_time, 20), kind='damage_settlement',
                    source_character_uid=1, target='enemy_a', target_uid='77',
                    damage_type=1, shield_absorbed=0, settlement_complete=True,
                    nominal_amount=20, killed=False)

    observer.commit_events = acknowledge
    runtime.poll(collector, observer)
    snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
    events.queue = [damage_record(1, 2, 1.)]
    runtime.poll(collector, observer)
    first = runtime.display_sample()['statistics']['rows']['char_a']['settled_damage']
    assert first['observed_hits'] == first['unverified_hits'] == 1

    player_side = 0
    snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2., 40.
    if changed_field is not None:
        events.queue[0][changed_field] += 1
    events.queue.append(damage_record(2, 3, 2.))
    runtime.poll(collector, observer)

    result = runtime.display_sample()
    row = result['statistics']['rows']['char_a']
    assert row['damage'] == 40
    damage = row['settled_damage']
    assert damage['unverified_hits'] == 1
    if changed_field is None:
        assert result['event_status'] == 'recording'
        assert result['event_watermark']['sequence'] == 2
        assert damage['observed_hits'] == 2
        assert damage['effective_damage'] == 20
        assert commits == [0, 2]
    else:
        assert result['event_status'] == 'gap'
        assert 'conflicts with the accepted record' in result['event_error']
        assert result['event_watermark']['sequence'] == 1
        assert damage['observed_hits'] == 1
        assert damage['effective_damage'] is None
        assert commits == [0]


def test_native_ack_replay_can_include_records_covered_by_the_initial_baseline():
    runtime, collector, observer, snapshot, _, events, _, commits = native_connection()
    snapshot.damage = 20
    events.queue = [event(1)]

    def acknowledge(sequence):
        if sequence == 1:
            raise ObserverReadRetry('baseline ack response timed out')
        commits.append(sequence)

    observer.commit_events = acknowledge
    runtime.poll(collector, observer)
    snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 40.
    events.queue.append(event(2, 2, 1., 20))
    runtime.poll(collector, observer)

    result = runtime.display_sample()
    row = result['statistics']['rows']['char_a']
    assert row['damage'] == 40 and row['observed_damage'] == 20
    assert result['event_status'] == 'recording'
    assert result['event_watermark']['sequence'] == 2
    assert commits == [2]


@pytest.mark.parametrize('guard_retry', [False, True])
def test_native_terminal_ack_retry_preserves_once_archive_and_finished_coverage(guard_retry):
    runtime, collector, observer, _, state, _, attempts, commits, accepted = native_ack_timeout(terminal=True)
    assert runtime.sessions.event_status == 'finished'
    if guard_retry:
        original_mark = observer.mark
        mark_calls = 0

        def mark():
            nonlocal mark_calls
            mark_calls += 1
            if mark_calls == 2:  # next acquisition mark succeeds; retained-ack guard is busy
                raise ObserverReadRetry('ack identity mark response timed out')
            return original_mark()

        observer.mark = mark
    state.active = False
    runtime.poll(collector, observer)
    archive, = runtime.sessions.pop_archives()
    assert archive['battle_id'] == accepted.sample['session']
    assert archive['event_status'] == 'finished'
    row = archive['statistics']['rows']['char_a']
    assert row['damage'] == row['observed_damage'] == 20 and row['event_coverage_complete'] is True
    if guard_retry:
        assert attempts == [0, 1] and runtime._pending_event_ack is not None
    runtime.poll(collector, observer)
    assert attempts == [0, 1, 1] and commits == [0, 1]
    assert not runtime.sessions.pop_archives()
    assert runtime._pending_event_ack is None


def test_native_pending_ack_does_not_cross_a_new_session_with_reset_sequence():
    runtime, collector, observer, snapshot, _, events, attempts, commits, _ = native_ack_timeout()
    snapshot.session = 'next-native-battle'
    snapshot.frame, snapshot.game_time, snapshot.damage = 1, 0., 0.
    events.queue = []
    mark, read = observer.mark, observer.read_through
    observer.mark = lambda: {**mark(), 'native_session': snapshot.session}
    observer.read_through = lambda sequence: {**read(sequence), 'native_session': snapshot.session}
    successor = runtime.poll(collector, observer)
    assert successor.generation == 2
    assert attempts == [0, 1, 0] and commits == [0, 0]
    assert runtime._pending_event_ack is None
    row = runtime.sessions.adapter.result()['rows']['char_a']
    assert row['damage'] == row['observed_damage'] == 0
    old, = runtime.sessions.pop_archives()
    assert old['statistics']['rows']['char_a']['observed_damage'] == 20


def test_native_pending_ack_is_not_sent_to_a_reconnected_observer():
    runtime, collector, observer, _, state, _, attempts, _, _ = native_ack_timeout(terminal=True)
    replacement_commits = []
    replacement = SimpleNamespace(**vars(observer))
    replacement.commit_events = replacement_commits.append
    state.active = False
    runtime.poll(collector, replacement)
    assert attempts == [0, 1] and replacement_commits == []
    assert runtime._pending_event_ack is None
    archive, = runtime.sessions.pop_archives()
    assert archive['statistics']['rows']['char_a']['observed_damage'] == 20


def test_terminal_ack_confirmation_reaches_runtime_from_the_production_observer(monkeypatch):
    runtime, collector, observer, snapshot, state, events, _, _ = native_connection()
    transport = FridaEventObserver(collector, 'unused')
    attempts = []

    def request(operation, *, sequence):
        assert operation == 'ack'
        attempts.append(sequence)
        if len(attempts) == 1:
            raise ObserverReadRetry('producer may have released the prefix')
        return {'acknowledged': sequence}

    monkeypatch.setattr(transport, '_request', request)
    observer.commit_events = transport.commit_events
    runtime.poll(collector, observer)
    snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1., 20.
    state.battle_state = 3
    events.queue = [event(1, frame=2, game_time=1., amount=20)]
    runtime.poll(collector, observer)

    assert transport._committed_sequence == 1 and transport._acknowledged_sequence == 0
    assert runtime._pending_event_ack is not None
    assert runtime.sessions.event_status == 'finished'
    state.active = False
    runtime.poll(collector, observer)
    archive, = runtime.sessions.pop_archives()
    assert archive['statistics']['rows']['char_a']['observed_damage'] == 20
    assert runtime._pending_event_ack is None and transport._acknowledged_sequence == 1
    assert attempts == [1, 1]


@pytest.mark.parametrize('key', ['native_session', 'source_build'])
def test_native_pending_terminal_ack_requires_a_fresh_matching_producer_binding(key):
    runtime, collector, observer, _, state, _, attempts, _, _ = native_ack_timeout(terminal=True)
    mark = observer.mark
    observer.mark = lambda: {**mark(), key: 'replacement-identity'}
    state.active = False
    runtime.poll(collector, observer)
    assert attempts == [0, 1] and runtime._pending_event_ack is None
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished'
    assert archive['statistics']['rows']['char_a']['observed_damage'] == 20


@pytest.mark.parametrize('sequence', [0, True, None, '1'])
def test_native_pending_terminal_ack_rejects_noninteger_or_reset_producer_sequence(sequence):
    runtime, collector, observer, _, state, _, attempts, _, _ = native_ack_timeout(terminal=True)
    mark = observer.mark
    observer.mark = lambda: {**mark(), 'sequence': sequence}
    state.active = False
    runtime.poll(collector, observer)
    assert attempts == [0, 1] and runtime._pending_event_ack is None
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished'
    assert archive['statistics']['rows']['char_a']['observed_damage'] == 20


def test_explicit_disconnect_clears_pending_ack_without_retrying_transport():
    runtime, _, _, _, _, _, attempts, _, _ = native_ack_timeout()
    runtime.disconnected('explicit_disconnect')
    assert runtime._pending_event_ack is None
    assert attempts == [0, 1]
    assert runtime.sessions.adapter.result()['rows']['char_a']['observed_damage'] == 20


@pytest.mark.parametrize('failure', [ProcessChanged, BattleChanged])
def test_process_change_clears_pending_ack_without_sending_old_watermark(failure):
    runtime, collector, observer, _, _, _, attempts, _, _ = native_ack_timeout()

    def changed():
        raise failure('process changed')

    collector.read_lifecycle = changed
    with pytest.raises(failure):
        runtime.poll(collector, observer)
    assert runtime._pending_event_ack is None and attempts == [0, 1]
    assert runtime.sessions.adapter.result()['rows']['char_a']['observed_damage'] == 20
