"""Business regressions reused from MuMu 01b7de4, including native boot safety."""
import pytest

from collector import ProcessChanged, SampleUnavailable
from event_capture import CaptureRetry
from test_collection_session import session
from test_event_capture import event
from test_native_pc_observer_session import session as native_session, TrustedObserver


def test_waiting_observer_preserves_counters_then_starts_a_later_event_interval(monkeypatch):
    current, collector, calls = session()
    current.enable_lifecycle()
    now = 0.0
    monkeypatch.setattr('collection_session.time.monotonic', lambda: now)
    monkeypatch.setattr(collector, 'observer_boot_ready', lambda: False)
    monkeypatch.setattr(collector, 'observer_lifecycle_ready', lambda: now >= 2)
    collector.observer_boot_reason = 'class_slot_build_mismatch'
    records = []
    current.audit = lambda kind, data: records.append((kind, data))
    try:
        current.connect()
        first = current.read()
        assert first['event_status'] == 'waiting_game_init'
        assert first['statistics']['rows']['char_a']['damage'] == 100
        assert first['event_watermark'] is None
        assert calls == ['transport', 'lifecycle']

        now = 2.0
        collector.frame, collector.game_time, collector.damage = 2, 1, 120
        baseline = current.read()
        assert baseline['battle_id'] == first['battle_id']
        assert baseline['event_status'] == 'recording'
        assert baseline['event_watermark']['frame'] == 2
        assert baseline['statistics']['coverage_start'] == 0
        row = baseline['statistics']['rows']['char_a']
        assert row['shield_coverage_start'] == 1 and row['damage'] == 120
        assert row['skill_split_reason'] == '观察者就绪前已有伤害'
        assert ('observer_game_init_ready', {'pid': 123, 'source': 'lifecycle'}) in records
        assert calls.count('attach') == 1

        collector.frame, collector.game_time, collector.damage = 3, 2, 150
        current.observer.sequence = current.observer.epoch = 1
        current.observer.events = [event(1, frame=3, game_time=2, amount=30)]
        result = current.read()
        assert result['event_status'] == 'recording'
        assert result['statistics']['rows']['char_a']['damage'] == 150
        assert result['statistics']['rows']['char_a']['observed_damage'] == 30
    finally:
        current.close()


@pytest.mark.parametrize('has_scene_method', [False, True])
def test_pending_native_boot_does_not_use_mumu_scene_readiness_or_read_snapshots(monkeypatch, has_scene_method):
    current, collector = native_session(TrustedObserver)
    collector.observer_boot_ready = lambda: False
    if has_scene_method:
        collector.observer_lifecycle_ready = lambda: pytest.fail('native VM gate cannot use scene readiness')
    # NativeCollector has no weaker scene-based fallback. A missing VM proof
    # must keep this collector untouched, rather than attempt raw metadata reads.
    try:
        current.connect()
        with pytest.raises(CaptureRetry, match='等待游戏初始化'):
            current.read()
        assert current.capture is None and current.observer is None
        assert collector.calls == ['connect']
    finally:
        current.close()


def test_headless_native_wait_cannot_enter_scene_fallback_or_read_snapshots(monkeypatch):
    current, collector = native_session(TrustedObserver)
    now = [0.0]
    monkeypatch.setattr('collection_session.time.monotonic', lambda: now[0])
    monkeypatch.setattr('collection_session.time.sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    collector.observer_boot_ready = lambda: False
    collector.observer_lifecycle_ready = lambda: pytest.fail('native VM gate cannot use scene readiness')
    try:
        current.connect()
        with pytest.raises(SampleUnavailable, match='等待游戏初始化超时'):
            current.wait_for_game_init(wait_seconds=1)
        assert current.capture is None and current.observer is None
        assert collector.calls == ['connect']
    finally:
        current.close()


def test_read_only_lifecycle_locating_is_bounded_while_observer_waits(monkeypatch):
    current, collector, calls = session()
    current.enable_lifecycle()
    now = 0.0
    monkeypatch.setattr('collection_session.time.monotonic', lambda: now)
    monkeypatch.setattr(collector, 'observer_boot_ready', lambda: False)
    original = collector.locate_lifecycle

    def locate():
        if now < 5:
            calls.append('locate_pending')
            raise SampleUnavailable('游戏场景暂未可读')
        original()

    monkeypatch.setattr(collector, 'locate_lifecycle', locate)
    try:
        current.connect()
        for now in (0.2, 1.0, 5.0):
            with pytest.raises(CaptureRetry, match='等待游戏初始化'):
                current.read()
        assert calls == ['transport', 'locate_pending']
        now = 5.3
        assert current.read()['event_status'] == 'waiting_game_init'
        assert calls == ['transport', 'locate_pending', 'lifecycle']
    finally:
        current.close()


def test_process_change_retires_the_native_interval_before_observer_attachment(monkeypatch):
    current, collector, calls = session()
    current.enable_lifecycle()
    now = 0.0
    monkeypatch.setattr('collection_session.time.monotonic', lambda: now)
    monkeypatch.setattr(collector, 'observer_boot_ready', lambda: False)
    try:
        current.connect()
        assert current.read()['state'] == 2
        now = 2.0

        def replaced():
            raise ProcessChanged('游戏进程已变化')

        monkeypatch.setattr(collector, 'observer_boot_ready', replaced)
        with pytest.raises(ProcessChanged):
            current.read()
        assert current.runtime.sessions.state == 'ended'
        assert current.runtime.sessions.connection == 'disconnected'
        assert 'attach' not in calls
    finally:
        current.close()
