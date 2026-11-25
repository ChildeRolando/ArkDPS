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
                 lifecycle_baseline=dict(version=1, complete=True, character_lifecycle_ready=True, frame=0, game_time=0, fixed='0',
                     units=[dict(cid='char_zero', data_uid=301, instance_uid=77, player_index=3,
                                 player_uid='003', player_side=3, inited=True, borned=True, disappeared=False)]))
    point['quality']['character_lifecycle_ready'] = True
    mark['character_lifecycle_ready'] = drained['character_lifecycle_ready'] = True
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
        for fault in ('flags_missing', 'instance_zero', 'duplicate', 'short_read', 'capability_missing', 'capability_bad_type', 'install_failure'):
            assert 'lifecycle_baseline' not in report[platform][fault]['point']

@pytest.fixture(scope='module')
def cast_transaction_source():
    return json.loads(subprocess.check_output(['node', str(ROOT / 'tests/event_probe_cast_transaction_fixture.js')],
                                             cwd=ROOT, text=True, encoding='utf-8'))


def replay_cast_transaction(packet, *, old_source=False):
    from copy import deepcopy
    stats, stream = setup()
    records = deepcopy(packet['batch']['events'])
    for raw in records:
        if old_source:
            raw.pop('skill_cast_invocation', None)
        stats.advance(raw['game_time'])
        identity = ('foreign', None, None) if raw['source_character_uid'] != 1010 else ('local', 'owner', 1010)
        stream.accept(session='life', settlement_identity=identity, replay_record=raw, **event_processing_payload(raw))
    for index in range(36):
        stats.advance(4)
        stream.accept(session='life', sequence=stream.sequence + 1, frame=120, game_time=4,
            source='char_237_gravel', source_character_uid=1010, source_instance_uid=10,
            kind='damage_settlement', amount=1, nominal_amount=1, shield_absorbed=0,
            settlement_complete=True, damage_type=1, target='enemy_1007_slime', target_uid='1',
            target_instance='enemy-1', killed=False, settlement_identity=('local', 'owner', 1010))
    stream.watermark(session='life', sequence=stream.sequence, frame=120, game_time=4)
    return stats, stats.result()['rows']['owner']['settled_damage']


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
def test_actual_nested_native_call_reproduces_36_unknown_on_old_contract_and_preserves_real_effect_on_new(
        cast_transaction_source, platform):
    from copy import deepcopy
    packet = cast_transaction_source[platform]['nested_after_first']
    original = deepcopy(packet)
    old_stats, old = replay_cast_transaction(packet, old_source=True)
    assert old['skill_unclassified_hits'] == 36 and old['skill_initial_unknown_hits'] == 0
    stats, new = replay_cast_transaction(packet)
    assert new['skill_unclassified_hits'] == 0 and new['skill_initial_unknown_hits'] == 0
    assert new['observed_skill_damage'] == new['observed_damage'] == 36
    assert new['observed_hits'] == old['observed_hits'] == 36
    assert not stats.settlement_skill_unassigned and not stats.settlement_skill_incomplete
    assert stats.skill_active['owner']['start'] == 1
    assert packet == original


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('scenario', ['native_first', 'foreign_after'])
def test_actual_native_transaction_supported_opposite_order_and_same_cid_foreign_isolation(
        cast_transaction_source, platform, scenario):
    stats, lane = replay_cast_transaction(cast_transaction_source[platform][scenario])
    assert lane['skill_unclassified_hits'] == 0
    assert stats.skill_active['owner']['start'] == 1


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('scenario', ['no_effect_start', 'wait_false', 'real_end'])
def test_cast_transaction_without_independent_live_effect_proof_never_opens_effect(
        cast_transaction_source, platform, scenario):
    stats, lane = replay_cast_transaction(cast_transaction_source[platform][scenario])
    assert not stats.skill_active and not stats.settlement_skill_active
    assert lane['observed_skill_damage'] == (0 if scenario == 'real_end' else None)


def test_zero_seed_duplicate_born_is_idempotent_and_retired_native_uid_cannot_be_reborn():
    life = DeploymentLifecycle()
    life.seed_zero('owner', 1010, 10)
    life.observe('owner', 1010, 10, 'character_born', 0, proof('born', 0), frame=0)
    assert life.result('owner', 2, continuous=True, archived=False)['lifecycle_presence_seconds'] == 2
    life.observe('owner', 1010, 10, 'skill_end', 2, proof('finish', 2), frame=60)
    life.observe('owner', 1010, 10, 'character_born', 3, proof('born', 3), frame=90)
    assert life.result('owner', 4, continuous=True, archived=False)['lifecycle_presence_complete'] is False


