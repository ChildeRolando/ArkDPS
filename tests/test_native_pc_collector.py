from copy import deepcopy

import pytest

from battle_history import BattleHistory
from battle_lifecycle import BattleRuntime
from collection_session import CollectionSession
from collector import ProcessChanged, SampleUnavailable, UnsupportedVersion
from event_capture import CaptureRetry
from native_pc.probe_native_pc_battle import ResearchFailure
from native_pc_collector import BUILD_ID, NativePcCollector
from panel_model import Category, PanelConfig, Ranking, RateBasis, render_panel, rows_from_statistics


OPERATOR = 'char_336_folivo'
TOKEN = 'token_10010_folivo_car'


class Probe:
    pid = 456
    process_instance = (456, 100)
    build = {'known_build': True}
    battle_identity = (100, 200, 300, 400, 500)

    def __init__(self, reports):
        self.reports = iter(reports)
        self.reads = 0
        self.closed = False

    def sample(self, *, extra_reader, idle_reader=None):
        assert idle_reader is None
        self.reads += 1
        return deepcopy(next(self.reports))

    def close(self):
        self.closed = True


def sample(frame, damage, *, state=2, presence=True):
    operator = dict(id=OPERATOR, unique_id=10, is_token=False, is_predefined=False,
                    host_key=None, host_uid=None, side=0)
    token = dict(id=TOKEN, unique_id=20, is_token=True, is_predefined=False,
                 host_key=OPERATOR, host_uid=10, side=0)
    return {'status': 'sample_available', 'sample': dict(
        battle_serial=1, frame=frame, game_time=frame / 30, battle_state=state,
        stage_status='available', stage_id='Obt/Main/level_main_01-07',
        outcome='defeat' if state == 3 else None, total_damage=damage, total_heal=0.,
        actor_rows_status='available', actor_rows=[dict(
            id=TOKEN, damage=damage, physical=damage, arts=0., true=0., elemental=0., healing=0.)],
        roster_status='available', roster={'local_side': 0, 'entries': [operator, token]},
        presence_status='available' if presence else 'unavailable',
        presence={'local_side': 0, 'entities': [dict(
            data_uid=10, player_side=0, side=1, inited=True, borned=True, is_disappeared=False,
            id=OPERATOR, is_token=False, is_predefined=False)]} if presence else None)}


ABSENT = {'status': 'waiting_for_battle', 'reason': 'singleton_is_null'}


def flow(source, target, *, transiting):
    return dict(game_flow_status='available', game_flow=dict(
        source_scene=source, target_scene=target, transiting=transiting))


def opening(practice=True, stage='main_01-07'):
    return dict(ABSENT, **flow('home', 'battle_loader', transiting=True),
                practice_selection_status='available', practice_selection=dict(stage_id=stage, practice=practice))


@pytest.mark.parametrize(('state', 'result', 'outcome'), [(2, 0, 'abandoned'), (3, 2, 'defeat')])
@pytest.mark.parametrize('opening_hint', [True, False])
def test_finish_outcome_reaches_history_without_publishing_opening_hint(
        tmp_path, state, result, outcome, opening_hint):
    start = sample(6, 5.)
    start['sample'].update(flow('home', 'battle_loader', transiting=False))
    finish = sample(6, 7., state=state)
    finish['sample'].update(flow('level_main_01-07', 'battle_finish', transiting=True), battle_result=result)
    probe = Probe([opening(opening_hint), dict(ABSENT, game_flow_status='unavailable'),
                   start, finish, ABSENT, ABSENT])
    history = BattleHistory(tmp_path)
    session = CollectionSession(None, None, collector=NativePcCollector(tmp_path, probe_factory=lambda _: probe),
                                frida_remote=None, history=history)
    session.enable_lifecycle()
    session.connect()
    for _ in range(6):
        try:
            session.read()
        except CaptureRetry:
            pass
    record, = history.list_recent().records
    assert record.metadata['practice'] is None
    assert 'native_pc_squad_loader' not in record.metadata['evidence']
    assert record.metadata['outcome'] == outcome
    assert record.metadata['platform'] == 'native_pc'
    assert record.statistics['rows'][OPERATOR]['damage'] == 7.
    session.close()


