"""Missing native skill initial state is not evidence of non-skill damage."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from roster import RosterSnapshot
from event_probe import bind_native_autochess_pair
from test_multiplayer_native_context import (CID, PLAYERS, QUALITY, ROOT,
    MultiplayerBattleStatistics, consume, context, hit, member, player, stamp)


CANONICAL_ROOT = '0x10000:0x20000:0x30000:0x40000:0x50000:autochess:2'


def activation(sequence=1, when=0, index=3, uid=None, cid=CID, event_scope=None):
    return dict(sequence=sequence, frame=round(when * 30), game_time=when,
        source=cid, source_character_uid=index * 100 + 1 if uid is None else uid,
        kind='skill_start', amount=1, skill_key='skchr_swllow_2',
        proof={'method': 'Character.SwitchToSkillState'},
        native_autochess_scope=stamp(when=when) if event_scope is None else event_scope)


def lane(result, index):
    return player(result, index)['actor_ledgers'][0]['ledger']


@pytest.mark.parametrize('when,changes', [(10, dict(global_total=500)),
    (0.1, {}), (0, dict(global_heal=1)), (0, dict(global_total=1)),
    (0, dict(rows=[dict(id=CID, damage=1, healing=0)]))])
def test_cold_or_already_output_baseline_never_certifies_missing_skill_state_as_non_skill(when, changes):
    meter = MultiplayerBattleStatistics()
    consume(meter, when, sequence=50, **changes)
    result = consume(meter, when + 1, [hit(51, 3, when + 1, 100)])
    observed = lane(result, 3)
    assert player(result, 3)['observed_damage'] == 100
    assert observed['skill_split_complete'] is False
    assert observed['skill_damage_unverified_share'] == 1
    assert observed['observed_skill_damage'] is observed['observed_non_skill_damage'] is None


def test_fresh_zero_baseline_does_not_infer_missing_skill_state_from_global_old_sequence():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, sequence=500)
    result = consume(meter, 1, [hit(501, 3, 1, 100)])
    assert lane(result, 3)['skill_split_complete'] is True
    assert lane(result, 3)['observed_non_skill_damage'] == 100
    assert lane(result, 3)['skill_damage_unverified_share'] == 0


@pytest.mark.parametrize('witnesses', [
    {'actor_rows': {'status': 'unavailable', 'reason': 'counter_list_size'}},
    {'actor_rows': {'status': 'not_requested', 'reason': 'native_event_identity'}},
    {'actor_rows': {'status': None}}, {}, None])
def test_empty_rows_without_available_native_counter_witness_do_not_certify_initial_skill_state(witnesses):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native_snapshot_witnesses=witnesses)
    result = consume(meter, 1, [hit(1, 3, 1, 100)])
    observed = lane(result, 3)
    assert observed['observed_damage'] == 100
    assert observed['skill_split_complete'] is False
    assert observed['observed_non_skill_damage'] is None
    assert observed['skill_damage_unverified_share'] == 1


def test_available_empty_native_counter_rows_still_certify_fresh_initial_skill_state():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native_snapshot_witnesses={
        'actor_rows': {'status': 'available', 'reason': None},
        'roster': {'status': 'unavailable', 'reason': 'counter_list_size'}})
    result = consume(meter, 1, [hit(1, 3, 1, 100)])
    assert lane(result, 3)['skill_split_complete'] is True
    assert lane(result, 3)['observed_non_skill_damage'] == 100


@pytest.mark.parametrize('baseline_time,changes,peer_non_skill', [
    (2, {}, None),
    (0, {'global_total': 700000, 'global_heal': 20000, 'native_snapshot_witnesses': {
        'actor_rows': {'status': 'available', 'reason': None}}}, None),
    (0, {'native_snapshot_witnesses': {
        'actor_rows': {'status': 'not_requested', 'reason': 'native_event_identity'}}}, None),
    (0, {'quality': {'baseline_discarded_skill_events': [activation(50)]}}, 100),
])
def test_verified_effect_recovers_after_a_cold_baseline_without_reclassifying_old_hits(
        baseline_time, changes, peer_non_skill):
    meter = MultiplayerBattleStatistics()
    consume(meter, baseline_time, sequence=50, **changes)
    requested = activation(52, 4)
    requested['proof']['method'] = 'Character.OnSkillStart'
    result = consume(meter, 6, [
        hit(51, 3, 3, 20), requested, hit(53, 3, 5, 30),
        activation(54, 6), hit(55, 3, 6, 50)])
    during = lane(result, 3)
    assert during['observed_damage'] == 100
    assert during['observed_skill_damage'] == 50
    assert during['observed_non_skill_damage'] == 0
    assert during['skill_split_complete'] is False
    assert during['skill_damage_unverified_share'] == pytest.approx(2 / 3)
    finish = dict(activation(56, 7), kind='skill_end', proof={'method': 'Character.OnSkillFinish'})
    result = consume(meter, 8, [finish, hit(57, 3, 7, 25), hit(58, 4, 8, 100)])
    after = lane(result, 3)
    assert after['observed_skill_damage'] == 50
    assert after['observed_non_skill_damage'] == 25
    assert after['observed_skill_damage_effective'] == 50
    assert after['observed_non_skill_damage_effective'] == 25
    assert after['skill_split_complete'] is False
    assert after['skill_damage_unverified_share'] == pytest.approx(0.5)
    peer = lane(result, 4)
    assert peer['observed_non_skill_damage'] == peer_non_skill
    assert peer['skill_damage_unverified_share'] == (1 if peer_non_skill is None else 0)


@pytest.mark.parametrize('starts_unknown', [True, False])
def test_unmatched_cast_return_recovers_only_for_an_unknown_initial_actor(starts_unknown):
    meter = MultiplayerBattleStatistics()
    consume(meter, 1 if starts_unknown else 0, sequence=50)
    cast_return = dict(activation(51, 2), kind='skill_end',
        skill_wait_for_end=True, skill_reason=1, proof={'method': 'BasicSkill.OnCastFinish'})
    result = consume(meter, 4, [cast_return, activation(52, 3), hit(53, 3, 4, 100)])
    observed = lane(result, 3)
    assert observed['observed_damage'] == 100
    assert observed['observed_skill_damage'] == (100 if starts_unknown else None)
    assert observed['skill_split_complete'] is starts_unknown


def test_tentative_initial_window_does_not_block_a_later_verified_effect():
    meter = MultiplayerBattleStatistics()
    consume(meter, 1, sequence=50)
    requested = dict(activation(51, 2), proof={'method': 'Character.OnSkillStart'})
    cast_return = dict(activation(52, 3), kind='skill_end',
        skill_wait_for_end=True, skill_reason=1, proof={'method': 'BasicSkill.OnCastFinish'})
    result = consume(meter, 6, [requested, cast_return, hit(53, 3, 4, 20),
        activation(54, 5), hit(55, 3, 6, 50)])
    observed = lane(result, 3)
    assert observed['observed_damage'] == 70
    assert observed['observed_skill_damage'] == 50
    assert observed['skill_split_complete'] is False
    assert observed['skill_damage_unverified_share'] == pytest.approx(0.5)


@pytest.mark.parametrize('field,bad', [('baseline_discarded_skill_events_complete', None),
    ('baseline_discarded_skill_events_complete', False),
    ('baseline_discarded_skill_events_complete', 1), ('baseline_discarded_skill_events', None)])
def test_incomplete_or_malformed_prefix_evidence_keeps_fresh_actor_initial_state_unknown(field, bad):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, quality={field: bad})
    result = consume(meter, 1, [hit(1, 3, 1)])
    assert lane(result, 3)['skill_split_complete'] is False
    assert lane(result, 3)['skill_damage_unverified_share'] == 1


def test_new_native_contract_missing_prefix_fields_is_not_silently_backfilled_complete():
    meter = MultiplayerBattleStatistics()
    quality = {k: v for k, v in QUALITY.items() if not k.startswith('baseline_discarded_skill_events')}
    sample = dict(session='round-owner:1', native_session='native-controller:1',
        frame=0, game_time=0, global_total=0, global_heal=0, rows=[],
        native_counter_scope='events_only', autochess_context=context(0),
        battle_metadata={'stage_id': 'Activities/ACT3AUTOCHESS/level_test'})
    meter.consume(sample, RosterSnapshot(0), 0, dict(events=[], **quality), mark=quality)
    result = consume(meter, 1, [hit(1, 3, 1)])
    assert lane(result, 3)['observed_non_skill_damage'] is None
    assert lane(result, 3)['skill_damage_unverified_share'] == 1


def test_retained_discarded_start_only_quarantines_its_verified_actor_without_replaying_window():
    meter = MultiplayerBattleStatistics()
    lost = activation()
    original = deepcopy(lost)
    consume(meter, 0, sequence=1, quality={'baseline_discarded_skill_events': [lost]})
    result = consume(meter, 5, [hit(i + 1, i, i) for i in range(1, 5)])
    assert lane(result, 3)['skill_damage_unverified_share'] == 1
    assert all(lane(result, i)['skill_split_complete'] is True for i in (1, 2, 4))
    assert all(not p.statistics.skill_active and not p.statistics.settlement_skill_active
               for p in meter._players.values())
    assert all(1 not in p.stream.received for p in meter._players.values())
    assert lost == original


def test_real_baseline_summary_without_kind_or_amount_is_local_uncertainty_evidence():
    meter = MultiplayerBattleStatistics()
    source = activation()
    lost = {key: value for key, value in source.items() if key not in ('kind', 'amount', 'skill_key')}
    original = deepcopy(lost)
    consume(meter, 0, sequence=1, quality={'baseline_discarded_skill_events': [lost]})
    result = consume(meter, 2, [hit(2, 3, 1), hit(3, 1, 2)])
    assert lane(result, 3)['skill_damage_unverified_share'] == 1
    assert lane(result, 1)['skill_split_complete'] is True
    assert not meter._players[PLAYERS[2]['uid']].statistics.settlement_skill_active
    assert lost == original and 'kind' not in lost and 'amount' not in lost


def test_baseline_retained_events_fallback_is_only_initial_uncertainty_evidence():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, [activation()], sequence=1)
    result = consume(meter, 1, [hit(2, 3, 1)])
    assert lane(result, 3)['observed_damage'] == 100
    assert lane(result, 3)['skill_damage_unverified_share'] == 1
    assert not meter._players[PLAYERS[2]['uid']].statistics.settlement_skill_active


@pytest.mark.parametrize('scope', [stamp(phase=5, when=0),
    stamp(native_round=5, when=0), stamp(root=ROOT + ':different', when=0)])
def test_unrelated_phase_round_or_root_discarded_prefix_does_not_poison_fresh_actors(scope):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, sequence=1,
        quality={'baseline_discarded_skill_events': [activation(event_scope=scope)]})
    result = consume(meter, 1, [hit(2, 3, 1)])
    assert lane(result, 3)['skill_split_complete'] is True


def test_previous_native_epoch_same_five_roots_and_role_is_only_local_uncertainty_after_clock_reset():
    meter = MultiplayerBattleStatistics()
    old = activation(when=180, event_scope=stamp(when=180,
        root=CANONICAL_ROOT.rsplit(':', 1)[0] + ':1'))
    consume(meter, 0, sequence=1, native=context(0, root=CANONICAL_ROOT),
        quality={'baseline_discarded_skill_events': [old]})
    result = consume(meter, 2, [hit(2, 3, 1, event_scope=stamp(root=CANONICAL_ROOT)),
        hit(3, 1, 2, event_scope=stamp(root=CANONICAL_ROOT))], native=context(2, root=CANONICAL_ROOT))
    assert lane(result, 3)['skill_damage_unverified_share'] == 1
    assert lane(result, 1)['skill_split_complete'] is True


def test_discarded_token_skill_start_does_not_open_or_poison_host_skill_window():
    meter = MultiplayerBattleStatistics()
    token = member(3, uid=399, cid='token_test', is_token=True, host_key=CID, host_uid=301)
    members = [member(3), token]
    consume(meter, 0, sequence=1, native=context(0, members=members), quality={
        'baseline_discarded_skill_events': [activation(uid=399, cid='token_test')]})
    result = consume(meter, 1, [hit(2, 3, 1)], native=context(1, members=members))
    assert lane(result, 3)['skill_split_complete'] is True
    assert not meter._players[PLAYERS[2]['uid']].statistics.settlement_skill_active


def test_missing_uid_prefix_is_diagnostic_without_guessing_all_same_cid_actors():
    meter = MultiplayerBattleStatistics()
    lost = activation()
    lost.pop('source_character_uid')
    consume(meter, 0, sequence=1, quality={'baseline_discarded_skill_events': [lost]})
    result = consume(meter, 5, [hit(i + 1, i, i) for i in range(1, 5)])
    assert all(lane(result, i)['skill_split_complete'] is True for i in range(1, 5))
    assert result['reasons']


def test_late_new_actor_without_a_verified_start_only_makes_its_own_skill_split_unknown():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=context(0, members=[member(3)]))
    consume(meter, 1, [hit(1, 3, 1)], native=context(1, members=[member(3)]))
    members = [member(3), member(4)]
    result = consume(meter, 2, [hit(2, 4, 2)], native=context(2, members=members))
    assert lane(result, 4)['skill_damage_unverified_share'] == 1
    assert lane(result, 3)['skill_split_complete'] is True


def test_late_new_actor_with_shared_core_confirmed_effect_start_can_classify_observed_damage():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=context(0, members=[member(3)]))
    members = [member(3), member(4)]
    result = consume(meter, 2, [activation(when=1, index=4), hit(2, 4, 2)],
        native=context(2, members=members))
    assert lane(result, 4)['skill_split_complete'] is True
    assert lane(result, 4)['observed_skill_damage'] == 100


@pytest.mark.parametrize('first_kind', ['healing', 'elemental_bar'])
def test_new_actor_non_damage_before_real_start_does_not_override_its_first_confirmed_damage_window(first_kind):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=context(0, members=[member(3)]))
    first = dict(sequence=1, frame=30, game_time=1, source=CID, source_character_uid=401,
        kind=first_kind, amount=10, native_autochess_scope=stamp(when=1))
    if first_kind == 'elemental_bar':
        first.update(target_uid='500', target_instance='target-500')
        damage = hit(3, 4, 3, source=None, damage_type=5)
        damage.pop('source_character_uid')
    else:
        damage = hit(3, 4, 3)
    result = consume(meter, 3, [first, activation(2, when=2, index=4), damage],
        native=context(3, members=[member(3), member(4)]))
    assert lane(result, 4)['observed_skill_damage'] == 100
    assert lane(result, 4)['skill_split_complete'] is True


def test_new_actor_bar_without_any_effect_start_keeps_sourceless_damage_skill_unknown():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=context(0, members=[member(3)]))
    bar = dict(sequence=1, frame=30, game_time=1, source=CID, source_character_uid=401,
        kind='elemental_bar', amount=10, target_uid='500', target_instance='target-500',
        native_autochess_scope=stamp(when=1))
    damage = hit(2, 4, 2, source=None, damage_type=5)
    damage.pop('source_character_uid')
    result = consume(meter, 2, [bar, damage], native=context(2, members=[member(3), member(4)]))
    assert lane(result, 4)['observed_damage'] == 100
    assert lane(result, 4)['observed_non_skill_damage'] is None
    assert lane(result, 4)['skill_damage_unverified_share'] == 1


def source_baseline(mode):
    """Actual adapter/runtime packets from owned memory; no game connection."""
    script = r"""
