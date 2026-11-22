"""Full player DTOs own every mutable container, without a whole-DTO clone."""
from collections import deque
from copy import deepcopy
import json
from types import ModuleType

import pytest

from battle_history import BattleHistory, BattleRecord
from multiplayer_battle import MultiplayerBattleStatistics
from roster import RosterSnapshot
from test_battle_history import record
from test_multiplayer_battle import actor, sample as legacy_sample
from test_multiplayer_native_context import (CID, PLAYERS, QUALITY, ROOT, context,
                                            hit, member, stamp)


ENEMY = 'enemy_1007_slime'
READY = dict(QUALITY, enemy_entry_ready=True, enemy_state_ready=True,
             enemy_phase_ready=True)


def containers(value, *, objects=False):
    """Map recursively reachable mutable containers; immutable leaves may share."""
    found, seen = {}, set()

    def visit(item, path):
        key = id(item)
        if key in seen:
            return
        seen.add(key)
        if isinstance(item, (dict, list, set, deque, bytearray)):
            found[key] = path
        if isinstance(item, dict):
            for name, child in item.items():
                visit(name, path + '.<key>')
                visit(child, path + '[' + repr(name) + ']')
        elif isinstance(item, (list, tuple, set, frozenset, deque)):
            for index, child in enumerate(item):
                visit(child, path + f'[{index}]')
        elif (objects and not isinstance(item, (type, ModuleType))
              and not callable(item) and hasattr(item, '__dict__')):
            visit(vars(item), path + '.__dict__')

    visit(value, '$')
    return found


def assert_disjoint(left, right, *, right_objects=False):
    a, b = containers(left), containers(right, objects=right_objects)
    shared = a.keys() & b.keys()
    assert not shared, [(a[key], b[key]) for key in shared]


class RichBattle:
    def __init__(self, *, native=True, session='round-owner:1'):
        self.meter = MultiplayerBattleStatistics()
        self.native, self.session = native, session
        self.phase, self.round, self.root = 4, 4, ROOT

    def feed(self, when, events=(), *, sequence=None):
        if sequence is None:
            sequence = max((e['sequence'] for e in events), default=self.meter._sequence or 0)
        if self.native:
            value = dict(session=self.session, native_session='native-controller:1',
                frame=round(when * 30), game_time=when, global_total=0, global_heal=0, rows=[],
                native_counter_scope='events_only', autochess_context=context(when,
                    phase=self.phase, native_round=self.round, root=self.root,
                    members=[member(i) for i in range(1, 5)] + [member(4, uid=499,
                        cid='token_unresolved', is_token=True)]),
                battle_metadata={'stage_id': 'ACT3AUTOCHESS/owned-projection'})
            roster = RosterSnapshot(value['frame'], local_player_side=0)
        else:
            value = legacy_sample(when, self.session)
            roster = RosterSnapshot(value['frame'], tuple(actor(i) for i in range(1, 5)),
                                    local_player_side=3)
        return self.meter.consume(value, roster, sequence, dict(READY, events=list(events)),
                                  mark=READY, round_id=self.session)

    def event(self, sequence, kind, when, *, target='500', **fields):
        result = dict(sequence=sequence, kind=kind, frame=round(when * 30), game_time=when,
                      source=None, amount=0, target=ENEMY, target_instance=target)
        result.update(fields)
        if self.native:
            result['native_autochess_scope'] = stamp(phase=self.phase, native_round=self.round,
                                                    root=self.root, when=when)
        return result

    def damage(self, sequence, index, when, amount, *, target='500'):
        value = hit(sequence, index, when, amount, target_instance=target, target_uid=target,
                    event_scope=stamp(phase=self.phase, native_round=self.round,
                                      root=self.root, when=when))
        if not self.native:
            value.pop('native_autochess_scope')
        return value

    def populate(self):
        self.feed(0)
        events = [self.event(1, 'enemy_enter', 1, hp_after=1000,
                            enemy_entry_attributes=dict(max_hp=1000, atk=120, **{'def': 30},
                                                        res=20, move_speed=1.1, attack_speed=100)),
                  self.event(2, 'enemy_hp', 1.1, hp_before=1000, hp_after=900, enemy_max_hp=1000)]
        events += [self.damage(i + 2, i, 1.1 + i / 10, i * 100) for i in range(1, 5)]
        events += [self.event(7, 'enemy_fake_death', 2),
                   self.event(8, 'enemy_reborn', 2.1, amount=1000, hp_before=0, hp_after=1000,
                              phase_total_hp=2000, phase_count=2, phase_from_birth=True),
                   self.event(9, 'enemy_hp', 2.2, hp_before=1000, hp_after=900, enemy_max_hp=1500),
                   self.event(10, 'enemy_finish', 3, hp_after=0, finish_reason=2)]
        missing = self.damage(11, 3, 4, 999)
        missing.pop('source_character_uid')
        value = self.feed(5, events + [missing])
        assert value['status'] == 'observing'
        assert [p['observed_damage'] for p in value['players']] == [100, 200, 300, 400]
        assert value['unknown_attribution']
        for player in value['players']:
            stats = player['statistics']
            if self.native:
                assert not {'enemy_state', 'enemy_entry', 'enemy_phases'} & stats.keys()
            else:
                assert len(stats['enemy_state']['events']) == 6
                assert stats['enemy_entry']['events'][0]['enemy_entry_attributes']['atk'] == 120
                assert stats['enemy_phases']['instances']['500']['phase_total_hp'] == 2000
            assert stats['enemy_damage']['by_uid']['500']['amount'] == player['observed_damage']
        return value