@pytest.mark.parametrize('incomplete', [
    dict(game_flow_status='unavailable'),
    dict(stage_id=None, stage_status='unavailable', **flow('home', 'battle_loader', transiting=False)),
    flow('home', 'battle_loader', transiting=True),
])
def test_recovered_opening_still_has_no_battle_mode_evidence(tmp_path, incomplete):
    first, recovered = sample(4, 0.), sample(8, 0.)
    first['sample'].update(incomplete)
    recovered['sample'].update(flow('home', 'battle_loader', transiting=False))
    probe = Probe([opening(), first, recovered, sample(120, 7., state=3), ABSENT])
    history = BattleHistory(tmp_path)
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    session = CollectionSession(None, None, collector=collector, frida_remote=None, history=history)
    session.enable_lifecycle()
    session.connect()
    with pytest.raises(CaptureRetry):
        session.read()
    assert session.read()['battle_metadata']['practice'] is None
    assert session.read()['battle_metadata']['practice'] is None
    session.read()
    with pytest.raises(CaptureRetry):
        session.read()
    record, = history.list_recent().records
    assert record.metadata['practice'] is None
    session.close()


@pytest.mark.parametrize(('stage_key', 'mode'), [
    ('wk_melee_5', True),
    ('wk_melee_5', False),
    ('wk_melee_6', True),
])
def test_weekly_stage_stays_known_with_unverified_opening_hint(tmp_path, stage_key, mode):
    start = sample(4, 0.)
    level = 'Obt/Weekly/level_weekly_melee_5'
    start['sample'].update(stage_id=level, **flow('home', 'battle_loader', transiting=False))
    probe = Probe([opening(mode, stage_key), start])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    collector.connect()
    assert collector.read_lifecycle()['battle'] is None
    metadata = collector.read_lifecycle()['metadata']
    assert metadata['stage_id'] == level
    assert metadata['practice'] is None
    collector.close()


@pytest.mark.parametrize(('source', 'target', 'transiting', 'result'), [
    ('level_main_01-08', 'battle_finish', True, 0),
    ('level_main_01-07', 'home', True, 0),
    ('level_main_01-07', 'battle_finish', False, 0),
    ('level_main_01-07', 'battle_finish', True, 2),
])
def test_nearby_flow_signatures_do_not_classify_abandonment(tmp_path, source, target, transiting, result):
    active = sample(30, 5.)
    active['sample'].update(flow(source, target, transiting=transiting), battle_result=result)
    probe = Probe([active, ABSENT])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    collector.connect()
    lifecycle = collector.read_lifecycle()
    assert lifecycle['metadata']['outcome'] is None
    assert not lifecycle['transiting']
    assert collector.read_lifecycle()['battle'] is None
    collector.close()


