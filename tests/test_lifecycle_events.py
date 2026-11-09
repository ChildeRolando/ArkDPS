"""Product entry: a single lifecycle ID owns snapshots, events and archives."""
from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from collection_session import CollectionSession
from event_capture import CaptureRetry
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


@pytest.mark.parametrize('before_baseline', [True, False])
def test_unverified_shield_is_unknown_or_incomplete_instead_of_reliable_zero(before_baseline):
    from panel_model import Category, PanelConfig, render_panel, rows_from_statistics
    session, collector, _, _ = connected()
    try:
        if not before_baseline:
            baseline = session.read()
            assert baseline['statistics']['rows']['char_a']['shield_absorbed'] == 0
            assert baseline['statistics']['rows']['char_a']['event_coverage_complete']
        # The probe reports this fatal mark for an unverified owned shield.
        session.observer.error = 'Error: unverified shield'
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
        assert archive['metadata']['outcome'] == 'unknown'
    finally:
        session.close()
    assert calls.count('attach') == calls.count('detach') == 1


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
