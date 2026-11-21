from copy import deepcopy

import pytest

from battle_history import BattleHistory
from collection_session import CollectionSession
from collector import SampleUnavailable, UnsupportedVersion
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

    def sample(self, *, extra_reader):
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