def test_guarded_pair_publishes_and_archives_once_across_same_stage_restart(tmp_path):
    probe = Probe([ABSENT, sample(900, 100.), sample(930, 200.),
                   sample(960, 220., state=3), ABSENT, ABSENT, sample(3, 0.)])
    history = BattleHistory(tmp_path)
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    updates = []
    session = CollectionSession(None, None, collector=collector, frida_remote=None,
                                history=history, on_history=lambda: updates.append(True))
    session.enable_lifecycle()
    session.connect()
    diagnostics = collector.diagnostics.as_dict()
    assert diagnostics['process_pid'] == probe.pid
    assert diagnostics['process_created_filetime'] == probe.process_instance[1]
    with pytest.raises(CaptureRetry):
        session.read()
    first = session.read()
    assert first['statistics']['coverage_start'] == 30
    assert first['statistics']['rows'][OPERATOR]['damage'] == 100.
    assert first['statistics']['rows'][OPERATOR]['present'] is True
    assert set(first['statistics']['rows']) == {OPERATOR}
    assert first['event_status'] == 'unavailable'
    assert first['event_error']
    after = session.read()['statistics']['rows'][OPERATOR]
    assert after['damage'] == 200.
    assert after['recent_damage'] == 100.
    assert after['presence_seconds'] is None and not after['presence_complete']
    session.read()
    for _ in range(2):
        with pytest.raises(CaptureRetry):
            session.read()
    new = session.read()
    assert new['battle_id'] != first['battle_id']
    assert new['statistics']['rows'][OPERATOR]['damage'] == 0.
    record, = history.list_recent().records
    assert record.metadata['outcome'] == 'defeat'
    assert record.metadata['platform'] == 'native_pc'
    assert record.metadata['source_build'] == BUILD_ID
    assert record.statistics['rows'][OPERATOR]['damage'] == 220.
    assert record.event_status == 'unavailable' and record.event_error
    assert len(updates) == 1
    assert probe.reads == 7
    session.close()
    assert probe.closed


def full_cycle_sample(frame, damage=0., healing=0., *, owner_present=True, state=2):
    report = sample(frame, damage, state=state)
    raw = report['sample']
    raw['total_heal'] = healing
    raw['actor_rows'][0]['healing'] = healing
    if not owner_present:
        raw['presence']['entities'] = [dict(data_uid=1, player_side=0, inited=True, borned=True,
                                           is_disappeared=False, id='trap_002_emp', is_token=True,
                                           is_predefined=True)]
    return report


def test_native_full_cycle_integrates_owner_presence_and_survives_history(tmp_path):
    start = full_cycle_sample(4, owner_present=False)
    start['sample']['actor_rows'] = []
    bench = full_cycle_sample(60, 90., 11., owner_present=False)
    # A summon remains active after its host leaves. Its output still belongs
    # to the host, while the host's own on-field clock is stopped.
    bench['sample']['presence']['entities'].append(dict(data_uid=20, player_side=0, inited=True,
        borned=True, is_disappeared=False, id=TOKEN, is_token=True, is_predefined=False))
    same_frame = full_cycle_sample(150, 140., 27.)
    probe = Probe([start, full_cycle_sample(30, 30., 5.), bench,
                   full_cycle_sample(90, 120., 17., owner_present=False),
                   full_cycle_sample(120, 130., 20.), same_frame, deepcopy(same_frame),
                   full_cycle_sample(180, 180., 30., state=3),
                   full_cycle_sample(180, 210., 33., state=3), ABSENT])
    history = BattleHistory(tmp_path)
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    session = CollectionSession(None, None, collector=collector, frida_remote=None, history=history)
    session.enable_lifecycle()
    session.connect()
    assert session.read()['statistics']['rows'] == {}
    session.read()
    session.read()
    on_bench = session.read()['statistics']['rows'][OPERATOR]
    assert on_bench['damage'] == 120. and on_bench['presence_seconds'] == 1.
    assert on_bench['presence_complete'] and on_bench['present'] is False
    for _ in range(6):
        try:
            session.read()
        except CaptureRetry:
            pass
    record, = history.list_recent().records
    row, = rows_from_statistics(record.statistics)
    assert row.presence_complete and row.presence_seconds == 3.
    assert row.full_cycle(Category.DAMAGE) == 70.
    assert row.full_cycle(Category.HEALING) == 11.
    assert row.full_cycle(Category.DAMAGE, effective=True) is None
    display = render_panel(PanelConfig(category=Category.HEALING, ranking=Ranking.DPS,
                                        rate_basis=RateBasis.FULL_CYCLE), [row])
    assert display.metric_label == '治疗 · HPS' and display.rate_label == '全周期'
    assert display.entries[0].value == display.entries[0].rate == 11.
    session.close()


