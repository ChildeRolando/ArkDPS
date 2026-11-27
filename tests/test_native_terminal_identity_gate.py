"""Shared root validation must respect the cold gate and live address reuse."""
from pathlib import Path
import json
import shutil
import subprocess
import pytest


def test_terminal_identity_class_gate_and_contradictory_second_read():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for the shared function fixture')
    fixture = Path(__file__).with_name('native_terminal_identity_gate_fixture.js')
    run = subprocess.run([node, str(fixture)], capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads(run.stdout)
    assert result['checks'] == 10
    assert result['captured_class_race_rejected'] and result['raw_wrong_block_rejected']
    assert result['unknown_classes_rejected_without_metadata']
