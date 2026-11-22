"""Native AutoChess identities never borrow player identity from battle side.

Business witnesses: ignored round-watch-20261122-204540-308181/samples.jsonl
lines 130..178 (BATTLE) and 262 (HELP_BATTLE). Scope roots and account IDs below
are synthetic; field relationships and the help-round side change are retained.
"""
import json

import pytest

from autochess_context import lookup_native_actor, native_autochess_scope_id, normalize_native_autochess


SCOPE = 'controller:host:manager:center:scope:7'


def member(cid='char_133_bluep', data_uid=3145911, player_index=3,
           player_side=3, **changes):
    return dict(cid=cid, data_uid=data_uid, player_index=player_index,
                player_side=player_side, is_token=False, is_predefined=False,
                host_key=None, host_uid=None, **changes)


def payload(**changes):
    result = dict(protocol_version=1, status='available', verified_same_frame=True,
                  phase=4, round=4, self_player_index=3, scope_key=SCOPE,
                  frame=2318, game_time=77.2666,
                  players=[dict(index=i, uid=str(910000000 + i), nick_name=f'玩家{i}',
                                nick_number=f'{i:04d}') for i in range(1, 5)],
                  memberships=[member()])
    result.update(changes)
    return result


def normalize(value, **changes):
    expected = dict(expected_frame=2318, expected_game_time=77.2666,
                    expected_scope_key=SCOPE)
    expected.update(changes)
    return normalize_native_autochess(value, **expected)


def test_battle_and_help_keep_player_index_separate_from_side_and_data_uid():
    battle = normalize(payload())
    help_round = normalize(payload(phase=5, memberships=[member(
        data_uid=1048780, player_side=1)]))
    assert battle['status'] == help_round['status'] == 'available'
    for context, uid, side in ((battle, 3145911, 3), (help_round, 1048780, 1)):
        actor = lookup_native_actor(context, cid='char_133_bluep', data_uid=uid)
        assert (actor['player_index'], actor['player_side']) == (3, side)
        assert actor['player_uid'] == context['self_player_uid'] == '910000003'
        assert actor['actor_key'] == ('910000003', uid)
    assert battle['identity'] != help_round['identity']
    assert battle['identity'] == dict(scope_key=SCOPE, phase=4, round=4)


def test_same_cid_multiple_players_and_instances_are_separate_without_uid_prefix_guess():
    context = normalize(payload(memberships=[member(data_uid=42),
        member(data_uid=43), member(data_uid=44, player_index=1, player_side=3)]))
    assert context['coverage_complete'] is True
    assert [lookup_native_actor(context, cid='char_133_bluep', data_uid=uid)['player_uid']
            for uid in (42, 43, 44)] == ['910000003', '910000003', '910000001']
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911) is None


def test_empty_verified_memberships_and_unresolved_self_do_not_invent_actor_or_account():
    context = normalize(payload(self_player_index=0, memberships=[]))
    assert context['status'] == 'available'
    assert context['self_player_uid'] is None
    assert context['memberships'] == []
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911) is None
    fewer = normalize(payload(players=payload()['players'][:2]))
    assert fewer['self_player_uid'] is None
    assert fewer['status'] == 'partial'
    assert fewer['unknown_memberships'][0]['reason'] == 'membership_player_unresolved'


@pytest.mark.parametrize('changes,reason', [
    ({'protocol_version': True}, 'unsupported_protocol'),
    ({'protocol_version': 2}, 'unsupported_protocol'),
    ({'status': 'partial'}, 'native_context_unavailable'),
    ({'verified_same_frame': False}, 'unverified_same_frame'),
    ({'frame': 2319}, 'frame_mismatch'),
    ({'frame': True}, 'invalid_frame'),
    ({'game_time': 77.2667}, 'game_time_mismatch'),
    ({'game_time': float('nan')}, 'invalid_game_time'),
    ({'game_time': float('inf')}, 'invalid_game_time'),
    ({'game_time': 10 ** 1000}, 'invalid_game_time'),
    ({'game_time': True}, 'invalid_game_time'),
    ({'scope_key': SCOPE + ':other'}, 'scope_mismatch'),
    ({'scope_key': ' padded '}, 'invalid_scope_key'),
    ({'phase': True}, 'invalid_phase'),
    ({'phase': 999}, 'invalid_phase'),
    ({'round': -1}, 'invalid_round'),
    ({'round': False}, 'invalid_round'),
    ({'self_player_index': 5}, 'invalid_self_player_index'),
    ({'pages': []}, 'paged_context'),
    ({'sampling': {'coverage_complete': False}}, 'paged_context'),
])
def test_root_proof_failure_removes_all_memberships(changes, reason):
    context = normalize(payload(**changes))
    assert context['status'] == 'unknown'
    assert reason in context['reasons']
    assert context['memberships'] == []
    assert context['coverage_complete'] is False
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911) is None


