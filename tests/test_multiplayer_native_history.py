"""Schema 2 archives preserve real account identity and independent phase scopes."""
from copy import deepcopy
import json

import pytest

from battle_history import BattleHistory, BattleRecord, HistoryError
from test_battle_history import record
from test_multiplayer_native_context import (CID, PLAYERS, MultiplayerBattleStatistics,
                                             consume, context, hit, member, stamp)


def native_record(*, previous=False, missing_slot=False, unknown=False):
    data = record().to_dict()
    meter = MultiplayerBattleStatistics()
    battle = data['battle_id']
    players = PLAYERS[:3] if missing_slot else PLAYERS
    members = [member(3)]
    if unknown:
        members.append(member(2, uid=299, cid='token_bad', is_token=True))
    consume(meter, 0, session=battle, native=context(0, players=players, members=members))
    consume(meter, 2, [hit(1, 3, 1)], session=battle,
            native=context(2, players=players, members=members))
    phase = 5 if previous else 4
    sequence = 1
    if previous:
        consume(meter, 3, sequence=1, session=battle,
                native=context(3, phase=5, players=players, members=members))
        consume(meter, 5, [hit(2, 3, 4, 50, event_scope=stamp(phase=5))], session=battle,
                native=context(5, phase=5, players=players, members=members))
        sequence = 2
    consume(meter, 12, sequence=sequence, session=battle,
            native=context(12, phase=phase, players=players, members=members))
    data['last_sample']['multiplayer'] = meter.finish(12, complete=True)
    return data


def payload(data):
    return data['last_sample']['multiplayer']


def local(value):
    return value['players'][2]


@pytest.mark.parametrize('previous', [False, True])
@pytest.mark.parametrize('missing_slot', [False, True])
def test_frozen_real_schema2_payload_survives_record_and_disk_roundtrip(tmp_path, previous, missing_slot):
    data = native_record(previous=previous, missing_slot=missing_slot, unknown=True)
    original = BattleRecord.from_dict(data)
    history = BattleHistory(tmp_path)
    assert history.save(original).written
    loaded, = BattleHistory(tmp_path).get(original.battle_id).records
    value = loaded.last_sample['multiplayer']
    assert value == json.loads(json.dumps(payload(data)))
    assert value['schema_version'] == 2
    assert local(value)['player_uid'] == PLAYERS[2]['uid']
    assert local(value)['observed_damage'] == (50 if previous else 100)
    assert value['unknown_memberships']
    if previous:
        old, = value['previous_rounds']
        assert old['scope_kind'] == 'defense' and value['scope_kind'] == 'assist'
        assert old['scope'] != value['scope']
        assert all('statistics' not in p and 'actor_ledgers' not in p for p in old['players'])
    if missing_slot:
        absent = value['players'][3]
        assert absent['player_uid'] is absent['statistics'] is absent['observed_damage'] is None
        assert absent['actor_ledgers'] == []


@pytest.mark.parametrize('change', [
    lambda p: p.update(source_round_id='other-battle'),
    lambda p: p.update(scope='autochess:wrong'),
    lambda p: p.update(round_id='wrong'),
    lambda p: p.update(scope_kind='assist'),
    lambda p: p.update(native_phase=5),
    lambda p: p.update(native_round=99),
    lambda p: p.update(self_player_uid=PLAYERS[0]['uid']),
    lambda p: p['context_identity'].update(phase=5),
    lambda p: p['context_identity'].update(scope_key='different-roots'),
    lambda p: p['context_identity'].update(round=True),
    lambda p: p['players'][0].update(slot=3),
    lambda p: p['players'][0].update(player_uid=PLAYERS[2]['uid']),
    lambda p: p['players'][0].update(player_uid=3),
    lambda p: local(p).update(damage=0),
    lambda p: local(p).update(dps=1),
    lambda p: local(p).update(recent_dps=0),
    lambda p: local(p).update(observed_damage=-1),
    lambda p: local(p).update(observed_dps=float('nan')),
    lambda p: local(p)['statistics'].update(ended=False),
    lambda p: local(p)['statistics'].update(session='other-scope:910000003'),
    lambda p: local(p)['statistics'].update(totals_complete=True),
    lambda p: local(p)['actor_ledgers'][0].update(player_uid=PLAYERS[0]['uid']),
    lambda p: local(p)['actor_ledgers'][0].update(owner_id=f'{CID}#301'),
    lambda p: local(p)['actor_ledgers'][0].update(source_character_uid=302),
    lambda p: p.update(observed_player_slots=[1, 3]),
    lambda p: p.update(reasons=['too long'] * 65),
    lambda p: p.update(unknown_memberships=[{'row_index': 0, 'reason': 'bad'}] * 514),
    lambda p: p.update(unknown_memberships=[{'row_index': True, 'reason': 'bad', 'reasons': ['bad']}]),
])
def test_schema2_rejects_cross_account_scope_time_or_unwitnessed_counter_tampering(change):
    data = native_record()
    change(payload(data))
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)


