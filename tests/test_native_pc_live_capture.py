"""A cooperative producer and independent sampler drive the real PC lifecycle."""

from contextlib import contextmanager
from copy import deepcopy
import json

import pytest

from battle_history import BattleHistory
from collection_session import CollectionSession
from event_capture import CaptureRetry
from native_pc.event_source import PcEventSource
from native_pc.internal_capture import ROOTS
from native_pc.live_event_producer import CooperativeEventWriter
from native_pc_collector import NativePcCollector
from test_event_probe_parity import declared_quality
from test_internal_capture import ROOT, native_host
from test_native_pc_collector import ABSENT, OPERATOR, TOKEN, sample


STAGE = 'Obt/Main/level_main_01-07'
BUILD = 'win-x64:owned-cooperative-snapshot'


class OwnedSnapshotProcess:
    build = {'source_build': BUILD, 'known_build': True}
    battle_identity = tuple(int(value, 16) for value in ROOTS.values())

    def __init__(self, pid):
        self.pid = pid
        self.process_instance = pid, 1000 + pid
        self.report = None

    def process_alive(self):
        return True

    def observer_boot_ready(self):
        return True

    def battle_serial_for(self, identity, frame, raw_time, stage):
        return 1

    def advance(self, frame, damage, healing=0, *, state=2):
        self.report = sample(frame, damage, state=state)
        self.report['sample']['total_heal'] = healing
        self.report['sample']['actor_rows'][0]['healing'] = healing

    def sample(self, *, extra_reader, idle_reader=None):
        return deepcopy(self.report)

    def close(self):
        self.report = None


def publish_header(feed, process):
    feed.write_text(json.dumps(dict(format='arkdps-pc-events', version=1, producer='owned-combat-test',
                                    producer_version=1, process_instance=process.process_instance,
                                    source_build=BUILD)) + '\n', encoding='utf-8')


def frame_mark(process, frame, sequence, *, stage=STAGE):
    when = frame // 30
    return dict(declared_quality(), native_process_pid=process.pid, native_frame=frame,
                native_fixed_time=str(when << 32), native_identity=ROOTS, native_stage_id=stage,
                native_generation=1, frame=frame, game_time=when, sequence=sequence, epoch=sequence, active=0)


def publish_frame(feed, process, frame, sequence, events=(), *, terminal=False, stage=STAGE):
    mark = frame_mark(process, frame, sequence, stage=stage)
    line = dict(mark=mark, events=list(events))
    if terminal:
        line['terminal_mark'] = mark
    with feed.open('ab') as stream:
        stream.write((json.dumps(line) + '\n').encode('utf-8'))


def hit(sequence, frame, actual, nominal, *, killed=True):
    return dict(sequence=sequence, frame=frame, game_time=frame // 30, source=TOKEN,
                source_character_uid=20, kind='damage_settlement', amount=actual, nominal_amount=nominal,
                target='enemy_1007_slime', target_uid='5', target_instance='owned-enemy-5', damage_type=1,
                shield_absorbed=0, settlement_complete=True, killed=killed)


@contextmanager
def connected_session(feed, process, history):
    collector = NativePcCollector(feed.parent, probe_factory=lambda _root: process)
    source = PcEventSource(feed.parent, collector_root=ROOT)
    observations = []
    session = CollectionSession(
        None, None, frida_remote=None, collector=collector, history=history,
        observer_factory=source.select(enabled=True, backend='native', game_root=feed.parent, feed_path=feed),
        audit=lambda kind, data: observations.append(data) if kind == 'lifecycle_observation' else None,
    )
    session.enable_lifecycle()
    try:
        session.connect()
        assert session.event_status == 'ready', session.event_error
        yield session, observations
    finally:
        try:
            session.close()
        finally:
            source.close()
    assert not session.cleanup_errors


