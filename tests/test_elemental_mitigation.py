"""Paired elemental settlements flow through the mitigation accounting."""

import pytest

from event_stream import EventStream
from stats_core import Statistics


BOSS = 'enemy_1506_patrt'
NORMAL = 'enemy_1007_slime'


def battle(host=100, summon=80):
    stats = Statistics('elemental-formula', 0, event_coverage=True)
    stats.register_source('host', 'operator')
    stats.register_source('summon', 'operator')
    stats.snapshot(0, {key: {'damage': 0, 'healing': 0} for key in stats.owners})
    stats.snapshot(1, {'host': {'damage': host, 'healing': 0},
                       'summon': {'damage': summon, 'healing': 0}})
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
    row = s.result()['rows']['operator']

    assert row['damage'] == 240
    assert row['effective_damage'] == 225
    assert row['burst_added_settlement'] == 60
    assert row['observed_mitigation_before'] == 365
    assert row['observed_mitigation_after'] == 240
    assert row['mitigation_matched_hits'] == 3
    if complete:
        assert row['mitigation_rate'] == pytest.approx((1 - 240 / 365) * 100)
    else:
        assert row['mitigation_rate'] is None
        assert row['mitigation_unknown_reason'] == '公式后伤害与本局累计不一致'


@pytest.mark.parametrize('before, after', [(None, None), (80, None), (80, 50)])
def test_unpaired_burst_keeps_full_mitigation_unknown_and_retains_matched_subset(before, after):
    s = battle(host=60, summon=80)
    burst(s, 'direct', source='summon', amount=80, actual=75, before=160, after=80)
    burst(s, 'unpaired', source='host', before=before, after=after)
    row = s.result()['rows']['operator']
    assert row['damage'] == 140
    assert row['mitigation_rate'] is None
    assert row['observed_mitigation_rate'] == 50
    assert row['mitigation_matched_hits'] == 1


def test_unattributed_positive_burst_leaves_team_scope_unverified_without_counting_its_pair():
    s = battle(host=0, summon=80)
    burst(s, 'direct', source='summon', amount=80, actual=75, before=160, after=80)
    burst(s, 'unattributed', target=NORMAL)
    row = s.result()['rows']['operator']
    assert row['damage'] == 80
    assert row['mitigation_rate'] is None
    assert '元素爆条' in row['mitigation_unknown_reason']
    assert row['observed_mitigation_rate'] == 50
    assert row['mitigation_matched_hits'] == 1


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
