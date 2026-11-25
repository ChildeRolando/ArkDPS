"""Unverified healing cannot certify a current player's event-only HPS."""
import pytest

from test_multiplayer_native_context import (
    CID, MultiplayerBattleStatistics, consume, context, member, player, stamp,
)


def heal(sequence, when, amount, *, index=3, scope=None, missing_uid=False):
    event = dict(sequence=sequence, frame=round(when*30), game_time=when,
        source=CID, source_character_uid=index*100+1, kind='healing', amount=amount,
        proof={'method': 'Entity.ApplyModifier'},
        native_autochess_scope=stamp(when=when) if scope is None else scope)
    if missing_uid:
        del event['source_character_uid']
    return event


def actor(result, index=3):
    return next(iter(player(result, index)['statistics']['rows'].values()))


def test_unknown_heal_scope_only_downgrades_its_proved_player():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    result = consume(meter, 1, [heal(1, .25, 10),
        heal(2, .5, 10000, scope=dict(status='unknown')),
        heal(3, .75, 20, index=2), heal(4, 1, 5)])
    local, peer = actor(result), actor(result, 2)
    assert local['observed_healing_total'] == 15
    assert not local['observed_healing_complete']
    assert local['observed_recent_healing'] is None
    assert peer['observed_healing_total'] == 20
    assert peer['observed_healing_complete']
    assert peer['observed_recent_healing'] == 20
    assert all(not core.stream.closed and core.stream.sequence == 4 for core in meter._players.values())
    future = actor(consume(meter, 6, [heal(5, 6, 10)]))
    assert future['observed_healing_total'] == 25
    assert not future['observed_healing_complete']
    assert future['observed_recent_healing'] == 2
    assert future['observed_recent_healing_window'] == (1, 6)
    assert future['observed_recent_healing_complete']


@pytest.mark.parametrize('other_scope', [False, True])
def test_missing_actor_heal_scope_does_not_credit_unknown_or_foreign_amount(other_scope):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    scope = stamp(phase=5, when=.5) if other_scope else dict(status='unknown')
    result = consume(meter, 1, [heal(1, .25, 10),
        heal(2, .5, 10000, scope=scope, missing_uid=True),
        heal(3, 1, 20, index=2)])
    assert actor(result)['observed_healing_total'] == 10
    assert actor(result, 2)['observed_healing_total'] == 20
    for index in (2, 3):
        assert actor(result, index)['observed_healing_complete'] is other_scope
        assert (actor(result, index)['observed_recent_healing'] is not None) is other_scope


def test_same_scope_heal_with_unmatched_source_cannot_certify_other_healers():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    unmatched = dict(heal(2, .5, 10000), source='char_unmapped')
    result = consume(meter, 1, [heal(1, .25, 10), unmatched, heal(3, 1, 20, index=2)])
    for index, total in ((3, 10), (2, 20)):
        observed = actor(result, index)
        assert observed['observed_healing_total'] == total
        assert not observed['observed_healing_complete']
        assert observed['observed_recent_healing'] is None


@pytest.mark.parametrize('mode', ['local', 'all'])
def test_scope_unknown_heal_cannot_use_new_owner_after_historical_uid_conflict(mode):
    meter = MultiplayerBattleStatistics()
    native = lambda t, index: dict(context(t, members=[member(index, uid=301)]), collection_mode=mode)
    consume(meter, 0, native=native(0, 3))
    consume(meter, 1, [heal(1, .5, 10)], native=native(1, 3))
    event = heal(2, 1.5, 10000, scope=dict(status='unknown'))
    result = consume(meter, 2, [event], native=native(2, 2))
    observed = actor(result, 3)
    assert observed['observed_healing_total'] == 10
    assert not observed['observed_healing_complete']
    assert observed['observed_recent_healing'] is None
