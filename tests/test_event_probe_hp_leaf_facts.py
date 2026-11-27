from pathlib import Path
import json
import subprocess


def test_shared_leaf_business_kernel_preserves_all_retained_state_and_wire():
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run(['node', 'tests/event_probe_hp_leaf_facts_fixture.js'], cwd=root,
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads(run.stdout)
    assert result['pass'] is True and result['cases'] == 66
