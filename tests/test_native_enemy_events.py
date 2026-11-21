"""PC snapshot/event projection only; no target or Android reader is used."""
from copy import deepcopy

import pytest

from collection_session import CollectionSession
from enemy_telemetry import EnemyTelemetry, NativeEnemyObservations
from event_observer import ObserverStatus
from native_pc_collector import BUILD_ID, NativePcCollector
from test_native_pc_collector import Probe, sample, stage_structure


def snapshot(frame=3, hp=100., *, uid=9, eid='enemy_a'):
    raw = {'enemies_status': 'available', 'enemies': {
        'scope': 'unit_manager_enemies_membership', 'collection_count': 1,
        'entities': [dict(instance_uid=uid, enemy_key=eid, inited=True, borned=True,
                          is_disappeared=False, native_object_present=True,
                          hp=hp, hp_status='available')],
    }}
    return NativeEnemyObservations().observe(raw, frame=frame, game_time=frame / 30., catalog={})


def event(sequence, frame, kind='enemy_hp', **extra):
    return dict(sequence=sequence, frame=frame, game_time=frame / 30., kind=kind,
                instance_uid='9', eid='enemy_a', **extra)


def state(events=(), *, start=3, complete=True):
    return dict(complete=complete, start_frame=start, events=list(events))


def project(telemetry, seed, frame, events=(), **kwargs):
    return telemetry.sample_native_snapshot(seed, frame=frame, game_time=frame / 30.,
                                            enemy_state=state(events), **kwargs)


def no_bootstrap(_reader):
    pytest.fail('normalized PC projection must not bootstrap or read an Android reader')


def test_pc_seed_keeps_hp_membership_distinct_from_alive_capacity_and_plan():
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    original = snapshot()
    view = project(telemetry, original, 3)
    row, = view['rows']
    assert view['scope'] == 'native_pc_enemy_events' and view['enemy_state_complete'] is True
    assert row['hp'] == 100 and row['alive'] is None
    assert row['max_hp'] is None and row['hp_known'] is False
    assert row['spawn_frame'] is None and row['end_frame'] is None
    assert view['totals']['remaining_hp_complete'] is False
    assert view['totals']['planned_count'] == 0 and view['totals']['planned_complete'] is False
    assert view['totals']['total_hp_complete'] is False
    observed = view['snapshot_observation']
    assert observed['stale'] is False and 'series' not in observed
    assert observed['totals']['remaining_hp'] == 100
    assert observed['totals']['remaining_hp_complete'] is True
    assert observed['rows'][0]['first_observed_frame'] == 3
    assert original['rows'][0]['lifecycle'] == 'current'


def test_pc_known_seed_reuses_hp_rebirth_and_finish_reducer_through_stale_snapshots():
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    seed = snapshot()
    project(telemetry, seed, 3)
    records = [event(1, 6, hp_before=100., hp_after=20.)]
    hit = project(telemetry, None, 6, records,
                  enemy_damage={'complete': True, 'by_uid': {'9': {'eid': 'enemy_a', 'amount': 80.}}})
    assert hit['rows'][0]['hp'] == 20 and hit['rows'][0]['alive'] is None
    assert hit['snapshot_observation']['stale'] is True
    assert hit['snapshot_observation']['rows'][0]['last_observed_hp'] == 100
    assert hit['snapshot_observation']['rows'][0]['last_hp_observed_frame'] == 3
    assert hit['last_point']['our_effective_damage'] == 80
    assert hit['last_point']['our_damage_complete'] is False  # no entry coverage declaration

    records.append(event(2, 9, 'enemy_fake_death', hp_after=0.))
    fake = project(telemetry, None, 9, records)
    assert fake['rows'][0]['lifecycle'] == 'rebirthing'
    assert fake['rows'][0]['end_frame'] is None
    records.append(event(3, 12, 'enemy_reborn', hp_before=0., hp_after=100.,
                         phase_total_hp=200., phase_count=2, phase_from_birth=False))
    reborn = project(telemetry, None, 12, records)
    assert reborn['rows'][0]['hp'] == 100 and reborn['rows'][0]['alive'] is True
    assert reborn['rows'][0]['max_hp'] is None
    records.append(event(4, 15, hp_before=100., hp_after=80., enemy_max_hp=120.))
    measured = project(telemetry, None, 15, records)
    assert measured['rows'][0]['max_hp'] == 120  # explicit validated event, never the seed HP
    records.append(event(5, 18, 'enemy_finish', hp_after=0., finish_reason=2))
    finished = project(telemetry, None, 18, records)
    row, = finished['rows']
    assert row['lifecycle'] == 'departed' and row['alive'] is False
    assert row['end_frame'] == 18 and row['end_reason'] == 'death'
    assert row['hp'] == 0 and row['hp_at_finish'] == 0
    for totals in [finished['totals'], finished['last_point'], *finished['series']]:
        assert totals['total_hp_complete'] is False
        assert totals['planned_complete'] is False and totals['stage_total_hp_complete'] is False
    assert finished['snapshot_observation']['rows'][0]['lifecycle'] == 'current'


