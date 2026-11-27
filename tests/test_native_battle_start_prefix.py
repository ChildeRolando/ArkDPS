"""Retained ordinary-PC zero-origin events, without invented counter snapshots."""
from copy import deepcopy

import pytest

from event_capture import LiveEventCapture, read_event_batch, source_attribution_batch
from battle_lifecycle import BattleObservation, BattleSessions
from desktop_bridge import DesktopState
from panel_model import PanelConfig
from event_probe import NATIVE_PC_CONTRACT, StreamGap, verified_native_battle_start
from roster import CharacterEntry, RosterSnapshot
from test_deployment_lifecycle import proof
from test_event_probe_parity import declared_quality
from test_event_probe_round_start_contract import inputs


STAGE = 'Obt/Hard/level_hard_07-04'


def fixture():
    sample, batch, mark = inputs(events=[])
    sample.pop('autochess_context')
    sample.pop('autochess_round_start', None)
    sample.pop('native_counter_scope')
    sample.pop('autochess_snapshot_anchor')
    sample['battle_metadata']['stage_id'] = STAGE
    for stamp in (sample['native_window_guard']['before'], sample['native_window_guard']['after']):
        stamp['stage_id'] = STAGE
    sample.update(rows=[dict(id='char_a', damage=80, healing=0)], global_total=80, global_heal=0,
                  presence_duration_supported=True)
    mark.pop('native_autochess_context')
    mark.update(native_stage_id=STAGE, sequence=3, epoch=110, **declared_quality(),
                enemy_entry_ready=True, enemy_state_ready=True, character_lifecycle_ready=True)
    point = {key: deepcopy(mark[key]) for key in ('native_identity', 'native_generation',
        'native_process_pid', 'native_stage_id', 'damage_settlement_scope')}
    point.update(version=1, source='native-pc-producer', method='BattleController.set_state',
                 phase='leave', state=2, hooks_ready=True, native_frame=0, native_fixed_time='0',
                 frame=0, game_time=0, sequence=0, active=0, epoch=100)
    mark['initial_boundary'] = point
    records = [
        dict(sequence=1, kind='enemy_enter', source=None, amount=0, frame=0, game_time=0,
             target='enemy_1506_patrt', target_instance='44', hp_after=60_000,
             enemy_entry_attributes=dict(max_hp=60_000, atk=100, **{'def': 500}, res=50)),
        dict(sequence=2, kind='character_born', source='char_a', amount=0, frame=15, game_time=.5,
             source_character_uid=301, source_instance_uid=10,
             actor_lifecycle=proof('born', .5, actor=301)),
        dict(sequence=3, kind='damage_settlement', source='char_a', amount=80, nominal_amount=80,
             source_character_uid=301, source_instance_uid=10, frame=30, game_time=1,
             target='enemy_1506_patrt', target_uid='44', target_instance='enemy-body',
             damage_type=1, shield_absorbed=0, settlement_complete=True, killed=False),
    ]
    # The real fixed clock certificate is integer text, not an inferred float.
    records[1]['actor_lifecycle'].update(frame=15, fixed=str(1 << 31))
    batch = dict(deepcopy(mark), events=[], probe_contract=NATIVE_PC_CONTRACT.describe())
    roster = RosterSnapshot(frame=30, local_player_side=0, present_known=True,
        present_unique_ids=frozenset({301}), entries=(CharacterEntry('char_a', 301, False, False, None, None, 0),))
    return sample, batch, mark, records, roster


class Observer:
    contract = NATIVE_PC_CONTRACT

    def __init__(self, batch, records):
        self.batch, self.records = batch, records
        self._committed_sequence = 0
        self.calls = []

    def read_baseline(self, sequence):
        self.calls.append(('baseline', sequence))
        return deepcopy(self.batch)

    def read_through(self, sequence):
        raise AssertionError('the explicit retained range owns prefix acquisition')

    def read_round_start_range(self, after, through):
        self.calls.append(('prefix', after, through))
        return dict(deepcopy(self.batch), events=deepcopy(self.records[after:through]))

    def commit_events(self, sequence):
        self._committed_sequence = sequence


def acquired():
    sample, batch, mark, records, roster = fixture()
    observer = Observer(batch, records)
    batch = read_event_batch(observer, mark['sequence'], baseline=True)
    return sample, batch, mark, records, roster, observer


