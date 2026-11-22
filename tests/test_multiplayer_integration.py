"""Native acquisition -> player ledgers -> history -> shared desktop view.

All clocks, callbacks and records are local fixtures; these tests acquire no
device, open no window and do not replace producer quality declarations.
"""
from copy import deepcopy
from dataclasses import replace
import time
from types import SimpleNamespace

import pytest

from battle_history import BattleRecord
from battle_lifecycle import BattleRuntime
from desktop_bridge import DesktopState
from panel_model import Ranking, RateBasis
from roster import CharacterEntry, RosterSnapshot
from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch


CID = 'char_367_swllow'
STAGE = 'Activities/ACT3AUTOCHESS/level_act3autochess_m11'


def hit(sequence, side, amount, *, when=1):
    return dict(sequence=sequence, frame=round(when * 30), game_time=when,
                source=CID, source_character_uid=side * 100 + 1,
                kind='damage_settlement', amount=amount, nominal_amount=amount,
                target='enemy_1007_slime', target_uid=str(sequence),
                target_instance=f'target-{sequence}', damage_type=1,
                shield_absorbed=0, settlement_complete=True, killed=False)


class NativeBattle:
    """One bounded event source and one acquisition owner, including ACKs."""

    def __init__(self, stage=STAGE):
        self.runtime = BattleRuntime()
        self.stage, self.frame, self.when, self.damage = stage, 0, 0, 0
        self.active, self.state, self.scope = True, 2, 0
        self.sequence = 0
        self.queue, self.commits, self.reads = [], [], []
        self.batch_changes = {}
        self.quality_changes = {}
        self.session, self.build = '10:1234:opaque-battle', 'win-x64:integration-fixture'
        self.collector = SimpleNamespace(
            process=SimpleNamespace(pid=10, process_instance=(10, 1234)),
            read_lifecycle=self.lifecycle, read_guarded_pair=self.pair)
        self.observer = SimpleNamespace(mark=self.mark, read_through=self.batch,
            commit_events=self.commit, read_terminal_mark=lambda: {'terminal_boundary': self.mark()})

    def quality(self):
        return dict(source_attribution_version=1, partial_damage_version=7,
                    damage_settlement_quality_version=1, damage_settlement_scope=self.scope,
                    enemy_damage_ready=True, enemy_damage_unknown=False,
                    damage_unknown=False, engagement_unknown=False, skill_unknown=False,
                    shield_unknown=False, mitigation_ready=True, mitigation_unknown=False) | self.quality_changes

    def binding(self):
        return dict(native_session=self.session, source_build=self.build)

    def lifecycle(self):
        self.reads.append('lifecycle')
        metadata = dict(platform='native_pc', source_build=self.build,
                        stage_id=self.stage, evidence='integration-fixture',
                        outcome='victory' if self.state == 3 else None)
        identity = ('opaque-battle',) if self.active else None
        life = dict(battle=identity, state=self.state, ready=True, transiting=False,
                    source='', target='', metadata=metadata)
        sample = dict(session=self.session, frame=self.frame, game_time=self.when,
                      battle_identity=identity, state=self.state,
                      battle_metadata=metadata, observed_at=time.time(),
                      captured_monotonic=time.monotonic(), rows=[dict(
                          id=CID, damage=self.damage, healing=0,
                          physical=self.damage, arts=0, true=0, elemental=0)])
        entries = tuple(CharacterEntry(CID, side * 100 + 1, False, False, None, None, side)
                        for side in range(1, 5))
        roster = RosterSnapshot(self.frame, entries, frozenset({301}), True, 3)
        self.captured = life, sample, roster
        return life

    def pair(self, lifecycle):
        self.reads.append('pair')
        assert lifecycle is self.captured[0]
        return deepcopy(self.captured[1]), self.captured[2]

    def mark(self):
        self.reads.append('mark')
        return dict(self.binding(), sequence=self.sequence, epoch=self.sequence,
                    active=0, frame=self.frame, game_time=self.when, **self.quality())

    def batch(self, sequence):
        self.reads.append('batch')
        return dict(self.binding(), events=deepcopy([e for e in self.queue if e['sequence'] <= sequence]),
                    error=None, stopped=False, **self.quality()) | self.batch_changes

    def commit(self, sequence):
        self.commits.append(sequence)
        self.queue = [e for e in self.queue if e['sequence'] > sequence]

    def advance(self, when, events=(), *, damage=None, state=None):
        self.when, self.frame = when, round(when * 30)
        if damage is not None:
            self.damage = damage
        if state is not None:
            self.state = state
        self.queue.extend(deepcopy(events))
        self.sequence = max([self.sequence, *(e['sequence'] for e in events)])

    def poll(self):
        return self.runtime.poll(self.collector, self.observer)