@pytest.mark.parametrize('loss', ['missing', 'incomplete', 'start', 'future_event',
                                 'sequence_shape', 'tail_regression', 'identity'])
def test_pc_event_loss_does_not_reconstruct_continuity_from_a_later_snapshot(loss):
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    records = [event(1, 6, hp_after=20.)]
    project(telemetry, snapshot(), 3)
    assert project(telemetry, None, 6, records)['rows'][0]['hp'] == 20
    observation = state(records)
    if loss == 'missing':
        observation = None
    elif loss == 'incomplete':
        observation['complete'] = False
    elif loss == 'start':
        observation['start_frame'] = 6
    elif loss == 'future_event':
        observation['events'].append(event(2, 12, hp_after=5.))
    elif loss == 'sequence_shape':
        observation['events'] = [{'sequence': None}, event(2, 9, hp_after=5.)]
    elif loss == 'tail_regression':
        observation['events'] = []
    else:
        changed = event(2, 9, hp_after=5.)
        changed['eid'] = 'another_enemy'
        observation['events'].append(changed)
    assert telemetry.sample_native_snapshot(None, frame=9, game_time=.3,
                                           enemy_state=observation) is None
    assert project(telemetry, snapshot(15), 15, records) is None
    telemetry.reset()
    assert telemetry.sample_native_snapshot(snapshot(15), frame=15, game_time=.5,
                                           enemy_state=state(start=15)) is not None


def test_pc_needs_a_nonfuture_seed_and_refuses_a_regressing_event_watermark():
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    assert project(telemetry, None, 3) is None
    assert project(telemetry, snapshot(9), 6) is None
    assert project(telemetry, snapshot(9), 9) is not None
    assert project(telemetry, None, 6) is None
    assert project(telemetry, snapshot(12), 12) is None


def test_pc_duplicate_snapshot_and_verified_prefix_do_not_rebuild_or_rescan(monkeypatch):
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    seed = snapshot()
    records = [event(index, 3, hp_after=100.) for index in range(1, 501)]
    project(telemetry, seed, 3, records)
    observation = telemetry._native_observation

    class VerifiedPrefix(dict):
        def __getitem__(self, key):
            assert key == 'sequence', 'a verified cumulative prefix was rescanned'
            return super().__getitem__(key)

    monkeypatch.setattr(telemetry, '_event_row', lambda *_args: pytest.fail('duplicate seed rebuilt'))
    records = [VerifiedPrefix(item) for item in records] + [event(501, 6, hp_after=20.)]
    updated = project(telemetry, seed, 6, records)
    assert updated['rows'][0]['hp'] == 20
    assert telemetry._native_observation is observation
    assert updated['snapshot_observation']['stale'] is False
    assert project(telemetry, None, 9, records)['snapshot_observation']['stale'] is True


def native_report(frame, hp=100.):
    report = sample(frame, 0.)
    report['sample'].update(enemies_status='available', enemies={
        'scope': 'unit_manager_enemies_membership', 'collection_count': 1,
        'entities': [dict(instance_uid=9, enemy_key='enemy_a', inited=True, borned=True,
                          is_disappeared=False, native_object_present=True,
                          hp=hp, hp_status='available')],
    }, stage_spawns_status='available', stage_spawns=stage_structure())
    return report


