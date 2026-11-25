"""Optional live presence earns only continuous, independently timed intervals."""
import pytest

from test_multiplayer_native_context import (
    CID, PLAYERS, MultiplayerBattleStatistics, consume, context, hit, player, stamp,
)
from test_multiplayer_native_skill_baseline import activation
from test_skill_scope_recovery import skill


def point(when, *, android=False, seq=1, instance=71, units=None):
    result = dict(frame=round(when * 30), game_time=when,
                  units=([dict(cid=CID, data_uid=301, instance_uid=instance, player_side=3)]
                         if units is None else units))
    if android:
        result.update(version=1, binding='event_mark', sample_seq=seq)
    return result


def publish(meter, when, events=(), *, android=False, seq=1, **changes):
    changes.setdefault('scoped_presence', point(when, android=android, seq=seq))
    if android:
        changes.setdefault('battle_metadata', dict(platform='android',
            stage_id='Activities/ACT3AUTOCHESS/level_act3autochess_m11'))
    return consume(meter, when, events, **changes)


def row(result):
    return next(iter(player(result, 3)['statistics']['rows'].values()))


@pytest.mark.parametrize('android', [False, True])
def test_valid_zero_percent_and_active_intervals_survive_unscheduled_polls(android):
    meter = MultiplayerBattleStatistics()
    publish(meter, 0, android=android)
    consume(meter, .5)  # A normal 4Hz poll has no optional 1Hz point.
    first = row(publish(meter, 1, android=android, seq=2))
    assert first['observed_presence_seconds'] == 1
    assert first['observed_skill_active_seconds'] == first['observed_skill_coverage'] == 0
    result = publish(meter, 2, [activation(1, 1.5), hit(2, 3, 2)], android=android, seq=3)
    assert row(result)['observed_presence_seconds'] == 2
    assert row(result)['observed_skill_active_seconds'] == .5
    assert row(result)['observed_skill_coverage'] == .25


def test_android_closing_mark_presence_does_not_borrow_physical_snapshot_clock():
    meter = MultiplayerBattleStatistics()
    publish(meter, 0, android=True, autochess_snapshot_anchor=dict(frame=0, game_time=0))
    result = publish(meter, 1, android=True, seq=2,
                     autochess_snapshot_anchor=dict(frame=24, game_time=.8))
    assert row(result)['observed_presence_seconds'] == 1
    assert row(result)['observed_skill_coverage'] == 0


@pytest.mark.parametrize('fault', ['version', 'bool_version', 'binding', 'seq', 'bool_seq',
                                  'missing_seq', 'platform', 'clock', 'units'])
def test_bad_android_optional_point_resets_only_presence(fault):
    meter = MultiplayerBattleStatistics()
    publish(meter, 0, android=True)
    optional = point(1, android=True, seq=2)
    metadata = dict(platform='android', stage_id='Activities/ACT3AUTOCHESS/level_act3autochess_m11')
    if fault == 'version':
        optional['version'] = 2
    elif fault == 'bool_version':
        optional['version'] = True
    elif fault == 'binding':
        optional['binding'] = 'roster'
    elif fault == 'seq':
        optional['sample_seq'] = 0
    elif fault == 'bool_seq':
        optional['sample_seq'] = True
    elif fault == 'missing_seq':
        del optional['sample_seq']
    elif fault == 'platform':
        metadata['platform'] = 'native_pc'
    elif fault == 'clock':
        optional['game_time'] = .9
    else:
        optional['units'] = None
    publish(meter, 1, [hit(1, 3, 1)], android=True,
            scoped_presence=optional, battle_metadata=metadata)
    result = publish(meter, 2, [hit(2, 3, 2)], android=True, seq=3)
    assert 'observed_presence_seconds' not in row(result)
    assert player(result, 3)['observed_damage'] == 200
    assert meter._players[PLAYERS[2]['uid']].stream.sequence == 2
    assert row(publish(meter, 3, android=True, seq=4))['observed_presence_seconds'] == 1


def test_missed_android_attempt_cannot_be_bridged_by_next_good_point():
    meter = MultiplayerBattleStatistics()
    publish(meter, 0, android=True)
    # Sequence 2's entire closing mark/window was rejected, so no point arrived.
    consume(meter, 1)
    assert 'observed_presence_seconds' not in row(publish(meter, 2, android=True, seq=3))
    assert row(publish(meter, 3, android=True, seq=4))['observed_presence_seconds'] == 1


@pytest.mark.parametrize('explicit', [False, True])
def test_missing_native_scope_breaks_adjacency_without_closing_event_stream(explicit):
    meter = MultiplayerBattleStatistics()
    publish(meter, 0)
    if explicit:
        meter.invalidate_presence()  # Lifecycle scope_unavailable short circuit.
    else:
        consume(meter, 1, native=dict(status='unavailable'))
    result = publish(meter, 2)
    assert 'observed_presence_seconds' not in row(result)
    assert not meter._closed
    assert row(publish(meter, 3))['observed_presence_seconds'] == 1


def test_skill_recovery_never_retroactively_certifies_an_unknown_interval():
    meter = MultiplayerBattleStatistics()
    publish(meter, 0)
    unknown = skill(1, .5, 'Character.OnSkillStart', kind='skill_start', unknown=True)
    recovered = skill(2, 1.5, 'Character.OnSkillFinish')
    result = publish(meter, 2, [unknown, recovered])
    assert row(result)['observed_presence_seconds'] == 2
    assert 'observed_skill_coverage' not in row(result)
    assert row(result)['skill_active_seconds'] is None
    after = row(publish(meter, 3))
    assert after['observed_presence_seconds'] == 3
    assert after['observed_skill_active_seconds'] == after['observed_skill_coverage'] == 0
    core = meter._players[PLAYERS[2]['uid']]
    assert list(core.skill_timed_presence.values()) == [1]