def populated_battle():
    battle = NativeBattle()
    battle.poll()
    records = [hit(side, side, side * 100) for side in range(1, 5)]
    battle.advance(2, records, damage=300)
    battle.poll()
    return battle


def amounts(payload):
    return [player['observed_damage'] for player in payload['players']]


def test_one_accepted_batch_drives_four_players_and_replay_without_extra_acquisition_or_ack():
    battle = populated_battle()
    sample = battle.runtime.display_sample()
    assert battle.reads.count('batch') == 2 and battle.commits == [0, 4]
    assert sample['event_status'] == 'recording'
    assert sample['statistics']['rows'][CID]['damage'] == 300
    assert amounts(sample['multiplayer']) == [100, 200, 300, 400]
    assert sample['multiplayer'] == battle.runtime.sessions.live()['multiplayer']
    assert [p['is_local'] for p in sample['multiplayer']['players']] == [False, False, True, False]
    assert all(p['damage'] is p['dps'] is None for p in sample['multiplayer']['players'])
    assert sample['event_watermark'] == {'sequence': 4, 'frame': 60, 'game_time': 2}

    # A producer may retain an acknowledged prefix. Its original records are
    # compared and retired once by each consumer, never charged a second time.
    battle.queue = [hit(side, side, side * 100) for side in range(1, 5)]
    original = deepcopy(battle.queue)
    battle.advance(3)
    battle.poll()
    assert battle.commits == [0, 4, 4] and battle.reads.count('batch') == 3
    assert amounts(battle.runtime.display_sample()['multiplayer']) == [100, 200, 300, 400]
    assert original == [hit(side, side, side * 100) for side in range(1, 5)]


def test_non_autochess_stage_does_not_construct_or_publish_player_projection(monkeypatch):
    def forbidden():
        raise AssertionError('ordinary stage must not construct player statistics')
    monkeypatch.setattr('multiplayer_battle.MultiplayerBattleStatistics', forbidden)
    battle = NativeBattle('main_01-07')
    battle.poll()
    battle.advance(2, [hit(1, 3, 30)], damage=30)
    battle.poll()
    assert battle.runtime.sessions.multiplayer is None
    assert 'multiplayer' not in battle.runtime.display_sample()
    assert battle.runtime.sessions.multiplayer_error is None
    assert battle.commits == [0, 1]


def test_unaccepted_sequence_gap_never_reaches_player_projection(monkeypatch):
    battle = NativeBattle()
    battle.poll()
    projection = battle.runtime.sessions.multiplayer
    calls = []
    monkeypatch.setattr(projection, 'consume', lambda *args, **kwargs: calls.append(args))
    battle.advance(2, [hit(2, 1, 100)], damage=30)
    battle.poll()
    assert calls == [] and battle.commits == [0]
    sample = battle.runtime.display_sample()
    assert sample['event_status'] == 'gap'
    assert sample['statistics']['rows'][CID]['damage'] == 30
    assert sample['event_watermark']['sequence'] == 0
    assert amounts(sample['multiplayer']) == [None, None, None, None]


def test_generation_reset_archives_old_player_scope_and_starts_empty_without_reusing_old_events():
    battle = populated_battle()
    first = battle.runtime.display_sample()
    first_scope = first['multiplayer']['round_id']
    battle.scope += 1
    battle.advance(0, damage=0)
    battle.poll()
    archive, = battle.runtime.sessions.pop_archives()
    assert archive['battle_id'] == first['battle_id']
    assert archive['last_sample']['multiplayer']['round_id'] == first_scope
    assert archive['last_sample']['multiplayer']['status'] == 'ended'
    assert amounts(archive['last_sample']['multiplayer']) == [100, 200, 300, 400]
    assert archive['event_status'] == 'finished'

    second = battle.runtime.display_sample()
    assert second['battle_id'] != first['battle_id']
    assert second['multiplayer']['round_id'] != first_scope
    assert amounts(second['multiplayer']) == [None, None, None, None]
    frozen = deepcopy(archive)
    battle.advance(1, [hit(5, 2, 25)], damage=0)
    battle.poll()
    assert amounts(battle.runtime.display_sample()['multiplayer']) == [None, 25, None, None]
    assert archive == frozen and battle.commits == [0, 4, 4, 5]


def test_terminal_commit_freezes_player_scope_and_retires_recent_rates():
    battle = populated_battle()
    battle.advance(3, [hit(5, 3, 50, when=3)], damage=350, state=3)
    battle.poll()
    sample = battle.runtime.display_sample()
    assert sample['event_status'] == 'finished' and sample['statistics']['ended']
    assert sample['multiplayer']['status'] == 'ended'
    assert amounts(sample['multiplayer']) == [100, 200, 350, 400]
    assert all(p['observed_recent_dps'] is None for p in sample['multiplayer']['players'])
    assert all(p['statistics']['ended'] for p in sample['multiplayer']['players'])
    assert sample['event_watermark']['sequence'] == 5 and battle.commits == [0, 4, 5]