@pytest.mark.parametrize('native', [False, True])
def test_fresh_projection_graph_never_borrows_core_mutable_containers(native):
    battle = RichBattle(native=native)
    battle.populate()
    value = battle.meter._result(include_previous=True)
    assert_disjoint(value, battle.meter, right_objects=True)
    public = battle.meter.result()
    assert public == value
    assert_disjoint(value, public)
    assert_disjoint(public, battle.meter, right_objects=True)
    for index, player in enumerate(public['players']):
        for other in public['players'][index + 1:]:
            assert_disjoint(player, other)
        assert_disjoint(player['actor_ledgers'], player['statistics'])


@pytest.mark.parametrize('native', [False, True])
def test_public_nested_mutation_cannot_change_other_players_core_or_another_snapshot(native):
    battle = RichBattle(native=native)
    value = battle.populate()
    frozen = deepcopy(value)
    independent = battle.meter.result()
    player = value['players'][0]
    stats = player['statistics']
    if not native:
        stats['enemy_entry']['events'][0]['enemy_entry_attributes']['atk'] = -1
        stats['enemy_state']['events'][0]['enemy_entry_attributes']['max_hp'] = -2
        stats['enemy_state']['events'].pop()
        stats['enemy_phases']['instances']['500']['phase_total_hp'] = -3
    stats['enemy_damage']['by_uid']['500']['amount'] = -4
    owner = player['actor_ledgers'][0]['owner_id']
    stats['rows'][owner]['settled_damage']['effective_damage_by_type']['physical'] = -5
    player['actor_ledgers'][0]['ledger']['observed_damage'] = -6
    value['unknown_attribution'][0]['events'] = 999
    if native:
        value['context_identity']['scope_key'] = 'changed'
        value['unknown_memberships'][0]['reasons'].append('changed')
    assert value['players'][1:] == frozen['players'][1:]
    assert independent == frozen == battle.meter.result()


def test_next_batch_does_not_revise_a_public_enemy_timeline_or_damage_snapshot():
    battle = RichBattle(native=False)
    old = battle.populate()
    frozen = deepcopy(old)
    new = battle.feed(6, [battle.event(12, 'enemy_enter', 6, target='600', hp_after=200,
                                    enemy_entry_attributes={'max_hp': 200, 'atk': 15}),
                          battle.damage(13, 1, 6, 25, target='600')])
    assert new['players'][0]['observed_damage'] == 125
    assert len(new['players'][0]['statistics']['enemy_entry']['events']) == 2
    assert old == frozen
    assert_disjoint(old, new)
    assert_disjoint(new, battle.meter, right_objects=True)


def test_new_scope_previous_summaries_and_old_public_details_have_independent_ownership():
    battle = RichBattle()
    old = battle.populate()
    frozen = deepcopy(old)
    battle.phase, battle.round, battle.root = 5, 5, ROOT + ':next'
    baseline = battle.feed(6, sequence=11)
    assert baseline['scope'] != old['scope'] and baseline['scope_kind'] == 'assist'
    assert all(p['observed_damage'] is None for p in baseline['players'])
    current = battle.feed(7, [battle.event(12, 'enemy_enter', 7, target='700', hp_after=100,
                                         enemy_entry_attributes={'max_hp': 100, 'atk': 5}),
                              battle.damage(13, 3, 7, 25, target='700')])
    expected = deepcopy(current)
    assert len(current['previous_rounds']) == 1
    assert current['players'][2]['observed_damage'] == 25
    assert old == frozen
    assert_disjoint(old, current)
    assert_disjoint(current, battle.meter, right_objects=True)
    current['previous_rounds'][0]['players'][0]['reasons'].append('mutated past scope')
    current['players'][2]['actor_ledgers'][0]['ledger']['effective_damage_by_type']['physical'] = -1
    assert battle.meter.result() == expected and old == frozen


@pytest.mark.parametrize('native', [False, True])
def test_full_rich_projection_survives_history_without_sharing_with_public_results(tmp_path, native):
    data = record().to_dict()
    battle = RichBattle(native=native, session=data['battle_id'])
    battle.populate()
    final = battle.meter.finish(5, complete=True)
    original = deepcopy(final)
    serialized = json.loads(json.dumps(original, ensure_ascii=False, allow_nan=False))
    data['last_sample']['multiplayer'] = final
    saved = BattleRecord.from_dict(data)
    history = BattleHistory(tmp_path)
    assert history.save(saved).written
    loaded, = history.get(saved.battle_id).records
    assert loaded.to_dict() == saved.to_dict()
    value = loaded.last_sample['multiplayer']
    assert value == serialized
    assert_disjoint(value, final)
    assert_disjoint(value, battle.meter, right_objects=True)
    if not native:
        final['players'][0]['statistics']['enemy_entry']['events'][0]['enemy_entry_attributes']['atk'] = -1
        final['players'][1]['statistics']['enemy_state']['events'].clear()
    final['players'][0]['statistics']['enemy_damage']['by_uid']['500']['amount'] = -2
    final['players'][2]['actor_ledgers'][0]['ledger']['observed_damage'] = -1
    assert loaded.last_sample['multiplayer'] == serialized
    assert battle.meter.result() == original
    value['players'][3]['statistics']['enemy_damage']['by_uid']['500']['amount'] = 999
    assert loaded.last_sample['multiplayer'] == serialized
