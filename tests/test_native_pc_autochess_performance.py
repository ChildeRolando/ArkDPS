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
    assert 'second_body_guard_failure_clears_both_caches_without_retry' in result['checks']
    assert len(result['counts']) == 8
