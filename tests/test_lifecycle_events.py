"""Product entry: a single lifecycle ID owns snapshots, events and archives."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from collection_session import CollectionSession
from enemy_telemetry import EnemyTelemetry
from event_capture import CaptureRetry
from event_observer import ObserverReadRetry
from test_collection_session import Collector, LiveObserver
from test_event_capture import event


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


def test_terminal_read_recovery_updates_totals_but_never_recovers_missing_events(monkeypatch):
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
        assert session.capture.closed
        recovered = session.read()
        row = recovered['statistics']['rows']['char_a']
        assert row['damage'] == 175
        assert row['observed_damage'] == 20
        assert row['event_coverage_end'] == 1
        assert not row['event_coverage_complete']
        assert row['recent_damage'] is None
        assert recovered['event_status'] == 'gap'
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