def test_ordinary_start_replays_enemy_birth_body_and_damage_before_first_snapshot_once():
    sample, batch, mark, records, roster, observer = acquired()
    original = deepcopy(sample)
    assert observer.calls == [('baseline', 3), ('prefix', 0, 3)]
    assert observer._committed_sequence == 0
    capture = LiveEventCapture(None)
    attribution = source_attribution_batch(batch, 3, 3, sample=sample, mark=mark)
    evidence = list(attribution['source_events'])
    assert evidence == records
    attribution['source_events'] = evidence
    capture.adapter.update(sample, roster, **attribution)
    result = capture.ingest(sample, roster, 3, batch, mark=mark)
    assert result['coverage_start'] == 0 and result['game_time'] == 1
    row = result['rows']['char_a']
    assert row['damage'] == row['observed_damage'] == 80
    assert row.get('damage_scope_reason') is None
    assert row['settled_damage']['observed_damage_window'] == (0, 1)
    assert row['lifecycle_presence_seconds'] == .5
    assert row['lifecycle_presence_complete']
    assert result['enemy_entry']['start_frame'] == result['enemy_state']['start_frame'] == 0
    assert result['enemy_entry']['events'][0]['enemy_entry_attributes']['max_hp'] == 60_000
    assert capture.adapter.statistics.sources['char_a']['history'][0] == (1, dict(damage=80, healing=0))
    assert capture.adapter.last_frame == 30 and capture.adapter.last_game_time == 1
    assert sample == original
    replayed = capture.ingest(sample, roster, 3, dict(batch, events=records), mark=mark)
    assert replayed['rows']['char_a']['observed_damage'] == 80


def test_replayed_ordinary_skill_window_and_body_lifetime_reach_the_inspector_model(tmp_path):
    sample, batch, mark, records, roster = fixture()
    identity = dict(source='char_a', source_character_uid=301, source_instance_uid=10,
                    amount=1, skill_key='owned_skill', skill_id='owned_skill')
    records = records[:2] + [
        dict(identity, sequence=3, frame=18, game_time=.6, kind='skill_start',
             proof=dict(method='Character.SwitchToSkillState')),
        dict(identity, sequence=4, frame=26, game_time=.85, kind='skill_end',
             proof=dict(method='Character.OnSkillFinish')),
        dict(records[2], sequence=5),
    ]
    mark['sequence'] = batch['sequence'] = 5
    batch = read_event_batch(Observer(batch, records), 5, baseline=True)
    capture = LiveEventCapture(None)
    capture.adapter.update(sample, roster,
        **source_attribution_batch(batch, 5, 5, sample=sample, mark=mark))
    result = capture.ingest(sample, roster, 5, batch, mark=mark)
    row = result['rows']['char_a']
    assert row['lifecycle_presence_seconds'] == .5
    assert row['skill_active_seconds'] == .25 and row['skill_coverage_complete']
    view = DesktopState(tmp_path).view(PanelConfig(), result)
    detail = view['details_by_owner']['char_a']
    assert detail['presence_seconds'] == .5 and detail['presence_complete']
    assert detail['skill_coverage'] == .5


@pytest.mark.parametrize('fault', ['missing', 'gap', 'reverse_clock', 'scope', 'event_binding',
    'event_raw_clock', 'roots', 'process_birth', 'epoch', 'point_bool', 'cursor', 'not_complete',
    'point_build', 'point_birth', 'point_unknown', 'not_playing'])
def test_incomplete_or_conflicting_initial_prefix_never_backdates_event_coverage(fault):
    sample, batch, mark, records, roster, _ = acquired()
    if fault == 'missing': batch['native_battle_start_events'].pop()
    elif fault == 'gap': batch['native_battle_start_events'][1]['sequence'] = 22
    elif fault == 'reverse_clock': batch['native_battle_start_events'][2]['game_time'] = .1
    elif fault == 'scope': batch['native_battle_start_events'][0]['native_autochess_scope'] = {'status': 'available'}
    elif fault == 'event_binding': batch['native_battle_start_events'][0]['native_generation'] = 99
    elif fault == 'event_raw_clock': batch['native_battle_start_events'][0]['native_fixed_time'] = '1'
    elif fault == 'roots': mark['native_identity']['level'] = '0x90000'
    elif fault == 'process_birth': sample['native_window_guard']['process_instance_after'][1] += 1
    elif fault == 'epoch': mark['epoch'] = 99
    elif fault == 'point_bool': mark['initial_boundary']['sequence'] = False
    elif fault == 'cursor': batch['native_battle_start_committed_sequence'] = 1
    elif fault == 'point_build':
        batch['initial_boundary']['source_build'] = mark['initial_boundary']['source_build'] = 'other-build'
    elif fault == 'point_birth':
        batch['initial_boundary']['native_process_created_filetime'] = mark['initial_boundary']['native_process_created_filetime'] = 1
    elif fault == 'point_unknown':
        batch['initial_boundary']['skill_unknown'] = mark['initial_boundary']['skill_unknown'] = True
    elif fault == 'not_playing': sample['state'] = 1
    else: batch['native_battle_start_events_complete'] = False
    capture = LiveEventCapture(None)
    capture.adapter.update(sample, roster)
    with pytest.raises(StreamGap): capture.ingest(sample, roster, 3, batch, mark=mark)
    assert capture.adapter.statistics.start_time == 1
    assert capture.adapter.statistics.sources['char_a']['values']['damage'] == 80


def test_old_ordinary_unknown_scope_envelope_is_not_a_guard_scope():
    sample, batch, mark, _, _, _ = acquired()
    for record in batch['native_battle_start_events']:
        record['native_autochess_scope'] = dict(protocol_version=1, status='unknown', verified_same_frame=False,
            frame=record['frame'], game_time=record['game_time'], reason='captured native scope unavailable')
    assert verified_native_battle_start(sample, batch, mark)


