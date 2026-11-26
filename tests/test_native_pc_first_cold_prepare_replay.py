"""Real metadata closure followed by the first owned cold binding, without preheat."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_first_cold_uses_real_prepared_metadata():
    result = subprocess.run(
        ['node', str(ROOT / 'tests/native_pc_first_cold_prepare_replay_fixture.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_FIRST_COLD_REAL_PREPARE' in result.stdout
    assert 'zero live replay reads' in result.stdout