@pytest.mark.parametrize('change', [
    lambda p: p['players'][3].update(observed_damage=0),
    lambda p: p['players'][3].update(statistics={'rows': {}}),
    lambda p: p['players'][3].update(actor_ledgers=[{}]),
    lambda p: p['players'][3].update(is_local=True),
])
def test_unknown_display_slot_cannot_become_a_fake_player_or_zero_counter(change):
    data = native_record(missing_slot=True)
    change(payload(data))
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)


def test_side_none_and_shared_help_side_are_not_player_identity_conflicts():
    data = native_record(previous=True)
    value = payload(data)
    local(value)['player_side'] = None
    value['observed_player_sides'] = []
    assert BattleRecord.from_dict(data).last_sample['multiplayer']['players'][2]['player_side'] is None
    for p in value['players']:
        p['player_side'] = 1
    value['observed_player_sides'] = [1]
    assert BattleRecord.from_dict(data).last_sample['multiplayer']['scope_kind'] == 'assist'


@pytest.mark.parametrize('change', [
    lambda p: p['previous_rounds'][0].update(scope=p['scope'], round_id=p['round_id']),
    lambda p: p['previous_rounds'][0].update(game_time=4),
    lambda p: p['previous_rounds'][0]['players'][0].update(statistics={}),
    lambda p: p['previous_rounds'][0].update(source_round_id='other-battle'),
])
def test_compact_previous_native_scopes_cannot_overlap_new_identity_or_reintroduce_mutable_core(change):
    data = native_record(previous=True)
    change(payload(data))
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)


def test_actor_and_core_row_cannot_be_moved_together_to_a_different_account():
    data = native_record()
    value = payload(data)
    source, target = local(value), value['players'][0]
    actor = deepcopy(source['actor_ledgers'][0])
    owner = actor['owner_id']
    target['actor_ledgers'].append(actor)
    target['statistics']['rows'][owner] = deepcopy(source['statistics']['rows'][owner])
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)


@pytest.mark.parametrize('field,bad', [('observed_skill_damage', False),
    ('observed_skill_damage', float('inf')), ('known_hp_damage', -1),
    ('observed_hits', 1.5), ('skill_damage_unverified_share', 1.1)])
def test_nested_core_observations_keep_strict_numeric_types_and_finite_bounds(field, bad):
    data = native_record()
    p = local(payload(data))
    actor = p['actor_ledgers'][0]
    actor['ledger'][field] = bad
    p['statistics']['rows'][actor['owner_id']]['settled_damage'][field] = bad
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)


def test_native_amplification_percentage_is_finite_signed_evidence_not_negative_damage():
    data = native_record()
    p = local(payload(data))
    actor = p['actor_ledgers'][0]
    actor['ledger']['observed_mitigation_rate'] = -25.0
    p['statistics']['rows'][actor['owner_id']]['settled_damage']['observed_mitigation_rate'] = -25.0
    assert BattleRecord.from_dict(data).last_sample['multiplayer']['players'][2]['actor_ledgers'][0][
        'ledger']['observed_mitigation_rate'] == -25.0


def test_later_membership_conflict_preserves_previously_verified_amount_and_archives_as_partial():
    data = record().to_dict()
    battle = data['battle_id']
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, session=battle)
    consume(meter, 2, [hit(1, 3, 1, 100)], session=battle)
    conflicting = [member(3), member(1, uid=301, cid='char_194_leto')]
    consume(meter, 12, [hit(2, 3, 3, 999)], session=battle,
            native=context(12, members=conflicting))
    data['last_sample']['multiplayer'] = meter.finish(12, complete=True)
    value = BattleRecord.from_dict(data).last_sample['multiplayer']
    assert local(value)['observed_damage'] == 100
    assert local(value)['coverage_complete'] is False
    assert value['unknown_memberships']


def test_ending_with_unknown_context_archives_last_verified_amount_without_claiming_current_scope():
    data = record().to_dict()
    meter = MultiplayerBattleStatistics()
    battle = data['battle_id']
    consume(meter, 0, session=battle)
    consume(meter, 2, [hit(1, 3, 1, 30)], session=battle)
    unknown = context(12)
    unknown.update(status='unknown', verified_same_frame=False, memberships=[])
    consume(meter, 12, [hit(2, 3, 3, 50)], session=battle, native=unknown)
    data['last_sample']['multiplayer'] = meter.finish(12, complete=True)
    value = BattleRecord.from_dict(data).last_sample['multiplayer']
    assert value['available'] is False and value['scope_kind'] == 'unknown'
    assert local(value)['observed_damage'] == 30
    assert not local(value)['coverage_complete']
