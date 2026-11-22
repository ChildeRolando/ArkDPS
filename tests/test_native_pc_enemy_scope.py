"""An optional native membership is not a seed for another AutoChess scope."""
from copy import deepcopy

import pytest

from battle_lifecycle import BattleRuntime
from event_capture import CaptureRetry
from event_probe import bind_native_autochess_pair
from test_native_pc_autochess_collector_gate import fixture


def native_snapshot(hp):
    return dict(enemies_status='available', enemies=dict(
        scope='unit_manager_enemies_membership', local_side=0, collection_count=1,
        entities=[dict(instance_uid=103, enemy_key='enemy_1007_slime_2',
                       inited=True, borned=True, is_disappeared=False,
                       native_object_present=True, enemy_events_supported=True,
                       hp=hp, hp_status='available', max_hp=None)]))


def source(tmp_path):
    collector, probe, observer, commits, records = fixture(tmp_path)
    collector._enemy_catalog = {}
    requested = [731.4]
    original = probe.sample

    def sample(**kwargs):
        report = original(**kwargs)
        if requested:
            report['sample'].update(native_snapshot(requested.pop()))
        return report

    probe.sample = sample
    return BattleRuntime(), collector, probe, observer, requested


def read(collector, *, frame, when, identity, start=0):
    return collector.read_enemies(frame=frame, game_time=when, event_scope=identity,
        enemy_entry=dict(complete=True, start_frame=start, events=[]),
        enemy_state=dict(complete=True, start_frame=start, events=[]))


def test_phase_change_resets_native_observations_and_event_seed_without_relabelling_old_snapshot(tmp_path):
    runtime, collector, probe, observer, requested = source(tmp_path)
    first = runtime.poll(collector, observer)
    identity = first.sample['autochess_context']['identity']
    initial = read(collector, frame=0, when=0, identity=identity)
    assert initial['ready'] and collector._enemy_observations.rows
    assert collector._enemy_events._native_identities

    probe.phase = 5  # Same native battle, frame/time and pooled UID.
    second = runtime.poll(collector, observer)
    other = second.sample['autochess_context']['identity']
    assert other != identity and collector._enemy_scoped_snapshot[0] != collector._event_scope_key(other)
    unknown = read(collector, frame=0, when=0, identity=other)
    assert not unknown['ready'] and unknown['rows'] == []
    assert collector._enemy_observations.rows == {}
    assert collector._enemy_events._native_identities == {} and not collector._enemy_events._seeded

    requested.append(100.)
    probe.frame, probe.when = 30, 1
    third = runtime.poll(collector, observer)
    current = read(collector, frame=30, when=1, identity=third.sample['autochess_context']['identity'])
    assert current['ready'] and current['rows'][0]['hp'] == 100.
    assert collector._enemy_observations.rows['103']['peak_observed_hp'] == 100.


def test_unknown_context_cannot_project_old_native_members_or_old_core_events(tmp_path):
    runtime, collector, probe, observer, requested = source(tmp_path)
    accepted = runtime.poll(collector, observer)
    identity = accepted.sample['autochess_context']['identity']
    assert read(collector, frame=0, when=0, identity=identity)['ready']
    unknown = read(collector, frame=0, when=0, identity={})
    assert not unknown['ready'] and unknown['rows'] == []
    assert unknown['error'] == 'native_enemy_scope_unverified'
    assert collector._enemy_observations.rows == {} and collector._enemy_events._native_identities == {}


def test_failed_new_pair_preserves_fresh_same_scope_seed_but_never_relabels_it(tmp_path):
    runtime, collector, probe, observer, requested = source(tmp_path)
    first = runtime.poll(collector, observer)
    identity = first.sample['autochess_context']['identity']
    committed = collector._enemy_scoped_snapshot
    assert read(collector, frame=0, when=0, identity=identity)['rows'][0]['hp'] == 731.4
    original, count = observer.mark, [0]

    def changed():
        count[0] += 1
        result = original()
        if count[0] == 2:
            result['frame'] += 1
        return result

    observer.mark = changed
    requested.append(600.)
    probe.frame, probe.when = 30, 1
    with pytest.raises(CaptureRetry):
        runtime.poll(collector, observer)
    assert collector._enemy_scoped_snapshot is committed
    assert collector._enemy_snapshot[3]['enemies']['entities'][0]['hp'] == 600.
    observer.mark = original
    probe.frame, probe.when = 60, 2
    runtime.poll(collector, observer)  # No new native membership in this pair.
    retained = read(collector, frame=60, when=2, identity=identity)
    assert retained['ready'] and retained['rows'][0]['hp'] == 731.4
    assert collector._enemy_scoped_snapshot is committed

    probe.phase = 5
    changed = runtime.poll(collector, observer)
    new_scope = changed.sample['autochess_context']['identity']
    without_seed = read(collector, frame=60, when=2, identity=new_scope)
    assert not without_seed['ready'] and without_seed['rows'] == []
    requested.append(100.)
    probe.frame, probe.when = 90, 3
    runtime.poll(collector, observer)
    latest = read(collector, frame=90, when=3, identity=new_scope)
    assert latest['ready'] and latest['rows'][0]['hp'] == 100.
    assert collector._enemy_observations.rows['103']['peak_observed_hp'] == 100.


def test_scope_binding_requires_the_exact_pair_object_and_ordinary_fallback_is_unchanged(tmp_path):
    runtime, collector, probe, observer, requested = source(tmp_path)
    life = collector.read_lifecycle()
    sample, roster = collector.read_guarded_pair(life)
    sample['battle_metadata'] = life['metadata']
    mark = observer.mark()
    bind_native_autochess_pair(sample, mark, deepcopy(mark))
    collector.bind_enemy_scope(deepcopy(sample))
    assert collector._enemy_scoped_snapshot is None
    identity = sample['autochess_context']['identity']
    assert not read(collector, frame=0, when=0, identity=identity)['ready']
    ordinary = collector.read_enemies(frame=0, game_time=0)
    assert ordinary['ready'] and ordinary['rows'][0]['hp'] == 731.4