@pytest.mark.parametrize('field,value', [('frame', 2317), ('game_time', 76.0),
    ('scope_key', SCOPE + ':old'), ('verified_same_frame', False)])
def test_cross_frame_or_scope_membership_page_rejects_entire_context(field, value):
    context = normalize(payload(memberships=[member(), dict(member(data_uid=99), **{field: value})]))
    assert context['status'] == 'unknown'
    assert context['reason'] == 'membership_proof_mismatch'
    assert context['memberships'] == []


@pytest.mark.parametrize('change', [dict(index=True), dict(index=5), dict(uid=123),
    dict(uid=''), dict(uid='０１２３'), dict(uid=' 123'), dict(uid='0'),
    dict(nick_name=15), dict(nick_name='bad\x00name'), dict(nick_number=False)])
def test_invalid_player_identity_table_cannot_create_lookup(change):
    value = payload()
    value['players'][0].update(change)
    context = normalize(value)
    assert context['status'] == 'unknown'
    assert context['memberships'] == []


@pytest.mark.parametrize('field', ['index', 'uid'])
def test_player_table_requires_unique_index_to_uid_bijection(field):
    value = payload()
    value['players'][1][field] = value['players'][0][field]
    context = normalize(value)
    assert context['status'] == 'unknown'
    assert context['reason'] == 'player_identity_conflict'


@pytest.mark.parametrize('change,reason', [
    ({'data_uid': None, 'card_uid': 99}, 'invalid_data_uid'),
    ({'data_uid': True}, 'invalid_data_uid'),
    ({'data_uid': 0}, 'invalid_data_uid'),
    ({'data_uid': 1 << 32}, 'invalid_data_uid'),
    ({'cid': ''}, 'invalid_cid'),
    ({'cid': 'char_foo\n'}, 'invalid_cid'),
    ({'player_index': True}, 'invalid_player_index'),
    ({'player_side': True}, 'invalid_player_side'),
    ({'is_token': 0}, 'invalid_token_flag'),
    ({'is_predefined': None}, 'invalid_predefined_flag'),
    ({'host_uid': 123}, 'unexpected_host_identity'),
    ({'status': 'unavailable'}, 'membership_unavailable'),
])
def test_single_bad_membership_is_quarantined_without_erasing_verified_players(change, reason):
    bad = dict(member(data_uid=99), **change)
    context = normalize(payload(memberships=[member(), bad]))
    assert context['status'] == context['membership_status'] == 'partial'
    assert context['coverage_complete'] is False
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911) is not None
    assert context['unknown_memberships'][0]['row_index'] == 1
    assert reason in context['unknown_memberships'][0]['reasons']


@pytest.mark.parametrize('change', [dict(player_index=2), dict(cid='char_194_leto'),
                                  dict(player_side=1), {}])
def test_duplicate_or_conflicting_data_uid_quarantines_every_claim_to_that_uid(change):
    context = normalize(payload(memberships=[member(), dict(member(), **change), member(data_uid=42)]))
    assert context['status'] == 'partial'
    assert [row['row_index'] for row in context['unknown_memberships']] == [0, 1]
    assert all('data_uid_conflict' in row['reasons'] for row in context['unknown_memberships'])
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911) is None
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=42) is not None


def test_bad_token_or_foreign_host_only_quarantines_token():
    token = dict(member(cid='token_test', data_uid=99), is_token=True,
                 host_key='char_133_bluep', host_uid=3145911)
    context = normalize(payload(memberships=[member(), token]))
    assert context['status'] == 'available'
    assert lookup_native_actor(context, cid='token_test', data_uid=99)['host_uid'] == 3145911
    for change in (dict(host_uid=None), dict(player_index=2)):
        bad = normalize(payload(memberships=[member(), dict(token, **change)]))
        assert bad['status'] == 'partial'
        assert lookup_native_actor(bad, cid='char_133_bluep', data_uid=3145911) is not None
        assert lookup_native_actor(bad, cid='token_test', data_uid=99) is None


def test_normalized_dto_is_json_serializable_detached_and_lookup_is_read_only():
    original = payload()
    context = normalize(original)
    restored = json.loads(json.dumps(context, allow_nan=False))
    assert restored == context
    actor = lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911)
    with pytest.raises(TypeError):
        actor['player_uid'] = 'different'
    with pytest.raises(TypeError):
        actor['actor_key'][0] = 'different'
    original['memberships'][0]['cid'] = 'changed'
    original['players'][2]['uid'] = 'changed'
    assert context == restored
    assert lookup_native_actor(context, cid='changed', data_uid=3145911) is None


