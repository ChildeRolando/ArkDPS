from copy import deepcopy
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from battle_lifecycle import (BattleFeed, BattleObservation, BattleRuntime,
                              BattleSessions, BattleView, MappingRetry)
from roster import CharacterEntry, RosterSnapshot
from snapshot_adapter import FrameMismatch, MappingUnavailable
from event_capture import CaptureRetry


class FakeCollector:
    def __init__(self):
        self.core = SimpleNamespace(mc=SimpleNamespace(process_instance=('boot', 1), pid=10))
        self.lifecycle = dict(battle=(100, 200), ready=True, transiting=False,
                              source='level_a', target='', state=2)
        self.sample = dict(session='addresses', frame=10, game_time=10.,
                           battle_identity=(100, 200), state=2,
                           captured_monotonic=1., rows=[dict(
                               id='a', name='A', damage=100., healing=50.,
                               physical=100., arts=0., true=0., elemental=0.)])
        self.roster = RosterSnapshot(10, (CharacterEntry('a', 1, False, False,
                                                       None, None, 0),), frozenset({1}), True)
        self.after = None
        self.reads = 0

    def read_lifecycle(self):
        self.reads += 1
        return deepcopy(self.after if self.after is not None and self.reads % 2 == 0
                        else self.lifecycle)

    def bind_battle(self, address):
        assert address == self.lifecycle['battle'][0]

    def read(self):
        return dict(deepcopy(self.sample), state=self.lifecycle['state'])

    def read_roster(self):
        return self.roster

    def advance(self, time=11., frame=11, damage=150.):
        self.sample.update(game_time=time, frame=frame)
        self.sample['rows'][0].update(damage=damage, physical=damage)
        self.roster = replace(self.roster, frame=frame)


def begin():
    c, r = FakeCollector(), BattleRuntime()
    observation = r.poll(c)
    return c, r, observation


def total(runtime):
    return runtime.sessions.live()['statistics']['rows']['a']['damage']


@pytest.mark.parametrize('change', ['same_map', 'different_map', 'native_reuse', 'process'])
def test_new_battles_archive_old_statistics_and_start_new_generation(change):
    c, r, first = begin()
    c.advance(time=1., frame=1, damage=5.)
    if change == 'different_map':
        c.sample['session'] = 'other_level'
    if change == 'native_reuse':
        c.lifecycle['battle'] = (100, 300)
        c.sample['battle_identity'] = (100, 300)
    if change == 'process':
        c.core.mc.process_instance = ('boot', 2)
    second = r.poll(c)
    assert second.generation == first.generation + 1
    assert total(r) == 5.
    archive, = r.sessions.pop_archives()
    assert archive['statistics']['rows']['a']['damage'] == 100.
    assert archive['statistics']['ended']
    assert archive['battle_id'] != r.sessions.battle_id
    assert not r.sessions.pop_archives()
    archive['statistics']['rows']['a']['damage'] = 500.
    assert total(r) == 5.


def test_pause_and_retreat_redeployment_never_clear_or_archive():
    c, r, first = begin()
    for present in (frozenset(), frozenset({1})):
        c.roster = replace(c.roster, present_unique_ids=present)
        assert r.poll(c).generation == first.generation
        assert total(r) == 100.
        assert not r.sessions.pop_archives()
    c.advance()
    r.poll(c)
    assert total(r) == 150.
    assert r.sessions.generation == first.generation


def test_torn_lifecycle_binding_retries_without_losing_battle(monkeypatch):
    from tools.stage_global_state.memory import ReadFailure

    collector, runtime, first = begin()

    def unavailable():
        raise ReadFailure('binding_invalid')

    with monkeypatch.context() as patch:
        patch.setattr(collector, 'read_lifecycle', unavailable)
        with pytest.raises(CaptureRetry, match='binding_invalid'):
            runtime.poll(collector)
    assert not runtime.sessions.pop_archives()
    assert runtime.poll(collector).generation == first.generation


def test_busy_event_frames_retry_inside_lifecycle_bracket_without_consuming(monkeypatch):
    import battle_lifecycle

    c, runtime = FakeCollector(), BattleRuntime()
    monkeypatch.setattr(battle_lifecycle, 'time', SimpleNamespace(monotonic=lambda: 0.))
    mark = dict(sequence=0, epoch=0, active=0, frame=10, game_time=10.)
    marks = iter([
        dict(mark, frame=9, game_time=9.),  # Already older than guarded counters.
        mark, dict(mark, epoch=1),        # Crossed a callback while reading roster.
        mark, mark,                      # Re-read all inputs inside a fresh pair.
    ])
    reads, acks = [], []
    roster_reads = []
    read_roster = c.read_roster

    def roster():
        roster_reads.append(True)
        return read_roster()

    def drain(sequence):
        assert c.reads == 2
        reads.append(sequence)
        return dict(events=[])

    monkeypatch.setattr(c, 'read_roster', roster)
    observer = SimpleNamespace(mark=lambda: next(marks), read_through=drain,
                               commit_events=acks.append)
    observation = runtime.poll(c, observer)
    assert next(marks, None) is None
    assert observation.events['before'] == observation.events['after'] == mark
    assert runtime.sessions.event_status == 'recording'
    assert len(roster_reads) == 2
    assert reads == acks == [0]
    assert c.reads == 2