const vm=require('node:vm');
const {world}=require('./tests/native_pc_autochess_context_fixture.js');
const mode=process.argv[1],w=world({token:mode==='token'});
for(const [index,p]of w.players)w.write(p.add(0x18),w.text(String(910000000+index)));
w.mark();
if(mode==='old_epoch')w.clock(5400,180n*4294967296n);
if(mode==='foreign_phase'){w.write(w.machine.add(0x5c),5);w.mark();}
if(['unknown','missing_uid','token'].includes(mode))vm.runInContext('nativeAutoCache=null',w.context);
const source=mode==='token'?'token_shared':'char_shared',uid=mode==='token'?999:mode==='missing_uid'?null:103;
w.context.skillArgs={source,uid};
vm.runInContext("record(skillArgs.source,'skill_start',1,time(),{method:'Character.SwitchToSkillState'},{skill_key:'skillA'},skillArgs.uid)",w.context);
const original=JSON.parse(vm.runInContext('JSON.stringify(queue[0])',w.context));
if(mode==='old_epoch')w.clock(0,0n);
if(mode==='foreign_phase')w.write(w.machine.add(0x5c),4);
const mark=w.mark(),baseline=w.context.rpc.exports.readEvents(mark.sequence,mark.sequence);
w.clock(30,4294967296n);w.context.damageUid=mode==='token'?101:103;
vm.runInContext("record('char_shared','damage_settlement',100,time(),{method:'Entity.ApplyModifier'},{nominal_amount:100,target:'enemy_1007_slime',target_uid:'500',target_instance:'target-500',damage_type:1,shield_absorbed:0,settlement_complete:true,killed:false},damageUid)",w.context);
const after=w.mark(),next=w.context.rpc.exports.readEvents(mark.sequence,after.sequence);
console.log(JSON.stringify({original,mark,baseline,after,next}));
"""
    completed = subprocess.run(['node', '-e', script, mode], capture_output=True,
        text=True, encoding='utf-8', check=True, timeout=30, cwd=Path(__file__).resolve().parents[1])
    return json.loads(completed.stdout)


@pytest.mark.parametrize('mode,unknown', [('unknown', True), ('old_epoch', True),
                                        ('foreign_phase', False), ('missing_uid', False), ('token', False)])
def test_actual_source_baseline_summary_flows_through_binding_and_uid_local_skill_uncertainty(mode, unknown):
    packet = source_baseline(mode)
    original = deepcopy(packet)
    meter = MultiplayerBattleStatistics()
    for mark, batch in ((packet['mark'], packet['baseline']), (packet['after'], packet['next'])):
        sample = dict(session='owned-world:1', native_session='native-world:1',
            frame=mark['frame'], game_time=mark['game_time'], global_total=0, global_heal=0, rows=[],
            battle_metadata=dict(platform='native_pc', source_build='owned-world-fixture',
                                 stage_id='ACT3AUTOCHESS'))
        bind_native_autochess_pair(sample, mark, deepcopy(mark))
        quality = dict(QUALITY)
        for key in ('baseline_discarded_skill_events', 'baseline_discarded_skill_events_complete'):
            if key in batch:
                quality[key] = batch[key]
        result = meter.consume(sample, RosterSnapshot(sample['frame']), mark['sequence'],
            dict(quality, events=batch['events']), mark=quality)
    slot = 1 if mode == 'token' else 3
    observed = lane(result, slot)
    assert observed['observed_damage'] == 100
    assert observed['skill_split_complete'] is (not unknown)
    assert observed['skill_damage_unverified_share'] == (1 if unknown else 0)
    assert all(not p.statistics.skill_active and not p.statistics.settlement_skill_active
               for p in meter._players.values())
    assert packet == original
    if mode == 'old_epoch':
        assert len(packet['baseline']['baseline_discarded_skill_events']) == 1
        assert packet['baseline']['baseline_discarded_skill_events_complete'] is True
        old = packet['original']['native_autochess_scope']
        current = packet['mark']['native_autochess_context']
        assert old['scope_key'] != current['scope_key']
        assert (old['phase'], old['round']) == (current['phase'], current['round'])
