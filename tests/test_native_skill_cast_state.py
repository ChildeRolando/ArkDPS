"""Explicit synthetic delivery of native cast-return state, not a game replay.

The frozen native-field case preserves real clocks/identities/booleans. Its
additional canonical cast-state event and damage are explicitly synthetic until
the production hook is exercised separately. Old 2206 recordings are unchanged.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT
from panel_model import EventAvailability, PanelConfig, render_panel, rows_from_statistics
from stats_core import Statistics
from test_event_probe_parity import BoundedObserver, CanonicalCollector


ACTOR, KEY = 'char_1041_angel2', 'skchr_angel2_3'
BACKENDS = (('mumu', MUMU_CONTRACT), ('native_pc', NATIVE_PC_CONTRACT))
MISSING = object()


def connect(platform, contract, uid=77):
    collector = CanonicalCollector(platform)
    collector.cid, collector.uid = ACTOR, uid
    observer = BoundedObserver(collector, contract)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    return collector, observer, capture


def marker(method, kind, when, *, key=KEY, reason=None, uid=77, wait=MISSING):
    result = dict(source=ACTOR, source_character_uid=uid, kind=kind, amount=1,
                  frame=when * 30, game_time=when, skill_key=key,
                  skill_reason=reason, proof={'method': method})
    if wait is not MISSING:
        result['skill_wait_for_end'] = wait
    return result


def hits(when, amount=40, uid=77):
    base = dict(source=ACTOR, source_character_uid=uid, frame=when * 30,
                game_time=when, target='enemy_a', target_instance='enemy-1', target_uid='11',
                damage_type=1, shield_absorbed=0, killed=False,
                mitigation_before=amount * 2, mitigation_after=amount)
    return [dict(base, kind='damage', amount=amount, target_type=0,
                 hp_before=100, hp_after=100 - amount, real_delta=-amount),
            dict(base, kind='damage_settlement', amount=amount, nominal_amount=amount,
                 settlement_complete=True)]


def advance(state, when, records, damage, *, frame=None):
    collector, observer, capture = state
    collector.frame = frame if frame is not None else when * 30
    collector.game_time, collector.damage = when, damage
    observer.append(records)
    return capture.sample()['rows'][ACTOR]


def start_and_cast(state, *, wait=True, native=True, effect=True, native_first=True, uid=77, key=KEY,
                   native_reason=1):
    starts = [marker('Character.OnSkillStart', 'skill_start', 1)]
    if effect:
        starts.insert(0, marker('Character.SwitchToSkillState', 'skill_start', 1))
    starts.append(marker('Character.OnSkillCastSucceed' if effect else 'Character.OnSkillStart',
                         'skill_start', 1))
    advance(state, 1, starts, 0)
    cast = [marker('Character.OnAfterSkill', 'skill_end', 2, reason=1),
            marker('Character.OnAfterSkill', 'skill_end', 2, reason=1)]
    if native:
        observed = marker('BasicSkill.OnCastFinish', 'skill_end', 2, reason=native_reason,
                          wait=wait, uid=uid, key=key)
        cast.insert(0 if native_first else len(cast), observed)
    advance(state, 2, cast, 0)


def detail(result):
    return render_panel(PanelConfig(), rows_from_statistics(result),
                        event=EventAvailability('recording')).details_by_owner[ACTOR]


def assert_live_split(row, inside, outside=0):
    assert row['skill_split_complete'] is True and row['skill_split_effective_complete'] is True
    assert (row['skill_damage'], row['non_skill_damage']) == (inside, outside)
    lane = row['settled_damage']
    assert lane['skill_split_complete'] is True and lane['skill_split_effective_complete'] is True
    assert (lane['observed_skill_damage'], lane['observed_non_skill_damage']) == (inside, outside)
    assert (lane['observed_skill_damage_effective'], lane['observed_non_skill_damage_effective']) == (inside, outside)


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('native_first', [False, True])
def test_native_cast_wait_keeps_ammo_live_then_real_character_end_closes_it(platform, contract, native_first):
    state = connect(platform, contract)
    start_and_cast(state, native_first=native_first)
    row = advance(state, 3, hits(3), 40)
    assert_live_split(row, 40)
    assert row['skill_window_count'] == 1
    selected = detail(state[2].adapter.result())
    assert selected['skill_damage'] == selected['skill_damage_effective'] == 40
    advance(state, 4, [marker('Character.OnSkillFinish', 'skill_end', 4)], 40)
    row = advance(state, 5, hits(5, 10), 50)
    assert_live_split(row, 40, 10)
    assert row['skill_window_count'] == 1 and row['skill_active_seconds'] == 3
    assert row['skill_split_reason'] is None


@pytest.mark.parametrize('platform,contract', BACKENDS)
def test_same_tick_normal_cast_return_preserves_effect_until_real_finish(platform, contract):
    state = connect(platform, contract)
    key = 'skchr_blemsh_3'
    advance(state, 1, [marker('Character.OnSkillCastSucceed', 'skill_start', 1, key=key)], 0)
    advance(state, 2, [marker('BasicSkill.OnCastFinish', 'skill_end', 2, key=key, reason=0, wait=True),
                       marker('Character.OnAfterSkill', 'skill_end', 2, key=key, reason=0)], 0)
    assert_live_split(advance(state, 3, hits(3), 40), 40)
    advance(state, 4, [marker('Character.OnSkillFinish', 'skill_end', 4, key=key)], 40)
    row = advance(state, 5, hits(5, 10), 50)
    assert_live_split(row, 40, 10)
    assert row['skill_window_count'] == 1 and row['skill_active_seconds'] == 3


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('case', ['wait_false', 'stale', 'other_frame', 'missing_key',
                                  'other_reason', 'no_effect'])
def test_normal_cast_return_requires_current_effect_witness(platform, contract, case):
    state = connect(platform, contract)
    start = 'Character.OnSkillStart' if case == 'no_effect' else 'Character.OnSkillCastSucceed'
    advance(state, 1, [marker(start, 'skill_start', 1)], 0)
    advance(state, 2, [marker('BasicSkill.OnCastFinish', 'skill_end', 2, reason=0,
                              wait=case != 'wait_false')], 0)
    when = 3 if case == 'stale' else 2
    after = marker('Character.OnAfterSkill', 'skill_end', when,
                   key=None if case == 'missing_key' else KEY,
                   reason=2 if case == 'other_reason' else 0)
    if case == 'other_frame':
        after['frame'] += 1
    advance(state, when, [after], 0, frame=after['frame'])
    row = advance(state, 4, hits(4), 40)
    assert row['skill_split_complete'] is False
    lane = row['settled_damage']
    assert lane['observed_damage'] == lane['effective_damage'] == 40
    assert lane['skill_split_complete'] is False and lane['skill_unclassified_hits'] == 1


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('conflicting_callback', ['native_return', 'after_skill'])
def test_empty_skill_keys_cannot_match_conflicting_nonempty_skill_ids(platform, contract, conflicting_callback):
    state = connect(platform, contract)
    start = marker('Character.OnSkillCastSucceed', 'skill_start', 1, key='')
    advance(state, 1, [dict(start, skill_id=KEY)], 0)
    cast = marker('BasicSkill.OnCastFinish', 'skill_end', 2, key='', reason=0, wait=True)
    after = marker('Character.OnAfterSkill', 'skill_end', 2, key='', reason=0)
    cast['skill_id'] = 'other-skill' if conflicting_callback == 'native_return' else KEY
    after['skill_id'] = 'other-skill' if conflicting_callback == 'after_skill' else KEY
    advance(state, 2, [cast, after], 0)
    row = advance(state, 3, hits(3), 40)
    assert row['skill_split_complete'] is False
    assert row['settled_damage']['skill_unclassified_hits'] == 1


@pytest.mark.parametrize('start_actor,actor,key,frame,stays_open', [
    (77, 78, KEY, 60, False), (77, 77, 'other-skill', 60, True),
    (None, None, KEY, 60, False), (77, 77, KEY, None, False),
])
def test_normal_cast_return_requires_matching_verified_actor_and_frame(
        start_actor, actor, key, frame, stays_open):
    stats = Statistics('cast', 0)
    active = dict(owner=ACTOR, source=ACTOR, key=KEY, skill_id=None, name=None, actor=start_actor)
    stats._record_skill_transition(kind='skill_start', game_time=1,
                                   method='Character.OnSkillCastSucceed', reason=None, **active)
    stats._record_skill_transition(kind='skill_end', game_time=2,
                                   method='BasicSkill.OnCastFinish', reason=0, wait_for_end=True,
                                   frame=frame, **active)
    stats._record_skill_transition(kind='skill_end', game_time=2,
                                   method='Character.OnAfterSkill', reason=0, frame=frame,
                                   **dict(active, key=key, actor=actor))
    assert (ACTOR in stats.skill_active) is stays_open
    assert (ACTOR in stats.skill_incomplete) is not stays_open


@pytest.mark.parametrize('frame', [True, -1, 60.0, '60'])
def test_skill_transition_rejects_supplied_invalid_frame(frame):
    stats = Statistics('cast', 0)
    with pytest.raises(ValueError, match='invalid skill frame'):
        stats._record_skill_transition(ACTOR, ACTOR, 'skill_start', 1, key=KEY, skill_id=None,
                                       name=None, method='Character.OnSkillCastSucceed', reason=None,
                                       actor=77, frame=frame)
    assert not stats.skill_active


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('case', ['legacy', 'not_waiting', 'no_effect', 'wrong_uid', 'wrong_key'])
def test_unproven_cast_state_cannot_upgrade_ammo_classification(platform, contract, case):
    state = connect(platform, contract)
    start_and_cast(state, native=case != 'legacy', wait=case != 'not_waiting',
                   effect=case != 'no_effect', uid=999 if case == 'wrong_uid' else 77,
                   key='different-skill' if case == 'wrong_key' else KEY)
    row = advance(state, 3, hits(3), 40)
    assert row['skill_split_complete'] is False
    assert row['settled_damage']['skill_split_complete'] is False
    assert row['settled_damage']['observed_skill_damage'] is None
    if case == 'wrong_uid':
        assert row['damage'] == row['observed_damage'] == 40
        assert state[2].adapter.mapping_conflicts == set()
        assert state[2].closed is False and state[2].adapter.event_stream.closed is False


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('wait', [None, 0, 1, 'true', {}])
def test_native_cast_state_requires_literal_boolean(platform, contract, wait):
    state = connect(platform, contract)
    start_and_cast(state, wait=wait)
    row = advance(state, 3, hits(3), 40)
    assert row['skill_split_complete'] is False
    assert row['settled_damage']['skill_split_complete'] is False
    assert row['settled_damage']['observed_skill_damage'] is None


@pytest.mark.parametrize('platform,contract', BACKENDS)
def test_native_cast_without_matching_start_cannot_certify_non_skill_damage(platform, contract):
    state = connect(platform, contract)
    advance(state, 2, [marker('BasicSkill.OnCastFinish', 'skill_end', 2, reason=1, wait=True)], 0)
    row = advance(state, 3, hits(3), 40)
    assert row['skill_split_complete'] is False
    lane = row['settled_damage']
    assert lane['skill_split_complete'] is False
    assert lane['observed_skill_damage'] is None and lane['observed_non_skill_damage'] is None


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('native_wait', [MISSING, False, True], ids=['legacy', 'false', 'true'])
def test_existing_ordinary_window_still_clips_at_a_clean_battle_boundary(platform, contract, native_wait):
    state = connect(platform, contract)
    advance(state, 1, [marker('Character.SwitchToSkillState', 'skill_start', 1,
                              key='ordinary-skill')], 0)
    records = [marker('Character.OnAfterSkill', 'skill_end', 2, key='ordinary-skill', reason=1)]
    if native_wait is not MISSING:
        records.append(marker('BasicSkill.OnCastFinish', 'skill_end', 2,
                               key='ordinary-skill', reason=1, wait=native_wait))
    advance(state, 2, records, 0)
    advance(state, 3, hits(3), 40)
    row = state[2].adapter.finish(3)['rows'][ACTOR]
    assert_live_split(row, 40)


@pytest.mark.parametrize('platform,contract', BACKENDS)
def test_native_false_cast_return_after_real_finish_does_not_reopen_or_poison_window(platform, contract):
    state = connect(platform, contract)
    start_and_cast(state)
    advance(state, 3, hits(3), 40)
    # A different native implementation can deliver its real finish inside
    # OnCastFinish, before the latter's final wait=false observation is sent.
    advance(state, 4, [marker('Character.OnSkillFinish', 'skill_end', 4),
                       marker('BasicSkill.OnCastFinish', 'skill_end', 4, reason=1, wait=False)], 40)
    row = advance(state, 5, hits(5, 10), 50)
    assert_live_split(row, 40, 10)
    assert row['skill_window_count'] == 1


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('reason', [None, True, 'bad', 1.0, -(1 << 31) - 1, 1 << 31])
def test_native_cast_reason_requires_a_literal_int32(platform, contract, reason):
    state = connect(platform, contract)
    start_and_cast(state, native_reason=reason)
    row = advance(state, 3, hits(3), 40)
    assert row['skill_split_complete'] is False
    assert row['settled_damage']['skill_split_complete'] is False
    assert row['settled_damage']['observed_skill_damage'] is None


@pytest.mark.parametrize('reason', [-(1 << 31), -1, 0, (1 << 31) - 1])
def test_native_cast_does_not_guess_an_enum_whitelist(reason):
    state = connect('native_pc', NATIVE_PC_CONTRACT)
    start_and_cast(state, native_reason=reason)
    assert_live_split(advance(state, 3, hits(3), 40), 40)


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('already_finished', [False, True])
def test_passive_false_from_unmatched_uid_does_not_poison_known_actor(platform, contract, already_finished):
    state = connect(platform, contract)
    start_and_cast(state)
    advance(state, 3, hits(3), 40)
    records = [marker('Character.OnSkillFinish', 'skill_end', 4)] if already_finished else []
    records.append(marker('BasicSkill.OnCastFinish', 'skill_end', 4, reason=1, wait=False, uid=999))
    advance(state, 4, records, 40)
    row = advance(state, 5, hits(5, 10), 50)
    assert_live_split(row, 40 if already_finished else 50, 10 if already_finished else 0)
    assert row['damage'] == row['observed_damage'] == 50
    assert state[2].adapter.mapping_conflicts == set()
    assert 999 not in state[2].adapter.source_observed_uids.get(ACTOR, set())
    assert state[2].closed is False and state[2].adapter.event_stream.closed is False


@pytest.mark.parametrize('platform,contract', BACKENDS)
def test_invalid_passive_false_is_unknown_skill_state_without_corrupting_damage_source(platform, contract):
    state = connect(platform, contract)
    start_and_cast(state)
    advance(state, 3, hits(3), 40)
    row = advance(state, 4, [marker('BasicSkill.OnCastFinish', 'skill_end', 4,
                                   reason=True, wait=False, uid=999)], 40)
    assert row['skill_split_complete'] is False and row['settled_damage']['skill_split_complete'] is False
    assert row['damage'] == row['observed_damage'] == 40
    assert state[2].adapter.mapping_conflicts == set()
    assert state[2].closed is False and state[2].adapter.event_stream.closed is False


@pytest.mark.parametrize('platform,contract', BACKENDS)
def test_native_cast_for_second_known_uid_cannot_upgrade_first_actor_or_crash_stream(platform, contract):
    state = connect(platform, contract)
    collector, observer, capture = state
    original_roster = collector.read_roster

    def two_instances():
        roster = original_roster()
        return replace(roster, entries=(*roster.entries, replace(roster.entries[0], unique_id=78)),
                       present_unique_ids=frozenset({77, 78}))

    collector.read_roster = two_instances
    advance(state, 1, [marker('Character.SwitchToSkillState', 'skill_start', 1)], 0)
    advance(state, 2, [marker('BasicSkill.OnCastFinish', 'skill_end', 2,
                              reason=1, wait=True, uid=78)], 0)
    row = advance(state, 3, hits(3), 40)
    assert capture.closed is False and capture.adapter.event_stream.closed is False
    assert row['skill_split_complete'] is False
    assert row['settled_damage']['skill_split_complete'] is False
    assert capture.adapter.statistics.skill_active[ACTOR].get('native_wait_for_skill_end') is not True


@pytest.mark.parametrize('platform,contract', BACKENDS)
@pytest.mark.parametrize('close', ['clean_finish', 'gap', 'recast'])
def test_missing_real_end_keeps_observed_values_but_never_complete_split(platform, contract, close):
    state = connect(platform, contract)
    start_and_cast(state)
    assert_live_split(advance(state, 3, hits(3), 40), 40)
    if close == 'clean_finish':
        result = state[2].adapter.finish(3)
    elif close == 'gap':
        state[2].close()
        result = state[2].adapter.statistics.result()
    else:
        advance(state, 4, [marker('Character.SwitchToSkillState', 'skill_start', 4),
                           marker('Character.OnSkillStart', 'skill_start', 4)], 40)
        result = state[2].adapter.result()
        assert result['rows'][ACTOR]['skill_window_count'] == 2
    row = result['rows'][ACTOR]
    assert row['skill_split_complete'] is False and row['observed_skill_damage'] == 40
    lane = row['settled_damage']
    assert lane['skill_split_complete'] is False and lane['skill_split_effective_complete'] is False
    assert lane['observed_skill_damage'] == lane['observed_skill_damage_effective'] == 40
    selected = detail(result)
    assert selected['skill_damage'] is None and selected['skill_damage_effective'] is None
    assert selected['observed_skill_damage'] == selected['observed_skill_damage_effective'] == 40
    expected = ('未观察到弹药技能的实际结束；结算事件覆盖不完整'
                if close == 'gap' else '未观察到弹药技能的实际结束')
    assert selected['skill_split_reason'] == selected['skill_split_effective_reason'] == expected


def test_frozen_native_cast_field_and_real_character_finish_order_are_preserved():
    raw = (Path(__file__).with_name('fixtures') / 'event_probe_business' /
           '2206-native-callback-fields.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'da6122420941983fa56037c5e4039a81ea8b8ca2e14c3d53e7f0bab2ec52912f'
    native = json.loads(raw)
    casts = [entry['record'] for entry in native['ammo_lifecycle_records']
             if entry['record']['methods'] == ['BasicSkill.OnCastFinish']]
    assert [(record['phase'], record['snapshot']['wait_for_skill_end']) for record in casts] == [
        ('enter', False), ('leave', True), ('enter', False), ('leave', True)]
    first = casts[1]
    state = connect('native_pc', NATIVE_PC_CONTRACT, first['snapshot']['source_character_uid'])
    for entry in native['ammo_canonical_skill_records']:
        record = deepcopy(entry['event'])
        if record['frame'] < 306 or record['frame'] > 314:
            continue
        record.pop('sequence')
        advance(state, record['game_time'], [record], 0, frame=record['frame'])
    snapshot, clock = first['snapshot'], first['clock']
    # Explicit interpretation of a real native return; this is not claimed to
    # be output from the production hook added in the same change.
    observed = dict(source=snapshot['owner_cid'], source_character_uid=snapshot['source_character_uid'],
                    frame=clock['frame'], game_time=clock['game_time'], kind='skill_end', amount=1,
                    skill_key=snapshot['skill_key'], skill_id=snapshot['skill_id'], skill_reason=1,
                    skill_wait_for_end=snapshot['wait_for_skill_end'], proof={'method':'BasicSkill.OnCastFinish'})
    advance(state, clock['game_time'], [observed], 0, frame=clock['frame'])
    synthetic_hits = hits(11, uid=snapshot['source_character_uid'])
    assert_live_split(advance(state, 11, synthetic_hits, 40), 40)
    finish = deepcopy(next(entry['event'] for entry in native['ammo_canonical_skill_records']
                           if entry['event']['proof']['method'] == 'Character.OnSkillFinish'))
    finish.pop('sequence')
    advance(state, finish['game_time'], [finish], 40, frame=finish['frame'])
    assert ACTOR not in state[2].adapter.statistics.skill_active
    assert state[2].adapter.result()['rows'][ACTOR]['skill_split_complete'] is True
