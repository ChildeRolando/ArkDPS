"""Optional account reduction leaves identity, world receipts and archives intact."""
from copy import deepcopy
import json

import pytest

import multiplayer_battle as module
from autochess_context import native_local_player
from battle_history import BattleHistory, BattleRecord, HistoryError
from test_autochess_context import normalize, payload
from test_battle_history import record
from test_multiplayer_native_context import (CID, PLAYERS, ROOT, consume, context,
                                             hit, member, player, stamp)
from test_native_autochess_world_events import damage, world_battle, world_event


def native(when, mode='local', *, self_unknown=False, **changes):
    value = context(when, **changes)
    value['collection_mode'] = mode
    if self_unknown:
        value.update(self_player_index=0, self_player_uid=None)
    return value


def populated(*, mode='local', self_unknown=False, phase=4):
    meter = module.MultiplayerBattleStatistics()
    consume(meter, 0, native=native(0, mode, self_unknown=self_unknown, phase=phase))
    events = [hit(i, i, i, i * 100, event_scope=stamp(phase=phase, collection_mode=mode))
              for i in range(1, 5)]
    result = consume(meter, 5, events,
        native=native(5, mode, self_unknown=self_unknown, phase=phase))
    return meter, result


def assert_uncollected(account):
    assert account['player_uid'] is not None
    assert account['statistics_status'] == 'not_collected'
    assert account['status'] == 'unknown' and account['coverage_complete'] is False
    assert account['statistics'] is None and account['actor_ledgers'] == []
    assert all(account[key] is None for key in ('damage', 'dps', 'recent_dps',
        'observed_damage', 'observed_dps', 'observed_recent_dps', 'observed_window',
        'observed_recent_window'))
    assert '未启用该玩家统计' in account['reasons']


@pytest.mark.parametrize('mode', ['all', 'local'])
def test_optional_mode_is_normalized_only_when_declared(mode):
    source = payload(collection_mode=mode)
    before = deepcopy(source)
    normalized = normalize(source)
    assert normalized['collection_mode'] == mode and normalized['status'] == 'available'
    assert source == before
    assert 'collection_mode' not in normalize(payload())


@pytest.mark.parametrize('mode', [None, True, False, 0, 1, '', 'LOCAL', 'local ', [], {}])
def test_invalid_declared_mode_cannot_create_a_usable_identity(mode):
    value = normalize(payload(collection_mode=mode))
    assert value['status'] == 'unknown' and value['identity'] is None
    assert value['memberships'] == [] and 'invalid_collection_mode' in value['reasons']


@pytest.mark.parametrize('phase', [4, 5, 6])
def test_local_creates_only_one_real_reducer_and_preserves_all_account_specs(monkeypatch, phase):
    constructed = []
    original = module.Statistics

    def statistics(*args, **kwargs):
        constructed.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'Statistics', statistics)
    local, value = populated(phase=phase)
    assert len(constructed) == len(local._players) == 1
    assert set(local._players) == {PLAYERS[2]['uid']}
    assert [account['player_uid'] for account in value['players']] == [p['uid'] for p in PLAYERS]
    assert value['collection_mode'] == 'local' and player(value, 3)['observed_damage'] == 300
    for index in (1, 2, 4):
        account = player(value, index)
        assert_uncollected(account)
        assert account['slot'] == index and account['player_name'] == PLAYERS[index - 1]['nick_name']
        assert account['is_local'] is False
    assert value['unknown_attribution'] == []
    own = local._players[PLAYERS[2]['uid']]
    assert own.stream.sequence == 4 and list(own.stream.received) == [1, 2, 3, 4]
    assert list(player(value, 3)['statistics']['rows']) == [f'{PLAYERS[2]["uid"]}:{CID}#301']
    _, all_value = populated(mode='all', phase=phase)
    assert player(value, 3) == player(all_value, 3)
    assert value['scope'] == all_value['scope']  # Policy is not a fabricated native identity.


def test_missing_self_never_allocates_or_borrows_another_account():
    meter, value = populated(self_unknown=True)
    assert meter._players == {} and value['self_player_uid'] is None
    assert value['available'] and value['scope_kind'] == 'defense'
    for account in value['players']:
        assert_uncollected(account)
        assert account['is_local'] is None
    assert native_local_player(dict(native_counter_scope='events_only', multiplayer=value)) is None
    frozen = meter.finish(5, complete=True)
    assert frozen['status'] == 'ended' and frozen['scope'] == value['scope']


