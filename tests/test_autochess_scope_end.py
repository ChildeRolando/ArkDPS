"""Logical round retirement needs an unchanged, fully verified source cursor."""

from dataclasses import asdict, replace
from copy import deepcopy
from types import SimpleNamespace

import pytest

from battle_history import BattleRecord
from battle_lifecycle import BattleSessions
from capture_battles import observation_from_json
from collection_session import CollectionSession
from desktop_bridge import DesktopState
from panel_model import PanelConfig, RateBasis
from test_android_autochess_acquisition import AndroidAutoChessBattle
from test_pc_autochess_cross_frame_admission import CrossingPcBattle


class OwnedScopeEnd:
    def __init__(self, platform, *, phase=6, start=0, mode=None):
        self.platform = platform
        self.fixture = CrossingPcBattle() if platform == 'native_pc' else AndroidAutoChessBattle()
        self.records = []
        self.fixture.runtime.record = self.records.append
        self.extra_epoch = 0
        self.mode = mode or ('all' if phase == 5 else 'local')
        if platform == 'native_pc':
            raw_binding = self.fixture.binding

            def binding():
                result = raw_binding()
                result['native_autochess_context'].update(
                    collection_mode=self.mode, match_key='0x400000:0x410000')
                return result

            self.fixture.binding = binding
        else:
            self.fixture.mode = self.mode
        self.raw_mark = self.fixture.observer.mark
        self.fixture.observer.mark = self.mark
        self.advance(start, phase=phase)
        self.fixture.poll()
        self.advance(1, amount=300)
        self.fixture.poll()
        self.last_frame = self.fixture.runtime.sessions.latest_sample['frame']

    def mark(self):
        value = self.raw_mark()
        value.update(active_hooks=[], stopped=False, error=None,
                     terminal_boundary=None,
                     native_generation=value.get('native_generation', 1),
                     native_battle_state=2, quality_contract_version=1,
                     damage_settlement_quality_version=1, damage_settlement_source_quality_version=1,
                     damage_settlement_unlocalized_unknown=False,
                     damage_settlement_source_issues=[], damage_settlement_first_issue=None,
                     unknown_sources=[], engagement_unknown_sources=[])
        value['epoch'] += self.extra_epoch
        return value

    def advance(self, when, *, phase=None, next_round=False, amount=None):
        battle = self.fixture
        if self.platform == 'native_pc':
            if phase is not None:
                if battle.native_phase != phase:
                    battle.autochess_epoch += 1
                battle.native_phase = phase
            if next_round:
                battle.native_round += 1
                battle.autochess_epoch += 1
            battle.advance(when)
            if amount is not None:
                event = battle.scoped_hit(battle.sequence + 1, 3, amount)
                event['native_autochess_scope']['collection_mode'] = self.mode
                battle.advance(when, [event])
        else:
            round_ = battle.round + 1 if next_round else None
            epoch = battle.epoch + 1 if round_ is not None else None
            battle.advance(when, phase=phase, round_=round_, epoch=epoch,
                           amounts=() if amount is None else ((3, amount),))

    def finish(self, *, phase=None, next_round=False, amount=None):
        self.advance(1.5, phase=phase, next_round=next_round, amount=amount)
        successor = self.fixture.poll()
        archive, = self.fixture.runtime.sessions.pop_archives()
        return successor, archive


def old_local(archive):
    return next(player for player in archive['last_sample']['multiplayer']['players'] if player['is_local'] is True)