@pytest.mark.parametrize('reason', [11, 12])
def test_typed_temporary_finish_keeps_body_open_but_malformed_finish_invalidates(reason):
    life = DeploymentLifecycle()
    life.seed_zero('owner', 1010, 10)
    life.observe('owner', 1010, 10, 'skill_end', 1, proof('finish', 1, reason=reason), frame=30, expected_finish=True)
    assert life.result('owner', 2, continuous=True, archived=False)['lifecycle_presence_seconds'] == 2
    invalid = dict(proof('finish', 3), fixed='0')
    life.observe('owner', 1010, 10, 'skill_end', 3, invalid, frame=90, expected_finish=True)
    assert life.result('owner', 4, continuous=True, archived=False)['lifecycle_presence_complete'] is False

@pytest.mark.parametrize('order', ['finish_first', 'born_first'])
def test_same_clock_body_handoff_preserves_both_intervals_in_both_orders(order):
    life = DeploymentLifecycle()
    life.observe('owner', 1010, 10, 'character_born', 1, proof('born', 1), frame=30)
    callbacks = [(10, 'skill_end', proof('finish', 2)),
                 (20, 'character_born', proof('born', 2, instance=20))]
    if order == 'born_first':
        callbacks.reverse()
    for instance, kind, certificate in callbacks:
        life.observe('owner', 1010, instance, kind, 2, certificate, frame=60)
    life.observe('owner', 1010, 20, 'skill_end', 4, proof('finish', 4, instance=20), frame=120)
    result = life.result('owner', 4, continuous=True, archived=True)
    assert result['lifecycle_presence_seconds'] == 3
    assert result['lifecycle_presence_complete'] is True and result['lifecycle_terminal_complete'] is True


def test_positive_time_body_overlap_is_never_certified_or_double_counted():
    life = DeploymentLifecycle()
    life.observe('owner', 1010, 10, 'character_born', 1, proof('born', 1), frame=30)
    life.observe('owner', 1010, 20, 'character_born', 2, proof('born', 2, instance=20), frame=60)
    assert life.result('owner', 2, continuous=True, archived=False)['lifecycle_presence_seconds'] is None
    life.observe('owner', 1010, 10, 'skill_end', 3, proof('finish', 3), frame=90)
    life.observe('owner', 1010, 20, 'skill_end', 4, proof('finish', 4, instance=20), frame=120)
    assert life.result('owner', 4, continuous=True, archived=True)['lifecycle_presence_seconds'] is None


@pytest.mark.parametrize('changes', [dict(fixed='1'), dict(fixed='00'), dict(fixed='9'*10000),
    dict(fixed=str(1 << 63)), dict(game_time=float('nan')), dict(game_time=float('inf')),
    dict(game_time=10**1000), dict(instance_uid=1 << 32), dict(data_uid=True), dict(frame=-1)])
def test_lifecycle_raw_clock_and_identity_reject_malformed_values_without_throwing(changes):
    value = dict(proof('born', 0), **changes)
    assert lifecycle_proof(value, kind='character_born', actor=value['data_uid'], instance=value['instance_uid'],
                           frame=value['frame'], game_time=value['game_time']) is None

@pytest.mark.parametrize('fault', ['invocation', 'native_body', 'missing_invocation'])
def test_native_normal_return_cannot_borrow_another_invocation_or_body(cast_transaction_source, fault):
    from copy import deepcopy
    packet = deepcopy(cast_transaction_source['native_pc']['nested_after_first'])
    cast = next(e for e in packet['batch']['events'] if e['proof']['method'] == 'BasicSkill.OnCastFinish')
    if fault == 'invocation':
        cast['skill_cast_invocation'] += 1
    elif fault == 'native_body':
        cast['source_instance_uid'] += 1
    else:
        del cast['skill_cast_invocation']
    stats, lane = replay_cast_transaction(packet)
    assert lane['skill_unclassified_hits'] == 36 and not stats.settlement_skill_unassigned


@pytest.mark.parametrize('mark_field', ['point', 'observer'])
def test_zero_body_baseline_requires_complete_actual_hook_capability_at_both_fences(mark_field):
    from event_probe import autochess_round_start_lifecycle_instances
    zero, _, _, _ = lifecycle_zero_sample()
    if mark_field == 'point':
        zero['autochess_round_start']['quality']['character_lifecycle_ready'] = False
    else:
        zero['autochess_lifecycle_baseline_ready'] = False
    assert autochess_round_start_lifecycle_instances(zero) is None


