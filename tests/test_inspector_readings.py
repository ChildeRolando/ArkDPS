"""Python owns Inspector evidence selection; no window or game access."""
import pytest
from panel_model import inspector_readings


def test_partial_overkill_uses_matching_hit_base_not_native_total():
    result = inspector_readings({'damage': 9000}, dict(overkill_base_damage=100,
        observed_overkill_damage=25, overkill_coverage_complete=False,
        overkill_matched_hits=2, overkill_unverified_share=.5))['overkill']
    assert result['amount'] == 25 and result['fraction'] == .25
    assert result['denominator'] == 100 and result['observed']
    assert result['label'] == '已观察伤害溢出率'
    assert '未核实命中占比 50%' in result['explanation']


def test_complete_zero_hit_is_unknown_fraction_not_zero_skill_share():
    result = inspector_readings({}, {'settled_damage': {'complete': True, 'observed_hits': 0},
        'skill_split_complete': True, 'skill_damage': 0, 'non_skill_damage': 0})
    split = result['skill_split']['nominal']
    assert split['complete'] and not split['observed'] and split['fraction'] is None
    assert split['skill'] is None and '本次未造成伤害' in split['explanation']
    assert result['mitigation']['fraction'] is None
    assert '没有可配对的命中' in result['mitigation']['explanation']


@pytest.mark.parametrize('effective', [False, True])
def test_skill_split_preserves_observed_evidence_and_whole_window_reason(effective):
    suffix = '_effective' if effective else ''
    detail = {'skill_split_reason': '技能窗口缺口',
        'observed_skill_damage' + suffix: 40, 'observed_non_skill_damage' + suffix: 60}
    result = inspector_readings({}, detail)['skill_split']['effective' if effective else 'nominal']
    assert result['fraction'] == .4 and result['denominator'] == 100
    assert result['observed'] and not result['complete'] and result['reason'] == '技能窗口缺口'


@pytest.mark.parametrize('percent, fraction', [(-25., -.25), (0.000002, 0.), (.02, .0002)])
def test_mitigation_keeps_negative_and_precision_with_paired_hits(percent, fraction):
    detail = {'settled_damage': dict(mitigation_complete=True, observed_mitigation_rate=percent,
        mitigation_matched_hits=2, observed_mitigation_before=100, observed_mitigation_after=100-percent)}
    result = inspector_readings({}, detail)['mitigation']
    assert result['fraction'] == fraction and not result['observed']
    assert result['denominator'] == 100
    assert '配对命中合计' in result['explanation']
    if percent < 0: assert '比例为负' in result['explanation']


def test_scope_failure_blocks_full_and_observed_skill_and_mitigation():
    result = inspector_readings({'mitigation_rate': 20}, {'damage_scope_reason': 'scope冲突',
        'observed_skill_damage': 1, 'observed_non_skill_damage': 1})
    assert result['skill_split']['nominal']['fraction'] is None
    assert result['mitigation']['fraction'] is None
    assert result['mitigation']['reason'] == 'scope冲突'


def test_legacy_overkill_retains_native_basis_and_zero_unknown():
    assert inspector_readings({'damage': 100, 'overkill_damage': 20}, {'damage': 100,
        'overkill_damage': 20})['overkill']['fraction'] == .2
    reading = inspector_readings({}, {'damage': 0, 'overkill_damage': 0})['overkill']
    assert reading['fraction'] is None and '累计总伤为零' in reading['explanation']


def test_frozen_cross_language_readings():
    import json
    from pathlib import Path
    path = Path(__file__).parents[1] / 'desktop/ArkDPS.Desktop.Tests/Fixtures/inspector-readings.json'
    for case in json.loads(path.read_text(encoding='utf8')):
        assert inspector_readings(case['row'], case['detail']) == case['readings'], case['name']
