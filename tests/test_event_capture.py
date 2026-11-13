import pytest
from pathlib import Path

from event_capture import CaptureRetry, LiveEventCapture, read_terminal_mark
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


def test_terminal_mark_uses_optional_bounded_observer_method():
    observer = Observer(Collector())
    assert read_terminal_mark(observer)['frame'] == 1
    terminal_mark = {'terminal_boundary': {'frame': 2}}
    observer.read_terminal_mark = lambda: terminal_mark
    assert read_terminal_mark(observer) is terminal_mark


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


def test_formula_pair_flows_through_damage_queue_without_using_hp_delta():
    collector = Collector()
    collector.damage = 0
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time, collector.damage = 2, 1, 170
    first = dict(event(1, frame=2, game_time=1, amount=20),
                 target='enemy_a', mitigation_before=100, mitigation_after=20,
                 hp_before=20, hp_after=0, real_delta=-20, killed=True)
    second = dict(event(2, frame=2, game_time=1, amount=150),
                  target='enemy_b', mitigation_before=150, mitigation_after=150,
                  hp_before=15, hp_after=0, real_delta=-15, killed=True,
                  shield_absorbed=30)
    observer.events = [first, second]
    observer.sequence = observer.epoch = 2
    row = capture.sample()['rows']['char_a']

    assert row['damage'] == row['observed_damage'] == row['mitigation_after'] == 170
    assert row['mitigation_before'] == 250
    assert row['mitigation_rate'] == pytest.approx(32)
    assert row['mitigation_unknown_reason'] is None
    assert row['observed_mitigation_rate'] == pytest.approx(32)
    assert row['mitigation_matched_hits'] == 2
    observer.events = [first, second]
    replayed = capture.sample()['rows']['char_a']
    assert replayed['mitigation_rate'] == pytest.approx(32)
    assert replayed['mitigation_matched_hits'] == 2

    capture.adapter.on_end()
    assert capture.adapter.result()['rows']['char_a']['mitigation_rate'] == pytest.approx(32)


@pytest.mark.parametrize('fields', [
    {}, {'mitigation_before': 45},
    {'mitigation_before': True, 'mitigation_after': 20},
    {'mitigation_before': float('inf'), 'mitigation_after': 20},
    {'mitigation_before': 10 ** 500, 'mitigation_after': 20},
    {'mitigation_before': 45, 'mitigation_after': 15},
])
def test_missing_or_invalid_formula_pair_does_not_break_damage_events(fields):
    collector = Collector()
    collector.damage = 0
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time, collector.damage = 2, 1, 40
    observer.events = [dict(event(1, frame=2, game_time=1),
                            mitigation_before=35, mitigation_after=20),
                       dict(event(2, frame=2, game_time=1), **fields)]
    observer.sequence = observer.epoch = 2
    row = capture.sample()['rows']['char_a']

    assert row['damage'] == row['observed_damage'] == 40
    assert row['event_coverage_complete']
    assert row['mitigation_rate'] is None
    assert row['mitigation_unknown_reason'] == '部分伤害缺少同次命中的公式前后值'
    assert row['observed_mitigation_before'] == 35
    assert row['observed_mitigation_after'] == 20
    assert row['observed_mitigation_rate'] == pytest.approx((1 - 20/35) * 100)
    assert row['mitigation_matched_hits'] == 1


def test_pre_baseline_native_damage_cannot_be_inferred_from_subsequent_formula_pairs():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    observer.events = [dict(event(1, frame=2, game_time=1),
                            mitigation_before=40, mitigation_after=20)]
    observer.sequence = observer.epoch = 1
    row = capture.sample()['rows']['char_a']

    assert row['damage'] == 120
    assert row['mitigation_rate'] is None
    assert row['mitigation_unknown_reason'] == '公式后伤害与本局累计不一致'
    assert row['observed_mitigation_rate'] == 50
    assert row['mitigation_matched_hits'] == 1


