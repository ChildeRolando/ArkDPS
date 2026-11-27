"""Run the actual producer callback order and rejected-proof regression matrix."""
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_disabling_enemy_hp_callbacks_preserves_damage_and_marks_partial_coverage():
    run = subprocess.run(['node', str(ROOT / 'tests/native_pc_settlement_order_fixture.js'),
                          '--enemy-hp-opt-out'], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    default, disabled = result['default'], result['disabled']
    events = default['batch']['events']
    assert [event['kind'] for event in events] == ['enemy_enter', 'enemy_hp', 'damage_settlement']
    kept = [event for event in events if event['kind'] != 'enemy_hp']
    assert disabled['batch']['events'] == [dict(event, sequence=i) for i, event in enumerate(kept, 1)]
    for field in ('mark', 'batch'):
        assert default[field]['enemy_state_ready'] is True
        assert disabled[field]['enemy_state_ready'] is False
        assert disabled[field]['enemy_max_hp_ready'] is False
        assert disabled[field]['enemy_damage_ready'] is True
        assert disabled[field]['enemy_state_error'] == 'continuous enemy HP observation disabled'


def test_actual_callback_order_and_identity_failures_keep_source_certificates_honest():
    result = subprocess.run(["node", str(ROOT / "tests/native_pc_settlement_order_fixture.js")],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    for backend in ("android", "native_pc"):
        actual_order = report[backend]["target_return_before_apply_commit"]
        hit = actual_order["settlement"]
        assert hit["amount"] == 40 and hit["settlement_complete"] is True
        assert hit["proof"]["evidence_method"] == "Unit.OnTakeDamage+Entity.ApplyModifier"
        assert hit["amount"] == 40
        assert hit["hp_before"] == 50
        assert hit["hp_after"] == 10
        rejected = report[backend]["rejected"]
        assert len(rejected) == 16
        for reason, evidence in rejected.items():
            local = reason in {"hp_difference", "nominal_less_than_hp", "cancel_reason",
                               "duplicate_target_callback", "nested_damage", "nested_external", "nested_healing"}
            assert evidence["batch"]["enemy_damage_unknown"] is (not local), reason
            if local:
                unknown = [hit for hit in evidence["batch"]["events"]
                           if hit["kind"] == "damage_settlement" and not hit["settlement_complete"]]
                assert unknown and all(hit["source_character_uid"] == 1010 for hit in unknown)
                assert all(hit["proof"]["settlement_unknown_reason"] for hit in unknown)
            assert evidence["settlement"] is None or evidence["settlement"]["settlement_complete"] is False


def test_source_formula_diagnostics_do_not_depend_on_a_legacy_wire_record():
    result = subprocess.run(['node', str(ROOT / 'tests/native_pc_settlement_order_fixture.js')],
                            cwd=ROOT, text=True, capture_output=True, timeout=30, check=True)
    report = json.loads(result.stdout)
    for platform in ('android', 'native_pc'):
        legacy = report[platform]['target_return_before_apply_commit']
        modern = report[platform]['without_legacy_wire']
        assert [event['kind'] for event in modern['batch']['events']] == ['damage_settlement']
        assert modern['settlement']['settlement_complete'] is True
        assert modern['settlement']['hp_before'] == 50
        assert modern['settlement']['hp_after'] == 10
        assert modern['mark']['mitigation_diagnostics'] == legacy['mark']['mitigation_diagnostics']
        assert modern['mark']['mitigation_diagnostics']['unpaired_damage'] == 1
        assert modern['settlement']['sequence'] == 1
