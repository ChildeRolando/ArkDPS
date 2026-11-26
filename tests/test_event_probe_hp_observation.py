import subprocess
from pathlib import Path


def test_count_one_hp_observation_owner_preserves_branch_demand():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/event_probe_hp_observation_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_HP_OBSERVATION_OWNER' in result.stdout


def test_native_hp_observation_contract_lifetime_and_mode_epoch():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/native_pc_hp_observation_contract_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_HP_OBSERVATION_CONTRACT' in result.stdout


def test_direct_hp_source_keeps_one_state_owner_and_strict_issuance_gate():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/native_pc_direct_hp_source_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_DIRECT_HP_SOURCE' in result.stdout
