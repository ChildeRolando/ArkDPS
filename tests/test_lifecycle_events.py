"""Product entry: a single lifecycle ID owns snapshots, events and archives."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from battle_history import BattleHistory
from battle_lifecycle import BattleRuntime
from collection_session import CollectionSession
from enemy_telemetry import EnemyTelemetry
from event_capture import CaptureRetry
from event_observer import ObserverReadRetry
from test_collection_session import Collector, LiveObserver
from test_event_capture import Collector as SnapshotFixture, event


def native_connection():
    """Use an atomic collector and a serialized observer with no target access."""
    snapshot = SnapshotFixture()
    snapshot.damage = 0
    snapshot.session = '10:1234:opaque-battle'
    process = SimpleNamespace(pid=10, process_instance=(10, 1234))
    state = SimpleNamespace(active=True, battle_state=2, healing=0)
    events = SimpleNamespace(queue=[], claims={}, terminal={})
    calls = []
    source_build = 'win-x64:fixture-build'
    binding = dict(native_session=snapshot.session, source_build=source_build)
    commit = []
    captured = None

    def read_lifecycle():
        nonlocal captured
        calls.append('lifecycle')
        metadata = dict(platform='native_pc', source_build=source_build, evidence='fixture',
                        outcome='victory' if state.battle_state == 3 else None)
        battle = ('opaque-battle',) if state.active else None
        life = dict(battle=battle, state=state.battle_state, ready=True, transiting=False,
                    source='', target='', metadata=metadata)
        sample = dict(snapshot.read(), battle_identity=battle, state=state.battle_state)
        sample['rows'][0]['healing'] = state.healing
        captured = life, sample, snapshot.read_roster()
        return life

    def guarded_pair(life):
        calls.append('pair')
        assert captured[0] is life
        return captured[1:]

    def mark():
        calls.append('mark')
        latest = events.queue[-1]['sequence'] if events.queue else 0
        value = dict(binding, sequence=latest, epoch=latest, active=0,
                     frame=snapshot.frame, game_time=snapshot.game_time)
        value.update(events.claims.get(calls.count('mark'), {}))
        return value

    def read_through(sequence):
        calls.append('batch')
        return dict(binding, events=[e for e in events.queue if e['sequence'] <= sequence],
                    damage_unknown=False, engagement_unknown=False, skill_unknown=False,
                    shield_unknown=False, mitigation_ready=True, mitigation_unknown=False) | events.claims.get('batch', {})

    collector = SimpleNamespace(process=process, read_lifecycle=read_lifecycle,
                                read_guarded_pair=guarded_pair)
    observer = SimpleNamespace(mark=mark, read_through=read_through, commit_events=commit.append,
                               read_terminal_mark=lambda: {'terminal_boundary': mark() | events.terminal})
    return BattleRuntime(), collector, observer, snapshot, state, events, calls, commit


def test_native_guarded_sample_brackets_and_consumes_all_event_families_once(tmp_path):
    runtime, collector, observer, snapshot, state, events, calls, commits = native_connection()
    collector.diagnostics = SimpleNamespace(platform='native_pc', connection_ok=True)
    collector.connect = lambda **_: None
    collector.locate_lifecycle = lambda: None
    collector.close = lambda: None
    observer.start = observer.stop = lambda: None
    history = BattleHistory(tmp_path)
    session = CollectionSession(None, None, collector=collector, frida_remote=None,
                                observer_factory=lambda *_: observer, history=history)
    session.enable_lifecycle(runtime)
    session.connect()
    try:
        first = session.read()
        assert calls == ['mark', 'lifecycle', 'pair', 'mark', 'batch']
        assert first['event_status'] == 'recording'
        snapshot.frame, snapshot.game_time, snapshot.damage, state.healing = 2, 1, 20, 6
        events.queue = [
            dict(event(1, 2, 1, 1), kind='skill_start', skill_key='skill'),
            dict(event(2, 2, 1, 20), target='enemy', target_type=0, damage_type=1,
                 hp_before=100, hp_after=80, real_delta=-20, killed=False, shield_absorbed=0),
            dict(event(3, 2, 1, 6), kind='healing'),
            dict(event(4, 2, 1, 3), kind='shield_absorbed'),
        ]
        session.read()
        snapshot.frame, snapshot.game_time, state.battle_state = 3, 2, 3
        events.queue.append(dict(event(5, 3, 2, 1), kind='skill_end', skill_key='skill'))
        assert session.read()['event_status'] == 'finished'
        assert commits == [0, 4, 5]
        state.active = False
        with pytest.raises(CaptureRetry, match='battle_absent'):
            session.read()
        with pytest.raises(CaptureRetry, match='battle_absent'):
            session.read()
        archive, = history.list_recent().records
        assert archive.event_status == 'finished'
        assert archive.battle_id == first['battle_id']
        row = archive.statistics['rows']['char_a']
        assert row['damage'] == row['observed_damage'] == row['skill_damage'] == 20
        assert row['effective_damage'] == 20
        assert row['healing'] == row['observed_healing'] == 6
        assert row['shield_absorbed'] == 3
    finally:
        session.close()


@pytest.mark.parametrize('step', [1, 2, 'batch'])
@pytest.mark.parametrize('key', ['source_build', 'native_session'])
def test_native_observer_rejects_mismatched_marks_or_batches_without_losing_counters(step, key):
    runtime, collector, observer, snapshot, _, events, calls, commits = native_connection()
    events.claims[step] = {key: 'wrong'}
    snapshot.damage = 12
    result = runtime.poll(collector, observer)
    assert 'different or unverified' in result.events['error']
    assert ('batch' in calls) == (step == 'batch')
    row = runtime.sessions.adapter.result()['rows']['char_a']
    assert row['damage'] == 12 and not row['event_coverage_complete']
    assert commits == []


def test_busy_native_opening_keeps_events_pending_for_the_next_guarded_pair():
    runtime, collector, observer, snapshot, _, events, calls, commits = native_connection()
    runtime.poll(collector, observer)
    snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1, 20
    events.queue = [event(1, 2, 1, 20)]
    events.claims[4] = {'active': 1}
    with pytest.raises(CaptureRetry, match='in flight'):
        runtime.poll(collector, observer)
    assert calls.count('batch') == 1 and commits == [0]
    runtime.poll(collector, observer)
    assert commits == [0, 1]
    assert runtime.sessions.adapter.result()['rows']['char_a']['observed_damage'] == 20


@pytest.mark.parametrize('defer_baseline', [False, True])
def test_native_baseline_releases_retained_events_from_the_previous_round(defer_baseline):
    runtime, collector, observer, snapshot, _, events, _, commits = native_connection()
    collector.event_capability = dict(status='ready', error=None)
    read_through = observer.read_through

    def read_baseline(sequence):
        return dict(read_through(sequence), events=[])

    observer.read_baseline = read_baseline
    snapshot.frame, snapshot.game_time = 5106, 170
    runtime.poll(collector, observer)
    # The old round was replaced before its final event could be accepted.
    events.queue = [event(1, 5106, 170)]
    snapshot.frame, snapshot.game_time, snapshot.damage = 45, 1.5, 0
    runtime.poll(collector, observer, prepare_observer=lambda _: (defer_baseline, observer))
    assert runtime.sessions.generation == 2
    assert runtime.display_sample()['event_status'] == 'recording'
    assert commits == [0, 1]

    snapshot.frame, snapshot.game_time, snapshot.damage = 60, 2, 40
    events.queue = [event(2, 60, 2, 40)]
    runtime.poll(collector, observer)
    result = runtime.display_sample()
    assert result['statistics']['rows']['char_a']['observed_damage'] == 40
    assert result['event_watermark']['sequence'] == 2
    assert commits == [0, 1, 2]


@pytest.mark.parametrize('clock', [
    {'sequence': 3},
    {'frame': 4},
    {'game_time': 3},
    {'game_time': 1.5},
])
def test_native_batch_cannot_publish_or_acknowledge_events_beyond_its_watermark(clock):
    runtime, collector, observer, snapshot, _, events, calls, commits = native_connection()
    runtime.poll(collector, observer)
    snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1, 20
    first = event(1, 2, 1, 20)
    events.queue = [first]
    runtime.poll(collector, observer)

    snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2, 60
    second = event(2, 3, 2, 40)
    events.queue.append(second)
    bad_events = [first, second, second | clock] if 'sequence' in clock else [first, second | clock]
    events.claims['batch'] = dict(events=bad_events, source_attribution_version=1,
                                  partial_damage_version=7, damage_settlement_ready=True)
    runtime.poll(collector, observer)

    result = runtime.display_sample()
    assert result['event_status'] == 'gap'
    assert 'bounded watermark' in result['event_error']
    row = result['statistics']['rows']['char_a']
    assert row['damage'] == 60
    assert row['observed_damage'] == 20
    assert result['event_watermark']['sequence'] == 1
    assert not runtime.sessions.adapter.statistics.settlement_enabled
    assert commits == [0, 1]

    # A permanent gap must keep native counters advancing without requesting
    # the same rejected event payload on each poll.
    reads = calls.count('batch')
    snapshot.frame, snapshot.game_time, snapshot.damage = 4, 3, 70
    runtime.poll(collector, observer)
    result = runtime.display_sample()
    assert result['event_status'] == 'gap'
    assert result['statistics']['rows']['char_a']['damage'] == 70
    assert calls.count('batch') == reads
    assert commits == [0, 1]


def test_native_batch_with_a_sequence_gap_cannot_change_source_attribution():
    runtime, collector, observer, snapshot, _, events, _, commits = native_connection()
    runtime.poll(collector, observer)
    snapshot.frame, snapshot.game_time, snapshot.damage = 2, 1, 20
    events.queue = [event(1, 2, 1, 20)]
    runtime.poll(collector, observer)

    snapshot.frame, snapshot.game_time, snapshot.damage = 3, 2, 50
    events.queue = [dict(event(3, 3, 2, 30), source_character_uid=77)]
    events.claims['batch'] = dict(source_attribution_version=1)
    runtime.poll(collector, observer)

    result = runtime.display_sample()
    assert result['event_status'] == 'gap'
    assert 'event sequence gap' in result['event_error']
    row = result['statistics']['rows']['char_a']
    assert row['damage'] == 50
    assert row['observed_damage'] == 20
    assert 'char_a' not in runtime.sessions.adapter.mapping_conflicts
    assert commits == [0, 1]


def test_native_observer_requires_ordered_read_and_acknowledgement_before_reading():
    runtime, collector, observer, snapshot, _, _, calls, _ = native_connection()
    del observer.commit_events
    snapshot.damage = 12
    result = runtime.poll(collector, observer)
    assert 'bounded reads and commit acknowledgements' in result.events['error']
    assert 'batch' not in calls
    assert runtime.sessions.adapter.result()['rows']['char_a']['damage'] == 12


@pytest.mark.parametrize('valid', [True, False])
def test_native_terminal_boundary_requires_exact_opaque_identity(valid):
    runtime, collector, observer, _, state, events, _, _ = native_connection()
    runtime.poll(collector, observer)
    state.active = False
    if not valid:
        events.terminal = {'native_session': 'different battle'}
    runtime.poll(collector, observer)
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == ('finished' if valid else 'gap')


def connected(*, observer=LiveObserver, remote='remote'):
    calls, records = [], []
    collector = Collector(calls)
    session = CollectionSession('adb', 'serial', collector=collector,
                                observer_factory=observer, frida_remote=remote,
                                audit=lambda kind, data: records.append((kind, deepcopy(data))))
    session.enable_lifecycle()
    session.connect()
    return session, collector, calls, records


def advance(session, collector, *, terminal=False):
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    collector.state = 3 if terminal else 2
    session.observer.sequence = session.observer.epoch = 1
    session.observer.events = [event(1)]


def test_event_read_timeout_preserves_terminal_batch_until_host_commit():
    session, collector, _, records = connected()
    acknowledgements = []
    try:
        observer = session.observer
        fail = True

        def read_through(sequence):
            nonlocal fail
            if sequence == 1 and fail:
                fail = False
                raise ObserverReadRetry('observer read response timed out')
            return observer.drain()

        def commit_events(sequence):
            result = session.runtime.sessions.adapter.result()
            acknowledgements.append((sequence, result['rows']['char_a']['observed_damage']))

        observer.read_through = read_through
        observer.commit_events = commit_events
        baseline = session.read()
        assert baseline['event_status'] == 'recording'
        advance(session, collector, terminal=True)
        with pytest.raises(CaptureRetry, match='response timed out'):
            session.read()
        assert acknowledgements == [(0, 0)]
        assert len([kind for kind, _ in records if kind == 'published']) == 1
        finished = session.read()
        assert finished['event_status'] == 'finished'
        assert finished['statistics']['rows']['char_a']['observed_damage'] == 20
        assert acknowledgements == [(0, 0), (1, 20)]
        session.read()
        assert acknowledgements == [(0, 0), (1, 20)]
    finally:
        session.close()


def test_enemy_state_batch_updates_the_panel_without_waiting_for_another_snapshot():
    session, collector, _, _ = connected()
    polls = []

    def seed():
        polls.append(collector.frame)
        return {'ok': True, 'fixed_frame': collector.frame, 'enemies': []}

    collector.enemy_telemetry = EnemyTelemetry(SimpleNamespace(poll_fast=seed),
                                              bootstrap=lambda _: True, clock=lambda: 50.0)
    collector.read_enemies = collector.enemy_telemetry.sample
    observer = session.observer
    drain = observer.drain
    observer.drain = lambda: dict(drain(), enemy_state_ready=True,
                                  enemy_entry_ready=True, enemy_phase_ready=True)
    sequence = 0

    def advance_enemy(frame, records, *, terminal=False):
        nonlocal sequence
        collector.frame, collector.game_time = frame, float(frame - 1)
        collector.state = 3 if terminal else 2
        observer.events = []
        for uid, eid, kind, evidence in records:
            sequence += 1
            observer.events.append(dict(sequence=sequence, frame=frame, game_time=collector.game_time,
                                        kind=kind, source=None, target=eid, target_instance=uid,
                                        **({'amount': 0} | evidence)))
        observer.sequence = observer.epoch = sequence
        return session.read()

    def enemy_rows(sample):
        return {row['instance_uid']: row for row in sample['enemy']['rows']}

    try:
        assert session.read()['statistics']['enemy_state']['complete']
        boss, soldier = ('12', 'enemy_patrt'), ('13', 'enemy_soldier')
        first = advance_enemy(2, [
            (*boss, 'enemy_enter', dict(hp_after=60000, enemy_entry_attributes={'max_hp': 60000})),
            (*soldier, 'enemy_enter', dict(hp_after=4500, enemy_entry_attributes={'max_hp': 4500})),
            (*soldier, 'enemy_hp', dict(hp_before=4500, hp_after=0)),
            (*soldier, 'enemy_finish', dict(hp_after=0, finish_reason=2)),
            (*boss, 'enemy_hp', dict(hp_before=60000, hp_after=0)),
            (*boss, 'enemy_fake_death', dict(hp_after=0)),
        ])
        assert enemy_rows(first)['13']['lifecycle'] == 'departed'
        assert enemy_rows(first)['13']['end_frame'] == 2
        assert enemy_rows(first)['12']['lifecycle'] == 'rebirthing'
        assert enemy_rows(first)['12']['end_frame'] is None
        assert first['enemy']['last_point']['game_time'] == 1
        second = advance_enemy(3, [(*boss, 'enemy_reborn', dict(
            amount=60000, hp_before=0, hp_after=60000, phase_total_hp=120000,
            phase_count=2, phase_from_birth=True))])
        assert enemy_rows(second)['12']['hp'] == 60000
        assert enemy_rows(second)['12']['lifecycle'] == 'active'
        assert second['enemy']['totals']['entered_total_hp'] == 124500
        assert second['enemy']['last_point']['remaining_hp'] == 60000
        final = advance_enemy(4, [(*boss, 'enemy_finish', dict(hp_after=0, finish_reason=2))],
                              terminal=True)
        assert final['event_status'] == 'finished'
        assert final['event_watermark']['sequence'] == sequence
        assert enemy_rows(final)['12']['lifecycle'] == 'departed'
        assert enemy_rows(final)['12']['end_frame'] == 4
        assert final['enemy']['totals']['remaining_hp'] == 0
        assert final['statistics']['rows']['char_a']['observed_damage'] == 0
        assert polls == [1]
    finally:
        session.close()


@pytest.mark.parametrize('before_baseline', [True, False])
@pytest.mark.parametrize('probe_error', [
    'Error: unverified shield', 'Error: missing source identity',
    'Error: shield absorption mismatch',
])
def test_unverified_shield_is_unknown_or_incomplete_instead_of_reliable_zero(before_baseline, probe_error):
    from panel_model import Category, PanelConfig, render_panel, rows_from_statistics
    session, collector, _, _ = connected()
    try:
        if not before_baseline:
            baseline = session.read()
            assert baseline['statistics']['rows']['char_a']['shield_absorbed'] == 0
            assert baseline['statistics']['rows']['char_a']['event_coverage_complete']
        # The probe reports this fatal mark for an unverified owned shield.
        session.observer.error = probe_error
        collector.frame, collector.game_time, collector.damage = 2, 1, 135
        sample = session.read()
        row = sample['statistics']['rows']['char_a']
        assert not row['event_coverage_complete']
        assert row['shield_absorbed'] == (None if before_baseline else 0)
        assert sample['event_status'] in ('failed', 'gap')
        view = render_panel(PanelConfig(category=Category.SHIELD),
                            rows_from_statistics(sample['statistics']))
        assert not view.share_known and not view.entries
        collector.frame, collector.game_time, collector.damage = 3, 2, 145
        current = session.read()['statistics']['rows']['char_a']
        assert current['damage'] == 145
        assert not current['event_coverage_complete']
    finally:
        session.close()


def test_product_snapshot_events_final_revision_and_archive_have_one_id():
    session, collector, calls, records = connected()
    try:
        first = session.read()
        advance(session, collector, terminal=True)
        last = session.read()
        adapter = session.runtime.sessions.adapter
        assert session.capture.adapter is adapter
        assert first['session'] == last['battle_id'] == adapter.statistics.session == adapter.event_stream.session
        row = last['statistics']['rows']['char_a']
        assert row['observed_damage'] == 20 and row['event_coverage_complete']
        assert last['event_status'] == 'finished'
        collector.damage = 125
        revision = session.read()
        assert revision['statistics']['rows']['char_a']['damage'] == revision['rows'][0]['damage'] == 125
        assert revision['event_watermark'] == last['event_watermark']
        assert revision['statistics']['rows']['char_a']['observed_damage'] == 20
        assert revision['snapshot_revision'] > last['snapshot_revision']
        assert session.read()['snapshot_revision'] == revision['snapshot_revision']
        collector.state = 0
        with pytest.raises(CaptureRetry):
            session.read()
        with pytest.raises(CaptureRetry):
            session.read()
        archive, = [data for kind, data in records if kind == 'archive_ready']
        assert archive['battle_id'] == first['battle_id']
        assert archive['statistics']['rows']['char_a']['damage'] == 125
        assert archive['statistics']['rows']['char_a']['observed_damage'] == 20
        assert archive['statistics']['game_time'] == 1
        assert archive['event_status'] == last['event_status'] == 'finished'
        assert archive['event_error'] is None
        assert archive['metadata']['outcome'] == 'unknown'
    finally:
        session.close()
    assert calls.count('attach') == calls.count('detach') == 1


@pytest.mark.parametrize('ending', ['scene_left', 'terminal_without_observer'])
def test_missing_terminal_event_watermark_archives_a_gap_and_keeps_native_totals(ending):
    session, collector, _, records = connected()
    try:
        session.read()
        advance(session, collector)
        assert session.read()['event_status'] == 'recording'
        if ending == 'terminal_without_observer':
            collector.state = 3
            observer, session.observer = session.observer, None
            try:
                # Matching the last live frame alone cannot verify the event endpoint.
                collector.damage = 125
                terminal = session.read()
                assert terminal['event_status'] == 'gap'
            finally:
                session.observer = observer
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
        archive, = [data for kind, data in records if kind == 'archive_ready']
        row = archive['statistics']['rows']['char_a']
        assert row['damage'] == (120 if ending == 'scene_left' else 125)
        assert row['observed_damage'] == 20
        assert row['event_coverage_end'] == 1
        assert not row['event_coverage_complete']
        assert archive['event_status'] == 'gap'
        assert archive['event_error'] == '未取得最终事件水位'
    finally:
        session.close()


def test_same_map_clock_and_address_reuse_discards_old_event_queue_and_history_stays_pinned():
    session, collector, _, records = connected()
    try:
        first = session.read()
        advance(session, collector)
        session.read()
        old = next(data for kind, data in reversed(records) if kind == 'lifecycle_observation')
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
        archive = next(data for kind, data in records if kind == 'archive_ready')
        session.runtime.view.show_history(archive)
        collector.state, collector.frame, collector.game_time, collector.damage = 2, 1, 0, 3
        # A late delivery from the old battle is drained at the new baseline,
        # before event coverage starts; no new observer is attached to this PID.
        session.observer.events = [event(1)]
        second = session.read()
        assert second['battle_id'] != first['battle_id']
        assert second['statistics']['rows']['char_a']['observed_damage'] == 0
        assert second['statistics']['rows']['char_a']['damage'] == 3
        state = session.runtime.sessions.live()
        assert not session.runtime.sessions.consume(replace(old, sequence=999))
        assert session.runtime.sessions.live() == state
        assert session.runtime.view.gui()['battle_id'] == first['battle_id']
        assert session.runtime.view.hud()['battle_id'] == second['battle_id']
        assert len([1 for kind, _ in records if kind == 'archive_ready']) == 1
    finally:
        session.close()


def test_transient_frame_failure_preserves_events_until_a_reliable_pair(monkeypatch):
    session, collector, _, _ = connected()
    try:
        session.read()
        advance(session, collector)
        original = collector.read_roster
        monkeypatch.setattr(collector, 'read_roster', lambda: replace(original(), frame=99))
        with pytest.raises(CaptureRetry):
            session.read()
        assert len(session.observer.events) == 1
        assert not session.capture.adapter.event_stream.closed
        monkeypatch.setattr(collector, 'read_roster', original)
        sample = session.read()
        assert sample['statistics']['rows']['char_a']['observed_damage'] == 20
        assert sample['event_status'] == 'recording'
    finally:
        session.close()


def test_subround_with_unmapped_roster_preserves_observer_and_archives(tmp_path, monkeypatch):
    from battle_lifecycle import MappingRetry
    from capture_battles import replay
    from capture_cli import json_value

    session, collector, calls, records = connected()
    try:
        previous = session.read()
        advance(session, collector, terminal=True)
        session.read()
        collector.state, collector.frame, collector.game_time, collector.damage = 2, 1, 0, 0
        original = collector.read_roster
        monkeypatch.setattr(collector, 'read_roster', lambda: replace(original(), entries=()))
        observer = session.observer
        for _ in range(3):
            with pytest.raises(MappingRetry):
                session.read()
            assert session.observer is observer
            assert session.runtime.display_sample()['battle_id'] == previous['battle_id']
        assert not any(data.reason == 'read_unavailable' for kind, data in records
                       if kind == 'lifecycle_observation')
        monkeypatch.setattr(collector, 'read_roster', original)
        baseline = session.read()
        assert baseline['battle_id'] != previous['battle_id']
        assert baseline['event_status'] == 'recording'
        advance(session, collector, terminal=True)
        session.read()
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
    finally:
        session.close()

    assert calls.count('attach') == calls.count('detach') == 1
    path = tmp_path / 'capture.jsonl'
    path.write_text('\n'.join(json.dumps(dict(kind=kind, data=data), default=json_value)
                              for kind, data in records), encoding='utf-8')
    result = replay(path)
    assert result['archives'] == 2


def test_settlement_quality_stays_with_its_round_and_paired_watermark():
    session, collector, calls, _ = connected()
    observer = session.observer
    scoped_mark, read_batch = observer.mark, observer.drain
    read_roster = collector.read_roster
    collector.read_roster = lambda: replace(read_roster(), local_player_side=0)
    round_unknown = False
    drain_unknown = True
    observer.mark = lambda: dict(scoped_mark(), damage_settlement_quality_version=1,
                                 enemy_damage_ready=True, enemy_damage_unknown=round_unknown)
    observer.drain = lambda: dict(read_batch(), partial_damage_version=7,
                                  source_attribution_version=1,
                                  enemy_damage_ready=True, enemy_damage_unknown=drain_unknown)

    def hit(sequence, frame, time, amount, *, complete=True):
        collector.frame, collector.game_time = frame, time
        collector.damage += amount
        observer.sequence = observer.epoch = sequence
        observer.events = [dict(event(sequence, frame=frame, game_time=time, amount=amount),
                                kind='damage_settlement', source_character_uid=1,
                                target='enemy_a', target_uid='77', damage_type=1,
                                shield_absorbed=0, settlement_complete=complete,
                                nominal_amount=amount, killed=False)]
        return session.read()['statistics']['rows']['char_a']['settled_damage']

    try:
        first = session.read()
        # A later batch can report an unrelated quality value. The paired
        # mark is the authority at the exact frame being applied.
        assert hit(1, 30, 1, 20)['complete']
        round_unknown, drain_unknown = True, False
        affected = hit(2, 60, 2, 25)
        assert not affected['complete']
        assert affected['unverified_hits'] == 0
        assert affected['effective_damage'] == 45

        collector.frame, collector.game_time, collector.damage = 0, 0, 0
        round_unknown, drain_unknown = False, True
        successor = session.read()
        assert successor['battle_id'] != first['battle_id']
        healthy = hit(3, 30, 1, 30)
        assert healthy['complete'] and healthy['effective_damage'] == 30
        assert healthy['unverified_hits'] == 0
        partial = hit(4, 60, 2, 10, complete=False)
        assert not partial['complete'] and partial['unverified_hits'] == 1
        assert partial['effective_damage'] == 40
        assert calls.count('attach') == 1
    finally:
        session.close()


def test_empty_preparation_preserves_observer_and_has_no_archive(monkeypatch):
    session, collector, calls, records = connected()
    try:
        previous = session.read()
        advance(session, collector)
        session.read()
        collector.state, collector.frame, collector.game_time = 2, 1, 0
        original_roster, original_sample = collector.read_roster, collector.read
        monkeypatch.setattr(collector, 'read_roster', lambda: replace(original_roster(), entries=()))
        monkeypatch.setattr(collector, 'read', lambda: dict(original_sample(), rows=[]))
        observer = session.observer
        drained, committed = [], []
        drain = observer.drain
        def read_through(sequence):
            drained.append(sequence)
            return drain()
        observer.read_through = read_through
        observer.commit_events = committed.append
        for _ in range(2):
            with pytest.raises(CaptureRetry, match='preparing'):
                session.read()
            assert session.observer is observer
            assert session.runtime.sessions.state == 'preparing'
            assert session.runtime.display_sample()['battle_id'] == previous['battle_id']
        prepared = [data for kind, data in records if kind == 'lifecycle_observation' and data.kind == 'preparing']
        assert len({observation.generation for observation in prepared}) == 1
        assert all(observation.events is None for observation in prepared)
        archives = [data for kind, data in records if kind == 'archive_ready']
        assert [archive['battle_id'] for archive in archives] == [previous['battle_id']]
        assert not drained and not committed

        collector.state = 3
        with pytest.raises(CaptureRetry, match='end'):
            session.read()
        assert session.runtime.sessions.state == 'ended'
        assert len([1 for kind, _ in records if kind == 'archive_ready']) == 1

        collector.state = 1
        with pytest.raises(CaptureRetry, match='initializing'):
            session.read()
        collector.state, collector.frame, collector.game_time, collector.damage = 2, 1, 0, 0
        with pytest.raises(CaptureRetry, match='preparing'):
            session.read()
        generation = session.runtime.sessions.preparing_generation
        monkeypatch.setattr(collector, 'read_roster', original_roster)
        monkeypatch.setattr(collector, 'read', original_sample)
        sample = session.read()
        assert sample['battle_id'].endswith(f':{generation}')
        assert sample['event_status'] == 'recording' and session.observer is observer
        assert drained == committed == [observer.sequence]
        assert calls.count('attach') == 1
    finally:
        session.close()


@pytest.mark.parametrize('mode', ['disabled', 'startup_failure', 'runtime_failure'])
def test_lifecycle_retains_default_event_fallback_and_native_totals(mode):
    class Failed(LiveObserver):
        def start(self):
            raise ConnectionError('remote unavailable')
    session, collector, _, records = connected(
        observer=Failed if mode == 'startup_failure' else LiveObserver,
        remote=None if mode == 'disabled' else 'remote')
    try:
        session.read()
        collector.frame, collector.game_time, collector.damage = 2, 1, 150
        if mode == 'runtime_failure':
            session.observer.error = 'observer lost'
        sample = session.read()
        row = sample['statistics']['rows']['char_a']
        assert row['damage'] == sample['rows'][0]['damage'] == 150
        assert sample['event_status'] == {'disabled': 'unavailable', 'startup_failure': 'failed',
                                          'runtime_failure': 'gap'}[mode]
        if mode != 'runtime_failure':
            assert row['shield_absorbed'] is None
        else:
            assert row['event_coverage_end'] == 0 and not row['event_coverage_complete']
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
        archive = next(data for kind, data in records if kind == 'archive_ready')
        assert archive['event_status'] == sample['event_status']
    finally:
        session.close()


def test_lifecycle_reconnect_keeps_old_result_until_new_generation():
    session, collector, _, records = connected(remote=None)
    first = session.read()
    runtime = session.runtime
    session.close()
    calls = []
    other = Collector(calls)
    other.core.mc.process_instance = ('boot', 2)
    next_session = CollectionSession('adb', 'serial', collector=other, frida_remote=None,
                                     audit=lambda kind, data: records.append((kind, deepcopy(data))))
    next_session.enable_lifecycle(runtime)
    try:
        next_session.connect()
        current = next_session.read()
        assert current['battle_id'] != first['battle_id']
        archive, = [data for kind, data in records if kind == 'archive_ready']
        assert archive['battle_id'] == first['battle_id']
    finally:
        next_session.close()


def test_product_journal_replays_event_totals_and_rejects_lost_archive(tmp_path):
    from capture_battles import replay
    from capture_cli import json_value
    session, collector, _, records = connected()
    try:
        session.read()
        advance(session, collector, terminal=True)
        session.read()
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
    finally:
        session.close()
    path = tmp_path / 'capture.jsonl'
    def save(values):
        path.write_text('\n'.join(json.dumps(dict(kind=kind, data=data), default=json_value)
                                  for kind, data in values), encoding='utf-8')
    save(records)
    result = replay(path)
    assert result['archives'] == 1
    assert result['last_state']['statistics']['rows']['char_a']['observed_damage'] == 20
    save([(kind, data) for kind, data in records if kind != 'archive_ready'])
    with pytest.raises(ValueError, match='handoff'):
        replay(path)
    damaged = deepcopy(records)
    next(data for kind, data in damaged if kind == 'published')['statistics']['rows']['char_a']['damage'] = 999
    save(damaged)
    with pytest.raises(ValueError, match='published statistics'):
        replay(path)


def test_guarded_native_metadata_reaches_archive_without_inventing_practice_or_result(monkeypatch):
    session, collector, _, records = connected(remote=None)
    original = collector.read_lifecycle
    stage = 'Activities/ACT3D0/level_act3d0_02'
    monkeypatch.setattr(collector, 'read_lifecycle', lambda: dict(original(), metadata=dict(
        stage_id=stage, evidence='native.LevelData.levelId:Android2.7.91')))
    try:
        session.read()
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
        archive = next(data for kind, data in records if kind == 'archive_ready')
        assert archive['metadata']['stage_id'] == stage
        assert archive['metadata']['practice'] is None
        assert archive['metadata']['outcome'] == 'unknown'
        observed = next(data for kind, data in records if kind == 'lifecycle_observation')
        assert observed.lifecycle['metadata']['stage_id'] == stage
    finally:
        session.close()


def test_confirmed_stage_switch_with_reused_addresses_and_monotonic_clock_starts_new_id(monkeypatch):
    session, collector, _, records = connected(remote=None)
    original = collector.read_lifecycle
    stage = {'value': 'level_a'}
    monkeypatch.setattr(collector, 'read_lifecycle', lambda: dict(original(), metadata=dict(
        stage_id=stage['value'], evidence='native.LevelData.levelId:Android2.7.91')))
    try:
        first = session.read()
        stage['value'] = 'level_b'
        # Clock, frame and every pointer may be reused. Confirmed stage evidence
        # identifies this switch before the adapter filters duplicate frames.
        collector.damage = 3
        second = session.read()
        assert second['session'] != first['session']
        assert second['statistics']['rows']['char_a']['damage'] == 3
        archive, = [data for kind, data in records if kind == 'archive_ready']
        assert archive['metadata']['stage_id'] == 'level_a'
    finally:
        session.close()


def test_process_exit_handoff_is_immediate_once_and_replayable(tmp_path):
    from types import SimpleNamespace
    from collector import Collector as NativeCollector, BattleChanged
    from capture_battles import replay
    from capture_cli import json_value

    session, collector, _, records = connected()
    try:
        first = session.read()
        collector.core.mc._pid_for_known_package = lambda: ('', 0)
        collector._lifecycle_reader = SimpleNamespace(sample=lambda: None)
        collector._check_process_identity = lambda: NativeCollector._check_process_identity(collector)
        collector.read_lifecycle = lambda: NativeCollector.read_lifecycle(collector)
        for _ in range(2):
            with pytest.raises(BattleChanged):
                session.read()
        archive, = [data for kind, data in records if kind == 'archive_ready']
        assert archive['battle_id'] == first['battle_id']
        assert archive['statistics']['ended']
        assert session.runtime.sessions.connection == 'disconnected'
    finally:
        session.close()
    path = tmp_path / 'exit.jsonl'
    path.write_text('\n'.join(json.dumps(dict(kind=kind, data=data), default=json_value)
                              for kind, data in records), encoding='utf-8')
    result = replay(path)
    assert result['archives'] == 1
    assert result['last_state']['state'] == 'ended'
    assert result['last_state']['connection'] == 'disconnected'


@pytest.mark.parametrize('observer_survives', [True, False])
def test_terminal_short_read_preserves_queue_until_recovery_or_observer_loss(monkeypatch, observer_survives):
    from roster import RosterUnavailable

    session, collector, _, records = connected()
    try:
        first = session.read()
        advance(session, collector)
        session.read()
        collector.state, collector.frame, collector.game_time, collector.damage = 3, 3, 2, 175
        session.observer.sequence = session.observer.epoch = 2
        session.observer.events = [event(2, frame=3, game_time=2, amount=55)]

        def unavailable():
            raise RosterUnavailable('one terminal short read')

        with monkeypatch.context() as patch:
            patch.setattr(collector, 'read_roster', unavailable)
            with pytest.raises(CaptureRetry):
                session.read()
        assert session.runtime.sessions.state == 'finalizing'
        assert not session.capture.closed
        if not observer_survives:
            session._retire_observer()
        recovered = session.read()
        row = recovered['statistics']['rows']['char_a']
        assert row['damage'] == 175
        assert row['observed_damage'] == (75 if observer_survives else 20)
        assert row['event_coverage_end'] == (2 if observer_survives else 1)
        assert row['event_coverage_complete'] is observer_survives
        assert row['recent_damage'] is None
        assert recovered['event_status'] == ('finished' if observer_survives else 'gap')
        collector.state = 1
        with pytest.raises(CaptureRetry):
            session.read()
        archive, = [data for kind, data in records if kind == 'archive_ready']
        assert archive['battle_id'] == first['battle_id']
        assert archive['statistics']['game_time'] == 2
        assert archive['statistics']['rows']['char_a'] == row
        assert archive['last_sample']['frame'] == 3
        assert 'final_sample_error' not in archive['metadata']
    finally:
        session.close()