@pytest.mark.parametrize('elapsed, expected_marks', [(0., 3), (.05, 1)])
def test_busy_event_frames_leave_uncommitted_batch_and_last_sample_untouched(
        monkeypatch, elapsed, expected_marks):
    import battle_lifecycle

    c, runtime, baseline = begin()
    c.advance()
    times = iter([0., elapsed])
    monkeypatch.setattr(battle_lifecycle, 'time',
                        SimpleNamespace(monotonic=lambda: next(times, elapsed)))
    marks = []
    events = []

    def mark():
        marks.append(True)
        return dict(sequence=2, epoch=3, active=1, frame=11, game_time=11.,
                    active_hooks=['Entity.ApplyModifier'])

    observer = SimpleNamespace(mark=mark, read_through=events.append,
                               commit_events=events.append)
    with pytest.raises(CaptureRetry, match='callback is in flight'):
        runtime.poll(c, observer)
    assert len(marks) == expected_marks
    assert events == []
    assert runtime.feed.sequence == baseline.sequence
    assert runtime.sessions.latest_sample == baseline.sample


def test_successful_frame_retry_still_requires_closing_lifecycle_check(monkeypatch):
    import battle_lifecycle

    c, runtime, baseline = begin()
    c.advance()
    c.reads = 0
    c.after = dict(c.lifecycle, battle=(100, 300))
    monkeypatch.setattr(battle_lifecycle, 'time', SimpleNamespace(monotonic=lambda: 0.))
    mark = dict(sequence=1, epoch=1, active=0, frame=11, game_time=11.)
    marks = iter([dict(mark, active=1), mark, mark])
    events = []
    observer = SimpleNamespace(mark=lambda: next(marks), read_through=events.append,
                               commit_events=events.append)
    with pytest.raises(CaptureRetry, match='生命周期'):
        runtime.poll(c, observer)
    assert next(marks, None) is None
    assert c.reads == 2
    assert events == []
    assert runtime.sessions.latest_sample == baseline.sample


def test_abandon_between_bracket_reads_is_preserved_in_single_archive():
    collector, runtime, first = begin()
    collector.reads = 0
    collector.after = dict(collector.lifecycle, transiting=True, target='battle_finish',
                           metadata={'outcome': 'abandoned', 'evidence': 'native result and transition'})
    observation = runtime.poll(collector)
    assert observation.kind == 'end'
    archive, = runtime.sessions.pop_archives()
    assert archive['battle_id'] == first.sample['session']
    assert archive['metadata']['outcome'] == 'abandoned'
    collector.lifecycle['battle'] = None
    collector.after = None
    runtime.poll(collector)
    assert not runtime.sessions.pop_archives()


def test_delayed_old_sample_cannot_reopen_or_mutate_new_battle():
    c, r, first = begin()
    c.advance(time=1., frame=1, damage=5.)
    second = r.poll(c)
    assert not r.sessions.consume(first)
    # Even an old generation accidentally given a newer queue sequence is stale.
    assert not r.sessions.consume(replace(first, sequence=second.sequence + 10))
    assert total(r) == 5.
    assert r.sessions.generation == second.generation
    assert len(r.sessions.pop_archives()) == 1


@pytest.mark.parametrize('ending', ['absent', 'finished', 'finish_flow'])
def test_each_end_signal_archives_once_and_never_infers_a_victory(ending):
    c, r, first = begin()
    if ending == 'absent':
        c.lifecycle['battle'] = None
    elif ending == 'finished':
        c.lifecycle.update(state=3, ready=False)
        c.advance(damage=175.)
    else:
        c.lifecycle.update(transiting=True, target='battle_finish')
    end = r.poll(c)
    r.sessions.consume(end)
    r.poll(c)
    if ending == 'finished':
        assert r.sessions.state == 'finalizing'
        assert not r.sessions.pop_archives()
        c.lifecycle['battle'] = None
        r.poll(c)
    archive, = r.sessions.pop_archives()
    assert archive['metadata']['outcome'] == 'unknown'
    assert archive['metadata']['practice'] is None
    expected = 175. if ending == 'finished' else 100.
    assert archive['statistics']['rows']['a']['damage'] == expected
    assert r.sessions.state == 'ended'
    assert not r.sessions.consume(replace(first, sequence=100))
    assert not r.sessions.pop_archives()


def test_observed_absence_allows_a_new_battle_with_all_addresses_and_clock_reused():
    c, r, first = begin()
    c.lifecycle['battle'] = None
    r.poll(c)
    c.lifecycle['battle'] = (100, 200)
    second = r.poll(c)
    assert second.generation > first.generation
    assert second.sample['session'] != first.sample['session']
    assert len(r.sessions.pop_archives()) == 1


