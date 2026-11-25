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


def test_legacy_available_effective_amount_does_not_fabricate_hp_certificate():
    stats = Statistics('old-api', 0, event_coverage=True)
    stats.register_source('source', 'owner')
    stats.snapshot(0, {'source': {'damage': 0, 'healing': 0}})
    stats.snapshot(2, {'source': {'damage': 100, 'healing': 0}})
    stats.partial_damage_enabled = True
    stats.event('hit', 1, 'source', 'damage', 100, target='enemy_test',
        hp_before=80, hp_after=0, real_delta=-80, killed=True,
        shield_absorbed=0, damage_type=1)
    meter = stats.settled['owner']
    assert meter.hits == 1 and meter.raw == 100
    assert meter.hp == meter.shield == 0
    assert meter.legacy_effective == 80
    assert meter.unverified == 1
    assert 'partial_damage' not in vars(stats)
    assert 'observed_skill_splits' not in vars(stats)
    assert 'overkill' not in vars(stats)
    row = stats.result()['rows']['owner']
    assert row['damage'] == 100 and row['effective_damage'] == 80
    assert row['partial_damage']['effective_damage'] == 80
