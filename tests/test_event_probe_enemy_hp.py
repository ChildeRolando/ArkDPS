"""Run the real enemy HP setter hooks against owned memory for both adapters."""

from pathlib import Path
import subprocess


def test_enemy_hp_setter_preserves_proof_with_and_without_coalescing():
    fixture = Path(__file__).with_name("event_probe_enemy_hp_fixture.js")
    result = subprocess.run(["node", str(fixture)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