def test_waiting_signal_keeps_stage_through_settlement_and_clears_it_in_lobby():
    collector, runtime, _ = begin()
    collector.lifecycle.update(source='home', target='battle_loader')
    runtime.poll(collector)
    assert runtime.feed.battle_waiting is False

    collector.lifecycle.update(source='level_a', target='battle_finish', transiting=True)
    runtime.poll(collector)
    assert runtime.feed.battle_waiting is False
    collector.lifecycle.update(battle=None, state=None, transiting=False)
    runtime.poll(collector)
    assert runtime.feed.battle_waiting is False
    collector.lifecycle.update(source='battle_finish', target='home')
    runtime.poll(collector)
    assert runtime.feed.battle_waiting is True
    assert runtime.sessions.state == 'ended'
    assert runtime.display_sample()['rows'][0]['damage'] == 100.
    assert len(runtime.sessions.pop_archives()) == 1
    runtime.feed.gap()
    assert runtime.feed.battle_waiting is None


def test_finished_object_is_not_reopened_while_scene_tears_down():
    c, r, _ = begin()
    c.lifecycle.update(transiting=True, target='battle_finish')
    r.poll(c)
    c.lifecycle.update(transiting=False, target='', ready=True)
    r.poll(c)
    assert r.sessions.state == 'ended'
    assert len(r.sessions.pop_archives()) == 1


def test_new_statistic_or_level_object_can_reopen_reused_native_address():
    c, r, first = begin()
    c.lifecycle.update(transiting=True, target='battle_finish')
    r.poll(c)
    c.lifecycle.update(transiting=False, target='', ready=True)
    c.sample['session'] = 'new_statistics_or_level_object'
    assert r.poll(c).generation == first.generation + 1
    assert len(r.sessions.pop_archives()) == 1


def test_transport_gap_freezes_without_archiving_or_inventing_zero():
    c, r, first = begin()
    r.disconnected('transport_lost')
    assert r.sessions.connection == 'disconnected'
    assert r.sessions.state == 'frozen'
    assert total(r) == 100.
    assert r.sessions.live()['statistics']['rows']['a']['recent_damage'] is None
    assert not r.sessions.pop_archives()
    c.advance()
    assert r.poll(c).generation == first.generation
    assert r.sessions.connection == 'connected'
    assert total(r) == 150.


@pytest.mark.parametrize('fault', ['lifecycle', 'roster', 'ownership'])
def test_unstable_pair_cannot_publish_or_advance_generation(fault):
    c, r, _ = begin()
    c.advance(time=1., frame=1, damage=5.)
    if fault == 'lifecycle':
        c.after = dict(c.lifecycle, battle=(500, 600))
    elif fault == 'roster':
        c.roster = replace(c.roster, frame=9)
    else:
        c.sample['battle_identity'] = (500, 600)
    with pytest.raises(CaptureRetry):
        r.poll(c)
    assert total(r) == 100.
    assert r.sessions.state == 'playing'
    assert r.feed.generation == 1
    assert not r.sessions.pop_archives()


@pytest.mark.parametrize('outcome', ['victory', 'defeat', 'abandoned', 'invalid'])
@pytest.mark.parametrize('practice', [True, False, None])
def test_result_and_practice_require_explicit_evidence(outcome, practice):
    c = FakeCollector()
    c.sample['battle_metadata'] = dict(stage_id='OF-1', outcome=outcome,
                                       practice=practice, evidence='fixture')
    r = BattleRuntime()
    r.poll(c)
    c.lifecycle['battle'] = None
    r.poll(c)
    record, = r.sessions.pop_archives()
    assert record['metadata']['outcome'] == (outcome if outcome != 'invalid' else 'unknown')
    assert record['metadata']['practice'] is practice


def test_unconfirmed_result_fields_stay_unknown():
    c = FakeCollector()
    c.sample['battle_metadata'] = dict(stage_id='OF-1', outcome='victory', practice=True)
    r = BattleRuntime()
    r.poll(c)
    assert r.sessions.metadata['outcome'] == 'unknown'
    assert r.sessions.metadata['practice'] is None
    assert r.sessions.metadata['stage_id'] is None


def test_final_metadata_preserves_known_stage_and_conflicting_outcomes_stay_unknown():
    c = FakeCollector()
    c.sample['battle_metadata'] = dict(stage_id='OF-1', practice=True, evidence='entry')
    r = BattleRuntime()
    r.poll(c)
    c.advance()
    c.sample['battle_metadata'] = dict(outcome='defeat', evidence='result-a')
    r.poll(c)
    assert r.sessions.metadata['stage_id'] == 'OF-1'
    assert r.sessions.metadata['practice'] is True
    assert r.sessions.metadata['outcome'] == 'defeat'
    c.advance(time=12., frame=12)
    c.sample['battle_metadata'] = dict(outcome='victory', evidence='result-b')
    r.poll(c)
    c.lifecycle.update(state=3, ready=False)
    r.poll(c)
    c.lifecycle['battle'] = None
    r.poll(c)
    record, = r.sessions.pop_archives()
    assert record['metadata']['outcome'] == 'unknown'
    assert record['metadata']['metadata_conflicts'] == ['outcome']
    assert record['metadata']['stage_id'] == 'OF-1'
    assert record['metadata']['evidence_sources'] == ['entry', 'result-a', 'result-b']