def test_late_or_already_acknowledged_attach_keeps_its_current_baseline():
    sample, batch, mark, records, roster = fixture()
    observer = Observer(batch, records)
    observer._committed_sequence = 2
    batch = read_event_batch(observer, 3, baseline=True)
    assert observer.calls == [('baseline', 3)]
    assert verified_native_battle_start(sample, batch, mark) is None
    capture = LiveEventCapture(None)
    capture.adapter.update(sample, roster)
    result = capture.ingest(sample, roster, 3, batch, mark=mark)
    assert result['coverage_start'] == 1
    assert not result['rows']['char_a']['lifecycle_presence_complete']


@pytest.mark.parametrize('fault', [None, 'second_page_scope', 'second_page_range'])
def test_retained_native_prefix_is_paged_before_any_ack_and_rejects_changed_later_pages(fault):
    _, batch, _, records, _ = fixture()
    records = [dict(records[0], sequence=index + 1) for index in range(2050)]
    batch['sequence'] = 2050
    observer = Observer(batch, records)
    original = observer.read_round_start_range
    def read(after, through):
        page = original(after, through)
        if after and fault == 'second_page_scope': page['damage_settlement_scope'] += 1
        if after and fault == 'second_page_range': page['events'].pop()
        return page
    observer.read_round_start_range = read
    if fault is None:
        result = read_event_batch(observer, 2050, baseline=True)
        assert len(result['native_battle_start_events']) == 2050
    else:
        with pytest.raises(StreamGap): read_event_batch(observer, 2050, baseline=True)
    assert observer.calls == [('baseline', 2050), ('prefix', 0, 2048), ('prefix', 2048, 2050)]
    assert observer._committed_sequence == 0


@pytest.mark.parametrize('origin,sequence', [(True, 1), (1, 0), (None, 0)])
def test_rejected_origin_start_is_transactional(origin, sequence):
    sample, _, _, _, roster = fixture()
    capture = LiveEventCapture(None)
    capture.adapter.update(sample, roster)
    with pytest.raises(ValueError):
        capture.adapter.start_events(session=sample['session'], frame=30, sequence=sequence, retained_zero_origin=origin)
    assert capture.adapter.statistics.event_coverage is False
    assert capture.adapter.statistics.start_time == 1


@pytest.mark.parametrize('fault', [None, 'missing', 'epoch', 'sequence', 'clock', 'roots'])
def test_real_final_source_fence_cuts_off_deployed_bodies_but_gap_or_altered_fence_cannot(fault):
    sample, batch, mark, _, roster, _ = acquired()
    sample['session'] = 'test:1'
    sample['battle_metadata']['evidence'] = 'owned-guarded-fixture'
    sessions = BattleSessions('test')
    assert sessions.consume(BattleObservation('test', 1, 1, 'sample', sample, roster,
        events=dict(before=mark, after=mark, sequence=3, drain=batch)))
    assert sessions.event_status == 'recording', sessions.event_error
    assert sessions.adapter.result()['rows']['char_a']['lifecycle_presence_seconds'] == .5
    boundary = deepcopy(mark)
    if fault == 'epoch': boundary['epoch'] += 1
    elif fault == 'sequence': boundary['sequence'] += 1
    elif fault == 'clock': boundary['game_time'] += 1
    elif fault == 'roots': boundary['source_build'] = 'another-build'
    terminal = {} if fault == 'missing' else dict(terminal_boundary=boundary)
    assert sessions.consume(BattleObservation('test', 2, 1, 'end', reason='battle_absent', events=terminal))
    archive, = sessions.pop_archives()
    row = archive['statistics']['rows']['char_a']
    assert row['lifecycle_presence_complete'] is (fault is None)
    assert row['lifecycle_terminal_complete'] is (fault is None)
    assert row['lifecycle_presence_seconds'] == (.5 if fault is None else None)


def test_quiet_clock_reset_successor_cannot_invent_a_terminal_body_fence(monkeypatch):
    sample, batch, mark, _, roster, _ = acquired()
    sample['session'] = 'test:1'
    sessions = BattleSessions('test')
    sessions.consume(BattleObservation('test', 1, 1, 'sample', sample, roster,
        events=dict(before=mark, after=mark, sequence=3, drain=batch)))
    # The existing successor path can freeze statistics without a raw OnDestroy
    # boundary. Exercise that distinct admission branch, not a terminal event.
    monkeypatch.setattr(sessions, '_clock_reset_matches_watermark', lambda *_: True)
    assert sessions._finish_at_verified_watermark(BattleObservation('test', 2, 2, 'sample'))
    row = sessions.adapter.result()['rows']['char_a']
    assert sessions.adapter.statistics.lifecycle_terminal_watermark is None
    assert row['lifecycle_presence_seconds'] is None
    assert not row['lifecycle_terminal_complete']
