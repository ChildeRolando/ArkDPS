import subprocess
from pathlib import Path


def test_hp_readplan_tiers_preserve_shared_callback_validation():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/event_probe_hp_readplan_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_HP_READPLAN_SOURCE_TIERS' in result.stdout