def selected_effective(tmp_path, archive, rate_basis=RateBasis.RECENT_5S):
    state = DesktopState(tmp_path)
    state.history_config = PanelConfig(panel_id='history', effective_only=True, rate_basis=rate_basis)
    state.selected = BattleRecord.from_archive(archive)
    return state._selected_history()['view']['entries']


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('old_phase,new_phase,next_round', [
    (6, 7, False), (5, 7, False), (4, 7, False),
    (4, 5, False),
    (4, 3, True), (5, 3, True), (4, 4, True), (5, 5, True), (6, 6, True),
])
def test_certified_no_tail_finishes_at_accepted_old_watermark_and_history_has_no_scope_star(
        platform, old_phase, new_phase, next_round, tmp_path):
    case = OwnedScopeEnd(platform, phase=old_phase)
    commits = list(case.fixture.commits)
    successor, archive = case.finish(phase=new_phase, next_round=next_round)
    assert archive['event_status'] == 'finished' and archive['event_error'] is None
    assert archive['last_sample']['frame'] == case.last_frame
    assert archive['last_sample']['game_time'] == archive['statistics']['game_time'] == 1
    player = old_local(archive)
    actor, = player['actor_ledgers']
    assert actor['ledger']['complete'] and actor['ledger']['effective_damage'] == 300
    assert actor['ledger']['observed_damage_window'] == (0, 1)
    assert actor['ledger']['recent_effective_damage'] is None
    assert player['observed_window'] == [0, 1]
    entry, = selected_effective(tmp_path, archive)
    assert entry['value'] == 300 and entry['rate'] is None
    assert 'effective_damage' not in entry['coverage']
    whole_cycle, = selected_effective(tmp_path, archive, RateBasis.FULL_CYCLE)
    assert whole_cycle['rate'] is None
    if new_phase in (3, 7):
        assert successor.kind == 'preparing' and successor.events is None
        assert case.fixture.commits == commits
    else:
        assert successor.kind == 'sample'
        assert case.fixture.commits == commits + [commits[-1]]
        assert not any(player['observed_damage'] for player in
                       case.fixture.runtime.display_sample()['multiplayer']['players'])
    assert successor.autochess_successor['sequence'] == commits[-1]

    replay = BattleSessions(successor.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    recreated, = replay.pop_archives()
    assert recreated == archive


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
def test_no_tail_close_preserves_a_real_unverified_hit(platform, tmp_path):
    case = OwnedScopeEnd(platform)
    case.advance(1.25, amount=75)
    pending = case.fixture.queue if platform == 'native_pc' else case.fixture.events
    missing = pending[-1]
    missing.update(amount=0, settlement_complete=False, shield_absorbed=None)
    missing.pop('nominal_amount')
    case.fixture.poll()
    _, archive = case.finish(phase=7)
    assert archive['event_status'] == 'finished'
    actor, = old_local(archive)['actor_ledgers']
    assert actor['ledger']['unverified_hits'] == 1 and actor['ledger']['observed_hits'] == 2
    entry, = selected_effective(tmp_path, archive)
    assert entry['value'] == 300
    assert entry['coverage']['effective_damage']['basis_kind'] == 'hits'
    assert entry['coverage']['effective_damage']['failed_share'] == .5


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('unlocalized', [False, True])
@pytest.mark.parametrize('phase', [5, 6])
@pytest.mark.parametrize('historical_time', [0, 100])
@pytest.mark.parametrize('new_callback', [False, True])
def test_retained_prior_phase_numeric_issue_does_not_break_no_tail_transport_end(
        platform, unlocalized, phase, historical_time, new_callback):
    case = OwnedScopeEnd(platform, phase=phase)
    raw_mark = case.fixture.observer.mark
    sample = case.fixture.runtime.sessions.latest_sample
    # The native controller retains quality certificates across logical combat
    # phases. This is deliberately an earlier scope, not current-scope evidence.
    scope = deepcopy(sample['autochess_context']['identity'])
    scope.update(protocol_version=1, status='available', verified_same_frame=True,
                 scope_key=scope['scope_key'].rsplit(':', 1)[0] + ':0',
                 phase=4, round=0, frame=historical_time * 20, game_time=historical_time,
                 collection_mode='local' if case.mode == 'all' else 'all')
    issue = dict(source='char_001_amiya', source_character_uid=3,
                 frame=historical_time * 20, game_time=historical_time, native_autochess_scope=scope)

    def mark():
        value = raw_mark()
        value.update(enemy_damage_unknown=True,
                     damage_settlement_unlocalized_unknown=unlocalized,
                     damage_settlement_source_issues=[issue],
                     damage_settlement_first_issue=dict(frame=historical_time * 20,
                                                        game_time=historical_time, sequence=0,
                                                        reason='unverified enemy settlement domain'))
        return value

    case.fixture.observer.mark = mark
    case.advance(1.25, amount=75)
    pending = case.fixture.queue if platform == 'native_pc' else case.fixture.events
    pending[-1].update(amount=0, settlement_complete=False, shield_absorbed=None)
    pending[-1].pop('nominal_amount')
    case.fixture.poll()
    if new_callback:
        # Unknown was already sticky: another callback must still invalidate
        # the quiet-prefix proof even when it emits no additional record.
        case.extra_epoch += 1
    _, archive = case.finish(phase=7)
    assert archive['event_status'] == ('gap' if new_callback else 'finished')
    assert archive['event_error'] == ('未取得最终事件水位' if new_callback else None)
    assert archive['statistics']['game_time'] == 1.25
    assert archive['last_sample']['game_time'] == 1.25
    assert old_local(archive)['observed_damage'] == 300
    actor, = old_local(archive)['actor_ledgers']
    assert actor['ledger']['unverified_hits'] == 1
    assert actor['ledger']['observed_hits'] == 2


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
def test_lifecycle_receiver_replacement_carries_only_retired_numeric_witnesses(platform):
    case = OwnedScopeEnd(platform, phase=4)
    old = case.fixture.runtime.sessions.latest_sample
    event = case.records[-1].events['drain']['events'][0]
    scope = dict(protocol_version=1, status='available', verified_same_frame=True,
                 **old['autochess_context']['identity'], frame=old['frame'],
                 game_time=old['game_time'], collection_mode='local')
    issue = dict(source=event['source'], source_character_uid=event['source_character_uid'],
                 frame=old['frame'], game_time=old['game_time'], native_autochess_scope=scope)
    original_mark = case.fixture.observer.mark

    def mark():
        value = original_mark()
        value.update(enemy_damage_unknown=True, damage_settlement_unknown=True,
                     damage_settlement_source_issues=[issue],
                     damage_settlement_first_issue=dict(frame=old['frame'], game_time=old['game_time'],
                                                        sequence=event['sequence'], reason='unverified settlement'))
        return value

    case.fixture.observer.mark = mark
    previous_receiver = case.fixture.runtime.sessions.multiplayer
    case.advance(1.5, phase=6)
    case.fixture.poll()
    receiver = case.fixture.runtime.sessions.multiplayer
    assert receiver is not previous_receiver
    assert previous_receiver._context_fingerprint[:3] in receiver._retired_settlement_scopes
    case.advance(2, amount=45)
    case.fixture.poll()
    assert all(not p.statistics.settlement_stream_incomplete for p in receiver._players.values())
    assert all(not p.statistics.settlement_source_incomplete for p in receiver._players.values())
    assert '结算事件质量未完整确认' not in receiver.result()['reasons']
    assert case.records[-1].events['after']['damage_settlement_source_issues'] == [issue]
    replay = BattleSessions(case.records[-1].feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    assert replay.multiplayer.result() == receiver.result()


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('phase', [3, 102])
def test_same_round_prepare_or_restart_do_not_prove_the_old_scope_ended(platform, phase):
    case = OwnedScopeEnd(platform, phase=4)
    successor, archive = case.finish(phase=phase)
    assert successor.autochess_successor is None
    assert archive['event_status'] == 'gap'


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('fault', ['unread_event', 'callback_without_output', 'different_observer',
                                  'retired_observer_reused', 'late_start'])
def test_real_producer_handoff_retains_gap_and_preserves_old_values(platform, fault, tmp_path):
    case = OwnedScopeEnd(platform, start=.5 if fault == 'late_start' else 0)
    if fault == 'callback_without_output':
        case.extra_epoch += 1
    if fault == 'different_observer':
        case.fixture.observer = SimpleNamespace(**vars(case.fixture.observer))
    if fault == 'retired_observer_reused':
        observer = case.fixture.observer
        observer.stop = lambda: None
        session = CollectionSession(None, None)
        session.runtime = case.fixture.runtime
        session.observer = observer
        assert session._retire_observer()
    commits = list(case.fixture.commits)
    if fault == 'unread_event':
        case.advance(1.25, amount=700)
    successor, archive = case.finish(phase=7)
    assert successor.kind == 'preparing' and successor.events is None
    assert case.fixture.commits == commits
    if fault in ('different_observer', 'retired_observer_reused'):
        assert successor.autochess_successor is None
    else:
        assert successor.autochess_successor is not None
    assert archive['event_status'] == 'gap' and archive['event_error'] == '未取得最终事件水位'
    assert old_local(archive)['observed_damage'] == 300
    entry, = selected_effective(tmp_path, archive)
    assert entry['coverage']['effective_damage']['basis_kind'] == 'scope'


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('field,value', [
    ('active', 1), ('active_hooks', ['Enemy.SetHp']), ('native_deferred_callbacks', 1),
    ('native_quiet_boundary_version', 1), ('retry', True), ('stopped', True),
    ('error', 'source failed'), ('native_generation', 9), ('damage_settlement_scope', 13),
    ('native_battle_state', 3), ('quality_contract_version', 2),
    ('damage_settlement_quality_version', 2), ('enemy_damage_unknown', True),
    ('shield_unknown', None), ('mitigation_unknown', 0), ('unknown_sources', ['char_x']),
    ('native_process_pid', 99), ('native_frame', 3), ('native_fixed_time', '0'),
])
def test_replay_does_not_trust_a_candidate_with_changed_raw_successor_evidence(platform, field, value):
    case = OwnedScopeEnd(platform)
    successor, archive = case.finish(phase=7)
    assert archive['event_status'] == 'finished'
    evidence = deepcopy(successor.autochess_successor)
    evidence['before'][field] = value
    replay = BattleSessions(successor.feed)
    for record in case.records[:-1]:
        assert replay.consume(record)
    assert replay.consume(replace(successor, autochess_successor=evidence))
    recreated, = replay.pop_archives()
    assert recreated['event_status'] == 'gap'
    assert old_local(recreated)['observed_damage'] == 300


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('field,value', [
    ('damage_settlement_source_issues', [{}]), ('damage_settlement_first_issue', 'invalid'),
    ('unknown_sources', [{}]), ('engagement_unknown_sources', [{}]), ('retry', []),
])
def test_identically_malformed_old_and_successor_quality_is_not_a_valid_end(platform, field, value):
    case = OwnedScopeEnd(platform)
    successor, archive = case.finish(phase=7)
    assert archive['event_status'] == 'finished'
    replay = BattleSessions(successor.feed)
    for record in deepcopy(case.records):
        for evidence in (record.events, record.autochess_successor):
            if evidence:
                for side in ('before', 'after'):
                    evidence[side][field] = value
        assert replay.consume(record)
    rejected, = replay.pop_archives()
    assert rejected['event_status'] == 'gap'
