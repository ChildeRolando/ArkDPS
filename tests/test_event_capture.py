import pytest

from event_capture import CaptureRetry, LiveEventCapture
from event_stream import StreamGap
from roster import CharacterEntry, RosterSnapshot
from roster import RosterUnavailable
from collector import SampleUnavailable, BattleChanged


class Collector:
    def __init__(self):
        self.frame = 1
        self.game_time = 0
        self.damage = 100
        self.session = 'battle'

    def read(self):
        return {'session': self.session, 'frame': self.frame, 'game_time': self.game_time,
                'rows': [{'id': 'char_a', 'damage': self.damage, 'healing': 0,
                          'physical': self.damage, 'arts': 0, 'true': 0, 'elemental': 0}]}

    def read_roster(self):
        entry = CharacterEntry(cid='char_a', unique_id=1, is_token=False,
                               is_predefined=False, host_key=None, host_uid=None,
                               player_side=0)
        return RosterSnapshot(frame=self.frame, entries=(entry,),
                              present_unique_ids=frozenset({1}), present_known=True)


class Observer:
    def __init__(self, collector):
        self.collector = collector
        self.sequence = 0
        self.epoch = 0
        self.events = []
        self.error = None
        self.shield_unknown = False
        self.damage_unknown = False
        self.engagement_unknown = False
        self.unknown_sources = []
        self.engagement_unknown_sources = []
        self.marks = []

    def mark(self):
        if self.marks:
            return self.marks.pop(0)
        return {'sequence': self.sequence, 'epoch': self.epoch, 'active': 0,
                'frame': self.collector.frame, 'game_time': self.collector.game_time,
                'error': self.error, 'stopped': False,
                'shield_unknown': self.shield_unknown,
                'damage_unknown': self.damage_unknown,
                'engagement_unknown': self.engagement_unknown,
                'unknown_sources': list(self.unknown_sources),
                'engagement_unknown_sources': list(self.engagement_unknown_sources)}

    def drain(self):
        events, self.events = self.events, []
        return {'events': events, 'error': self.error, 'stopped': False,
                'shield_unknown': self.shield_unknown,
                'damage_unknown': self.damage_unknown,
                'engagement_unknown': self.engagement_unknown,
                'unknown_sources': list(self.unknown_sources),
                'engagement_unknown_sources': list(self.engagement_unknown_sources)}


def event(sequence, frame=1, game_time=0, amount=20):
    return {'sequence': sequence, 'frame': frame, 'game_time': game_time,
            'source': 'char_a', 'kind': 'damage', 'amount': amount}


def test_baseline_event_and_replay_keep_snapshot_authoritative():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.events = [event(1)]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    row = capture.sample()['rows']['char_a']
    assert row['damage'] == 120 and row['observed_damage'] == 20
    observer.events = [event(1)]
    assert capture.sample()['rows']['char_a']['observed_damage'] == 20


def test_missing_event_closes_coverage_at_last_watermark():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 2
    observer.events = [event(2)]
    collector.frame, collector.game_time = 2, 1
    with pytest.raises(StreamGap):
        capture.sample()
    row = capture.adapter.result()['rows']['char_a']
    assert row['event_coverage_end'] == 0
    assert not row['event_coverage_complete']


def test_post_barrier_event_is_buffered_until_its_snapshot():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    # This record arrived after the last mark and must wait for the next frame.
    observer.events = [event(1, frame=2, game_time=1)]
    assert capture.sample()['rows']['char_a']['observed_damage'] == 0
    observer.sequence = observer.epoch = 1
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    assert capture.sample()['rows']['char_a']['observed_damage'] == 20


def test_replay_from_before_baseline_is_rejected_after_initial_drain():
    collector = Collector()
    observer = Observer(collector)
    observer.sequence = observer.epoch = 5
    observer.events = [event(5)]
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.events = [event(5)]
    with pytest.raises(StreamGap):
        capture.sample()


def test_callback_during_baseline_retries_without_claiming_coverage():
    collector = Collector()
    observer = Observer(collector)
    before = observer.mark()
    observer.marks = [before, {**before, 'epoch': 1}]
    capture = LiveEventCapture(collector, observer)
    with pytest.raises(CaptureRetry):
        capture.sample()
    assert not capture.adapter.active
    assert not capture.closed


def test_battle_change_closes_coverage():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.session = 'another battle'
    with pytest.raises(BattleChanged):
        capture.sample()
    assert capture.closed


def test_observer_loss_freezes_events_but_native_snapshot_can_continue():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.error = 'observer lost'
    with pytest.raises(StreamGap):
        capture.sample()
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    result = capture.snapshot()['rows']['char_a']
    assert result['damage'] == 120 and result['event_coverage_end'] == 0
    assert not result['event_coverage_complete']


