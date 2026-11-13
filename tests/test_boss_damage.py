import pytest

from stats_core import Statistics


BOSS = 'enemy_1506_patrt'
NORMAL = 'enemy_1000_gopro'


def setup():
    stats = Statistics('battle', 0, event_coverage=True)
    stats.register_source('host', 'operator')
    stats.register_source('summon', 'operator')
    stats.register_source('other', 'other')
    stats.snapshot(0, counters(0, 0, 0))
    stats.observe_boss_damage(complete=True)
    stats.mark_boss_watermark(0)
    return stats


def counters(host, summon, other):
    return {key: {'damage': amount, 'healing': 0}
            for key, amount in zip(('host', 'summon', 'other'), (host, summon, other))}


def test_boss_scope_uses_only_verified_boss_hits_for_all_damage_metrics():
    s = setup()
    s.snapshot(8, counters(230, 50, 0))
    s.event('regular', 1, 'host', 'damage', 100, target=NORMAL, killed=False,
            hp_after=300, damage_type=1)
    s.event('self-cost', 1, 'host', 'damage', 20, target='char_1041_angel2',
            killed=False, hp_after=800, damage_type=3)
    s.event('skill', 2, 'host', 'skill_start', 1, skill_key='skill')
    s.event('phase-one', 3, 'host', 'damage', 130, target=BOSS, hp_before=100,
            hp_after=0, killed=True, real_delta=-100, shield_absorbed=0, damage_type=1,
            mitigation_before=260, mitigation_after=130)
    s.event('phase-two', 7, 'summon', 'damage', 50, target=BOSS, killed=False,
            hp_after=800, damage_type=2, mitigation_before=100, mitigation_after=50)
    before_watermark = s.result()['rows']['operator']['boss_damage']
    assert before_watermark['damage'] is None
    s.mark_boss_watermark(8)
    result = s.result()
    all_targets = result['rows']['operator']
    boss = all_targets['boss_damage']
    assert all_targets['damage'] == 280
    assert all_targets['effective_damage'] == 250
    assert result['boss_damage_available']
    assert result['boss_damage_reason'] == ''
    assert boss['damage'] == 180 and boss['effective_damage'] == 150
    assert boss['skill_damage'] == 180 and boss['skill_damage_effective'] == 150
    assert boss['damage_by_type']['physical'] == 130
    assert boss['damage_by_type']['arts'] == 50
    assert boss['recent_damage_window'] == (3, 8)
    assert boss['recent_damage'] == 10 and boss['recent_overkill_damage'] == 0
    assert boss['active_damage'] == 2.5
    assert boss['engagement_damage'] == 72
    assert boss['mitigation_rate'] == 50
    assert result['rows']['other']['boss_damage']['damage'] == 0

    s.invalidate_events(8, complete=True)
    s.finish(8)
    final = s.result()['rows']['operator']['boss_damage']
    assert final['skill_damage_effective'] == 150
    assert final['recent_damage'] is None
    assert final['effective_damage'] == 150


@pytest.mark.parametrize(('fault', 'reason'), [
    ('unknown', '首领身份未知'), ('gap', '事件覆盖不完整'), ('unreconciled', '原生累计尚未对齐'),
])
def test_boss_scope_requires_attributed_complete_damage(fault, reason):
    s = setup()
    s.snapshot(4, counters(101 if fault == 'unreconciled' else 100, 0, 0))
    s.event('hit', 3, 'host', 'damage', 100, target='enemy_new' if fault == 'unknown' else BOSS,
            hp_after=50, killed=False, damage_type=1)
    s.mark_boss_watermark(4)
    if fault == 'gap':
        s.invalidate_events(4)
    result = s.result()
    assert reason in result['boss_damage_reason']
    assert result['rows']['operator']['damage'] is not None
    assert result['rows']['operator']['boss_damage']['damage'] is None


def test_elemental_burst_credits_same_boss_once_and_missing_overkill_keeps_raw():
    s = setup()
    s.snapshot(4, counters(50, 0, 0))
    s.event('bar', 1, 'host', 'elemental_bar', 20, target_instance='boss')
    s.event('burst', 2, None, 'elemental_burst', 120, target=BOSS, target_instance='boss',
            real_delta=-100, hp_before=100, hp_after=0)
    s.event('hit', 3, 'host', 'damage', 50, target=BOSS, damage_type=1)
    s.mark_boss_watermark(4)
    boss = s.result()['rows']['operator']['boss_damage']
    assert boss['damage'] == 170
    assert boss['damage_by_type']['elemental'] == 120
    assert boss['effective_damage'] is None
    assert boss['mitigation_rate'] is None