def test_unlocalizable_scope_skill_event_invalidates_timing_but_not_damage():
    meter = MultiplayerBattleStatistics()
    publish(meter, 0)
    unknown = skill(1, .5, 'Character.OnSkillStart', kind='skill_start', unknown=True)
    del unknown['source_character_uid']
    result = publish(meter, 1, [unknown, hit(2, 3, 1)])
    assert row(result)['observed_presence_seconds'] == 1
    assert 'observed_skill_coverage' not in row(result)
    assert player(result, 3)['observed_damage'] == 100


@pytest.mark.parametrize('event_scope,source', [(stamp(phase=5, when=.5), CID),
    (dict(status='unknown'), 'trap_fixture')])
def test_proven_other_scope_or_map_prop_cannot_make_all_local_skills_unknown(event_scope, source):
    meter = MultiplayerBattleStatistics()
    publish(meter, 0)
    event = skill(1, .5, 'Character.OnSkillStart', kind='skill_start', unknown=True)
    event.update(source=source, native_autochess_scope=event_scope)
    del event['source_character_uid']
    result = publish(meter, 1, [event])
    assert row(result)['observed_skill_coverage'] == 0
    assert all(not p.statistics.skill_unknown for p in meter._players.values())


def test_legacy_pc_point_still_requires_physical_snapshot_anchor():
    meter = MultiplayerBattleStatistics()
    publish(meter, 0)
    publish(meter, 1, autochess_snapshot_anchor=dict(frame=27, game_time=.9))
    assert 'observed_presence_seconds' not in row(publish(meter, 2))
    assert row(publish(meter, 3))['observed_presence_seconds'] == 1


def test_legacy_optional_point_tolerates_explicit_absent_metadata():
    meter = MultiplayerBattleStatistics()
    publish(meter, 0)
    consume(meter, 1)
    meter._observe_presence(dict(frame=30, game_time=1, battle_metadata=None,
                                 scoped_presence=point(1)))
    assert row(meter.result())['observed_presence_seconds'] == 1


@pytest.mark.parametrize('sequences', [(1, 3, 4), (None, 1, 2), (1, None, None)])
def test_pc_attempt_gaps_and_legacy_transition_cannot_bridge_unknown_read(sequences):
    meter = MultiplayerBattleStatistics()
    results = []
    for when, sequence in enumerate(sequences):
        optional = point(when)
        if sequence is not None:
            optional['sample_seq'] = sequence
        results.append(publish(meter, when, scoped_presence=optional,
            battle_metadata=dict(platform='native_pc',
                stage_id='Activities/ACT3AUTOCHESS/level_act3autochess_m11')))
    assert 'observed_presence_seconds' not in row(results[1])
    assert row(results[2])['observed_presence_seconds'] == 1


@pytest.mark.parametrize('android', [False, True])
@pytest.mark.parametrize('new_activation', [False, True])
@pytest.mark.parametrize('observed_absence', [False, True])
def test_redeployed_body_never_inherits_old_uid_skill_window(android, new_activation, observed_absence):
    meter = MultiplayerBattleStatistics()
    publish(meter, 0, android=android)
    publish(meter, 1, [activation(1, .5)], android=android, seq=2)
    if observed_absence:
        publish(meter, 1.5, android=android,
                scoped_presence=point(1.5, android=android, seq=3, units=[]))
    offset = int(observed_absence)
    events = [activation(2, 1.75)] if new_activation else []
    publish(meter, 2, events, android=android,
            scoped_presence=point(2, android=android, seq=3+offset, instance=72))
    result = publish(meter, 3, android=android,
                     scoped_presence=point(3, android=android, seq=4+offset, instance=72))
    observed = row(result)
    assert observed['observed_presence_seconds'] == 2
    assert observed['skill_timed_presence_seconds'] == 1
    assert observed['observed_skill_active_seconds'] == .5
    assert observed['observed_skill_coverage'] == .5
    # Optional timing must not discard a real incoming skill callback.
    assert meter._players[PLAYERS[2]['uid']].stream.sequence == (2 if new_activation else 1)


@pytest.mark.parametrize('android', [False, True])
def test_redeployment_uncertainty_is_reset_with_new_battle_scope(android):
    meter = MultiplayerBattleStatistics()
    publish(meter, 0, android=android)
    publish(meter, 1, android=android,
            scoped_presence=point(1, android=android, seq=2, instance=72))
    assert meter._presence_skill_replaced
    scope = context(2, phase=5)
    publish(meter, 2, android=android, native=scope,
            scoped_presence=point(2, android=android, seq=3, instance=99))
    assert not meter._presence_skill_replaced
    start = activation(1, 2, event_scope=stamp(phase=5, when=2))
    result = publish(meter, 3, [start], android=android, native=context(3, phase=5),
                     scoped_presence=point(3, android=android, seq=4, instance=99))
    # The late new scope starts unknown, then a verified start supplies future state.
    assert row(result)['observed_presence_seconds'] == 1
    result = publish(meter, 4, android=android, native=context(4, phase=5),
                     scoped_presence=point(4, android=android, seq=5, instance=99))
    assert row(result)['observed_skill_coverage'] == 1
