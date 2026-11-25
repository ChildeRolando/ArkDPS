"""Verify the optional actor-form proof from real PC and MuMu callbacks."""

from pathlib import Path
import subprocess


def test_native_character_form_is_typed_and_bound_to_the_same_callback():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["node", str(root / "tests" / "event_probe_character_form_fixture.js")],
        cwd=root,
        check=True,
        timeout=30,
    )
