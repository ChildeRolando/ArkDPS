from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from autochess_context import normalize_native_autochess
from event_probe import (BarrierBusy, NATIVE_PC_CONTRACT, bind_native_autochess_pair,
                         native_autochess_event_baseline)
from roster import RosterSnapshot
from roster import CharacterEntry
from event_capture import CaptureRetry, LiveEventCapture
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable
from test_autochess_context import payload as identity_payload
from test_autochess_context import member as identity_member
from test_multiplayer_integration import NativeBattle, CID, hit


ROOTS = dict(zip(('controller', 'native', 'logger', 'stats', 'level'),
                 ('0x10000', '0x20000', '0x30000', '0x40000', '0x50000')))
SCOPE = ':'.join(ROOTS.values()) + ':autochess:1'


def sample(frame=1, when=0):
    return dict(session='battle', native_session='native-battle', frame=frame, game_time=when,
                state=2, rows=[dict(id='char_a', damage=90000, healing=0)],
                battle_metadata=dict(platform='native_pc', source_build='verified-build',
                                     stage_id='Activities/ACT3AUTOCHESS/level_test'))


def mark(frame=1, when=0, **changes):
    context = identity_payload(frame=frame, game_time=when, scope_key=SCOPE, **changes)
    return dict(native_session='native-battle', source_build='verified-build', native_identity=ROOTS,
                native_autochess_context=context)


def bound_sample(frame=1, when=0):
    value, evidence = sample(frame, when), mark(frame, when)
    bind_native_autochess_pair(value, evidence, deepcopy(evidence))
    return value, evidence


def test_empty_local_roster_can_admit_clock_but_never_cid_cumulative_amounts():
    value, _ = bound_sample()
    roster = RosterSnapshot(frame=1, entries=(), present_known=True, local_player_side=0)
    adapter = BattleStatisticsAdapter()
    update = adapter.update(value, roster)
    assert update.result['rows'] == {}
    assert update.result['totals_complete'] is False
    assert update.result['native_counter_scope'] == 'unavailable'
    assert adapter.owner_map.owners == {}  # No synthetic owner or per-player native zero.
    adapter.start_events(session='battle', frame=1, sequence=0)
    adapter.accept_event(session='battle', sequence=1, frame=1, game_time=0,
                         source='char_a', kind='damage', amount=20)
    assert adapter.event_stream.sequence == 1 and adapter.result()['rows'] == {}
    later, _ = bound_sample(2, 1)
    adapter.update(later, replace(roster, frame=2))
    assert adapter.result()['totals_complete'] is False
    adapter.finish(1)
    assert adapter.result()['totals_complete'] is False


def test_event_baseline_requires_bound_native_combat_frame():
    value, _ = bound_sample()
    assert native_autochess_event_baseline(value)
    for change in (dict(native_counter_scope=None), dict(battle_metadata=dict(platform='mumu')),
                   dict(autochess_context=dict(value['autochess_context'], verified_same_frame=False)),
                   dict(autochess_context=dict(value['autochess_context'], phase=3)),
                   dict(frame=2)):
        invalid = dict(value, **change)
        assert not native_autochess_event_baseline(invalid)
        with pytest.raises(MappingUnavailable):
            BattleStatisticsAdapter().update(invalid, RosterSnapshot(frame=invalid['frame']))


def test_two_native_contexts_must_match_without_merging_pages_or_players():
    value = sample()
    first, changed = mark(), mark()
    changed['native_autochess_context']['players'][0]['uid'] = '920000001'
    with pytest.raises(BarrierBusy):
        bind_native_autochess_pair(value, first, changed)
    assert 'native_counter_scope' not in value
    paged = mark()
    paged['native_autochess_context']['sampling'] = dict(coverage_complete=True)
    bind_native_autochess_pair(value, paged, deepcopy(paged))
    assert value['autochess_context']['status'] == 'unknown'
    assert not native_autochess_event_baseline(value)


def test_later_phase_batch_retries_before_scope_or_money_can_be_accepted():
    value, evidence = bound_sample()
    NATIVE_PC_CONTRACT.validate_binding(value, evidence)
    changed = mark(phase=5)
    with pytest.raises(BarrierBusy):
        NATIVE_PC_CONTRACT.validate_binding(value, changed)
    # Old recordings and ordinary PC/MuMu retain their existing proof profile.
    legacy = sample()
    NATIVE_PC_CONTRACT.validate_binding(legacy, changed)
    ordinary = dict(legacy, battle_metadata=dict(platform='native_pc', stage_id='CE-5'))
    bind_native_autochess_pair(ordinary, evidence, changed)
    assert 'autochess_context' not in ordinary


def test_scope_key_must_include_the_five_host_bound_roots():
    value, first = sample(), mark()
    first['native_autochess_context']['scope_key'] = SCOPE.replace('0x10000', '0x60000')
    bind_native_autochess_pair(value, first, deepcopy(first))
    assert value['autochess_context']['status'] == 'unknown'


def test_cold_unknown_event_identity_cannot_fall_back_to_an_existing_cid_roster():
    value, evidence = bound_sample()
    value['autochess_context']['status'] = 'unknown'
    real_legacy_owner = CharacterEntry('char_a', 101, False, False, None, None, 1)
    roster = RosterSnapshot(1, (real_legacy_owner,), present_known=True, local_player_side=1)
    adapter = BattleStatisticsAdapter()
    with pytest.raises(MappingUnavailable, match='拒绝回退'):
        adapter.update(value, roster)
    assert adapter.statistics is None  # The existing 90000 CID amount was never admitted.