def test_unavailable_formula_probe_only_degrades_mitigation():
    class FormulaUnavailable(Observer):
        def drain(self):
            return {**super().drain(), 'mitigation_ready': False}

    collector = Collector()
    collector.damage = 0
    observer = FormulaUnavailable(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time, collector.damage = 2, 1, 20
    observer.events = [dict(event(1, frame=2, game_time=1),
                            mitigation_before=40, mitigation_after=20)]
    observer.sequence = observer.epoch = 1
    row = capture.sample()['rows']['char_a']

    assert row['event_coverage_complete']
    assert row['observed_damage'] == 20
    assert row['mitigation_rate'] is None
    assert row['mitigation_unknown_reason'] == '伤害公式回调覆盖不足'
    assert row['observed_mitigation_rate'] is None
    assert row['mitigation_matched_hits'] == 0


def test_enemy_settlement_reuses_the_ordered_queue_without_changing_native_totals():
    class EnemyDamageObserver(Observer):
        def drain(self):
            return {**super().drain(), 'enemy_damage_ready': True}

    collector = Collector()
    observer = EnemyDamageObserver(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    settled = dict(event(1, frame=2, game_time=1), kind='enemy_damage',
                   target='enemy_a', target_uid='10', target_instance='0x1', damage_type=2)
    bar = dict(event(2, frame=2, game_time=1), kind='elemental_bar', target_instance='0x1')
    burst = dict(settled, sequence=3, source=None, damage_type=5, amount=15)
    unknown = dict(burst, sequence=4, target_uid='20', target_instance='0x2', amount=11)
    observer.events = [settled, bar, burst, unknown, dict(event(5, frame=2, game_time=1),
                                                       target='enemy_a', hp_after=20, killed=False)]
    observer.sequence = observer.epoch = 5

    result = capture.sample()
    assert result['enemy_damage'] == {
        'complete': False, 'by_uid': {'10': {'eid': 'enemy_a', 'amount': 35.0}},
    }
    assert result['rows']['char_a']['damage'] == 120
    assert result['rows']['char_a']['observed_damage'] == 20
    assert result['rows']['char_a']['event_coverage_complete']
    observer.events = [settled]
    assert capture.sample()['enemy_damage'] == result['enemy_damage']


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


def test_clean_battle_end_closes_coverage_as_complete():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 1
    observer.events = [event(1)]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    capture.sample()
    capture.adapter.on_end()
    row = capture.adapter.result()['rows']['char_a']
    assert row['event_coverage_end'] is not None
    assert row['event_coverage_complete'] is True
    assert row['skill_split_reason'] != '事件覆盖已结束'


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


def test_probe_records_hp_evidence_only_after_onleave():
    js = (Path(__file__).resolve().parents[1] / 'event_probe_android2791.js').read_text('utf-8')
    start = js.index('for(const m of cfg.methods.filter(')
    production = js[start:js.index('\ninstallEnemyEntryHooks();', start)]
    enter, leave = production.split('onLeave(){', 1)
    assert 'hp_damage' not in enter
    assert 'recordHpContext(' not in enter
    assert leave.index('const end=time()') < leave.index("record(this.source,'hp_damage'")
    assert leave.index('const end=time()') < leave.index('recordHpContext(')


def test_probe_recovers_targetless_damage_from_the_same_modifier_invocation():
    js = (Path(__file__).resolve().parents[1] / 'event_probe_android2791.js').read_text('utf-8')
    assert 'scopes[index].mod.equals(modifier)' in js
    assert 'scope.source===this.source&&scope.when.frame===this.when.frame' in js
    assert 'if(scope.targetHit)scope.targetHitAmbiguous=true' in js
    assert 'overkillEvidence(this.mod,hit.target,hit.hpBefore,end.frame)' in js
    assert 'settlement&&settlement.amount===amount' in js
    assert 'if(this.tracksModifier)leaveModifier(this)' in js
    assert 'lastBarTarget' not in js
    assert "record(this.source,'hp_damage'" in js
    # Target-side HP evidence only describes our own outgoing damage. Enemy
    # attacks on operators are not stream events; emitting them closed the
    # whole authoritative stream before this guard existed.
    hp_guard = js[js.index("if(this.name==='Unit.OnTakeDamage'&&this.source&&own(this.source)"):]
    assert hp_guard.index("record(this.source,'hp_damage'") < hp_guard.index('const hpAfter=targetHp(this.target)')


def test_elemental_bar_and_sourceless_burst_reach_attribution():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    observer.sequence = observer.epoch = 2
    observer.events = [
        {'sequence': 1, 'frame': 2, 'game_time': 1, 'source': 'char_a',
         'kind': 'elemental_bar', 'amount': 308.88, 'target_instance': 'target-1'},
        {'sequence': 2, 'frame': 2, 'game_time': 1, 'source': None,
         'kind': 'elemental_burst', 'amount': 6000, 'target': 'enemy_1046_agent',
         'target_instance': 'target-1', 'real_delta': -784, 'hp_before': 784,
         'hp_after': 0, 'killed': True},
    ]
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    row = capture.sample()['rows']['char_a']
    assert row['burst_damage'] == 784
    assert row['burst_overkill'] == 5216
    assert row['damage'] == pytest.approx(120 + 6000)
    assert row['effective_damage'] == pytest.approx(120 + 784)


@pytest.mark.parametrize('burst_source, scoped_settlement', [
    (None, True),       # Native source-null burst, scoped before team attribution.
    ('char_a', True),   # Native source-owned burst, scoped by the output callback.
    (None, False),      # Legacy wire cannot supply the missing native target UID.
])
def test_elemental_burst_counts_actual_enemy_hp_once(burst_source, scoped_settlement):
    class EnemyDamageObserver(Observer):
        def drain(self):
            return {**super().drain(), 'enemy_damage_ready': True}

    collector = Collector()
    observer = EnemyDamageObserver(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time = 2, 1
    collector.damage = 120 if burst_source is None else 140
    records = [
        dict(event(1, frame=2, game_time=1), kind='elemental_bar', target_instance='0x1'),
    ]
    if scoped_settlement:
        records.append(dict(event(2, frame=2, game_time=1), kind='enemy_damage',
                            source=burst_source, amount=9, target='enemy_a',
                            target_instance='0x1', target_uid='10', damage_type=5))
    records.append(dict(event(len(records) + 1, frame=2, game_time=1), kind='elemental_burst',
                        source=burst_source, target='enemy_a', target_instance='0x1',
                        real_delta=-9, hp_before=9, hp_after=0, killed=True, damage_type=5))
    observer.sequence = observer.epoch = len(records)
    for _ in range(2):
        observer.events = records.copy()
        result = capture.sample()
        assert result['enemy_damage']['by_uid'] == (
            {'10': {'eid': 'enemy_a', 'amount': 9}} if scoped_settlement else {})
        row = result['rows']['char_a']
        assert row['effective_damage'] == 129
        assert row['burst_damage'] == 9


def test_probe_hooks_character_skill_windows_and_keeps_them_out_of_onenter():
    js = (Path(__file__).resolve().parents[1] / 'event_probe_android2791.js').read_text('utf-8')
    assert "wanted=new Set([" in js and "'Character'" in js and "'BasicSkill'" in js
    assert "'skill_start'" in js and "'skill_end'" in js
    assert 'OnSkillStart' in js and 'OnSkillFinish' in js and 'OnAfterSkill' in js
    enter = js[js.index('onEnter(args){'):js.index('onLeave(){')]
    leave = js[js.index('onLeave(){'):]
    assert "record(this.source,this.skillStart?'skill_start':'skill_end'" not in enter
    assert "record(this.source,this.skillStart?'skill_start':'skill_end'" in leave
    assert 'skill_unknown' in leave and 'skillUnknown' in js


def test_enemy_entries_share_the_watermark_and_ignore_drain_replays():
    collector = Collector()
    observer = Observer(collector)
    drain = observer.drain
    flags = {'enemy_entry_ready': True, 'enemy_entry_unknown': False}
    observer.drain = lambda: {**drain(), **flags}
    capture = LiveEventCapture(collector, observer)

    observer.sequence = observer.epoch = 1
    observer.events = [{**event(1), 'kind': 'enemy_enter', 'source': None,
                        'amount': 0, 'target': 'enemy_a', 'target_instance': '1'}]
    assert capture.sample()['enemy_entry'] == {'complete': True, 'start_frame': 1, 'events': []}

    first = {**event(2, frame=2, game_time=1, amount=0), 'kind': 'enemy_enter',
             'source': None, 'target': 'enemy_a', 'target_instance': '2'}
    second = {**first, 'sequence': 3, 'target_instance': '3'}
    collector.frame, collector.game_time, collector.damage = 2, 1, 120
    observer.sequence = observer.epoch = 4
    observer.events = [first, second, event(4, frame=2, game_time=1)]
    result = capture.sample()
    assert result['enemy_entry'] == {
        'complete': True,
        'start_frame': 1,
        'events': [
            {'instance_uid': '2', 'eid': 'enemy_a', 'frame': 2, 'game_time': 1},
            {'instance_uid': '3', 'eid': 'enemy_a', 'frame': 2, 'game_time': 1},
        ],
    }
    assert result['rows']['char_a']['observed_damage'] == 20
    assert capture.adapter.event_stream.sequence == 4

    observer.events = [first, second]
    assert capture.sample()['enemy_entry'] == result['enemy_entry']
    flags['enemy_entry_unknown'] = True
    result = capture.sample()
    assert not result['enemy_entry']['complete']
    assert result['rows']['char_a']['event_coverage_complete']


def test_enemy_entries_keep_the_verified_prefix_when_stream_closes():
    collector = Collector()
    observer = Observer(collector)
    drain = observer.drain
    observer.drain = lambda: {**drain(), 'enemy_entry_ready': True}
    capture = LiveEventCapture(collector, observer)
    capture.sample()

    collector.frame, collector.game_time = 2, 1
    observer.sequence = observer.epoch = 2
    observer.events = [{**event(1, frame=2, game_time=1, amount=0), 'kind': 'enemy_enter',
                        'source': None, 'target': 'enemy_a', 'target_instance': '1'}]
    with pytest.raises(StreamGap):
        capture.sample()
    result = capture.adapter.result()
    assert result['enemy_entry'] == {
        'complete': False,
        'start_frame': 1,
        'events': [{'instance_uid': '1', 'eid': 'enemy_a', 'frame': 2, 'game_time': 1}],
    }


def test_enemy_entry_result_is_absent_without_an_observer_capability():
    collector = Collector()
    capture = LiveEventCapture(collector, Observer(collector))
    assert 'enemy_entry' not in capture.sample()


def test_enemy_entry_capability_failure_does_not_affect_damage_coverage():
    collector = Collector()
    observer = Observer(collector)
    drain = observer.drain
    observer.drain = lambda: {**drain(), 'enemy_entry_ready': False}
    result = LiveEventCapture(collector, observer).sample()
    assert result['enemy_entry'] == {'complete': False, 'start_frame': 1, 'events': []}
    assert result['rows']['char_a']['event_coverage_complete']


def test_capture_accepts_rebirth_evidence_and_marks_an_unknown_phase_prefix():
    collector = Collector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    assert 'enemy_phases' not in capture.sample()
    collector.frame, collector.game_time = 2, 1
    observer.sequence = observer.epoch = 1
    observer.events = [{**event(1, frame=2, game_time=1, amount=60_000),
                        'kind': 'enemy_reborn', 'source': None, 'target': 'enemy_1506_patrt',
                        'target_instance': '7', 'hp_before': 0, 'hp_after': 60_000,
                        'phase_total_hp': 120_000, 'phase_count': 2, 'phase_from_birth': True}]
    assert capture.sample()['enemy_phases']['complete']

    drain = observer.drain
    observer.drain = lambda: {**drain(), 'enemy_phase_unknown': True}
    result = capture.sample()
    assert result['enemy_phases'] == {
        'complete': False,
        'instances': {'7': {'eid': 'enemy_1506_patrt', 'frame': 2, 'game_time': 1,
                            'phase_total_hp': 120_000, 'phase_count': 2,
                            'phase_from_birth': True}},
    }
    assert result['rows']['char_a']['event_coverage_complete']


@pytest.mark.parametrize('prefab_key', ['skchr_angel2_3', 'prefab_alias'])
def test_ammo_cast_completion_leaves_only_that_skill_window_unknown(prefab_key):
    class TeamCollector(Collector):
        def read(self):
            sample = super().read()
            sample['rows'].append({
                'id': 'char_1041_angel2', 'damage': 15, 'healing': 0,
                'physical': 15, 'arts': 0, 'true': 0, 'elemental': 0,
            })
            return sample

        def read_roster(self):
            original = super().read_roster()
            angel = CharacterEntry(cid='char_1041_angel2', unique_id=2, is_token=False,
                                   is_predefined=False, host_key=None, host_uid=None,
                                   player_side=0)
            return RosterSnapshot(frame=self.frame, entries=(*original.entries, angel),
                                  present_unique_ids=frozenset({1, 2}), present_known=True)

    collector = TeamCollector()
    observer = Observer(collector)
    capture = LiveEventCapture(collector, observer)
    capture.sample()

    def skill(sequence, frame, source, kind, method, key, reason=None, skill_id=None):
        return {**event(sequence, frame=frame, game_time=frame - 1, amount=1),
                'source': source, 'kind': kind, 'skill_key': key, 'skill_id': skill_id,
                'skill_reason': reason, 'proof': {'method': method}}

    observer.events = [
        skill(1, 2, 'char_a', 'skill_start', 'Character.SwitchToSkillState', 'regular'),
        skill(2, 2, 'char_1041_angel2', 'skill_start', 'Character.SwitchToSkillState',
              'skchr_angel2_3'),
        event(3, frame=2, game_time=1, amount=10),
        skill(4, 3, 'char_a', 'skill_end', 'Character.OnAfterSkill', 'regular', 1),
        skill(5, 3, 'char_1041_angel2', 'skill_end', 'Character.OnAfterSkill',
              prefab_key, 1, 'skchr_angel2_3'),
        skill(6, 3, 'char_1041_angel2', 'skill_end', 'Character.OnAfterSkill',
              prefab_key, 1, 'skchr_angel2_3'),
        skill(7, 3, 'char_a', 'skill_end', 'Character.OnSkillFinish', 'regular'),
        {**event(8, frame=4, game_time=3, amount=15), 'source': 'char_1041_angel2'},
        event(9, frame=4, game_time=3, amount=5),
    ]
    collector.frame, collector.game_time, collector.damage = 4, 3, 115
    observer.sequence = observer.epoch = len(observer.events)
    rows = capture.sample()['rows']
    ammo = rows['char_1041_angel2']
    assert ammo['event_coverage_complete']
    assert ammo['counter_snapshot_complete']
    assert ammo['damage'] == 15
    assert ammo['observed_damage'] == 15
    assert ammo['skill_window_count'] == 1
    assert ammo['skill_active_seconds'] is None
    assert ammo['skill_damage'] is None and ammo['non_skill_damage'] is None
    assert ammo['skill_damage_effective'] is None and ammo['non_skill_damage_effective'] is None
    assert not ammo['skill_coverage_complete']
    assert not ammo['skill_split_complete']
    assert '弹药技能' in ammo['skill_split_reason']
    regular = rows['char_a']
    assert regular['skill_coverage_complete'] and regular['skill_split_complete']
    assert regular['skill_active_seconds'] == 1
    assert regular['skill_damage'] == 10 and regular['non_skill_damage'] == 5
    assert capture.adapter.event_stream.sequence == 9
