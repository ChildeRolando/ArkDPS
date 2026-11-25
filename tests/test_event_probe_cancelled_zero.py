"""Run the shared producer against verified and contradictory cancellations."""

from pathlib import Path
import subprocess


def test_cancelled_zero_requires_matching_target_return_and_no_consumption():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["node", str(root / "tests" / "event_probe_cancelled_zero_fixture.js")],
        cwd=root,
        check=True,
        timeout=30,
    )