def test_local_tokens_and_discarded_foreign_skill_proofs_do_not_allocate_foreign_cores():
    meter = module.MultiplayerBattleStatistics()
    members = [member(3), member(1),
        member(3, uid=399, cid='token_local', is_token=True, host_key=CID, host_uid=301),
        member(1, uid=199, cid='token_foreign', is_token=True, host_key=CID, host_uid=101)]
    discarded = dict(sequence=0, source=CID, source_character_uid=101,
                     native_autochess_scope=stamp(when=0, collection_mode='local'))
    consume(meter, 0, native=native(0, members=members),
            quality=dict(baseline_discarded_skill_events=[discarded]))
    value = consume(meter, 3, [hit(1, 1, 1, 900, uid=199, cid='token_foreign'),
        hit(2, 3, 2, 20, uid=399, cid='token_local')], native=native(3, members=members))
    assert player(value, 3)['observed_damage'] == 20
    assert len(player(value, 3)['actor_ledgers']) == len(meter._players) == 1
    assert player(value, 3)['actor_ledgers'][0]['actor_uid'] == 301
    assert_uncollected(player(value, 1))
    assert value['unknown_attribution'] == []


def test_local_short_identity_read_keeps_the_bucket_and_receipts_without_assuming_ownership():
    meter, before = populated()
    bucket = meter._players[PLAYERS[2]['uid']]
    unknown = native(6)
    unknown.update(status='unknown', verified_same_frame=False, memberships=[])
    value = consume(meter, 6, [hit(5, 3, 6, 999)], native=unknown)
    assert value['available'] is False and value['scope_kind'] == 'unknown'
    assert player(value, 3)['observed_damage'] == 300
    recovered = consume(meter, 7, [hit(6, 3, 7, 20)], native=native(7))
    assert recovered['scope'] == before['scope'] and player(recovered, 3)['observed_damage'] == 320
    assert player(recovered, 3)['coverage_complete'] is False
    assert meter._players[PLAYERS[2]['uid']] is bucket and bucket.stream.sequence == 6
    assert len(meter._players) == 1


def test_explicit_all_keeps_legacy_all_account_results_exact_except_the_declared_field():
    legacy = module.MultiplayerBattleStatistics()
    consume(legacy, 0)
    legacy_value = consume(legacy, 5, [hit(i, i, i, i * 100) for i in range(1, 5)])
    explicit, explicit_value = populated(mode='all')
    # Extra raw transport stamps cannot alter the complete statistics projection.
    explicit_value.pop('collection_mode')
    assert explicit_value == legacy_value
    assert len(explicit._players) == 4


def test_foreign_elemental_bar_still_revokes_the_local_bar_witness():
    outputs = []
    for mode in ('all', 'local'):
        meter = module.MultiplayerBattleStatistics()
        consume(meter, 0, native=native(0, mode))
        events = []
        for seq, index in ((1, 3), (2, 1)):
            events.append(dict(sequence=seq, frame=seq * 30, game_time=seq, source=CID,
                source_character_uid=100 * index + 1, kind='elemental_bar', amount=100,
                target_uid='500', target_instance='target-500',
                native_autochess_scope=stamp(when=seq, collection_mode=mode)))
        event = hit(3, 3, 3, source=None, damage_type=5,
                    event_scope=stamp(collection_mode=mode))
        event.pop('source_character_uid')
        value = consume(meter, 4, events + [event], native=native(4, mode))
        outputs.append(player(value, 3))
        assert player(value, 3)['observed_damage'] is None
    assert outputs[0] == outputs[1]