def test_gui_history_selection_is_stable_while_hud_and_live_follow_new_battle():
    c, r, _ = begin()
    c.advance(time=1., frame=1, damage=5.)
    r.poll(c)
    record, = r.sessions.pop_archives()
    r.view.show_history(record)
    c.advance(time=.5, frame=1, damage=2.)
    r.poll(c)
    assert r.view.gui() == record
    assert r.view.hud()['battle_id'] == r.sessions.battle_id
    assert r.view.hud()['statistics']['rows']['a']['damage'] == 2.
    # Neither mutable UI copies nor the caller's record can rewrite live/history.
    r.view.gui()['statistics']['rows'].clear()
    record['statistics']['rows'].clear()
    assert r.view.gui()['statistics']['rows']['a']['damage'] == 100.
    r.view.show_live()
    assert r.view.gui() == r.view.hud()


def test_bad_new_roster_preserves_old_data_and_hands_off_its_archive_immediately():
    c, r, _ = begin()
    c.advance(time=1., frame=1, damage=5.)
    c.roster = replace(c.roster, entries=())
    with pytest.raises(MappingRetry) as retry:
        r.poll(c)
    assert isinstance(retry.value.__cause__, MappingUnavailable)
    assert total(r) == 100.
    record, = r.sessions.pop_archives()
    assert record['statistics']['rows']['a']['damage'] == 100.
    c.lifecycle['battle'] = None
    r.poll(c)
    assert not r.sessions.pop_archives()


def test_foreign_feed_and_wrong_generation_tags_cannot_inject_samples():
    c, r, first = begin()
    assert not r.sessions.consume(replace(first, feed='foreign', sequence=99))
    with pytest.raises(ValueError):
        r.sessions.consume(replace(first, generation=10, sequence=100))
    assert total(r) == 100.


def test_initialization_boundary_detects_complete_address_clock_and_frame_reuse():
    c, r, first = begin()
    c.lifecycle.update(state=1, ready=False)
    r.poll(c)
    c.lifecycle.update(state=2, ready=True)
    second = r.poll(c)
    assert second.generation == first.generation + 1
    assert len(r.sessions.pop_archives()) == 1


def test_unreadable_final_snapshot_still_archives_last_reliable_data_once():
    from roster import RosterUnavailable
    c, r, _ = begin()
    c.lifecycle.update(state=3, ready=False)

    def unavailable():
        raise RosterUnavailable('destroying roster')

    c.read_roster = unavailable
    r.poll(c)
    r.poll(c)
    assert r.sessions.state == 'finalizing'
    assert not r.sessions.pop_archives()
    c.lifecycle['battle'] = None
    r.poll(c)
    record, = r.sessions.pop_archives()
    assert record['metadata']['final_sample_error'] == 'finished_snapshot_unavailable'
    assert record['statistics']['rows']['a']['damage'] == 100.
    assert record['statistics']['rows']['a']['recent_damage'] is None
    assert record['statistics']['ended']


def test_final_mapping_failure_cannot_overwrite_reliable_totals_or_lose_archive():
    c, r, _ = begin()
    c.lifecycle.update(state=3, ready=False)
    c.advance(damage=175.)
    c.sample['rows'] = [dict(c.sample['rows'][0], id='unknown')]
    r.poll(c)
    c.lifecycle['battle'] = None
    r.poll(c)
    record, = r.sessions.pop_archives()
    assert record['statistics']['rows']['a']['damage'] == 100.
    assert record['metadata']['final_sample_error']
    assert record['statistics']['ended']


def test_replay_verifies_ordered_archive_and_summary_and_rejects_corruption(tmp_path):
    from capture_battles import replay
    c = FakeCollector()
    r = BattleRuntime()
    records = [{'header': {'feed': r.feed.id}}]
    samples = archives = 0
    for now in (10., 11., 1., 2.):
        c.advance(time=now, frame=int(now), damage=now * 10)
        observation = r.poll(c)
        records.append({'observation': asdict(observation)})
        samples += 1
        for record in r.sessions.pop_archives():
            records.append({'archive_ready': record})
            archives += 1
    records.append({'summary': dict(samples=samples, archives=archives,
                                    last_state=r.sessions.live())})
    path = tmp_path / 'observations.jsonl'

    def save(values):
        path.write_text('\n'.join(json.dumps(value, default=sorted) for value in values),
                        encoding='utf-8')

    save(records)
    assert replay(path)['archives'] == 1
    save(records[:-1])
    with pytest.raises(ValueError, match='summary'):
        replay(path)
    corrupt = deepcopy(records)
    next(value for value in corrupt if 'archive_ready' in value)['archive_ready']['battle_id'] = 'wrong'
    save(corrupt)
    with pytest.raises(ValueError, match='archive differs'):
        replay(path)


