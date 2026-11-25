"""A native CharacterData form proof cannot substitute an actor or account."""

from copy import deepcopy

import pytest

from autochess_context import resolve_native_event_member
from desktop_bridge import _display_statistics, team_unassigned_hits
from test_multiplayer_local_mode import native
from test_multiplayer_native_context import (
    CID, MultiplayerBattleStatistics, consume, hit, member, player, stamp,
)
from test_native_autochess_world_events import EID, UID, damage, published, world_battle


ENTITY, FORM = 'char_002_amiya', 'char_1037_amiya3'
FORM_UID = 101


def attach_form(event, *, mode='local'):
    scope = event['native_autochess_scope']
    scope['collection_mode'] = mode
    event['source'] = ENTITY
    event['proof'] = {'native_autochess_character_form': dict(
        version=1, entity_cid=ENTITY, data_cid=FORM,
        data_uid=event['source_character_uid'], instance_uid=88,
        **{key: scope[key] for key in (
            'frame', 'game_time', 'scope_key', 'phase', 'round', 'collection_mode')},
    )}
    return event


def form_hit(seq=1, when=1):
    return attach_form(hit(seq, 1, when, 100))


def team_counter(value):
    statistics = _display_statistics(dict(native_counter_scope='events_only', multiplayer=value))
    return team_unassigned_hits(statistics, boss_scope=False)


@pytest.mark.parametrize('mode', ['local', 'all'])
def test_foreign_character_form_is_receipted_without_affecting_local_damage_or_unknown(mode):
    rows = [member(3), member(1, cid=FORM)]
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=native(0, mode, members=rows))
    local_event = hit(1, 3, 1, 20, event_scope=stamp(collection_mode=mode))
    foreign_event = attach_form(hit(2, 1, 1, 100), mode=mode)
    original = deepcopy(foreign_event)
    result = consume(meter, 1, [local_event, foreign_event], native=native(1, mode, members=rows))
    assert team_counter(result) == 0 and result['unknown_attribution'] == []
    assert player(result, 3)['observed_damage'] == 20
    assert player(result, 1)['observed_damage'] == (None if mode == 'local' else 100)
    assert meter._received[2] == foreign_event == original
    assert meter._sequence == 2
    replay = deepcopy(foreign_event)
    replay['proof']['native_autochess_character_form']['data_cid'] = 'char_other'
    conflicted = consume(meter, 2, [replay], native=native(2, mode, members=rows))
    assert conflicted['status'] == 'gap' and meter._received[2] == original
    assert player(conflicted, 3)['observed_damage'] == 20


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('entity_cid', 'char_other'), ('data_cid', ENTITY),
    ('data_cid', 'token_other'), ('data_cid', 'char_other'), ('data_uid', 301),
    ('instance_uid', False), ('instance_uid', 0), ('instance_uid', 1 << 32),
    ('frame', 31), ('game_time', 2), ('game_time', True), ('scope_key', 'other-scope'),
    ('phase', 5), ('round', 5), ('collection_mode', 'all'), ('player_index', 1),
])
def test_character_form_rejects_malformed_or_mismatched_producer_witness(field, value):
    context = native(1, members=[member(1, cid=FORM), member(3)])
    event = form_hit()
    event['proof']['native_autochess_character_form'][field] = value
    assert resolve_native_event_member(context, event) is None


@pytest.mark.parametrize('field', ['frame', 'game_time'])
def test_boolean_clock_in_raw_event_cannot_match_integer_zero_in_form_witness(field):
    context = native(0, members=[member(1, cid=FORM), member(3)])
    event = form_hit(when=0)
    event[field] = False
    assert resolve_native_event_member(context, event) is None


@pytest.mark.parametrize('fault', ['missing', 'stamp', 'event_kind', 'data_uid', 'roster',
                                   'predefined', 'token', 'unknown_membership'])
def test_character_form_needs_current_unambiguous_regular_actor_and_matching_scope(fault):
    form_member = member(1, cid=FORM)
    if fault == 'predefined':
        form_member['is_predefined'] = True
    if fault == 'token':
        form_member.update(is_token=True, host_key=CID, host_uid=199)
    rows = [member(3), member(1, uid=199)]
    prior = native(0, members=[*rows, member(1, cid=FORM)])
    if fault != 'roster':
        rows.append(form_member)
    context = native(1, members=rows)
    if fault == 'unknown_membership':
        context['unknown_memberships'] = [dict(data_uid=FORM_UID, reason='ambiguous input')]
    event = form_hit()
    if fault == 'missing':
        event['proof']['native_autochess_character_form'].pop('instance_uid')
    if fault == 'stamp':
        event['native_autochess_scope']['verified_same_frame'] = False
    if fault == 'event_kind':
        event['kind'] = 'skill_end'
    if fault == 'data_uid':
        event.pop('source_character_uid')
    assert resolve_native_event_member(context, event, prior) is None