def test_cooperative_feed_publishes_only_on_matching_snapshot_and_archives_once(native_host, tmp_path):
    feed = tmp_path / 'producer.jsonl'
    history = BattleHistory(tmp_path / 'history')
    process = OwnedSnapshotProcess(123)
    process.advance(0, 0)
    with CooperativeEventWriter(feed, producer='owned-combat-test', producer_version=1,
                                process_instance=process.process_instance, source_build=BUILD) as writer, \
            connected_session(feed, process, history) as (session, observations):
        writer.publish(frame_mark(process, 0, 0), [])
        first = session.read()
        assert first['event_status'] == 'recording'
        assert first['event_watermark'] == dict(sequence=0, frame=0, game_time=0)
        events = [hit(1, 30, 5, 5, killed=False), hit(2, 30, 12.5, 15), dict(
            sequence=3, frame=30, game_time=1, source=TOKEN, source_character_uid=20, kind='healing', amount=6,
        )]

        # The cooperative writer progresses independently. Its newer mark
        # cannot be credited or acknowledged against the earlier snapshot.
        writer.publish(frame_mark(process, 30, 3), events)
        with pytest.raises(CaptureRetry):
            session.read()
        assert session.runtime.display_sample()['event_watermark']['sequence'] == 0
        assert session.observer.read_through(3)['events'] == events

        process.advance(30, 20, 6)
        observed = session.read()
        assert observed['battle_id'] == first['battle_id']
        assert observed['event_watermark'] == dict(sequence=3, frame=30, game_time=1)
        row = observed['statistics']['rows'][OPERATOR]
        assert row['damage'] == 20 and row['effective_damage'] == 17.5
        assert row['healing'] == row['observed_healing'] == 6
        assert [event['sequence'] for event in observations[-1].events['drain']['events']] == [1, 2, 3]
        assert session.observer.read_through(3)['events'] == []
        writer.heartbeat()
        repeated = session.read()
        assert repeated['event_watermark'] == observed['event_watermark']
        assert repeated['statistics']['rows'][OPERATOR]['effective_damage'] == 17.5

        process.advance(60, 20, 6, state=3)
        writer.publish(frame_mark(process, 60, 3), [], terminal=True)
        finished = session.read()
        assert finished['event_status'] == 'finished'
        process.report = ABSENT
        for _ in range(2):
            with pytest.raises(CaptureRetry, match='battle_absent'):
                session.read()
        archive, = history.list_recent().records
        assert archive.battle_id == first['battle_id'] and archive.event_status == 'finished'
        assert archive.statistics['rows'][OPERATOR]['effective_damage'] == 17.5


def test_header_only_does_not_expire_until_independent_sampler_is_in_battle(native_host, tmp_path, monkeypatch):
    from native_pc import live_event_source

    clock = [0.0]
    monkeypatch.setattr(live_event_source, 'monotonic', lambda: clock[0])
    feed = tmp_path / 'producer.jsonl'
    process = OwnedSnapshotProcess(123)
    process.report = ABSENT
    with CooperativeEventWriter(feed, producer='owned-combat-test', producer_version=1,
                                process_instance=process.process_instance, source_build=BUILD), \
            connected_session(feed, process, BattleHistory(tmp_path / 'history')) as (session, _):
        clock[0] = 2 * live_event_source.LiveNativeEventProbe.stale_timeout
        with pytest.raises(CaptureRetry, match='battle_absent'):
            session.read()
        assert session.event_status == 'ready'
        process.advance(0, 25, 4)
        with pytest.raises(CaptureRetry):
            session.read()
        clock[0] += live_event_source.LiveNativeEventProbe.stale_timeout
        sample = session.read()
        assert sample['event_status'] == 'gap' and sample['event_watermark'] is None
        row = sample['statistics']['rows'][OPERATOR]
        assert row['damage'] == 25 and row['healing'] == 4
        assert row['effective_damage'] is None and row['event_coverage_complete'] is False


def test_late_writer_without_quality_does_not_prove_missing_history(native_host, tmp_path):
    feed = tmp_path / 'late-producer.jsonl'
    process = OwnedSnapshotProcess(123)
    process.advance(30, 25, 4)
    mark = frame_mark(process, 30, 7)
    for key in declared_quality():
        mark.pop(key)
    with CooperativeEventWriter(feed, producer='owned-combat-test', producer_version=1,
                                process_instance=process.process_instance, source_build=BUILD) as writer, \
            connected_session(feed, process, BattleHistory(tmp_path / 'history')) as (session, _):
        writer.publish(mark, [])
        sample = session.read()
        row = sample['statistics']['rows'][OPERATOR]
        assert row['damage'] == 25 and row['healing'] == 4
        assert sample['event_watermark'] == dict(sequence=7, frame=30, game_time=1)
        assert row['shield_coverage_start'] == 1
        assert row['shield_absorbed'] is None and row['effective_damage'] is None
        assert row['overkill_coverage_complete'] is False


