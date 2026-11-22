"""Optional player observations remain bound to their frozen battle record."""
from copy import deepcopy
import json

import pytest

from battle_history import BattleHistory, BattleRecord, HistoryError
from multiplayer_battle import MultiplayerBattleStatistics
from roster import RosterSnapshot
from test_battle_history import record
from test_multiplayer_battle import actor, hit, sample, QUALITY


def player_record(*, previous=False):
    base = record()
    data = base.to_dict()
    battle = base.battle_id
    meter = MultiplayerBattleStatistics()
    entries = [actor(2), actor(3)]

    def consume(when, sequence, events=()):
        value = sample(when, session=battle)
        return meter.consume(value, RosterSnapshot(value['frame'], tuple(entries), local_player_side=3),
            sequence, dict(events=list(events), **QUALITY), mark=QUALITY, round_id=battle)

    consume(0, 0)
    consume(2, 1, [hit(1, 2, 1, 100)])
    if previous:
        entries = [actor(2, uid=222), actor(3, uid=333)]
        consume(3, 1)
        consume(5, 2, [hit(2, 3, 4, 50, uid=333)])
        sequence = 2
    else:
        sequence = 1
    consume(12, sequence)
    data['last_sample']['multiplayer'] = meter.finish(12, complete=True)
    return data


def first_actor(payload):
    return payload['players'][1]['actor_ledgers'][0]


@pytest.mark.parametrize('kind,amount', [('healing', -1), ('invalid_kind', 1)])
def test_foreign_callback_failure_freezes_event_endpoint_but_preserves_archive_clock(kind, amount):
    data = player_record()
    meter = MultiplayerBattleStatistics()
    entries = (actor(2), actor(3))
    for when, seq, events in ((0, 0, []), (2, 1, [hit(1, 2, 1, 100)]),
                             (4, 2, [dict(hit(2, 2, 3), kind=kind, amount=amount)])):
        value = sample(when, session=data['battle_id'])
        result = meter.consume(value, RosterSnapshot(value['frame'], entries), seq,
                               dict(events=events, **QUALITY), mark=QUALITY)
    assert result['status'] == 'gap'
    assert result['game_time'] == 4
    frozen = meter.finish(4, complete=False)
    player = frozen['players'][1]
    assert player['observed_damage'] == 100
    assert player['observed_window'] == [0, 2]
    assert player['coverage_complete'] is False
    assert all(p['statistics']['game_time'] == frozen['game_time'] for p in frozen['players'])
    data['last_sample']['multiplayer'] = frozen
    assert BattleRecord.from_dict(data).last_sample['multiplayer'] == json.loads(json.dumps(frozen))


def test_module_frozen_payload_survives_record_and_disk_roundtrip_with_unknown_values(tmp_path):
    data = player_record(previous=True)
    original = BattleRecord.from_dict(data)
    history = BattleHistory(tmp_path)
    assert history.save(original).written
    loaded, = BattleHistory(tmp_path).get(original.battle_id).records
    payload = loaded.last_sample['multiplayer']
    assert payload == original.last_sample['multiplayer']
    assert payload['source_round_id'] == original.battle_id
    assert payload['identity_epoch'] == 1
    assert len(payload['previous_rounds']) == 1
    assert payload['players'][0]['observed_damage'] is None
    assert payload['players'][0]['observed_dps'] is None
    assert payload['players'][0]['damage'] is None
    assert payload['players'][2]['observed_damage'] == 50
    assert payload['players'][2]['damage'] is None


@pytest.mark.parametrize('present', [False, True])
def test_legacy_record_without_player_observations_or_with_explicit_null_still_reads(present):
    data = record().to_dict()
    if present:
        data['last_sample']['multiplayer'] = None
    loaded = BattleRecord.from_dict(data)
    assert loaded.last_sample.get('multiplayer') is None


@pytest.mark.parametrize('change', [
    lambda p: p.update(schema_version=2),
    lambda p: p.update(source_round_id='different-battle'),
    lambda p: p.update(round_id='different-battle:players:0'),
    lambda p: p.update(identity_epoch=True),
    lambda p: p.update(game_time=9999),
    lambda p: p.update(coverage_start=20),
    lambda p: p.update(status='observing'),
    lambda p: p['players'][0].update(player_side=2),
    lambda p: p['players'].pop(),
    lambda p: p['players'][1].update(damage=100),
    lambda p: p['players'][1].update(dps=0),
    lambda p: p['players'][1].update(recent_dps=20),
    lambda p: p['players'][1].update(observed_damage=-1),
    lambda p: p['players'][1].update(observed_dps=float('nan')),
    lambda p: p['players'][0].update(observed_dps=0),
    lambda p: p['players'][0].update(coverage_complete=True),
    lambda p: p['players'][1].update(observed_window=[0, 13]),
    lambda p: p['players'][1].update(observed_recent_window=[11, 10]),
    lambda p: p['players'][1]['statistics'].update(session='other-scope:players:0:2'),
    lambda p: p['players'][1]['statistics'].update(ended=False),
    lambda p: p['players'][1]['statistics'].update(totals_complete=True),
    lambda p: first_actor(p).update(owner_id='other-round:char_367_swllow#201'),
    lambda p: first_actor(p).update(source_character_uid=301),
    lambda p: first_actor(p).update(actor_uid=999),
    lambda p: p.update(observed_player_sides=[1, 2]),
])
def test_malformed_identity_time_native_or_unknown_claims_are_rejected(change):
    data = player_record()
    change(data['last_sample']['multiplayer'])
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)


def test_row_owner_and_ledger_cannot_be_moved_to_a_different_actor_or_player():
    data = player_record()
    payload = data['last_sample']['multiplayer']
    side2, side3 = payload['players'][1:3]
    actor2, = side2['actor_ledgers']
    owner = actor2['owner_id']
    row = side2['statistics']['rows'][owner]
    row['damage'] = 123
    with pytest.raises(HistoryError, match='native counters'):
        BattleRecord.from_dict(data)
    row['damage'] = None
    actor2['ledger']['observed_damage'] = 999
    with pytest.raises(HistoryError, match='ledger mismatch'):
        BattleRecord.from_dict(data)
    actor2['ledger'] = deepcopy(row['settled_damage'])
    side3['actor_ledgers'].append(deepcopy(actor2))
    side3['statistics']['rows'][owner] = deepcopy(row)
    with pytest.raises(HistoryError, match='actor identity'):
        BattleRecord.from_dict(data)


@pytest.mark.parametrize('change', [
    lambda p: p['previous_rounds'][0].update(source_round_id='other-battle'),
    lambda p: p['previous_rounds'][0].update(identity_epoch=p['identity_epoch']),
    lambda p: p['previous_rounds'].append(deepcopy(p['previous_rounds'][0])),
    lambda p: p.update(previous_rounds=p['previous_rounds'] * 9),
    lambda p: p.update(previous_rounds_pruned=-1),
    lambda p: p['previous_rounds'][0]['players'][0].update(statistics={'rows': {}}),
])
def test_retired_scopes_are_same_battle_bounded_ordered_compact_summaries(change):
    data = player_record(previous=True)
    change(data['last_sample']['multiplayer'])
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)
