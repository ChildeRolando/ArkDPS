from types import SimpleNamespace

import pytest

from collection_session import CollectionSession
from connection import VersionStatus
from collector import BattleChanged, UnsupportedVersion, SampleUnavailable
from test_event_capture import Collector as SnapshotCollector, Observer, event


class Collector(SnapshotCollector):
    def __init__(self, calls):
        super().__init__()
        self.calls = calls
        self.diagnostics = SimpleNamespace(connection_ok=True, version_status=VersionStatus.COMPATIBLE)
        self.core = SimpleNamespace(mc=SimpleNamespace(pid=123, process_instance=('boot', 1)))
        self.state = 2

    def locate_lifecycle(self):
        self.calls.append('lifecycle')

    def read_lifecycle(self):
        return dict(battle=(100, 200), state=self.state, ready=self.state == 2,
                    transiting=False, source='level_a', target='')

    def bind_battle(self, address):
        assert address == 100

    def read(self):
        return dict(super().read(), battle_identity=(100, 200), state=self.state)

    def connect(self, *, locate):
        assert locate is False
        self.calls.append('transport')

    def locate_battle(self, *, force=False):
        self.calls.append('locate')

    def close(self):
        self.calls.append('collector_close')


class LiveObserver(Observer):
    def __init__(self, collector, remote):
        super().__init__(collector)
        self.calls = collector.calls
        self.remote = remote

    def start(self):
        self.calls.append('attach')

    def stop(self):
        self.calls.append('detach')


def session(live=True):
    calls = []
    collector = Collector(calls)
    result = CollectionSession('adb', 'serial', collector=collector,
                               frida_remote='remote' if live else None,
                               observer_factory=LiveObserver)
    return result, collector, calls


def test_shared_entry_attaches_before_locating_and_publishes_core_result():
    current, collector, calls = session()
    current.connect()
    current.locate()
    assert calls == ['transport', 'attach', 'locate']
    current.read()
    current.observer.sequence = current.observer.epoch = 1
    current.observer.events = [event(1)]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    sample = current.read()
    assert sample['statistics']['rows']['char_a']['observed_damage'] == 20
    assert sample['rows'][0]['damage'] == 120 and sample['event_status'] == 'recording'
    current.close()
    assert calls[-2:] == ['detach', 'collector_close']


def test_shared_entry_without_frida_keeps_shields_unknown():
    current, _, calls = session(live=False)
    current.connect()
    current.locate()
    sample = current.read()
    assert 'attach' not in calls
    assert sample['statistics']['rows']['char_a']['shield_absorbed'] is None
    assert sample['event_status'] == 'unavailable'
    current.close()


def test_default_entry_observes_after_certification_before_locating():
    calls = []
    current = CollectionSession('adb', 'serial', collector=Collector(calls),
                                observer_factory=LiveObserver)
    try:
        current.connect()
        current.locate()
        assert calls == ['transport', 'attach', 'locate']
        assert current.observer.remote == '127.0.0.1:27043'
        assert current.read()['event_status'] == 'recording'
    finally:
        current.close()


@pytest.mark.parametrize('failure', [ModuleNotFoundError('optional frida missing'),
                                    RuntimeError('Frida version mismatch'),
                                    ConnectionError('remote unreachable'),
                                    RuntimeError('attach failed')])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_observer_start_failure_preserves_native_snapshots(failure, cleanup_fails):
    calls, audit = [], []

    class FailedObserver(LiveObserver):
        def start(self):
            super().start()
            raise failure

        def stop(self):
            super().stop()
            if cleanup_fails:
                raise RuntimeError('cleanup failed')

    collector = Collector(calls)
    current = CollectionSession('adb', 'serial', collector=collector,
                                frida_remote='remote',
                                observer_factory=FailedObserver,
                                audit=lambda kind, data: audit.append((kind, data)))
    try:
        current.connect()
        assert current.observer is None and current.capture.observer is None
        current.locate()
        first = current.read()
        collector.frame, collector.game_time, collector.damage = 2, 1, 120
        sample = current.read()
        assert sample['event_status'] == 'failed' and sample['event_error'] == str(failure)
        assert first['statistics']['rows']['char_a']['damage'] == 100
        row = sample['statistics']['rows']['char_a']
        assert row['damage'] == 120 and row['healing'] == 0
        assert row['shield_absorbed'] is None and row['active_damage'] is None
        assert sample['event_watermark'] is None
        assert calls == ['transport', 'attach', 'detach', 'locate']
        assert ('observer_error', {'reason': str(failure)}) in audit
        assert any(kind == 'observer_cleanup_error' for kind, _ in audit) == cleanup_fails
    finally:
        if cleanup_fails:
            with pytest.raises(RuntimeError, match='cleanup failed'):
                current.close()
        else:
            current.close()
    assert calls[-1] == 'collector_close'
    assert calls.count('detach') == (2 if cleanup_fails else 1)
    assert bool(current._pending_observers) == cleanup_fails


def test_observer_construction_failure_also_degrades():
    def fail(*_args):
        raise RuntimeError('observer construction failed')

    current = CollectionSession('adb', 'serial', frida_remote='remote',
                                collector=Collector([]), observer_factory=fail)
    try:
        current.connect()
        current.locate()
        sample = current.read()
        assert sample['statistics']['rows']['char_a']['damage'] == 100
        assert sample['event_error'] == 'observer construction failed'
    finally:
        current.close()


@pytest.mark.parametrize('failure', [UnsupportedVersion('uncertified'),
                                    SampleUnavailable('process identity unverified')])
