"""Bounded source diagnostics preserve the authoritative combat journal."""
import json
from pathlib import Path
import subprocess


def test_nested_checkpoints_observe_real_parent_and_child_fields_without_changing_events():
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run(['node', str(root / 'tests/native_pc_nested_trace_fixture.js')],
                         cwd=root, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout) == {
        'android': {'unchanged_events': True, 'checkpoints': 3},
        'native_pc': {'unchanged_events': True, 'checkpoints': 3},
    }
