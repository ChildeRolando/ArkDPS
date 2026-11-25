"""Known actor endpoints recover future skill classification, never old hits."""
from copy import deepcopy

import pytest

from test_multiplayer_native_context import (
    CID, PLAYERS, MultiplayerBattleStatistics, consume, hit, player, stamp,
)


def skill(seq, when, method, *, kind='skill_end', index=3, key='fixture_skill', unknown=False):
    event = dict(sequence=seq, frame=round(when * 30), game_time=when,
                 source=CID, source_character_uid=index * 100 + 1, kind=kind, amount=1,
                 skill_key=key, proof=dict(method=method),
                 native_autochess_scope=(dict(status='unknown', reason='scope cache unavailable')
                                         if unknown else stamp(when=when)))
    if method == 'BasicSkill.OnCastFinish':
        event.update(skill_wait_for_end=False, skill_reason=1)
    return event


def lane(value):
    return player(value, 3)['actor_ledgers'][0]['ledger']


@pytest.mark.parametrize('method', ['Character.OnSkillFinish', 'Character.SwitchOutFromSkillState'])
def test_verified_endpoint_recovers_future_after_known_actor_scope_loss(method):
    meter = MultiplayerBattleStatistics()
    consume(meter, 2)
    before = consume(meter, 4, [hit(1, 3, 3, 100),
        skill(2, 4, 'Character.OnSkillStart', kind='skill_start', unknown=True)])
    frozen = deepcopy(before)
    consume(meter, 5, [skill(3, 5, method)])
    after = lane(consume(meter, 6, [hit(4, 3, 6, 200)]))
    assert before == frozen
    assert lane(before)['skill_unclassified_hits'] == 1
    assert after['observed_damage'] == after['effective_damage'] == 300
    assert after['observed_hits'] == 2 and after['unverified_hits'] == 0
    assert after['skill_initial_unknown_hits'] == after['skill_unclassified_hits'] == 1
    assert after['observed_non_skill_damage'] == after['observed_non_skill_damage_effective'] == 200
    assert after['skill_split_complete'] is False  # The earlier 100 still has no skill proof.
    assert meter._players[PLAYERS[2]['uid']].statistics.skill_windows == {}


def test_verified_activation_also_recovers_actor_specific_scope_uncertainty():
    meter = MultiplayerBattleStatistics()
    consume(meter, 2)
    consume(meter, 3, [skill(1, 3, 'Character.OnSkillStart', kind='skill_start', unknown=True)])
    consume(meter, 4, [skill(2, 4, 'Character.SwitchToSkillState', kind='skill_start')])
    observed = lane(consume(meter, 5, [hit(3, 3, 5, 75)]))
    assert observed['observed_skill_damage'] == 75
    assert observed['skill_unclassified_hits'] == observed['skill_initial_unknown_hits'] == 0


def test_skill_timing_revision_keeps_intervening_scope_uncertainty_after_recovery():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    core = meter._players[PLAYERS[2]['uid']].statistics
    owner = next(iter(core.owners))
    before = core.skill_observation_revision(owner, 301)
    neighboring_actor = core.skill_observation_revision(owner, 302)
    assert core.observed_skill_interval(owner, 301, 0, 0) == 0

    result = consume(meter, 2, [
        skill(1, .5, 'Character.OnSkillStart', kind='skill_start', unknown=True),
        skill(2, 1.5, 'Character.OnSkillFinish'),
    ])
    after = core.skill_observation_revision(owner, 301)
    assert after != before
    assert core.skill_observation_revision(owner, 302) == neighboring_actor
    row = next(iter(player(result, 3)['statistics']['rows'].values()))
    assert row['skill_active_seconds'] is None
    assert row['skill_coverage_complete'] is False

    consume(meter, 3)
    assert core.skill_observation_revision(owner, 301) == after
    assert core.observed_skill_interval(owner, 301, 2, 3) == 0
    core.mark_skill_unknown()
    assert core.skill_observation_revision(owner, 302) != neighboring_actor
    assert core.observed_skill_interval(owner, 301, 2, 3) is None


@pytest.mark.parametrize('changes', [
    dict(method='Character.OnAfterSkill'),
    dict(method='BasicSkill.OnCastFinish'),
    dict(method='BattleController.OnCharacterFinished'),
    dict(method='Character.OnSkillFinish', unknown=True),
    dict(method='Character.OnSkillFinish', index=1),
    dict(method='Character.OnSkillFinish', key=None),
])
def test_unproved_cast_or_other_actor_end_does_not_heal_initial_state(changes):
    meter = MultiplayerBattleStatistics()
    consume(meter, 2)
    consume(meter, 3, [skill(1, 3, **changes)])
    observed = lane(consume(meter, 4, [hit(2, 3, 4, 100)]))
    assert observed['skill_initial_unknown_hits'] == observed['skill_unclassified_hits'] == 1
    assert observed['observed_non_skill_damage'] is None


def test_conflicting_active_skill_end_cannot_clear_scope_uncertainty():
    meter = MultiplayerBattleStatistics()
    consume(meter, 2)
    consume(meter, 3, [skill(1, 3, 'Character.SwitchToSkillState', kind='skill_start')])
    consume(meter, 4, [skill(2, 4, 'Character.OnSkillStart', kind='skill_start', unknown=True)])
    consume(meter, 5, [skill(3, 5, 'Character.OnSkillFinish', key='different_skill')])
    assert lane(consume(meter, 6, [hit(4, 3, 6, 100)]))['skill_unclassified_hits'] == 1
    consume(meter, 7, [skill(5, 7, 'Character.OnSkillFinish')])
    after = lane(consume(meter, 8, [hit(6, 3, 8, 200)]))
    assert after['skill_unclassified_hits'] == 1 and after['observed_non_skill_damage'] == 200


def test_actor_endpoint_does_not_clear_unassigned_owner_or_global_hook_failure():
    for global_failure in (False, True):
        meter = MultiplayerBattleStatistics()
        consume(meter, 2)
        core = meter._players[PLAYERS[2]['uid']].statistics
        owner = next(iter(core.owners))
        if global_failure:
            core.mark_skill_unknown()
        else:
            core.mark_settlement_skill_unassigned(owner, 'unresolved_source', None)
        consume(meter, 3, [skill(1, 3, 'Character.OnSkillFinish')])
        observed = lane(consume(meter, 4, [hit(2, 3, 4, 100)]))
        assert observed['skill_unclassified_hits'] == 1
        assert observed['observed_non_skill_damage'] is None


def test_other_skill_end_cannot_heal_previously_closed_unconfirmed_effect():
    meter = MultiplayerBattleStatistics()
    consume(meter, 0)
    consume(meter, 1, [skill(1, 1, 'Character.SwitchToSkillState', kind='skill_start')])
    cast = skill(2, 2, 'BasicSkill.OnCastFinish')
    cast['skill_wait_for_end'] = True
    consume(meter, 2, [cast])
    ending = skill(3, 3, 'Character.OnAfterSkill')
    ending['skill_reason'] = 2
    consume(meter, 3, [ending])
    consume(meter, 4, [skill(4, 4, 'Character.OnSkillFinish', key='different_skill')])
    observed = lane(consume(meter, 5, [hit(5, 3, 5, 100)]))
    assert observed['observed_damage'] == 100
    assert observed['skill_unclassified_hits'] == 1
    assert observed['observed_non_skill_damage'] is None
