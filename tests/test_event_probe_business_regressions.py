"""Historical business semantics, independent of backend-equality assertions.

All original records are frozen official-PC observations. The local-reducer
case deliberately tests the per-hit seam: it does not pretend an old erroneous
global declaration can be ignored by an ordinary receiver.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from battle_lifecycle import BattleSessions
from capture_battles import observation_from_json
from panel_model import EventAvailability, PanelConfig, render_panel, rows_from_statistics


FIXTURES = Path(__file__).with_name('fixtures') / 'event_probe_business'
FIXTURE_HASHES = {
    '2206-open-effect.json': 'adeba757b2e60abe59bb34d1bcdf3fa5d0f9e94ba524b1c2b47e14854552066d',
    '2206-local-nested.json': '073a61a138ba204a4e45c16d14848516df0e6fcf99eab94ef8de48e2340f3063',
    '2206-ammo-uncertain-end.json': 'dde3a9c7b361f0b852b7afcff9a43e52f0a7ea1060ca27ba4bbf4375d7463e0c',
}


def frozen(name):
    raw = (FIXTURES / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_HASHES[name], 'immutable business fixture changed'
    value = json.loads(raw)
    assert value['provenance']['source_prefix_sha256'] == (
        '935abf29a45e6c0f72308e090072a5a7ccc3e027cee69b32c324fdcef68de8b4')
    assert value['provenance']['source_head'] == '43bb3bb15ddb77fc2f348ea3f8c8892f9f450c05'
    return value


def replay_observations(records):
    sessions = BattleSessions(records[0]['data']['feed'])
    for original in records:
        sessions.consume(observation_from_json(deepcopy(original['data'])))
    return sessions


def owner_detail(result, owner, *, effective):
    view = render_panel(PanelConfig(panel_id='historical-business', effective_only=effective),
                        rows_from_statistics(result), event=EventAvailability('recording'))
    return view.details_by_owner[owner]


@pytest.mark.parametrize('effective', [False, True])
def test_real_effect_start_and_cast_completion_keep_ordinary_window_live(effective):
    scene = frozen('2206-open-effect.json')
    actor = scene['checkpoint']['actor']
    events = {}
    for original in scene['observations']:
        evidence = original['data'].get('events') or {}
        for record in (evidence.get('drain') or {}).get('events') or ():
            if record.get('source') == actor:
                previous = events.setdefault(record['sequence'], record)
                assert previous == record
    markers = [record for record in events.values() if record['kind'] in ('skill_start', 'skill_end')]
    assert any(record['proof']['method'] == 'Character.SwitchToSkillState' for record in markers)
    assert any(record['proof']['method'] == 'Character.OnSkillCastSucceed' for record in markers)
    assert any(record['proof']['method'] == 'Character.OnAfterSkill' and record['skill_reason'] == 1
               for record in markers)
    assert not any(record['proof']['method'] in ('Character.OnSkillFinish', 'Character.SwitchOutFromSkillState')
                   for record in markers)
    sessions = replay_observations(scene['observations'])
    result = sessions.adapter.result()
    row = result['rows'][actor]
    for key, expected in scene['checkpoint']['expected'].items():
        assert row[key] == expected
    assert actor in sessions.adapter.statistics.skill_active
    assert any(identity[0] == actor for identity in sessions.adapter.statistics.settlement_skill_active)
    lane = row['settled_damage']
    assert lane['complete'] and lane['skill_split_complete'] and lane['skill_split_effective_complete']
    detail = owner_detail(result, actor, effective=effective)
    suffix = '_effective' if effective else ''
    inside, outside = detail['skill_damage' + suffix], detail['non_skill_damage' + suffix]
    assert inside == pytest.approx(397.79998540878296) and outside == 0
    assert detail['skill_split' + suffix + '_complete']
    assert inside / (inside + outside) == 1


def local_hit_reducer():
    """Original known identities and amounts through the public per-hit reducer.

    EventStream sequence and batch quality are intentionally outside this unit
    seam. Their original failing declaration is retained and tested separately.
    """
    scene = frozen('2206-local-nested.json')
    sessions = replay_observations([{'data': scene['baseline_original']['data']}])
    adapter, stats = sessions.adapter, sessions.adapter.statistics
    original_events = {record['sequence']: record for record in scene['events_original']}
    for number in (1626, 1635):
        record = original_events[number]
        state, owner, actor = adapter._settlement_identity(record['source'], record['source_character_uid'])
        assert state == 'local' and owner == record['source'] and actor == record['source_character_uid']
        if record['source'] not in stats.owners:
            stats.register_source(record['source'], owner)
        stats.advance(record['game_time'])
        stats.record_damage_settlement(
            record['game_time'], state, owner, actor, record['source'], record['target'],
            record['target_uid'], record['target_instance'], record['amount'], record['shield_absorbed'],
            record['settlement_complete'], record['damage_type'], record.get('nominal_amount'),
            record.get('mitigation_before'), record.get('mitigation_after'), killed=record.get('killed'),
            settled_after_nested_kill=record.get('settled_after_nested_kill', False))
        stats.mark_boss_watermark(record['game_time'])
    return scene, adapter


def test_real_known_actor_incomplete_hit_does_not_poison_another_actor_reducer():
    scene, adapter = local_hit_reducer()
    result = adapter.statistics.result()
    bad = result['rows'][scene['actors']['incomplete_actor']]['settled_damage']
    good = result['rows'][scene['actors']['complete_actor']]['settled_damage']
    assert bad['complete'] is False and bad['unverified_hits'] == 1
    assert bad['effective_damage'] is None
    assert good['complete'] is True and good['unverified_hits'] == 0
    assert good['effective_damage'] == pytest.approx(138.73999896622263)
    assert good['mitigation_complete'] is True and good['mitigation_matched_hits'] == 1
    original_quality = scene['first_issue_observation_original']['data']['events']['drain']
    assert original_quality['enemy_damage_unknown'] is True  # original source bug, not repaired test data


def test_actual_global_event_gap_still_closes_previously_complete_actor():
    scene, adapter = local_hit_reducer()
    actor = scene['actors']['complete_actor']
    assert adapter.statistics.result()['rows'][actor]['settled_damage']['complete'] is True
    adapter.on_event_gap()
    lane = adapter.statistics.result()['rows'][actor]['settled_damage']
    assert lane['complete'] is False
    assert lane['known_hp_damage'] == pytest.approx(138.73999896622263)
    assert lane['effective_damage'] == pytest.approx(138.73999896622263)  # retained component, incomplete scope
    assert lane['unverified_share'] is None


def test_mumu_late_baseline_rule_composes_with_real_source_attribution_and_v7():
    """Delay delivery of actual events; retain every native sample unchanged.

    This is an explicit observer-availability injection, not a claim that the
    original official-PC observer joined late. It composes upstream 01b7de4's
    snapshot/event baseline distinction with the real source-attribution v7 feed.
    """
    scene = frozen('2206-open-effect.json')
    sessions = BattleSessions(scene['observations'][0]['data']['feed'])
    attach = None
    for original in scene['observations']:
        observation = observation_from_json(deepcopy(original['data']))
        sample = observation.sample
        if sample is None or sample['frame'] < 199:
            sessions.consume(replace(observation, events=None))
            continue
        if attach is None:
            attach = observation
            quality = observation.events['drain']
            assert quality['source_attribution_version'] == 1 and quality['partial_damage_version'] == 7
        sessions.consume(observation)
    assert attach is not None and sessions.event_status == 'recording'
    stats = sessions.adapter.statistics
    result = stats.result()
    first_sample = next(original['data']['sample'] for original in scene['observations']
                        if original['data'].get('sample') is not None)
    assert result['coverage_start'] == first_sample['game_time']
    assert stats.event_coverage_start == attach.sample['game_time'] > result['coverage_start']
    actor = scene['checkpoint']['actor']
    row = result['rows'][actor]
    assert row['damage'] == scene['checkpoint']['expected']['damage']
    assert row['observed_damage'] == pytest.approx(397.79998540878296)
    assert row['settled_damage']['effective_damage'] == pytest.approx(397.79998540878296)
    before = {item['id']: item['damage'] for item in attach.sample['rows'] if item.get('damage', 0) > 0}
    assert before, 'the real attachment point must contain earlier cumulative damage'
    assert any(result['rows'][source]['observed_damage'] is None
               or result['rows'][source]['damage'] > result['rows'][source]['observed_damage']
               for source in before if source in result['rows'])


def actual_ammo_result():
    scene = frozen('2206-ammo-uncertain-end.json')
    base = frozen(scene['base_fixture']['name'])
    assert scene['base_fixture']['sha256'] == FIXTURE_HASHES[scene['base_fixture']['name']]
    sessions = replay_observations([*base['observations'], *scene['continuation']])
    return scene, sessions.adapter.statistics.result()


def test_real_ammo_without_effect_end_keeps_unknown_values_and_verified_hp_separate():
    scene, result = actual_ammo_result()
    actor = scene['checkpoint']['actor']
    markers = scene['raw_skill_markers_original']
    assert any(record['proof']['method'] == 'Character.OnSkillCastSucceed' for record in markers)
    assert any(record['proof']['method'] == 'Character.OnAfterSkill' and record['skill_reason'] == 1
               for record in markers)
    assert not any(record['proof']['method'] in ('Character.OnSkillFinish', 'Character.SwitchOutFromSkillState')
                   for record in markers)
    hits = scene['raw_enemy_settlements_original']
    assert len(hits) == 3 and all(record['settlement_complete'] is True for record in hits)
    row = result['rows'][actor]
    assert row['skill_split_reason'] == scene['checkpoint']['expected_unknown_reason']
    assert row['skill_damage'] is None and row['non_skill_damage'] is None
    lane = row['settled_damage']
    assert lane['complete'] is True and lane['observed_hits'] == 3
    assert lane['known_hp_damage'] == pytest.approx(sum(record['amount'] for record in hits))
    assert lane['mitigation_complete'] is True and lane['mitigation_matched_hits'] == 3
    assert lane['observed_skill_damage'] is None and lane['observed_non_skill_damage'] is None
    assert lane['skill_split_complete'] is False and lane['skill_split_effective_complete'] is False


@pytest.mark.parametrize('effective', [False, True])
def test_real_ammo_specific_unknown_reason_survives_selected_panel_measurement(effective):
    scene, result = actual_ammo_result()
    actor = scene['checkpoint']['actor']
    detail = owner_detail(result, actor, effective=effective)
    suffix = '_effective' if effective else ''
    assert detail['skill_damage' + suffix] is None and detail['non_skill_damage' + suffix] is None
    assert detail['skill_split' + suffix + '_complete'] is False
    # rc.5's public observed-value contract includes the actual missing-proof
    # reason. An independent HP measurement cannot replace it with a generic
    # classification gap or make unobserved ammo termination look like zero.
    assert detail['skill_split' + suffix + '_reason'] == scene['checkpoint']['expected_unknown_reason']


@pytest.mark.parametrize('effective', [False, True])
def test_real_ammo_gap_keeps_both_scope_and_specific_skill_reasons(effective):
    scene = frozen('2206-ammo-uncertain-end.json')
    base = frozen(scene['base_fixture']['name'])
    sessions = replay_observations([*base['observations'], *scene['continuation']])
    sessions.adapter.on_event_gap()  # Explicit stream fault; original observations are unchanged.
    result = sessions.adapter.statistics.result()
    actor = scene['checkpoint']['actor']
    lane = result['rows'][actor]['settled_damage']
    assert lane['reason'] == '结算事件覆盖不完整'
    assert lane['complete'] is False
    detail = owner_detail(result, actor, effective=effective)
    suffix = '_effective' if effective else ''
    assert detail['skill_split' + suffix + '_reason'] == (
        lane['skill_split_reason'] + '；' + lane['reason'])
    assert detail['skill_damage' + suffix] is None and detail['non_skill_damage' + suffix] is None


@pytest.mark.parametrize('lane_reason,lane_skill_reason,row_reason,expected', [
    ('结算事件覆盖不完整', '当前命中的技能窗口未确认', '未观察到弹药技能的实际结束',
     '当前命中的技能窗口未确认；结算事件覆盖不完整'),
    (None, '当前命中的技能窗口未确认', '未观察到弹药技能的实际结束', '当前命中的技能窗口未确认'),
    (None, None, '未观察到弹药技能的实际结束', '未观察到弹药技能的实际结束'),
    (None, None, None, '部分命中缺少技能窗口证据'),
])
def test_skill_unknown_reason_projection_priority(lane_reason, lane_skill_reason, row_reason, expected):
    """Projection-only conflict injection, not additional producer evidence."""
    scene, result = actual_ammo_result()
    actor = scene['checkpoint']['actor']
    row = result['rows'][actor]
    row['skill_split_reason'] = row_reason
    row['settled_damage'].update(reason=lane_reason, skill_split_reason=lane_skill_reason)
    original = deepcopy(result)
    for effective in (False, True):
        detail = owner_detail(result, actor, effective=effective)
        suffix = '_effective' if effective else ''
        assert detail['skill_split' + suffix + '_reason'] == expected
        assert detail['skill_damage' + suffix] is None and detail['non_skill_damage' + suffix] is None
    assert result == original, 'presentation must not mutate statistical evidence or values'