@pytest.mark.parametrize('problem', ['late_start', 'already_deployed', 'nonzero_source',
                                     'unknown_member', 'missing_membership', 'gap', 'wide_interval'])
def test_native_full_cycle_requires_a_zero_opening_and_contiguous_membership(tmp_path, problem):
    start = full_cycle_sample(4, owner_present=problem == 'already_deployed')
    later = full_cycle_sample(30, 30., 5.)
    extra = []
    if problem == 'late_start':
        start = full_cycle_sample(60, owner_present=False)
        later = full_cycle_sample(90, 30., 5.)
    elif problem == 'nonzero_source':
        start['sample']['actor_rows'][0].update(healing=1.)
    elif problem == 'unknown_member':
        later['sample']['presence']['entities'][0]['id'] = 'char_wrong'
    elif problem == 'missing_membership':
        later['sample'].update(presence_status='unavailable', presence=None)
    elif problem == 'gap':
        extra.append({'status': 'unsupported_layout', 'reason': 'real gap'})
    elif problem == 'wide_interval':
        later = full_cycle_sample(210, 30., 5.)
    finish_frame = later['sample']['frame'] + 30
    probe = Probe([start, *extra, later, full_cycle_sample(finish_frame, 60., 15.)])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    session = CollectionSession(None, None, collector=collector, frida_remote=None)
    session.enable_lifecycle()
    session.connect()
    session.read()
    if extra:
        with pytest.raises(CaptureRetry):
            session.read()
    session.read()
    current = session.read()['statistics']
    row, = rows_from_statistics(current)
    assert row.damage == 60. and row.healing == 15.
    assert row.presence_seconds is None and not row.presence_complete
    assert row.full_cycle(Category.DAMAGE) is row.full_cycle(Category.HEALING) is None
    session.close()


def test_native_full_cycle_does_not_divide_a_retained_counter_by_new_presence(tmp_path):
    missing = full_cycle_sample(90, 30., 15.)
    missing['sample']['actor_rows'] = []
    probe = Probe([full_cycle_sample(4, owner_present=False), full_cycle_sample(30, 0., 0.),
                   full_cycle_sample(60, 30., 15.), missing, full_cycle_sample(120, 60., 30.)])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    session = CollectionSession(None, None, collector=collector, frida_remote=None)
    session.enable_lifecycle()
    session.connect()
    for _ in range(3):
        session.read()
    row, = rows_from_statistics(session.read()['statistics'])
    assert (row.damage, row.healing, row.presence_seconds) == (30., 15., 2.)
    assert row.presence_complete and not row.counter_complete
    assert row.full_cycle(Category.DAMAGE) is row.full_cycle(Category.HEALING) is None
    row, = rows_from_statistics(session.read()['statistics'])
    assert row.full_cycle(Category.DAMAGE) == 20.
    assert row.full_cycle(Category.HEALING) == 10.
    session.close()


def test_missing_roster_does_not_advance_or_archive_and_marks_gap(tmp_path):
    missing = sample(60, 200.)
    missing['sample'].update(roster_status='unavailable', roster=None)
    probe = Probe([sample(30, 100.), missing, sample(90, 300., presence=False)])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    session = CollectionSession(None, None, collector=collector, frida_remote=None,
                                history=BattleHistory(tmp_path))
    session.enable_lifecycle()
    session.connect()
    first = session.read()
    with pytest.raises(CaptureRetry):
        session.read()
    assert session.runtime.sessions.state == 'frozen'
    assert session.runtime.sessions.latest_sample['frame'] == first['frame']
    new = session.read()
    assert new['battle_id'] == first['battle_id']
    assert not session.history.list_recent().records
    row = new['statistics']['rows'][OPERATOR]
    assert row['damage'] == 300.
    assert row['present'] is None
    assert row['presence_seconds'] is None
    session.close()