def test_terminal_transaction_rejection_cannot_charge_player_events_before_main_commit(monkeypatch):
    battle = populated_battle()
    original_update = BattleStatisticsAdapter.update

    def reject_terminal(self, *args, **kwargs):
        if kwargs.get('terminal'):
            raise FrameMismatch('terminal fixture rejection')
        return original_update(self, *args, **kwargs)

    monkeypatch.setattr(BattleStatisticsAdapter, 'update', reject_terminal)
    battle.advance(3, [hit(5, 1, 50, when=3)], state=3)
    battle.poll()
    sample = battle.runtime.display_sample()
    assert sample['event_status'] == 'gap'
    assert amounts(sample['multiplayer']) == [100, 200, 300, 400]
    assert sample['event_watermark']['sequence'] == 4
    assert battle.commits == [0, 4]
    assert battle.runtime.sessions.multiplayer._sequence == 4


@pytest.mark.parametrize('failure', ['consume', 'result', 'consume_and_gap'])
def test_optional_player_projection_fault_does_not_close_local_capture_or_lose_ack(monkeypatch, failure):
    battle = NativeBattle()
    battle.poll()
    projection = battle.runtime.sessions.multiplayer

    def unavailable(*args, **kwargs):
        raise RuntimeError('optional player projection fixture fault')

    monkeypatch.setattr(projection, 'consume' if failure == 'consume_and_gap' else failure, unavailable)
    if failure == 'consume_and_gap':
        monkeypatch.setattr(projection, 'gap', unavailable)
    battle.advance(2, [hit(1, 3, 30)], damage=30)
    battle.poll()
    sample = battle.runtime.display_sample()
    assert sample['event_status'] == 'recording'
    assert sample['statistics']['rows'][CID]['settled_damage']['effective_damage'] == 30
    assert sample['event_watermark']['sequence'] == 1
    assert battle.commits == [0, 1] and not battle.runtime.sessions.capture.closed
    assert 'fixture fault' in battle.runtime.sessions.multiplayer_error


def test_optional_player_constructor_fault_does_not_block_local_first_ack(monkeypatch):
    def unavailable():
        raise RuntimeError('optional player constructor fixture fault')
    monkeypatch.setattr('multiplayer_battle.MultiplayerBattleStatistics', unavailable)
    battle = NativeBattle()
    battle.poll()
    assert battle.runtime.sessions.event_status == 'recording'
    assert battle.commits == [0] and not battle.runtime.sessions.capture.closed
    assert 'fixture fault' in battle.runtime.sessions.multiplayer_error


def test_optional_player_finish_fault_cannot_block_main_archive(monkeypatch):
    battle = populated_battle()

    def unavailable(*args, **kwargs):
        raise RuntimeError('optional player finish fixture fault')
    monkeypatch.setattr(battle.runtime.sessions.multiplayer, 'finish', unavailable)
    battle.scope += 1
    battle.advance(0, damage=0)
    battle.poll()
    archive, = battle.runtime.sessions.pop_archives()
    assert archive['statistics']['rows'][CID]['damage'] == 300
    assert archive['event_status'] == 'finished'
    assert battle.commits == [0, 4, 4]
    assert battle.runtime.sessions.event_status == 'recording'


@pytest.mark.parametrize('flag,value', [('skill_unknown', True), ('shield_unknown', True),
                                      ('mitigation_unknown', True), ('mitigation_ready', False)])
