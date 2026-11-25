"""Typed synthetic lifecycle callback execution; no claim of live acceptance."""
import json
from pathlib import Path
import subprocess

import pytest

from deployment_lifecycle import DeploymentLifecycle, lifecycle_proof
from event_probe import event_processing_payload
from event_stream import EventStream
from stats_core import Statistics

ROOT = Path(__file__).resolve().parents[1]


def proof(state, when, instance=10, actor=1010, reason=2):
    result = dict(version=1, state=state, instance_uid=instance, data_uid=actor,
                  frame=when * 30, game_time=when, fixed=str(when * 4294967296))
    if state == 'finish':
        result.update(reason_type='Torappu.Battle.Entity.FinishReason', reason=reason)
    return result


def setup():
    stats = Statistics('life', 0, event_coverage=True)
    stats.register_source('char_237_gravel', 'owner')
    stats.snapshot(0, {'char_237_gravel': dict(damage=0, healing=0)})
    stats.settlement_enabled = True
    stats.damage_settlement_version = 7
    return stats, EventStream(stats, session='life', baseline_frame=0, baseline_sequence=0)


def deliver(stats, stream, kind, when, *, method=None, reason=None, instance=10, certificate=None,
            key='skill-fixture', actor=1010, wait=None):
    stats.advance(when)
    payload = dict(session='life', sequence=stream.sequence + 1, frame=when * 30, game_time=when,
                   source='char_237_gravel', source_character_uid=actor, source_instance_uid=instance,
                   kind=kind, amount=0 if kind == 'character_born' else 1,
                   skill_method=method, skill_key=key, skill_reason=reason, actor_lifecycle=certificate)
    if wait is not None:
        payload['skill_wait_for_end'] = wait
    stream.accept(settlement_identity=('local', 'owner', actor), **payload)


@pytest.mark.parametrize('first', ['ability', 'entity'])
@pytest.mark.parametrize('reason', [2, 4, 8])
def test_typed_permanent_endpoint_closes_both_skill_ledgers_in_either_callback_order(first, reason):
    stats, stream = setup()
    deliver(stats, stream, 'character_born', 1, certificate=proof('born', 1))
    deliver(stats, stream, 'skill_start', 1, method='Character.SwitchToSkillState')
    callbacks = [('Character.OnAfterSkill', 2, None),
                 ('BattleController.OnCharacterFinished', reason, proof('finish', 2, reason=reason))]
    if first == 'entity':
        callbacks.reverse()
    for method, value, certificate in callbacks:
        deliver(stats, stream, 'skill_end', 2, method=method, reason=value, certificate=certificate, key=None)
    assert not stats.skill_active and not stats.settlement_skill_active
    assert 'owner' not in stats.skill_incomplete
    assert not stats.settlement_skill_incomplete
    assert stats.skill_windows['owner'][0]['end'] == 2
    assert stats.deployment_lifecycle.result('owner', 2, continuous=True, archived=True)['lifecycle_presence_seconds'] == 1


@pytest.mark.parametrize('reason', [0, 7, 10, 11, 12])
def test_nonterminal_entity_reason_never_certifies_body_endpoint(reason):
    stats, stream = setup()
    deliver(stats, stream, 'character_born', 1, certificate=proof('born', 1))
    deliver(stats, stream, 'skill_end', 2, method='BattleController.OnCharacterFinished', reason=reason,
            certificate=proof('finish', 2, reason=reason), key=None)
    result = stats.deployment_lifecycle.result('owner', 2, continuous=True, archived=True)
    assert result['lifecycle_presence_complete'] is False
    assert result['lifecycle_terminal_complete'] is False


def test_quiet_successor_or_gap_cannot_supply_missing_terminal_and_active_watermark_is_independent():
    life = DeploymentLifecycle()
    life.observe('owner', 1010, 10, 'character_born', 1, proof('born', 1), frame=30)
    assert life.result('owner', 4, continuous=True, archived=False)['lifecycle_presence_seconds'] == 3
    assert life.result('owner', 4, continuous=True, archived=False)['lifecycle_presence_window'] == [0, 4]
    assert life.result('owner', 100, continuous=True, archived=True)['lifecycle_presence_seconds'] is None
    assert life.result('owner', 4, continuous=False, archived=False)['lifecycle_presence_seconds'] is None