def test_product_capture_emits_replayable_archives_and_cleans_up(tmp_path, monkeypatch):
    import capture_battles
    c = FakeCollector()
    c.closed = False
    c.connect = lambda **kwargs: None
    c.close = lambda: setattr(c, 'closed', True)
    c.diagnostics = SimpleNamespace(version_actual='fixture')
    clock = SimpleNamespace(now=0.)
    timer = SimpleNamespace(monotonic=lambda: clock.now,
                            sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds))

    def lifecycle():
        state = 3 if .4 <= clock.now < .6 else 2
        c.lifecycle.update(state=state, ready=state == 2,
                           battle=None if .6 <= clock.now < .8 else (100, 200))
        return deepcopy(c.lifecycle)

    c.read_lifecycle = lifecycle
    monkeypatch.setattr(capture_battles, 'Collector', lambda *args: c)
    monkeypatch.setattr(capture_battles, 'time', timer)
    summary = capture_battles.capture(1.2, tmp_path)
    result = capture_battles.replay(tmp_path / 'observations.jsonl')
    assert summary['errors'] == 0
    assert summary['archives'] == result['archives'] == 1
    assert summary['samples'] == result['samples']
    assert c.closed
    assert result['last_state']['connection'] == 'disconnected'
    assert result['last_state']['state'] == 'frozen'


def test_failed_connect_is_a_replayable_gap_and_closes_owned_collector(tmp_path, monkeypatch):
    import capture_battles
    c = FakeCollector()
    c.closed = False

    def fail(**kwargs):
        raise RuntimeError('unsupported layout')

    c.connect = fail
    c.close = lambda: setattr(c, 'closed', True)
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(capture_battles, 'Collector', lambda *args: c)
    monkeypatch.setattr(capture_battles, 'time', SimpleNamespace(
        monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds)))
    summary = capture_battles.capture(.5, tmp_path)
    assert summary['samples'] == summary['archives'] == 0
    assert summary['errors'] == 1
    assert c.closed
    assert capture_battles.replay(tmp_path / 'observations.jsonl')['archives'] == 0


def test_prototype_projection_does_not_present_partial_shares_as_full_team():
    c = FakeCollector()
    c.sample['rows'].append(dict(c.sample['rows'][0], id='unknown', damage=1000.))
    r = BattleRuntime()
    r.poll(c)
    sample = r.display_sample()
    assert sample['sum_damage'] == 100.
    assert sample['statistics_complete'] is False
    assert sample['rows'][0]['share'] is None


@pytest.mark.parametrize('exiting', [False, True])
def test_terminal_same_frame_revisions_are_kept_until_confirmed_scene_exit(exiting):
    # OF-8 native samples: 2964 / 98.79990577697754, first state=3 then a
    # later accounting revision at exactly that frame/time (host aef6051).
    c, r, _ = begin()
    c.lifecycle.update(state=3, ready=False)
    if exiting:
        c.lifecycle.update(transiting=True, target='battle_finish')
    c.advance(time=98.79990577697754, frame=2964, damage=28985.8828125)
    r.poll(c)
    c.advance(time=98.79990577697754, frame=2964, damage=31899.103515625)
    r.poll(c)
    assert total(r) == 31899.103515625
    assert r.sessions.state == 'finalizing'
    assert not r.sessions.pop_archives()
    c.lifecycle['battle'] = None
    r.poll(c)
    record, = r.sessions.pop_archives()
    assert record['statistics']['rows']['a']['damage'] == 31899.103515625
    assert record['statistics']['rows']['a']['recent_damage'] is None