def test_no_observer_requires_no_frida_and_keeps_event_metrics_unknown():
    capture = LiveEventCapture(Collector())
    row = capture.sample()['rows']['char_a']
    assert row['damage'] == 100 and row['shield_absorbed'] is None


@pytest.mark.parametrize('where,error', [('read', SampleUnavailable('cross frame')),
                                      ('read_roster', RosterUnavailable('cross frame'))])
def test_temporary_frame_failure_preserves_queued_events(monkeypatch, where, error):
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.events = [event(1)]
    original = getattr(collector, where)
    def fail_once():
        raise error
    monkeypatch.setattr(collector, where, fail_once)
    with pytest.raises(CaptureRetry):
        capture.sample()
    assert not capture.closed and observer.events == [event(1)]
    monkeypatch.setattr(collector, where, original)
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    result = capture.sample()['rows']['char_a']
    assert result['observed_damage'] == 20 and result['event_coverage_complete']


def test_real_battle_change_does_not_retry(monkeypatch):
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    def changed():
        raise BattleChanged('game process changed')
    monkeypatch.setattr(collector, 'read', changed)
    with pytest.raises(BattleChanged):
        capture.sample()
    assert capture.closed


@pytest.mark.parametrize('after_snapshot', [False, True])
def test_observer_clock_race_preserves_queued_events(after_snapshot):
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.events = [event(1)]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    marks = [observer.mark()] if after_snapshot else []
    observer.marks = marks + [{'retry': True, 'error': None, 'stopped': False}]
    with pytest.raises(CaptureRetry, match='clock advanced'):
        capture.sample()
    assert not capture.closed
    assert observer.events == [event(1)]
    row = capture.sample()['rows']['char_a']
    assert row['observed_damage'] == 20 and row['event_coverage_complete']


def test_clock_retry_does_not_hide_a_real_observer_error():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.marks = [{'retry': True, 'error': 'callback failed', 'stopped': False}]
    with pytest.raises(StreamGap):
        capture.sample()
    assert capture.closed


def test_probe_overkill_evidence_reaches_the_published_result():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.events = [{**event(1, amount=200),
                        'target': 'enemy_1046_agent', 'real_delta': -44,
                        'hp_before': 44., 'hp_after': 0., 'killed': True,
                        'target_type': 0, 'damage_type': 1,
                        'cancel_reason': 0, 'shield_absorbed': 0.}]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    row = capture.sample()['rows']['char_a']
    assert row['damage'] == 120 and row['observed_damage'] == 200
    assert row['overkill_damage'] == 156
    assert row['overkill_coverage_complete'] is True


def test_lethal_hit_without_verified_absorption_context_stays_unknown():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.events = [{**event(1, amount=200),
                        'target': 'enemy_1046_agent', 'real_delta': -44,
                        'hp_before': 44., 'hp_after': 0., 'killed': True,
                        'target_type': 0, 'damage_type': 1,
                        'cancel_reason': 0, 'shield_absorbed': None}]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    row = capture.sample()['rows']['char_a']
    assert row['event_coverage_complete'] is True
    assert row['damage'] == 120 and row['observed_damage'] == 200
    assert row['overkill_damage'] is None


def test_shield_gap_keeps_damage_and_overkill_without_closing_events():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.shield_unknown = True
    observer.events = [{**event(1, amount=200),
                        'target': 'enemy_1046_agent', 'real_delta': -44,
                        'hp_before': 44., 'hp_after': 0., 'killed': True,
                        'target_type': 0, 'damage_type': 1,
                        'cancel_reason': 0, 'shield_absorbed': 0.}]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    row = capture.sample()['rows']['char_a']
    assert row['shield_absorbed'] is None
    assert row['shield_unknown'] is True
    assert row['event_coverage_complete'] is True
    assert row['observed_damage'] == 200
    assert row['overkill_damage'] == 156


def test_dropped_event_hides_engagement_without_closing_coverage():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.engagement_unknown_sources = ['char_a']
    observer.events = [event(1, amount=20)]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    row = capture.sample()['rows']['char_a']
    assert row['event_coverage_complete'] is True
    assert row['observed_damage'] is None
    assert row['engagement_damage'] is None
    assert row['active_damage'] is None
    assert row['observed_healing'] is None


def test_unattributed_event_hides_team_engagement_and_overkill():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.damage_unknown = True
    observer.engagement_unknown = True
    observer.events = [event(1, amount=20)]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    row = capture.sample()['rows']['char_a']
    assert row['observed_damage'] is None
    assert row['overkill_damage'] is None


def test_unknown_source_keeps_coverage_and_only_hides_its_overkill():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.unknown_sources = ['char_a']
    observer.events = [event(1, amount=20)]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    row = capture.sample()['rows']['char_a']
    assert row['event_coverage_complete'] is True
    assert row['observed_damage'] == 20
    assert row['overkill_damage'] is None