def test_verified_foreign_bar_restores_scope_after_unknown_bar_without_a_foreign_reducer():
    outputs = []
    for mode in ('all', 'local'):
        meter = module.MultiplayerBattleStatistics()
        consume(meter, 0, native=native(0, mode))
        events = []
        for seq, index, verified in ((1, 3, True), (2, 1, False), (3, 1, True)):
            events.append(dict(sequence=seq, frame=seq * 30, game_time=seq, source=CID,
                source_character_uid=100 * index + 1, kind='elemental_bar', amount=100,
                target_uid='500', target_instance='target-500', native_autochess_scope=stamp(
                    when=seq, collection_mode=mode, verified_same_frame=verified)))
        event = hit(4, 3, 4, source=None, damage_type=5,
                    event_scope=stamp(collection_mode=mode))
        event.pop('source_character_uid')
        events += [event, hit(5, 3, 5, 20, event_scope=stamp(collection_mode=mode))]
        value = consume(meter, 6, events, native=native(6, mode))
        outputs.append(player(value, 3))
        assert value['reasons'] == []
        assert player(value, 3)['observed_damage'] == 20
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize('bad_mode', ['all', 'bogus', None, True])
def test_explicit_same_scope_event_mode_conflict_is_receipted_without_credit(bad_mode):
    meter = module.MultiplayerBattleStatistics()
    consume(meter, 0, native=native(0))
    bad = hit(1, 3, 1, 999, event_scope=stamp(collection_mode=bad_mode))
    value = consume(meter, 3, [bad, hit(2, 3, 2, 20)], native=native(3))
    assert player(value, 3)['observed_damage'] == 20
    assert player(value, 3)['coverage_complete'] is False
    assert value['unknown_attribution'][0]['reason'] == 'event_scope_mismatch'
    assert meter._players[PLAYERS[2]['uid']].stream.received[1] == bad


def test_foreign_scope_mode_does_not_reclassify_a_foreign_receipt_as_current_uncertainty():
    meter, before = populated()
    bad = hit(5, 3, 6, 999,
        event_scope=stamp(root=ROOT + ':retired', phase=5, collection_mode='all'))
    value = consume(meter, 6, [bad], native=native(6, phase=8, root=ROOT + ':end'),
                    terminal=True, state=3)
    assert player(value, 3)['observed_damage'] == player(before, 3)['observed_damage']
    assert value['scope_foreign_events'] == 1 and value['unknown_attribution'] == []


@pytest.mark.parametrize('change', [dict(collection_mode='all'), dict(self_player_uid=PLAYERS[0]['uid'])])
def test_same_native_scope_cannot_silently_retarget_or_reopen_a_local_reducer(change):
    meter, before = populated()
    current = native(6)
    current.update(change)
    value = consume(meter, 6, [hit(5, 3, 6, 999)], native=current)
    assert value['status'] == 'gap' and value['scope'] == before['scope']
    assert player(value, 3)['observed_damage'] == 300
    assert value['previous_rounds'] == [] and set(meter._players) == {PLAYERS[2]['uid']}


def local_record(*, self_unknown=False, transition=False):
    data = record().to_dict()
    meter = module.MultiplayerBattleStatistics()
    battle = data['battle_id']
    consume(meter, 0, session=battle, native=native(0, self_unknown=self_unknown))
    consume(meter, 5, [hit(i, i, i, i * 100) for i in range(1, 5)],
            session=battle, native=native(5, self_unknown=self_unknown))
    if transition:
        consume(meter, 6, sequence=4, session=battle, native=native(6, 'all', phase=5))
        consume(meter, 8, [hit(5, 1, 7, 50, event_scope=stamp(phase=5))],
                session=battle, native=native(8, 'all', phase=5))
    phase, mode = (5, 'all') if transition else (4, 'local')
    consume(meter, 12, sequence=5 if transition else 4, session=battle,
            native=native(12, mode, phase=phase, self_unknown=self_unknown))
    data['last_sample']['multiplayer'] = meter.finish(12, complete=True)
    return data


@pytest.mark.parametrize('self_unknown,transition', [(False, False), (True, False), (False, True)])
def test_local_known_account_placeholders_survive_real_history_save_load(tmp_path, self_unknown, transition):
    data = local_record(self_unknown=self_unknown, transition=transition)
    original = BattleRecord.from_dict(data)
    history = BattleHistory(tmp_path)
    assert history.save(original).written
    loaded, = history.get(original.battle_id).records
    assert loaded.to_dict() == json.loads(json.dumps(original.to_dict()))
    value = loaded.last_sample['multiplayer']
    assert [p['player_uid'] for p in value['players']] == [p['uid'] for p in PLAYERS]
    if transition:
        prior, = value['previous_rounds']
        assert prior['collection_mode'] == 'local' and prior['scope'] != value['scope']
        assert player(prior, 3)['observed_damage'] == 300
        assert player(value, 1)['observed_damage'] == 50
        assert len([p for p in value['players'] if p['statistics'] is not None]) == 4
    elif not self_unknown:
        assert player(value, 3)['observed_damage'] == 300
        for index in (1, 2, 4):
            assert_uncollected(player(value, index))


