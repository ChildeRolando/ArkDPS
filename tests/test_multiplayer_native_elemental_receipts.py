"""Native legacy callbacks keep receipts without consuming settled bar owners."""
from copy import deepcopy

import pytest

from test_multiplayer_native_context import (CID, MultiplayerBattleStatistics, consume,
    context, hit, player, stamp)
from test_multiplayer_battle import actor, consume as legacy_consume, elemental_bar


A = ('501', 'target-a')
B = ('502', 'target-b')


def native(when, *, mode='all', phase=4):
    return dict(context(when, phase=phase), collection_mode=mode)


def bar(sequence, index, when, target, *, phase=4):
    return dict(sequence=sequence, frame=when * 30, game_time=when, source=CID,
        source_character_uid=index * 100 + 1, kind='elemental_bar', amount=100,
        target_uid=target[0], target_instance=target[1],
        native_autochess_scope=stamp(phase=phase, when=when))


def settled(sequence, when, target, amount, *, phase=4):
    event = hit(sequence, 3, when, amount, source=None, damage_type=5,
        target_uid=target[0], target_instance=target[1], event_scope=stamp(phase=phase))
    event.pop('source_character_uid')
    return event


def legacy(sequence, when, target=A, *, target_uid=False, phase=4):
    event = dict(sequence=sequence, frame=when * 30, game_time=when, source=None,
        kind='elemental_burst', amount=900, damage_type=5, target='enemy_1007_slime',
        target_instance=target[1], hp_before=1000, hp_after=100, real_delta=-900,
        killed=False, native_autochess_scope=stamp(phase=phase, when=when))
    if target_uid:
        event['target_uid'] = target[0]
    return event


@pytest.mark.parametrize('mode', ['all', 'local'])
@pytest.mark.parametrize('phase', [4, 5])
@pytest.mark.parametrize('target_uid', [False, True], ids=['old_no_target_uid', 'with_target_uid'])
def test_native_legacy_burst_preserves_two_target_owners_and_never_adds_a_second_account(
        mode, phase, target_uid):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=native(0, mode=mode, phase=phase))
    records = [bar(1, 3, 1, A, phase=phase), bar(2, 1, 2, B, phase=phase)]
    consume(meter, 2, records, native=native(2, mode=mode, phase=phase))
    bars = {uid: deepcopy(p.statistics.settlement_bars) for uid, p in meter._players.items()}
    old = legacy(3, 3, target_uid=target_uid, phase=phase)
    original = deepcopy(old)
    consume(meter, 3, [old], native=native(3, mode=mode, phase=phase))
    assert {uid: p.statistics.settlement_bars for uid, p in meter._players.items()} == bars
    assert meter._scope_unknown_all_targets is False and not meter._scope_unknown_targets
    assert all(not p.statistics.burst for p in meter._players.values())
    records += [old, settled(4, 4, B, 70, phase=phase), settled(5, 5, A, 40, phase=phase)]
    before = deepcopy(records)
    result = consume(meter, 6, records, native=native(6, mode=mode, phase=phase))
    assert player(result, 3)['observed_damage'] == 40
    assert player(result, 1)['observed_damage'] == (70 if mode == 'all' else None)
    assert all(player(result, i)['observed_damage'] is None for i in (2, 4))
    assert result['unknown_attribution'] == []
    assert all(not p.statistics.burst for p in meter._players.values())
    assert all(p.stream.received[3] == old and p.stream.sequence == 5 for p in meter._players.values())
    assert meter._received[3] == old and old == original and records == before
    # Re-delivery of the same original global sequence never credits either
    # the legacy callback or the authoritative settled hit a second time.
    assert consume(meter, 6, records, native=native(6, mode=mode, phase=phase)) == result


def test_unknown_scope_legacy_without_target_uid_still_blocks_stale_owners_until_reconfirmed():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    consume(meter, 2, [bar(1, 3, 1, A), bar(2, 1, 2, B)])
    unknown = legacy(3, 3)
    unknown.pop('native_autochess_scope')
    original = deepcopy(unknown)
    result = consume(meter, 5, [unknown, settled(4, 4, B, 70), settled(5, 5, A, 40)])
    assert meter._scope_unknown_all_targets is True
    assert all(p['observed_damage'] is None for p in result['players'])
    result = consume(meter, 8, [bar(6, 3, 6, A), settled(7, 7, A, 10), settled(8, 8, B, 20)])
    assert player(result, 3)['observed_damage'] == 10
    assert player(result, 1)['observed_damage'] is None
    assert unknown == original and meter._received[3] == original


@pytest.mark.parametrize('mode', ['all', 'local'])
def test_legacy_duplicate_after_first_target_settled_does_not_erase_the_second_target(mode):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=native(0, mode=mode))
    consume(meter, 3, [bar(1, 3, 1, A), bar(2, 1, 2, B), settled(3, 3, A, 40)],
        native=native(3, mode=mode))
    duplicate = legacy(4, 4)
    result = consume(meter, 5, [duplicate, settled(5, 5, B, 70)], native=native(5, mode=mode))
    assert player(result, 3)['observed_damage'] == 40
    assert player(result, 1)['observed_damage'] == (70 if mode == 'all' else None)
    assert result['unknown_attribution'] == []
    assert all(not p.statistics.burst for p in meter._players.values())
    assert all(p.stream.received[4] == duplicate for p in meter._players.values())


@pytest.mark.parametrize('kind', ['elemental_bar', 'damage_settlement'])
def test_unknown_scope_real_fact_only_invalidates_its_verified_target(kind):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    consume(meter, 2, [bar(1, 3, 1, A), bar(2, 1, 2, B)])
    unknown = bar(3, 1, 3, B) if kind == 'elemental_bar' else settled(3, 3, B, 900)
    unknown.pop('native_autochess_scope')
    result = consume(meter, 5, [unknown, settled(4, 4, B, 70), settled(5, 5, A, 40)])
    assert player(result, 3)['observed_damage'] == 40
    assert player(result, 1)['observed_damage'] is None
    result = consume(meter, 7, [bar(6, 1, 6, B), settled(7, 7, B, 20)])
    assert player(result, 1)['observed_damage'] == 20
    assert player(result, 3)['observed_damage'] == 40


def test_retired_scope_legacy_receipt_does_not_invalidate_current_target_owners():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    consume(meter, 0, native=native(0, phase=5))
    records = [bar(1, 3, 1, A, phase=5), bar(2, 1, 2, B, phase=5), legacy(3, 3, phase=4),
        settled(4, 4, B, 70, phase=5), settled(5, 5, A, 40, phase=5)]
    result = consume(meter, 6, records, native=native(6, phase=5))
    assert player(result, 3)['observed_damage'] == 40
    assert player(result, 1)['observed_damage'] == 70
    assert result['scope_foreign_events'] == 1
    assert result['unknown_attribution'] == []


def test_schema_one_legacy_burst_account_and_same_sequence_replay_remain_unchanged():
    meter = MultiplayerBattleStatistics()
    entries = [actor(3), actor(1)]
    legacy_consume(meter, 0, entries)
    records = [elemental_bar(1, 3, 1, target_uid=A[0]), legacy(2, 2, ('501', '0x501'))]
    records[1].pop('native_autochess_scope')
    result = legacy_consume(meter, 3, entries, records)
    assert result['schema_version'] == 1
    burst, = meter._players[3].statistics.burst.values()
    assert burst['count'] == 1 and burst['actual'] == 900
    assert not meter._players[1].statistics.burst
    assert legacy_consume(meter, 3, entries, records) == result
