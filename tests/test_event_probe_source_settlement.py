"""Run the actual producer callback order and rejected-proof regression matrix."""
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


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
        assert actual_order["raw_damage"]["amount"] == 40
        assert actual_order["raw_damage"]["hp_before"] == 50
        assert actual_order["raw_damage"]["hp_after"] == 10
        rejected = report[backend]["rejected"]
        assert len(rejected) == 16
        for reason, evidence in rejected.items():
            assert evidence["batch"]["enemy_damage_unknown"] is True, reason
            assert evidence["settlement"] is None or evidence["settlement"]["settlement_complete"] is False
