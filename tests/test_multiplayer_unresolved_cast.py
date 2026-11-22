"""Missing actor IDs retain the shared receiver's native-cast semantics."""
from copy import deepcopy

import pytest

from multiplayer_battle import MultiplayerBattleStatistics
from roster import CharacterEntry, RosterSnapshot
from test_player_event_isolation import FOREIGN_UID, LOCAL_UID, SOURCE, setup


CID, OTHER = 'char_367_swllow', 'char_140_whitew'
QUALITY = dict(enemy_damage_ready=True, enemy_damage_unknown=False, partial_damage_version=7)


def actor(side, uid, cid=CID):
    return CharacterEntry(cid, uid, False, False, None, None, side)


ACTORS = (actor(2, 201), actor(2, 299), actor(3, 301), actor(4, 401, OTHER))


def consume(meter, when, records=()):
    sample = dict(session='uncertain-cast-round', frame=when * 30, game_time=when,
                  battle_metadata={'stage_id': 'Activities/ACT3AUTOCHESS/level_act3autochess_m11'})
    roster = RosterSnapshot(sample['frame'], ACTORS, local_player_side=3)
    return meter.consume(sample, roster, max((e['sequence'] for e in records), default=0),
                         dict(events=list(records), **QUALITY), mark=QUALITY)


def hit(sequence, entry):
    amount = sequence * 10
    return dict(sequence=sequence, frame=sequence * 30, game_time=sequence,
                kind='damage_settlement', source=entry.cid, source_character_uid=entry.unique_id,
                amount=amount, nominal_amount=amount, target='enemy_1007_slime',
                target_uid=str(sequence), target_instance=f'target-{sequence}',
                damage_type=1, shield_absorbed=0, settlement_complete=True, killed=False)


def unresolved_marker(kind='skill_end', **fields):
    return dict(sequence=1, frame=30, game_time=1, kind=kind, source=CID, amount=777,
                skill_key='skchr_swllow_2', **fields)


@pytest.mark.parametrize('marker,unknown', [
    (unresolved_marker(proof={'method': 'BasicSkill.OnCastFinish'},
                       skill_wait_for_end=False, skill_reason=1), False),
    (unresolved_marker(proof={'method': 'BasicSkill.OnCastFinish'},
                       skill_wait_for_end=True, skill_reason=1), True),
    (unresolved_marker(proof={'method': 'BasicSkill.OnCastFinish'},
                       skill_wait_for_end=0, skill_reason=1), True),
    (unresolved_marker(proof={'method': 'BasicSkill.OnCastFinish'},
                       skill_wait_for_end=False, skill_reason=None), True),
    (unresolved_marker(proof={'method': 'BasicSkill.OnCastFinish'},
                       skill_wait_for_end=False, skill_reason=1, skill_id=None) | {'skill_key': None}, True),
    (unresolved_marker('skill_start', proof={'method': 'Character.SwitchToSkillState'}), True),
    (unresolved_marker(proof={'method': 'Character.OnSkillFinish'}), True),
])
def test_missing_uid_cast_uses_canonical_ignore_or_uncertainty_for_every_plausible_actor(marker, unknown):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    records = [marker, *(hit(seq, entry) for seq, entry in enumerate(ACTORS, 2))]
    original = deepcopy(records)
    value = consume(meter, 6, records)
    assert value['status'] == 'observing'
    assert [p['observed_damage'] for p in value['players']] == [None, 50, 40, 50]
    assert records == original
    for side, player in meter._players.items():
        assert player.stream.sequence == 5 and not player.stream.closed
        assert player.stream.received[1] == marker
        for owner, entry in player.actors.items():
            lane = value['players'][side - 1]['statistics']['rows'][owner]['settled_damage']
            affected = entry.cid == CID and unknown
            assert lane['skill_split_complete'] is not affected
            assert lane['skill_damage_unverified_share'] == (1 if affected else 0)
            assert (owner in player.statistics.settlement_skill_unassigned) is affected
            assert (owner in player.statistics.skill_incomplete) is affected
        assert not player.statistics.skill_active
        assert not player.statistics.settlement_skill_active


def test_missing_uid_damage_never_uses_plausible_keys_as_monetary_ownership():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    missing = dict(hit(1, ACTORS[0]), amount=777, nominal_amount=777)
    missing.pop('source_character_uid')
    value = consume(meter, 6, [missing, *(hit(seq, entry) for seq, entry in enumerate(ACTORS, 2))])
    assert [p['observed_damage'] for p in value['players']] == [None, 50, 40, 50]
    assert sum(p['observed_damage'] or 0 for p in value['players']) == 140
    assert value['unknown_attribution'][0]['reason'] == 'source_uid_missing'
    assert all(p['damage'] is None for p in value['players'])


def test_caller_only_uncertain_keys_keep_foreign_cast_early_isolation_and_original_replay():
    adapter = setup()
    stream = adapter.event_stream
    original = dict(sequence=1, frame=1, game_time=1, source=SOURCE,
                    source_character_uid=FOREIGN_UID, kind='skill_end', amount=1,
                    skill_method='BasicSkill.OnCastFinish', skill_key='skchr_swllow_2',
                    skill_wait_for_end=True, skill_reason=1)
    before = deepcopy(adapter.statistics.result())
    stream.accept(None, (SOURCE,), session=adapter.session,
                  settlement_identity=('foreign', None, None), replay_record=original, **original)
    assert adapter.statistics.result() == before and stream.sequence == 1
    assert stream.received[1] == original
    stream.accept(None, (SOURCE, 'another-candidate'), session=adapter.session,
                  settlement_identity=('foreign', None, None), replay_record=original, **original)
    assert adapter.statistics.result() == before


def test_producer_payload_cannot_declare_consumer_uncertain_keys_by_keyword():
    adapter = setup()
    with pytest.raises(TypeError, match='positional-only'):
        adapter.event_stream.accept(session=adapter.session, sequence=1, frame=1, game_time=1,
            source=SOURCE, source_character_uid=LOCAL_UID, kind='skill_start', amount=1,
            unresolved_source_keys=(SOURCE,))
    assert adapter.event_stream.sequence == 0
    assert not adapter.statistics.skill_active