def test_normal_session_enemy_projection_forwards_committed_events_and_preserves_fallback(tmp_path, monkeypatch):
    now = [10.]
    monkeypatch.setattr('native_pc_collector.time.monotonic', lambda: now[0])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: Probe([
        native_report(3), native_report(6), sample(9, 0.), native_report(12), native_report(15),
    ]))
    session = CollectionSession(None, None, collector=collector, frida_remote=None)
    session.enable_lifecycle()
    session.connect()
    try:
        original = session.read()
        fallback = deepcopy(original['enemy'])
        assert fallback['scope'] == 'native_pc_current_hp'
        original['statistics']['enemy_state'] = state()
        session._attach_enemy(original)
        assert original['enemy']['scope'] == 'native_pc_enemy_events'
        next_sample = session.read()
        # No producer was installed: normal entry still uses the unchanged PC fallback.
        assert next_sample['enemy']['scope'] == 'native_pc_current_hp'
        assert next_sample['enemy']['stage_plan']['fixed_count'] == 42
        assert next_sample['enemy']['rows'][0]['last_hp_observed_frame'] == 6
        assert next_sample['enemy']['totals']['remaining_hp'] == 100
    finally:
        session.close()


def test_collector_complete_events_update_through_expired_cache_and_true_gap_falls_back(tmp_path, monkeypatch):
    now = [10.]
    monkeypatch.setattr('native_pc_collector.time.monotonic', lambda: now[0])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: Probe([native_report(3)]))
    collector.connect()
    try:
        lifecycle = collector.read_lifecycle()
        collector.read_guarded_pair(lifecycle)
        seeded = collector.read_enemies(frame=3, game_time=.1, enemy_state=state())
        assert seeded['scope'] == 'native_pc_enemy_events'
        now[0] = 20.
        records = [event(1, 6, hp_after=20.)]
        live = collector.read_enemies(frame=6, game_time=.2, enemy_state=state(records))
        assert live['rows'][0]['hp'] == 20 and live['enemy_state_complete'] is True
        assert live['snapshot_observation']['stale'] is True
        assert not collector.read_enemies(frame=2, game_time=.05, enemy_state=state(records))['ready']
        failed = collector.read_enemies(frame=9, game_time=.3, enemy_state=state(records, complete=False))
        assert failed['scope'] == 'native_pc_current_hp' and failed['error'] == 'enemy_sample_stale'
        assert not collector.read_enemies(frame=12, game_time=.4, enemy_state=state(records))['ready']
    finally:
        collector.close()


@pytest.mark.parametrize('seed_frame,empty,entry_complete', [
    (0, True, True), (3, True, True), (0, False, True), (0, True, False),
])
def test_only_empty_battle_zero_seed_can_prove_entered_capacity(seed_frame, empty, entry_complete):
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    if empty:
        seed = NativeEnemyObservations().observe({
            'enemies_status': 'available', 'enemies': {
                'scope': 'unit_manager_enemies_membership', 'collection_count': 0, 'entities': []}},
            frame=seed_frame, game_time=seed_frame / 30., catalog={})
    else:
        seed = snapshot(seed_frame)
    assert telemetry.sample_native_snapshot(seed, frame=seed_frame, game_time=seed_frame / 30.,
        enemy_state=state(start=seed_frame), enemy_entry={
            'complete': True, 'start_frame': seed_frame, 'events': []}) is not None
    born = event(1, 6, 'enemy_enter', hp_after=100., enemy_entry_attributes={'max_hp': 100.})
    entered = {'complete': entry_complete, 'start_frame': seed_frame, 'events': [born]}
    result = telemetry.sample_native_snapshot(None, frame=6, game_time=.2,
        enemy_state=state([born], start=seed_frame), enemy_entry=entered)
    exact = seed_frame == 0 and empty and entry_complete
    assert result['totals']['entered_total_hp'] == 100
    assert result['totals']['total_hp_complete'] is exact
    assert result['last_point']['total_hp_complete'] is exact
    assert result['totals']['planned_count'] == 0
    assert result['totals']['planned_complete'] is False
    assert result['totals']['stage_total_hp_complete'] is False


def test_native_snapshot_seed_does_not_fill_missing_hp_or_event_attributes():
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    incomplete = snapshot(hp=None)
    assert incomplete['totals']['remaining_hp_complete'] is False
    assert project(telemetry, incomplete, 3) is None
    ready = project(telemetry, snapshot(6, 25.), 6)
    assert ready['rows'][0]['hp'] == 25
    assert ready['rows'][0]['max_hp'] is None
    assert ready['rows'][0]['alive'] is None


def test_native_entry_coverage_cannot_cross_another_stream_start():
    telemetry = EnemyTelemetry(None, bootstrap=no_bootstrap)
    project(telemetry, snapshot(), 3)
    assert project(telemetry, None, 6, enemy_entry={
        'complete': True, 'start_frame': 0, 'events': []}) is None
    assert project(telemetry, snapshot(9), 9) is None