def test_reused_native_roots_keep_new_battle_separate_after_missing_first_roster(tmp_path):
    first = sample(30, 100.)
    reset = sample(5, 0.)
    reset['sample'].update(battle_serial=2, roster_status='unavailable', roster=None)
    recovered = sample(45, 120.)
    recovered['sample'].update(battle_serial=2)
    probe = Probe([first, reset, recovered])
    history = BattleHistory(tmp_path)
    session = CollectionSession(None, None, collector=NativePcCollector(tmp_path, probe_factory=lambda _: probe),
                                frida_remote=None, history=history)
    session.enable_lifecycle()
    session.connect()
    old = session.read()
    with pytest.raises(CaptureRetry):
        session.read()
    new = session.read()
    assert new['battle_id'] != old['battle_id']
    row = new['statistics']['rows'][OPERATOR]
    assert row['damage'] == 120.
    assert row['recent_damage'] is None
    record, = history.list_recent().records
    assert record.battle_id == old['battle_id']
    assert record.statistics['rows'][OPERATOR]['damage'] == 100.
    assert record.metadata['outcome'] == 'unknown'
    session.close()


def test_process_restart_archives_once_without_publishing_unreliable_practice(tmp_path):
    first = sample(3, 50.)
    first['sample'].update(flow('home', 'battle_loader', transiting=False))
    old = Probe([opening(), first, {'status': 'process_changed'}])
    new = Probe([sample(3, 0.)])
    new.process_instance = (old.pid, old.process_instance[1] + 1)
    history = BattleHistory(tmp_path)
    runtime = BattleRuntime()

    def connect(probe):
        collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
        session = CollectionSession(None, None, collector=collector, frida_remote=None, history=history)
        session.enable_lifecycle(runtime)
        session.connect()
        return session

    original = connect(old)
    with pytest.raises(CaptureRetry):
        original.read()
    first_update = original.read()
    with pytest.raises(ProcessChanged):
        original.read()
    original.close()
    assert old.closed
    previous, = history.list_recent().records
    assert previous.battle_id == first_update['battle_id']
    assert previous.metadata['practice'] is None
    assert previous.metadata['outcome'] == 'unknown'
    assert previous.statistics['rows'][OPERATOR]['damage'] == 50.

    replacement = connect(new)
    next_update = replacement.read()
    assert next_update['battle_id'] != first_update['battle_id']
    assert next_update['battle_metadata']['practice'] is None
    assert next_update['statistics']['rows'][OPERATOR]['damage'] == 0.
    prior_only, = history.list_recent().records
    assert prior_only.battle_id == previous.battle_id
    replacement.close()


def test_enemy_snapshot_retains_its_frame_and_clears_at_battle_boundary(tmp_path, monkeypatch):
    now = 10.0
    monkeypatch.setattr('time.monotonic', lambda: now)
    first = sample(30, 100.)
    first['sample'].update(enemies_status='available', enemies={
        'scope': 'unit_manager_enemies_membership', 'collection_count': 1,
        'entities': [dict(instance_uid=103, enemy_key='enemy_1007_slime_2',
                          inited=True, borned=True, is_disappeared=False,
                          native_object_present=True, hp=731.4, hp_status='available')],
    })
    probe = Probe([first, sample(60, 150.), sample(90, 200.), ABSENT, sample(3, 0.)])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    session = CollectionSession(None, None, collector=collector, frida_remote=None)
    session.enable_lifecycle()
    session.connect()

    view = session.read()['enemy']
    assert view['status'] == 'ready' and view['observed_frame'] == 30
    assert view['rows'][0]['instance_uid'] == '103'
    assert view['rows'][0]['hp'] == 731.4 and view['rows'][0]['max_hp'] is None
    assert view['totals']['remaining_hp'] == 731.4 and view['totals']['remaining_hp_complete']
    assert view['totals']['current_count'] == view['totals']['current_hp_count'] == 1
    assert 'entered_total_hp' not in view['totals']
    assert not collector.read_enemies(frame=29, game_time=1)['ready']

    now = 12.4
    updated = session.read()
    assert updated['frame'] == 60 and updated['enemy']['observed_frame'] == 30
    now = 13.
    expired = session.read()['enemy']
    assert not expired['ready'] and expired['error'] == 'enemy_sample_stale'
    with pytest.raises(CaptureRetry):
        session.read()
    next_battle = session.read()
    assert not next_battle['enemy']['ready'] and next_battle['enemy']['rows'] == []
    session.close()


