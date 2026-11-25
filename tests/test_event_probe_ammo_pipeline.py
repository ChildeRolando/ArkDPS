"""Actual producer callbacks -> capture/legacy/v7/panel in a controlled model.

Hit numbers and Q32 event times are frozen native inputs. Metadata, object and
Modifier memory plus the cumulative two-hit counter are synthetic controls;
other native cumulative damage is outside this slice. This is not a full real
battle replay or acceptance of the candidate in the official client.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT
from panel_model import EventAvailability, PanelConfig, render_panel, rows_from_statistics
from test_event_probe_parity import CanonicalCollector, BoundedObserver


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/event_probe_business'
INPUT_HASH = 'bfc347718c1bdd0ae1f839c1d8deb902c1ba8c281e2e3603eee582a6d9573c94'


@pytest.fixture(scope='module')
def producer():
    run = subprocess.run(['node', str(ROOT / 'tests/event_probe_ammo_pipeline_fixture.js')],
                         cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


class SourceMarksObserver(BoundedObserver):
    """Preserve real source marks/quality; only host session binding is synthetic."""
    def set_stage(self, stage):
        self.source_mark = deepcopy(stage['mark'])
        self.quality = deepcopy(stage['batch'])
        self.records = deepcopy(stage['batch']['events'])
        self.sequence = stage['mark']['sequence']

    def mark(self):
        return dict(self.source_mark, **self.binding())


@pytest.fixture(scope='module', params=[('android', 'mumu', MUMU_CONTRACT),
                                     ('native_pc', 'native_pc', NATIVE_PC_CONTRACT)])
def chain(request, producer):
    backend, platform, contract = request.param
    report = producer[backend]
    collector = CanonicalCollector(platform)
    collector.cid, collector.uid = report['actor']['cid'], report['actor']['data_uid']
    observer = SourceMarksObserver(collector, contract, report['stages'][0]['batch'])
    capture = LiveEventCapture(collector, observer)
    stages = {}
    for stage in report['stages']:
        collector.frame, collector.game_time = stage['mark']['frame'], stage['mark']['game_time']
        collector.damage = stage['counter_damage']  # explicitly owned cumulative two-hit scene
        observer.set_stage(stage)
        result = capture.sample()
        if stage['name'] == 'terminal_without_skill_end':
            # Public adapter terminal seam used by BattleSessions, after an
            # actual source OnDestroy mark in the owned synthetic controller.
            boundary = stage['mark']['terminal_boundary']
            assert boundary['frame'] == collector.frame
            result = capture.adapter.finish(boundary['game_time'])
        panels = {}
        for effective in (False, True):
            view = render_panel(PanelConfig(panel_id='ammo-shared-chain', effective_only=effective),
                                rows_from_statistics(result), event=EventAvailability('recording'))
            panels[effective] = deepcopy(view.details_by_owner[collector.cid])
        stages[stage['name']] = {'result': deepcopy(result),
                                'row': deepcopy(result['rows'][collector.cid]),
                                'panels': panels}
    return report, stages


def test_two_native_hit_inputs_and_original_line_bytes_are_frozen():
    raw = (FIXTURES / 'owned-ammo-pipeline-inputs.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == INPUT_HASH
    data = json.loads(raw)
    source_lines = (FIXTURES / data['provenance']['source_lines_file']).read_bytes()
    assert hashlib.sha256(source_lines).hexdigest() == data['provenance']['source_lines_sha256']
    lines = source_lines.splitlines(keepends=True)
    for entry in data['provenance']['source_lines']:
        assert hashlib.sha256(lines[entry['fixture_line'] - 1]).hexdigest() == entry['line_sha256']
    for hit in data['hits'].values():
        batch = json.loads(lines[next(x['fixture_line'] for x in data['provenance']['source_lines']
                                     if x['original_line'] == hit['reference']['line']) - 1])['data']['drain']
        assert batch['events'][hit['reference']['drain_event_index']] == hit['damage']
        assert hit['settlement']['settlement_complete'] is True
    assert data['hits']['first_live']['damage']['frame'] == 320
    assert 2543 < data['hits']['outside_after_true_finish']['damage']['frame'] < 3765
    assert data['second_activation_has_no_outgoing_hp_formula_hit'] is True


def expected(report):
    inside = report['inputs']['first_live']['damage']['amount']
    outside = report['inputs']['outside_after_true_finish']['damage']['amount']
    before = sum(value['damage']['mitigation_before'] for value in report['inputs'].values())
    return inside, outside, before


@pytest.mark.parametrize('effective', [False, True])
def test_first_live_native_wait_has_finite_nominal_and_actual_panel_share(chain, effective):
    report, stages = chain
    inside, _, _ = expected(report)
    stage = stages['first_live']
    row, lane, detail = stage['row'], stage['row']['settled_damage'], stage['panels'][effective]
    assert row['skill_damage'] == pytest.approx(inside) and row['non_skill_damage'] == 0
    assert row['skill_split_complete'] and row['skill_split_effective_complete']
    assert lane['observed_skill_damage'] == pytest.approx(inside)
    assert lane['observed_non_skill_damage'] == 0
    assert lane['skill_split_complete'] and lane['skill_split_effective_complete']
    for suffix in ('', '_effective'):
        assert detail['skill_damage' + suffix] == pytest.approx(inside)
        assert detail['non_skill_damage' + suffix] == 0
        assert math.isfinite(detail['skill_damage' + suffix] / (detail['skill_damage' + suffix] + detail['non_skill_damage' + suffix]))
    assert detail['settled_damage']['observed_hits'] == 1


@pytest.mark.parametrize('name', ['outside_after_true_finish', 'second_live_no_hit'])
@pytest.mark.parametrize('effective', [False, True])
def test_true_finish_separates_outside_hit_and_second_live_preserves_components(chain, name, effective):
    report, stages = chain
    inside, outside, before = expected(report)
    stage = stages[name]
    row, lane, detail = stage['row'], stage['row']['settled_damage'], stage['panels'][effective]
    assert row['skill_damage'] == pytest.approx(inside)
    assert row['non_skill_damage'] == pytest.approx(outside)
    assert row['skill_split_complete'] and row['skill_split_effective_complete']
    assert lane['complete'] and lane['skill_split_complete'] and lane['skill_split_effective_complete']
    assert lane['observed_skill_damage'] == pytest.approx(inside)
    assert lane['observed_non_skill_damage'] == pytest.approx(outside)
    assert lane['mitigation_complete'] and lane['mitigation_matched_hits'] == 2
    assert lane['observed_mitigation_before'] == pytest.approx(before)
    assert lane['observed_mitigation_after'] == pytest.approx(inside + outside)
    assert lane['observed_mitigation_rate'] == pytest.approx((1 - (inside + outside) / before) * 100)
    for suffix in ('', '_effective'):
        assert detail['skill_damage' + suffix] == pytest.approx(inside)
        assert detail['non_skill_damage' + suffix] == pytest.approx(outside)
        ratio = detail['skill_damage' + suffix] / (detail['skill_damage' + suffix] + detail['non_skill_damage' + suffix])
        assert math.isfinite(ratio) and ratio == pytest.approx(inside / (inside + outside))
    assert detail['settled_damage']['observed_hits'] == 2  # full-prefix replay never doubles hits
    assert row['skill_window_count'] == (1 if name == 'outside_after_true_finish' else 2)


@pytest.mark.parametrize('effective', [False, True])
def test_terminal_without_second_true_end_keeps_observed_components_without_complete_claim(chain, effective):
    report, stages = chain
    inside, outside, _ = expected(report)
    stage = stages['terminal_without_skill_end']
    row, lane, detail = stage['row'], stage['row']['settled_damage'], stage['panels'][effective]
    assert stage['result']['ended'] is True
    assert row['skill_split_complete'] is False
    assert row['skill_split_effective_complete'] is False
    assert lane['skill_split_complete'] is False
    assert lane['skill_split_effective_complete'] is False
    assert lane['complete'] is True  # HP proof is independent of an unobserved skill end
    for suffix in ('', '_effective'):
        assert detail['skill_damage' + suffix] is None
        assert detail['non_skill_damage' + suffix] is None
        assert detail['observed_skill_damage' + suffix] == pytest.approx(inside)
        assert detail['observed_non_skill_damage' + suffix] == pytest.approx(outside)
    assert detail['settled_damage']['observed_hits'] == 2


def test_actual_source_markers_and_quality_cross_the_consumer_without_repairs(producer):
    for report in producer.values():
        last = report['stages'][-1]
        events = last['batch']['events']
        methods = [event['proof']['method'] for event in events if event['kind'] in ('skill_start','skill_end')]
        assert methods.count('Character.SwitchToSkillState') == 2
        assert methods.count('Character.OnSkillCastSucceed') == 2
        assert methods.count('Character.OnSkillFinish') == 1
        assert [event['skill_wait_for_end'] for event in events if event['proof']['method'] == 'BasicSkill.OnCastFinish'] == [True, True]
        assert not any(event['kind'] in ('damage', 'hp_damage', 'elemental_burst') for event in events)
        assert len([event for event in events if event['kind'] == 'damage_settlement']) == 2
        assert all(event['frame'] < 3765 for event in events if event['kind'] == 'damage_settlement')
        assert last['batch']['mitigation_ready'] is True
        assert last['batch']['mitigation_unknown'] is False
        assert last['batch']['enemy_damage_unknown'] is False
