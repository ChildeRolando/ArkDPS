"""Scope follows the original event clock; transport evidence stays out of Stats."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from event_probe import event_processing_payload


def test_delayed_event_keeps_original_scope_and_mumu_wire_shape():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/event_probe_autochess_clock_fixture.js')],
                            capture_output=True, text=True, timeout=30, check=True)
    reports = json.loads(result.stdout)
    assert reports['delayed']['native_autochess_scope']['phase'] == 4
    assert reports['failed']['native_autochess_scope']['status'] == 'unknown'
    assert 'native_autochess_scope' not in reports['ordinary']


def test_processing_payload_preserves_raw_replay_and_skill_evidence():
    event = dict(sequence=1, frame=3, game_time=.1, kind='skill_end', source='char_a', amount=0,
                 native_autochess_scope=dict(status='available', phase=4, round=1),
                 proof=dict(method='BasicSkill.OnCastFinish'), unknown_protocol_field='must_not_be_hidden')
    before = deepcopy(event)
    payload = event_processing_payload(event)
    assert event == before
    assert 'proof' not in payload and 'native_autochess_scope' not in payload
    assert payload['skill_method'] == 'BasicSkill.OnCastFinish'
    assert payload['unknown_protocol_field'] == 'must_not_be_hidden'
    assert payload['amount'] == 0
