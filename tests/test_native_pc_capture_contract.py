"""Bounded owned-memory native capture/replay contracts; never attach to a target."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('script,marker', [
    ('native_pc_owned_replay_fixture.js', 'PASS_OWNED_REPLAY'),
    ('native_pc_capture_plan_fixture.js', 'PASS_CAPTURE_PLAN_INVENTORY'),
    ('native_pc_captured_publication_fixture.js', 'PASS_CAPTURED_PUBLICATION'),
    ('native_pc_capture_mode_fixture.js', 'PASS_CAPTURE_MODE_HISTORY'),
    ('native_pc_parent_checkpoint_fixture.js', 'PASS_PARENT_CHECKPOINT'),
    ('native_pc_hp_owned_replay_fixture.js', 'PASS_OWNED_HP_SOURCE_REPLAY'),
    ('native_pc_cold_binding_memo_fixture.js', 'PASS_RECORD_LOCAL_COLD_BINDING'),
    ('native_pc_cold_source_replay_fixture.js', 'PASS_COLD_SOURCE_REPLAY'),
    ('native_pc_modifier_source_replay_fixture.js', 'PASS_MODIFIER_SOURCE_REPLAY'),
])
def test_native_capture_owned_contract(script, marker):
    completed = subprocess.run(['node', str(ROOT / 'tests' / script)], cwd=ROOT,
                               check=True, capture_output=True, text=True, timeout=20)
    assert marker in completed.stdout
