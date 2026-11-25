"""Exact body timing has its own coverage domain, independent of hit/skill quality."""
from copy import deepcopy

import pytest

from multiplayer_battle import MultiplayerBattleStatistics
from test_deployment_lifecycle import proof
from test_multiplayer_native_context import CID, consume, context, hit, member, player, stamp


META = dict(platform='native_pc', stage_id='Activities/AUTOCHESS/lifetime-quality')
MEMBERS = [member(3), member(3, uid=302, cid='char_peer'), member(4)]


def native(t, members=MEMBERS):
    return dict(context(t, members=members), collection_mode='all')


def body(seq, kind, when, *, cid=CID, uid=301, instance=10, scope=None):
    state = 'born' if kind == 'character_born' else 'finish'
    return dict(sequence=seq, frame=when * 30, game_time=when, source=cid,
        source_character_uid=uid, source_instance_uid=instance, kind=kind, amount=0,
        actor_lifecycle=proof(state, when, actor=uid, instance=instance),
        skill_reason=2 if state == 'finish' else None,
        native_autochess_scope=stamp(when=when, collection_mode='all') if scope is None else scope)


def established():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=native(0), battle_metadata=META)
    births = [body(1, 'character_born', 1),
              body(2, 'character_born', 1, cid='char_peer', uid=302, instance=20),
              body(3, 'character_born', 1, uid=401, instance=30)]
    consume(meter, 1, births, native=native(1), battle_metadata=META)
    event = hit(4, 3, 2, 100, cid='char_peer', uid=302, source_instance_uid=20,
                event_scope=stamp(when=2, collection_mode='all'))
    consume(meter, 2, [event], native=native(2), battle_metadata=META)
    return meter


def lifetimes(result, side=3):
    return {owner: row['lifecycle_presence_complete']
            for owner, row in player(result, side)['statistics']['rows'].items()}


@pytest.mark.parametrize('fault', ['localized_numeric', 'global_numeric', 'skill'])
def test_numeric_or_skill_quality_does_not_erase_proven_body_time(fault):
    meter = established()
    quality = dict(skill_unknown=True) if fault == 'skill' else dict(
        enemy_damage_unknown=True, damage_settlement_unknown=True,
        damage_settlement_source_quality_version=1,
        damage_settlement_unlocalized_unknown=fault == 'global_numeric',
        damage_settlement_source_issues=[dict(source=CID, source_character_uid=301,
            frame=60, game_time=2, native_autochess_scope=stamp(when=2, collection_mode='all'))])
    result = consume(meter, 3, native=native(3), quality=quality, battle_metadata=META)
    assert all(lifetimes(result).values())
    assert all(lifetimes(result, 4).values())
    good = player(result, 3)['statistics']['rows']['910000003:char_peer#302']
    assert good['lifecycle_presence_seconds'] == 2
    # The numerical gap remains exactly where its own quality rules put it.
    if fault != 'skill':
        assert any(not actor['ledger']['complete'] for actor in player(result, 3)['actor_ledgers'])
    assert not meter._closed


@pytest.mark.parametrize('kind', ['character_born', 'skill_end'])
def test_unknown_scope_body_callback_invalidates_only_its_known_actor(kind):
    meter = established()
    scope = dict(protocol_version=1, status='unknown', verified_same_frame=False,
                 frame=90, game_time=3, reason='owned rejection')
    result = consume(meter, 3, [body(5, kind, 3, scope=scope)], native=native(3), battle_metadata=META)
    rows = lifetimes(result)
    assert rows['910000003:' + CID + '#301'] is False
    assert rows['910000003:char_peer#302'] is True
    assert all(lifetimes(result, 4).values())


def test_uid_conflict_keeps_that_body_unknown_without_blocking_other_body():
    meter = established()
    conflicted = [*MEMBERS, member(2, uid=301)]
    result = consume(meter, 3, native=native(3, conflicted), battle_metadata=META)
    assert lifetimes(result)['910000003:' + CID + '#301'] is False
    assert lifetimes(result)['910000003:char_peer#302'] is True
    assert all(lifetimes(result, 4).values())


def test_unknown_body_uid_and_real_sequence_gap_keep_lifetimes_unknown():
    meter = established()
    scope = dict(protocol_version=1, status='unknown', verified_same_frame=False,
                 frame=90, game_time=3, reason='owned rejection')
    result = consume(meter, 3, [body(5, 'character_born', 3, uid=999, scope=scope)],
                     native=native(3), battle_metadata=META)
    assert not any(lifetimes(result).values())
    other = established()
    consume(other, 3, [], sequence=6, native=native(3), battle_metadata=META)
    assert other._closed
    assert not any(lifetimes(other.result()).values())


def test_unresolved_lifecycle_prefix_is_temporary_but_overflow_is_permanent():
    meter = established()
    pending = body(5, 'character_born', 3, uid=501, instance=50)
    result = consume(meter, 3, [pending], native=native(3), battle_metadata=META)
    # An exact, different data UID is not an unknown endpoint for these bodies.
    # Its own prefix stays pending until an account/roster can establish it.
    assert meter._pending_lifecycles and all(lifetimes(result).values())
    result = consume(meter, 4, native=native(4, [*MEMBERS, member(1, uid=501)]), battle_metadata=META)
    assert not meter._pending_lifecycles
    assert all(lifetimes(result).values())
    # Populate the real bounded pending queue, then deliver the next callback.
    meter._pending_lifecycles = [deepcopy(dict(pending, sequence=100 + i,
        source_character_uid=9000 + i, actor_lifecycle=proof('born', 3, actor=9000 + i, instance=50)))
        for i in range(1024)]
    result = consume(meter, 5, [body(6, 'character_born', 5, uid=99999, instance=90)],
                     native=native(5), battle_metadata=META)
    assert meter._lifecycle_unknown_all
    assert not any(lifetimes(result).values())


@pytest.mark.parametrize('uid', [None, 0, True])
def test_lifecycle_prefix_without_a_usable_uid_still_blocks_known_bodies(uid):
    meter = established()
    pending = body(5, 'character_born', 3, uid=999, instance=50)
    pending['source_character_uid'] = uid
    result = consume(meter, 3, [pending], native=native(3), battle_metadata=META)
    assert meter._pending_lifecycles
    assert not any(lifetimes(result).values())


def test_foreign_unbound_cleanup_endpoint_cannot_erase_known_deployed_body_time():
    meter = established()
    pending = body(5, 'skill_end', 3, uid=999, instance=50)
    pending['proof'] = {'method': 'BattleController.OnCharacterFinished'}
    result = consume(meter, 3, [pending], native=native(3), battle_metadata=META)
    assert meter._pending_lifecycles
    assert all(lifetimes(result).values())
    assert all(lifetimes(result, 4).values())
    known = player(result, 3)['statistics']['rows']['910000003:char_peer#302']
    assert known['lifecycle_presence_seconds'] == 2
    assert known['settled_damage']['effective_damage'] == 100
