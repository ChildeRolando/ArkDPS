"""Actor windows are the only mutable skill effect state."""
from stats_core import Statistics


def transition(stats, actor, kind, when, deployment):
    stats.record_settlement_skill('owner', actor, 'source', kind, when,
        key='skill', skill_id='skill', name=None,
        method='Character.OnSkillCastSucceed' if kind == 'skill_start' else 'Character.OnSkillFinish',
        reason=None, deployment=deployment)


def test_owner_duration_unions_actor_windows_and_actor_observation_stays_separate():
    stats = Statistics('scope', 0)
    transition(stats, 1, 'skill_start', 1, 11)
    transition(stats, 2, 'skill_start', 2, 22)
    transition(stats, 1, 'skill_end', 3, 11)
    stats.now = 4
    assert stats._skill_active_seconds('owner') == 3
    assert stats._skill_active_seconds('owner', actor=1) == 2
    assert stats._skill_active_seconds('owner', actor=2) == 2
    assert set(stats.settlement_skill_active) == {('owner', 2)}
    assert 'skill_active' not in vars(stats)


def test_owner_projection_cannot_change_actor_state():
    stats = Statistics('scope', 0)
    transition(stats, 1, 'skill_start', 1, 11)
    stats.skill_active.clear()
    assert ('owner', 1) in stats.settlement_skill_active
    stats._close_skill_windows(3, battle_end=True)
    assert not stats.settlement_skill_active
    assert stats.skill_windows['owner'][0]['end'] == 3


def test_token_host_resolver_rejects_side_mismatch_and_ambiguity():
    from autochess_context import verified_roster_token_host
    from roster import CharacterEntry
    host = CharacterEntry('char_host', 1, False, False, None, None, 2)
    token = CharacterEntry('token_child', 2, True, False, 'char_host', 1, 2)
    assert verified_roster_token_host(token, [host]) == host
    assert verified_roster_token_host(token, [host, host]) is None
    wrong_side = CharacterEntry('char_host', 1, False, False, None, None, 3)
    assert verified_roster_token_host(token, [wrong_side]) is None
