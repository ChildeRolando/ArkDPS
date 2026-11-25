"""Run the actual native adapter against bounded, owned managed-memory data."""
import json
from pathlib import Path
import subprocess


def test_native_autochess_cache_bulk_witnesses_and_read_budgets():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ['node', str(root / 'tests/native_pc_autochess_performance_fixture.js')],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    result = json.loads(completed.stdout)
    assert result['status'] == 'verified'
    assert 'same_frame_late_owner_change_cannot_publish_single_pass_stale_owner' in result['checks']
    assert 'second_body_guard_failure_clears_the_body_without_reusing_unverified_roster' in result['checks']
    assert 'committed_scope_survives_membership_churn_and_a_rejected_cold_read' in result['checks']
    assert 'crossed_cold_read_cannot_keep_an_old_stage_pointer_or_changed_scope' in result['checks']
    assert 'successfully_read_partial_roster_keeps_only_fully_guarded_unbound_hosts' in result['checks']
    assert 'native_lazy_arguments_preserve_production_callback_batches_and_ack' in result['checks']
    assert len(result['counts']) == 8
