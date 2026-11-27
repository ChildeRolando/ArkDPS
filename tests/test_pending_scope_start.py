"""Positive-time empty-scope anchor never discards the first retained hit."""
from copy import deepcopy
from dataclasses import asdict
import pytest
from battle_lifecycle import BattleSessions
from capture_battles import observation_from_json
from event_probe import StreamGap
from test_pc_serial_clock_reset_end import CollectorClockBattle
from test_autochess_scope_end import OwnedScopeEnd


def pending_case(monkeypatch):
    import test_autochess_scope_end
    from event_probe import reset_probe_cursor
    class SourceWithLifetime(CollectorClockBattle):
        def __init__(self):
            super().__init__()
            reset_probe_cursor(self.observer)
    monkeypatch.setattr(test_autochess_scope_end, 'CrossingPcBattle', SourceWithLifetime)
    case = OwnedScopeEnd('native_pc', phase=4, mode='all')
    source = case.fixture
    source.range_reads = []
    def read_range(after, through):
        source.range_reads.append((after, through))
        return dict(source.batch(through), events=deepcopy([event for event in source.queue
            if after < event['sequence'] <= through]))
    source.observer.read_round_start_range = read_range
    return case


def test_pending_empty_scope_retains_first_75_and_replays_at_actual_positive_anchor(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    case.advance(1.5, phase=5)
    pending = source.poll()
    assert pending.kind == 'preparing'
    assert source.commits == [0, 1]
    case.advance(2, amount=75)
    released = source.poll()
    assert source.range_reads == [(1, 2)]
    assert released.events['pending_scope_start']['anchor']['sample']['game_time'] == 1.5
    assert released.events['pending_scope_start']['anchor']['roster_frame'] == 45
    assert source.runtime.sessions.capture.baseline_sequence == 1
    assert source.runtime.sessions.multiplayer.result()['coverage_start'] == 1.5
    player = source.runtime.sessions.multiplayer.result()['players'][2]
    assert player['observed_damage'] == 75 and player['damage'] is None
    assert source.commits == [0, 1, 2] and source.queue == []
    assert source.runtime.sessions.adapter.event_stream.sequence == 2
    replay = BattleSessions(released.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == source.runtime.sessions.multiplayer.result()


def start_pending(case):
    case.advance(1.5, phase=5)
    assert case.fixture.poll().kind == 'preparing'
    assert case.fixture.commits == [0, 1]


@pytest.mark.parametrize('empty_exit', [False, True], ids=['first_hit', 'empty_exit'])
def test_cross_frame_pending_anchor_keeps_its_opening_mark_on_bootstrap(monkeypatch, empty_exit):
    case = pending_case(monkeypatch)
    source = case.fixture
    case.advance(1.5, phase=5)
    with monkeypatch.context() as sampling:
        mark = source.observer.mark

        def opening_mark():
            before = mark()

            def closing_mark():
                case.advance(1.625)
                sampling.setattr(source.observer, 'mark', mark)
                return mark()

            sampling.setattr(source.observer, 'mark', closing_mark)
            return before

        sampling.setattr(source.observer, 'mark', opening_mark)
        assert source.poll().kind == 'preparing'
    anchor = source.runtime.feed._pending_scope['anchor']
    assert anchor['before']['game_time'] == anchor['sample']['autochess_snapshot_anchor']['game_time'] == 1.5
    assert anchor['after']['game_time'] == anchor['sample']['game_time'] == 1.625
    assert source.commits == [0, 1]

    if empty_exit:
        case.advance(2, phase=7)
        ending = source.poll()
        assert 'pending_scope_exit' in ending.events
        archive = source.runtime.sessions.pop_archives()[-1]
        assert 'outside the event window' not in (archive['event_error'] or '')
        case.advance(2.5, phase=4, next_round=True)
        source.poll()
    case.advance(3, amount=75)
    first = source.poll()
    case.advance(3.5, amount=25)
    second = source.poll()
    assert source.runtime.sessions.event_status == 'recording'
    assert source.runtime.feed._pending_scope is None
    assert source.commits[-2:] == [2, 3] and source.queue == []
    first_hit, = first.events['drain']['events']
    second_hit, = second.events['drain']['events']
    assert [(hit['sequence'], hit['amount']) for hit in (first_hit, second_hit)] == [(2, 75), (3, 25)]
    multiplayer = source.runtime.sessions.multiplayer.result()
    assert multiplayer['players'][2]['observed_damage'] == 100
    replay = BattleSessions(second.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == multiplayer


def sample_empty_window_with_callback(case, monkeypatch):
    source = case.fixture
    case.advance(1.75)
    mark = source.observer.mark
    with monkeypatch.context() as sampling:
        def opening_mark():
            before = mark()
            case.extra_epoch += 1
            sampling.setattr(source.observer, 'mark', mark)
            return before

        sampling.setattr(source.observer, 'mark', opening_mark)
        assert source.poll().kind == 'preparing'
    assert source.range_reads == [(1, 1)] and source.commits == [0, 1]


def test_pending_ack_applied_but_reply_lost_never_reduces_first_hit_twice(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    commit = source.observer.commit_events
    attempts = []
    def uncertain(sequence):
        attempts.append(sequence)
        commit(sequence)
        return False if len(attempts) == 1 else None
    source.observer.commit_events = uncertain
    case.advance(2, amount=75)
    source.poll()
    assert source.runtime._pending_event_ack is not None
    case.advance(2.5)
    source.poll()
    assert attempts == [2, 2] and source.runtime._pending_event_ack is None
    actor, = source.runtime.sessions.multiplayer.result()['players'][2]['actor_ledgers']
    assert actor['ledger']['observed_damage'] == 75 and actor['ledger']['observed_hits'] == 1
    assert actor['ledger']['skill_initial_unknown_hits'] == 1
    player = source.runtime.sessions.multiplayer.result()['players'][2]
    assert player['coverage_complete'] is False
    assert player['skill_initialization']['reason_codes'] == ['baseline_not_zero_tick']


@pytest.mark.parametrize('fault', ['zero', 'consumer_seed', 'raw_guard', 'accounts', 'anchor_callback', 'missing_page',
    'page_generation', 'page_birth', 'sequence_gap', 'before_anchor', 'foreign_scope', 'merged_drain'])
def test_pending_transaction_raw_tamper_fails_before_new_receiver_or_ack(monkeypatch, fault):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    case.advance(2, amount=75)
    observation = source.runtime._acquire(source.collector, source.observer)
    raw = asdict(observation)
    tx = raw['events']['pending_scope_start']
    if fault == 'zero': tx['anchor']['sample']['game_time'] = 0
    elif fault == 'consumer_seed': tx['anchor']['sample']['autochess_lifecycle_baseline_ready'] = True
    elif fault == 'raw_guard': tx['anchor']['sample']['native_window_guard']['before']['native_identity']['stats'] = '0x60000'
    elif fault == 'accounts': tx['anchor']['after']['native_autochess_context']['players'][0]['uid'] = 'other'
    elif fault == 'anchor_callback': tx['anchor']['before']['epoch'] -= 1
    elif fault == 'missing_page': tx['pages'] = []
    elif fault == 'page_generation': tx['pages'][0]['batch']['native_generation'] += 1
    elif fault == 'page_birth': tx['pages'][0]['batch']['native_process_created_filetime'] = 999
    elif fault == 'sequence_gap': tx['pages'][0]['batch']['events'][0]['sequence'] += 1
    elif fault == 'before_anchor': tx['pages'][0]['batch']['events'][0].update(frame=30, game_time=1)
    elif fault == 'foreign_scope': tx['pages'][0]['batch']['events'][0]['native_autochess_scope']['phase'] = 4
    elif fault == 'merged_drain': raw['events']['drain']['events'] = []
    with pytest.raises(StreamGap): source.runtime.sessions.consume(observation_from_json(raw))
    assert source.runtime.sessions.generation == 1
    assert source.commits == [0, 1] and len(source.queue) == 1


def test_replacement_observer_retires_old_anchor_without_replaying_its_prefix(monkeypatch):
    from types import SimpleNamespace
    from event_probe import reset_probe_cursor
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    original = source.observer
    source.observer = SimpleNamespace(**vars(original))
    reset_probe_cursor(source.observer)
    def retired_ack(_sequence):
        raise AssertionError('retired observer must never receive an ACK')
    original.commit_events = retired_ack
    case.advance(2, amount=75)
    observation = source.poll()
    assert observation.kind == 'sample'
    assert source.runtime.feed._pending_scope is None
    assert source.range_reads == []  # No retained old-owner range is read on the new source.
    assert 'pending_scope_start' not in observation.events
    assert any(record.kind == 'gap' and record.reason == 'pending_scope_observer_retired'
               for record in case.records)
    # New source establishes a real positive-time baseline, never the old anchor.
    assert source.runtime.sessions.adapter.event_stream.baseline_frame == 60
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] is None
    replay = BattleSessions(observation.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == source.runtime.sessions.multiplayer.result()


def test_temporary_missing_observer_preserves_pending_prefix_until_owner_returns(monkeypatch):
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    pending = source.runtime.feed._pending_scope
    case.advance(2, amount=75)
    observation = source.runtime.poll(source.collector, None)
    assert observation.kind == 'scope_unavailable'
    assert source.runtime.feed._pending_scope is pending
    assert source.commits == [0, 1] and source.range_reads == []
    source.poll()
    assert source.range_reads == [(1, 2)]
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75


def test_explicit_retirement_drops_only_its_owned_pending_anchor(monkeypatch):
    from types import SimpleNamespace
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    pending = source.runtime.feed._pending_scope
    source.runtime.observer_retired(SimpleNamespace())
    assert source.runtime.feed._pending_scope is pending
    source.runtime.observer_retired(source.observer)
    assert source.runtime.feed._pending_scope is None
    assert source.commits == [0, 1] and source.range_reads == []
    assert source.runtime._accepted_event_observer is None


def test_real_cursor_reset_has_bounded_failure_context_and_never_retires_owner(monkeypatch):
    from event_probe import reset_probe_cursor
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    pending = source.runtime.feed._pending_scope
    reset_probe_cursor(source.observer)
    with pytest.raises(StreamGap) as failure:
        source.poll()
    assert failure.value.cursor_failure == dict(version=1, reason='cursor_lifetime_changed',
        phase='accepted_before_acquisition', owner_matches=True, observer_present=True,
        expected_present=True, actual_present=True)
    assert source.runtime.feed._pending_scope is pending
    assert source.commits == [0, 1]


def test_prefix_read_retry_retains_anchor_and_queue_then_releases_first_hit(monkeypatch):
    from event_capture import CaptureRetry
    from event_probe import ObserverReadRetry
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    read = source.observer.read_round_start_range
    source.observer.read_round_start_range = lambda *_: (_ for _ in ()).throw(ObserverReadRetry('owned timeout'))
    case.advance(2, amount=75)
    with pytest.raises(CaptureRetry): source.poll()
    assert source.commits == [0, 1] and source.runtime.feed._pending_scope is not None
    source.observer.read_round_start_range = read
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert source.commits == [0, 1, 2]


def test_bounded_pages_preserve_every_first_scope_hit_without_baseline_read(monkeypatch):
    import event_probe
    monkeypatch.setattr(event_probe, '_PENDING_SCOPE_PAGE_SIZE', 2)
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    for amount in (10, 20, 30): case.advance(2, amount=amount)
    observation = source.poll()
    assert source.range_reads == [(1, 3), (3, 4)]
    assert [record['amount'] for record in observation.events['drain']['events']] == [10, 20, 30]
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 60
    assert source.commits == [0, 1, 4]


@pytest.mark.parametrize('callback', [False, True], ids=['quiet', 'callback_without_output'])
def test_no_reset_zero_output_scope_is_materialized_at_real_anchor_and_latest_empty_point(monkeypatch, callback):
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    if callback:
        sample_empty_window_with_callback(case, monkeypatch)
    else:
        case.advance(1.75)
        assert source.poll().kind == 'preparing'
        assert source.range_reads == [(1, 1)] and source.commits == [0, 1]
    case.advance(2, phase=7)
    ending = source.poll()
    assert ending.events['pending_scope_exit']['version'] == 1
    assert ending.sample['game_time'] == 1.75 and ending.sample['frame'] == 52
    assert ending.events['before']['sequence'] == ending.events['after']['sequence'] == 1
    assert ending.events['after']['epoch'] - ending.events['before']['epoch'] == int(callback)
    archives = source.runtime.sessions.pop_archives()
    assert len(archives) == 2  # Defense plus a genuine assistance scope with no output.
    empty = archives[-1]
    assert empty['last_sample']['autochess_context']['phase'] == 5
    mp = empty['last_sample']['multiplayer']
    assert mp['coverage_start'] == 1.5 and mp['game_time'] == 1.75
    assert all(player['damage'] is None and player['coverage_complete'] is False for player in mp['players'])
    assert empty['event_status'] != 'finished'  # Late origin and terminal body remain unknown.
    from battle_history import BattleRecord
    restored = BattleRecord.from_archive(empty)
    assert restored.last_sample['multiplayer']['coverage_start'] == 1.5
    assert source.runtime.feed._pending_scope is None and source.commits == [0, 1]
    assert source.poll().kind == 'preparing'
    replay = BattleSessions(ending.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.pop_archives() == archives

    observer = source.observer
    case.advance(2.5, phase=4, next_round=True)
    assert source.poll().kind == 'sample'
    case.advance(3, amount=75)
    source.poll()
    assert source.observer is observer and source.commits == [0, 1, 1, 2]
    player = source.runtime.sessions.multiplayer.result()['players'][2]
    assert player['observed_damage'] == 75


def test_real_serial_and_generation_reset_can_supersede_only_the_empty_prestart_fragment(monkeypatch):
    from test_pc_serial_clock_reset_end import reset_native_clock
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    reset_native_clock(case)
    reset = source.poll()
    assert reset.sample['frame'] == 0 and reset.sample['game_time'] == 0
    assert reset.events['pending_scope_superseded']['mode'] == 'reset'
    assert source.runtime.feed._pending_scope is None
    archives = source.runtime.sessions.pop_archives()
    assert len(archives) == 1 and archives[0]['last_sample']['autochess_context']['phase'] == 4
    assert source.runtime.sessions.multiplayer.result()['coverage_start'] == 0
    case.advance(.5, amount=75)
    source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert source.commits == [0, 1, 1, 2]
    replay = BattleSessions(reset.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.pop_archives() == archives
    assert replay.multiplayer.result() == source.runtime.sessions.multiplayer.result()


@pytest.mark.parametrize('fault', ['serial_skip', 'generation_skip', 'callback_entered', 'uncertainty', 'roots'])
def test_empty_reset_supersession_requires_exact_independent_and_source_fences(monkeypatch, fault):
    from test_pc_serial_clock_reset_end import reset_native_clock
    from event_capture import CaptureRetry
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    if fault == 'callback_entered': case.extra_epoch += 1
    if fault == 'uncertainty': source.quality_changes['damage_unknown'] = True
    reset_native_clock(case, serial_step=2 if fault == 'serial_skip' else 1,
                       generation_step=2 if fault == 'generation_skip' else 1)
    if fault == 'serial_skip': source.collector.process._serial += 1
    if fault == 'roots': source.identity_roots['stats'] = '0x60000'
    with pytest.raises((StreamGap, CaptureRetry)): source.poll()
    assert source.commits == [0, 1]
    assert source.runtime.feed._pending_scope is not None


def test_reset_cannot_supersede_an_empty_window_that_spanned_a_callback(monkeypatch):
    from test_pc_serial_clock_reset_end import reset_native_clock
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    sample_empty_window_with_callback(case, monkeypatch)
    reset_native_clock(case)
    with pytest.raises(StreamGap, match='strict source-generation fence'):
        source.poll()
    assert source.commits == [0, 1]
    assert source.runtime.feed._pending_scope is not None


def test_zero_output_exit_raw_tamper_cannot_manufacture_an_archive(monkeypatch):
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    case.advance(2, phase=7)
    ending = source.runtime._acquire(source.collector, source.observer)
    raw = asdict(ending)
    raw['events']['pending_scope_exit']['successor']['sample']['native_window_guard']['before']['stage_id'] = 'forged-stage'
    with pytest.raises((StreamGap, ValueError)): source.runtime.sessions.consume(observation_from_json(raw))
    assert source.runtime.sessions.generation == 1
    assert len(source.runtime.sessions.pop_archives()) == 1
    assert source.commits == [0, 1]


@pytest.mark.parametrize('ending', ['battle_absent', 'finished', 'process_changed'])
@pytest.mark.parametrize('callback', [False, True], ids=['initial_only', 'callback_without_output'])
def test_pending_zero_scope_survives_an_actual_departure_without_a_reset(monkeypatch, ending, callback):
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    if callback:
        sample_empty_window_with_callback(case, monkeypatch)
    if ending == 'process_changed':
        source.runtime.process_changed()
    else:
        life = deepcopy(source.collector.read_lifecycle())
        if ending == 'battle_absent': life['battle'] = None
        else: life['state'] = 3
        source.collector.read_lifecycle = lambda: deepcopy(life)
        observation = source.poll()
        assert observation.events['pending_scope_end']['reason'] == ending
    archives = source.runtime.sessions.pop_archives()
    assert len(archives) == 2
    empty = archives[-1]
    assert empty['last_sample']['game_time'] == (1.75 if callback else 1.5)
    assert empty['last_sample']['multiplayer']['coverage_start'] == 1.5
    assert empty['event_status'] != 'finished'
    assert source.runtime.feed._pending_scope is None and source.commits == [0, 1]
    replay = BattleSessions(case.records[0].feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.pop_archives() == archives


def test_unconsumed_prefix_cannot_replace_last_empty_scope_endpoint(monkeypatch):
    case = pending_case(monkeypatch)
    source = case.fixture
    start_pending(case)
    sample_empty_window_with_callback(case, monkeypatch)
    case.advance(2, amount=75)
    unconsumed = source.runtime._acquire(source.collector, source.observer)
    assert [record['amount'] for record in unconsumed.events['drain']['events']] == [75]

    life = deepcopy(source.collector.read_lifecycle())
    life['battle'] = None
    source.collector.read_lifecycle = lambda: deepcopy(life)
    ending = source.poll()
    assert ending.events['pending_scope_end']['reason'] == 'battle_absent'
    assert ending.sample['game_time'] == 1.75
    assert source.commits == [0, 1] and source.queue[0]['amount'] == 75
    archives = source.runtime.sessions.pop_archives()
    assert len(archives) == 2 and archives[-1]['event_status'] != 'finished'

    replay = BattleSessions(ending.feed)
    for record in case.records[:-1]:
        assert replay.consume(observation_from_json(asdict(record)))
    forged = asdict(ending)
    forged['sample']['rows'] = [{'damage': 75}]
    with pytest.raises(StreamGap, match='invalid positive-time empty scope witness'):
        replay.consume(observation_from_json(forged))
    assert replay.consume(observation_from_json(asdict(ending)))
    assert replay.pop_archives() == archives


def test_pending_contract_cannot_be_retagged_as_an_ordinary_current_generation(monkeypatch):
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    case.advance(2, amount=75)
    accepted = source.poll()
    raw = asdict(accepted)
    raw['sequence'] += 1
    with pytest.raises(StreamGap, match='established generation'):
        source.runtime.sessions.consume(observation_from_json(raw))
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75


def test_pending_prefix_byte_budget_rejects_before_ack_or_receiver_initialization(monkeypatch):
    import event_probe
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    monkeypatch.setattr(event_probe, '_PENDING_SCOPE_BYTES', 1)
    case.advance(2, amount=75)
    with pytest.raises(StreamGap, match='byte budget'): source.poll()
    assert source.commits == [0, 1] and source.queue[0]['amount'] == 75
    assert source.runtime.sessions.generation == 1


@pytest.mark.parametrize('point', ['before_range', 'during_range', 'before_consume', 'before_ack'])
def test_same_observer_new_stream_lifetime_cannot_reuse_or_ack_pending_anchor(monkeypatch, point):
    from event_probe import reset_probe_cursor
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case)
    case.advance(2, amount=75)
    if point == 'before_range':
        reset_probe_cursor(source.observer)
        with pytest.raises(StreamGap, match='lifetime'): source.poll()
    elif point == 'during_range':
        original = source.observer.read_round_start_range
        def restart(after, through):
            result = original(after, through)
            reset_probe_cursor(source.observer)
            return result
        source.observer.read_round_start_range = restart
        with pytest.raises(StreamGap, match='lifetime'): source.poll()
    else:
        observation = source.runtime._acquire(source.collector, source.observer)
        if point == 'before_consume':
            reset_probe_cursor(source.observer)
            count = len(case.records)
            with pytest.raises(StreamGap, match='lifetime'): source.runtime._consume(observation)
            assert len(case.records) == count  # Live-rejected prefixes are not replayable observations.
        else:
            assert source.runtime._consume(observation)
            reset_probe_cursor(source.observer)
            source.runtime._acknowledge_observation(observation, source.observer, True)
            assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75
    assert source.commits == [0, 1] and source.queue[0]['amount'] == 75


@pytest.mark.parametrize('fault', ['whole_release', 'page_stale', 'page_future', 'page_bool'])
def test_sequence_growth_requires_strict_epoch_growth_and_valid_raw_page_epoch(monkeypatch, fault):
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case); case.advance(2, amount=75)
    observation = source.runtime._acquire(source.collector, source.observer)
    raw = asdict(observation)
    epoch = raw['events']['pending_scope_start']['anchor']['after']['epoch']
    if fault == 'whole_release':
        for key in ('before', 'after', 'drain'): raw['events'][key]['epoch'] = epoch
        raw['events']['pending_scope_start']['pages'][0]['batch']['epoch'] = epoch
    else:
        batch = raw['events']['pending_scope_start']['pages'][0]['batch']
        batch['epoch'] = epoch if fault == 'page_stale' else epoch+10 if fault == 'page_future' else True
    with pytest.raises(StreamGap): source.runtime.sessions.consume(observation_from_json(raw))
    assert source.commits == [0, 1] and source.runtime.sessions.generation == 1


def test_fresh_positive_time_attachment_keeps_original_immediate_late_baseline(monkeypatch):
    from battle_lifecycle import BattleRuntime
    from event_probe import reset_probe_cursor
    case = pending_case(monkeypatch); source = case.fixture
    source.runtime = BattleRuntime(); source.sequence = 0; source.queue = []; source.commits = []
    reset_probe_cursor(source.observer)
    case.advance(10)
    observation = source.poll()
    assert observation.kind == 'sample' and source.runtime.feed._pending_scope is None
    assert observation.sample['game_time'] == 10 and source.commits == [0]
    assert source.runtime.sessions.adapter.event_stream.baseline_frame == 300
    assert source.runtime.sessions.multiplayer.result()['coverage_start'] == 10


def test_changed_epoch_without_old_prefix_seal_uses_immediate_late_baseline(monkeypatch):
    case = pending_case(monkeypatch); source = case.fixture
    case.extra_epoch += 1
    case.advance(1.5, phase=5)
    observation = source.poll()
    assert observation.kind == 'sample' and source.runtime.feed._pending_scope is None
    assert source.runtime.sessions.multiplayer.result()['coverage_start'] == 1.5
    case.advance(2, amount=75); source.poll()
    assert source.runtime.sessions.multiplayer.result()['players'][2]['observed_damage'] == 75


def test_ack_retry_cannot_commit_a_previous_source_prefix_after_same_object_restart(monkeypatch):
    from event_probe import reset_probe_cursor
    case = pending_case(monkeypatch); source = case.fixture
    start_pending(case); case.advance(2, amount=75)
    source.observer.commit_events = lambda sequence: False
    source.poll()
    assert source.runtime._pending_event_ack is not None
    reset_probe_cursor(source.observer)
    case.advance(2.5)
    with pytest.raises(StreamGap, match='lifetime'): source.poll()
    assert source.commits == [0, 1]
    assert source.queue[0]['amount'] == 75


def test_one_raw_scope_parser_preserves_zero_frame_fallback_without_loosening_pending():
    from event_probe import autochess_raw_read_scope
    from event_capture import _round_start_read_scope
    from test_round_start_chunk_reads import binding
    raw = binding(30)
    original = deepcopy(raw)
    context, identity = autochess_raw_read_scope(raw, require_native_frame=True)
    assert identity == _round_start_read_scope(raw)
    assert raw == original and context['frame'] == 30
    fallback = deepcopy(raw)
    fallback['frame'] = fallback.pop('native_frame')
    assert _round_start_read_scope(fallback) == identity
    with pytest.raises(StreamGap, match='raw native frame'):
        autochess_raw_read_scope(fallback, require_native_frame=True)
    for mutation in ('clock', 'account'):
        invalid = deepcopy(raw)
        if mutation == 'clock': invalid['native_fixed_time'] = '１'
        else: invalid['native_autochess_context']['players'][0]['uid'] = invalid['native_autochess_context']['players'][1]['uid']
        with pytest.raises(StreamGap): _round_start_read_scope(invalid)
        with pytest.raises(StreamGap): autochess_raw_read_scope(invalid, require_native_frame=True)