@pytest.mark.parametrize('status,diagnostic,code,error_type', [
    ('process_access_failed', {'win32_error': 5}, 'native_permission_denied', SampleUnavailable),
    ('unsupported_build', None, 'native_unsupported_build', UnsupportedVersion),
])
def test_attach_errors_have_native_diagnostics(tmp_path, status, diagnostic, code, error_type):
    def attach(_):
        raise ResearchFailure(status, 'fixture', diagnostic)

    collector = NativePcCollector(tmp_path, probe_factory=attach)
    with pytest.raises(error_type):
        collector.connect()
    assert collector.diagnostics.as_dict()['code'] == code
    assert not collector.diagnostics.connection_ok


def stage_structure(*, fixed=42, conditional_actions=0, branches=False):
    return dict(schema_version=1, scope='current_stage_waves_spawn_structure',
                wave_count=1, fragment_occurrences=1, action_occurrences=1,
                scheduled_spawn_count=fixed, scheduled_counted_spawn_count=fixed,
                conditional_spawn_count=0, conditional_spawn_action_count=conditional_actions,
                not_count_in_total_spawn_count=0, branch_container_present=branches,
                branches_counted=False, dynamic_spawns_counted=False,
                spawn_actions=[dict(kind='scheduled', enemy_key='enemy_1007_slime_2', count=fixed,
                                    wave_index=0, fragment_index=0, action_index=0,
                                    not_count_in_total=False)])


def planned_sample(frame, structure=None):
    report = sample(frame, 100.)
    report['sample'].update(stage_spawns_status='available',
                            stage_spawns=deepcopy(structure if structure is not None else stage_structure()),
                            stage_spawns_read_cost=dict(calls=263, bytes=15534),
                            enemies_status='available', enemies=dict(
                                scope='unit_manager_enemies_membership', collection_count=0, entities=[]))
    return report


def stage_collector(tmp_path, reports):
    probe = Probe(reports)
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    collector.connect()
    return collector


def stage_view(collector):
    lifecycle = collector.read_lifecycle()
    pair, _ = collector.read_guarded_pair(lifecycle)
    return collector.read_enemies(frame=pair['frame'], game_time=pair['game_time'])


def test_stage_plan_needs_two_distinct_frames_ignores_cost_and_returns_copy(tmp_path):
    structure = stage_structure(conditional_actions=2, branches=True)
    # fixed_count is the counted scheduled subset, not all configured entries.
    structure.update(scheduled_spawn_count=43, not_count_in_total_spawn_count=1)
    first, duplicate, second, skipped = [planned_sample(f, structure) for f in (3, 3, 6, 9)]
    duplicate['sample']['stage_spawns_read_cost']['calls'] = 280
    second['sample']['stage_spawns_read_cost']['bytes'] = 17000
    for key in ('stage_spawns_status', 'stage_spawns', 'stage_spawns_read_cost'):
        skipped['sample'].pop(key)
    collector = stage_collector(tmp_path, [first, duplicate, second, skipped])
    assert stage_view(collector)['stage_plan'] is None
    assert stage_view(collector)['stage_plan'] is None
    confirmed = stage_view(collector)['stage_plan']
    expected = dict(fixed_count=42, conditional_actions=2, branches_present=True)
    assert confirmed == expected
    confirmed['fixed_count'] = 999  # A consumer cannot mutate the stored view.
    assert stage_view(collector)['stage_plan'] == expected
    assert collector.read_enemies(frame=5, game_time=1)['stage_plan'] is None
    collector.close()
    assert collector.read_enemies(frame=9, game_time=1)['stage_plan'] is None


