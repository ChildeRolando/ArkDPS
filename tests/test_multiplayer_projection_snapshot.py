"""One retained player DTO per accepted batch, with detached public snapshots."""
from copy import deepcopy

import pytest

from test_native_pc_autochess_acquisition import ScopedNativeBattle


def populated():
    battle = ScopedNativeBattle()
    battle.poll()
    battle.advance(2)
    battle.advance(2, [battle.scoped_hit(i, i, i * 100) for i in range(1, 5)], damage=90000)
    battle.poll()
    return battle


def amounts(multiplayer):
    return [player['observed_damage'] for player in multiplayer['players']]


def count_results(monkeypatch, battle):
    meter = battle.runtime.sessions.multiplayer
    original, calls = meter.result, []

    def counted():
        calls.append(None)
        return original()

    monkeypatch.setattr(meter, 'result', counted)
    return calls


def test_display_does_not_rebuild_retained_projection_and_public_values_are_detached(monkeypatch):
    battle = populated()
    sessions = battle.runtime.sessions
    retained = deepcopy(sessions.latest_sample['multiplayer'])
    calls = count_results(monkeypatch, battle)
    first = battle.runtime.display_sample()
    second = battle.runtime.display_sample()
    assert calls == []
    assert first['multiplayer'] == second['multiplayer'] == retained
    assert first['multiplayer'] is not second['multiplayer']
    player = first['multiplayer']['players'][2]
    player['observed_damage'] = -1
    player['actor_ledgers'][0]['ledger']['observed_damage'] = -2
    owner = player['actor_ledgers'][0]['owner_id']
    player['statistics']['rows'][owner]['settled_damage']['observed_damage'] = -3
    assert sessions.latest_sample['multiplayer'] == second['multiplayer'] == retained
    assert battle.runtime.display_sample()['multiplayer'] == retained
    assert sessions.multiplayer._players['910000003'].statistics.result()['rows'][owner][
        'settled_damage']['observed_damage'] == 300
    assert calls == [] and battle.commits == [0, 4]


def test_next_accepted_batch_constructs_one_projection_and_does_not_mutate_previous(monkeypatch):
    battle = populated()
    old = battle.runtime.display_sample()
    frozen = deepcopy(old)
    calls = count_results(monkeypatch, battle)
    battle.advance(3)
    battle.advance(3, [battle.scoped_hit(5, 3, 50)])
    battle.poll()
    assert len(calls) == 1
    current = battle.runtime.display_sample()
    assert len(calls) == 1
    assert amounts(current['multiplayer']) == [100, 200, 350, 400]
    assert current['multiplayer'] == battle.runtime.sessions.latest_sample['multiplayer']
    assert old == frozen and battle.commits == [0, 4, 5]


def test_gap_replaces_retained_ready_projection_without_changing_previous_snapshot(monkeypatch):
    battle = populated()
    old = battle.runtime.display_sample()
    frozen = deepcopy(old)
    battle.runtime.disconnected('fixture_connection_lost')
    calls = count_results(monkeypatch, battle)
    current = battle.runtime.display_sample()
    assert current['multiplayer']['status'] == 'gap'
    assert 'fixture_connection_lost' in current['multiplayer']['reasons']
    assert current['multiplayer'] == battle.runtime.sessions.multiplayer.result()
    assert len(calls) == 1  # Only the explicit independent control read above.
    assert old == frozen and amounts(current['multiplayer']) == [100, 200, 300, 400]


@pytest.mark.parametrize('ending', ['terminal', 'process_changed'])
def test_ending_publishes_the_latest_closed_projection(monkeypatch, ending):
    battle = populated()
    old = battle.runtime.display_sample()
    frozen = deepcopy(old)
    if ending == 'terminal':
        battle.advance(3)
        battle.advance(3, [battle.scoped_hit(5, 3, 50)], state=3)
        battle.poll()
        expected = [100, 200, 350, 400]
    else:
        battle.runtime.process_changed()
        expected = [100, 200, 300, 400]
    calls = count_results(monkeypatch, battle)
    current = battle.runtime.display_sample()
    assert current['multiplayer']['status'] == 'ended'
    assert amounts(current['multiplayer']) == expected
    assert current['multiplayer'] == battle.runtime.sessions.latest_sample['multiplayer']
    assert all(p['statistics']['ended'] for p in current['multiplayer']['players'])
    assert calls == [] and old == frozen


def test_new_native_scope_replaces_the_retained_projection_and_archive_stays_independent(monkeypatch):
    battle = populated()
    old = battle.runtime.display_sample()
    frozen = deepcopy(old)
    battle.native_phase, battle.autochess_epoch = 5, 2
    battle.advance(3)
    battle.poll()
    archive, = battle.runtime.sessions.pop_archives()
    archived = deepcopy(archive)
    calls = count_results(monkeypatch, battle)
    current = battle.runtime.display_sample()
    assert current['multiplayer']['scope_kind'] == 'assist'
    assert current['multiplayer']['scope'] != old['multiplayer']['scope']
    assert amounts(current['multiplayer']) == [None, None, None, None]
    assert archive['last_sample']['multiplayer']['status'] == 'ended'
    assert amounts(archive['last_sample']['multiplayer']) == [100, 200, 300, 400]
    current['multiplayer']['players'].clear()
    assert archive == archived and old == frozen
    assert len(battle.runtime.display_sample()['multiplayer']['players']) == 4
    assert calls == [] and battle.commits == [0, 4, 4]


@pytest.mark.parametrize('missing', [True, False])
def test_missing_or_failed_retained_projection_preserves_result_retry(monkeypatch, missing):
    battle = populated()
    latest = battle.runtime.sessions.latest_sample
    expected = deepcopy(latest['multiplayer'])
    if missing:
        latest.pop('multiplayer')
    else:
        latest['multiplayer'] = None
    calls = count_results(monkeypatch, battle)
    current = battle.runtime.display_sample()
    assert len(calls) == 1 and current['multiplayer'] == expected


def test_result_failure_during_retention_can_recover_on_display_without_losing_primary_ack(monkeypatch):
    battle = populated()
    meter = battle.runtime.sessions.multiplayer
    original, calls = meter.result, []

    def fail_once():
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError('projection transient fixture fault')
        return original()

    monkeypatch.setattr(meter, 'result', fail_once)
    battle.advance(3)
    battle.advance(3, [battle.scoped_hit(5, 3, 50)])
    battle.poll()
    assert battle.runtime.sessions.latest_sample['multiplayer'] is None
    current = battle.runtime.display_sample()
    assert len(calls) == 2
    assert current['multiplayer']['status'] == 'gap'
    assert amounts(current['multiplayer']) == [100, 200, 350, 400]
    assert current['event_status'] == 'recording' and battle.commits == [0, 4, 5]
