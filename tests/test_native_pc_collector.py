from copy import deepcopy

import pytest

from battle_history import BattleHistory
from battle_lifecycle import BattleRuntime
from collection_session import CollectionSession
from collector import ProcessChanged, SampleUnavailable, UnsupportedVersion
from event_capture import CaptureRetry
from native_pc.probe_native_pc_battle import ResearchFailure
from native_pc_collector import BUILD_ID, NativePcCollector


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
            data_uid=10, player_side=0, side=1, borned=True, is_disappeared=False)]} if presence else None)}


ABSENT = {'status': 'waiting_for_battle', 'reason': 'singleton_is_null'}


def flow(source, target, *, transiting):
    return dict(game_flow_status='available', game_flow=dict(
        source_scene=source, target_scene=target, transiting=transiting))


def opening(practice=True, stage='main_01-07'):
    return dict(ABSENT, **flow('home', 'battle_loader', transiting=True),
                practice_selection_status='available', practice_selection=dict(stage_id=stage, practice=practice))


@pytest.mark.parametrize('loader_stays_open', [True, False])
def test_idle_choice_requires_same_loader_before_and_after_read(tmp_path, monkeypatch, loader_stays_open):
    loading = flow('home', 'battle_loader', transiting=True)
    after = loading if loader_stays_open else flow('home', 'battle_loader', transiting=False)
    flows = iter((loading, after))
    monkeypatch.setattr('native_pc_collector.capture_game_flow', lambda *_: next(flows))
    monkeypatch.setattr('native_pc_collector.capture_practice_selection', lambda *_: {
        'practice_selection_status': 'available', 'practice_selection': dict(stage_id='main_01-07', practice=True)})
    fields = NativePcCollector(tmp_path)._capture_idle(None, {})
    assert ('practice_selection' in fields) is loader_stays_open


@pytest.mark.parametrize(('state', 'result', 'outcome'), [(2, 0, 'abandoned'), (3, 2, 'defeat')])
def test_opening_mode_and_finish_outcome_reach_history_once(tmp_path, state, result, outcome):
    start = sample(6, 5.)
    start['sample'].update(flow('home', 'battle_loader', transiting=False))
    finish = sample(6, 7., state=state)
    finish['sample'].update(flow('level_main_01-07', 'battle_finish', transiting=True), battle_result=result)
    probe = Probe([opening(), dict(ABSENT, game_flow_status='unavailable'), start, finish, ABSENT, ABSENT])
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
    assert record.metadata['practice'] is True
    assert record.metadata['outcome'] == outcome
    assert record.metadata['platform'] == 'native_pc'
    assert record.statistics['rows'][OPERATOR]['damage'] == 7.
    session.close()


@pytest.mark.parametrize(('choice', 'frame', 'wait', 'expected'), [
    (None, 6, 0, None),
    (opening(False), 6, 4, False),
    (opening(False, 'main_01-08'), 6, 4, None),
    (opening(), 151, 4, None),
    (opening(), 6, 16, None),
    (dict(opening(), **flow('battle_finish', 'home', transiting=False)), 6, 4, None),
])
def test_practice_requires_fresh_matching_opening_and_does_not_carry_over(
        tmp_path, monkeypatch, choice, frame, wait, expected):
    now = 0.
    monkeypatch.setattr('native_pc_collector.time.monotonic', lambda: now)
    start = sample(frame, 5.)
    start['sample'].update(flow('home', 'battle_loader', transiting=False))
    restart = deepcopy(start)
    restart['sample'].update(battle_serial=2)
    probe = Probe([*([choice] if choice is not None else []), start, restart])
    collector = NativePcCollector(tmp_path, probe_factory=lambda _: probe)
    collector.connect()
    if choice is not None:
        assert collector.read_lifecycle()['battle'] is None
    now += wait
    assert collector.read_lifecycle()['metadata']['practice'] is expected
    assert collector.read_lifecycle()['metadata']['practice'] is None
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


def test_process_restart_archives_once_and_reuses_no_previous_practice(tmp_path):
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
    assert previous.metadata['practice'] is True
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