def test_late_born_cannot_hide_earlier_body_output_but_same_clock_and_post_retreat_dot_are_valid():
    life = DeploymentLifecycle()
    life.note_body_output('owner', 1010, 10, 1)
    life.observe('owner', 1010, 10, 'character_born', 2, proof('born', 2), frame=60)
    assert life.result('owner', 4, continuous=True, archived=False)['lifecycle_presence_complete'] is False
    same_clock = DeploymentLifecycle()
    same_clock.note_body_output('owner', 1010, 10, 1)
    same_clock.observe('owner', 1010, 10, 'character_born', 1, proof('born', 1), frame=30)
    same_clock.observe('owner', 1010, 10, 'skill_end', 2, proof('finish', 2), frame=60)
    same_clock.note_body_output('owner', 1010, 10, 3)
    assert same_clock.result('owner', 4, continuous=True, archived=True)['lifecycle_presence_seconds'] == 1


def test_selected_real_pc_disk_metadata_confirms_runtime_lifecycle_and_cast_signatures():
    frozen = json.loads((ROOT / 'tests/fixtures/event_probe_business/20261125-pc-lifecycle-metadata-v29.json').read_text(encoding='utf-8'))
    assert frozen['metadata_sha256'] == 'e58fb854ae519bc22f7d8bc7ac83502425a51b896a907a3374f5215202f9caa5'
    types = frozen['types']
    assert types['Torappu.Battle.Entity.FinishReason']['declaring_type'] == 'Torappu.Battle.Entity'
    assert types['Torappu.Battle.Entity.FinishReason']['enum_literals']['HP_ZERO'] == 2
    assert types['Torappu.Battle.Entity.FinishReason']['enum_literals']['MOVE_LIKE_RESPAWN_EXTERNAL'] == 12
    assert types['Torappu.Battle.Ability.FinishReason']['declaring_type'] == 'Torappu.Battle.Ability'
    assert types['Torappu.Battle.Ability.FinishReason']['enum_literals']['OWNER_DEAD'] == 2
    def method(owner, name):
        return next(m for m in types['Torappu.Battle.' + owner]['methods'] if m['name'] == name)
    assert method('Character', 'Born')['parameters'] == []
    assert method('Character', 'Born')['return_type'] == method('BObject', 'Born')['return_type'] == 'System.Void'
    assert [p['type'] for p in method('BattleController', 'OnCharacterFinished')['parameters']] == [
        'Torappu.Battle.Character', 'Torappu.Battle.Entity.FinishReason']
    assert [p['type'] for p in method('Character', 'OnAfterSkill')['parameters']] == [
        'Torappu.Battle.BasicSkill', 'Torappu.Battle.Ability.FinishReason']
    metadata = Path('D:/games/Hypergryph Launcher/games/Arknights/Arknights_Data/il2cpp_data/Metadata/global-metadata.dat')
    if metadata.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location('inspect_lifecycle_metadata', ROOT / 'tools/inspect_lifecycle_metadata.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.inspect(metadata) == frozen

@pytest.mark.parametrize('instance', [None, 10])
def test_multiplayer_body_hit_before_late_born_cannot_certify_shorter_denominator(instance):
    from multiplayer_battle import MultiplayerBattleStatistics
    from test_multiplayer_native_context import CID, consume, hit, player, stamp
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    early = hit(1, 3, 1, 10)
    if instance is not None:
        early['source_instance_uid'] = instance
    born = dict(sequence=2, frame=60, game_time=2, source=CID, source_character_uid=301,
                source_instance_uid=20, kind='character_born', amount=0,
                actor_lifecycle=proof('born', 2, actor=301, instance=20), native_autochess_scope=stamp(when=2))
    result = consume(meter, 3, [early, born])
    row = next(iter(player(result, 3)['statistics']['rows'].values()))
    assert row['lifecycle_presence_complete'] is False and row['lifecycle_presence_seconds'] is None


def test_multiplayer_token_with_character_like_cid_is_not_a_host_body_output_witness():
    from multiplayer_battle import MultiplayerBattleStatistics
    from test_multiplayer_native_context import CID, consume, hit, member, player, context, stamp
    meter = MultiplayerBattleStatistics()
    members = [member(3), member(3, uid=302, cid='char_token_disguised', is_token=True, host_key=CID, host_uid=301)]
    consume(meter, 0, native=context(0, members=members))
    born = dict(sequence=1, frame=30, game_time=1, source=CID, source_character_uid=301,
                source_instance_uid=10, kind='character_born', amount=0,
                actor_lifecycle=proof('born', 1, actor=301), native_autochess_scope=stamp(when=1))
    token = hit(2, 3, 2, 10, uid=302, cid='char_token_disguised')
    result = consume(meter, 3, [born, token], native=context(3, members=members))
    row = next(iter(player(result, 3)['statistics']['rows'].values()))
    assert row['lifecycle_presence_seconds'] == 2 and row['lifecycle_presence_complete'] is True