def test_unmatched_typed_cast_keeps_actor_unknown_without_owner_wide_attribution_gap_and_recovers_future():
    stats, stream = setup()
    deliver(stats, stream, 'character_born', 1, certificate=proof('born', 1))
    deliver(stats, stream, 'skill_end', 1, method='BasicSkill.OnCastFinish', reason=0, wait=True)
    assert stats._settlement_skill('owner', 1010) is None
    assert ('owner', 1010) not in stats.settlement_skill_initial_pending
    assert not stats.settlement_skill_unassigned
    assert not stats._skill_unresolved_owners
    assert not stats.skill_active
    deliver(stats, stream, 'skill_start', 2, method='Character.SwitchToSkillState')
    assert stats._settlement_skill('owner', 1010) is True


def test_same_data_uid_old_body_late_callbacks_cannot_end_or_poison_redeployment():
    stats, stream = setup()
    deliver(stats, stream, 'character_born', 1, certificate=proof('born', 1))
    deliver(stats, stream, 'skill_start', 1, method='Character.SwitchToSkillState')
    deliver(stats, stream, 'skill_end', 2, method='BattleController.OnCharacterFinished', reason=2,
            certificate=proof('finish', 2))
    deliver(stats, stream, 'character_born', 3, instance=20, certificate=proof('born', 3, instance=20))
    deliver(stats, stream, 'skill_start', 3, method='Character.SwitchToSkillState', instance=20)
    deliver(stats, stream, 'skill_end', 4, method='BasicSkill.OnCastFinish', reason=0, wait=True)
    deliver(stats, stream, 'skill_end', 4, method='BattleController.OnCharacterFinished', reason=2,
            certificate=proof('finish', 4))
    assert stats.skill_active['owner']['deployment'] == 20
    assert stats._settlement_skill('owner', 1010) is True
    assert not stats.settlement_skill_unassigned
    assert 'owner' not in stats.deployment_lifecycle.unknown


def test_missing_or_cross_actor_or_non_raw_clock_proof_stays_unknown():
    for change in [dict(data_uid=999), dict(instance_uid=99), dict(fixed='0'),
                   dict(reason_type='Torappu.Battle.Ability.FinishReason')]:
        certificate = dict(proof('finish', 2), **change)
        assert lifecycle_proof(certificate, kind='skill_end', actor=1010, instance=10,
                               frame=60, game_time=2) is None


def test_actual_shared_producer_metadata_and_callbacks_both_platforms():
    report = json.loads(subprocess.check_output(['node', str(ROOT / 'tests/event_probe_character_lifecycle_fixture.js')],
                                               cwd=ROOT, text=True, encoding='utf-8'))
    for platform, scenario in ((p, s) for p in ('native_pc', 'android') for s in ('valid', 'nested_start')):
        stats, stream = setup()
        for raw in report[platform][scenario]['batch']['events']:
            stats.advance(raw['game_time'])
            stream.accept(session='life', settlement_identity=('local', 'owner', 1010),
                          **event_processing_payload(raw))
        assert not stats.skill_active and not stats.settlement_skill_active
        assert 'owner' not in stats.skill_incomplete
        assert not stats.settlement_skill_incomplete
        assert stats.deployment_lifecycle.result('owner', 2, continuous=True, archived=True)['lifecycle_presence_seconds'] == 1

def test_multiplayer_uid_scope_isolation_and_late_roster_binding_preserve_original_born_prefix():
    from multiplayer_battle import MultiplayerBattleStatistics
    from test_multiplayer_native_context import CID, consume, context, member, player, stamp
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=context(0, members=[member(3)]))
    born = dict(sequence=1, frame=30, game_time=1, source=CID, source_character_uid=401,
                source_instance_uid=10, kind='character_born', amount=0,
                actor_lifecycle=proof('born', 1, actor=401), native_autochess_scope=stamp(when=1))
    consume(meter, 1, [born], native=context(1, members=[member(3)]))
    result = consume(meter, 2, native=context(2, members=[member(3), member(4)]))
    foreign_row = next(iter(player(result, 4)['statistics']['rows'].values()))
    assert foreign_row['lifecycle_presence_seconds'] == 1
    local_row = next(iter(player(result, 3)['statistics']['rows'].values()))
    assert local_row['lifecycle_presence_complete'] is False
    assert not meter._pending_lifecycles
    archived = meter.finish(2, complete=True)
    foreign_row = next(iter(player(archived, 4)['statistics']['rows'].values()))
    assert foreign_row['lifecycle_presence_seconds'] is None