def test_contradictory_form_proof_cannot_replace_an_exact_actor_match():
    context = native(1, members=[member(1, cid=ENTITY), member(3, cid=FORM)])
    event = form_hit()
    event['proof']['native_autochess_character_form']['data_uid'] = 301
    resolved = resolve_native_event_member(context, event)
    assert resolved['cid'] == ENTITY and resolved['player_index'] == 1 and resolved['data_uid'] == FORM_UID


@pytest.mark.parametrize('form_proof', [False, True])
def test_shape_without_proof_still_reports_unknown_and_sourceless_scope_requires_its_own_evidence(form_proof):
    rows = [member(3), member(1, cid=FORM)]
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=native(0, members=rows))
    events = [hit(1, 3, 1, 20, event_scope=stamp(collection_mode='local'))]
    for seq in range(2, 123):
        event = form_hit(seq)
        if not form_proof:
            event.pop('proof')
        events.append(event)
    for seq in range(123, 168):
        event = hit(seq, 3, 1, cid=None, damage_type=5, event_scope=stamp(collection_mode='local'))
        event.pop('source_character_uid')
        events.append(event)
    result = consume(meter, 1, events, native=native(1, members=rows))
    counts = {row['reason']: row['events'] for row in result['unknown_attribution']}
    assert team_counter(result) == (45 if form_proof else 166)
    assert counts == ({'elemental_owner_unverified': 45} if form_proof
                      else {'elemental_owner_unverified': 45, 'source_uid_unmapped': 121})
    assert player(result, 3)['observed_damage'] == 20


@pytest.mark.parametrize('form_proof', [False, True])
def test_foreign_character_form_bar_prevents_sourceless_burst_from_using_local_owner(form_proof):
    rows = [member(3), member(1, cid=FORM)]
    meter = MultiplayerBattleStatistics()
    consume(meter, 0, native=native(0, members=rows))

    def bar(seq, cid, uid):
        return dict(sequence=seq, frame=seq * 30, game_time=seq, source=cid,
                    source_character_uid=uid, kind='elemental_bar', amount=100,
                    target_uid='500', target_instance='target-500',
                    native_autochess_scope=stamp(when=seq, collection_mode='local'))

    foreign = bar(2, ENTITY, FORM_UID)
    if form_proof:
        attach_form(foreign)
    burst = hit(3, 3, 3, cid=None, damage_type=5, event_scope=stamp(collection_mode='local'))
    burst.pop('source_character_uid')
    local_hit = hit(4, 3, 4, 20, event_scope=stamp(collection_mode='local'))
    result = consume(meter, 4, [bar(1, CID, 301), foreign, burst, local_hit], native=native(4, members=rows))
    assert player(result, 3)['observed_damage'] == 20
    assert team_counter(result) == int(not form_proof)
    assert bool(result['unknown_attribution']) is not form_proof


def test_character_form_reaches_world_and_exact_player_ledgers_without_tampering_replay(tmp_path):
    battle = world_battle(tmp_path)

    def declare_form(original):
        def read(*args):
            packet = original(*args)
            context = packet['native_autochess_context']
            context['collection_mode'] = 'all'
            for row in context['memberships']:
                if row['data_uid'] == FORM_UID:
                    row['cid'] = FORM
            return packet
        return read

    for method in ('mark', 'read_through', 'read_baseline'):
        setattr(battle.observer, method, declare_form(getattr(battle.observer, method)))
    published(battle)
    battle.advance(1)
    event = attach_form(damage(battle, 1, index=1, amount=40), mode='all')
    raw = deepcopy(event)
    battle.advance(1, [event])
    sample = published(battle)
    local = next(p for p in sample['multiplayer']['players'] if p['is_local'])
    other = next(p for p in sample['multiplayer']['players'] if p['slot'] == 1)
    assert local['observed_damage'] is None and other['observed_damage'] == 40
    assert team_counter(sample['multiplayer']) == 0
    assert sample['statistics']['enemy_damage'] == {
        'complete': True, 'by_uid': {UID: {'eid': EID, 'amount': 40}},
    }
    assert event == raw and battle.commits == [0, 1]
