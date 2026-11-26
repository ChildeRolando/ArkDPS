"""The cold graph describes current layouts, never a previous roster's values."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_native_cold_projection_builder():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for the cold projection fixture')
    result = subprocess.run([node, str(Path(__file__).with_name('native_pc_cold_plan_fixture.js'))],
                            check=True, capture_output=True, text=True, timeout=20)
    report = json.loads(result.stdout)
    assert report['checks'] >= 100
    assert 12 < report['depth'] <= 24
    assert report['nodes'] <= 128
    assert report['live_reads'] == report['live_calls'] == 0


def test_native_cold_metadata_freezes_concrete_generic_closure():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for the cold metadata fixture')
    result = subprocess.run([node, str(Path(__file__).with_name('native_pc_cold_metadata_fixture.js'))],
                            check=True, capture_output=True, text=True, timeout=20)
    report = json.loads(result.stdout)
    assert report['metadata_ready'] is True
    assert report['generic_instantiations'] == 2
    assert report['replay_live_reads'] == report['replay_native_calls'] == 0
