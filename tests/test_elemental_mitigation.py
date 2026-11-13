"""Paired elemental settlements share mitigation and boss accounting."""

import pytest

from battle_history import BattleRecord
from battle_share import record_view
from event_stream import EventStream
from panel_model import PanelConfig, panel_statistics, render_panel, rows_from_statistics
from stats_core import Statistics
from test_battle_history import record


BOSS = 'enemy_1506_patrt'
NORMAL = 'enemy_1007_slime'


def battle(host=100, summon=80):
    stats = Statistics('elemental-formula', 0, event_coverage=True)
    stats.register_source('host', 'operator')
    stats.register_source('summon', 'operator')
    stats.snapshot(0, {key: {'damage': 0, 'healing': 0} for key in stats.owners})
    stats.snapshot(1, {'host': {'damage': host, 'healing': 0},
                       'summon': {'damage': summon, 'healing': 0}})
    stats.observe_boss_damage(complete=True)
    return stats


def burst(stats, key, *, source=None, target=BOSS, amount=60, actual=50, before=80, after=60):
    stats.event(key, 1, source, 'elemental_burst', amount,
                target=target, target_instance=key, real_delta=-actual,
                hp_before=actual, hp_after=0, killed=True, damage_type=5,
                mitigation_before=before, mitigation_after=after)


@pytest.mark.parametrize('native_totals, complete', [((100, 80), True), ((110, 70), False)])
def test_paired_bursts_include_supplement_without_changing_source_reconciliation(native_totals, complete):
    s = battle(*native_totals)
    s.event('normal', 1, 'host', 'damage', 100, target=NORMAL, hp_after=250,
            killed=False, damage_type=1, mitigation_before=125, mitigation_after=100)
    burst(s, 'direct', source='summon', amount=80, actual=75, before=160, after=80)
    s.event('bar', 1, 'summon', 'elemental_bar', 20, target_instance='supplement')
    burst(s, 'supplement')
    burst(s, 'supplement')
    s.mark_boss_watermark(1)
    row = s.result()['rows']['operator']
    boss = row['boss_damage']

    assert row['damage'] == 240
    assert row['effective_damage'] == 225
    assert row['burst_added_settlement'] == 60
    assert row['observed_mitigation_before'] == 365
    assert row['observed_mitigation_after'] == 240
    assert row['mitigation_matched_hits'] == 3
    assert boss['observed_mitigation_before'] == 240
    assert boss['observed_mitigation_after'] == 140
    assert boss['mitigation_matched_hits'] == 2
    if complete:
        assert row['mitigation_rate'] == pytest.approx((1 - 240 / 365) * 100)
        assert boss['damage'] == 140
        assert boss['effective_damage'] == 125
        assert boss['mitigation_rate'] == pytest.approx((1 - 140 / 240) * 100)
    else:
        assert row['mitigation_rate'] is None
        assert row['mitigation_unknown_reason'] == '公式后伤害与本局累计不一致'
        assert boss['mitigation_rate'] is None


@pytest.mark.parametrize('before, after', [(None, None), (80, None), (80, 50)])
def test_unpaired_burst_keeps_full_mitigation_unknown_and_retains_matched_subset(before, after):
    s = battle(host=60, summon=80)
    burst(s, 'direct', source='summon', amount=80, actual=75, before=160, after=80)
    burst(s, 'unpaired', source='host', before=before, after=after)
    s.mark_boss_watermark(1)
    row = s.result()['rows']['operator']
    for scope in (row, row['boss_damage']):
        assert scope['damage'] == 140
        assert scope['mitigation_rate'] is None
        assert scope['observed_mitigation_rate'] == 50
        assert scope['mitigation_matched_hits'] == 1


def test_unattributed_positive_burst_leaves_team_scope_unverified_without_counting_its_pair():
    s = battle(host=0, summon=80)
    burst(s, 'direct', source='summon', amount=80, actual=75, before=160, after=80)
    burst(s, 'unattributed', target=NORMAL)
    s.mark_boss_watermark(1)
    row = s.result()['rows']['operator']
    assert row['damage'] == 80
    assert row['mitigation_rate'] is None
    assert '元素爆条' in row['mitigation_unknown_reason']
    assert row['observed_mitigation_rate'] == 50
    assert row['mitigation_matched_hits'] == 1
    assert row['boss_damage']['damage'] == 80
    assert row['boss_damage']['mitigation_rate'] == 50