@pytest.mark.parametrize('change', [
    lambda p: p.pop('collection_mode'),
    lambda p: p.update(collection_mode='all'),
    lambda p: p.update(collection_mode='invalid'),
    lambda p: p['players'][0].pop('statistics_status'),
    lambda p: p['players'][0].update(observed_damage=0),
    lambda p: p['players'][0].update(statistics={'rows': {}}),
    lambda p: p['players'][0].update(actor_ledgers=[{}]),
    lambda p: p['players'][2].update(statistics_status='not_collected'),
])
def test_history_does_not_let_disabled_slots_bypass_identity_or_coverage(change):
    data = local_record()
    change(data['last_sample']['multiplayer'])
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)


def declare_mode(battle, mode):
    # Freeze only the fixture's host-generated UUID so cross-mode world results
    # can be compared in full, including their original session identifiers.
    battle.runtime.feed.id = battle.runtime.sessions.feed_id = 'local-mode-parity'
    for name in ('mark', 'read_through', 'read_baseline'):
        original = getattr(battle.observer, name)

        def declared(*args, _original=original):
            packet = _original(*args)
            packet['native_autochess_context']['collection_mode'] = mode
            return packet

        setattr(battle.observer, name, declared)


@pytest.mark.parametrize('mode', ['all', 'local'])
def test_real_native_prepare_preserves_the_committed_local_prefix(tmp_path, mode):
    battle = world_battle(tmp_path)
    declare_mode(battle, mode)
    assert battle.poll().kind == 'sample'
    battle.advance(1)
    battle.advance(1, [damage(battle, 1)])
    assert battle.poll().kind == 'sample'
    before = native_local_player(battle.runtime.display_sample())
    battle.advance(2, phase=3)
    assert battle.poll().kind == 'preparing'
    prepared = native_local_player(battle.runtime.display_sample())
    assert prepared['observed_damage'] == before['observed_damage'] == 20
    assert prepared['player_uid'] == before['player_uid']
    assert set(prepared['statistics']['rows']) == set(before['statistics']['rows'])
    assert prepared['statistics']['ended'] is True and prepared['observed_recent_dps'] is None
    assert len(battle.runtime.sessions.multiplayer._players) == (4 if mode == 'all' else 1)


def test_real_native_collector_world_local_and_ack_match_all_through_end8(tmp_path):
    results = []
    for mode in ('all', 'local'):
        directory = tmp_path / mode
        directory.mkdir()
        battle = world_battle(directory)
        declare_mode(battle, mode)
        assert battle.poll().kind == 'sample'
        battle.advance(1)
        events = [world_event(battle, 1), damage(battle, 2), damage(battle, 3, index=1, amount=35),
                  world_event(battle, 4, 'enemy_hp', hp_before=100, hp_after=45, enemy_max_hp=100)]
        battle.advance(1, events)
        assert battle.poll().kind == 'sample'
        live = battle.runtime.display_sample()
        assert live['statistics']['rows'] == {} and live['native_rows'][0]['damage'] == 90000
        assert len(battle.runtime.sessions.multiplayer._players) == (4 if mode == 'all' else 1)
        own = native_local_player(live)
        assert own['observed_damage'] == 20
        battle.advance(3, phase=8, state=3)
        assert battle.poll().kind == 'terminal'
        battle.active = False
        assert battle.poll().kind == 'end'
        archive, = battle.runtime.sessions.pop_archives()
        saved = BattleRecord.from_archive(archive)
        history = BattleHistory(directory / 'history')
        assert history.save(saved).written
        loaded, = history.get(saved.battle_id).records
        final = loaded.last_sample['multiplayer']
        assert final['scope_kind'] == 'defense' and final['status'] == 'ended'
        assert final['players'][2]['observed_damage'] == 20
        results.append((live['statistics'], own, battle.commits, final['players'][2]))
    assert results[0] == results[1]