def test_source_instance_unmatched_cast_does_not_poison_other_current_body_without_born_history():
    stats, stream = setup()
    deliver(stats, stream, 'skill_start', 1, method='Character.SwitchToSkillState', instance=20)
    deliver(stats, stream, 'skill_end', 2, method='BasicSkill.OnCastFinish', reason=0, wait=True, instance=10)
    assert stats._settlement_skill('owner', 1010) is True
    assert ('owner', 1010, 10) in stats.settlement_skill_deployment_unknown
    assert not stats.settlement_skill_unassigned


def lifecycle_zero_sample():
    from event_probe import verified_autochess_round_start
    from test_event_probe_round_start_contract import inputs
    sample, drained, mark = inputs(events=[])
    point = drained['autochess_round_start']
    point.update(version=2, live_character_collection_empty=False, live_character_data_uids=[301],
                 lifecycle_baseline=dict(version=1, complete=True, frame=0, game_time=0, fixed='0',
                     units=[dict(cid='char_zero', data_uid=301, instance_uid=77, player_index=3,
                                 player_uid='003', player_side=3, inited=True, borned=True, disappeared=False)]))
    return verified_autochess_round_start(sample, drained, mark)


def test_independent_zero_body_proof_seeds_current_round_without_guessing_skill_initial_state():
    from event_probe import autochess_round_start_lifecycle_instances
    zero, opening, empty, replay = lifecycle_zero_sample()
    assert autochess_round_start_lifecycle_instances(zero)[0]['instance_uid'] == 77
    from multiplayer_battle import MultiplayerBattleStatistics
    from roster import RosterSnapshot
    meter = MultiplayerBattleStatistics()
    meter.consume(zero, RosterSnapshot(0), opening['sequence'], empty, mark=opening)
    owner_player = meter._players['003']
    owner = next(iter(owner_player.actors))
    life = owner_player.statistics.deployment_lifecycle
    assert life.result(owner, 4, continuous=True, archived=False)['lifecycle_presence_seconds'] == 4
    assert owner_player.statistics._settlement_skill(owner, 301) is None
    life.observe(owner, 301, 77, 'skill_end', 5, proof('finish', 5, actor=301, instance=77), frame=150)
    assert life.result(owner, 8, continuous=True, archived=True)['lifecycle_presence_seconds'] == 5


@pytest.mark.parametrize('fault', ['missing_flags', 'duplicate', 'instance_zero', 'wrong_player', 'token'])
def test_invalid_zero_body_proof_is_independent_and_cannot_seed_presence(fault):
    from copy import deepcopy
    from event_probe import autochess_round_start_lifecycle_instances, autochess_round_start_origin
    zero, _, _, _ = lifecycle_zero_sample()
    raw = zero['autochess_round_start']['lifecycle_baseline']
    if fault == 'missing_flags':
        raw['units'][0].pop('borned')
    elif fault == 'duplicate':
        raw['units'].append(deepcopy(raw['units'][0]))
    elif fault == 'instance_zero':
        raw['units'][0]['instance_uid'] = 0
    elif fault == 'wrong_player':
        raw['units'][0]['player_uid'] = '004'
    else:
        zero['autochess_round_start']['native_autochess_context']['memberships'][0]['is_token'] = True
    assert autochess_round_start_lifecycle_instances(zero) is None
    if fault != 'token':
        assert autochess_round_start_origin(zero)


def test_actual_zero_unit_manager_alive_bodies_and_rejected_independent_witnesses_both_platforms():
    report = json.loads(subprocess.check_output(['node', str(ROOT / 'tests/event_probe_lifecycle_baseline_fixture.js')],
                                               cwd=ROOT, text=True, encoding='utf-8'))
    for platform in ('native_pc', 'mumu'):
        assert len(report[platform]['valid']['point']['lifecycle_baseline']['units']) == 2
        assert report[platform]['token']['point']['lifecycle_baseline']['units'] == []
        for fault in ('flags_missing', 'instance_zero', 'duplicate', 'short_read'):
            assert 'lifecycle_baseline' not in report[platform][fault]['point']