def test_skill_split_reconciles_credited_bursts_even_if_another_burst_has_no_owner():
    s = battle(host=60, summon=0)
    s.event('bar', 1, 'host', 'elemental_bar', 20, target_instance='credited')
    s.event('skill', 1, 'host', 'skill_start', 1)
    burst(s, 'credited')
    burst(s, 'unattributed', target=NORMAL)
    burst(s, 'direct', source='host')
    row = s.result()['rows']['operator']
    assert row['burst_complete'] is False
    assert row['burst_unknown_reason']
    assert row['effective_damage'] == 100
    assert row['skill_split_reason'] == row['burst_unknown_reason']
    assert row['skill_split_effective_complete'] is False
    assert row['observed_skill_damage_effective'] == 100
    assert row['observed_non_skill_damage_effective'] == 0


def test_observed_skill_share_keeps_only_verifiable_effective_hits_after_a_gap():
    s = battle(host=180, summon=0)
    s.event('ordinary', .2, 'host', 'damage', 100, target=BOSS,
            hp_after=500, killed=False, damage_type=1)
    s.event('skill', .4, 'host', 'skill_start', 1)
    s.event('lethal', .6, 'host', 'damage', 60, target=BOSS,
            real_delta=-20, hp_before=20, hp_after=0, killed=True,
            shield_absorbed=0, damage_type=1)
    s.event('unverified', .8, 'host', 'damage', 20, target=BOSS, damage_type=1)
    s.event('friendly', .9, 'host', 'damage', 500, target='char_self',
            hp_after=500, killed=False, damage_type=1)
    s.mark_boss_watermark(1)
    s.invalidate_events(1)
    s.finish(1)
    result = s.result()
    row = result['rows']['operator']
    assert row['skill_split_effective_complete'] is False
    assert row['skill_damage_effective'] is None
    assert row['skill_split_reason'] == '事件覆盖已结束'
    assert row['observed_skill_damage'] == 80
    assert row['observed_non_skill_damage'] == 100
    assert row['observed_skill_damage_effective'] == 20
    assert row['observed_non_skill_damage_effective'] == 100
    assert row['boss_damage']['observed_skill_damage_effective'] == 20
    assert row['boss_damage']['observed_non_skill_damage_effective'] == 100

    config = PanelConfig(effective_only=True)
    view = render_panel(config, rows_from_statistics(panel_statistics(config, result)))
    detail = view.details_by_owner['operator']
    assert detail['observed_skill_damage_effective'] == 20
    assert detail['observed_non_skill_damage_effective'] == 100
    assert not view.entries  # An unverified full-battle effective total stays unavailable.
    boss_config = PanelConfig(effective_only=True, boss_only=True)
    boss_view = render_panel(boss_config, rows_from_statistics(panel_statistics(boss_config, result)))
    assert boss_view.details_by_owner['operator']['observed_skill_damage_effective'] == 20
    assert not boss_view.entries

    archive = record().to_dict()
    archive['statistics']['rows']['a'].update(
        observed_skill_damage_effective=20, observed_non_skill_damage_effective=100,
        skill_split_effective_complete=False, skill_split_reason=row['skill_split_reason'])
    restored = BattleRecord.from_dict(archive)
    history_view = record_view(restored, config)
    assert history_view.details_by_owner['a']['observed_skill_damage_effective'] == 20
    assert history_view.details_by_owner['a']['observed_non_skill_damage_effective'] == 100


def test_observed_skill_share_is_labelled_partial_when_native_totals_include_missing_damage():
    s = battle(host=150, summon=0)
    s.event('ordinary', .2, 'host', 'damage', 100, target=NORMAL,
            hp_after=500, killed=False, damage_type=1)
    s.event('skill', .4, 'host', 'skill_start', 1)
    s.event('active', .6, 'host', 'damage', 20, target=NORMAL,
            hp_after=480, killed=False, damage_type=1)
    row = s.result()['rows']['operator']
    assert row['skill_split_reason'] == '技能窗口未覆盖全部伤害事件'
    assert row['skill_split_effective_complete'] is False
    assert row['observed_skill_damage_effective'] == 20
    assert row['observed_non_skill_damage_effective'] == 100


def test_wire_burst_pair_reconciles_with_explicit_source_and_retries_once():
    s = battle(host=60, summon=0)
    stream = EventStream(s, session=s.session, baseline_frame=0, baseline_sequence=0)
    event = dict(session=s.session, sequence=1, frame=30, game_time=1,
                 source='host', kind='elemental_burst', amount=60, target=BOSS,
                 target_instance='boss', real_delta=-50, hp_before=50,
                 hp_after=0, killed=True, damage_type=5,
                 mitigation_before=80, mitigation_after=60)
    stream.accept(**event)
    stream.accept(**event)
    stream.watermark(session=s.session, sequence=1, frame=30, game_time=1)
    row = s.result()['rows']['operator']
    assert row['effective_damage'] == 50
    assert row['mitigation_rate'] == 25
    assert row['mitigation_matched_hits'] == 1
    assert row['boss_damage']['mitigation_rate'] == 25