def test_declared_event_family_quality_degrades_each_player_like_local_core(flag, value):
    battle = NativeBattle()
    battle.poll()
    battle.quality_changes[flag] = value
    records = [dict(hit(side, side, side * 100),
                    mitigation_before=side * 200, mitigation_after=side * 100)
               for side in range(1, 5)]
    records += [dict(hit(side + 4, side, side * 100), kind='damage',
                     mitigation_before=side * 200, mitigation_after=side * 100,
                     real_delta=-side * 100, hp_before=10000, hp_after=10000 - side * 100,
                     target_type=0, cancel_reason=0) for side in range(1, 5)]
    control = NativeBattle()
    control.poll()
    control.advance(2, records, damage=300)
    control.poll()
    control_sample = control.runtime.display_sample()
    assert control_sample['statistics']['rows'][CID]['observed_mitigation_rate'] == 50
    assert all(p['statistics']['rows'][f'{CID}#{side * 100 + 1}']['observed_mitigation_rate'] == 50
               for side, p in enumerate(control_sample['multiplayer']['players'], 1))
    battle.advance(2, records, damage=300)
    battle.poll()
    sample = battle.runtime.display_sample()
    local = sample['statistics']['rows'][CID]
    for side, player in enumerate(sample['multiplayer']['players'], 1):
        row = player['statistics']['rows'][f'{CID}#{side * 100 + 1}']
        # A family declaration must reach the shared core; it does not erase
        # independent measured HP/nominal settlement components.
        assert row['settled_damage']['effective_damage'] == side * 100
        assert row['settled_damage']['complete']
        if flag == 'skill_unknown':
            for scoped in (row, local):
                assert not scoped['skill_coverage_complete']
                assert not scoped['settled_damage']['skill_split_complete']
                assert scoped['settled_damage']['skill_damage_unverified_share'] == 1
        elif flag == 'shield_unknown':
            assert row['shield_unknown'] is local['shield_unknown'] is True
            assert row['shield_absorbed'] is local['shield_absorbed'] is None
        else:
            assert row['mitigation_rate'] is local['mitigation_rate'] is None
            assert row['observed_mitigation_rate'] is local['observed_mitigation_rate'] is None
            assert row['mitigation_unknown_reason'] == local['mitigation_unknown_reason']
            assert row['mitigation_unknown_reason'] == '伤害公式回调覆盖不足'
    assert battle.commits == [0, 8] and sample['event_status'] == 'recording'


@pytest.mark.parametrize('field,unknown_set', [('unknown_sources', 'overkill_incomplete'),
                                             ('engagement_unknown_sources', 'engagement_incomplete')])
def test_declared_source_quality_uses_each_players_uid_owner_without_crossing_native_boundary(field, unknown_set):
    battle = NativeBattle()
    battle.poll()
    battle.quality_changes[field] = [CID]
    battle.advance(2, [hit(side, side, side * 100) for side in range(1, 5)], damage=300)
    battle.poll()
    local = battle.runtime.sessions.adapter.statistics
    assert CID in getattr(local, unknown_set)
    for side, player in battle.runtime.sessions.multiplayer._players.items():
        assert getattr(player.statistics, unknown_set) == {f'{CID}#{side * 100 + 1}'}
    assert amounts(battle.runtime.display_sample()['multiplayer']) == [100, 200, 300, 400]
    assert battle.commits == [0, 4]


def test_same_player_snapshot_roundtrips_history_and_uses_shared_desktop_view(tmp_path, monkeypatch):
    battle = populated_battle()
    live_sample = battle.runtime.display_sample()
    state = DesktopState(tmp_path)
    state.layout = replace(state.layout, multiplayer_enabled=True)
    calls = []
    render = state.view

    def shared_view(config, statistics, *args, **kwargs):
        if config.panel_id.startswith('multiplayer-'):
            calls.append((config, deepcopy(statistics)))
        return render(config, statistics, *args, **kwargs)

    monkeypatch.setattr(state, 'view', shared_view)
    state.accept('sample', live_sample)
    original = deepcopy(live_sample)
    live = state.snapshot()['multiplayer']
    assert len(calls) == 4
    assert all(c.effective_only and c.ranking is Ranking.TOTAL and c.rate_basis is RateBasis.ENGAGEMENT
               for c, _ in calls)
    for side, player in enumerate(live['players'], 1):
        row, = player['view']['entries']
        assert row['value'] == side * 100 and row['actor_uid'] == side * 100 + 1
        assert row['portrait_id'] == CID and row['player_side'] == side
        assert row['owner_id'] == f'{CID}#{side * 100 + 1}'
        assert player['display']['damage'].startswith('已观察 ')
    assert live_sample == original

    battle.scope += 1
    battle.advance(0, damage=0)
    battle.poll()
    archive, = battle.runtime.sessions.pop_archives()
    record = BattleRecord.from_archive(archive)
    state.history.save(record)
    state.accept('history', None)
    history = state.snapshot()['history_selected']
    assert history['battle_id'] == live_sample['battle_id']
    assert amounts(history['multiplayer']) == amounts(live)
    assert history['multiplayer']['round_id'] == live['round_id']
    assert history['multiplayer']['status'] == 'ended'
    assert [[(e['owner_id'], e['value']) for e in p['view']['entries']]
            for p in history['multiplayer']['players']] == [
                [(e['owner_id'], e['value']) for e in p['view']['entries']] for p in live['players']]
    restarted = DesktopState(tmp_path)
    restarted.layout = replace(restarted.layout, multiplayer_enabled=True)
    reopened = restarted.snapshot()['history_selected']['multiplayer']
    assert reopened == history['multiplayer']
    assert not state.history.list_recent().diagnostics