@pytest.mark.parametrize('bad', [None, [], 'context', 1])
def test_malformed_payload_is_unknown_without_raising(bad):
    assert normalize(bad)['status'] == 'unknown'


def test_lookup_cannot_resolve_from_cid_or_side_without_exact_native_data_uid():
    context = normalize(payload())
    for cid, uid in (('char_194_leto', 3145911), ('char_133_bluep', None),
                     ('char_133_bluep', True), ('char_133_bluep', '3145911')):
        assert lookup_native_actor(context, cid=cid, data_uid=uid) is None


def test_invalid_row_with_duplicate_uid_cannot_leave_the_other_claim_usable():
    bad = dict(member(), cid=None)
    context = normalize(payload(memberships=[member(), bad, member(data_uid=42)]))
    assert context['status'] == 'partial'
    assert len(context['unknown_memberships']) == 2
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911) is None
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=42) is not None


@pytest.mark.parametrize('change', [dict(players=[]), dict(players={}),
    dict(memberships={}), dict(memberships=[member()] * 257)])
def test_missing_or_unbounded_identity_containers_are_unknown(change):
    assert normalize(payload(**change))['status'] == 'unknown'


def test_only_verified_values_are_copied_from_native_membership_rows():
    original = payload(memberships=[dict(member(), card_uid=7, inst_id=9,
                                         raw_secret='not forwarded')])
    actor = normalize(original)['memberships'][0]
    assert actor['data_uid'] == 3145911
    assert not {'card_uid', 'inst_id', 'raw_secret'} & actor.keys()


@pytest.mark.parametrize('changes,reason', [
    (dict(expected_frame=True), 'invalid_frame'),
    (dict(expected_game_time=10 ** 1000), 'invalid_game_time'),
    (dict(expected_scope_key=''), 'invalid_scope_key')])
def test_invalid_owner_expectations_cannot_certify_native_context(changes, reason):
    context = normalize(payload(), **changes)
    assert context['status'] == 'unknown'
    assert reason in context['reasons']


def test_self_player_index_without_player_entry_does_not_fall_back_to_side():
    value = payload(self_player_index=4, players=payload()['players'][:3],
                    memberships=[member(player_side=4)])
    context = normalize(value)
    assert context['status'] == 'available'
    assert context['self_player_uid'] is None
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911)['player_uid'] == '910000003'


def test_native_unknown_claim_blocks_same_uid_without_discarding_verified_neighbors():
    value = payload(memberships=[member(), member(data_uid=42)],
        unknown_memberships=[dict(data_uid=3145911, reason='native role identity incomplete')])
    context = normalize(value)
    assert context['status'] == context['membership_status'] == 'partial'
    assert not context['coverage_complete']
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911) is None
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=42) is not None
    native, = [row for row in context['unknown_memberships'] if row.get('origin') == 'native_unknown']
    assert native['row_index'] == 0
    assert native['reason'] == 'native role identity incomplete'


@pytest.mark.parametrize('unknown', [None, {}, [None], [dict(data_uid='3145911', reason='missing')],
    [dict(data_uid=True, reason='missing')], [dict(reason='missing', row_index=1 << 30)],
    [dict(reason='\x00bad')], [dict(reason='missing')] * 257])
def test_invalid_native_unknown_claim_keeps_coverage_partial_without_guessing_uid(unknown):
    context = normalize(payload(unknown_memberships=unknown))
    assert context['status'] == 'partial'
    assert not context['coverage_complete']
    assert context['unknown_memberships']
    assert lookup_native_actor(context, cid='char_133_bluep', data_uid=3145911) is not None


def test_native_unknown_claim_copies_only_bounded_verified_diagnostics():
    context = normalize(payload(unknown_memberships=[dict(
        candidate_player_index=3, cid='char_133_bluep', reason='native read failed',
        raw_secret='never copied', card_uid=3145911, host_uid=-9)]))
    row, = context['unknown_memberships']
    assert row['candidate_player_index'] == 3
    assert row['cid'] == 'char_133_bluep'
    assert not {'raw_secret', 'card_uid', 'host_uid'} & row.keys()
    assert context['status'] == 'partial'


def test_scope_encoding_is_stable_and_depends_on_native_identity_and_real_account_table():
    identity = dict(scope_key=SCOPE, phase=4, round=4)
    table = [(1, '910000001'), (3, '910000003')]
    scope = native_autochess_scope_id(identity, table)
    assert scope == native_autochess_scope_id(identity, list(reversed(table)))
    assert scope != native_autochess_scope_id(dict(identity, phase=5), table)
    assert scope != native_autochess_scope_id(identity, [(1, '910000001'), (3, '920000003')])

