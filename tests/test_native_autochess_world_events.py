"""One scoped world reducer beside four UID-owned player damage cores.

Only the raw memory/observer inputs are synthetic. NativePcCollector, admission,
Statistics, event replay, ACK and the enemy telemetry reducer are production code.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from battle_history import BattleRecord
from collection_session import CollectionSession
from enemy_telemetry import EnemyTelemetry, NativeEnemyObservations
from test_native_autochess_leading_zero_uid import leading_zero_battle


EID, UID = 'enemy_1007_slime', '500'
WORLD_QUALITY = dict(enemy_entry_ready=True, enemy_entry_unknown=False,
                     enemy_state_ready=True, enemy_state_unknown=False,
                     enemy_phase_ready=True, enemy_phase_unknown=False)


def world_battle(tmp_path):
    battle = leading_zero_battle(tmp_path)
    battle.world_quality = dict(WORLD_QUALITY)
    for method in ('mark', 'read_through', 'read_baseline'):
        original = getattr(battle.observer, method)

        def with_quality(*args, _original=original):
            return dict(_original(*args), **battle.world_quality)

        setattr(battle.observer, method, with_quality)
    return battle


def world_event(battle, sequence, kind='enemy_enter', *, uid=UID, **changes):
    record = battle.hit(sequence, 3, 0)
    for key in ('source_character_uid', 'nominal_amount', 'damage_type',
                'shield_absorbed', 'settlement_complete', 'killed', 'target_uid'):
        record.pop(key, None)
    record.update(source=None, kind=kind, amount=0, target=EID,
                  target_instance=uid)
    if kind == 'enemy_enter':
        record.update(hp_after=100, enemy_entry_attributes={'max_hp': 100, 'atk': 10})
    record.update(changes)
    return record


def damage(battle, sequence, index=3, amount=20, **changes):
    record = battle.hit(sequence, index, amount)
    record.update(target=EID, target_uid=UID, target_instance='0x90000')
    record.update(changes)
    return record


def published(battle):
    battle.poll()
    return battle.runtime.display_sample()


def test_native_world_is_reduced_once_without_player_world_copies(tmp_path):
    battle = world_battle(tmp_path)
    published(battle)
    battle.advance(1)
    events = [world_event(battle, 1), damage(battle, 2),
              world_event(battle, 3, 'enemy_hp', hp_before=100, hp_after=80, enemy_max_hp=100)]
    original = deepcopy(events)
    battle.advance(1, events)
    sample = published(battle)
    stats = sample['statistics']
    assert [e['kind'] for e in stats['enemy_state']['events']] == ['enemy_enter', 'enemy_hp']
    assert stats['enemy_entry']['events'][0]['enemy_entry_attributes']['max_hp'] == 100
    assert stats['enemy_damage'] == {'complete': True, 'by_uid': {UID: {'eid': EID, 'amount': 20}}}
    assert stats['rows'] == {} and not stats['totals_complete']
    assert [p['observed_damage'] for p in sample['multiplayer']['players']] == [None, None, 20, None]
    for player in sample['multiplayer']['players']:
        assert 'enemy_state' not in player['statistics']
        assert 'enemy_entry' not in player['statistics']
        assert 'enemy_phases' not in player['statistics']
    assert battle.commits == [0, 3] and events == original
    battle.advance(2)
    replayed = published(battle)
    assert replayed['statistics']['enemy_state']['events'] == stats['enemy_state']['events']
    assert replayed['statistics']['enemy_damage'] == stats['enemy_damage']
    assert battle.commits == [0, 3, 3]


def test_committed_world_reaches_enemy_hp_entry_and_damage_without_native_counter(tmp_path):
    battle = world_battle(tmp_path)
    first = published(battle)
    telemetry = EnemyTelemetry(None)
    seed = NativeEnemyObservations().observe({'enemies_status': 'available', 'enemies': {
        'scope': 'unit_manager_enemies_membership', 'collection_count': 1,
        'entities': [dict(instance_uid=500, enemy_key=EID, inited=True, borned=True,
                          is_disappeared=False, native_object_present=True,
                          hp=100, hp_status='available')]}}, frame=0, game_time=0, catalog={})
    inputs = []

    def read_enemies(*, frame, game_time, our_damage, event_scope, **events):
        inputs.append(our_damage)
        assert event_scope == battle.runtime.sessions.adapter._world_last_context['identity']
        return telemetry.sample_native_snapshot(seed, frame=frame, game_time=game_time, **events)

    session = CollectionSession(None, None, collector=SimpleNamespace(
        enemy_frame_bound=True, read_enemies=read_enemies))
    session._attach_enemy(first)
    battle.advance(1)
    battle.advance(1, [world_event(battle, 1), damage(battle, 2),
        world_event(battle, 3, 'enemy_hp', hp_before=100, hp_after=80, enemy_max_hp=100)])
    sample = published(battle)
    session._attach_enemy(sample)
    enemy = sample['enemy']
    assert inputs == [None, None]  # No CID native counter or fabricated empty-team zero.
    assert enemy['enemy_state_complete'] is True
    assert enemy['totals']['entered_total_hp'] == 100
    assert enemy['rows'][0]['hp'] == 80
    assert enemy['last_point']['our_effective_damage'] == 20
    assert enemy['last_point']['our_damage_complete'] is True


@pytest.mark.parametrize('kind', ['enemy_enter', 'enemy_hp', 'damage_settlement'])
def test_unknown_event_scope_preserves_receipt_but_loses_world_completeness(tmp_path, kind):
    battle = world_battle(tmp_path)
    published(battle)
    battle.advance(1)
    event = damage(battle, 1) if kind == 'damage_settlement' else world_event(battle, 1, kind, hp_after=80)
    event['native_autochess_scope']['status'] = 'unknown'
    battle.advance(1, [event])
    sample = published(battle)
    stats = sample['statistics']
    family = 'enemy_damage' if kind == 'damage_settlement' else 'enemy_state'
    assert stats[family]['complete'] is False
    if kind == 'enemy_enter':
        assert stats['enemy_entry']['complete'] is False
    assert stats['enemy_state']['events'] == []
    assert stats['enemy_damage']['by_uid'] == {}
    assert battle.commits == [0, 1]


def test_player_projection_failure_does_not_drop_shared_world_facts(tmp_path, monkeypatch):
    battle = world_battle(tmp_path)
    published(battle)

    def failed(*args, **kwargs):
        raise ValueError('owned projection failed')

    monkeypatch.setattr(battle.runtime.sessions.multiplayer, 'consume', failed)
    battle.advance(1)
    battle.advance(1, [world_event(battle, 1), damage(battle, 2)])
    sample = published(battle)
    assert len(sample['statistics']['enemy_entry']['events']) == 1
    assert sample['statistics']['enemy_damage']['by_uid'][UID]['amount'] == 20
    assert sample['event_status'] == 'recording' and battle.commits == [0, 2]


@pytest.mark.parametrize('phase', [4, 5, 6])
def test_world_damage_uses_all_verified_accounts_not_only_the_local_side(tmp_path, phase):
    battle = world_battle(tmp_path)
    battle.probe.phase = phase
    published(battle)
    battle.advance(1)
    battle.advance(1, [world_event(battle, 1),
        *(damage(battle, index + 1, index, index * 10) for index in range(1, 5))])
    sample = published(battle)
    assert sample['statistics']['enemy_damage']['by_uid'][UID]['amount'] == 100
    assert [p['observed_damage'] for p in sample['multiplayer']['players']] == [10, 20, 30, 40]
    assert sample['statistics']['rows'] == {}  # Not a fifth actor/native account.


@pytest.mark.parametrize('changes', [dict(source_character_uid=None), dict(source_character_uid=999),
                                  dict(settlement_complete=False)])
def test_unverified_damage_keeps_independent_hp_but_never_certifies_zero(tmp_path, changes):
    battle = world_battle(tmp_path)
    published(battle)
    battle.advance(1)
    battle.advance(1, [world_event(battle, 1), damage(battle, 2, 1, 5),
                       damage(battle, 3, 3, 20, **changes)])
    sample = published(battle)
    value = sample['statistics']['enemy_damage']
    assert value['complete'] is False
    assert value['by_uid'][UID]['amount'] == (25 if changes.get('settlement_complete') is False else 5)
    assert sample['statistics']['rows'] == {}


def test_world_scope_switch_and_old_tail_do_not_mix_reused_enemy_uids(tmp_path):
    battle = world_battle(tmp_path)
    published(battle)
    battle.advance(1)
    battle.advance(1, [world_event(battle, 1), damage(battle, 2)])
    previous = published(battle)
    old_scope = battle.scope()
    battle.advance(2, phase=5, epoch=2, native_round=5)
    baseline = published(battle)
    assert baseline['statistics']['enemy_state']['events'] == []
    assert baseline['statistics']['enemy_damage']['by_uid'] == {}
    battle.advance(3)
    stale = world_event(battle, 3)
    stale['native_autochess_scope'].update(scope_key=old_scope, phase=4, round=4)
    battle.advance(3, [stale, world_event(battle, 4), damage(battle, 5, 2, 7)])
    current = published(battle)
    assert len(current['statistics']['enemy_entry']['events']) == 1
    assert current['statistics']['enemy_damage'] == {'complete': True, 'by_uid': {UID: {'eid': EID, 'amount': 7}}}
    assert previous['statistics']['enemy_damage']['by_uid'][UID]['amount'] == 20
    archive, = battle.runtime.sessions.pop_archives()
    saved = BattleRecord.from_archive(archive)
    assert saved.statistics['enemy_damage']['by_uid'][UID]['amount'] == 20


def test_terminal_end_keeps_combat_world_tail_and_history(tmp_path):
    battle = world_battle(tmp_path)
    published(battle)
    scope = battle.scope()
    battle.advance(1)
    battle.advance(1, [world_event(battle, 1), damage(battle, 2)])
    published(battle)
    battle.advance(2, state=3, phase=8, epoch=2)
    tail = world_event(battle, 3, 'enemy_finish', hp_after=0, finish_reason=2)
    tail['native_autochess_scope'].update(scope_key=scope, phase=4, round=4)
    battle.advance(2, [tail])
    terminal = published(battle)
    assert terminal['statistics']['ended']
    assert terminal['autochess_context']['identity']['phase'] == 8
    assert terminal['enemy_world_scope'] == dict(scope_key=scope, phase=4, round=4)
    enemy_scopes = []

    def read_enemies(*, event_scope, **_):
        enemy_scopes.append(event_scope)
        return {'status': 'ready'}

    session = CollectionSession(None, None, collector=SimpleNamespace(
        enemy_frame_bound=True, read_enemies=read_enemies))
    session._attach_enemy(terminal)
    assert enemy_scopes == [dict(scope_key=scope, phase=4, round=4)]
    assert terminal['statistics']['enemy_state']['events'][-1]['kind'] == 'enemy_finish'
    assert terminal['statistics']['enemy_state']['complete']
    battle.active = False
    battle.poll()
    archive, = battle.runtime.sessions.pop_archives()
    saved = BattleRecord.from_archive(archive)
    assert saved.statistics['enemy_damage']['by_uid'][UID]['amount'] == 20
    assert len(saved.statistics['enemy_state']['events']) == 2


def test_lost_world_quality_is_sticky_without_erasing_observed_facts(tmp_path):
    battle = world_battle(tmp_path)
    published(battle)
    battle.advance(1)
    battle.advance(1, [world_event(battle, 1), damage(battle, 2)])
    published(battle)
    battle.world_quality.update(enemy_state_unknown=True, enemy_damage_unknown=True)
    battle.advance(2)
    gap = published(battle)
    assert not gap['statistics']['enemy_state']['complete']
    assert not gap['statistics']['enemy_damage']['complete']
    battle.world_quality.update(enemy_state_unknown=False, enemy_damage_unknown=False)
    battle.advance(3)
    later = published(battle)
    assert not later['statistics']['enemy_state']['complete']
    assert not later['statistics']['enemy_damage']['complete']
    assert later['statistics']['enemy_damage']['by_uid'][UID]['amount'] == 20
    assert len(later['statistics']['enemy_entry']['events']) == 1


def test_sourceless_elemental_world_hp_uses_the_existing_target_bar(tmp_path):
    battle = world_battle(tmp_path)
    published(battle)
    battle.advance(1)
    bar = damage(battle, 2, 3, 10)
    for key in ('target', 'nominal_amount', 'damage_type', 'shield_absorbed', 'settlement_complete', 'killed'):
        bar.pop(key, None)
    bar['kind'] = 'elemental_bar'
    burst = damage(battle, 3, 3, 20, source=None, source_character_uid=None, damage_type=5)
    battle.advance(1, [world_event(battle, 1), bar, burst])
    sample = published(battle)
    assert sample['statistics']['enemy_damage'] == {'complete': True, 'by_uid': {UID: {'eid': EID, 'amount': 20}}}
    assert [p['observed_damage'] for p in sample['multiplayer']['players']] == [None, None, 20, None]


def test_unknown_scope_bar_cannot_leave_old_world_owner_for_a_later_burst(tmp_path):
    battle = world_battle(tmp_path)
    published(battle)
    battle.advance(1)
    bar = damage(battle, 1, 3, 10)
    for key in ('target', 'nominal_amount', 'damage_type', 'shield_absorbed', 'settlement_complete', 'killed'):
        bar.pop(key, None)
    bar['kind'] = 'elemental_bar'
    unknown = deepcopy(bar)
    unknown['sequence'] = 2
    unknown['native_autochess_scope']['status'] = 'unknown'
    burst = damage(battle, 3, 3, 20, source=None, source_character_uid=None, damage_type=5)
    battle.advance(1, [bar, unknown, burst])
    sample = published(battle)
    assert sample['statistics']['enemy_damage'] == {'complete': False, 'by_uid': {}}
    assert all(p['observed_damage'] is None for p in sample['multiplayer']['players'])