@pytest.mark.parametrize('final_watermark', [True, False])
@pytest.mark.parametrize('exiting', [False, True])
def test_battle_end_keeps_open_skill_split_only_with_complete_events(final_watermark, exiting):
    c, runtime = FakeCollector(), BattleRuntime()
    c.advance(time=10., frame=10, damage=0.)
    events = []

    def mark():
        return dict(sequence=len(events), epoch=len(events), active=0,
                    frame=c.sample['frame'], game_time=c.sample['game_time'])

    observer = SimpleNamespace(mark=mark, read_through=lambda sequence: dict(
        events=deepcopy(events[:sequence])), commit_events=lambda _: None)
    runtime.poll(c, observer)

    def record(frame, kind, amount, **fields):
        events.append(dict(sequence=len(events) + 1, frame=frame, game_time=float(frame),
                           source='a', kind=kind, amount=amount, **fields))

    record(11, 'damage', 20, target='enemy_1', hp_before=100, hp_after=80,
           real_delta=-20, killed=False, damage_type=1)
    record(11, 'skill_start', 1, skill_id='skill', proof={'method': 'Character.SwitchToSkillState'})
    record(12, 'skill_end', 1, skill_id='skill', skill_reason=1,
           proof={'method': 'Character.OnAfterSkill'})
    c.advance(time=12., frame=12, damage=20.)
    runtime.poll(c, observer)
    live = runtime.sessions.live()['statistics']['rows']['a']
    assert live['skill_active_seconds'] == 1
    assert live['skill_split_effective_complete']

    record(15, 'damage', 50, target='enemy_2', hp_before=10, hp_after=0,
           real_delta=-10, killed=True, shield_absorbed=0, damage_type=1)
    c.lifecycle.update(state=3, ready=False)
    if exiting:
        c.lifecycle.update(transiting=True, target='battle_finish')
    c.advance(time=15., frame=15, damage=70.)
    runtime.poll(c, observer if final_watermark else None)
    final = runtime.sessions.live()['statistics']['rows']['a']
    c.lifecycle['battle'] = None
    runtime.poll(c)
    archive, = runtime.sessions.pop_archives()
    row = archive['statistics']['rows']['a']
    assert row == final
    assert row['skill_window_count'] == 1
    if final_watermark:
        assert archive['event_status'] == 'finished'
        assert row['skill_active_seconds'] == 4
        assert row['skill_coverage_complete']
        assert row['skill_split_effective_complete']
        assert row['skill_damage_effective'] == 10
        assert row['non_skill_damage_effective'] == 20
        assert row['skill_split_reason'] is None
    else:
        assert archive['event_status'] == 'gap'
        assert row['skill_damage_effective'] is None
        assert not row['skill_split_effective_complete']
        assert row['skill_split_reason'] == '事件覆盖已结束'


def test_active_sample_cannot_reopen_a_finalizing_generation():
    c, r, first = begin()
    c.lifecycle.update(state=3, ready=False)
    c.advance(damage=175.)
    r.poll(c)
    assert not r.sessions.consume(replace(first, sequence=999))
    assert r.sessions.state == 'finalizing'
    assert total(r) == 175.
    assert not r.sessions.pop_archives()


@pytest.mark.parametrize('change', ['exit', 'pid', 'birth'])
@pytest.mark.parametrize('bracket', ['before', 'after'])
@pytest.mark.parametrize('terminal', [False, True])
def test_confirmed_process_change_archives_without_waiting_for_a_new_battle(change, bracket, terminal):
    from collector import Collector, BattleChanged

    c, r, first = begin()
    lifecycle = c.read_lifecycle
    c._lifecycle_reader = SimpleNamespace(sample=lifecycle)
    c._lifecycle_maps_refresh_at = float('-inf')
    lookups = []

    def lookup():
        lookups.append(True)
        changed = bracket == 'before' or len(lookups) > 1
        pid = (0 if change == 'exit' else 20) if changed and change != 'birth' else 10
        return 'package', pid

    def shell(*args, **kwargs):
        birth = 2 if change == 'birth' and (bracket == 'before' or len(lookups) > 1) else 1
        return 'boot\n10 (game) ' + ' '.join(['0'] * 19 + [str(birth)])

    c.core.mc._pid_for_known_package = lookup
    c.core.mc.shell = shell
    c.read_lifecycle = lambda: Collector.read_lifecycle(c)
    c.advance(damage=175.)
    if terminal:
        c.lifecycle.update(state=3, ready=False)
    for _ in range(2):
        with pytest.raises(BattleChanged):
            r.poll(c)
    record, = r.sessions.pop_archives()
    assert record['battle_id'] == first.sample['session']
    assert record['statistics']['ended']
    assert record['statistics']['rows']['a']['damage'] == 100.
    assert record['last_sample']['frame'] == 10
    assert record['metadata']['outcome'] == 'unknown'
    assert r.sessions.state == 'ended'
    assert r.sessions.connection == 'disconnected'
    assert r.feed.identity is None
    assert not r.sessions.consume(replace(first, sequence=999))
    assert not r.sessions.pop_archives()


def test_unconfirmed_process_read_failure_keeps_the_live_generation(monkeypatch):
    from collector import Collector

    c, r, first = begin()
    c._lifecycle_reader = SimpleNamespace(sample=lambda: None)
    c.core.mc._pid_for_known_package = lambda: ('package', 10)
    c.core.mc.shell = lambda *args, **kwargs: ''
    with monkeypatch.context() as patch:
        patch.setattr(c, 'read_lifecycle', lambda: Collector.read_lifecycle(c))
        with pytest.raises(CaptureRetry):
            r.poll(c)
    assert not r.sessions.pop_archives()
    assert r.poll(c).generation == first.generation
    assert total(r) == 100.


