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


def test_nominal_and_effective_keep_separate_activity_triggers():
    from damage_settlement import DamageSettlement
    meter = DamageSettlement()
    meter.hit(1, 0, 0, 'physical', True, False, 100, None, None, overkill=100)
    meter.hit(3, 40, 0, 'physical', True, False, None, None, None)
    row = meter.result(0, 5, 5, stream_complete=True)
    assert row['active_damage'] == 1.5
    assert row['active_effective_damage'] == 1.5
    assert row['engagement_damage'] == 100 / 1.5
    assert row['engagement_effective_damage'] == 40 / 1.5


def test_explicit_legacy_observation_mode_keeps_identity_and_partial_hp_quality():
    from event_stream import EventStream
    stats = Statistics('old-observer', 0, event_coverage=True)
    stats.settlement_enabled = stats.settlement_sides_known = True
    stats.damage_settlement_version = 7
    stats.legacy_observation_mode = True
    stats.partial_damage_enabled = True
    stats.register_source('char_a', 'owner')
    stats.snapshot(0, {'char_a': {'damage': 0, 'healing': 0}})
    stream = EventStream(stats, session=stats.session, baseline_frame=0, baseline_sequence=0)
    stats.snapshot(2, {'char_a': {'damage': 10, 'healing': 0}})
    common = dict(session=stats.session, frame=30, game_time=1, source='char_a',
        kind='damage', target='enemy_test', killed=False, hp_after=100, damage_type=1,
        mitigation_before=20, mitigation_after=10)
    stream.accept(sequence=1, source_character_uid=2,
        settlement_identity=('foreign', None, None), amount=999, **common)
    stream.accept(sequence=2, source_character_uid=1,
        settlement_identity=('local', 'owner', 1), amount=10, **common)
    stream.watermark(session=stats.session, sequence=2, frame=60, game_time=2)
    lane = stats.result()['rows']['owner']['settled_damage']
    assert lane['observed_damage'] == 10 and lane['observed_hits'] == 1
    assert lane['observed_damage_complete'] and lane['mitigation_complete']
    assert lane['known_hp_damage'] == lane['known_shield_damage'] == 0
    assert lane['complete'] is False
    assert stats.settled['owner'].legacy_effective == 10


def test_legacy_capability_rejects_a_second_native_certificate_writer():
    import pytest
    stats = Statistics('capability-conflict', 0, event_coverage=True)
    stats.settlement_enabled = stats.partial_damage_enabled = True
    stats.legacy_observation_mode = True
    stats.register_source('char_a', 'owner')
    stats.snapshot(0, {'char_a': {'damage': 0, 'healing': 0}})
    stats.snapshot(2, {'char_a': {'damage': 20, 'healing': 0}})
    stats.event('legacy', 1, 'char_a', 'damage', 20, target='enemy_test',
        hp_after=100, killed=False, damage_type=1)
    with pytest.raises(ValueError, match='contradicts declared legacy'):
        stats.record_damage_settlement(1, 'local', 'owner', 1, 'char_a',
            'enemy_test', '7', 'native', 8, 0, True, 1, 20, None, None, killed=False)
    assert stats.settlement_stream_incomplete
    assert stats.settled['owner'].hits == 1
    assert stats.settled['owner'].raw == 20
    assert stats.settled['owner'].hp == 0
    with pytest.raises(ValueError, match='cannot change after consumption'):
        stats.legacy_observation_mode = False
    assert stats.legacy_observation_mode is True


def test_native_certificate_consumption_prevents_later_legacy_capability_selection():
    import pytest
    stats = Statistics('capability-conflict', 0)
    stats.record_damage_settlement(1, 'local', 'owner', 1, 'char_a',
        'enemy_test', '7', 'native', 8, 0, True, 1, 20, None, None, killed=False)
    with pytest.raises(ValueError, match='cannot change after consumption'):
        stats.legacy_observation_mode = True
    assert stats.legacy_observation_mode is False
    assert stats.settled['owner'].raw == 20
