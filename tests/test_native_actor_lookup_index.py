"""A bound membership index preserves the canonical lookup's rejection rules."""
from copy import deepcopy

import pytest

import autochess_context as context_module
from autochess_context import lookup_native_actor, native_actor_lookup
from test_multiplayer_native_context import context


def normalized():
    return context(0)


def test_index_returns_the_same_immutable_member_and_does_not_rescan(monkeypatch):
    current = normalized()
    member = current['memberships'][0]
    find = native_actor_lookup(current)
    expected = lookup_native_actor(current, cid=member['cid'], data_uid=member['data_uid'])
    monkeypatch.setattr(context_module, 'lookup_native_actor', lambda *_args, **_kwargs:
                        pytest.fail('a bound lookup must not repeat canonical scans'))
    for _ in range(100):
        actual = find(current, cid=member['cid'], data_uid=member['data_uid'])
        assert actual == expected
    with pytest.raises(TypeError):
        actual['player_uid'] = '999'
    with pytest.raises(TypeError):
        actual['context_identity']['round'] = 99


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown', 'account', 'actor_key'])
def test_index_preserves_rejected_members_and_cid_uid_precision(mutation):
    current = normalized()
    member = current['memberships'][0]
    if mutation == 'duplicate':
        current['memberships'].append(deepcopy(member))
    elif mutation == 'unknown':
        current['unknown_memberships'].append(dict(data_uid=member['data_uid']))
    elif mutation == 'account':
        member['player_uid'] = '0'
    else:
        member['actor_key'] = ['999', member['data_uid']]
    find = native_actor_lookup(current)
    assert find(current, cid=member['cid'], data_uid=member['data_uid']) is None
    assert find(current, cid='char_wrong', data_uid=member['data_uid']) is None
    assert find(current, cid=member['cid'], data_uid=True) is None


def test_other_context_objects_and_deepcopied_adapters_cannot_borrow_index():
    current = normalized()
    member = current['memberships'][0]
    find = native_actor_lookup(current)
    changed = deepcopy(current)
    changed['memberships'].clear()
    assert find(changed, cid=member['cid'], data_uid=member['data_uid']) is None
    copied = deepcopy(current)
    assert find(copied, cid=member['cid'], data_uid=member['data_uid']) == lookup_native_actor(
        copied, cid=member['cid'], data_uid=member['data_uid'])


@pytest.mark.parametrize('invalid', [None, {}, {'memberships': None}])
def test_invalid_context_does_not_gain_members(invalid):
    assert native_actor_lookup(invalid)(invalid, cid='char_test', data_uid=1) is None
