from copy import deepcopy
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from battle_lifecycle import (BattleFeed, BattleObservation, BattleRuntime,
                              BattleSessions, BattleView)
from roster import CharacterEntry, RosterSnapshot
from snapshot_adapter import FrameMismatch, MappingUnavailable


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
    with pytest.raises(FrameMismatch):
        r.poll(c)
    assert total(r) == 100.
    assert r.sessions.state == 'frozen'
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
    with pytest.raises(MappingUnavailable):
        r.poll(c)
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
    record, = r.sessions.pop_archives()
    assert record['reason'] == 'finished_snapshot_unavailable'
    assert record['statistics']['rows']['a']['damage'] == 100.
    assert record['statistics']['rows']['a']['recent_damage'] is None
    assert record['statistics']['ended']


def test_final_mapping_failure_cannot_overwrite_reliable_totals_or_lose_archive():
    c, r, _ = begin()
    c.lifecycle.update(state=3, ready=False)
    c.advance(damage=175.)
    c.sample['rows'] = [dict(c.sample['rows'][0], id='unknown')]
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


def test_gui_worker_failure_still_closes_its_owned_collector():
    import queue
    from app import App
    window = App.__new__(App)
    window.events = queue.Queue()
    window.collector = SimpleNamespace(closed=False)
    window.collector.close = lambda: setattr(window.collector, 'closed', True)

    def disk_failure():
        raise OSError('capture log unavailable')

    window._collect = disk_failure
    window.collect()
    assert window.collector.closed
    assert window.events.get() == ('error', 'capture log unavailable')