@pytest.mark.parametrize('fault', ['roster', 'mapping'])
def test_recovered_terminal_pair_establishes_endpoint_after_a_failed_final_read(fault, monkeypatch):
    from roster import RosterUnavailable

    c, r, first = begin()
    c.lifecycle.update(state=3, ready=False)
    c.advance(time=11., frame=11, damage=175.)
    with monkeypatch.context() as patch:
        if fault == 'roster':
            def unavailable():
                raise RosterUnavailable('transient final read')
            patch.setattr(c, 'read_roster', unavailable)
        else:
            patch.setitem(c.sample, 'rows', [dict(c.sample['rows'][0], id='unknown')])
        r.poll(c)
    assert r.sessions.state == 'finalizing'
    assert total(r) == 100.
    assert not r.sessions.pop_archives()
    r.disconnected('transport_lost')
    assert not r.sessions.consume(replace(first, sequence=r.sessions.sequence + 1))
    r.poll(c)
    assert total(r) == 175.
    assert r.sessions.adapter.statistics.end_time == 11.
    c.advance(time=11., frame=11, damage=180.)
    r.poll(c)
    # Once a reliable endpoint exists, only same-frame/time corrections apply.
    c.advance(time=12., frame=12, damage=200.)
    r.poll(c)
    assert total(r) == 180.
    c.lifecycle['battle'] = None
    r.poll(c)
    record, = r.sessions.pop_archives()
    assert record['battle_id'] == first.sample['session']
    assert record['statistics']['game_time'] == 11.
    assert record['statistics']['rows']['a']['damage'] == 180.
    assert record['statistics']['rows']['a']['recent_damage'] is None
    assert record['last_sample']['frame'] == 11
    assert record['metadata']['final_sample_error']


def test_normal_end_without_a_settlement_sample_keeps_final_totals():
    from panel_model import PanelConfig, render_panel, rows_from_statistics

    c, r, _ = begin()
    c.advance()
    r.poll(c)
    c.lifecycle['battle'] = None
    observation = r.poll(c)
    assert observation.kind == 'end' and observation.reason == 'battle_absent'
    archive, = r.sessions.pop_archives()
    stats = archive['statistics']
    assert stats['totals_complete'] is True
    assert stats['rows']['a']['counter_snapshot_complete'] is True
    assert stats['rows']['a']['recent_damage'] is None
    view = render_panel(PanelConfig(), rows_from_statistics(stats))
    assert view.share_known and view.share_reason == ''
    assert view.entries[0].share == 1.


@pytest.mark.parametrize('ending', ['absent', 'exit_transition'])
@pytest.mark.parametrize('fault', [None, 'no_marker', 'sequence', 'epoch', 'active', 'clock',
                                    'controller', 'native', 'stats', 'level', 'observer_error'])
def test_finish_marker_must_match_last_accepted_battle_watermark(ending, fault):
    c, runtime = FakeCollector(), BattleRuntime()
    c.sample['session'] = 'boot:1:10:100:300:400'
    c.advance(time=10., frame=10, damage=0.)
    events, reads, committed = [], [], []
    final_mark = None

    def mark():
        if final_mark is not None:
            return final_mark
        return dict(frame=c.sample['frame'], game_time=c.sample['game_time'],
                    sequence=len(events), epoch=len(events) + 1, active=0)

    def read(sequence):
        reads.append(sequence)
        return {'events': deepcopy(events[len(events) - 1:sequence])}

    observer = SimpleNamespace(mark=mark, read_through=read, commit_events=committed.append)
    runtime.poll(c, observer)
    events.append(dict(sequence=1, frame=11, game_time=11., source='a', kind='damage', amount=20.,
                       target='enemy_1', real_delta=-20., hp_before=100., hp_after=80.,
                       killed=False, damage_type=1, mitigation_before=25., mitigation_after=20.))
    c.advance(time=11., frame=11, damage=20.)
    runtime.poll(c, observer)
    boundary = dict(mark(), controller='0x64', native='0xc8', logger='0xfa', stats='0x12c', level='0x190')
    # The clock visible after cleanup may already belong to the next scene.
    final_mark = dict(frame=0, game_time=0., sequence=1, epoch=2, active=0,
                      terminal_boundary=boundary)
    if fault == 'no_marker':
        final_mark.pop('terminal_boundary')
    elif fault == 'observer_error':
        final_mark['error'] = 'detached'
    elif fault == 'clock':
        boundary.update(frame=12, game_time=12.)
    elif fault in ('controller', 'native', 'stats', 'level'):
        boundary[fault] = '0xff'
    elif fault is not None:
        boundary[fault] += 1

    if ending == 'absent':
        c.lifecycle.update(battle=None, state=None, transiting=True, target='battle_finish')
    else:
        c.lifecycle.update(transiting=True, target='battle_finish')
    observation = runtime.poll(c, observer)
    assert observation.kind == 'end'
    assert observation.sample is None
    assert reads == committed == [0, 1]  # No replay or new drain from the terminal marker.
    archive, = runtime.sessions.pop_archives()
    assert archive['metadata']['outcome'] == 'unknown'
    row = archive['statistics']['rows']['a']
    assert row['damage'] == 20.
    assert row['recent_damage'] is None
    if fault is None:
        assert archive['event_status'] == 'finished'
        assert archive['event_error'] is None
        assert row['mitigation_rate'] == pytest.approx(20.)
    else:
        assert archive['event_status'] == 'gap'
        assert archive['event_error'] == '未取得最终事件水位'
        assert row['mitigation_rate'] is None
        assert row['observed_mitigation_rate'] == pytest.approx(20.)
    assert not runtime.sessions.consume(observation)
    assert not runtime.sessions.pop_archives()