def test_direct_capture_scope_race_retries_without_closing_or_acknowledging():
    current, evidence = sample(), mark()
    quality = dict(partial_damage_version=7, source_attribution_version=1,
                   damage_unknown=False, engagement_unknown=False, skill_unknown=False,
                   shield_unknown=False, mitigation_ready=True, mitigation_unknown=False,
                   enemy_damage_ready=True, enemy_damage_unknown=False)
    first_mark = dict(evidence, sequence=0, epoch=0, active=0, frame=1, game_time=0, **quality)
    later_batch = dict(mark(phase=5), events=[], error=None, stopped=False, **quality)
    commits = []
    collector = SimpleNamespace(read=lambda: deepcopy(current),
                                read_roster=lambda: RosterSnapshot(1, present_known=True, local_player_side=0))
    observer = SimpleNamespace(contract=NATIVE_PC_CONTRACT, mark=lambda: deepcopy(first_mark),
        read_through=lambda _: deepcopy(later_batch), commit_events=commits.append)
    capture = LiveEventCapture(collector, observer)
    with pytest.raises(CaptureRetry, match='phase or round'):
        capture.sample()
    assert not capture.closed and commits == [] and capture.adapter.statistics is None
    later_batch = dict(evidence, events=[], error=None, stopped=False, **quality)
    result = capture.sample()
    assert result['rows'] == {} and not result['totals_complete']
    assert not capture.closed and commits == [0]


def test_actual_pc_adapter_full_context_node_fixture():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/native_pc_autochess_context_fixture.js')],
                            text=True, capture_output=True, timeout=30, check=True)
    assert json.loads(result.stdout)


class ScopedNativeBattle(NativeBattle):
    """Exercise the ordinary acquisition owner, including the empty-own-roster case."""
    def __init__(self):
        super().__init__()
        self.native_phase, self.native_round, self.autochess_epoch = 4, 4, 1

    def quality(self):
        # This owned source retains every start; its synthetic initial window
        # has no clipped skill activation. Production obtains this from queue.
        return dict(super().quality(), baseline_discarded_skill_events=[],
                    baseline_discarded_skill_events_complete=True)

    @property
    def native_scope(self):
        return ':'.join(ROOTS.values()) + f':autochess:{self.autochess_epoch}'

    def binding(self):
        context = identity_payload(frame=self.frame, game_time=self.when,
            scope_key=self.native_scope, phase=self.native_phase, round=self.native_round,
            memberships=[identity_member(cid=CID, data_uid=i * 100 + 1,
                                         player_index=i, player_side=1 if self.native_phase == 5 else i)
                         for i in range(1, 5)])
        return dict(super().binding(), native_identity=ROOTS, native_autochess_context=context)

    def lifecycle(self):
        value = super().lifecycle()
        life, data, _ = self.captured
        data.update(global_total=self.damage, global_heal=0)
        # This is the live failure: the normal player roster is empty while
        # real actors/events and CID-only cumulative rows are already present.
        self.captured = life, data, RosterSnapshot(self.frame, present_known=True, local_player_side=0)
        return value

    def scoped_hit(self, sequence, index, amount, *, scope=None, phase=None):
        return dict(hit(sequence, index, amount, when=self.when), native_autochess_scope=dict(
            protocol_version=1, status='available', verified_same_frame=True,
            frame=self.frame, game_time=self.when, scope_key=scope or self.native_scope,
            phase=self.native_phase if phase is None else phase, round=self.native_round))


def test_native_empty_roster_flows_through_shared_admission_four_cores_and_single_ack():
    battle = ScopedNativeBattle()
    assert battle.poll().kind == 'sample'
    battle.advance(2, damage=90000)
    records = [battle.scoped_hit(i, i, i * 100) for i in range(1, 5)]
    battle.advance(2, records)
    observation = battle.poll()
    assert observation.kind == 'sample'
    published = battle.runtime.display_sample()
    assert published['event_status'] == 'recording'
    assert published['statistics']['rows'] == {} and published['statistics']['totals_complete'] is False
    assert published['multiplayer']['schema_version'] == 2
    assert [p['observed_damage'] for p in published['multiplayer']['players']] == [100, 200, 300, 400]
    assert battle.commits == [0, 4] and battle.reads.count('batch') == 2


def test_native_scope_switch_without_clock_reset_establishes_a_fresh_prefix():
    battle = ScopedNativeBattle()
    battle.poll()
    battle.advance(2)
    battle.advance(2, [battle.scoped_hit(1, 3, 300)])
    battle.poll()
    old_scope = battle.native_scope
    previous_generation = battle.runtime.feed.generation
    battle.native_phase, battle.autochess_epoch = 5, 2
    battle.advance(3)
    battle.poll()
    assert battle.runtime.feed.generation == previous_generation + 1
    current = battle.runtime.display_sample()['multiplayer']
    assert current['scope_kind'] == 'assist'
    assert all(p['observed_damage'] is None for p in current['players'])
    battle.advance(4)
    records = [battle.scoped_hit(2, 3, 500, scope=old_scope, phase=4),
               battle.scoped_hit(3, 3, 60)]
    battle.advance(4, records)
    battle.poll()
    current = battle.runtime.display_sample()['multiplayer']
    assert [p['observed_damage'] for p in current['players']] == [None, None, 60, None]
    assert current['players'][2]['player_uid'] == '910000003'
    assert current['players'][2]['player_side'] == 1
    assert battle.commits == [0, 1, 1, 3]