def test_collector_rejection_never_attaches_or_publishes(monkeypatch, failure):
    calls = []
    collector = Collector(calls)

    def reject(**_kwargs):
        raise failure

    monkeypatch.setattr(collector, 'connect', reject)
    current = CollectionSession('adb', 'serial', collector=collector, observer_factory=LiveObserver)
    try:
        with pytest.raises(type(failure)):
            current.connect()
        assert current.capture is None and 'attach' not in calls
        with pytest.raises(RuntimeError, match='not connected'):
            current.read()
    finally:
        current.close()


def test_live_gap_keeps_native_totals_and_freezes_event_coverage():
    current, collector, _ = session()
    current.connect()
    current.locate()
    current.read()
    current.observer.error = 'probe stopped'
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    sample = current.read()
    assert sample['event_status'] == 'gap'
    row = sample['statistics']['rows']['char_a']
    assert row['damage'] == 120 and row['event_coverage_end'] == 0
    assert not row['event_coverage_complete']
    current.close()


def test_event_gap_keeps_presence_so_full_cycle_dps_survives():
    current, collector, _ = session()
    current.connect()
    current.locate()
    current.read()
    current.observer.error = 'probe stopped'
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    sample = current.read()
    assert sample['event_status'] == 'gap'
    row = sample['statistics']['rows']['char_a']
    assert row['presence_complete'] is True and row['presence_seconds'] == 1
    collector.frame, collector.game_time, collector.damage = 4, 3, 150
    sample = current.read()
    row = sample['statistics']['rows']['char_a']
    assert sample['event_status'] == 'gap'
    assert row['damage'] == 150
    assert row['presence_complete'] is True and row['presence_seconds'] == 3
    current.close()


def test_observer_drain_exception_degrades_instead_of_stopping_snapshots(monkeypatch):
    current, collector, _ = session()
    try:
        current.connect()
        current.locate()
        current.read()
        collector.frame, collector.game_time, collector.damage = 2, 1, 120
        def fail():
            raise ConnectionError('drain disconnected')
        monkeypatch.setattr(current.observer, 'drain', fail)
        sample = current.read()
        assert sample['event_status'] == 'gap'
        assert 'drain disconnected' in sample['event_error']
        assert sample['statistics']['rows']['char_a']['damage'] == 120
        assert sample['statistics']['rows']['char_a']['event_coverage_end'] == 0
    finally:
        current.close()


def test_native_resources_are_closed_even_if_observer_cleanup_fails(monkeypatch):
    current, _, calls = session()
    current.connect()
    def fail():
        raise RuntimeError('observer detached already')
    monkeypatch.setattr(current.observer, 'stop', fail)
    with pytest.raises(RuntimeError):
        current.close()
    assert calls[-1] == 'collector_close'


def test_final_stopped_audit_failure_is_retained_and_retried_without_reclosing_resources():
    calls, stopped_records = [], []
    fail_once = True

    def audit(kind, data):
        nonlocal fail_once
        if kind == 'stopped':
            stopped_records.append(data)
            if fail_once:
                fail_once = False
                raise OSError('audit disk full')

    collector = Collector(calls)
    current = CollectionSession('adb', 'serial', collector=collector, audit=audit)
    with pytest.raises(OSError, match='audit disk full'):
        current.close()
    assert not current.closed
    assert current.cleanup_errors == {'audit': ['audit disk full']}
    assert calls.count('collector_close') == 1

    current.close()
    assert not current.closed  # historical audit failures remain reportable
    assert calls.count('collector_close') == 1
    assert len(stopped_records) == 2
    assert stopped_records[-1] == {'cleanup_errors': {'audit': ['audit disk full']}}


def test_final_watermark_clips_intervals_without_creating_a_gap(monkeypatch):
    current, collector, _ = session()
    current.connect()
    current.locate()
    current.read()
    current.observer.sequence = current.observer.epoch = 1
    current.observer.events = [event(1)]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    original = collector.read
    monkeypatch.setattr(collector, 'read', lambda: dict(original(), state=3))
    sample = current.read()
    row = sample['statistics']['rows']['char_a']
    assert sample['event_status'] == 'finished'
    assert sample['statistics']['ended'] and row['active_damage'] == 1
    assert row['event_coverage_end'] == 1 and row['event_coverage_complete']
    assert current.read()['event_status'] == 'finished'
    current.close()
    assert current.capture.adapter.result()['rows']['char_a']['event_coverage_complete']


def test_late_settlement_publishes_matching_native_and_statistics_revision(monkeypatch):
    current, collector, _ = session()
    current.connect()
    current.locate()
    current.read()
    collector.frame, collector.game_time = 2964, 98.8
    collector.damage = 28985.8828125
    original = collector.read
    monkeypatch.setattr(collector, 'read', lambda: dict(original(), state=3))
    initial = current.read()
    assert initial['statistics']['rows']['char_a']['damage'] == initial['rows'][0]['damage']
    collector.damage = 31899.103515625
    revised = current.read()
    assert revised['frame'] == initial['frame'] == 2964
    assert revised['statistics']['rows']['char_a']['damage'] == revised['rows'][0]['damage'] == collector.damage
    assert revised['snapshot_revision'] > initial['snapshot_revision']
    assert revised['event_watermark'] == initial['event_watermark']
    assert revised['statistics']['game_time'] == initial['statistics']['game_time']
    assert revised['statistics']['rows']['char_a']['recent_damage'] is None
    assert current.read()['snapshot_revision'] == revised['snapshot_revision']
    collector.session = 'new battle'
    with pytest.raises(BattleChanged):
        current.read()
    assert current.capture.adapter.result()['rows']['char_a']['damage'] == 31899.103515625
    current.close()
