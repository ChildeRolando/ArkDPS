"""Owned current-generation late roster mapping must remain recoverable."""
from dataclasses import replace
import pytest
from battle_lifecycle import MappingRetry
from roster import CharacterEntry, RosterSnapshot
from snapshot_adapter import BattleStatisticsAdapter, MappingUnavailable
from test_battle_lifecycle import begin


def test_late_token_host_recovers_after_rejected_same_generation_roster():
    collector, runtime, _ = begin()
    host = collector.roster.entries[0]
    token = CharacterEntry('token_owned', 2, True, False, host.cid, host.unique_id, host.player_side)
    collector.advance(time=11., frame=11, damage=0.)
    collector.sample['rows'][0]['id'] = token.cid
    collector.sample['rows'][0].update(damage=25., physical=25.)
    collector.roster = replace(collector.roster, entries=(token,))
    with pytest.raises(MappingRetry):
        runtime.poll(collector)
    assert runtime.sessions.generation == 1
    collector.advance(time=12., frame=12, damage=25.)
    collector.roster = replace(collector.roster, entries=(host, token))
    assert runtime.poll(collector).kind == 'sample'
    assert runtime.sessions.adapter.owner_map.owners[token.cid] == host.cid
    assert token.cid not in runtime.sessions.adapter.owner_map.unknown_token_cids


def counter(cid, amount):
    return dict(id=cid, damage=amount, healing=0., physical=amount,
                arts=0., true=0., elemental=0.)


def update(adapter, frame, entries, rows):
    return adapter.update(dict(session='owned', frame=frame, game_time=float(frame),
                               state=2, rows=rows),
                          RosterSnapshot(frame, tuple(entries),
                                         frozenset(e.unique_id for e in entries), True))


def owned_mapping():
    adapter = BattleStatisticsAdapter(lifecycle_managed=True)
    host = CharacterEntry('host_a', 1, False, False, None, None, 0)
    token = CharacterEntry('token_owned', 2, True, False, host.cid, host.unique_id, 0)
    update(adapter, 0, [host], [counter(host.cid, 0)])
    return adapter, host, token


def test_rejected_missing_host_has_no_partial_mapping_or_clock_commit():
    from copy import deepcopy
    adapter, host, token = owned_mapping()
    before = deepcopy(adapter.owner_map)
    with pytest.raises(MappingUnavailable):
        update(adapter, 1, [token], [counter(token.cid, 25)])
    assert adapter.owner_map == before
    assert adapter.last_frame == 0 and adapter.last_roster_frame == 0
    assert adapter.unattributed_output == {}
    update(adapter, 2, [host, token], [counter(token.cid, 25)])
    assert adapter.statistics.owners[token.cid] == host.cid


def test_accepted_partial_roster_can_resolve_only_exact_token_host_witness():
    adapter, host, token = owned_mapping()
    update(adapter, 1, [token], [counter(host.cid, 10), counter(token.cid, 25)])
    assert token.cid in adapter.owner_map.unknown_token_cids
    update(adapter, 2, [host, token], [counter(host.cid, 15), counter(token.cid, 30)])
    assert token.cid not in adapter.owner_map.unknown_token_cids
    assert adapter.owner_map.owners[token.cid] == host.cid
    assert adapter.statistics.owners[token.cid] == host.cid


@pytest.mark.parametrize('fault', ['new_token_uid', 'wrong_host_uid', 'still_missing_host'])
def test_new_identity_cannot_explain_old_unresolved_cumulative_contribution(fault):
    adapter, host, token = owned_mapping()
    update(adapter, 1, [token], [counter(host.cid, 10), counter(token.cid, 25)])
    entries = [host, token]
    if fault == 'new_token_uid':
        entries = [host, replace(token, unique_id=3)]
    elif fault == 'wrong_host_uid':
        entries = [replace(host, unique_id=9), token]
    else:
        entries = [token]
    with pytest.raises(MappingUnavailable):
        update(adapter, 2, entries, [counter(token.cid, 30)])
    assert token.cid in adapter.owner_map.unknown_token_cids
    assert token.cid not in adapter.statistics.sources


def test_real_owner_conflict_stays_blocked_after_other_counter_accepts_frame():
    adapter, host, token = owned_mapping()
    other = CharacterEntry('host_b', 3, False, False, None, None, 0)
    update(adapter, 1, [host, token, other], [counter(host.cid, 10), counter(token.cid, 25)])
    changed = replace(token, host_key=other.cid, host_uid=other.unique_id)
    update(adapter, 2, [host, other, changed], [counter(host.cid, 15), counter(token.cid, 30)])
    assert token.cid in adapter.mapping_conflicts
    with pytest.raises(MappingUnavailable):
        update(adapter, 3, [host, token], [counter(token.cid, 35)])
    assert token.cid in adapter.mapping_conflicts


@pytest.mark.parametrize('fault', ['owner_conflict', 'ambiguous_owners'])
def test_rejected_counter_frame_cannot_erase_positive_ownership_contradiction(fault):
    adapter, host, token = owned_mapping()
    other = CharacterEntry('host_b', 3, False, False, None, None, 0)
    update(adapter, 1, [host, token, other], [counter(token.cid, 25)])
    changed = replace(token, host_key=other.cid, host_uid=other.unique_id)
    entries = [host, other, changed]
    if fault == 'ambiguous_owners':
        entries.append(token)
    with pytest.raises(MappingUnavailable):
        update(adapter, 2, entries, [counter(token.cid, 30)])
    assert adapter.last_frame == 1 and adapter.last_roster_frame == 1
    assert (token.cid in adapter.mapping_conflicts
            or token.cid in adapter.owner_map.ambiguous_cids)
    with pytest.raises(MappingUnavailable):
        update(adapter, 3, [host, token], [counter(token.cid, 35)])
    assert adapter.statistics.sources[token.cid]['values']['damage'] == 25