def test_verified_empty_fixed_structure_is_zero_but_failed_read_is_unknown(tmp_path):
    empty = stage_structure(fixed=0)
    empty.update(wave_count=0, fragment_occurrences=0, action_occurrences=0, spawn_actions=[])
    failed = planned_sample(9, empty)
    failed['sample'].update(stage_spawns_status='unavailable', stage_spawns=None)
    collector = stage_collector(tmp_path, [planned_sample(3, empty), planned_sample(6, empty), failed])
    assert stage_view(collector)['stage_plan'] is None
    assert stage_view(collector)['stage_plan'] == dict(fixed_count=0, conditional_actions=0, branches_present=False)
    assert stage_view(collector)['stage_plan'] is None
    collector.close()


@pytest.mark.parametrize('change', ['enemy_key', 'action_index', 'hidden_group', 'failure'])
def test_stage_plan_changes_and_failure_clear_then_require_two_new_frames(tmp_path, monkeypatch, change):
    now = [0.]
    monkeypatch.setattr('native_pc_collector.time.monotonic', lambda: now[0])
    changed = stage_structure()
    if change != 'failure':
        changed['spawn_actions'][0][change] = 9 if change == 'action_index' else 'different'
    third = planned_sample(9, changed)
    if change == 'failure':
        third['sample'].update(stage_spawns_status='unavailable', stage_spawns=None)
    collector = stage_collector(tmp_path, [planned_sample(3), planned_sample(6), third,
                                           planned_sample(9, changed), planned_sample(12, changed)])
    assert stage_view(collector)['stage_plan'] is None
    assert stage_view(collector)['stage_plan']['fixed_count'] == 42
    assert collector._next_stage_plan == 5.
    now[0] = 1.
    assert stage_view(collector)['stage_plan'] is None
    assert collector._next_stage_plan <= 2.  # Drop the former five-second delay.
    # A changed payload may recover, but one same-frame repeat is not confirmation.
    assert stage_view(collector)['stage_plan'] is None
    assert stage_view(collector)['stage_plan']['fixed_count'] == 42
    collector.close()


@pytest.mark.parametrize('change', ['serial', 'stage', 'unknown_stage'])
def test_stage_plan_scope_does_not_leak_across_reset_or_unknown_stage(tmp_path, change):
    third, fourth, fifth = [planned_sample(frame) for frame in (90, 93, 96)]
    if change == 'serial':
        for report in (third, fourth, fifth): report['sample']['battle_serial'] = 2
    elif change == 'stage':
        for report in (third, fourth, fifth): report['sample']['stage_id'] = 'Obt/Main/level_main_01-08'
    else:
        third['sample'].update(stage_status='unavailable', stage_id=None)
    collector = stage_collector(tmp_path, [planned_sample(3), planned_sample(6), third, fourth, fifth])
    assert stage_view(collector)['stage_plan'] is None
    assert stage_view(collector)['stage_plan']['fixed_count'] == 42
    assert stage_view(collector)['stage_plan'] is None
    next_view = stage_view(collector)
    assert (next_view['stage_plan'] is None) is (change == 'unknown_stage')
    assert stage_view(collector)['stage_plan']['fixed_count'] == 42
    collector.close()


@pytest.mark.parametrize('field,value', [('scheduled_counted_spawn_count', True),
                                      ('conditional_spawn_action_count', -1),
                                      ('branch_container_present', 0), ('spawn_actions', None)])