def test_cooperative_wrong_stage_cannot_create_the_first_baseline(native_host, tmp_path):
    feed = tmp_path / 'producer.jsonl'
    history = BattleHistory(tmp_path / 'history')
    process = OwnedSnapshotProcess(123)
    process.advance(0, 25, 4)
    publish_header(feed, process)
    # Matching roots and clocks can still predict the same initial serial.
    # The independent stage read must reject this producer's claim.
    publish_frame(feed, process, 0, 0, stage='Obt/Main/level_main_01-08')
    with connected_session(feed, process, history) as (session, _):
        current = session.read()
        assert current['battle_metadata']['stage_id'] == STAGE
        assert current['event_status'] == 'gap' and current['event_watermark'] is None
        row = current['statistics']['rows'][OPERATOR]
        assert row['damage'] == 25 and row['healing'] == 4
        assert row['effective_damage'] is None and row['shield_absorbed'] is None


def test_cooperative_feed_that_cannot_match_a_frame_returns_to_counters(native_host, tmp_path, monkeypatch):
    from native_pc import live_event_source

    clock = [0.0]
    monkeypatch.setattr(live_event_source, 'monotonic', lambda: clock[0])
    feed = tmp_path / 'producer.jsonl'
    history = BattleHistory(tmp_path / 'history')
    process = OwnedSnapshotProcess(123)
    process.advance(0, 0)
    publish_header(feed, process)
    publish_frame(feed, process, 0, 0)

    with connected_session(feed, process, history) as (session, _):
        assert session.read()['event_status'] == 'recording'
        process.advance(30, 25, 4)
        publish_frame(feed, process, 60, 1, [hit(1, 60, 11, 11)])
        with pytest.raises(CaptureRetry):
            session.read()
        clock[0] += live_event_source.LiveNativeEventProbe.stale_timeout
        # A fresh heartbeat proves the file is active, but it still cannot
        # prove that this independently sampled frame belongs to its watermark.
        publish_frame(feed, process, 60, 1)
        current = session.read()
        assert current['event_status'] == 'gap'
        assert current['event_watermark']['sequence'] == 0
        row = current['statistics']['rows'][OPERATOR]
        assert row['damage'] == 25 and row['healing'] == 4
        assert row['event_coverage_complete'] is False


def test_cooperative_writer_rewind_keeps_counters_and_new_source_recovers(native_host, tmp_path):
    feed = tmp_path / 'producer.jsonl'
    history = BattleHistory(tmp_path / 'history')
    process = OwnedSnapshotProcess(123)
    process.advance(0, 0)
    publish_header(feed, process)
    publish_frame(feed, process, 0, 0)

    with connected_session(feed, process, history) as (session, _):
        first = session.read()
        assert first['event_status'] == 'recording'
        publish_frame(feed, process, 30, 1, [hit(1, 30, 14, 16)])
        process.advance(30, 16)
        known = session.read()
        assert known['event_watermark']['sequence'] == 1
        assert known['statistics']['rows'][OPERATOR]['effective_damage'] == 14

        # Restarting the writer in place withdraws the active stream. The
        # source must fail closed while the native counters remain readable.
        feed.write_bytes(b'')
        process.advance(60, 25, 4, state=3)
        fallback = session.read()
        row = fallback['statistics']['rows'][OPERATOR]
        assert fallback['event_status'] == 'gap'
        assert fallback['event_watermark']['sequence'] == 1
        assert row['damage'] == 25 and row['healing'] == 4
        assert row['event_coverage_complete'] is False
        assert row['settled_damage']['known_hp_damage'] == 14
        assert row['settled_damage']['complete'] is False
        process.report = ABSENT
        with pytest.raises(CaptureRetry, match='battle_absent'):
            session.read()

    previous, = history.list_recent().records
    assert previous.battle_id == first['battle_id'] and previous.event_status == 'gap'
    replacement = OwnedSnapshotProcess(124)
    replacement.advance(0, 0)
    publish_header(feed, replacement)
    publish_frame(feed, replacement, 0, 0)
    with connected_session(feed, replacement, history) as (session, _):
        recovered = session.read()
        assert recovered['battle_id'] != previous.battle_id and recovered['event_status'] == 'recording'
        publish_frame(feed, replacement, 30, 1, [hit(1, 30, 8, 8)], terminal=True)
        replacement.advance(30, 8, state=3)
        end = session.read()
        assert end['event_status'] == 'finished'
        assert end['statistics']['rows'][OPERATOR]['effective_damage'] == 8
        replacement.report = ABSENT
        with pytest.raises(CaptureRetry, match='battle_absent'):
            session.read()

    by_id = {record.battle_id: record for record in history.list_recent().records}
    assert set(by_id) == {previous.battle_id, recovered['battle_id']}
    assert by_id[previous.battle_id].statistics['rows'][OPERATOR]['damage'] == 25
    assert by_id[recovered['battle_id']].statistics['rows'][OPERATOR]['damage'] == 8
