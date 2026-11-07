import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'prepare_event_job', Path(__file__).resolve().parents[1] / 'acceptance' / 'prepare_event_job.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_baseline_pause_keeps_original_deployment_and_single_toggle(tmp_path):
    job = {'stage_name': 'OF-2', 'opers': [{'name': '山', 'skill': 2}],
           'actions': [{'type': 'SkillUsage', 'name': '山', 'skill_usage': 2, 'skill_times': 1}]}
    source, dest = tmp_path / 'source.json', tmp_path / 'capture.json'
    source.write_text(json.dumps(job), encoding='utf-8')
    result = module.prepare(source, dest)
    assert result['actions'][3:] == job['actions']
    assert [x['type'] for x in result['actions'][:3]] == ['Click', 'Output', 'Click']
    assert result['actions'][1]['post_delay'] == 45000
    assert json.loads(source.read_text(encoding='utf-8')) == job
    with pytest.raises(FileExistsError):
        module.prepare(source, dest)