def test_malformed_plan_is_unknown_without_losing_core_sample(tmp_path, field, value):
    malformed = stage_structure()
    malformed[field] = value
    collector = stage_collector(tmp_path, [planned_sample(3), planned_sample(6), planned_sample(9, malformed)])
    assert stage_view(collector)['stage_plan'] is None
    assert stage_view(collector)['stage_plan']['fixed_count'] == 42
    view = stage_view(collector)
    assert view['ready'] and view['stage_plan'] is None
    collector.close()


def test_stage_plan_process_change_and_failed_core_clear_old_value(tmp_path):
    failed_core = planned_sample(9)
    failed_core['sample'].update(roster_status='unavailable', roster=None,
                                stage_spawns_status='unavailable', stage_spawns=None)
    collector = stage_collector(tmp_path, [planned_sample(3), planned_sample(6), failed_core,
                                           planned_sample(12), planned_sample(15), {'status': 'process_changed'}])
    stage_view(collector)
    assert stage_view(collector)['stage_plan']['fixed_count'] == 42
    with pytest.raises(SampleUnavailable): stage_view(collector)
    assert collector.read_enemies(frame=9, game_time=3)['stage_plan'] is None
    assert stage_view(collector)['stage_plan'] is None
    assert stage_view(collector)['stage_plan']['fixed_count'] == 42
    with pytest.raises(ProcessChanged): collector.read_lifecycle()
    assert collector.read_enemies(frame=15, game_time=5)['stage_plan'] is None
    collector.close()


def test_stage_plan_only_reaches_enemy_view_not_statistics_or_history(tmp_path):
    history = BattleHistory(tmp_path / 'history')
    probe = Probe([planned_sample(3), planned_sample(6), ABSENT])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    session = CollectionSession(None, None, collector=collector, frida_remote=None, history=history)
    session.enable_lifecycle()
    session.connect()
    first, confirmed = session.read(), session.read()
    assert first['enemy']['stage_plan'] is None
    assert confirmed['enemy']['stage_plan'] == dict(fixed_count=42, conditional_actions=0, branches_present=False)
    assert 'stage_plan' not in confirmed and 'stage_spawns' not in confirmed
    assert 'stage_plan' not in repr(confirmed['statistics'])
    with pytest.raises(CaptureRetry): session.read()
    record, = history.list_recent().records
    assert 'stage_plan' not in repr(record.to_dict()) and 'stage_spawns' not in repr(record.to_dict())
    session.close()


def test_stage_capture_is_separate_from_enemy_and_after_existing_fields(tmp_path, monkeypatch):
    import native_pc_collector as native
    calls, now = [], [0.]
    monkeypatch.setattr(native.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(native, '_capture_fields', lambda *_: calls.append('core') or {'core': True})
    monkeypatch.setattr(native, 'capture_enemies', lambda *_: calls.append('enemies') or {'enemies_status': 'available'})
    monkeypatch.setattr(native, 'capture_game_flow', lambda *_: calls.append('flow') or {'game_flow_status': 'available'})
    def stage(*_):
        calls.append('stage')
        raise ResearchFailure('unsupported_layout', 'optional_read_budget_exhausted')
    monkeypatch.setattr(native, 'capture_stage_spawns', stage)
    collector = NativePcCollector(tmp_path)
    first = collector._capture(None, {})
    assert calls == ['core', 'enemies', 'flow'] and 'stage_spawns_status' not in first
    calls.clear()
    now[0] = .2
    optional = collector._capture(None, {})
    assert calls == ['core', 'flow', 'stage']
    assert optional['core'] and optional['game_flow_status'] == 'available'
    assert optional['stage_spawns_status'] == 'unavailable'
    calls.clear()
    now[0] = 1.2
    collector._capture(None, {})  # Both are due; enemy retains its existing priority.
    assert calls == ['core', 'enemies', 'flow']
    calls.clear()
    now[0] = 1.3
    collector._capture(None, {})
    assert calls == ['core', 'flow', 'stage']