def test_bound_native_observer_reaches_enemy_view_through_normal_session(tmp_path, monkeypatch):
    now = [10.]
    monkeypatch.setattr('native_pc_collector.time.monotonic', lambda: now[0])

    class MutableProbe(Probe):
        report = native_report(3)

        def sample(self, *, extra_reader, idle_reader=None):
            return deepcopy(self.report)

    probe = MutableProbe([])
    commits = []

    class AuthorizedObserver:
        status = ObserverStatus.READY
        error = None
        probe_modes = {}

        def __init__(self, collector, remote):
            assert remote is None
            lifecycle = collector.read_lifecycle()
            native, _ = collector.read_guarded_pair(lifecycle)
            self.binding = {'native_session': native['session'], 'source_build': BUILD_ID}
            self.events = []

        def start(self):
            pass

        def stop(self):
            self.status = ObserverStatus.STOPPED

        def mark(self):
            raw = probe.report['sample']
            sequence = self.events[-1]['sequence'] if self.events else 0
            return dict(self.binding, sequence=sequence, epoch=sequence, active=0,
                        frame=raw['frame'], game_time=raw['game_time'])

        def read_through(self, sequence):
            return dict(self.binding, events=[item for item in self.events if item['sequence'] <= sequence],
                        damage_unknown=False, engagement_unknown=False, shield_unknown=False,
                        skill_unknown=False, mitigation_ready=True, mitigation_unknown=False,
                        enemy_state_ready=True, enemy_state_unknown=False, enemy_entry_ready=True,
                        enemy_entry_unknown=False, enemy_phase_ready=True,
                        enemy_damage_ready=True, enemy_damage_unknown=False)

        def commit_events(self, sequence):
            commits.append(sequence)

    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    collector._enemy_events.bootstrap = no_bootstrap
    session = CollectionSession(None, None, collector=collector, frida_remote=None,
                                observer_factory=AuthorizedObserver)
    session.enable_lifecycle()
    session.connect()
    try:
        first = session.read()
        assert first['event_status'] == 'recording'
        assert first['enemy']['scope'] == 'native_pc_enemy_events'
        observer = session.observer

        def advance(frame, kind, *, hp, fresh=False, **evidence):
            probe.report = native_report(frame, hp) if fresh else sample(frame, 0.)
            observer.events.append(dict(sequence=len(observer.events) + 1, frame=frame,
                game_time=frame / 30., kind=kind, source=None, target='enemy_a',
                target_instance='9', amount=100 if kind == 'enemy_reborn' else 0,
                hp_after=hp, **evidence))
            return session.read()

        hit = advance(6, 'enemy_hp', hp=20., fresh=True, hp_before=100.)
        assert hit['statistics']['enemy_state']['complete'] is True
        assert hit['enemy']['rows'][0]['hp'] == 20
        assert hit['enemy']['stage_plan']['fixed_count'] == 42
        now[0] = 20.
        fake = advance(9, 'enemy_fake_death', hp=0.)
        assert fake['enemy']['rows'][0]['lifecycle'] == 'rebirthing'
        assert fake['enemy']['snapshot_observation']['stale'] is True
        assert fake['enemy']['snapshot_observation']['rows'][0]['hp'] == 20
        reborn = advance(12, 'enemy_reborn', hp=100., hp_before=0.,
                         phase_total_hp=200., phase_count=2, phase_from_birth=False)
        assert reborn['enemy']['rows'][0]['hp'] == 100
        ended = advance(15, 'enemy_finish', hp=0., fresh=True, finish_reason=2)
        row, = ended['enemy']['rows']
        assert row['instance_uid'] == '9' and row['eid'] == 'enemy_a'
        assert row['end_frame'] == 15 and row['end_reason'] == 'death'
        assert ended['enemy']['totals']['planned_complete'] is False
        assert ended['enemy']['totals']['total_hp_complete'] is False
        assert ended['enemy']['snapshot_observation']['rows'][0]['last_hp_observed_frame'] == 15
        assert commits == [0, 1, 2, 3, 4]
        replayed = session.read()
        assert replayed['enemy']['rows'] == ended['enemy']['rows']
        assert replayed['enemy']['totals']['recovered_hp'] == 0
    finally:
        session.close()
