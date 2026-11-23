"""Token settlements need a same-scope host even after the token disappears."""
from copy import deepcopy

import pytest

from test_multiplayer_native_context import CID, MultiplayerBattleStatistics, consume, context, hit, member, player, stamp
from test_native_autochess_world_events import EID, UID, damage, published, world_battle


TOKEN = 'token_10024_ebnhlz_rcube'
TOKEN_UID = 399
TOKEN_MEMBER = member(3, uid=TOKEN_UID, cid=TOKEN, is_token=True, host_key=CID, host_uid=301)
TOKEN_HOST = {'host_key': CID, 'host_uid': 301, 'player_side': 3}


def token_hit(sequence=1, when=1, **changes):
    return hit(sequence, 3, when, 40, uid=TOKEN_UID, cid=TOKEN, **changes)


@pytest.mark.parametrize('inactive', [False, True])
def test_previous_verified_token_can_finish_its_last_observed_interval(inactive):
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=context(0, members=[member(3), TOKEN_MEMBER, member(1)]))
    current = context(2, members=[member(3), member(1)])
    if inactive:
        current['unknown_memberships'] = [
            {'data_uid': TOKEN_UID, 'cid': TOKEN, 'reasons': ['token lifecycle unavailable or inactive']}]
    event = token_hit()
    original = deepcopy(event)
    result = consume(meter, 2, [event], native=current)
    assert player(result, 3)['observed_damage'] == 40
    assert player(result, 3)['actor_ledgers'][0]['source_character_uid'] == 301
    assert player(result, 1)['observed_damage'] is None
    assert event == original == meter._received[1]
    expired = consume(meter, 4, [token_hit(2, 3)], native=context(4, members=[member(3), member(1)]))
    assert player(expired, 3)['observed_damage'] == 40
    assert expired['unknown_attribution'][0]['reason'] == 'source_uid_unmapped'


@pytest.mark.parametrize('wrong', ['unproved', 'host_key', 'host_uid', 'player_side',
                                  'current_conflict', 'previous_conflict', 'previous_cid', 'scope'])
def test_unknown_or_contradictory_token_does_not_inherit_a_host(wrong):
    meter = MultiplayerBattleStatistics()
    members = [member(3), member(1)]
    prior = list(members)
    fields = dict(TOKEN_HOST)
    if wrong == 'previous_conflict':
        prior.append(dict(TOKEN_MEMBER, host_uid=101, player_index=1, player_side=1))
    if wrong == 'previous_cid':
        prior.append(dict(TOKEN_MEMBER, cid='token_other'))
    consume(meter, 0, native=context(0, members=prior))
    for key, value in (('host_key', 'char_other'), ('host_uid', 101), ('player_side', 1)):
        if wrong == key:
            fields[key] = value
    mark = context(2, members=members)
    if wrong == 'current_conflict':
        mark['unknown_memberships'] = [{'data_uid': TOKEN_UID, 'cid': TOKEN,
                                        'reasons': ['conflicting role UID']}]
    event = token_hit()
    if wrong != 'unproved':
        event['proof'] = {'native_autochess_token_host': fields}
    if wrong == 'scope':
        event['native_autochess_scope'] = stamp(when=1, phase=5)
    result = consume(meter, 2, [event], native=mark)
    assert all(p['observed_damage'] is None for p in result['players'])
    assert result['unknown_attribution']


def test_a_phase_change_retires_previous_token_evidence():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=context(0, members=[member(3), TOKEN_MEMBER]))
    consume(meter, 0, native=context(0, native_round=5, members=[member(3)]))
    result = consume(meter, 2, [token_hit(event_scope=stamp(when=1, native_round=5))],
                     native=context(2, native_round=5, members=[member(3)]))
    assert player(result, 3)['observed_damage'] is None
    assert result['unknown_attribution'][0]['reason'] == 'source_uid_unmapped'


@pytest.mark.parametrize('witness', ['sample_then_absent', 'only_in_callback'])
def test_token_evidence_reaches_both_player_and_enemy_world_ledgers(tmp_path, witness):
    battle = world_battle(tmp_path)

    def with_token(original):
        def read(*args):
            packet = original(*args)
            mark = packet['native_autochess_context']
            if mark['frame'] == 0 and witness == 'sample_then_absent':
                mark['memberships'].append(dict(TOKEN_MEMBER))
            return packet
        return read

    for method in ('mark', 'read_through', 'read_baseline'):
        setattr(battle.observer, method, with_token(getattr(battle.observer, method)))
    published(battle)
    battle.advance(1)
    event = damage(battle, 1, amount=40, source=TOKEN, source_character_uid=TOKEN_UID)
    if witness == 'only_in_callback':
        event['proof'] = {'native_autochess_token_host': dict(TOKEN_HOST)}
    original = deepcopy(event)
    battle.advance(1, [event])
    sample = published(battle)
    own = next(p for p in sample['multiplayer']['players'] if p['is_local'])
    assert own['observed_damage'] == 40
    assert own['actor_ledgers'][0]['source_character_uid'] == 301
    assert sample['statistics']['enemy_damage'] == {
        'complete': True, 'by_uid': {UID: {'eid': EID, 'amount': 40}}}
    assert event == original and battle.commits == [0, 1]
    assert all(row['cid'] != TOKEN for row in sample['autochess_context']['memberships'])