@pytest.mark.parametrize('preparing', [False, True])
@pytest.mark.parametrize('fault', [None, 'no_observer', 'sequence', 'epoch', 'identity',
                                    'native_session', 'frame_did_not_reset', 'uncommitted_tail'])
def test_same_native_round_reset_retires_only_the_last_verified_watermark(preparing, fault):
    collector, runtime = FakeCollector(), BattleRuntime()
    collector.advance(time=10., frame=300, damage=0.)
    observed_events, drains, committed = [], [], []
    sequence = epoch = 0

    def mark():
        return dict(frame=collector.sample['frame'], game_time=collector.sample['game_time'],
                    sequence=sequence, epoch=epoch, active=0)

    def read(through):
        drains.append(through)
        acknowledged = committed[-1] if committed else 0
        return {'events': [event for event in observed_events
                           if acknowledged < event['sequence'] <= through]}

    observer = SimpleNamespace(mark=mark, read_through=read, commit_events=committed.append)
    runtime.poll(collector, observer)
    observed_events.extend([
        dict(sequence=1, frame=330, game_time=11., source='a', kind='skill_start', amount=1.,
             skill_id='skill', proof={'method': 'Character.SwitchToSkillState'}),
        dict(sequence=2, frame=360, game_time=12., source='a', kind='damage', amount=20.,
             target='enemy_1', hp_before=100., hp_after=80., real_delta=-20., killed=False,
             damage_type=1, mitigation_before=25., mitigation_after=20.),
    ])
    sequence, epoch = 2, 5
    collector.advance(time=12., frame=360, damage=20.)
    previous = runtime.poll(collector, observer)
    row = runtime.sessions.live()['statistics']['rows']['a']
    assert row['skill_damage_effective'] == 20.
    assert row['skill_active_seconds'] == 1.
    assert drains == committed == [0, 2]

    if fault == 'uncommitted_tail':
        collector.advance(time=13., frame=390, damage=25.)
        runtime.poll(collector, None)
    frame = 360 if fault == 'frame_did_not_reset' else 0
    collector.advance(time=0., frame=frame, damage=0.)
    collector.sample['rows'][0]['healing'] = 0.
    if preparing:
        collector.roster = RosterSnapshot(frame, (), frozenset(), True)
        collector.sample['rows'].clear()
    if fault == 'identity':
        collector.lifecycle['battle'] = collector.sample['battle_identity'] = (100, 300)
    if fault == 'native_session':
        collector.sample['session'] = 'new_level_or_stats'
    if fault in ('sequence', 'epoch'):
        epoch += 1
    if fault == 'sequence':
        sequence += 1
        observed_events.append(dict(sequence=3, frame=0, game_time=0., source='a',
                                    kind='damage', amount=100.))
    use_observer = None if fault == 'no_observer' else observer
    successor = runtime.poll(collector, use_observer)
    assert successor.generation == previous.generation + 1
    assert successor.kind == ('preparing' if preparing else 'sample')
    if preparing:
        assert drains == committed == [0, 2]
        assert successor.events is None
    elif use_observer is not None:
        assert drains == committed == [0, 2, sequence]
        assert total(runtime) == 0.
    archive, = runtime.sessions.pop_archives()
    assert archive['metadata']['outcome'] == 'unknown'
    assert archive['statistics']['rows']['a']['damage'] == (25. if fault == 'uncommitted_tail' else 20.)
    if fault is None:
        assert successor.round_reset == dict(before=mark(), after=mark(), sequence=sequence)
        assert archive['event_status'] == 'finished'
        assert archive['event_error'] is None
        assert archive['statistics']['rows']['a']['skill_damage_effective'] == 20.
        assert archive['statistics']['rows']['a']['skill_active_seconds'] == 1.
    else:
        assert archive['event_status'] == 'gap'
        assert archive['event_error'] == '未取得最终事件水位'
        assert archive['statistics']['rows']['a']['skill_damage_effective'] is None
    assert not runtime.sessions.consume(successor)
    assert not runtime.sessions.pop_archives()


def test_mid_battle_gap_then_end_keeps_totals_but_not_completeness():
    from panel_model import team_status

    c, r, _ = begin()
    c.advance()
    r.poll(c)
    r.disconnected('read_unavailable')
    c.lifecycle['battle'] = None
    r.poll(c)
    archive, = r.sessions.pop_archives()
    stats = archive['statistics']
    assert stats['rows']['a']['damage'] == 150.  # retained, not zero-filled
    assert stats['totals_complete'] is False
    assert stats['rows']['a']['counter_snapshot_complete'] is False
    complete, reason, _ = team_status(stats)
    assert complete is False and '累计快照不完整' in reason
